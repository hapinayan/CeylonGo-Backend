"""
Automated API and ORM tests for Module 03 — Phase 5A:
Central Listing Synchronization, Reviews & Wishlist Integration.

Covers:
- C01: Admin Approval Listing Synchronization (dbo.listing_references sync on approval, rejection, updates)
- A13: Traveler Activity Ratings & Reviews (submit review on completed booking, duplicate prevention, public stats)
- B17: Provider Activity Reviews (provider interface to view feedback across owned activities, filtered by activity)
- A14: Traveler Activity Wishlist (add, delete, check status, paginated list, unapproved/inactive guards)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    require_admin,
    require_approved_provider,
    require_traveler,
)
from app.core.listing_sync import (
    sync_activity_booking_reference,
    sync_activity_listing_reference,
)
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_image import ActivityImage
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    ListingReference,
    Notification,
    Review,
    Wishlist,
)
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_bookings import router as activity_bookings_router
from app.routers.activity_reviews import router as activity_reviews_router
from app.routers.activity_slots import router as activity_slots_router
from app.routers.activity_wishlists import router as activity_wishlists_router
from app.routers.admin_activities import router as admin_activities_router
from app.routers.provider_activities import (
    require_activity_provider,
    router as provider_activities_router,
)

# Test FastAPI App with proper route ordering to prevent collision
test_app = FastAPI(title="CeylonGo Module 03 Phase 5A Test App")
test_app.include_router(activities_public_router)
test_app.include_router(activity_reviews_router)
test_app.include_router(activity_wishlists_router)
test_app.include_router(provider_activities_router)
test_app.include_router(admin_activities_router)
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


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded_users(db_session):
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
        full_name="Provider Alice",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Provider Bob",
        role="provider",
        is_active=True,
    )
    traveler1_user = User(
        id=4,
        email="traveler1@ceylongo.test",
        password_hash="fakehash",
        full_name="Traveler Charlie",
        role="traveler",
        is_active=True,
    )
    traveler2_user = User(
        id=5,
        email="traveler2@ceylongo.test",
        password_hash="fakehash",
        full_name="Traveler Dana",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin_user, prov1_user, prov2_user, traveler1_user, traveler2_user])
    db_session.commit()

    prof1 = ProviderProfile(
        id=1,
        user_id=prov1_user.id,
        business_name="Alice Outdoor Adventures",
        provider_type="activity",
        approval_status="approved",
        district="Kandy",
        phone="0771112233",
        description="Outdoor activities specialist",
    )
    prof2 = ProviderProfile(
        id=2,
        user_id=prov2_user.id,
        business_name="Bob Watersports",
        provider_type="activity",
        approval_status="approved",
        district="Galle",
        phone="0774445566",
        description="Watersports specialist",
    )
    db_session.add_all([prof1, prof2])
    db_session.commit()

    return {
        "admin": admin_user,
        "prov1_user": prov1_user,
        "prov1_profile": prof1,
        "prov2_user": prov2_user,
        "prov2_profile": prof2,
        "traveler1": traveler1_user,
        "traveler2": traveler2_user,
    }


def create_activity_record(
    db: Session,
    provider_profile_id: int,
    title: str = "Knuckles Mountain Trek",
    approval_status: str = "approved",
    is_active: bool = True,
    price: Decimal = Decimal("5000.00"),
) -> Activity:
    act = Activity(
        provider_profile_id=provider_profile_id,
        title=title,
        description="High altitude scenic trek through Knuckles range.",
        category="adventure",
        district="Kandy",
        city="Teldeniya",
        meeting_point="Teldeniya Junction Clock Tower",
        latitude=Decimal("7.300000"),
        longitude=Decimal("80.760000"),
        duration_minutes=240,
        itinerary="Morning hike -> Summit view -> Waterfall bath -> Return",
        requirements="Good physical fitness required.",
        safety_rules="Follow guide instructions at all times.",
        required_equipment="Trekking boots, 2L water.",
        minimum_age=12,
        pricing_type="per_person",
        price=price,
        currency="LKR",
        approval_status=approval_status,
        is_active=is_active,
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    return act


def create_slot_record(
    db: Session,
    activity_id: int,
    capacity: int = 10,
    reserved_count: int = 0,
) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, hours=4),
        capacity=capacity,
        reserved_count=reserved_count,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def create_booking_record(
    db: Session,
    slot_id: int,
    user_id: int,
    participants: int = 2,
    booking_status: str = "completed",
    total_price: Decimal = Decimal("10000.00"),
) -> ActivityBooking:
    booking = ActivityBooking(
        activity_slot_id=slot_id,
        user_id=user_id,
        participants=participants,
        total_price=total_price,
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status=booking_status,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


# ==============================================================================
# C01 – Central Activity Listing Synchronization Tests
# ==============================================================================


def test_admin_approve_syncs_listing_reference(db_session, seeded_users):
    """Admin approval creates an active central listing_references row (C01)."""
    admin = seeded_users["admin"]
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(
        db_session, prof.id, title="Sigiriya Rock Sunrise Climb", approval_status="pending", is_active=False
    )

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_admin] = lambda: admin
    client = TestClient(test_app)

    res = client.post(f"/api/v1/admin/activities/{act.id}/approve", json={})
    assert res.status_code == 200
    assert res.json()["approval_status"] == "approved"
    assert res.json()["is_active"] is True

    # Verify central listing reference in database
    ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == act.id,
        )
        .first()
    )
    assert ref is not None
    assert ref.title == "Sigiriya Rock Sunrise Climb"
    assert ref.district == "Kandy"
    assert ref.city == "Teldeniya"
    assert ref.provider_profile_id == prof.id
    assert ref.is_active is True
    assert ref.latitude == Decimal("7.300000")
    assert ref.longitude == Decimal("80.760000")

    test_app.dependency_overrides.clear()


def test_admin_reject_syncs_listing_reference_inactive(db_session, seeded_users):
    """Admin rejection sets listing reference is_active to False (C01)."""
    admin = seeded_users["admin"]
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(
        db_session, prof.id, title="Unsafe Cliff Diving", approval_status="pending", is_active=False
    )
    # Manually pre-sync as inactive
    sync_activity_listing_reference(db_session, act)

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_admin] = lambda: admin
    client = TestClient(test_app)

    res = client.post(f"/api/v1/admin/activities/{act.id}/reject", json={"reason": "Safety hazard"})
    assert res.status_code == 200
    assert res.json()["approval_status"] == "rejected"
    assert res.json()["is_active"] is False

    ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == act.id,
        )
        .first()
    )
    if ref:
        assert ref.is_active is False

    test_app.dependency_overrides.clear()


def test_provider_toggle_active_syncs_listing_reference(db_session, seeded_users):
    """Provider deactivating/activating an approved activity updates central listing reference."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    sync_activity_listing_reference(db_session, act)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: prof
    client = TestClient(test_app)

    # Deactivate
    res = client.patch(f"/api/v1/provider/activities/{act.id}/active", json={"is_active": False})
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == act.id,
        )
        .first()
    )
    assert ref is not None
    assert ref.is_active is False

    # Reactivate
    res = client.patch(f"/api/v1/provider/activities/{act.id}/active", json={"is_active": True})
    assert res.status_code == 200
    assert res.json()["is_active"] is True

    db_session.refresh(ref)
    assert ref.is_active is True

    test_app.dependency_overrides.clear()


# ==============================================================================
# A13 – Traveler Activity Ratings & Reviews Tests
# ==============================================================================


def test_traveler_review_submission_completed_booking_success(db_session, seeded_users):
    """Verified traveler can review an activity with completed booking (A13)."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    booking = create_booking_record(db_session, slot.id, traveler.id, booking_status="completed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    payload = {
        "booking_id": booking.id,
        "rating": 4.5,
        "title": "Breathtaking views and great guide!",
        "comment": "The trail was well marked and Alice was an extraordinary host.",
    }
    res = client.post(f"/api/v1/activities/{act.id}/reviews", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert float(data["rating"]) == 4.5
    assert data["title"] == payload["title"]
    assert data["comment"] == payload["comment"]
    assert data["is_verified_booking"] is True
    assert data["is_published"] is True
    assert data["reviewer_name"] == traveler.full_name
    assert data["activity_id"] == act.id

    # Verify review in dbo.reviews
    rev = db_session.query(Review).filter(Review.id == data["id"]).first()
    assert rev is not None
    assert rev.rating == Decimal("4.50")
    assert rev.user_id == traveler.id

    # Verify notification to provider
    notif = (
        db_session.query(Notification)
        .filter(Notification.user_id == prof.user_id)
        .first()
    )
    assert notif is not None
    assert "New Review" in notif.title

    test_app.dependency_overrides.clear()


def test_traveler_review_rejected_for_non_completed_booking(db_session, seeded_users):
    """Reviews on pending or confirmed (uncompleted) bookings return 400 Bad Request."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    booking = create_booking_record(db_session, slot.id, traveler.id, booking_status="confirmed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res = client.post(
        f"/api/v1/activities/{act.id}/reviews",
        json={"booking_id": booking.id, "rating": 5.0, "title": "Great"},
    )
    assert res.status_code == 400
    assert "completed" in res.json()["detail"].lower()

    test_app.dependency_overrides.clear()


def test_traveler_review_rejected_for_unowned_booking(db_session, seeded_users):
    """Traveler cannot review a booking made by another traveler (404)."""
    prof = seeded_users["prov1_profile"]
    traveler1 = seeded_users["traveler1"]
    traveler2 = seeded_users["traveler2"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    # Booking belongs to traveler2
    booking = create_booking_record(db_session, slot.id, traveler2.id, booking_status="completed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    # Caller is traveler1
    test_app.dependency_overrides[require_traveler] = lambda: traveler1
    client = TestClient(test_app)

    res = client.post(
        f"/api/v1/activities/{act.id}/reviews",
        json={"booking_id": booking.id, "rating": 5.0},
    )
    assert res.status_code == 404
    assert "eligible booking not found" in res.json()["detail"].lower()

    test_app.dependency_overrides.clear()


def test_traveler_review_duplicate_submission_conflict(db_session, seeded_users):
    """Submitting multiple reviews for the same booking returns 409 Conflict."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    booking = create_booking_record(db_session, slot.id, traveler.id, booking_status="completed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    payload = {"booking_id": booking.id, "rating": 4.0, "title": "Good"}
    first_res = client.post(f"/api/v1/activities/{act.id}/reviews", json=payload)
    assert first_res.status_code == 201

    second_res = client.post(f"/api/v1/activities/{act.id}/reviews", json=payload)
    assert second_res.status_code == 409
    assert "already submitted a review" in second_res.json()["detail"].lower()

    test_app.dependency_overrides.clear()


def test_traveler_review_rating_validation_bounds(db_session, seeded_users):
    """Ratings < 1.0 or > 5.0 fail validation with 422 Unprocessable Content."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    booking = create_booking_record(db_session, slot.id, traveler.id, booking_status="completed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res_low = client.post(
        f"/api/v1/activities/{act.id}/reviews",
        json={"booking_id": booking.id, "rating": 0.5},
    )
    assert res_low.status_code == 422

    res_high = client.post(
        f"/api/v1/activities/{act.id}/reviews",
        json={"booking_id": booking.id, "rating": 6.0},
    )
    assert res_high.status_code == 422

    test_app.dependency_overrides.clear()


def test_public_activity_reviews_list_and_stats(db_session, seeded_users):
    """Public endpoint returns paginated published reviews with accurate average rating."""
    prof = seeded_users["prov1_profile"]
    traveler1 = seeded_users["traveler1"]
    traveler2 = seeded_users["traveler2"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    ref = sync_activity_listing_reference(db_session, act)
    db_session.flush()

    # Seed 2 reviews: 4.0 and 5.0 -> avg 4.5
    r1 = Review(
        listing_reference_id=ref.id,
        user_id=traveler1.id,
        rating=Decimal("4.00"),
        title="Solid adventure",
        comment="Fun time",
        is_verified_booking=True,
        is_published=True,
    )
    r2 = Review(
        listing_reference_id=ref.id,
        user_id=traveler2.id,
        rating=Decimal("5.00"),
        title="Unforgettable!",
        comment="Best guide ever",
        is_verified_booking=True,
        is_published=True,
    )
    # Seed 1 unpublished review (should be excluded)
    r3 = Review(
        listing_reference_id=ref.id,
        user_id=traveler1.id,
        rating=Decimal("1.00"),
        title="Hidden review",
        comment="Draft",
        is_verified_booking=False,
        is_published=False,
    )
    db_session.add_all([r1, r2, r3])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(test_app)

    res = client.get(f"/api/v1/activities/{act.id}/reviews")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["average_rating"] == 4.5
    assert len(data["items"]) == 2
    assert all(item["is_published"] is True for item in data["items"])

    test_app.dependency_overrides.clear()


# ==============================================================================
# B17 – Provider Activity Reviews Tests
# ==============================================================================


def test_provider_views_all_owned_activity_reviews(db_session, seeded_users):
    """Provider can query all reviews across all their owned activities (B17)."""
    prof1 = seeded_users["prov1_profile"]
    prof2 = seeded_users["prov2_profile"]
    traveler = seeded_users["traveler1"]

    act1 = create_activity_record(db_session, prof1.id, title="Knuckles Trek")
    act2 = create_activity_record(db_session, prof1.id, title="Ella Rock Climb")
    act_other = create_activity_record(db_session, prof2.id, title="Mirissa Surfing")

    ref1 = sync_activity_listing_reference(db_session, act1)
    ref2 = sync_activity_listing_reference(db_session, act2)
    ref_other = sync_activity_listing_reference(db_session, act_other)
    db_session.flush()

    # Add reviews
    r1 = Review(
        listing_reference_id=ref1.id,
        user_id=traveler.id,
        rating=Decimal("5.00"),
        title="Great Knuckles",
        is_published=True,
    )
    r2 = Review(
        listing_reference_id=ref2.id,
        user_id=traveler.id,
        rating=Decimal("3.00"),
        title="Ella okay",
        is_published=True,
    )
    r_other = Review(
        listing_reference_id=ref_other.id,
        user_id=traveler.id,
        rating=Decimal("4.00"),
        title="Nice waves",
        is_published=True,
    )
    db_session.add_all([r1, r2, r_other])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: prof1
    client = TestClient(test_app)

    res = client.get("/api/v1/provider/activities/reviews")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["average_rating"] == 4.0
    titles = [item["activity_title"] for item in data["items"]]
    assert "Knuckles Trek" in titles
    assert "Ella Rock Climb" in titles
    assert "Mirissa Surfing" not in titles

    test_app.dependency_overrides.clear()


def test_provider_views_reviews_filtered_by_activity(db_session, seeded_users):
    """Provider can filter reviews by a specific owned activity (B17)."""
    prof1 = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act1 = create_activity_record(db_session, prof1.id, title="Knuckles Trek")
    act2 = create_activity_record(db_session, prof1.id, title="Ella Rock Climb")
    ref1 = sync_activity_listing_reference(db_session, act1)
    ref2 = sync_activity_listing_reference(db_session, act2)
    db_session.flush()

    r1 = Review(listing_reference_id=ref1.id, user_id=traveler.id, rating=Decimal("5.00"), is_published=True)
    r2 = Review(listing_reference_id=ref2.id, user_id=traveler.id, rating=Decimal("2.00"), is_published=True)
    db_session.add_all([r1, r2])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_activity_provider] = lambda: prof1
    client = TestClient(test_app)

    res = client.get(f"/api/v1/provider/activities/reviews?activity_id={act1.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["average_rating"] == 5.0
    assert data["items"][0]["activity_id"] == act1.id

    # Filtering by activity owned by another provider returns 404
    act_other = create_activity_record(db_session, seeded_users["prov2_profile"].id, title="Other")
    res_unowned = client.get(f"/api/v1/provider/activities/reviews?activity_id={act_other.id}")
    assert res_unowned.status_code == 404

    test_app.dependency_overrides.clear()


# ==============================================================================
# A14 – Traveler Activity Wishlist Tests
# ==============================================================================


def test_traveler_add_to_wishlist_success(db_session, seeded_users):
    """Traveler can add an approved, active activity to their wishlist (A14)."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res = client.post(f"/api/v1/activities/{act.id}/wishlist")
    assert res.status_code == 201
    data = res.json()
    assert data["saved"] is True
    assert data["activity_id"] == act.id

    # Verify wishlist entry in database
    w = (
        db_session.query(Wishlist)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .filter(
            Wishlist.user_id == traveler.id,
            ListingReference.module_type == "activity",
            ListingReference.target_id == act.id,
        )
        .first()
    )
    assert w is not None

    test_app.dependency_overrides.clear()


def test_traveler_add_to_wishlist_duplicate_conflict(db_session, seeded_users):
    """Adding an already wishlisted activity returns 409 Conflict (A14)."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    first = client.post(f"/api/v1/activities/{act.id}/wishlist")
    assert first.status_code == 201

    second = client.post(f"/api/v1/activities/{act.id}/wishlist")
    assert second.status_code == 409
    assert "already in your wishlist" in second.json()["detail"].lower()

    test_app.dependency_overrides.clear()


def test_traveler_add_unapproved_or_inactive_activity_to_wishlist(db_session, seeded_users):
    """Unapproved activities return 404 and inactive activities return 400."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act_unapproved = create_activity_record(
        db_session, prof.id, approval_status="pending", is_active=False
    )
    act_inactive = create_activity_record(
        db_session, prof.id, approval_status="approved", is_active=False
    )

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res_unapproved = client.post(f"/api/v1/activities/{act_unapproved.id}/wishlist")
    assert res_unapproved.status_code == 404

    res_inactive = client.post(f"/api/v1/activities/{act_inactive.id}/wishlist")
    assert res_inactive.status_code == 400
    assert "not active" in res_inactive.json()["detail"].lower()

    test_app.dependency_overrides.clear()


def test_traveler_check_and_remove_wishlist(db_session, seeded_users):
    """Traveler can check status and remove activity from wishlist."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    # Initial check -> not saved
    chk1 = client.get(f"/api/v1/activities/{act.id}/wishlist/status")
    assert chk1.status_code == 200
    assert chk1.json()["saved"] is False

    # Add to wishlist
    add_res = client.post(f"/api/v1/activities/{act.id}/wishlist")
    assert add_res.status_code == 201

    # Check -> saved
    chk2 = client.get(f"/api/v1/activities/{act.id}/wishlist/status")
    assert chk2.status_code == 200
    assert chk2.json()["saved"] is True

    # Remove from wishlist
    del_res = client.delete(f"/api/v1/activities/{act.id}/wishlist")
    assert del_res.status_code == 200
    assert del_res.json()["saved"] is False

    # Check -> not saved
    chk3 = client.get(f"/api/v1/activities/{act.id}/wishlist/status")
    assert chk3.json()["saved"] is False

    # Removing again -> 404 Not Found
    del_again = client.delete(f"/api/v1/activities/{act.id}/wishlist")
    assert del_again.status_code == 404

    test_app.dependency_overrides.clear()


def test_traveler_list_wishlist_pagination(db_session, seeded_users):
    """Traveler can list saved activities with pagination and rich summaries (A14)."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]

    act1 = create_activity_record(db_session, prof.id, title="Sigiriya Climb")
    act2 = create_activity_record(db_session, prof.id, title="Yala Safari")
    ref1 = sync_activity_listing_reference(db_session, act1)
    ref2 = sync_activity_listing_reference(db_session, act2)
    db_session.flush()

    # Add image to act1
    img1 = ActivityImage(
        activity_id=act1.id,
        image_url="https://img.test/act1.jpg",
        is_cover=True,
    )
    db_session.add(img1)

    # Add review to act1 (rating 5.0)
    rev = Review(
        listing_reference_id=ref1.id,
        user_id=traveler.id,
        rating=Decimal("5.00"),
        is_published=True,
    )
    db_session.add(rev)

    # Wishlist both
    w1 = Wishlist(user_id=traveler.id, listing_reference_id=ref1.id)
    w2 = Wishlist(user_id=traveler.id, listing_reference_id=ref2.id)
    db_session.add_all([w1, w2])
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res = client.get("/api/v1/activities/wishlist/me?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Check act1 summary
    item1 = next(item for item in data["items"] if item["activity"]["id"] == act1.id)
    assert item1["is_available"] is True
    assert item1["activity"]["title"] == "Sigiriya Climb"
    assert item1["activity"]["cover_image_url"] == "https://img.test/act1.jpg"
    assert item1["activity"]["average_rating"] == 5.0
    assert item1["activity"]["total_reviews"] == 1

    test_app.dependency_overrides.clear()


def test_traveler_wishlist_handles_deactivated_activity_gracefully(db_session, seeded_users):
    """Deactivated saved activities return is_available=False with activity=None without crashing."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]

    act = create_activity_record(db_session, prof.id, title="Temporarily Closed Tour", is_active=False)
    ref = sync_activity_listing_reference(db_session, act)
    # Force ref to exist
    if not ref:
        ref = ListingReference(
            module_type="activity",
            target_id=act.id,
            provider_profile_id=prof.id,
            title=act.title,
            district="Kandy",
            is_active=False,
        )
        db_session.add(ref)
        db_session.flush()

    w = Wishlist(user_id=traveler.id, listing_reference_id=ref.id)
    db_session.add(w)
    db_session.commit()

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    # available_only=False -> included with is_available=False
    res_all = client.get("/api/v1/activities/wishlist/me?available_only=false")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 1
    assert data_all["items"][0]["is_available"] is False
    assert data_all["items"][0]["activity"] is None

    # available_only=True -> filtered out
    res_avail = client.get("/api/v1/activities/wishlist/me?available_only=true")
    assert res_avail.status_code == 200
    data_avail = res_avail.json()
    assert data_avail["total"] == 0
    assert len(data_avail["items"]) == 0

    test_app.dependency_overrides.clear()


def test_activity_review_response_excludes_sensitive_participant_data(db_session, seeded_users):
    """Reviews must never leak traveler phone, email, or participant emergency contacts."""
    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    slot = create_slot_record(db_session, act.id)
    booking = create_booking_record(db_session, slot.id, traveler.id, booking_status="completed")

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    res = client.post(
        f"/api/v1/activities/{act.id}/reviews",
        json={"booking_id": booking.id, "rating": 5.0, "title": "Safe hike"},
    )
    assert res.status_code == 201
    data = res.json()

    # Verify no private traveler or participant fields are present
    disallowed_keys = {
        "email",
        "phone",
        "phone_number",
        "emergency_contact_name",
        "emergency_contact_phone",
        "passport",
        "nic",
        "waiver_signed",
        "medical_conditions",
        "special_requirements",
    }
    assert disallowed_keys.isdisjoint(data.keys())

    test_app.dependency_overrides.clear()


def test_activity_wishlist_duplicate_integrity_error_handled(db_session, seeded_users, monkeypatch):
    """IntegrityError during concurrent wishlist add returns 409 Conflict rather than 500."""
    from sqlalchemy.exc import IntegrityError

    prof = seeded_users["prov1_profile"]
    traveler = seeded_users["traveler1"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    test_app.dependency_overrides[get_db] = lambda: db_session
    test_app.dependency_overrides[require_traveler] = lambda: traveler
    client = TestClient(test_app)

    # Monkeypatch db_session.commit to simulate race condition where unique constraint catches race
    original_commit = db_session.commit
    committed_once = False

    def simulated_race_commit():
        nonlocal committed_once
        if not committed_once:
            committed_once = True
            raise IntegrityError("Simulated race conflict", params=None, orig=Exception("UQ_wishlists_user_listing"))
        return original_commit()

    monkeypatch.setattr(db_session, "commit", simulated_race_commit)

    res = client.post(f"/api/v1/activities/{act.id}/wishlist")
    assert res.status_code == 409
    assert "already in your wishlist" in res.json()["detail"].lower()

    test_app.dependency_overrides.clear()
