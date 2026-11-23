"""
Automated verification tests for CeylonGo Module 04 — Phase 5B:
Traveler & Provider Ratings and Reviews.

Requirements Covered:
- T13: Traveler Ratings & Reviews
  * POST /api/v1/eco-experiences/{experience_id}/reviews (submission)
  * GET /api/v1/eco-experiences/{experience_id}/reviews (public listing)
- P17: Provider Ratings & Reviews
  * GET /api/v1/eco-experiences/me/reviews (feedback listing & metrics)

Safety & Governance:
- Only traveler who owns the completed booking may review.
- Cross-module isolation: booking must belong to the Eco Experience.
- Status validation: only 'completed' bookings can be reviewed (400 for pending/confirmed/cancelled/rejected).
- Duplicate review prevention: strictly 1 review per booking (409 Conflict).
- Rating bounds: 1.00 <= rating <= 5.00 enforced by schema and physical check constraints.
- Privacy rule: public endpoints expose only display names, never email/phone.
- Provider isolation: Provider B cannot inspect Provider A's review metrics (403/404).
- Atomic transaction management with safe rollback on database failure.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.routers.auth import ADMIN_AUTH_METHOD
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    ListingReference,
    Notification,
    Review,
)
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)

# Test application isolating M04 routers
eco_p5b_app = FastAPI(title="CeylonGo Phase 5B Test App")
eco_p5b_app.include_router(auth_router)
eco_p5b_app.include_router(eco_experiences_router)
eco_p5b_app.include_router(admin_eco_router)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
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
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_p5b_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p5b_app) as test_client:
        yield test_client
    eco_p5b_app.dependency_overrides.clear()


@pytest.fixture
def seed_data(db_session: Session):
    # 1. Users
    provider_user_a = User(
        email="provider_a@ceylongo.com",
        password_hash=hash_password("ProviderPass123!"),
        full_name="Eco Provider Alice",
        role="provider",
        is_active=True,
    )
    provider_user_b = User(
        email="provider_b@ceylongo.com",
        password_hash=hash_password("ProviderPass123!"),
        full_name="Eco Provider Bob",
        role="provider",
        is_active=True,
    )
    traveler_user_1 = User(
        email="traveler1@ceylongo.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Traveler One",
        role="traveler",
        is_active=True,
    )
    traveler_user_2 = User(
        email="traveler2@ceylongo.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Traveler Two",
        role="traveler",
        is_active=True,
    )
    admin_user = User(
        email="admin@ceylongo.com",
        password_hash=hash_password("AdminPass123!"),
        full_name="System Admin",
        role="admin",
        is_active=True,
    )
    db_session.add_all([provider_user_a, provider_user_b, traveler_user_1, traveler_user_2, admin_user])
    db_session.flush()

    # 2. Provider Profiles
    profile_a = ProviderProfile(
        user_id=provider_user_a.id,
        business_name="Alice Rainforest Expeditions",
        business_registration_number="PV-ECO-A-001",
        provider_type="eco_tourism",
        phone="+94771234567",
        district="Ratnapura",
        approval_status="approved",
    )
    profile_b = ProviderProfile(
        user_id=provider_user_b.id,
        business_name="Bob Mangrove Kayaking",
        business_registration_number="PV-ECO-B-002",
        provider_type="eco_tourism",
        phone="+94777654321",
        district="Galle",
        approval_status="approved",
    )
    db_session.add_all([profile_a, profile_b])
    db_session.flush()

    # 3. Base Activities (for foreign key integrity of activity_slots)
    base_activity_a = Activity(
        provider_profile_id=profile_a.id,
        title="Sinharaja Rainforest Trek",
        description="Base activity for slot FK",
        category="adventure",
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kalawana Park Gate",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("5000.00"),
    )
    base_activity_b = Activity(
        provider_profile_id=profile_b.id,
        title="Bentota Mangrove Safari",
        description="Base activity for slot FK",
        category="adventure",
        district="Galle",
        city="Bentota",
        meeting_point="Bentota Bridge",
        duration_minutes=180,
        pricing_type="per_person",
        price=Decimal("4000.00"),
    )
    db_session.add_all([base_activity_a, base_activity_b])
    db_session.flush()

    # 4. Eco Experiences
    exp_a = EcoExperience(
        provider_profile_id=profile_a.id,
        title="Sinharaja Biodiversity Canopy Walk",
        eco_category="Forest & Nature Trails",
        description="Immersive educational tour of Sinharaja rain forest.",
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kalawana Park Gate",
        duration_hours=Decimal("4.00"),
        base_price=Decimal("5000.00"),
        currency="LKR",
        max_group_size=12,
        min_participants=1,
        min_age=8,
        environmental_guidelines="Strict zero-waste policy. Stay on marked trails.",
        wildlife_safety_summary="Do not feed or approach wild animals.",
        conservation_factual_contribution="5% of revenue donated to reforestation.",
        conservation_marketing_statement="Protecting Sri Lanka's endemic rainforest.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="granted",
    )
    exp_b = EcoExperience(
        provider_profile_id=profile_b.id,
        title="Bentota River Eco Kayaking",
        eco_category="Wildlife & Birdwatching",
        description="Quiet paddle through delicate mangrove estuaries.",
        district="Galle",
        city="Bentota",
        meeting_point="Bentota Bridge",
        duration_hours=Decimal("3.00"),
        base_price=Decimal("4000.00"),
        currency="LKR",
        max_group_size=8,
        min_participants=1,
        min_age=10,
        environmental_guidelines="Quiet navigation; no plastic allowed.",
        wildlife_safety_summary="Wear life vests at all times.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="none",
    )
    db_session.add_all([exp_a, exp_b])
    db_session.flush()

    # 5. Activity Slots
    slot_a = ActivitySlot(
        activity_id=base_activity_a.id,
        eco_experience_id=exp_a.id,
        starts_at=datetime.now(UTC) - timedelta(days=2),
        ends_at=datetime.now(UTC) - timedelta(days=2) + timedelta(hours=4),
        capacity=12,
        reserved_count=3,
        is_active=True,
    )
    slot_b = ActivitySlot(
        activity_id=base_activity_b.id,
        eco_experience_id=exp_b.id,
        starts_at=datetime.now(UTC) - timedelta(days=1),
        ends_at=datetime.now(UTC) - timedelta(days=1) + timedelta(hours=3),
        capacity=8,
        reserved_count=0,
        is_active=True,
    )
    db_session.add_all([slot_a, slot_b])
    db_session.flush()

    # 6. Activity Bookings
    # Booking 1: Traveler 1, Exp A, COMPLETED
    booking_completed_1 = ActivityBooking(
        activity_slot_id=slot_a.id,
        eco_experience_id=exp_a.id,
        user_id=traveler_user_1.id,
        participants=2,
        total_price=Decimal("10000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="paid",
        booking_status="completed",
    )
    # Booking 2: Traveler 2, Exp A, COMPLETED
    booking_completed_2 = ActivityBooking(
        activity_slot_id=slot_a.id,
        eco_experience_id=exp_a.id,
        user_id=traveler_user_2.id,
        participants=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="paid",
        booking_status="completed",
    )
    # Booking 3: Traveler 1, Exp A, CONFIRMED (not completed)
    booking_confirmed = ActivityBooking(
        activity_slot_id=slot_a.id,
        eco_experience_id=exp_a.id,
        user_id=traveler_user_1.id,
        participants=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="paid",
        booking_status="confirmed",
    )
    # Booking 4: Traveler 1, Exp A, CANCELLED
    booking_cancelled = ActivityBooking(
        activity_slot_id=slot_a.id,
        eco_experience_id=exp_a.id,
        user_id=traveler_user_1.id,
        participants=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="refunded",
        booking_status="cancelled",
    )
    # Booking 5: Generic Activity Booking (Module 03, eco_experience_id=None)
    booking_module03 = ActivityBooking(
        activity_slot_id=slot_a.id,
        eco_experience_id=None,
        user_id=traveler_user_1.id,
        participants=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="paid",
        booking_status="completed",
    )
    db_session.add_all([
        booking_completed_1,
        booking_completed_2,
        booking_confirmed,
        booking_cancelled,
        booking_module03,
    ])
    db_session.commit()

    return {
        "provider_user_a": provider_user_a,
        "provider_user_b": provider_user_b,
        "traveler_user_1": traveler_user_1,
        "traveler_user_2": traveler_user_2,
        "admin_user": admin_user,
        "profile_a": profile_a,
        "profile_b": profile_b,
        "exp_a": exp_a,
        "exp_b": exp_b,
        "booking_completed_1": booking_completed_1,
        "booking_completed_2": booking_completed_2,
        "booking_confirmed": booking_confirmed,
        "booking_cancelled": booking_cancelled,
        "booking_module03": booking_module03,
    }


def auth_headers(user: User) -> dict[str, str]:
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "auth_version": user.auth_version,
    }
    if user.role == "admin":
        payload["auth_method"] = ADMIN_AUTH_METHOD

    token = create_access_token(payload)
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# TEST SUITE: T13 Traveler Ratings & Reviews
# ==============================================================================


def test_submit_traveler_review_success(client: TestClient, db_session: Session, seed_data: dict):
    """
    T13: A verified traveler with a completed booking successfully submits a review.
    Validates:
    - 201 Created
    - Schema response has correct fields and display name
    - Notification is generated for the provider
    - Safe privacy: no traveler contact details exposed
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking = seed_data["booking_completed_1"]
    provider = seed_data["provider_user_a"]

    payload = {
        "booking_id": booking.id,
        "rating": "4.50",
        "title": "Unforgettable Canopy Experience!",
        "comment": "Our naturalist guide was extraordinarily knowledgeable and protected the wildlife.",
    }

    resp = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json=payload,
        headers=auth_headers(traveler),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["eco_experience_id"] == exp_a.id
    assert data["booking_id"] == booking.id
    assert data["user_id"] == traveler.id
    assert data["reviewer_name"] == traveler.full_name
    assert Decimal(str(data["rating"])) == Decimal("4.50")
    assert data["title"] == payload["title"]
    assert data["comment"] == payload["comment"]
    assert data["is_verified_booking"] is True
    assert data["is_published"] is True
    assert "email" not in data
    assert "phone" not in data

    # Verify notification created for provider
    notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == provider.id,
            Notification.notification_type == "new_review",
        )
        .first()
    )
    assert notif is not None
    assert exp_a.title in notif.title
    assert "4.5" in notif.message


def test_reject_review_for_non_completed_bookings(client: TestClient, seed_data: dict):
    """
    T13: Enforces that reviews can only be submitted for completed bookings.
    Bookings in status 'confirmed', 'cancelled', 'pending', or 'rejected' are rejected with 400.
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking_confirmed = seed_data["booking_confirmed"]
    booking_cancelled = seed_data["booking_cancelled"]

    # 1. Attempt on confirmed booking
    resp_conf = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_confirmed.id, "rating": "5.00"},
        headers=auth_headers(traveler),
    )
    assert resp_conf.status_code == 400
    assert "completed" in resp_conf.json()["detail"].lower()

    # 2. Attempt on cancelled booking
    resp_canc = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_cancelled.id, "rating": "3.00"},
        headers=auth_headers(traveler),
    )
    assert resp_canc.status_code == 400
    assert "completed" in resp_canc.json()["detail"].lower()


def test_reject_unauthorized_traveler_review(client: TestClient, seed_data: dict):
    """
    T13: Enforces authorization and ownership.
    Traveler 2 cannot submit a review for Traveler 1's booking (404 Not Found).
    """
    traveler_2 = seed_data["traveler_user_2"]
    exp_a = seed_data["exp_a"]
    booking_1 = seed_data["booking_completed_1"]  # belongs to traveler 1

    resp = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "5.00"},
        headers=auth_headers(traveler_2),
    )
    assert resp.status_code == 404
    assert "Eligible booking not found" in resp.json()["detail"]


def test_reject_non_traveler_review_submission(client: TestClient, seed_data: dict):
    """
    T13: Enforces role checks. Only callers with 'traveler' role can submit reviews.
    Providers and Admins receive 403 Forbidden.
    """
    provider = seed_data["provider_user_a"]
    admin = seed_data["admin_user"]
    exp_a = seed_data["exp_a"]
    booking_1 = seed_data["booking_completed_1"]

    resp_prov = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "5.00"},
        headers=auth_headers(provider),
    )
    assert resp_prov.status_code == 403

    resp_admin = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "5.00"},
        headers=auth_headers(admin),
    )
    assert resp_admin.status_code == 403


def test_prevent_duplicate_review_for_same_booking(client: TestClient, seed_data: dict):
    """
    T13: Strictly prevents duplicate review submissions for the same booking (409 Conflict).
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking = seed_data["booking_completed_1"]

    payload = {
        "booking_id": booking.id,
        "rating": "4.00",
        "title": "First Review",
        "comment": "Great experience.",
    }

    # First submission -> 201 Created
    resp1 = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json=payload,
        headers=auth_headers(traveler),
    )
    assert resp1.status_code == 201

    # Second submission -> 409 Conflict
    resp2 = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json=payload,
        headers=auth_headers(traveler),
    )
    assert resp2.status_code == 409
    assert "already submitted a review" in resp2.json()["detail"]


def test_review_rating_validation_bounds(client: TestClient, seed_data: dict):
    """
    T13: Validates that ratings must be within the range [1.00, 5.00].
    Values < 1.00 or > 5.00 are rejected with 422 Unprocessable Content.
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking = seed_data["booking_completed_1"]

    # 1. Under minimum (0.50)
    resp_under = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking.id, "rating": "0.50"},
        headers=auth_headers(traveler),
    )
    assert resp_under.status_code == 422

    # 2. Over maximum (5.50)
    resp_over = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking.id, "rating": "5.50"},
        headers=auth_headers(traveler),
    )
    assert resp_over.status_code == 422

    # 3. Exact valid boundaries (1.00 and 5.00)
    resp_valid = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking.id, "rating": "1.00"},
        headers=auth_headers(traveler),
    )
    assert resp_valid.status_code == 201


def test_cross_module_and_listing_isolation(client: TestClient, seed_data: dict):
    """
    T13: Prevents cross-module review submissions.
    A Module 03 activity booking cannot be reviewed under an eco experience (404).
    A booking for Experience A cannot be reviewed under Experience B (404).
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    exp_b = seed_data["exp_b"]
    booking_m03 = seed_data["booking_module03"]
    booking_a = seed_data["booking_completed_1"]

    # 1. Generic Module 03 booking submitted to Eco Experience A
    resp_m03 = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_m03.id, "rating": "5.00"},
        headers=auth_headers(traveler),
    )
    assert resp_m03.status_code == 404

    # 2. Experience A's booking submitted to Experience B
    resp_cross = client.post(
        f"/api/v1/eco-experiences/{exp_b.id}/reviews",
        json={"booking_id": booking_a.id, "rating": "5.00"},
        headers=auth_headers(traveler),
    )
    assert resp_cross.status_code == 404


def test_public_review_listing_and_average_calculation(client: TestClient, seed_data: dict):
    """
    T13: Public review retrieval endpoint.
    Retrieves published reviews, calculates average rating dynamically, and verifies privacy rules.
    """
    traveler_1 = seed_data["traveler_user_1"]
    traveler_2 = seed_data["traveler_user_2"]
    exp_a = seed_data["exp_a"]
    booking_1 = seed_data["booking_completed_1"]
    booking_2 = seed_data["booking_completed_2"]

    # 1. Initially 0 reviews
    resp_empty = client.get(f"/api/v1/eco-experiences/{exp_a.id}/reviews")
    assert resp_empty.status_code == 200
    assert resp_empty.json()["total"] == 0
    assert resp_empty.json()["average_rating"] == 0.0
    assert resp_empty.json()["items"] == []

    # 2. Traveler 1 submits rating 4.00
    client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "4.00", "title": "Good trip"},
        headers=auth_headers(traveler_1),
    )

    # 3. Traveler 2 submits rating 5.00
    client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_2.id, "rating": "5.00", "title": "Spectacular"},
        headers=auth_headers(traveler_2),
    )

    # 4. Public query
    resp = client.get(f"/api/v1/eco-experiences/{exp_a.id}/reviews")
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    assert data["average_rating"] == 4.5  # (4.0 + 5.0) / 2
    assert len(data["items"]) == 2
    assert data["items"][0]["reviewer_name"] in ["Traveler One", "Traveler Two"]
    assert "email" not in data["items"][0]


def test_public_reviews_404_for_unpublished_experience(client: TestClient, db_session: Session, seed_data: dict):
    """
    T13: Public review endpoint returns 404 for draft or unpublished experiences.
    """
    exp_a = seed_data["exp_a"]
    exp_a.is_published = False
    db_session.commit()

    resp = client.get(f"/api/v1/eco-experiences/{exp_a.id}/reviews")
    assert resp.status_code == 404


# ==============================================================================
# TEST SUITE: P17 Provider Ratings & Reviews
# ==============================================================================


def test_provider_review_listing_and_filtering(client: TestClient, seed_data: dict):
    """
    P17: Provider inspects reviews across owned experiences with aggregate metrics and filters.
    """
    provider_a = seed_data["provider_user_a"]
    traveler_1 = seed_data["traveler_user_1"]
    traveler_2 = seed_data["traveler_user_2"]
    exp_a = seed_data["exp_a"]
    booking_1 = seed_data["booking_completed_1"]
    booking_2 = seed_data["booking_completed_2"]

    # Submit reviews: 3.50 and 5.00
    client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "3.50", "title": "Decent trek"},
        headers=auth_headers(traveler_1),
    )
    client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_2.id, "rating": "5.00", "title": "Mind-blowing nature"},
        headers=auth_headers(traveler_2),
    )

    # 1. Provider A fetches all reviews
    resp_all = client.get(
        "/api/v1/eco-experiences/me/reviews",
        headers=auth_headers(provider_a),
    )
    assert resp_all.status_code == 200, resp_all.text
    data_all = resp_all.json()

    assert data_all["total"] == 2
    assert data_all["average_rating"] == 4.25  # (3.5 + 5.0) / 2
    assert len(data_all["items"]) == 2
    assert data_all["items"][0]["eco_experience_title"] == exp_a.title
    assert data_all["items"][0]["booking_reference"] is not None

    # 2. Provider A filters by min_rating=4.00
    resp_filtered = client.get(
        "/api/v1/eco-experiences/me/reviews?min_rating=4.00",
        headers=auth_headers(provider_a),
    )
    assert resp_filtered.status_code == 200
    data_filtered = resp_filtered.json()
    assert data_filtered["total"] == 1
    assert data_filtered["items"][0]["title"] == "Mind-blowing nature"
    assert data_filtered["average_rating"] == 5.0

    # 3. Provider A filters by experience_id
    resp_by_exp = client.get(
        f"/api/v1/eco-experiences/me/reviews?experience_id={exp_a.id}",
        headers=auth_headers(provider_a),
    )
    assert resp_by_exp.status_code == 200
    assert resp_by_exp.json()["total"] == 2


def test_provider_review_cross_provider_isolation(client: TestClient, seed_data: dict):
    """
    P17: Provider B cannot view Provider A's reviews or filter by Provider A's experience (403).
    """
    provider_a = seed_data["provider_user_a"]
    provider_b = seed_data["provider_user_b"]
    traveler_1 = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking_1 = seed_data["booking_completed_1"]

    # Submit review on Provider A's experience
    client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/reviews",
        json={"booking_id": booking_1.id, "rating": "5.00"},
        headers=auth_headers(traveler_1),
    )

    # Provider B calls /me/reviews -> sees 0 reviews
    resp_b_all = client.get(
        "/api/v1/eco-experiences/me/reviews",
        headers=auth_headers(provider_b),
    )
    assert resp_b_all.status_code == 200
    assert resp_b_all.json()["total"] == 0

    # Provider B tries to query Provider A's experience_id -> 403 Forbidden
    resp_b_unauthorized = client.get(
        f"/api/v1/eco-experiences/me/reviews?experience_id={exp_a.id}",
        headers=auth_headers(provider_b),
    )
    assert resp_b_unauthorized.status_code == 403
    assert "Access denied" in resp_b_unauthorized.json()["detail"]


def test_review_transaction_rollback_on_commit_error(client: TestClient, seed_data: dict):
    """
    Transaction safety: If a database commit failure occurs, transaction rolls back safely
    and returns generic HTTP 500 without leaking raw database trace.
    """
    traveler = seed_data["traveler_user_1"]
    exp_a = seed_data["exp_a"]
    booking = seed_data["booking_completed_1"]

    with patch("sqlalchemy.orm.Session.commit", side_effect=Exception("Database lock acquisition timeout")):
        resp = client.post(
            f"/api/v1/eco-experiences/{exp_a.id}/reviews",
            json={"booking_id": booking.id, "rating": "5.00"},
            headers=auth_headers(traveler),
        )
        assert resp.status_code == 500
        assert "internal error" in resp.json()["detail"].lower()
        # Verify no database crash message leaked
        assert "Database lock acquisition timeout" not in resp.json()["detail"]
