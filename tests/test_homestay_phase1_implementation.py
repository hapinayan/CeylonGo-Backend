from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.booking import Booking
from app.models.homestay import (
    Homestay,
    HomestayAmenity,
    HomestayAvailabilityBlock,
    HomestayImage,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference
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


@pytest.fixture
def provider_user(db_session: Session) -> User:
    user = User(
        full_name="Kusal Mendis",
        email="kusal.homestay@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Kandy Heritage Villas",
        provider_type="homestay",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
        identity_document_type="nic",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session: Session) -> User:
    admin = User(
        full_name="Platform Admin",
        email="admin@ceylongo.com",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


@pytest.fixture
def traveler_user(db_session: Session) -> User:
    traveler = User(
        full_name="Alice Wonderland",
        email="alice@example.com",
        password_hash=hash_password("TravelerPass123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    return traveler


def test_create_listing_as_draft_with_master_fields(client: TestClient, provider_user: User):
    """Verifies that new listings created by providers default to draft with all new fields."""
    headers = auth_header(provider_user)
    payload = {
        "name": "Kandy Royal Mist",
        "description": "Scenic hill country villa",
        "location": "Hanthana, Kandy",
        "province": "Central",
        "district": "Kandy",
        "city": "Kandy",
        "address": "45 Hanthana Road",
        "latitude": 7.290572,
        "longitude": 80.633728,
        "price_per_night": 9500.00,
        "max_guests": 5,
        "check_in_time": "14:00:00",
        "check_out_time": "11:00:00",
        "cancellation_policy": "Full refund up to 48 hours before check-in",
        "free_cancellation_hours": 48,
        "images": [{"image_url": "https://example.com/mist1.jpg", "is_primary": True}],
        "amenities": ["WiFi", "Mountain View", "Breakfast"],
    }

    res = client.post("/api/v1/homestays", json=payload, headers=headers)
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Kandy Royal Mist"
    assert data["approval_status"] == "draft"
    assert data["verification_status"] == "unverified"
    assert data["province"] == "Central"
    assert data["district"] == "Kandy"
    assert data["city"] == "Kandy"
    assert float(data["latitude"]) == pytest.approx(7.290572, rel=1e-4)
    assert float(data["longitude"]) == pytest.approx(80.633728, rel=1e-4)
    assert data["free_cancellation_hours"] == 48


def test_submit_listing_for_approval_lifecycle(client: TestClient, provider_user: User):
    """Verifies provider can submit draft listing for admin approval."""
    headers = auth_header(provider_user)

    # 1. Create draft
    create_res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Sigiriya Green Haven",
            "location": "Sigiriya",
            "price_per_night": 7500.00,
            "max_guests": 3,
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    homestay_id = create_res.json()["id"]
    assert create_res.json()["approval_status"] == "draft"

    # 2. Submit for approval
    submit_res = client.post(
        f"/api/v1/homestays/{homestay_id}/submit-approval",
        headers=headers,
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["approval_status"] == "pending"

    # 3. Resubmitting pending should be rejected
    resubmit_res = client.post(
        f"/api/v1/homestays/{homestay_id}/submit-approval",
        headers=headers,
    )
    assert resubmit_res.status_code == 400


def test_admin_approval_and_shared_listing_reference_sync(
    client: TestClient, provider_user: User, admin_user: User, db_session: Session
):
    """Verifies admin review flow and synchronization with listing_references table."""
    prov_headers = auth_header(provider_user)
    admin_headers = auth_header(admin_user)

    # Provider creates and submits
    create_res = client.post(
        "/api/v1/homestays",
        json={
            "name": "Galle Fort Colonial House",
            "location": "Galle Fort",
            "district": "Galle",
            "city": "Galle",
            "latitude": 6.026700,
            "longitude": 80.217000,
            "price_per_night": 12000.00,
            "max_guests": 6,
        },
        headers=prov_headers,
    )
    homestay_id = create_res.json()["id"]
    client.post(f"/api/v1/homestays/{homestay_id}/submit-approval", headers=prov_headers)

    # Admin lists pending homestays
    pending_res = client.get("/api/v1/admin/homestays/pending", headers=admin_headers)
    assert pending_res.status_code == 200
    pending_ids = [h["id"] for h in pending_res.json()]
    assert homestay_id in pending_ids

    # Admin approves
    review_res = client.post(
        f"/api/v1/admin/homestays/{homestay_id}/review",
        json={"decision": "approved", "reason": None},
        headers=admin_headers,
    )
    assert review_res.status_code == 200
    assert review_res.json()["approval_status"] == "approved"

    # Verify listing_references entry exists
    ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay_id,
        )
        .first()
    )
    assert ref is not None
    assert ref.title == "Galle Fort Colonial House"
    assert ref.district == "Galle"
    assert ref.is_active is True

    # Admin updates verification status
    verify_res = client.post(
        f"/api/v1/admin/homestays/{homestay_id}/verify",
        json={"verification_status": "verified", "notes": "On-site audit passed"},
        headers=admin_headers,
    )
    assert verify_res.status_code == 200
    assert verify_res.json()["verification_status"] == "verified"


def test_public_search_and_discovery_filters_and_sorting(
    client: TestClient, db_session: Session, provider_user: User
):
    """Verifies that public search only displays approved active homestays and filters correctly."""
    # Add an approved homestay in Kandy (price 8000)
    h1 = Homestay(
        name="Kandy Hill Vista",
        location="Kandy",
        province="Central",
        district="Kandy",
        city="Kandy",
        price_per_night=Decimal("8000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        owner_id=provider_user.id,
    )
    # Add an approved homestay in Nuwara Eliya (price 15000)
    h2 = Homestay(
        name="Nuwara Eliya Cold Breeze",
        location="Nuwara Eliya",
        province="Central",
        district="Nuwara Eliya",
        city="Nuwara Eliya",
        price_per_night=Decimal("15000.00"),
        max_guests=6,
        is_active=True,
        approval_status="approved",
        owner_id=provider_user.id,
    )
    # Add a DRAFT homestay in Kandy (should NOT appear publicly)
    h3_draft = Homestay(
        name="Kandy Secret Garden",
        location="Kandy",
        province="Central",
        district="Kandy",
        city="Kandy",
        price_per_night=Decimal("5000.00"),
        max_guests=2,
        is_active=True,
        approval_status="draft",
        owner_id=provider_user.id,
    )
    db_session.add_all([h1, h2, h3_draft])
    db_session.commit()

    # Public search: draft is excluded
    res = client.get("/api/v1/homestays")
    assert res.status_code == 200
    names = [h["name"] for h in res.json()["items"]]
    assert "Kandy Hill Vista" in names
    assert "Nuwara Eliya Cold Breeze" in names
    assert "Kandy Secret Garden" not in names

    # District filter
    res_kandy = client.get("/api/v1/homestays", params={"district": "Kandy"})
    assert res_kandy.status_code == 200
    kandy_names = [h["name"] for h in res_kandy.json()["items"]]
    assert "Kandy Hill Vista" in kandy_names
    assert "Nuwara Eliya Cold Breeze" not in kandy_names

    # Sorting by price_desc
    res_sorted = client.get("/api/v1/homestays", params={"sort_by": "price_desc"})
    assert res_sorted.status_code == 200
    items = res_sorted.json()["items"]
    prices = [float(h["price_per_night"]) for h in items]
    assert prices == sorted(prices, reverse=True)


def test_photo_management_workflow(client: TestClient, provider_user: User):
    """Verifies adding, setting primary, and deleting photos."""
    headers = auth_header(provider_user)

    # Create homestay
    create_res = client.post(
        "/api/v1/homestays",
        json={"name": "Photo Test Homestay", "location": "Bentota", "price_per_night": 6000.00, "max_guests": 2},
        headers=headers,
    )
    h_id = create_res.json()["id"]

    # 1. Add first image (becomes primary automatically)
    img1_res = client.post(
        f"/api/v1/homestays/{h_id}/images",
        json={"image_url": "https://example.com/photo1.jpg", "is_primary": False},
        headers=headers,
    )
    assert img1_res.status_code == 201
    img1_id = img1_res.json()["id"]
    assert img1_res.json()["is_primary"] is True

    # 2. Add second image as primary
    img2_res = client.post(
        f"/api/v1/homestays/{h_id}/images",
        json={"image_url": "https://example.com/photo2.jpg", "is_primary": True},
        headers=headers,
    )
    assert img2_res.status_code == 201
    img2_id = img2_res.json()["id"]
    assert img2_res.json()["is_primary"] is True

    # 3. Set img1 back to primary
    primary_res = client.patch(
        f"/api/v1/homestays/{h_id}/images/{img1_id}/set-primary",
        headers=headers,
    )
    assert primary_res.status_code == 200
    assert primary_res.json()["is_primary"] is True

    # 4. Reorder
    reorder_res = client.patch(
        f"/api/v1/homestays/{h_id}/images/reorder",
        json={"items": [{"image_id": img2_id, "display_order": 0}, {"image_id": img1_id, "display_order": 1}]},
        headers=headers,
    )
    assert reorder_res.status_code == 200

    # 5. Delete primary image, verify other image becomes primary
    del_res = client.delete(
        f"/api/v1/homestays/{h_id}/images/{img1_id}",
        headers=headers,
    )
    assert del_res.status_code == 200


def test_availability_blackout_blocks_and_booking_validation(
    client: TestClient, provider_user: User, traveler_user: User, admin_user: User
):
    """Verifies blackout date blocks prevent overlapping blocks and overlapping bookings."""
    prov_headers = auth_header(provider_user)
    traveler_headers = auth_header(traveler_user)

    # Create homestay
    create_res = client.post(
        "/api/v1/homestays",
        json={"name": "Blackout Test Homestay", "location": "Ella", "price_per_night": 7000.00, "max_guests": 4},
        headers=prov_headers,
    )
    assert create_res.status_code == 201, create_res.text
    h_id = create_res.json()["id"]

    today = date.today()
    block_start = today + timedelta(days=10)
    block_end = today + timedelta(days=15)
    dates = {
        "check_in": (block_start + timedelta(days=1)).isoformat(),
        "check_out": (block_start + timedelta(days=3)).isoformat(),
    }
    booking_request = {"homestay_id": h_id, **dates, "guests": 2}
    assert client.get(
        f"/api/v1/homestays/{h_id}/availability", params=dates
    ).status_code == 404
    assert client.post(
        "/api/v1/bookings", json=booking_request, headers=traveler_headers
    ).status_code == 404

    submission = client.post(
        f"/api/v1/homestays/{h_id}/submit-approval", headers=prov_headers,
    )
    assert submission.status_code == 200, submission.text
    review = client.post(
        f"/api/v1/admin/homestays/{h_id}/review",
        json={"decision": "approved"},
        headers=auth_header(admin_user),
    )
    assert review.status_code == 200, review.text

    # 1. Add blackout block
    block_res = client.post(
        f"/api/v1/homestays/{h_id}/availability-blocks",
        json={
            "start_date": block_start.isoformat(),
            "end_date": block_end.isoformat(),
            "block_type": "renovation",
            "reason": "Painting and roof repairs",
        },
        headers=prov_headers,
    )
    assert block_res.status_code == 201
    block_id = block_res.json()["id"]
    assert block_res.json()["block_type"] == "renovation"

    # 2. Overlapping block rejected
    overlap_res = client.post(
        f"/api/v1/homestays/{h_id}/availability-blocks",
        json={
            "start_date": (block_start + timedelta(days=2)).isoformat(),
            "end_date": (block_end + timedelta(days=2)).isoformat(),
            "block_type": "maintenance",
        },
        headers=prov_headers,
    )
    assert overlap_res.status_code == 409

    # 3. Check availability API reports False for blocked dates
    avail_res = client.get(
        f"/api/v1/homestays/{h_id}/availability",
        params=dates,
    )
    assert avail_res.status_code == 200
    assert avail_res.json()["available"] is False

    # 4. Traveler booking attempt on blocked dates rejected with 409
    book_res = client.post(
        "/api/v1/bookings",
        json=booking_request,
        headers=traveler_headers,
    )
    assert book_res.status_code == 409

    # 5. Remove block
    del_block_res = client.delete(
        f"/api/v1/homestays/{h_id}/availability-blocks/{block_id}",
        headers=prov_headers,
    )
    assert del_block_res.status_code == 200

    # 6. Check availability is now True
    avail_after = client.get(
        f"/api/v1/homestays/{h_id}/availability",
        params=dates,
    )
    assert avail_after.status_code == 200
    assert avail_after.json()["available"] is True
