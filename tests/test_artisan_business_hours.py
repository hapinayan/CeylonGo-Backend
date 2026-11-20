"""
Automated test suite for Phase P1 Step 3: Structured Artisan Business Opening Hours Management.
Tests:
1. Weekly schedule batch creation and retrieval.
2. Single weekday schedule creation, update, and patch.
3. Closed days handling with null open/close times.
4. Invalid time ranges (close_time <= open_time) rejected.
5. Duplicate weekday schedule prevention (per business).
6. Provider ownership enforcement (cannot modify others' listings).
7. Public opening-hours visibility (hidden when pending, exposed when approved; owner can view).
8. Business Detail API integration (structured business_hours returned alongside text opening_hours).
9. Deleting business hours for a specific weekday.
10. Role-based access control (travelers and unauthenticated callers blocked from writes).
"""

from datetime import UTC, datetime, time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanBusinessHours
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
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
def admin_user(db_session) -> User:
    admin = User(
        full_name="Admin Hours Moderator",
        email="admin.hours@ceylongo.com",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


@pytest.fixture
def traveler_user(db_session) -> User:
    traveler = User(
        full_name="Traveler Kamal",
        email="kamal.hours@example.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    return traveler


@pytest.fixture
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Jayasuriya",
        email="jayasuriya.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Jayasuriya Batik Studios",
        provider_type="artisan",
        district="Matara",
        approval_status="approved",
        phone="+94771234567",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def other_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Somapala",
        email="somapala.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Somapala Woodcraft",
        provider_type="artisan",
        district="Galle",
        approval_status="approved",
        phone="+94779876543",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def active_artisan_listing(db_session, approved_artisan_user) -> ArtisanListing:
    _, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Weligama Handcrafted Batik Studio",
        craft_category="Batik & Textiles",
        description="Traditional handmade batik with natural dyes.",
        district="Matara",
        city="Weligama",
        opening_hours="Mon - Sat: 9:00 AM - 6:00 PM, Sun: Closed",
        is_active=True,
        approval_status="approved",
        is_verified=True,
        verification_status="verified",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)
    return listing


# ==============================================================================
# TESTS
# ==============================================================================

def test_create_single_day_schedule_success(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify creating an opening hours schedule for a single weekday."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    payload = {
        "day_of_week": 1,  # Monday
        "open_time": "09:00:00",
        "close_time": "17:30:00",
        "is_closed": False,
    }
    res = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=payload,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["artisan_listing_id"] == listing_id
    assert data["day_of_week"] == 1
    assert data["day_name"] == "Monday"
    assert data["open_time"] == "09:00:00"
    assert data["close_time"] == "17:30:00"
    assert data["is_closed"] is False


def test_weekly_schedule_batch_creation(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify setting a full 7-day weekly schedule in one batch request."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    weekly_payload = {
        "hours": [
            {"day_of_week": 0, "is_closed": True},  # Sunday closed
            {"day_of_week": 1, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
            {"day_of_week": 2, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
            {"day_of_week": 3, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
            {"day_of_week": 4, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
            {"day_of_week": 5, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
            {"day_of_week": 6, "open_time": "10:00:00", "close_time": "15:00:00", "is_closed": False},  # Saturday half day
        ]
    }
    res = client.put(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=weekly_payload,
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 7
    days = [item["day_of_week"] for item in data]
    assert days == [0, 1, 2, 3, 4, 5, 6]
    assert data[0]["is_closed"] is True
    assert data[0]["day_name"] == "Sunday"
    assert data[1]["open_time"] == "09:00:00"
    assert data[6]["open_time"] == "10:00:00"


def test_updating_opening_and_closing_hours(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify updating existing day schedule via PUT and PATCH."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # Create Monday
    client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 1, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False},
    )

    # Full update via PUT /business-hours/1
    update_payload = {
        "day_of_week": 1,
        "open_time": "10:00:00",
        "close_time": "19:00:00",
        "is_closed": False,
    }
    put_res = client.put(
        f"/api/v1/artisan-listings/{listing_id}/business-hours/1",
        headers=auth_header(user),
        json=update_payload,
    )
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["open_time"] == "10:00:00"
    assert put_data["close_time"] == "19:00:00"

    # Partial update via PATCH /business-hours/1
    patch_res = client.patch(
        f"/api/v1/artisan-listings/{listing_id}/business-hours/1",
        headers=auth_header(user),
        json={"close_time": "20:00:00"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["close_time"] == "20:00:00"
    assert patch_res.json()["open_time"] == "10:00:00"


def test_closed_days_handling(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify closed days support null opening/closing times and reflect correctly."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    res = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 0, "is_closed": True},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["day_of_week"] == 0
    assert data["is_closed"] is True
    assert data["open_time"] is None
    assert data["close_time"] is None


def test_invalid_time_ranges_rejected(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify rejection when closing time is before or equal to opening time."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # 1. Closing time earlier than opening time
    bad_time_1 = {
        "day_of_week": 2,
        "open_time": "17:00:00",
        "close_time": "09:00:00",
        "is_closed": False,
    }
    res1 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=bad_time_1,
    )
    assert res1.status_code in (400, 422)

    # 2. Equal opening and closing times
    bad_time_2 = {
        "day_of_week": 2,
        "open_time": "10:00:00",
        "close_time": "10:00:00",
        "is_closed": False,
    }
    res2 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=bad_time_2,
    )
    assert res2.status_code in (400, 422)

    # 3. Not closed but times omitted
    bad_time_3 = {
        "day_of_week": 2,
        "is_closed": False,
    }
    res3 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=bad_time_3,
    )
    assert res3.status_code in (400, 422)


def test_duplicate_weekday_prevention(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify duplicate schedule entries for the same weekday are rejected."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # 1. Create Monday
    client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 1, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False},
    )

    # 2. Attempt to create Monday again -> 400 Bad Request
    res_dup = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 1, "open_time": "10:00:00", "close_time": "18:00:00", "is_closed": False},
    )
    assert res_dup.status_code == 400
    assert "already exist" in res_dup.json()["detail"]

    # 3. Batch payload with duplicate days -> 400 Bad Request
    dup_batch = {
        "hours": [
            {"day_of_week": 3, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False},
            {"day_of_week": 3, "open_time": "10:00:00", "close_time": "18:00:00", "is_closed": False},
        ]
    }
    res_batch = client.put(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json=dup_batch,
    )
    assert res_batch.status_code == 400
    assert "Duplicate day_of_week" in res_batch.json()["detail"]


def test_provider_ownership_enforced(
    client: TestClient, other_artisan_user, active_artisan_listing
):
    """Verify providers cannot create, update, or delete opening hours for listings they do not own."""
    other_user, _ = other_artisan_user
    listing_id = active_artisan_listing.id

    # Create attempt by other provider
    res_create = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(other_user),
        json={"day_of_week": 1, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False},
    )
    assert res_create.status_code == 403
    assert "do not own" in res_create.json()["detail"]

    # Delete attempt by other provider
    res_del = client.delete(
        f"/api/v1/artisan-listings/{listing_id}/business-hours/1",
        headers=auth_header(other_user),
    )
    assert res_del.status_code == 403


def test_public_business_hours_visibility(
    client: TestClient, admin_user, approved_artisan_user, db_session
):
    """Verify unapproved listing hides business hours from public, owner can view, and approval reveals them."""
    user, profile = approved_artisan_user

    # Create pending listing
    pending_listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Pending Gem & Jewelry Workshop",
        craft_category="Jewelry & Gem Craft",
        district="Ratnapura",
        city="Ratnapura",
        is_active=True,
        approval_status="pending",
        is_verified=True,
    )
    db_session.add(pending_listing)
    db_session.commit()
    db_session.refresh(pending_listing)

    # Owner adds business hours
    res_add = client.post(
        f"/api/v1/artisan-listings/{pending_listing.id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 1, "open_time": "09:00:00", "close_time": "18:00:00", "is_closed": False},
    )
    assert res_add.status_code == 201

    # Public request (unauthenticated) -> 404
    res_pub = client.get(f"/api/v1/artisan-listings/{pending_listing.id}/business-hours")
    assert res_pub.status_code == 404

    # Owner request -> 200
    res_owner = client.get(
        f"/api/v1/artisan-listings/{pending_listing.id}/business-hours",
        headers=auth_header(user),
    )
    assert res_owner.status_code == 200
    assert len(res_owner.json()) == 1

    # Admin approves listing
    client.post(
        f"/api/v1/admin/artisan/listings/{pending_listing.id}/approve",
        headers=auth_header(admin_user),
    )

    # Now public request succeeds
    res_pub_approved = client.get(f"/api/v1/artisan-listings/{pending_listing.id}/business-hours")
    assert res_pub_approved.status_code == 200
    assert len(res_pub_approved.json()) == 1

    # Business detail endpoint returns business_hours array
    res_detail = client.get(f"/api/v1/artisan-listings/{pending_listing.id}")
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert "business_hours" in detail_data
    assert len(detail_data["business_hours"]) == 1
    assert detail_data["business_hours"][0]["day_name"] == "Monday"


def test_delete_business_hours_day(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify deleting business hours for a specific weekday."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(user),
        json={"day_of_week": 4, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False},
    )

    # Delete Thursday
    del_res = client.delete(
        f"/api/v1/artisan-listings/{listing_id}/business-hours/4",
        headers=auth_header(user),
    )
    assert del_res.status_code == 204

    # Confirm it's gone
    get_res = client.get(f"/api/v1/artisan-listings/{listing_id}/business-hours")
    assert len(get_res.json()) == 0

    # Delete non-existent day -> 404
    del_404 = client.delete(
        f"/api/v1/artisan-listings/{listing_id}/business-hours/4",
        headers=auth_header(user),
    )
    assert del_404.status_code == 404


def test_unauthorized_and_traveler_access_blocked(
    client: TestClient, traveler_user, active_artisan_listing
):
    """Verify unauthenticated users and travelers cannot write business hours."""
    listing_id = active_artisan_listing.id
    payload = {"day_of_week": 1, "open_time": "09:00:00", "close_time": "17:00:00", "is_closed": False}

    # Unauthenticated
    res_unauth = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        json=payload,
    )
    assert res_unauth.status_code in (401, 403)

    # Traveler
    res_traveler = client.post(
        f"/api/v1/artisan-listings/{listing_id}/business-hours",
        headers=auth_header(traveler_user),
        json=payload,
    )
    assert res_traveler.status_code == 403


def test_backward_compatibility_with_text_opening_hours(
    client: TestClient, active_artisan_listing
):
    """Verify existing text opening_hours field remains intact and is returned."""
    listing_id = active_artisan_listing.id

    res = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["opening_hours"] == "Mon - Sat: 9:00 AM - 6:00 PM, Sun: Closed"
    assert "business_hours" in data
    assert isinstance(data["business_hours"], list)

