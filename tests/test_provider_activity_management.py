"""
Automated API tests for Module 03 — Phase 3:
Provider Activity Management, Images & Slot Scheduling.

Covers:
- Step 4: Activity Activation & Deactivation (PATCH /api/v1/provider/activities/{activity_id}/active)
- Step 5: Provider Activity Image Management (Upload, List, Patch, Delete, Serve)
- Step 6: Provider Slot Update (PATCH /api/v1/provider/activities/{activity_id}/slots/{slot_id})
- Step 7: Safe Slot Delete & Deactivation (DELETE /api/v1/provider/activities/{activity_id}/slots/{slot_id})
- Step 8: Capacity & Concurrency Protection
- Cross-provider ownership security & isolation
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import io
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_image import ActivityImage
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_slots import router as activity_slots_router
from app.routers.provider_activities import (
    activity_uploads_router,
    require_activity_provider,
    router as provider_activities_router,
)
from app.services.activity_capacity import (
    reserve_activity_capacity,
    update_slot_capacity,
)

# Test FastAPI Application
test_app = FastAPI(title="CeylonGo Module 03 Provider Management Test App")
test_app.include_router(activities_public_router)
test_app.include_router(activity_uploads_router)
test_app.include_router(provider_activities_router)
test_app.include_router(activity_slots_router)

# In-memory SQLite engine
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def make_test_image_bytes(format="JPEG", size=(200, 200), color="blue") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=format)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def setup_test_database(tmp_path, monkeypatch):
    """Recreate clean schema and mock upload directory for every test."""
    monkeypatch.setenv("ACTIVITIES_IMAGES_DIR", str(tmp_path / "activities"))
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def active_providers(db_session):
    """Seed two approved activity providers and one traveler user."""
    # User 1 (Provider 1)
    user1 = User(
        id=1,
        email="provider1@ceylongo.com",
        password_hash="fakehash1",
        full_name="Provider One",
        role="provider",
        is_active=True,
    )
    # User 2 (Provider 2)
    user2 = User(
        id=2,
        email="provider2@ceylongo.com",
        password_hash="fakehash2",
        full_name="Provider Two",
        role="provider",
        is_active=True,
    )
    # User 3 (Traveler)
    user3 = User(
        id=3,
        email="traveler@ceylongo.com",
        password_hash="fakehash3",
        full_name="Traveler Jane",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([user1, user2, user3])
    db_session.commit()

    # Provider Profile 1
    prof1 = ProviderProfile(
        id=10,
        user_id=1,
        business_name="River Adventures",
        business_registration_number="BR10",
        provider_type="activity",
        approval_status="approved",
        phone="+94771111111",
        district="Kegalle",
    )
    # Provider Profile 2
    prof2 = ProviderProfile(
        id=20,
        user_id=2,
        business_name="Mountain Hikes",
        business_registration_number="BR20",
        provider_type="activity",
        approval_status="approved",
        phone="+94772222222",
        district="Badulla",
    )
    db_session.add_all([prof1, prof2])
    db_session.commit()

    return {"p1": prof1, "p2": prof2, "traveler": user3}


# ==============================================================================
# STEP 4: ACTIVITY ACTIVATION & DEACTIVATION TESTS
# ==============================================================================


def test_provider_activate_and_deactivate_approved_activity(active_providers, db_session):
    p1 = active_providers["p1"]

    act = Activity(
        id=101,
        provider_profile_id=p1.id,
        title="Kitulgala Rafting",
        description="Exciting rafting experience",
        category="adventure",
        district="Kegalle",
        city="Kitulgala",
        duration_minutes=180,
        pricing_type="per_person",
        price=Decimal("6000.00"),
        currency="LKR",
        minimum_age=12,
        meeting_point="Base Camp",
        approval_status="approved",
        is_active=False,
    )
    db_session.add(act)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # 1. Activate
    res1 = client.patch("/api/v1/provider/activities/101/active", json={"is_active": True})
    assert res1.status_code == 200
    assert res1.json()["is_active"] is True

    # Check public discovery reflects active state
    pub1 = client.get("/api/v1/activities")
    assert pub1.status_code == 200
    assert pub1.json()["total"] == 1
    assert pub1.json()["items"][0]["id"] == 101

    # 2. Deactivate (idempotent & safe)
    res2 = client.patch("/api/v1/provider/activities/101/active", json={"is_active": False})
    assert res2.status_code == 200
    assert res2.json()["is_active"] is False

    # Check public discovery hides deactivated activity
    pub2 = client.get("/api/v1/activities")
    assert pub2.status_code == 200
    assert pub2.json()["total"] == 0

    # 3. Idempotent deactivation
    res3 = client.patch("/api/v1/provider/activities/101/active", json={"is_active": False})
    assert res3.status_code == 200
    assert res3.json()["is_active"] is False


def test_activation_rejected_for_unapproved_activities(active_providers, db_session):
    p1 = active_providers["p1"]

    draft_act = Activity(
        id=102,
        provider_profile_id=p1.id,
        title="Draft Activity",
        description="Draft description",
        category="cultural",
        district="Kandy",
        city="Kandy",
        duration_minutes=90,
        pricing_type="per_person",
        price=Decimal("3000.00"),
        currency="LKR",
        minimum_age=5,
        meeting_point="Temple Gate",
        approval_status="draft",
        is_active=False,
    )
    pending_act = Activity(
        id=103,
        provider_profile_id=p1.id,
        title="Pending Activity",
        description="Pending review",
        category="cultural",
        district="Galle",
        city="Galle",
        duration_minutes=120,
        pricing_type="per_person",
        price=Decimal("4500.00"),
        currency="LKR",
        minimum_age=8,
        meeting_point="Fort Entrance",
        approval_status="pending",
        is_active=False,
    )
    db_session.add_all([draft_act, pending_act])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Activating draft activity must fail with 409 Conflict
    res_draft = client.patch("/api/v1/provider/activities/102/active", json={"is_active": True})
    assert res_draft.status_code == 409
    assert "Only approved activities can be activated" in res_draft.json()["detail"]

    # Activating pending activity must fail with 409 Conflict
    res_pending = client.patch("/api/v1/provider/activities/103/active", json={"is_active": True})
    assert res_pending.status_code == 409
    assert "Only approved activities can be activated" in res_pending.json()["detail"]


def test_activation_cross_provider_denied(active_providers, db_session):
    p1 = active_providers["p1"]
    p2 = active_providers["p2"]

    act = Activity(
        id=104,
        provider_profile_id=p1.id,
        title="Provider 1 Activity",
        description="Description",
        category="adventure",
        district="Matale",
        city="Sigiriya",
        duration_minutes=120,
        pricing_type="per_person",
        price=Decimal("5000.00"),
        currency="LKR",
        minimum_age=10,
        meeting_point="Sigiriya Base",
        approval_status="approved",
        is_active=False,
    )
    db_session.add(act)
    db_session.commit()

    # Provider 2 tries to activate Provider 1's activity
    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p2
    client = TestClient(test_app)

    res = client.patch("/api/v1/provider/activities/104/active", json={"is_active": True})
    assert res.status_code == 404
    assert res.json()["detail"] == "Activity not found"


# ==============================================================================
# STEP 5: ACTIVITY IMAGE MANAGEMENT TESTS
# ==============================================================================


def test_upload_valid_image_and_cover_handling(active_providers, db_session):
    p1 = active_providers["p1"]

    act = Activity(
        id=201,
        provider_profile_id=p1.id,
        title="Ella Climbing",
        description="Rock climbing",
        category="adventure",
        district="Badulla",
        city="Ella",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("7500.00"),
        currency="LKR",
        minimum_age=16,
        meeting_point="Ella Gap",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    img1_bytes = make_test_image_bytes(format="JPEG", size=(300, 300), color="red")
    img2_bytes = make_test_image_bytes(format="PNG", size=(400, 400), color="green")

    # 1. Upload first image as cover
    res1 = client.post(
        "/api/v1/provider/activities/201/images",
        files={"file": ("cover.jpg", img1_bytes, "image/jpeg")},
        data={"alt_text": "Climbing rock face", "is_cover": "true", "display_order": "0"},
    )
    assert res1.status_code == 201
    img1_data = res1.json()
    assert img1_data["is_cover"] is True
    assert img1_data["display_order"] == 0
    assert img1_data["alt_text"] == "Climbing rock face"
    assert "/uploads/activities/" in img1_data["image_url"]

    # 2. Upload second image and designate it as new cover
    res2 = client.post(
        "/api/v1/provider/activities/201/images",
        files={"file": ("view.png", img2_bytes, "image/png")},
        data={"alt_text": "View from summit", "is_cover": "true", "display_order": "1"},
    )
    assert res2.status_code == 201
    img2_data = res2.json()
    assert img2_data["is_cover"] is True

    # 3. List images and verify uniqueness of is_cover
    res_list = client.get("/api/v1/provider/activities/201/images")
    assert res_list.status_code == 200
    images = res_list.json()
    assert len(images) == 2
    # img1 should now have is_cover = False
    assert images[0]["id"] == img1_data["id"]
    assert images[0]["is_cover"] is False
    # img2 has is_cover = True
    assert images[1]["id"] == img2_data["id"]
    assert images[1]["is_cover"] is True

    # 4. Verify image can be served publicly
    filename = img1_data["image_url"].split("/")[-1]
    res_serve = client.get(f"/uploads/activities/{filename}")
    assert res_serve.status_code == 200
    assert len(res_serve.content) == len(img1_bytes)


def test_upload_image_validation_failures(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=202,
        provider_profile_id=p1.id,
        title="Tea Tour",
        description="Description",
        category="community",
        district="Nuwara Eliya",
        city="Nuwara Eliya",
        duration_minutes=120,
        pricing_type="per_person",
        price=Decimal("4000.00"),
        currency="LKR",
        minimum_age=6,
        meeting_point="Factory",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # 1. Invalid extension
    res_ext = client.post(
        "/api/v1/provider/activities/202/images",
        files={"file": ("script.sh", b"#!/bin/bash\necho 1", "image/jpeg")},
    )
    assert res_ext.status_code == 400
    assert "Unsupported file extension" in res_ext.json()["detail"]

    # 2. Corrupt content
    res_corrupt = client.post(
        "/api/v1/provider/activities/202/images",
        files={"file": ("fake.jpg", b"not-an-image-data-here", "image/jpeg")},
    )
    assert res_corrupt.status_code == 400
    assert "Invalid or corrupted image content" in res_corrupt.json()["detail"]

    # 3. Dimension too small (< 50px)
    tiny_img = make_test_image_bytes(size=(20, 20))
    res_tiny = client.post(
        "/api/v1/provider/activities/202/images",
        files={"file": ("tiny.jpg", tiny_img, "image/jpeg")},
    )
    assert res_tiny.status_code == 400
    assert "too small" in res_tiny.json()["detail"]


def test_update_and_delete_activity_image(active_providers, db_session):
    p1 = active_providers["p1"]
    p2 = active_providers["p2"]

    act = Activity(
        id=203,
        provider_profile_id=p1.id,
        title="Lagoon Tour",
        description="Description",
        category="local",
        district="Batticaloa",
        city="Batticaloa",
        duration_minutes=150,
        pricing_type="per_person",
        price=Decimal("3500.00"),
        currency="LKR",
        minimum_age=10,
        meeting_point="Bridge",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    img_bytes = make_test_image_bytes()
    upload_res = client.post(
        "/api/v1/provider/activities/203/images",
        files={"file": ("lagoon.jpg", img_bytes, "image/jpeg")},
        data={"alt_text": "Original Alt", "display_order": "5"},
    )
    assert upload_res.status_code == 201
    image_id = upload_res.json()["id"]

    # 1. Update image metadata
    patch_res = client.patch(
        f"/api/v1/provider/activities/203/images/{image_id}",
        json={"alt_text": "Updated Alt Text", "display_order": 1, "is_cover": True},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["alt_text"] == "Updated Alt Text"
    assert patch_res.json()["display_order"] == 1
    assert patch_res.json()["is_cover"] is True

    # 2. Cross-provider delete denied
    test_app.dependency_overrides[require_activity_provider] = lambda: p2
    del_forbidden = client.delete(f"/api/v1/provider/activities/203/images/{image_id}")
    assert del_forbidden.status_code == 404

    # 3. Successful delete by owner
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    del_res = client.delete(f"/api/v1/provider/activities/203/images/{image_id}")
    assert del_res.status_code == 204

    # Verify image is gone from DB
    list_res = client.get("/api/v1/provider/activities/203/images")
    assert list_res.status_code == 200
    assert len(list_res.json()) == 0


# ==============================================================================
# STEP 6 & 7: SLOT UPDATE & DELETION TESTS
# ==============================================================================


def test_update_slot_times_and_capacity_success(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=301,
        provider_profile_id=p1.id,
        title="Surfing Lessons",
        description="Description",
        category="adventure",
        district="Matara",
        city="Mirissa",
        duration_minutes=120,
        pricing_type="per_person",
        price=Decimal("4500.00"),
        currency="LKR",
        minimum_age=10,
        meeting_point="Mirissa Beach",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=2)
    end_time = start_time + timedelta(hours=3)

    slot = ActivitySlot(
        id=401,
        activity_id=301,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=10,
        reserved_count=0,
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    new_start = start_time + timedelta(hours=1)
    new_end = new_start + timedelta(hours=3)

    # Valid reschedule and capacity update
    patch_res = client.patch(
        "/api/v1/provider/activities/301/slots/401",
        json={
            "starts_at": new_start.isoformat(),
            "ends_at": new_end.isoformat(),
            "capacity": 15,
            "is_active": True,
        },
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["capacity"] == 15
    assert data["reserved_count"] == 0
    assert data["is_active"] is True


def test_update_slot_capacity_below_occupancy_rejected(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=302,
        provider_profile_id=p1.id,
        title="Caving Expedition",
        description="Description",
        category="adventure",
        district="Ratnapura",
        city="Kuruwita",
        duration_minutes=180,
        pricing_type="per_person",
        price=Decimal("5500.00"),
        currency="LKR",
        minimum_age=14,
        meeting_point="Cave entrance",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=3)
    end_time = start_time + timedelta(hours=3)

    slot = ActivitySlot(
        id=402,
        activity_id=302,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=10,
        reserved_count=4,  # 4 seats booked
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # 1. Reducing capacity to 3 (< 4 reserved) must fail with 409 Conflict
    res_low = client.patch(
        "/api/v1/provider/activities/302/slots/402",
        json={"capacity": 3},
    )
    assert res_low.status_code == 409
    assert "Cannot reduce capacity" in res_low.json()["detail"]

    # 2. Reducing capacity to exactly 4 (== 4 reserved) is allowed
    res_exact = client.patch(
        "/api/v1/provider/activities/302/slots/402",
        json={"capacity": 4},
    )
    assert res_exact.status_code == 200
    assert res_exact.json()["capacity"] == 4


def test_update_slot_rescheduling_booked_slot_rejected(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=303,
        provider_profile_id=p1.id,
        title="Safari Drive",
        description="Description",
        category="adventure",
        district="Hambantota",
        city="Yala",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("8000.00"),
        currency="LKR",
        minimum_age=5,
        meeting_point="Yala Gate",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=4)
    end_time = start_time + timedelta(hours=4)

    slot = ActivitySlot(
        id=403,
        activity_id=303,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=8,
        reserved_count=2,  # Booked
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    new_start = start_time + timedelta(days=1)
    new_end = new_start + timedelta(hours=4)

    # Attempting to reschedule slot with reservations must fail with 409 Conflict
    res = client.patch(
        "/api/v1/provider/activities/303/slots/403",
        json={"starts_at": new_start.isoformat(), "ends_at": new_end.isoformat()},
    )
    assert res.status_code == 409
    assert "Cannot reschedule a slot with existing reservations" in res.json()["detail"]


def test_delete_unbooked_slot_success(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=304,
        provider_profile_id=p1.id,
        title="Pottery Class",
        description="Description",
        category="local",
        district="Kegalle",
        city="Kegalle",
        duration_minutes=90,
        pricing_type="per_person",
        price=Decimal("2500.00"),
        currency="LKR",
        minimum_age=8,
        meeting_point="Workshop",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=5)
    end_time = start_time + timedelta(hours=2)

    slot = ActivitySlot(
        id=404,
        activity_id=304,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=6,
        reserved_count=0,
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Safe delete of unbooked slot
    del_res = client.delete("/api/v1/provider/activities/304/slots/404")
    assert del_res.status_code == 204

    # Verify slot is gone from database
    check_slot = db_session.query(ActivitySlot).filter(ActivitySlot.id == 404).first()
    assert check_slot is None


def test_delete_slot_with_active_bookings_rejected(active_providers, db_session):
    p1 = active_providers["p1"]
    traveler = active_providers["traveler"]

    act = Activity(
        id=305,
        provider_profile_id=p1.id,
        title="Cooking Workshop",
        description="Description",
        category="local",
        district="Galle",
        city="Galle",
        duration_minutes=150,
        pricing_type="per_person",
        price=Decimal("3500.00"),
        currency="LKR",
        minimum_age=12,
        meeting_point="Kitchen",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=6)
    end_time = start_time + timedelta(hours=3)

    slot = ActivitySlot(
        id=405,
        activity_id=305,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=8,
        reserved_count=2,
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    # Active booking attached to slot
    booking = ActivityBooking(
        id=501,
        activity_slot_id=405,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("7000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="paid",
        booking_status="confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Attempting to delete slot with reservations/bookings must fail with 409
    del_res = client.delete("/api/v1/provider/activities/305/slots/405")
    assert del_res.status_code == 409
    assert "Cannot delete slot with existing reservations" in del_res.json()["detail"]


# ==============================================================================
# STEP 8: CAPACITY & CONCURRENCY ATOMIC HELPER TESTS
# ==============================================================================


def test_atomic_capacity_update_service_helper(db_session, active_providers):
    p1 = active_providers["p1"]
    act = Activity(
        id=306,
        provider_profile_id=p1.id,
        title="Diving Excursion",
        description="Description",
        category="adventure",
        district="Trincomalee",
        city="Nilaveli",
        duration_minutes=180,
        pricing_type="per_person",
        price=Decimal("9000.00"),
        currency="LKR",
        minimum_age=16,
        meeting_point="Dive Center",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=7)
    end_time = start_time + timedelta(hours=3)

    slot = ActivitySlot(
        id=406,
        activity_id=306,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=10,
        reserved_count=5,  # 5 spots reserved
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    # 1. Atomic update to 8 (>= 5 reserved) succeeds
    success = update_slot_capacity(db_session, slot_id=406, new_capacity=8)
    assert success is True
    db_session.commit()
    db_session.refresh(slot)
    assert slot.capacity == 8

    # 2. Atomic update to 4 (< 5 reserved) fails atomically without modifying row
    failure = update_slot_capacity(db_session, slot_id=406, new_capacity=4)
    assert failure is False
    db_session.rollback()
    db_session.refresh(slot)
    assert slot.capacity == 8  # Unchanged


# ==============================================================================
# SECURITY REVIEW: PATH TRAVERSAL, RESILIENCE & COVER UNIQUENESS
# ==============================================================================


def test_serve_activity_image_path_traversal_blocked(active_providers, db_session):
    p1 = active_providers["p1"]
    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # 1. Path traversal via encoded slashes / relative directory components
    res_traversal1 = client.get("/uploads/activities/..%2f..%2fsecret.txt")
    assert res_traversal1.status_code in (400, 404)

    # 2. Non-existent file
    res_missing = client.get("/uploads/activities/non_existent_image.jpg")
    assert res_missing.status_code == 404
    assert res_missing.json()["detail"] == "Image not found"


def test_delete_activity_image_handles_missing_file_gracefully(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=307,
        provider_profile_id=p1.id,
        title="Safari Camp",
        description="Description",
        category="adventure",
        district="Wilpattu",
        city="Nochchiyagama",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("8000.00"),
        currency="LKR",
        minimum_age=12,
        meeting_point="Wilpattu Gate",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    # Create image record pointing to a file that does not exist on disk
    img = ActivityImage(
        id=701,
        activity_id=307,
        image_url="/uploads/activities/act_img_307_already_deleted.jpg",
        alt_text="Missing file",
        display_order=0,
        is_cover=False,
    )
    db_session.add(img)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Deleting an image whose physical file is already missing must succeed cleanly
    del_res = client.delete("/api/v1/provider/activities/307/images/701")
    assert del_res.status_code == 204

    # DB record is gone
    check_img = db_session.query(ActivityImage).filter(ActivityImage.id == 701).first()
    assert check_img is None


def test_update_slot_same_time_with_reservations_allowed(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=308,
        provider_profile_id=p1.id,
        title="Hiking Tour",
        description="Description",
        category="adventure",
        district="Nuwara Eliya",
        city="Horton Plains",
        duration_minutes=300,
        pricing_type="per_person",
        price=Decimal("6000.00"),
        currency="LKR",
        minimum_age=10,
        meeting_point="Horton Gate",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    start_time = datetime.now(UTC) + timedelta(days=5)
    end_time = start_time + timedelta(hours=4)

    slot = ActivitySlot(
        id=408,
        activity_id=308,
        starts_at=start_time.replace(tzinfo=None),
        ends_at=end_time.replace(tzinfo=None),
        capacity=10,
        reserved_count=3,  # 3 booked
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Sending the same starts_at and ends_at along with updated capacity
    # should NOT be rejected as a reschedule because times did not change!
    patch_res = client.patch(
        "/api/v1/provider/activities/308/slots/408",
        json={
            "starts_at": start_time.isoformat(),
            "ends_at": end_time.isoformat(),
            "capacity": 12,
        },
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["capacity"] == 12


def test_cover_image_uniqueness_across_multiple_updates(active_providers, db_session):
    p1 = active_providers["p1"]
    act = Activity(
        id=309,
        provider_profile_id=p1.id,
        title="Whale Watching",
        description="Description",
        category="adventure",
        district="Matara",
        city="Mirissa",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("12000.00"),
        currency="LKR",
        minimum_age=8,
        meeting_point="Mirissa Harbor",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(act)
    db_session.commit()

    img1 = ActivityImage(
        id=801,
        activity_id=309,
        image_url="/uploads/activities/img1.jpg",
        display_order=0,
        is_cover=True,
    )
    img2 = ActivityImage(
        id=802,
        activity_id=309,
        image_url="/uploads/activities/img2.jpg",
        display_order=1,
        is_cover=False,
    )
    img3 = ActivityImage(
        id=803,
        activity_id=309,
        image_url="/uploads/activities/img3.jpg",
        display_order=2,
        is_cover=False,
    )
    db_session.add_all([img1, img2, img3])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: p1
    client = TestClient(test_app)

    # Set img2 as cover
    res = client.patch("/api/v1/provider/activities/309/images/802", json={"is_cover": True})
    assert res.status_code == 200

    covers = (
        db_session.query(ActivityImage)
        .filter(ActivityImage.activity_id == 309, ActivityImage.is_cover == True)
        .all()
    )
    assert len(covers) == 1
    assert covers[0].id == 802

    # Set img3 as cover
    res3 = client.patch("/api/v1/provider/activities/309/images/803", json={"is_cover": True})
    assert res3.status_code == 200

    covers2 = (
        db_session.query(ActivityImage)
        .filter(ActivityImage.activity_id == 309, ActivityImage.is_cover == True)
        .all()
    )
    assert len(covers2) == 1
    assert covers2[0].id == 803

