"""
Automated API and ORM tests for Module 03 — Phase 5B:
Admin Activity Moderation & Platform-Wide Booking Monitoring.

Covers:
- C03: Admin Activity Moderation (Suspend & Unsuspend / Reinstate endpoints, status guards,
       listing sync, provider notifications, admin audit logs, public & booking isolation)
- C04: Admin Booking Monitoring (Platform-wide listing with filtering, pagination, sorting,
       detailed booking inspection, strict participant personal data exclusion)
- Isolation & Safety:
  - Suspended activities excluded from public discovery, detail, slot listing
  - Suspended activities reject new traveler bookings and wishlist additions
  - Existing bookings and slot capacities preserved during suspension
  - Role-based access control (Admin required, providers and travelers blocked with 403)
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_approved_provider,
    require_traveler,
)
from app.core.listing_sync import (
    record_booking_status_change,
    sync_activity_booking_reference,
    sync_activity_listing_reference,
)
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import (
    ActivityBooking,
    ActivityBookingParticipant,
)
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    Notification,
    Wishlist,
)
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_bookings import router as activity_bookings_router
from app.routers.activity_slots import router as activity_slots_router
from app.routers.activity_wishlists import router as activity_wishlists_router
from app.routers.admin_activities import router as admin_activities_router
from app.routers.admin_activity_bookings import (
    router as admin_activity_bookings_router,
)
from app.routers.provider_activities import (
    require_activity_provider,
    router as provider_activities_router,
)

# Test FastAPI App with proper route ordering to prevent collision
test_app = FastAPI(title="CeylonGo Module 03 Phase 5B Test App")
test_app.include_router(activities_public_router)
test_app.include_router(activity_wishlists_router)
test_app.include_router(provider_activities_router)
test_app.include_router(admin_activities_router)
test_app.include_router(admin_activity_bookings_router)
test_app.include_router(activity_slots_router)
test_app.include_router(activity_bookings_router)

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


@pytest.fixture(autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    test_app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded_users(db_session: Session):
    """Seed admin, provider 1, provider 2, and traveler users."""
    admin_user = User(
        id=1,
        email="admin@ceylongo.test",
        password_hash="fakehash",
        full_name="Admin Chief",
        role="admin",
        is_active=True,
    )
    prov1_user = User(
        id=2,
        email="provider1@ceylongo.test",
        password_hash="fakehash",
        full_name="Silva Surfing Provider",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Perera Hiking Provider",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        id=4,
        email="traveler@ceylongo.test",
        password_hash="fakehash",
        full_name="Jane Doe Traveler",
        role="traveler",
        is_active=True,
    )

    db_session.add_all([admin_user, prov1_user, prov2_user, traveler_user])
    db_session.commit()

    p1_profile = ProviderProfile(
        id=1,
        user_id=2,
        business_name="Silva Surf Academy",
        provider_type="activity",
        phone="+94771112233",
        district="Matara",
        approval_status="approved",
    )
    p2_profile = ProviderProfile(
        id=2,
        user_id=3,
        business_name="Perera Ella Treks",
        provider_type="activity",
        phone="+94772223344",
        district="Badulla",
        approval_status="approved",
    )
    db_session.add_all([p1_profile, p2_profile])
    db_session.commit()

    return {
        "admin": admin_user,
        "prov1": prov1_user,
        "prov2": prov2_user,
        "traveler": traveler_user,
        "p1_profile": p1_profile,
        "p2_profile": p2_profile,
    }


@pytest.fixture
def seeded_activities(db_session: Session, seeded_users):
    """Seed sample activities in various approval statuses."""
    act_approved = Activity(
        id=10,
        provider_profile_id=1,
        title="Weligama Beginner Surf Camp",
        description="Learn to surf with certified instructors in Weligama Bay.",
        category="adventure",
        district="Matara",
        city="Weligama",
        meeting_point="Weligama Beach Main Post",
        latitude=Decimal("5.972000"),
        longitude=Decimal("80.428000"),
        duration_minutes=120,
        minimum_age=10,
        pricing_type="per_person",
        price=Decimal("4500.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )
    act_pending = Activity(
        id=20,
        provider_profile_id=1,
        title="Mirissa Sunset Kayak Tour",
        description="Scenic coastal kayak tour around Mirissa bay.",
        category="adventure",
        district="Matara",
        city="Mirissa",
        meeting_point="Mirissa Harbor Gate",
        latitude=Decimal("5.948000"),
        longitude=Decimal("80.456000"),
        duration_minutes=90,
        minimum_age=12,
        pricing_type="per_person",
        price=Decimal("6000.00"),
        currency="LKR",
        approval_status="pending",
        is_active=False,
    )
    act_draft = Activity(
        id=30,
        provider_profile_id=2,
        title="Ella Rock Sunrise Hike",
        description="Early morning trek to Ella Rock summit.",
        category="adventure",
        district="Badulla",
        city="Ella",
        meeting_point="Ella Train Station",
        latitude=Decimal("6.866000"),
        longitude=Decimal("81.046000"),
        duration_minutes=240,
        minimum_age=14,
        pricing_type="per_person",
        price=Decimal("3500.00"),
        currency="LKR",
        approval_status="draft",
        is_active=False,
    )
    db_session.add_all([act_approved, act_pending, act_draft])
    db_session.commit()

    # Synchronize listing reference for the approved activity
    sync_activity_listing_reference(db_session, act_approved)
    db_session.commit()

    return {
        "approved": act_approved,
        "pending": act_pending,
        "draft": act_draft,
    }


@pytest.fixture
def seeded_slots_and_bookings(db_session: Session, seeded_users, seeded_activities):
    """Seed slots and bookings for monitoring and isolation testing."""
    now = datetime.now(UTC).replace(tzinfo=None)
    future_slot = ActivitySlot(
        id=101,
        activity_id=10,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, hours=2),
        capacity=10,
        reserved_count=3,
        is_active=True,
    )
    db_session.add(future_slot)
    db_session.commit()

    booking = ActivityBooking(
        id=501,
        activity_slot_id=101,
        user_id=4,  # traveler
        participants=3,
        total_price=Decimal("13500.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
        created_at=now - timedelta(days=1),
    )
    db_session.add(booking)
    db_session.commit()

    p1 = ActivityBookingParticipant(
        activity_booking_id=501,
        full_name="Jane Doe",
        age=28,
        emergency_contact_name="John Doe",
        emergency_contact_phone="+94770000000",
        special_requirements="None",
        waiver_signed=True,
    )
    p2 = ActivityBookingParticipant(
        activity_booking_id=501,
        full_name="Bob Smith",
        age=30,
        emergency_contact_name="Alice Smith",
        emergency_contact_phone="+94770000001",
        special_requirements="Asthma inhaler needed",
        waiver_signed=True,
    )
    p3 = ActivityBookingParticipant(
        activity_booking_id=501,
        full_name="Charlie Brown",
        age=12,
        emergency_contact_name="Sally Brown",
        emergency_contact_phone="+94770000002",
        special_requirements="Vegetarian meal",
        waiver_signed=True,
    )
    db_session.add_all([p1, p2, p3])
    db_session.commit()

    # Sync central booking reference & audit history
    b_ref = sync_activity_booking_reference(db_session, booking, seeded_activities["approved"])
    db_session.commit()
    record_booking_status_change(
        db_session,
        booking_reference_id=b_ref.id,
        from_status="pending",
        to_status="confirmed",
        changed_by=2,
        notes="Provider confirmed booking on pay_at_location",
    )
    db_session.commit()

    return {
        "slot": future_slot,
        "booking": booking,
        "booking_ref": b_ref,
    }


def set_auth(user: User | None):
    """Sets RBAC dependency overrides on test_app cleanly."""
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: TestingSessionLocal()

    if user is not None:
        test_app.dependency_overrides[get_current_user] = lambda: user
        if user.role == "admin":
            test_app.dependency_overrides[require_admin] = lambda: user
        elif user.role == "provider":
            test_app.dependency_overrides[require_approved_provider] = lambda: user
            test_app.dependency_overrides[require_activity_provider] = lambda: (
                user,
                TestingSessionLocal()
                .query(ProviderProfile)
                .filter(ProviderProfile.user_id == user.id)
                .first(),
            )
        elif user.role == "traveler":
            test_app.dependency_overrides[require_traveler] = lambda: user


@pytest.fixture
def client():
    """Provides a TestClient bound to test_app."""
    return TestClient(test_app)


# ==============================================================================
# 1. ADMIN ACTIVITY MODERATION TESTS (C03)
# ==============================================================================


def test_admin_suspend_approved_activity_success(seeded_users, seeded_activities, db_session, client):
    """Admin successfully suspends an approved activity with valid reason."""
    set_auth(seeded_users["admin"])

    payload = {
        "reason": "Safety audit flagged missing life jackets at venue.",
    }
    resp = client.post("/api/v1/admin/activities/10/suspend", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approval_status"] == "suspended"
    assert data["is_active"] is False

    # Check database state
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "suspended"
    assert db_act.is_active is False
    assert db_act.rejection_reason == payload["reason"]
    assert db_act.reviewed_by == seeded_users["admin"].id

    # Check listing_reference synchronized to inactive
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref is not None
    assert ref.is_active is False

    # Check notification sent to provider
    notif = db_session.query(Notification).filter(
        Notification.user_id == seeded_users["prov1"].id,
        Notification.notification_type == "activity_suspended",
    ).first()
    assert notif is not None
    assert "suspended" in notif.title.lower()
    assert payload["reason"] in notif.message

    # Check admin audit log
    audit = db_session.query(AdminAuditLog).filter(
        AdminAuditLog.entity_name == "activity",
        AdminAuditLog.entity_id == 10,
        AdminAuditLog.action == "activity_suspend",
    ).first()
    assert audit is not None
    assert audit.admin_user_id == seeded_users["admin"].id


def test_admin_suspend_repeated_conflict(seeded_users, seeded_activities, client):
    """Repeated suspension on an already suspended activity returns 409 Conflict."""
    set_auth(seeded_users["admin"])

    payload = {"reason": "First suspension for regulatory check."}
    resp1 = client.post("/api/v1/admin/activities/10/suspend", json=payload)
    assert resp1.status_code == 200

    # Second suspension request
    payload2 = {"reason": "Second suspension duplicate attempt."}
    resp2 = client.post("/api/v1/admin/activities/10/suspend", json=payload2)
    assert resp2.status_code == 409
    assert "already suspended" in resp2.json()["detail"].lower()


def test_admin_suspend_non_approved_activity_conflict(seeded_users, seeded_activities, client):
    """Attempting to suspend pending or draft activities returns 409 Conflict."""
    set_auth(seeded_users["admin"])

    # Pending activity (id 20)
    resp_pending = client.post(
        "/api/v1/admin/activities/20/suspend",
        json={"reason": "Cannot suspend non-approved listing."},
    )
    assert resp_pending.status_code == 409
    assert "only approved activities can be suspended" in resp_pending.json()["detail"].lower()

    # Draft activity (id 30)
    resp_draft = client.post(
        "/api/v1/admin/activities/30/suspend",
        json={"reason": "Cannot suspend draft listing directly."},
    )
    assert resp_draft.status_code == 409


def test_admin_suspend_validation_short_reason(seeded_users, seeded_activities, client):
    """Suspension reason shorter than 10 characters is rejected with 422."""
    set_auth(seeded_users["admin"])

    resp = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Too short"})
    assert resp.status_code == 422


def test_admin_suspend_not_found(seeded_users, client):
    """Suspending non-existent activity returns 404."""
    set_auth(seeded_users["admin"])

    resp = client.post("/api/v1/admin/activities/9999/suspend", json={"reason": "Non-existent activity check."})
    assert resp.status_code == 404


def test_provider_cannot_suspend_activity(seeded_users, seeded_activities, client):
    """Providers and travelers cannot access suspend endpoint (403 Forbidden)."""
    set_auth(seeded_users["prov1"])
    resp_p = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Unauthorized attempt."})
    assert resp_p.status_code == 403

    set_auth(seeded_users["traveler"])
    resp_t = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Unauthorized attempt."})
    assert resp_t.status_code == 403

    set_auth(None)
    resp_anon = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Unauthorized attempt."})
    assert resp_anon.status_code in (401, 403)


def test_admin_unsuspend_activity_success(seeded_users, seeded_activities, db_session, client):
    """Admin successfully unsuspends/reinstates a suspended activity."""
    set_auth(seeded_users["admin"])

    # First suspend it
    client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Temporary suspension for review."})

    # Now unsuspend it
    resp = client.post(
        "/api/v1/admin/activities/10/unsuspend",
        json={"reason": "Provider verified safety compliance certificates."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["approval_status"] == "approved"
    assert data["is_active"] is True

    # Check database state
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "approved"
    assert db_act.is_active is True
    assert db_act.rejection_reason is None

    # Check listing_reference synchronized to active
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref is not None
    assert ref.is_active is True

    # Check notification sent to provider
    notif = db_session.query(Notification).filter(
        Notification.user_id == seeded_users["prov1"].id,
        Notification.notification_type == "activity_unsuspended",
    ).first()
    assert notif is not None
    assert "reinstated" in notif.title.lower()

    # Check admin audit log
    audit = db_session.query(AdminAuditLog).filter(
        AdminAuditLog.entity_name == "activity",
        AdminAuditLog.entity_id == 10,
        AdminAuditLog.action == "activity_unsuspend",
    ).first()
    assert audit is not None


def test_admin_unsuspend_empty_body_success(seeded_users, seeded_activities, client):
    """Admin unsuspends with empty JSON body or no reason."""
    set_auth(seeded_users["admin"])

    client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Temporary audit."})
    resp = client.post("/api/v1/admin/activities/10/unsuspend", json={})
    assert resp.status_code == 200
    assert resp.json()["approval_status"] == "approved"


def test_admin_unsuspend_non_suspended_conflict(seeded_users, seeded_activities, client):
    """Unsuspending an activity that is not currently suspended returns 409 Conflict."""
    set_auth(seeded_users["admin"])

    # Activity 10 is currently approved, not suspended
    resp = client.post("/api/v1/admin/activities/10/unsuspend", json={})
    assert resp.status_code == 409
    assert "only suspended activities can be unsuspended" in resp.json()["detail"].lower()

    # Activity 20 is pending
    resp2 = client.post("/api/v1/admin/activities/20/unsuspend", json={})
    assert resp2.status_code == 409

    # Activity 30 is draft
    resp3 = client.post("/api/v1/admin/activities/30/unsuspend", json={})
    assert resp3.status_code == 409


def test_admin_unsuspend_blocked_if_provider_unapproved(seeded_users, seeded_activities, db_session, client):
    """Cannot unsuspend an activity if provider profile was revoked/unapproved."""
    set_auth(seeded_users["admin"])

    # Suspend activity
    client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety check."})

    # Revoke provider approval
    p_profile = db_session.query(ProviderProfile).filter(ProviderProfile.id == 1).first()
    p_profile.approval_status = "rejected"
    db_session.commit()

    # Attempt unsuspend
    resp = client.post("/api/v1/admin/activities/10/unsuspend", json={})
    assert resp.status_code == 409
    assert "provider profile is not approved" in resp.json()["detail"].lower()


# ==============================================================================
# 2. SUSPENSION INTEGRATION & ISOLATION TESTS (STEP 5)
# ==============================================================================


def test_suspended_activity_excluded_from_public_discovery_and_detail(
    seeded_users,
    seeded_activities,
    seeded_slots_and_bookings,
    client,
):
    """Suspended activities are hidden from public search, details, and slot listings."""
    set_auth(None)
    # Initially approved activity is discoverable
    resp_init = client.get("/api/v1/activities")
    assert resp_init.status_code == 200
    ids_init = [a["id"] for a in resp_init.json()["items"]]
    assert 10 in ids_init

    # Suspend activity as admin
    set_auth(seeded_users["admin"])
    resp_sus = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety violation found."})
    assert resp_sus.status_code == 200

    # Public list must exclude suspended activity
    set_auth(None)
    resp_list = client.get("/api/v1/activities")
    assert resp_list.status_code == 200
    ids_after = [a["id"] for a in resp_list.json()["items"]]
    assert 10 not in ids_after

    # Public detail returns 404
    resp_detail = client.get("/api/v1/activities/10")
    assert resp_detail.status_code == 404

    # Public slots return 404
    resp_slots = client.get("/api/v1/activities/10/slots")
    assert resp_slots.status_code == 404


def test_suspended_activity_blocks_new_bookings_and_wishlist(
    seeded_users,
    seeded_activities,
    seeded_slots_and_bookings,
    client,
):
    """Suspended activity rejects new bookings and wishlist additions."""
    # Suspend activity as admin
    set_auth(seeded_users["admin"])
    resp_sus = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Suspended for inspection."})
    assert resp_sus.status_code == 200

    # Booking attempt by traveler returns 404 (Approved activity not found)
    set_auth(seeded_users["traveler"])
    booking_payload = {
        "activity_slot_id": 101,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Test Traveler",
                "age": 25,
                "emergency_contact_name": "Emergency Contact",
                "emergency_contact_phone": "+94770000000",
                "waiver_signed": True,
            }
        ],
    }
    resp_booking = client.post("/api/v1/activity-bookings", json=booking_payload)
    assert resp_booking.status_code == 404, resp_booking.text

    # Wishlist attempt returns 404
    resp_wish = client.post("/api/v1/activities/10/wishlist")
    assert resp_wish.status_code == 404


def test_suspension_preserves_existing_bookings_and_capacity(
    seeded_users,
    seeded_activities,
    seeded_slots_and_bookings,
    db_session,
    client,
):
    """Suspension preserves existing bookings and reserved capacity without cancellation."""
    set_auth(seeded_users["admin"])

    # Initial booking and slot state
    db_b_before = db_session.query(ActivityBooking).filter(ActivityBooking.id == 501).first()
    assert db_b_before.booking_status == "confirmed"

    db_slot_before = db_session.query(ActivitySlot).filter(ActivitySlot.id == 101).first()
    assert db_slot_before.reserved_count == 3

    # Suspend activity
    resp = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Venue audit pending."})
    assert resp.status_code == 200

    # Verify existing booking is NOT cancelled or altered
    db_b_after = db_session.query(ActivityBooking).filter(ActivityBooking.id == 501).first()
    assert db_b_after.booking_status == "confirmed"
    assert db_b_after.cancelled_at is None

    # Verify reserved slot capacity is preserved
    db_slot_after = db_session.query(ActivitySlot).filter(ActivitySlot.id == 101).first()
    assert db_slot_after.reserved_count == 3


# ==============================================================================
# 3. ADMIN BOOKING MONITORING TESTS (C04)
# ==============================================================================


def test_admin_list_bookings_success(seeded_users, seeded_slots_and_bookings, client):
    """Admin lists all platform activity bookings with pagination."""
    set_auth(seeded_users["admin"])

    resp = client.get("/api/v1/admin/activity-bookings")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert "pagination" in data
    assert data["pagination"]["total"] >= 1
    assert data["pagination"]["page"] == 1

    item = data["items"][0]
    assert item["id"] == 501
    assert item["activity_title"] == "Weligama Beginner Surf Camp"
    assert item["provider_business_name"] == "Silva Surf Academy"
    assert item["traveler_name"] == "Jane Doe Traveler"
    assert item["traveler_email"] == "traveler@ceylongo.test"
    assert item["booking_status"] == "confirmed"
    assert item["participants"] == 3
    assert item["payment_method"] == "pay_at_location"
    assert item["payment_status"] == "unpaid"


def test_admin_list_bookings_filters(seeded_users, seeded_slots_and_bookings, client):
    """Admin filters bookings by status, provider, activity, and dates."""
    set_auth(seeded_users["admin"])

    # Filter matching status
    resp1 = client.get("/api/v1/admin/activity-bookings?booking_status=confirmed")
    assert resp1.status_code == 200
    assert len(resp1.json()["items"]) == 1

    # Filter non-matching status
    resp2 = client.get("/api/v1/admin/activity-bookings?booking_status=cancelled")
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) == 0

    # Filter by provider_profile_id
    resp_prov = client.get("/api/v1/admin/activity-bookings?provider_profile_id=1")
    assert resp_prov.status_code == 200
    assert len(resp_prov.json()["items"]) == 1

    resp_prov_none = client.get("/api/v1/admin/activity-bookings?provider_profile_id=2")
    assert resp_prov_none.status_code == 200
    assert len(resp_prov_none.json()["items"]) == 0

    # Filter by activity_id
    resp_act = client.get("/api/v1/admin/activity-bookings?activity_id=10")
    assert resp_act.status_code == 200
    assert len(resp_act.json()["items"]) == 1

    # Filter by date range
    today = date.today()
    resp_date = client.get(
        f"/api/v1/admin/activity-bookings?date_from={today - timedelta(days=5)}&date_to={today + timedelta(days=1)}"
    )
    assert resp_date.status_code == 200
    assert len(resp_date.json()["items"]) == 1


def test_admin_list_bookings_invalid_filters(seeded_users, seeded_slots_and_bookings, client):
    """Invalid filter parameters return 422 Unprocessable Entity."""
    set_auth(seeded_users["admin"])

    # Invalid status
    resp_bad_status = client.get("/api/v1/admin/activity-bookings?booking_status=invalid_status")
    assert resp_bad_status.status_code == 422

    # Invalid payment status
    resp_bad_pay = client.get("/api/v1/admin/activity-bookings?payment_status=crypto")
    assert resp_bad_pay.status_code == 422

    # Inverted date range
    today = date.today()
    resp_inv_date = client.get(
        f"/api/v1/admin/activity-bookings?start_date={today}&end_date={today - timedelta(days=2)}"
    )
    assert resp_inv_date.status_code == 422


def test_admin_booking_detail_success_with_privacy(seeded_users, seeded_slots_and_bookings, client):
    """Admin inspects booking detail; verifies participant sensitive data is excluded."""
    set_auth(seeded_users["admin"])

    resp = client.get("/api/v1/admin/activity-bookings/501")
    assert resp.status_code == 200, resp.text
    detail = resp.json()

    assert detail["id"] == 501
    assert detail["activity_id"] == 10
    assert detail["activity_title"] == "Weligama Beginner Surf Camp"
    assert detail["provider_business_name"] == "Silva Surf Academy"
    assert detail["traveler_name"] == "Jane Doe Traveler"
    assert detail["traveler_email"] == "traveler@ceylongo.test"
    assert detail["booking_status"] == "confirmed"
    assert detail["participants"] == 3

    # Check status history audit trail
    assert "status_history" in detail
    assert len(detail["status_history"]) >= 1
    assert detail["status_history"][0]["to_status"] == "confirmed"

    # STRICT PARTICIPANT PRIVACY CHECK:
    # Ensure participant objects contain only safe fields (id, full_name, age)
    participants = detail["participants_list"]
    assert len(participants) == 3
    for p in participants:
        assert "id" in p
        assert "full_name" in p
        assert "age" in p
        # Forbidden sensitive fields:
        assert "emergency_contact_name" not in p
        assert "emergency_contact_phone" not in p
        assert "special_requirements" not in p
        assert "waiver_signed" not in p


def test_admin_booking_detail_not_found(seeded_users, client):
    """Querying non-existent booking returns 404."""
    set_auth(seeded_users["admin"])

    resp = client.get("/api/v1/admin/activity-bookings/99999")
    assert resp.status_code == 404


def test_admin_booking_monitoring_access_control(seeded_users, seeded_slots_and_bookings, client):
    """Non-admin users (providers and travelers) cannot access monitoring endpoints (403 Forbidden)."""
    # Provider blocked
    set_auth(seeded_users["prov1"])
    assert client.get("/api/v1/admin/activity-bookings").status_code == 403
    assert client.get("/api/v1/admin/activity-bookings/501").status_code == 403

    # Traveler blocked
    set_auth(seeded_users["traveler"])
    assert client.get("/api/v1/admin/activity-bookings").status_code == 403
    assert client.get("/api/v1/admin/activity-bookings/501").status_code == 403

    # Unauthenticated blocked
    set_auth(None)
    assert client.get("/api/v1/admin/activity-bookings").status_code in (401, 403)
    assert client.get("/api/v1/admin/activity-bookings/501").status_code in (401, 403)


# ==============================================================================
# 4. TRANSACTION ROLLBACK TESTS (STEP 3)
# ==============================================================================


def test_suspend_rollback_on_listing_sync_failure(seeded_users, seeded_activities, db_session, client, monkeypatch):
    """If listing synchronization fails, activity suspension rolls back atomically."""
    set_auth(seeded_users["admin"])

    def fail_sync(db, activity):
        raise RuntimeError("Simulated listing sync failure")

    monkeypatch.setattr("app.routers.admin_activities.sync_activity_listing_reference", fail_sync)

    with pytest.raises(RuntimeError, match="Simulated listing sync failure"):
        client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety audit pending."})

    # Verify atomic rollback
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "approved"
    assert db_act.is_active is True
    assert db_act.rejection_reason is None

    # Listing ref must remain active
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref.is_active is True

    # No notification committed
    notifs = db_session.query(Notification).filter(Notification.user_id == seeded_users["prov1"].id).all()
    assert len(notifs) == 0

    # No audit log committed
    audits = db_session.query(AdminAuditLog).filter(AdminAuditLog.entity_id == 10).all()
    assert len(audits) == 0


def test_suspend_rollback_on_notification_failure(seeded_users, seeded_activities, db_session, client, monkeypatch):
    """If provider notification fails, entire suspension rolls back atomically."""
    set_auth(seeded_users["admin"])

    def fail_notify(db, user_id, title, message, notification_type, link_url=None):
        raise RuntimeError("Simulated notification service failure")

    monkeypatch.setattr("app.routers.admin_activities.create_notification", fail_notify)

    with pytest.raises(RuntimeError, match="Simulated notification service failure"):
        client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety audit pending."})

    # Verify atomic rollback
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "approved"
    assert db_act.is_active is True
    assert db_act.rejection_reason is None

    # Listing ref must remain active
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref.is_active is True

    # No audit log committed
    audits = db_session.query(AdminAuditLog).filter(AdminAuditLog.entity_id == 10).all()
    assert len(audits) == 0


def test_suspend_rollback_on_audit_log_failure(seeded_users, seeded_activities, db_session, client, monkeypatch):
    """If audit log write fails, entire suspension rolls back atomically."""
    set_auth(seeded_users["admin"])

    orig_add = Session.add

    def fail_add(self, instance, *args, **kwargs):
        if isinstance(instance, AdminAuditLog):
            raise RuntimeError("Simulated audit log write failure")
        return orig_add(self, instance, *args, **kwargs)

    monkeypatch.setattr("sqlalchemy.orm.Session.add", fail_add)

    with pytest.raises(RuntimeError, match="Simulated audit log write failure"):
        client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety audit pending."})

    # Verify atomic rollback
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "approved"
    assert db_act.is_active is True

    # Listing ref remains active
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref.is_active is True


def test_unsuspend_rollback_on_sync_failure(seeded_users, seeded_activities, db_session, client, monkeypatch):
    """If listing synchronization fails during unsuspend, state rolls back to suspended."""
    set_auth(seeded_users["admin"])

    # First suspend cleanly
    resp = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Temporary suspension."})
    assert resp.status_code == 200

    def fail_sync(db, activity):
        raise RuntimeError("Simulated sync failure during unsuspend")

    monkeypatch.setattr("app.routers.admin_activities.sync_activity_listing_reference", fail_sync)

    with pytest.raises(RuntimeError, match="Simulated sync failure during unsuspend"):
        client.post("/api/v1/admin/activities/10/unsuspend", json={"reason": "Reinstatement attempt."})

    # Activity must remain suspended
    db_act = db_session.query(Activity).filter(Activity.id == 10).first()
    assert db_act.approval_status == "suspended"
    assert db_act.is_active is False

    # Listing ref must remain inactive
    ref = db_session.query(ListingReference).filter(
        ListingReference.module_type == "activity",
        ListingReference.target_id == 10,
    ).first()
    assert ref.is_active is False


# ==============================================================================
# 5. ROUTE COLLISION & PRECEDENCE TESTS (STEP 6)
# ==============================================================================


def test_admin_routes_no_collision(seeded_users, seeded_activities, seeded_slots_and_bookings, client):
    """Verify that static and dynamic admin routes resolve to their intended handlers."""
    set_auth(seeded_users["admin"])

    # 1. Admin pending list (static GET)
    resp_list = client.get("/api/v1/admin/activities")
    assert resp_list.status_code == 200
    assert isinstance(resp_list.json(), list)

    # 2. Admin activity detail (dynamic GET)
    resp_detail = client.get("/api/v1/admin/activities/10")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["id"] == 10

    # 3. Admin booking monitoring list (static GET)
    resp_b_list = client.get("/api/v1/admin/activity-bookings")
    assert resp_b_list.status_code == 200
    assert "items" in resp_b_list.json()

    # 4. Admin booking detail (dynamic GET)
    resp_b_detail = client.get("/api/v1/admin/activity-bookings/501")
    assert resp_b_detail.status_code == 200
    assert resp_b_detail.json()["id"] == 501

    # 5. Suspend endpoint (dynamic POST)
    resp_sus = client.post("/api/v1/admin/activities/10/suspend", json={"reason": "Safety check."})
    assert resp_sus.status_code == 200
    assert resp_sus.json()["approval_status"] == "suspended"

    # 6. Unsuspend endpoint (dynamic POST)
    resp_unsus = client.post("/api/v1/admin/activities/10/unsuspend", json={})
    assert resp_unsus.status_code == 200
    assert resp_unsus.json()["approval_status"] == "approved"
