"""
Automated Integration Tests for Shared Infrastructure Integration (Phase P1 Step 8).

Verifies contract compliance between Artisan Module and Shared Infrastructure:
1. ListingReference creation on artisan listing creation & approval.
2. Correct module_type values ('artisan', 'artisan_reservation').
3. Idempotent duplicate prevention (unique constraint parity with Migration 027).
4. BookingReference creation on artisan reservation creation.
5. Reservation status synchronization across lifecycle (pending -> confirmed -> ready -> completed / cancelled / expired).
6. Payment status synchronization ('unpaid' for active/cancelled/expired, 'paid' for completed pickup).
7. Audit trail logging in BookingStatusHistory.
8. Deactivation synchronization on listing rejection, suspension, and deletion.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, BookingStatusHistory, ListingReference
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_reservation_expiry import expire_single_reservation
from app.services.artisan_shared_reference import (
    ARTISAN_MODULE_TYPE,
    ARTISAN_RESERVATION_MODULE_TYPE,
    get_artisan_booking_reference,
    get_artisan_listing_reference,
    sync_artisan_booking_reference,
    sync_artisan_listing_reference,
)

# In-memory SQLite engine for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, expire_on_commit=False
)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def artisan_provider_user(db_session):
    user = User(
        email="artisan_shared@test.com",
        password_hash=hash_password("ArtisanPass123!"),
        full_name="Shared Artisan Provider",
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Ceylon Heritage Crafts",
        provider_type="artisan",
        phone="+94770000001",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def traveler_user(db_session):
    user = User(
        email="traveler_shared@test.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Shared Traveler",
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session):
    user = User(
        email="admin_shared@test.com",
        password_hash=hash_password("AdminPass123!"),
        full_name="Shared Admin",
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def approved_listing_and_product(db_session, artisan_provider_user):
    _, profile = artisan_provider_user

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Galle Fort Mask Workshop",
        craft_category="Mask Making",
        description="Authentic handmade Raksha masks.",
        district="Galle",
        city="Galle",
        address="12 Church St, Galle Fort",
        latitude=Decimal("6.032800"),
        longitude=Decimal("80.216800"),
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    # Synchronize shared listing reference
    sync_artisan_listing_reference(db_session, listing)
    db_session.commit()

    product = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Traditional Naga Raksha Mask",
        item_type="product",
        price=Decimal("12500.00"),
        currency="LKR",
        stock_quantity=10,
        is_available=True,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)

    return listing, product


# ==============================================================================
# 1. Listing Reference Creation & Module Type Tests
# ==============================================================================

def test_listing_creation_creates_shared_reference_with_pending_status(
    client, artisan_provider_user, db_session
):
    """When a provider creates a listing, shared ListingReference is created with is_active=False (pending)."""
    user, _ = artisan_provider_user

    payload = {
        "title": "Kandy Brass Carving Workshop",
        "craft_category": "Brassware",
        "description": "Handcrafted traditional brass lamps and figures.",
        "district": "Kandy",
        "city": "Pilimathalawa",
        "latitude": 7.275000,
        "longitude": 80.560000,
    }

    response = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(user),
    )
    assert response.status_code == 201
    listing_id = response.json()["id"]

    # Verify shared ListingReference was created atomically
    ref = get_artisan_listing_reference(db_session, listing_id)
    assert ref is not None
    assert ref.module_type == ARTISAN_MODULE_TYPE
    assert ref.module_type == "artisan"
    assert ref.target_id == listing_id
    assert ref.title == "Kandy Brass Carving Workshop"
    assert ref.district == "Kandy"
    assert ref.city == "Pilimathalawa"
    assert ref.latitude == Decimal("7.275000")
    assert ref.longitude == Decimal("80.560000")
    # Unapproved listings must NOT be active in shared catalog
    assert ref.is_active is False


def test_listing_admin_approval_activates_shared_reference(
    client, artisan_provider_user, admin_user, db_session
):
    """When an admin approves a listing, shared ListingReference becomes is_active=True."""
    user, _ = artisan_provider_user

    # Provider creates listing
    res = client.post(
        "/api/v1/artisan-listings",
        json={
            "title": "Jaffna Palmyra Craft Center",
            "craft_category": "Weaving",
            "district": "Jaffna",
            "city": "Jaffna",
        },
        headers=auth_header(user),
    )
    assert res.status_code == 201
    listing_id = res.json()["id"]

    ref = get_artisan_listing_reference(db_session, listing_id)
    assert ref.is_active is False

    # Admin approves listing
    approve_res = client.post(
        f"/api/v1/admin/artisan/listings/{listing_id}/approve",
        headers=auth_header(admin_user),
    )
    assert approve_res.status_code == 200

    db_session.refresh(ref)
    assert ref.is_active is True


def test_listing_rejection_and_suspension_deactivates_shared_reference(
    client, approved_listing_and_product, admin_user, db_session
):
    """When an admin rejects or suspends an approved listing, shared ListingReference becomes is_active=False."""
    listing, _ = approved_listing_and_product

    ref = get_artisan_listing_reference(db_session, listing.id)
    assert ref is not None
    assert ref.is_active is True

    # Admin suspends listing
    suspend_res = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/suspend",
        json={"reason": "Policy compliance audit required"},
        headers=auth_header(admin_user),
    )
    assert suspend_res.status_code == 200

    db_session.refresh(ref)
    assert ref.is_active is False


def test_listing_soft_delete_and_update_synchronization(
    client, approved_listing_and_product, artisan_provider_user, db_session
):
    """When a provider updates or soft-deletes their listing, the shared reference stays in sync."""
    listing, _ = approved_listing_and_product
    user, _ = artisan_provider_user

    # Provider updates title and district
    update_res = client.put(
        f"/api/v1/artisan-listings/{listing.id}",
        json={"title": "Updated Galle Fort Art Studio", "district": "Galle", "city": "Unawatuna"},
        headers=auth_header(user),
    )
    assert update_res.status_code == 200

    ref = get_artisan_listing_reference(db_session, listing.id)
    db_session.refresh(ref)
    assert ref.title == "Updated Galle Fort Art Studio"
    assert ref.city == "Unawatuna"
    assert ref.is_active is True

    # Provider soft-deletes listing
    del_res = client.delete(
        f"/api/v1/artisan-listings/{listing.id}",
        headers=auth_header(user),
    )
    assert del_res.status_code == 200

    db_session.refresh(ref)
    assert ref.is_active is False


def test_listing_reference_idempotent_duplicate_prevention(db_session, artisan_provider_user):
    """sync_artisan_listing_reference must be completely idempotent and never insert duplicate rows."""
    _, profile = artisan_provider_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Matara Lace Making Studio",
        craft_category="Lacework",
        district="Matara",
        city="Matara",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    # Call sync multiple times
    ref1 = sync_artisan_listing_reference(db_session, listing)
    db_session.commit()
    ref2 = sync_artisan_listing_reference(db_session, listing)
    db_session.commit()
    ref3 = sync_artisan_listing_reference(db_session, listing)
    db_session.commit()

    assert ref1.id == ref2.id == ref3.id

    # Verify count in database
    count = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "artisan",
            ListingReference.target_id == listing.id,
        )
        .count()
    )
    assert count == 1


# ==============================================================================
# 2. Booking Reference Creation, Module Type & Parity Tests
# ==============================================================================

def test_artisan_reservation_creation_creates_shared_booking_reference(
    client, approved_listing_and_product, traveler_user, artisan_provider_user, db_session
):
    """When a reservation is created, a shared BookingReference and BookingStatusHistory are created."""
    listing, product = approved_listing_and_product
    _, profile = artisan_provider_user

    pickup_date = (date.today() + timedelta(days=2)).isoformat()
    res = client.post(
        "/api/v1/artisan-reservations",
        json={
            "showcase_item_id": product.id,
            "quantity": 2,
            "pickup_date": pickup_date,
            "special_notes": "Gift wrap please",
        },
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 201
    reservation_id = res.json()["id"]

    # Verify shared BookingReference
    b_ref = get_artisan_booking_reference(db_session, reservation_id)
    assert b_ref is not None
    assert b_ref.module_type == ARTISAN_RESERVATION_MODULE_TYPE
    assert b_ref.module_type == "artisan_reservation"
    assert b_ref.target_id == reservation_id
    assert b_ref.user_id == traveler_user.id
    assert b_ref.provider_profile_id == profile.id
    assert b_ref.total_amount == Decimal("25000.00")
    assert b_ref.currency == "LKR"
    assert b_ref.booking_status == "pending"
    assert b_ref.payment_status == "unpaid"
    assert b_ref.booking_reference_code.startswith(f"ART-RES-{reservation_id:06d}-")

    # Property alias verification (Migration 027 column compatibility)
    assert b_ref.booking_reference == b_ref.booking_reference_code
    assert b_ref.target_booking_id == b_ref.target_id
    assert b_ref.total_price == b_ref.total_amount

    # Verify BookingStatusHistory
    history = (
        db_session.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
        .all()
    )
    assert len(history) == 1
    # Both physical column from_status and synonym old_status are None for initial state
    assert history[0].from_status is None
    assert history[0].old_status is None
    assert history[0].to_status == "pending"
    assert history[0].new_status == "pending"
    assert history[0].changed_by == traveler_user.id


def test_artisan_reservation_lifecycle_status_synchronization(
    client, approved_listing_and_product, traveler_user, artisan_provider_user, db_session
):
    """
    Status transitions:
    pending -> confirmed -> ready_for_pickup -> completed
    Payment status transitions:
    'unpaid' -> 'unpaid' -> 'unpaid' -> 'paid' upon completion!
    """
    listing, product = approved_listing_and_product
    provider_user, profile = artisan_provider_user

    pickup_date = (date.today() + timedelta(days=1)).isoformat()
    create_res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": product.id, "quantity": 1, "pickup_date": pickup_date},
        headers=auth_header(traveler_user),
    )
    assert create_res.status_code == 201
    reservation_id = create_res.json()["id"]

    b_ref = get_artisan_booking_reference(db_session, reservation_id)
    assert b_ref.booking_status == "pending"
    assert b_ref.payment_status == "unpaid"

    # 1. Provider confirms reservation
    confirm_res = client.patch(
        f"/api/v1/artisan-reservations/{reservation_id}/confirm",
        headers=auth_header(provider_user),
    )
    assert confirm_res.status_code == 200
    db_session.refresh(b_ref)
    assert b_ref.booking_status == "confirmed"
    assert b_ref.payment_status == "unpaid"

    # 2. Provider marks ready for pickup
    ready_res = client.patch(
        f"/api/v1/artisan-reservations/{reservation_id}/ready",
        headers=auth_header(provider_user),
    )
    assert ready_res.status_code == 200
    db_session.refresh(b_ref)
    assert b_ref.booking_status == "ready_for_pickup"
    assert b_ref.payment_status == "unpaid"

    # 3. Provider completes in-store pickup
    complete_res = client.post(
        f"/api/v1/artisan-reservations/{reservation_id}/complete-pickup",
        headers=auth_header(provider_user),
    )
    assert complete_res.status_code == 200
    db_session.refresh(b_ref)
    assert b_ref.booking_status == "completed"
    # Handcrafted product picked up in shop: cash/card payment collected!
    assert b_ref.payment_status == "paid"

    # Verify history entries in order
    history = (
        db_session.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
        .order_by(BookingStatusHistory.id.asc())
        .all()
    )
    assert len(history) == 4
    assert history[0].from_status is None and history[0].to_status == "pending"
    assert history[0].old_status is None and history[0].new_status == "pending"
    assert history[1].old_status == "pending" and history[1].new_status == "confirmed"
    assert history[2].old_status == "confirmed" and history[2].new_status == "ready_for_pickup"
    assert history[3].old_status == "ready_for_pickup" and history[3].new_status == "completed"


def test_artisan_reservation_cancellation_synchronization(
    client, approved_listing_and_product, traveler_user, db_session
):
    """When a reservation is cancelled, shared reference booking_status becomes 'cancelled'."""
    listing, product = approved_listing_and_product

    pickup_date = (date.today() + timedelta(days=2)).isoformat()
    create_res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": product.id, "quantity": 1, "pickup_date": pickup_date},
        headers=auth_header(traveler_user),
    )
    assert create_res.status_code == 201
    reservation_id = create_res.json()["id"]

    # Traveler cancels reservation
    cancel_res = client.post(
        f"/api/v1/artisan-reservations/{reservation_id}/cancel",
        json={"cancellation_reason": "Travel plans altered"},
        headers=auth_header(traveler_user),
    )
    assert cancel_res.status_code == 200

    b_ref = get_artisan_booking_reference(db_session, reservation_id)
    db_session.refresh(b_ref)
    assert b_ref.booking_status == "cancelled"
    assert b_ref.payment_status == "unpaid"

    # Verify cancellation audit in history
    history = (
        db_session.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
        .order_by(BookingStatusHistory.id.desc())
        .first()
    )
    assert history.new_status == "cancelled"
    assert history.change_reason == "Travel plans altered"
    assert history.changed_by == traveler_user.id


def test_artisan_reservation_auto_expiry_synchronization(
    db_session, approved_listing_and_product, traveler_user
):
    """When a past-due reservation is auto-expired, shared BookingReference status becomes 'expired'."""
    listing, product = approved_listing_and_product

    past_expiry = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=2)
    reservation = ArtisanReservation(
        showcase_item_id=product.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=2,
        total_price=Decimal("25000.00"),
        currency="LKR",
        reservation_status="pending",
        pickup_date=date.today() - timedelta(days=1),
        expires_at=past_expiry,
    )
    db_session.add(reservation)
    product.stock_quantity -= 2
    db_session.commit()
    db_session.refresh(reservation)

    # Initial booking reference creation
    sync_artisan_booking_reference(db_session, reservation)
    db_session.commit()

    b_ref = get_artisan_booking_reference(db_session, reservation.id)
    assert b_ref.booking_status == "pending"

    # Run auto-expiry service
    success, msg, restored = expire_single_reservation(db_session, reservation.id)
    assert success is True
    assert restored == 2

    db_session.refresh(b_ref)
    assert b_ref.booking_status == "expired"
    assert b_ref.payment_status == "unpaid"

    history = (
        db_session.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
        .order_by(BookingStatusHistory.id.desc())
        .first()
    )
    assert history.new_status == "expired"


def test_booking_reference_idempotent_duplicate_prevention(
    db_session, approved_listing_and_product, traveler_user
):
    """Calling sync_artisan_booking_reference repeatedly must never create duplicate booking references."""
    listing, product = approved_listing_and_product

    reservation = ArtisanReservation(
        showcase_item_id=product.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("12500.00"),
        currency="LKR",
        reservation_status="pending",
        pickup_date=date.today() + timedelta(days=1),
    )
    db_session.add(reservation)
    db_session.commit()
    db_session.refresh(reservation)

    # Call sync multiple times
    ref1 = sync_artisan_booking_reference(db_session, reservation)
    db_session.commit()
    ref2 = sync_artisan_booking_reference(db_session, reservation)
    db_session.commit()
    ref3 = sync_artisan_booking_reference(db_session, reservation)
    db_session.commit()

    assert ref1.id == ref2.id == ref3.id

    # Count records in database
    count = (
        db_session.query(BookingReference)
        .filter(
            BookingReference.module_type == "artisan_reservation",
            BookingReference.target_id == reservation.id,
        )
        .count()
    )
    assert count == 1


def test_atomic_transaction_rollback_guarantee(db_session, approved_listing_and_product, traveler_user):
    """If a reservation transaction fails before commit, shared BookingReference is rolled back cleanly."""
    listing, product = approved_listing_and_product

    # Start a transaction block and force a rollback
    try:
        reservation = ArtisanReservation(
            showcase_item_id=product.id,
            artisan_listing_id=listing.id,
            traveler_id=traveler_user.id,
            quantity=1,
            total_price=Decimal("12500.00"),
            currency="LKR",
            reservation_status="pending",
            pickup_date=date.today() + timedelta(days=1),
        )
        db_session.add(reservation)
        db_session.flush()

        sync_artisan_booking_reference(db_session, reservation)
        db_session.flush()

        # Simulate unexpected failure before commit
        raise RuntimeError("Simulated transaction failure")
    except RuntimeError:
        db_session.rollback()

    # Verify that neither the reservation nor the booking reference was committed
    assert db_session.query(ArtisanReservation).filter(ArtisanReservation.showcase_item_id == product.id).first() is None
    assert db_session.query(BookingReference).filter(BookingReference.module_type == "artisan_reservation").first() is None


# ==============================================================================
# 3. SQL Server Migration 027 Physical Column & Synonym Compatibility Tests
# ==============================================================================

def test_physical_sql_column_names_match_migration_027_exactly():
    """
    Verifies that SQLAlchemy Table physical column names match Migration 027 T-SQL schema 100%.
    Guarantees no invalid column names are queried or inserted against SQL Server.
    """
    # 1. dbo.booking_references
    booking_cols = [c.name for c in BookingReference.__table__.columns]
    expected_booking_cols = [
        "id",
        "booking_reference",
        "module_type",
        "target_booking_id",
        "user_id",
        "provider_profile_id",
        "total_price",
        "currency",
        "booking_status",
        "payment_status",
        "created_at",
    ]
    assert booking_cols == expected_booking_cols, f"Mismatch in BookingReference columns: {booking_cols}"

    # Verify nonexistent ORM columns are strictly excluded from physical table
    assert "start_time" not in booking_cols
    assert "end_time" not in booking_cols
    assert "updated_at" not in booking_cols
    assert "booking_reference_code" not in booking_cols
    assert "target_id" not in booking_cols
    assert "total_amount" not in booking_cols

    # 2. dbo.listing_references
    listing_cols = [c.name for c in ListingReference.__table__.columns]
    expected_listing_cols = [
        "id",
        "module_type",
        "target_id",
        "provider_profile_id",
        "title",
        "district",
        "city",
        "latitude",
        "longitude",
        "is_active",
        "created_at",
    ]
    assert listing_cols == expected_listing_cols, f"Mismatch in ListingReference columns: {listing_cols}"

    # 3. dbo.booking_status_history
    history_cols = [c.name for c in BookingStatusHistory.__table__.columns]
    expected_history_cols = [
        "id",
        "booking_reference_id",
        "from_status",
        "to_status",
        "changed_by",
        "notes",
        "created_at",
    ]
    assert history_cols == expected_history_cols, f"Mismatch in BookingStatusHistory columns: {history_cols}"
    assert "old_status" not in history_cols
    assert "new_status" not in history_cols
    assert "change_reason" not in history_cols


def test_booking_reference_mssql_query_generation_and_synonyms(db_session, approved_listing_and_product, traveler_user):
    """
    Verifies that MSSQL SQL compiler compiles queries using physical column names
    and that both physical attributes and synonyms work identically.
    """
    listing, product = approved_listing_and_product

    # Create reference using synonym kwargs
    ref = BookingReference(
        booking_reference_code="ART-RES-SYN-001",
        module_type="artisan_reservation",
        target_id=999,
        user_id=traveler_user.id,
        provider_profile_id=listing.provider_profile_id,
        total_amount=Decimal("15000.00"),
        booking_status="pending",
        payment_status="unpaid",
    )
    db_session.add(ref)
    db_session.commit()
    db_session.refresh(ref)

    # Test physical property and synonym attribute access
    assert ref.booking_reference == "ART-RES-SYN-001"
    assert ref.booking_reference_code == "ART-RES-SYN-001"
    assert ref.target_booking_id == 999
    assert ref.target_id == 999
    assert ref.total_price == Decimal("15000.00")
    assert ref.total_amount == Decimal("15000.00")

    # Test in-memory non-column properties do not error
    assert ref.start_time is None
    ref.start_time = datetime(2026, 11, 20, 10, 0, 0)
    assert ref.start_time == datetime(2026, 11, 20, 10, 0, 0)

    # Test compiled MSSQL query does NOT include start_time, end_time, updated_at
    stmt = select(BookingReference).filter(BookingReference.target_id == 999)
    mssql_sql = str(stmt.compile(dialect=mssql.dialect()))
    assert "booking_references.target_booking_id" in mssql_sql
    assert "booking_references.booking_reference" in mssql_sql
    assert "booking_references.total_price" in mssql_sql
    assert "start_time" not in mssql_sql
    assert "end_time" not in mssql_sql
    assert "updated_at" not in mssql_sql

    # Test querying using synonym works in session
    found_via_synonym = db_session.query(BookingReference).filter(BookingReference.target_id == 999).first()
    assert found_via_synonym is not None
    assert found_via_synonym.id == ref.id

    # Test querying using physical name works in session
    found_via_physical = db_session.query(BookingReference).filter(BookingReference.target_booking_id == 999).first()
    assert found_via_physical is not None
    assert found_via_physical.id == ref.id


def test_booking_status_history_mssql_query_generation_and_synonyms(db_session, approved_listing_and_product, traveler_user):
    """
    Verifies that BookingStatusHistory MSSQL queries use physical columns:
    from_status, to_status, notes, while synonyms (old_status, new_status, change_reason) work seamlessly.
    """
    listing, product = approved_listing_and_product

    # Setup parent booking reference
    b_ref = BookingReference(
        booking_reference="ART-RES-HIST-001",
        module_type="artisan_reservation",
        target_booking_id=888,
        user_id=traveler_user.id,
        provider_profile_id=listing.provider_profile_id,
        total_price=Decimal("5000.00"),
    )
    db_session.add(b_ref)
    db_session.commit()

    # Create history using synonym kwargs
    history = BookingStatusHistory(
        booking_reference_id=b_ref.id,
        old_status="pending",
        new_status="confirmed",
        changed_by=traveler_user.id,
        change_reason="Provider confirmed online",
    )
    db_session.add(history)
    db_session.commit()
    db_session.refresh(history)

    # Physical vs synonym attribute access
    assert history.from_status == "pending"
    assert history.old_status == "pending"
    assert history.to_status == "confirmed"
    assert history.new_status == "confirmed"
    assert history.notes == "Provider confirmed online"
    assert history.change_reason == "Provider confirmed online"

    # Compiled MSSQL query inspection
    stmt = select(BookingStatusHistory).filter(BookingStatusHistory.new_status == "confirmed")
    mssql_sql = str(stmt.compile(dialect=mssql.dialect()))
    assert "booking_status_history.from_status" in mssql_sql
    assert "booking_status_history.to_status" in mssql_sql
    assert "booking_status_history.notes" in mssql_sql
    assert "old_status" not in mssql_sql
    assert "change_reason" not in mssql_sql
