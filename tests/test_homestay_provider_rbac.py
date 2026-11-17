"""
Automated regression tests for Homestay Provider RBAC and Ownership Stabilization (Phase 02).
Verifies:
1. Approved homestay provider access.
2. Pending provider rejection.
3. Rejected provider rejection.
4. Inactive provider rejection.
5. Wrong provider type rejection (e.g. artisan provider).
6. Cross-provider ownership protection.
7. Homestay promotion ownership protection.
8. Admin access override.
9. Traveler access restrictions.
10. Public homestay discovery and traveler booking compatibility.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.booking import Booking
from app.models.homestay import Homestay, HomestayAmenity, HomestayImage
from app.models.promotion import HomestayPromotion
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

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
    from unittest.mock import MagicMock
    session = TestingSessionLocal()
    original_execute = session.execute

    def execute_with_mock_lock(statement, params=None, **kwargs):
        if "sp_getapplock" in str(statement):
            lock_result = MagicMock()
            lock_result.scalar.return_value = 0
            return lock_result
        return original_execute(statement, params, **kwargs)

    session.execute = execute_with_mock_lock
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
    token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    return {"Authorization": f"Bearer {token}"}


# -----------------------------------------------------------------------------
# Fixtures for various user roles and provider profiles
# -----------------------------------------------------------------------------

@pytest.fixture
def approved_homestay_provider_a(db_session: Session) -> User:
    user = User(
        full_name="Amal Perera",
        email="amal.homestay@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Amal Kandy View Villa",
        provider_type="homestay",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def approved_homestay_provider_b(db_session: Session) -> User:
    user = User(
        full_name="Kamal Silva",
        email="kamal.homestay@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Kamal Galle Retreat",
        provider_type="homestay",
        phone="+94779876543",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def pending_homestay_provider(db_session: Session) -> User:
    user = User(
        full_name="Pending Nimal",
        email="pending.nimal@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Nimal Pending Villa",
        provider_type="homestay",
        phone="+94772223344",
        district="Matale",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def rejected_homestay_provider(db_session: Session) -> User:
    user = User(
        full_name="Rejected Ruwan",
        email="rejected.ruwan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Ruwan Rejected Homestay",
        provider_type="homestay",
        phone="+94773334455",
        district="Kandy",
        approval_status="rejected",
        rejection_reason="Unclear business verification documents.",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def inactive_homestay_provider(db_session: Session) -> User:
    user = User(
        full_name="Inactive Kasun",
        email="inactive.kasun@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Kasun Inactive Villa",
        provider_type="homestay",
        phone="+94774445566",
        district="Nuwara Eliya",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def approved_artisan_provider(db_session: Session) -> User:
    user = User(
        full_name="Sunil Woodcarver",
        email="sunil.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Sunil Traditional Masks",
        provider_type="artisan",
        phone="+94775556677",
        district="Ambalangoda",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def incomplete_provider(db_session: Session) -> User:
    user = User(
        full_name="Incomplete Provider",
        email="incomplete@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def traveler_user(db_session: Session) -> User:
    user = User(
        full_name="Jane Tourist",
        email="jane.tourist@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    user = User(
        full_name="Platform Admin",
        email="admin.officer@ceylongo.com",
        password_hash=hash_password("Password123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# -----------------------------------------------------------------------------
# 1. Approved Homestay Provider Access
# -----------------------------------------------------------------------------

def test_approved_homestay_provider_can_create_and_manage_homestay(
    client: TestClient, approved_homestay_provider_a: User
):
    headers = auth_header(approved_homestay_provider_a)

    # Create homestay
    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Kandy Scenic Homestay",
            "description": "Lovely villa with mountain views",
            "location": "Kandy",
            "price_per_night": 7500.00,
            "max_guests": 4,
            "images": [{"image_url": "https://example.com/kandy.jpg", "is_primary": True}],
            "amenities": ["WiFi", "Breakfast", "Mountain View"],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["name"] == "Kandy Scenic Homestay"
    assert data["owner_id"] == approved_homestay_provider_a.id
    assert data["provider_profile_id"] == approved_homestay_provider_a.provider_profile.id
    homestay_id = data["id"]

    # List own homestays
    res_list = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert any(h["id"] == homestay_id for h in items)

    # Update own homestay
    res_update = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Kandy Scenic Homestay Premium",
            "description": "Upgraded villa with luxury mountain views",
            "location": "Kandy",
            "price_per_night": 8500.00,
            "max_guests": 5,
            "is_active": True,
            "images": [{"image_url": "https://example.com/kandy2.jpg", "is_primary": True}],
            "amenities": ["WiFi", "Breakfast", "Mountain View", "AC"],
        },
        headers=headers,
    )
    assert res_update.status_code == 200
    assert res_update.json()["name"] == "Kandy Scenic Homestay Premium"
    assert res_update.json()["max_guests"] == 5


# -----------------------------------------------------------------------------
# 2. Pending Provider Rejection
# -----------------------------------------------------------------------------

def test_pending_homestay_provider_is_denied(
    client: TestClient, pending_homestay_provider: User
):
    headers = auth_header(pending_homestay_provider)

    res_create = client.post(
        "/api/v1/homestays",
        json={
            "name": "Pending Villa",
            "description": "Villa description",
            "location": "Matale",
            "price_per_night": 5000.00,
            "max_guests": 2,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res_create.status_code == 403
    assert "pending approval" in res_create.json()["detail"].lower()

    res_mine = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_mine.status_code == 403
    assert "pending approval" in res_mine.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 3. Rejected Provider Rejection
# -----------------------------------------------------------------------------

def test_rejected_homestay_provider_is_denied_with_feedback(
    client: TestClient, rejected_homestay_provider: User
):
    headers = auth_header(rejected_homestay_provider)

    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Rejected Villa",
            "description": "Villa description",
            "location": "Kandy",
            "price_per_night": 5000.00,
            "max_guests": 2,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert "rejected" in detail.lower()
    assert "Unclear business verification documents" in detail


# -----------------------------------------------------------------------------
# 4. Inactive Provider Rejection
# -----------------------------------------------------------------------------

def test_inactive_homestay_provider_is_denied(
    client: TestClient, inactive_homestay_provider: User
):
    headers = auth_header(inactive_homestay_provider)

    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Inactive Villa",
            "description": "Villa description",
            "location": "Nuwara Eliya",
            "price_per_night": 6000.00,
            "max_guests": 3,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res.status_code == 403


# -----------------------------------------------------------------------------
# 5. Wrong Provider Type Rejection (Artisan Provider Denied Homestay Management)
# -----------------------------------------------------------------------------

def test_artisan_provider_cannot_manage_homestays(
    client: TestClient, approved_artisan_provider: User
):
    headers = auth_header(approved_artisan_provider)

    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Artisan Trying Homestay",
            "description": "Should fail",
            "location": "Ambalangoda",
            "price_per_night": 4000.00,
            "max_guests": 2,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res.status_code == 403
    assert "only homestay service providers can manage homestays" in res.json()["detail"].lower()

    res_mine = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_mine.status_code == 403
    assert "only homestay service providers can manage homestays" in res_mine.json()["detail"].lower()


def test_incomplete_provider_registration_denied(
    client: TestClient, incomplete_provider: User
):
    headers = auth_header(incomplete_provider)

    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Incomplete Villa",
            "description": "Should fail",
            "location": "Colombo",
            "price_per_night": 4000.00,
            "max_guests": 2,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res.status_code in (403, 404)


# -----------------------------------------------------------------------------
# 6. Cross-Provider Ownership Protection
# -----------------------------------------------------------------------------

def test_provider_cannot_manage_another_providers_homestay(
    client: TestClient,
    approved_homestay_provider_a: User,
    approved_homestay_provider_b: User,
):
    headers_a = auth_header(approved_homestay_provider_a)
    headers_b = auth_header(approved_homestay_provider_b)

    # Provider A creates Homestay 1
    res_a = client.post(
        "/api/v1/homestays",
        json={
            "name": "Provider A Kandy Villa",
            "description": "Exclusive villa",
            "location": "Kandy",
            "price_per_night": 7000.00,
            "max_guests": 4,
            "images": [],
            "amenities": [],
        },
        headers=headers_a,
    )
    assert res_a.status_code == 201
    homestay_id = res_a.json()["id"]

    # Provider B attempts to update Provider A's homestay
    res_b_update = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Hijacked Villa",
            "description": "Tampered description",
            "location": "Galle",
            "price_per_night": 1000.00,
            "max_guests": 1,
            "is_active": True,
            "images": [],
            "amenities": [],
        },
        headers=headers_b,
    )
    assert res_b_update.status_code == 403
    assert "cannot manage this homestay" in res_b_update.json()["detail"].lower()

    # Provider B calling /mine does NOT see Provider A's homestay
    res_b_mine = client.get("/api/v1/homestays/mine", headers=headers_b)
    assert res_b_mine.status_code == 200
    assert not any(h["id"] == homestay_id for h in res_b_mine.json()["items"])


# -----------------------------------------------------------------------------
# 7. Homestay Promotion Ownership Protection
# -----------------------------------------------------------------------------

def test_homestay_promotion_ownership_protection(
    client: TestClient,
    approved_homestay_provider_a: User,
    approved_homestay_provider_b: User,
):
    headers_a = auth_header(approved_homestay_provider_a)
    headers_b = auth_header(approved_homestay_provider_b)

    # Provider A creates a homestay
    res_h = client.post(
        "/api/v1/homestays",
        json={
            "name": "Provider A Promotion Villa",
            "description": "Promotion testing villa",
            "location": "Kandy",
            "price_per_night": 10000.00,
            "max_guests": 4,
            "images": [],
            "amenities": [],
        },
        headers=headers_a,
    )
    assert res_h.status_code == 201
    homestay_id = res_h.json()["id"]

    # Provider B attempts to create a promotion on Provider A's homestay -> Denied (403)
    today = date.today()
    res_b_promo_create = client.post(
        "/api/v1/homestay-promotions",
        json={
            "homestay_id": homestay_id,
            "title": "Unauthorized Promo",
            "description": "Should fail",
            "discount_percent": "15.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=10)).isoformat(),
        },
        headers=headers_b,
    )
    assert res_b_promo_create.status_code == 403
    assert "cannot manage this homestay" in res_b_promo_create.json()["detail"].lower()

    # Provider A creates legitimate promotion -> Allowed (201)
    res_a_promo = client.post(
        "/api/v1/homestay-promotions",
        json={
            "homestay_id": homestay_id,
            "title": "Provider A Early Bird",
            "description": "10% off for early booking",
            "discount_percent": "10.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=7)).isoformat(),
        },
        headers=headers_a,
    )
    assert res_a_promo.status_code == 201
    promo_id = res_a_promo.json()["id"]

    # Provider B attempts to update Provider A's promotion -> Denied (403)
    res_b_promo_update = client.put(
        f"/api/v1/homestay-promotions/{promo_id}",
        json={
            "title": "Tampered Promo",
            "description": "Tampered description",
            "discount_percent": "50.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=14)).isoformat(),
            "is_active": True,
        },
        headers=headers_b,
    )
    assert res_b_promo_update.status_code == 403
    assert "cannot manage this promotion" in res_b_promo_update.json()["detail"].lower()

    # Provider B listing /mine does NOT see Provider A's promotion
    res_b_promos = client.get("/api/v1/homestay-promotions/mine", headers=headers_b)
    assert res_b_promos.status_code == 200
    assert not any(p["id"] == promo_id for p in res_b_promos.json()["items"])


# -----------------------------------------------------------------------------
# 8. Admin Access Override
# -----------------------------------------------------------------------------

def test_admin_can_update_any_homestay_and_promotion(
    client: TestClient,
    approved_homestay_provider_a: User,
    admin_user: User,
):
    headers_a = auth_header(approved_homestay_provider_a)
    headers_admin = auth_header(admin_user)

    # Provider A creates a homestay
    res_h = client.post(
        "/api/v1/homestays",
        json={
            "name": "Provider A Hilltop View",
            "description": "Hilltop retreat",
            "location": "Ella",
            "price_per_night": 9000.00,
            "max_guests": 3,
            "images": [],
            "amenities": [],
        },
        headers=headers_a,
    )
    assert res_h.status_code == 201
    homestay_id = res_h.json()["id"]

    # Admin updates Provider A's homestay
    res_admin_update = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Provider A Hilltop View (Moderated)",
            "description": "Admin moderated description",
            "location": "Ella",
            "price_per_night": 9000.00,
            "max_guests": 3,
            "is_active": True,
            "images": [],
            "amenities": [],
        },
        headers=headers_admin,
    )
    assert res_admin_update.status_code == 200
    assert res_admin_update.json()["name"] == "Provider A Hilltop View (Moderated)"

    # Provider A creates promotion
    today = date.today()
    res_promo = client.post(
        "/api/v1/homestay-promotions",
        json={
            "homestay_id": homestay_id,
            "title": "Summer Discount",
            "description": "Summer special",
            "discount_percent": "12.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=5)).isoformat(),
        },
        headers=headers_a,
    )
    assert res_promo.status_code == 201
    promo_id = res_promo.json()["id"]

    # Admin updates Provider A's promotion
    res_admin_promo_update = client.put(
        f"/api/v1/homestay-promotions/{promo_id}",
        json={
            "title": "Summer Discount (Verified)",
            "description": "Verified promotion",
            "discount_percent": "12.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=5)).isoformat(),
            "is_active": True,
        },
        headers=headers_admin,
    )
    assert res_admin_promo_update.status_code == 200
    assert res_admin_promo_update.json()["title"] == "Summer Discount (Verified)"


# -----------------------------------------------------------------------------
# 9. Traveler Access Restrictions
# -----------------------------------------------------------------------------

def test_traveler_access_restrictions(
    client: TestClient, traveler_user: User
):
    headers = auth_header(traveler_user)

    # Attempt create homestay
    res_create = client.post(
        "/api/v1/homestays",
        json={
            "name": "Traveler Villa",
            "description": "Should fail",
            "location": "Colombo",
            "price_per_night": 5000.00,
            "max_guests": 2,
            "images": [],
            "amenities": [],
        },
        headers=headers,
    )
    assert res_create.status_code == 403

    # Attempt list own homestays
    res_mine = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_mine.status_code == 403

    # Attempt create promotion
    today = date.today()
    res_promo = client.post(
        "/api/v1/homestay-promotions",
        json={
            "homestay_id": 1,
            "title": "Traveler Promo",
            "description": "Should fail",
            "discount_percent": "10.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=3)).isoformat(),
        },
        headers=headers,
    )
    assert res_promo.status_code == 403


# -----------------------------------------------------------------------------
# 10. Existing Public Discovery & Traveler Booking Compatibility
# -----------------------------------------------------------------------------

def test_public_discovery_and_traveler_booking_compatibility(
    client: TestClient,
    approved_homestay_provider_a: User,
    traveler_user: User,
):
    headers_prov = auth_header(approved_homestay_provider_a)
    headers_traveler = auth_header(traveler_user)

    # Provider A publishes a homestay
    res_h = client.post(
        "/api/v1/homestays",
        json={
            "name": "Public Sunshine Homestay",
            "description": "Publicly discoverable homestay",
            "location": "Mirissa",
            "price_per_night": 6000.00,
            "max_guests": 4,
            "images": [{"image_url": "https://example.com/mirissa.jpg", "is_primary": True}],
            "amenities": ["WiFi", "Sea View"],
        },
        headers=headers_prov,
    )
    assert res_h.status_code == 201
    homestay_id = res_h.json()["id"]

    # 1. Unauthenticated discovery: List homestays
    res_pub_list = client.get("/api/v1/homestays")
    assert res_pub_list.status_code == 200
    items = res_pub_list.json()["items"]
    assert any(h["id"] == homestay_id for h in items)

    # 2. Unauthenticated detail: Get single homestay
    res_detail = client.get(f"/api/v1/homestays/{homestay_id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["name"] == "Public Sunshine Homestay"

    # 3. Unauthenticated availability check
    today = date.today()
    check_in = today + timedelta(days=5)
    check_out = today + timedelta(days=8)
    res_avail = client.get(
        f"/api/v1/homestays/{homestay_id}/availability",
        params={"check_in": check_in.isoformat(), "check_out": check_out.isoformat()},
    )
    assert res_avail.status_code == 200
    assert res_avail.json()["available"] is True

    # 4. Unauthenticated price preview
    res_preview = client.get(
        f"/api/v1/homestays/{homestay_id}/price-preview",
        params={"check_in": check_in.isoformat(), "check_out": check_out.isoformat()},
    )
    assert res_preview.status_code == 200
    assert res_preview.json()["nights"] == 3
    assert float(res_preview.json()["final_price"]) == 18000.00

    # 5. Authenticated Traveler creates booking
    res_booking = client.post(
        "/api/v1/bookings",
        json={
            "homestay_id": homestay_id,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "guests": 2,
        },
        headers=headers_traveler,
    )
    assert res_booking.status_code == 201
    booking_id = res_booking.json()["id"]
    assert res_booking.json()["status"] == "confirmed"

    # 6. Traveler views their bookings
    res_my_bookings = client.get("/api/v1/bookings/me", headers=headers_traveler)
    assert res_my_bookings.status_code == 200
    assert any(b["id"] == booking_id for b in res_my_bookings.json())


# -----------------------------------------------------------------------------
# 11. Ownership Identifier Manipulation Prevention
# -----------------------------------------------------------------------------

def test_ownership_identifier_manipulation_prevention(
    client: TestClient,
    approved_homestay_provider_a: User,
    approved_homestay_provider_b: User,
):
    headers_a = auth_header(approved_homestay_provider_a)

    # Attempt to inject spoofed owner_id on creation
    res_spoof_owner = client.post(
        "/api/v1/homestays",
        json={
            "name": "Spoofed Owner Villa",
            "description": "Attempt to impersonate owner",
            "location": "Galle",
            "price_per_night": 5000.00,
            "max_guests": 2,
            "owner_id": approved_homestay_provider_b.id,
        },
        headers=headers_a,
    )
    assert res_spoof_owner.status_code == 422

    # Attempt to inject spoofed provider_profile_id on creation
    res_spoof_profile = client.post(
        "/api/v1/homestays",
        json={
            "name": "Spoofed Profile Villa",
            "description": "Attempt to hijack provider profile",
            "location": "Galle",
            "price_per_night": 5000.00,
            "max_guests": 2,
            "provider_profile_id": approved_homestay_provider_b.provider_profile.id,
        },
        headers=headers_a,
    )
    assert res_spoof_profile.status_code == 422

    # Create legitimate homestay
    res_legit = client.post(
        "/api/v1/homestays",
        json={
            "name": "Legitimate Villa",
            "description": "Legit listing",
            "location": "Galle",
            "price_per_night": 5000.00,
            "max_guests": 2,
        },
        headers=headers_a,
    )
    assert res_legit.status_code == 201
    homestay_id = res_legit.json()["id"]

    # Attempt to inject spoofed owner_id on update
    res_update_spoof_owner = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Updated Villa",
            "description": "Attempt update with owner_id",
            "location": "Galle",
            "price_per_night": 5500.00,
            "max_guests": 2,
            "is_active": True,
            "owner_id": approved_homestay_provider_b.id,
        },
        headers=headers_a,
    )
    assert res_update_spoof_owner.status_code == 422

    # Attempt to inject spoofed provider_profile_id on update
    res_update_spoof_profile = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Updated Villa",
            "description": "Attempt update with provider_profile_id",
            "location": "Galle",
            "price_per_night": 5500.00,
            "max_guests": 2,
            "is_active": True,
            "provider_profile_id": approved_homestay_provider_b.provider_profile.id,
        },
        headers=headers_a,
    )
    assert res_update_spoof_profile.status_code == 422


# -----------------------------------------------------------------------------
# 12. Admin-Created Homestays Explicit Ownership Behavior
# -----------------------------------------------------------------------------

def test_admin_created_homestay_ownership_behavior(
    client: TestClient,
    admin_user: User,
    db_session: Session,
):
    headers_admin = auth_header(admin_user)

    # Admin creates a platform homestay
    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Government Rest House Nuwara Eliya",
            "description": "Admin-curated heritage property",
            "location": "Nuwara Eliya",
            "price_per_night": 12000.00,
            "max_guests": 6,
            "images": [{"image_url": "https://example.com/resthouse.jpg", "is_primary": True}],
            "amenities": ["Fireplace", "Garden", "Heater"],
        },
        headers=headers_admin,
    )
    assert res.status_code == 201
    data = res.json()
    homestay_id = data["id"]

    # Verify explicit ownership: owner_id is admin, provider_profile_id is NULL
    assert data["owner_id"] == admin_user.id
    assert data["provider_profile_id"] is None

    # Verify database persistence
    saved = db_session.get(Homestay, homestay_id)
    assert saved is not None
    assert saved.owner_id == admin_user.id
    assert saved.provider_profile_id is None

    # Admin can list their curated homestay in /mine
    res_mine = client.get("/api/v1/homestays/mine", headers=headers_admin)
    assert res_mine.status_code == 200
    assert any(h["id"] == homestay_id for h in res_mine.json()["items"])

    # Admin can update the homestay
    res_update = client.put(
        f"/api/v1/homestays/{homestay_id}",
        json={
            "name": "Government Rest House Nuwara Eliya (Refurbished)",
            "description": "Refurbished heritage property",
            "location": "Nuwara Eliya",
            "price_per_night": 14000.00,
            "max_guests": 6,
            "is_active": True,
            "images": [],
            "amenities": ["Fireplace", "Garden", "Heater", "WiFi"],
        },
        headers=headers_admin,
    )
    assert res_update.status_code == 200
    assert res_update.json()["provider_profile_id"] is None


# -----------------------------------------------------------------------------
# 13. Legacy Owner Approval Bypass Prevention
# -----------------------------------------------------------------------------

def test_legacy_owner_approval_bypass_prevention(
    client: TestClient,
    db_session: Session,
):
    # 1. User with role='owner' but approval_status='pending'
    pending_owner = User(
        full_name="Bypass Attempt Pending",
        email="bypass.pending@example.com",
        password_hash=hash_password("Password123!"),
        role="owner",
        is_active=True,
    )
    db_session.add(pending_owner)
    db_session.flush()

    profile_pending = ProviderProfile(
        user_id=pending_owner.id,
        business_name="Bypass Pending Villa",
        provider_type="homestay",
        phone="+94770000001",
        district="Galle",
        approval_status="pending",
    )
    db_session.add(profile_pending)
    db_session.commit()

    headers_pending = auth_header(pending_owner)
    res_p = client.post(
        "/api/v1/homestays",
        json={
            "name": "Pending Villa",
            "description": "Should fail",
            "location": "Galle",
            "price_per_night": 4500.00,
            "max_guests": 2,
        },
        headers=headers_pending,
    )
    assert res_p.status_code == 403
    assert "pending approval" in res_p.json()["detail"].lower()

    # 2. User with role='owner' but approval_status='rejected'
    rejected_owner = User(
        full_name="Bypass Attempt Rejected",
        email="bypass.rejected@example.com",
        password_hash=hash_password("Password123!"),
        role="owner",
        is_active=True,
    )
    db_session.add(rejected_owner)
    db_session.flush()

    profile_rejected = ProviderProfile(
        user_id=rejected_owner.id,
        business_name="Bypass Rejected Villa",
        provider_type="homestay",
        phone="+94770000002",
        district="Matara",
        approval_status="rejected",
        rejection_reason="Invalid identity documents",
    )
    db_session.add(profile_rejected)
    db_session.commit()

    headers_rejected = auth_header(rejected_owner)
    res_r = client.post(
        "/api/v1/homestays",
        json={
            "name": "Rejected Villa",
            "description": "Should fail",
            "location": "Matara",
            "price_per_night": 4500.00,
            "max_guests": 2,
        },
        headers=headers_rejected,
    )
    assert res_r.status_code == 403
    assert "rejected" in res_r.json()["detail"].lower()
    assert "invalid identity documents" in res_r.json()["detail"].lower()

    # 3. User with role='owner' but provider_type='artisan'
    artisan_owner = User(
        full_name="Bypass Attempt Artisan",
        email="bypass.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="owner",
        is_active=True,
    )
    db_session.add(artisan_owner)
    db_session.flush()

    profile_artisan = ProviderProfile(
        user_id=artisan_owner.id,
        business_name="Bypass Artisan Masks",
        provider_type="artisan",
        phone="+94770000003",
        district="Colombo",
        approval_status="approved",
    )
    db_session.add(profile_artisan)
    db_session.commit()

    headers_artisan = auth_header(artisan_owner)
    res_a = client.post(
        "/api/v1/homestays",
        json={
            "name": "Artisan Villa",
            "description": "Should fail",
            "location": "Colombo",
            "price_per_night": 4500.00,
            "max_guests": 2,
        },
        headers=headers_artisan,
    )
    assert res_a.status_code == 403
    assert "only homestay service providers can manage homestays" in res_a.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 14. Legacy Unlinked Homestay Compatibility and Automatic Linking
# -----------------------------------------------------------------------------

def test_legacy_unlinked_homestay_compatibility_and_autolink(
    client: TestClient,
    approved_homestay_provider_a: User,
    db_session: Session,
):
    headers = auth_header(approved_homestay_provider_a)

    # Directly insert a legacy homestay with provider_profile_id = NULL
    legacy_homestay = Homestay(
        name="Legacy Unlinked Colonial Bungalow",
        description="Historical listing created before dual FK migration",
        location="Bandarawela",
        price_per_night=Decimal("9500.00"),
        max_guests=6,
        is_active=True,
        owner_id=approved_homestay_provider_a.id,
        provider_profile_id=None,
    )
    db_session.add(legacy_homestay)
    db_session.commit()
    db_session.refresh(legacy_homestay)
    legacy_id = legacy_homestay.id

    # Provider A can see legacy unlinked listing in /mine
    res_mine = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_mine.status_code == 200
    assert any(h["id"] == legacy_id for h in res_mine.json()["items"])

    # Provider A updates the legacy listing -> automatically links to provider_profile_id
    res_update = client.put(
        f"/api/v1/homestays/{legacy_id}",
        json={
            "name": "Legacy Colonial Bungalow (Modernized)",
            "description": "Updated historical listing",
            "location": "Bandarawela",
            "price_per_night": 10500.00,
            "max_guests": 6,
            "is_active": True,
            "images": [],
            "amenities": ["Fireplace"],
        },
        headers=headers,
    )
    assert res_update.status_code == 200
    data = res_update.json()
    assert data["provider_profile_id"] == approved_homestay_provider_a.provider_profile.id

    # Verify database was updated with the link
    db_session.expire_all()
    updated_homestay = db_session.get(Homestay, legacy_id)
    assert updated_homestay.provider_profile_id == approved_homestay_provider_a.provider_profile.id


# -----------------------------------------------------------------------------
# 15. API Compatibility and KYC Privacy Protection
# -----------------------------------------------------------------------------

def test_api_compatibility_and_kyc_privacy(
    client: TestClient,
    approved_homestay_provider_a: User,
):
    headers = auth_header(approved_homestay_provider_a)

    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Privacy Test Villa",
            "description": "Checking response fields",
            "location": "Bentota",
            "price_per_night": 8000.00,
            "max_guests": 3,
        },
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()

    # Public / management fields are present
    assert "id" in data
    assert "name" in data
    assert "description" in data
    assert "location" in data
    assert "price_per_night" in data
    assert "max_guests" in data
    assert "is_active" in data
    assert "created_at" in data
    assert "images" in data
    assert "amenities" in data
    assert "owner_id" in data
    assert "provider_profile_id" in data

    # Sensitive KYC fields MUST NOT be present in homestay response
    sensitive_kyc_fields = [
        "nic_number",
        "date_of_birth",
        "gender",
        "selfie_image_path",
        "nic_front_image_path",
        "nic_back_image_path",
        "bank_account",
    ]
    for field in sensitive_kyc_fields:
        assert field not in data, f"Sensitive KYC field '{field}' leaked in HomestayResponse!"


# -----------------------------------------------------------------------------
# 16. Security Hardening: Legacy Owner Without Provider Profile Denied
# -----------------------------------------------------------------------------

def test_legacy_owner_without_provider_profile_denied(
    client: TestClient,
    db_session: Session,
):
    legacy_owner = User(
        full_name="Legacy Orphan Owner",
        email="legacy.orphan@example.com",
        password_hash=hash_password("Password123!"),
        role="owner",
        is_active=True,
    )
    db_session.add(legacy_owner)
    db_session.commit()
    db_session.refresh(legacy_owner)

    headers = auth_header(legacy_owner)

    # Attempt to create homestay -> Denied (403)
    res_create = client.post(
        "/api/v1/homestays",
        json={
            "name": "Orphan Villa",
            "description": "Should fail",
            "location": "Jaffna",
            "price_per_night": 5000.00,
            "max_guests": 2,
        },
        headers=headers,
    )
    assert res_create.status_code == 403
    assert "approved homestay provider profile required" in res_create.json()["detail"].lower()

    # Attempt to access /mine -> Denied (403)
    res_mine = client.get("/api/v1/homestays/mine", headers=headers)
    assert res_mine.status_code == 403
    assert "approved homestay provider profile required" in res_mine.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 17. Security Hardening: Inactive Provider Profile Rejected
# -----------------------------------------------------------------------------

def test_inactive_provider_profile_rejected(
    client: TestClient,
    db_session: Session,
):
    user = User(
        full_name="Inactive Profile Provider",
        email="inactive.profile@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Inactive Business",
        provider_type="homestay",
        phone="+94771112233",
        district="Galle",
        approval_status="approved",
    )
    # Simulate an inactive profile attribute if present on model/mock
    profile.is_active = False
    db_session.add(profile)
    db_session.commit()

    headers = auth_header(user)
    res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Inactive Profile Homestay",
            "description": "Should fail",
            "location": "Galle",
            "price_per_night": 6000.00,
            "max_guests": 2,
        },
        headers=headers,
    )
    assert res.status_code == 403
    assert "inactive" in res.json()["detail"].lower()


# -----------------------------------------------------------------------------
# 18. Security Hardening: Unauthenticated Management Access Denied
# -----------------------------------------------------------------------------

def test_unauthenticated_management_access_denied(client: TestClient):
    # Homestay create
    res_create = client.post("/api/v1/homestays", json={"name": "Test"})
    assert res_create.status_code in (401, 403)

    # Homestay mine
    res_mine = client.get("/api/v1/homestays/mine")
    assert res_mine.status_code in (401, 403)

    # Homestay update
    res_update = client.put("/api/v1/homestays/1", json={"name": "Test"})
    assert res_update.status_code in (401, 403)

    # Homestay delete
    res_del_h = client.delete("/api/v1/homestays/1")
    assert res_del_h.status_code in (401, 403)

    # Promotion create
    res_promo_create = client.post("/api/v1/homestay-promotions", json={"homestay_id": 1})
    assert res_promo_create.status_code in (401, 403)

    # Promotion mine
    res_promo_mine = client.get("/api/v1/homestay-promotions/mine")
    assert res_promo_mine.status_code in (401, 403)

    # Promotion delete
    res_del_p = client.delete("/api/v1/homestay-promotions/1")
    assert res_del_p.status_code in (401, 403)


# -----------------------------------------------------------------------------
# 19. Security Hardening: Cross-Provider Deletion Protection (Homestay & Promotion)
# -----------------------------------------------------------------------------

def test_cross_provider_deletion_protection(
    client: TestClient,
    approved_homestay_provider_a: User,
    approved_homestay_provider_b: User,
    admin_user: User,
):
    headers_a = auth_header(approved_homestay_provider_a)
    headers_b = auth_header(approved_homestay_provider_b)
    headers_admin = auth_header(admin_user)

    # Provider A creates Homestay
    res_h = client.post(
        "/api/v1/homestays",
        json={
            "name": "Provider A Delete Protection Villa",
            "description": "Testing deletion permissions",
            "location": "Tangalle",
            "price_per_night": 7500.00,
            "max_guests": 4,
        },
        headers=headers_a,
    )
    assert res_h.status_code == 201
    homestay_id = res_h.json()["id"]

    # Provider A creates Promotion
    today = date.today()
    res_p = client.post(
        "/api/v1/homestay-promotions",
        json={
            "homestay_id": homestay_id,
            "title": "Provider A Special Promo",
            "description": "Promo to test delete",
            "discount_percent": "15.00",
            "start_date": today.isoformat(),
            "end_date": (today + timedelta(days=7)).isoformat(),
        },
        headers=headers_a,
    )
    assert res_p.status_code == 201
    promo_id = res_p.json()["id"]

    # 1. Provider B attempts to delete Provider A's promotion -> Denied (403)
    res_b_del_promo = client.delete(f"/api/v1/homestay-promotions/{promo_id}", headers=headers_b)
    assert res_b_del_promo.status_code == 403
    assert "cannot manage this promotion" in res_b_del_promo.json()["detail"].lower()

    # 2. Provider B attempts to delete Provider A's homestay -> Denied (403)
    res_b_del_h = client.delete(f"/api/v1/homestays/{homestay_id}", headers=headers_b)
    assert res_b_del_h.status_code == 403
    assert "cannot manage this homestay" in res_b_del_h.json()["detail"].lower()

    # 3. Provider A successfully deletes own promotion -> Allowed (200)
    res_a_del_promo = client.delete(f"/api/v1/homestay-promotions/{promo_id}", headers=headers_a)
    assert res_a_del_promo.status_code == 200
    assert res_a_del_promo.json()["is_active"] is False

    # 4. Admin successfully deletes Provider A's homestay -> Allowed (200 - Admin override)
    res_admin_del_h = client.delete(f"/api/v1/homestays/{homestay_id}", headers=headers_admin)
    assert res_admin_del_h.status_code == 200
    assert res_admin_del_h.json()["is_active"] is False


# -----------------------------------------------------------------------------
# 20. Security Hardening: Inconsistent Ownership Relationship Handling
# -----------------------------------------------------------------------------

def test_inconsistent_ownership_relationship_handling(
    client: TestClient,
    approved_homestay_provider_a: User,
    approved_homestay_provider_b: User,
    db_session: Session,
):
    headers_a = auth_header(approved_homestay_provider_a)
    headers_b = auth_header(approved_homestay_provider_b)

    # Inconsistent homestay record: owner_id belongs to A, but provider_profile_id belongs to B
    tampered_homestay = Homestay(
        name="Tampered Inconsistent Listing",
        description="Mismatched ownership attributes",
        location="Trincomalee",
        price_per_night=Decimal("8000.00"),
        max_guests=4,
        is_active=True,
        owner_id=approved_homestay_provider_a.id,
        provider_profile_id=approved_homestay_provider_b.provider_profile.id,
    )
    db_session.add(tampered_homestay)
    db_session.commit()
    db_session.refresh(tampered_homestay)
    tampered_id = tampered_homestay.id

    # Provider A attempts to update -> Denied (403) because provider_profile_id does not belong to A
    res_a_update = client.put(
        f"/api/v1/homestays/{tampered_id}",
        json={
            "name": "Provider A Hijack Attempt",
            "description": "Should fail",
            "location": "Trincomalee",
            "price_per_night": 8500.00,
            "max_guests": 4,
            "is_active": True,
            "images": [],
            "amenities": [],
        },
        headers=headers_a,
    )
    assert res_a_update.status_code == 403
    assert "cannot manage this homestay" in res_a_update.json()["detail"].lower()

    # Provider B attempts to update -> Denied (403) because owner_id and provider_profile.user_id are inconsistent
    res_b_update = client.put(
        f"/api/v1/homestays/{tampered_id}",
        json={
            "name": "Provider B Hijack Attempt",
            "description": "Should fail",
            "location": "Trincomalee",
            "price_per_night": 8500.00,
            "max_guests": 4,
            "is_active": True,
            "images": [],
            "amenities": [],
        },
        headers=headers_b,
    )
    assert res_b_update.status_code == 403
    assert "cannot manage this homestay" in res_b_update.json()["detail"].lower()

