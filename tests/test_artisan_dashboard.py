"""
Automated test suite for Phase P1 Step 4: Secure Artisan Provider Dashboard Summary API.
Tests:
1. Empty provider dashboard returns exact zero values.
2. Dashboard with multiple listings (approved, pending, rejected, suspended, active/inactive).
3. Product counts by inventory and approval status.
4. Reservation counts by status (pending, confirmed, ready_for_pickup, completed, cancelled, expired).
5. Completed pickup value calculation (only completed reservations counted in LKR).
6. Recent reservation ordering (5 most recent reservations).
7. Multi-provider data isolation (no cross-tenant leakage).
8. Unauthorized traveler access (403 Forbidden).
9. Unauthenticated access (401/403).
10. Non-artisan provider access blocked (403 Forbidden).
11. Active promotions overview integration.
"""

from datetime import UTC, date, datetime, timedelta
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
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User

# In-memory SQLite engine
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


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def traveler_user(db_session) -> User:
    traveler = User(
        full_name="Traveler Anura",
        email="anura.dash@example.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    return traveler


@pytest.fixture
def artisan_provider_1(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Ranil",
        email="ranil.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Ranil Brass Center",
        provider_type="artisan",
        district="Kandy",
        approval_status="approved",
        phone="+94771112233",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def artisan_provider_2(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Rohana",
        email="rohana.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Rohana Wood Crafts",
        provider_type="artisan",
        district="Galle",
        approval_status="approved",
        phone="+94774445566",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def homestay_provider(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Homestay Gamage",
        email="gamage.homestay@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Gamage Homestay",
        provider_type="homestay",
        district="Nuwara Eliya",
        approval_status="approved",
        phone="+94777778899",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


# ==============================================================================
# TESTS
# ==============================================================================

def test_empty_provider_dashboard(client: TestClient, artisan_provider_1):
    """Verify empty provider dashboard returns all zero counts and empty lists."""
    user, profile = artisan_provider_1

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    data = res.json()

    assert data["provider_id"] == profile.id
    assert data["provider_business_name"] == "Ranil Brass Center"

    # Business Overview
    bus = data["business_overview"]
    assert bus["total_listings"] == 0
    assert bus["approved_listings"] == 0
    assert bus["pending_listings"] == 0
    assert bus["rejected_listings"] == 0
    assert bus["active_listings"] == 0

    # Product Overview
    prod = data["product_overview"]
    assert prod["total_products"] == 0
    assert prod["in_stock_products"] == 0
    assert prod["out_of_stock_products"] == 0

    # Reservation Overview
    resv = data["reservation_overview"]
    assert resv["total_reservations"] == 0
    assert resv["completed_reservations"] == 0

    # Financial Summary
    fin = data["financial_summary"]
    assert Decimal(str(fin["recorded_completed_pickup_value_lkr"])) == Decimal("0.00")
    assert fin["currency"] == "LKR"

    # Recent items
    assert data["recent_reservations"] == []
    assert data["recent_products"] == []
    assert data["pending_approval"]["requires_action"] is False
    assert data["promotions_overview"]["total_promotions"] == 0


def test_dashboard_with_multiple_listings(
    client: TestClient, db_session, artisan_provider_1
):
    """Verify business listing counts by approval and active status."""
    user, profile = artisan_provider_1

    listing1 = ArtisanListing(
        provider_profile_id=profile.id,
        title="Listing Approved Active",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Pilimathalawa",
        is_active=True,
        approval_status="approved",
        verification_status="verified",
    )
    listing2 = ArtisanListing(
        provider_profile_id=profile.id,
        title="Listing Pending Active",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="pending",
        verification_status="unverified",
    )
    listing3 = ArtisanListing(
        provider_profile_id=profile.id,
        title="Listing Rejected Inactive",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Peradeniya",
        is_active=False,
        approval_status="rejected",
        verification_status="unverified",
    )
    db_session.add_all([listing1, listing2, listing3])
    db_session.commit()

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    data = res.json()
    bus = data["business_overview"]
    assert bus["total_listings"] == 3
    assert bus["approved_listings"] == 1
    assert bus["pending_listings"] == 1
    assert bus["rejected_listings"] == 1
    assert bus["active_listings"] == 2
    assert bus["inactive_listings"] == 1
    assert bus["verified_listings"] == 1
    assert bus["unverified_listings"] == 2


def test_product_counts_by_status(
    client: TestClient, db_session, artisan_provider_1
):
    """Verify product counts by active, available, in-stock, and approval status."""
    user, profile = artisan_provider_1

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Main Brass Shop",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()

    # Product 1: Approved, Active, In stock (5)
    p1 = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Brass Oil Lamp",
        item_type="product",
        price=Decimal("8500.00"),
        stock_quantity=5,
        is_active=True,
        is_available=True,
        approval_status="approved",
    )
    # Product 2: Approved, Active, Out of stock (0)
    p2 = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Brass Betel Box",
        item_type="product",
        price=Decimal("4200.00"),
        stock_quantity=0,
        is_active=True,
        is_available=False,
        approval_status="approved",
    )
    # Product 3: Pending, Inactive, In stock (2)
    p3 = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Brass Ganesha Figurine",
        item_type="product",
        price=Decimal("15000.00"),
        stock_quantity=2,
        is_active=False,
        is_available=True,
        approval_status="pending",
    )
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    prod = res.json()["product_overview"]
    assert prod["total_products"] == 3
    assert prod["active_products"] == 2
    assert prod["inactive_products"] == 1
    assert prod["in_stock_products"] == 2
    assert prod["out_of_stock_products"] == 1
    assert prod["approved_products"] == 2
    assert prod["pending_products"] == 1
    assert res.json()["pending_approval"]["pending_products_count"] == 1
    assert res.json()["pending_approval"]["requires_action"] is True


def test_reservation_counts_and_completed_value(
    client: TestClient, db_session, artisan_provider_1, traveler_user
):
    """Verify reservation breakdown by status and accurate completed-pickup value in LKR."""
    user, profile = artisan_provider_1

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Brass Workshop",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Temple Lamp",
        item_type="product",
        price=Decimal("5000.00"),
        stock_quantity=20,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(item)
    db_session.commit()

    # 1. Completed reservation of 5,000.00 LKR
    r1 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    # 2. Completed reservation of 7,500.00 LKR
    r2 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=2,
        total_price=Decimal("7500.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    # 3. Pending reservation of 3,000.00 LKR (should NOT be added to completed value)
    r3 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("3000.00"),
        currency="LKR",
        reservation_status="pending",
        pickup_date=date.today() + timedelta(days=2),
    )
    # 4. Ready for pickup reservation of 2,000.00 LKR
    r4 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("2000.00"),
        currency="LKR",
        reservation_status="ready_for_pickup",
        pickup_date=date.today() + timedelta(days=1),
    )
    # 5. Cancelled reservation of 4,000.00 LKR
    r5 = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("4000.00"),
        currency="LKR",
        reservation_status="cancelled",
        pickup_date=date.today(),
    )
    db_session.add_all([r1, r2, r3, r4, r5])
    db_session.commit()

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    data = res.json()

    resv = data["reservation_overview"]
    assert resv["total_reservations"] == 5
    assert resv["completed_reservations"] == 2
    assert resv["pending_reservations"] == 1
    assert resv["ready_for_pickup_reservations"] == 1
    assert resv["cancelled_reservations"] == 1

    # Financial: 5,000.00 + 7,500.00 = 12,500.00 LKR
    fin = data["financial_summary"]
    assert Decimal(str(fin["recorded_completed_pickup_value_lkr"])) == Decimal("12500.00")


def test_recent_reservation_ordering(
    client: TestClient, db_session, artisan_provider_1, traveler_user
):
    """Verify recent reservations return at most 5 items in descending chronological order."""
    user, profile = artisan_provider_1

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Chronological Shop",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Chronological Item",
        item_type="product",
        price=Decimal("1000.00"),
        stock_quantity=50,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(item)
    db_session.commit()

    now = datetime.now(UTC)
    reservations = []
    for i in range(7):
        r = ArtisanReservation(
            showcase_item_id=item.id,
            artisan_listing_id=listing.id,
            traveler_id=traveler_user.id,
            quantity=1,
            total_price=Decimal(f"{1000 + i * 100}.00"),
            currency="LKR",
            reservation_status="pending",
            pickup_date=date.today(),
            created_at=now + timedelta(minutes=i),
        )
        reservations.append(r)
    db_session.add_all(reservations)
    db_session.commit()

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    recent = res.json()["recent_reservations"]
    assert len(recent) == 5

    # Should be newest first (index 6 down to 2)
    prices = [Decimal(str(r["total_price"])) for r in recent]
    assert prices == [
        Decimal("1600.00"),
        Decimal("1500.00"),
        Decimal("1400.00"),
        Decimal("1300.00"),
        Decimal("1200.00"),
    ]
    assert recent[0]["item_title"] == "Chronological Item"
    assert recent[0]["traveler_name"] == "Traveler Anura"


def test_multi_provider_data_isolation(
    client: TestClient, db_session, artisan_provider_1, artisan_provider_2, traveler_user
):
    """Verify strict multi-tenant isolation: Provider A sees only Provider A's metrics."""
    user1, profile1 = artisan_provider_1
    user2, profile2 = artisan_provider_2

    # Listing for Provider 1
    l1 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Provider 1 Shop",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="approved",
    )
    # Listing for Provider 2
    l2 = ArtisanListing(
        provider_profile_id=profile2.id,
        title="Provider 2 Shop",
        craft_category="Woodcarving & Masks",
        district="Galle",
        city="Ambalangoda",
        is_active=True,
        approval_status="approved",
    )
    db_session.add_all([l1, l2])
    db_session.commit()

    # Product for Provider 1
    item1 = ShowcaseItem(
        artisan_listing_id=l1.id,
        title="Brass Plate",
        item_type="product",
        price=Decimal("4000.00"),
        stock_quantity=5,
        is_active=True,
        approval_status="approved",
    )
    # Product for Provider 2
    item2 = ShowcaseItem(
        artisan_listing_id=l2.id,
        title="Wooden Mask",
        item_type="product",
        price=Decimal("9000.00"),
        stock_quantity=10,
        is_active=True,
        approval_status="approved",
    )
    db_session.add_all([item1, item2])
    db_session.commit()

    # Reservation on Provider 2's item (Completed: 9,000 LKR)
    resv2 = ArtisanReservation(
        showcase_item_id=item2.id,
        artisan_listing_id=l2.id,
        traveler_id=traveler_user.id,
        quantity=1,
        total_price=Decimal("9000.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    db_session.add(resv2)
    db_session.commit()

    # Provider 1 checks dashboard: should have 1 listing, 1 product, 0 reservations, 0 value
    res1 = client.get("/api/v1/artisan/dashboard", headers=auth_header(user1))
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["business_overview"]["total_listings"] == 1
    assert data1["product_overview"]["total_products"] == 1
    assert data1["reservation_overview"]["total_reservations"] == 0
    assert Decimal(str(data1["financial_summary"]["recorded_completed_pickup_value_lkr"])) == Decimal("0.00")

    # Provider 2 checks dashboard: should have 1 listing, 1 product, 1 reservation, 9,000 value
    res2 = client.get("/api/v1/artisan/dashboard", headers=auth_header(user2))
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["business_overview"]["total_listings"] == 1
    assert data2["product_overview"]["total_products"] == 1
    assert data2["reservation_overview"]["total_reservations"] == 1
    assert Decimal(str(data2["financial_summary"]["recorded_completed_pickup_value_lkr"])) == Decimal("9000.00")


def test_promotions_overview_integration(
    client: TestClient, db_session, artisan_provider_1
):
    """Verify promotions overview reflects active discounts correctly without invented metrics."""
    user, profile = artisan_provider_1

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Promotions Studio",
        craft_category="Brass & Metalware",
        district="Kandy",
        city="Kandy",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(listing)
    db_session.commit()

    now = datetime.now(UTC)
    # Active promotion
    promo_active = Promotion(
        artisan_listing_id=listing.id,
        title="Avurudu 15% Off",
        promo_code="AVURUDU15",
        discount_percentage=Decimal("15.00"),
        start_date=now - timedelta(days=2),
        end_date=now + timedelta(days=10),
        is_active=True,
    )
    # Expired promotion
    promo_expired = Promotion(
        artisan_listing_id=listing.id,
        title="New Year Special",
        promo_code="NEWYEAR",
        discount_amount=Decimal("500.00"),
        start_date=now - timedelta(days=30),
        end_date=now - timedelta(days=5),
        is_active=True,
    )
    db_session.add_all([promo_active, promo_expired])
    db_session.commit()

    res = client.get("/api/v1/artisan/dashboard", headers=auth_header(user))
    assert res.status_code == 200
    promo_data = res.json()["promotions_overview"]
    assert promo_data["total_promotions"] == 2
    assert promo_data["active_promotions_count"] == 1
    assert len(promo_data["active_promotions"]) == 1
    assert promo_data["active_promotions"][0]["promo_code"] == "AVURUDU15"


def test_unauthorized_and_forbidden_access(
    client: TestClient, traveler_user, homestay_provider
):
    """Verify travelers, unauthenticated users, and non-artisan providers cannot access the dashboard."""
    # 1. Unauthenticated -> 401 or 403
    res_unauth = client.get("/api/v1/artisan/dashboard")
    assert res_unauth.status_code in (401, 403)

    # 2. Traveler -> 403
    res_traveler = client.get("/api/v1/artisan/dashboard", headers=auth_header(traveler_user))
    assert res_traveler.status_code == 403

    # 3. Homestay provider -> 403 Forbidden
    homestay_user, _ = homestay_provider
    res_homestay = client.get("/api/v1/artisan/dashboard", headers=auth_header(homestay_user))
    assert res_homestay.status_code == 403
    assert "only artisan service providers" in res_homestay.json()["detail"]
