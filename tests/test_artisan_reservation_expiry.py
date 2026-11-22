"""
Tests for Artisan Product Reservation Auto-Expiry & Stock Restoration (Phase P1 Step 7).
Verifies safe, idempotent expiration of past-due reservations:
1. Expired pending reservation transitions to 'expired'.
2. Stock is restored correctly back to showcase product inventory.
3. Duplicate expiry attempts do not restore stock twice (idempotence).
4. Completed reservation is never expired.
5. Cancelled reservation is not expired again (no double stock restoration).
6. Reservation without expires_at timestamp is not expired.
7. Concurrent cancellation and expiry race condition protection.
8. UTC expiry comparison (future vs past).
9. Multi-provider ownership and data integrity isolation.
10. Admin HTTP trigger endpoint permissions and reporting.
"""

from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_reservation_expiry import (
    DEFAULT_ELIGIBLE_STATUSES,
    expire_single_reservation,
    process_expired_reservations,
)

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
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def make_artisan_context(db_session, prefix: str = "TestArtisan", initial_stock: int = 10):
    user = User(
        full_name=f"{prefix} User",
        email=f"{prefix.lower()}@artisan.test",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name=f"{prefix} Crafts",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title=f"{prefix} Workshop",
        craft_category="Woodcarving",
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title=f"{prefix} Wooden Mask",
        item_type="product",
        price=Decimal("3500.00"),
        stock_quantity=initial_stock,
        approval_status="approved",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    traveler = User(
        full_name=f"{prefix} Traveler",
        email=f"traveler_{prefix.lower()}@traveler.test",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)

    return user, profile, listing, item, traveler


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Expired Pending Reservation & Stock Restored Correctly
# ==============================================================================

def test_expired_pending_reservation_and_stock_restoration(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "PendingExpiry", initial_stock=10)

    # Simulate reservation of 3 items (stock becomes 7)
    item.stock_quantity -= 3
    db_session.commit()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past_expiry = now - timedelta(hours=2)

    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=3,
        total_price=Decimal("10500.00"),
        reservation_status="pending",
        pickup_date=(now - timedelta(days=1)).date(),
        expires_at=past_expiry,
    )
    db_session.add(reservation)
    db_session.commit()
    db_session.refresh(reservation)

    assert item.stock_quantity == 7
    assert reservation.reservation_status == "pending"

    # Execute expiry
    success, message, restored = expire_single_reservation(db_session, reservation.id, now=now)
    assert success is True
    assert restored == 3

    db_session.refresh(reservation)
    db_session.refresh(item)

    assert reservation.reservation_status == "expired"
    assert item.stock_quantity == 10  # 7 + 3 restored


# ==============================================================================
# 2. Batch Processing Restores Stock Correctly
# ==============================================================================

def test_batch_processing_restores_stock(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "BatchTest", initial_stock=20)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Create 3 reservations of 2 items each (6 items deducted, stock: 14)
    item.stock_quantity -= 6
    db_session.commit()

    res1 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="pending",
        expires_at=now - timedelta(hours=3),
    )
    res2 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="confirmed",
        expires_at=now - timedelta(hours=2),
    )
    res3 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="ready_for_pickup",
        expires_at=now - timedelta(hours=1),
    )
    db_session.add_all([res1, res2, res3])
    db_session.commit()

    assert item.stock_quantity == 14

    result = process_expired_reservations(db_session, batch_size=10, now=now)
    assert result.scanned_count == 3
    assert result.expired_count == 3
    assert result.restored_stock_total == 6

    db_session.refresh(item)
    assert item.stock_quantity == 20  # 14 + 6 restored


# ==============================================================================
# 3. Duplicate Expiry Does Not Restore Stock Twice (Idempotency)
# ==============================================================================

def test_duplicate_expiry_does_not_restore_stock_twice(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "IdempotentTest", initial_stock=10)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    item.stock_quantity -= 4
    db_session.commit()

    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=4,
        total_price=Decimal("14000.00"),
        reservation_status="pending",
        expires_at=now - timedelta(minutes=30),
    )
    db_session.add(reservation)
    db_session.commit()

    # First expiry run -> restores 4 items
    success1, _, restored1 = expire_single_reservation(db_session, reservation.id, now=now)
    assert success1 is True
    assert restored1 == 4

    db_session.refresh(item)
    assert item.stock_quantity == 10

    # Second expiry run -> must do nothing and return False
    success2, msg2, restored2 = expire_single_reservation(db_session, reservation.id, now=now)
    assert success2 is False
    assert restored2 == 0
    assert "already expired" in msg2.lower()

    db_session.refresh(item)
    assert item.stock_quantity == 10  # Stock must remain 10, not 14!

    # Batch run -> must skip
    batch_result = process_expired_reservations(db_session, now=now)
    assert batch_result.expired_count == 0
    db_session.refresh(item)
    assert item.stock_quantity == 10


# ==============================================================================
# 4. Completed Reservation is Never Expired
# ==============================================================================

def test_completed_reservation_is_not_expired(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "CompletedTest", initial_stock=5)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Sold and completed 2 items
    item.stock_quantity -= 2
    db_session.commit()

    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="completed",
        expires_at=now - timedelta(days=2),  # Past due, but completed
    )
    db_session.add(reservation)
    db_session.commit()

    assert item.stock_quantity == 3

    success, msg, restored = expire_single_reservation(db_session, reservation.id, now=now)
    assert success is False
    assert restored == 0
    assert "completed" in msg.lower()

    db_session.refresh(reservation)
    db_session.refresh(item)
    assert reservation.reservation_status == "completed"
    assert item.stock_quantity == 3  # Stock must NOT be restored


# ==============================================================================
# 5. Cancelled Reservation is Not Expired Again (No Double Restoration)
# ==============================================================================

def test_cancelled_reservation_is_not_expired_again(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "CancelledTest", initial_stock=10)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Reservation was cancelled and stock was already restored to 10
    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=3,
        total_price=Decimal("10500.00"),
        reservation_status="cancelled",
        cancelled_by=traveler.id,
        cancellation_reason="Changed mind",
        expires_at=now - timedelta(hours=5),
    )
    db_session.add(reservation)
    db_session.commit()

    assert item.stock_quantity == 10

    success, msg, restored = expire_single_reservation(db_session, reservation.id, now=now)
    assert success is False
    assert restored == 0
    assert "cancelled" in msg.lower()

    db_session.refresh(item)
    assert item.stock_quantity == 10  # Stock must NOT become 13


# ==============================================================================
# 6. Reservation Without Expiry Timestamp is Not Expired
# ==============================================================================

def test_reservation_without_expiry_timestamp(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "NoExpiryTime", initial_stock=10)

    item.stock_quantity -= 2
    db_session.commit()

    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="pending",
        expires_at=None,
    )
    db_session.add(reservation)
    db_session.commit()

    success, msg, restored = expire_single_reservation(db_session, reservation.id)
    assert success is False
    assert restored == 0
    assert "no expires_at timestamp" in msg.lower()

    db_session.refresh(item)
    assert item.stock_quantity == 8


# ==============================================================================
# 7. Concurrent Cancellation & Expiry Race Condition Protection
# ==============================================================================

def test_concurrent_cancellation_and_expiry(client: TestClient, db_session):
    user, _, listing, item, traveler = make_artisan_context(db_session, "RaceTest", initial_stock=10)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    item.stock_quantity -= 3
    db_session.commit()

    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=3,
        total_price=Decimal("10500.00"),
        reservation_status="pending",
        expires_at=now - timedelta(minutes=10),
    )
    db_session.add(reservation)
    db_session.commit()
    db_session.refresh(reservation)

    # 1. Expiry runs first
    success, _, restored = expire_single_reservation(db_session, reservation.id, now=now)
    assert success is True
    assert restored == 3
    db_session.refresh(item)
    assert item.stock_quantity == 10

    # 2. Subsequent API cancel attempt must be rejected with 400
    res_cancel = client.post(
        f"/api/v1/artisan-reservations/{reservation.id}/cancel",
        json={"cancellation_reason": "Late cancel attempt"},
        headers=auth_header(traveler),
    )
    assert res_cancel.status_code == 400
    assert "already expired" in res_cancel.json()["detail"].lower()

    # Stock must remain 10 (not double-restored to 13)
    db_session.refresh(item)
    assert item.stock_quantity == 10


# ==============================================================================
# 8. UTC Expiry Comparison
# ==============================================================================

def test_utc_expiry_comparison(db_session):
    _, _, listing, item, traveler = make_artisan_context(db_session, "UTCTest", initial_stock=10)

    now_utc = datetime.now(timezone.utc)

    # Reservation 1: Expired 5 minutes ago UTC
    res_past = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=1,
        total_price=Decimal("3500.00"),
        reservation_status="pending",
        expires_at=now_utc - timedelta(minutes=5),
    )
    # Reservation 2: Expires 10 minutes in the future UTC
    res_future = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        quantity=1,
        total_price=Decimal("3500.00"),
        reservation_status="pending",
        expires_at=now_utc + timedelta(minutes=10),
    )
    db_session.add_all([res_past, res_future])
    db_session.commit()

    result = process_expired_reservations(db_session, now=now_utc)
    assert result.scanned_count == 1
    assert result.expired_count == 1
    assert result.expired_reservations[0].reservation_id == res_past.id

    db_session.refresh(res_future)
    assert res_future.reservation_status == "pending"


# ==============================================================================
# 9. Multi-Provider Ownership & Data Integrity
# ==============================================================================

def test_multi_provider_ownership_and_data_integrity(db_session):
    _, _, listing1, item1, traveler1 = make_artisan_context(db_session, "ProviderA", initial_stock=10)
    _, _, listing2, item2, traveler2 = make_artisan_context(db_session, "ProviderB", initial_stock=20)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Provider A has an expired reservation of 2 items (stock: 8)
    item1.stock_quantity -= 2
    # Provider B has an active reservation of 5 items that is NOT expired (stock: 15)
    item2.stock_quantity -= 5
    db_session.commit()

    res_a = ArtisanReservation(
        showcase_item_id=item1.id,
        artisan_listing_id=listing1.id,
        traveler_id=traveler1.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="pending",
        expires_at=now - timedelta(hours=1),
    )
    res_b = ArtisanReservation(
        showcase_item_id=item2.id,
        artisan_listing_id=listing2.id,
        traveler_id=traveler2.id,
        quantity=5,
        total_price=Decimal("17500.00"),
        reservation_status="pending",
        expires_at=now + timedelta(hours=12),
    )
    db_session.add_all([res_a, res_b])
    db_session.commit()

    process_expired_reservations(db_session, now=now)

    db_session.refresh(item1)
    db_session.refresh(item2)

    # Provider A stock was restored (8 -> 10)
    assert item1.stock_quantity == 10
    # Provider B stock was NOT touched (remains 15)
    assert item2.stock_quantity == 15


# ==============================================================================
# 10. Admin HTTP Trigger Endpoint Permissions & Reporting
# ==============================================================================

def test_admin_process_expired_endpoint(client: TestClient, db_session):
    admin_user = User(
        full_name="Platform Admin",
        email="admin@ceylongo.test",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
    )
    traveler_user = User(
        full_name="Regular Traveler",
        email="traveler@test.com",
        password_hash=hash_password("Traveler123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin_user, traveler_user])
    db_session.commit()

    _, _, listing, item, _ = make_artisan_context(db_session, "AdminTrigger", initial_stock=10)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    item.stock_quantity -= 2
    db_session.commit()

    res = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=2,
        total_price=Decimal("7000.00"),
        reservation_status="pending",
        expires_at=now - timedelta(hours=1),
    )
    db_session.add(res)
    db_session.commit()

    # 1. Unauthenticated -> 401
    res_unauth = client.post("/api/v1/admin/artisan/reservations/process-expired")
    assert res_unauth.status_code == 401

    # 2. Traveler -> 403
    res_trav = client.post(
        "/api/v1/admin/artisan/reservations/process-expired",
        headers=auth_header(traveler_user),
    )
    assert res_trav.status_code == 403

    # 3. Admin -> 200
    res_admin = client.post(
        "/api/v1/admin/artisan/reservations/process-expired",
        headers=auth_header(admin_user),
    )
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["scanned_count"] >= 1
    assert data["expired_count"] >= 1
    assert data["restored_stock_total"] >= 2
    assert "successfully processed" in data["message"].lower()

    db_session.refresh(item)
    assert item.stock_quantity == 10
