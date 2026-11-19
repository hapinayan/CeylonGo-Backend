"""
CeylonGo Rental Backend - Phase 2 Batch 3 Test Suite
====================================================
Automated isolated unit & API tests verifying requirements TR-15 through TR-20:
- TR-15: Ratings & Reviews (verified travelers review completed rentals, rating 1-5, listing sync)
- TR-16: Wishlist / Favorites (save/unsave rental listings, list saved vehicles with availability)
- TR-17: Contact Rental Provider (approved contact channels for authenticated travelers)
- TR-18: Booking Notifications (in-app alerts on creation, confirmation, rejection, cancellation)
- TR-19: Sponsored Rental Listings (active promotion campaign boost & is_sponsored label)
- TR-20: Verified Rental Provider Badge (verified badge & verified_only discovery filter)

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.rental import RentalLocation, RentalVehicleImage, RentalEligibilityCheck
from app.models.shared import (
    BookingReference,
    ListingReference,
    Notification,
    PromotionCampaign,
    Review,
    Wishlist,
)
from app.database.connection import get_db
from app.routers.auth import get_current_user
from app.routers.rental_vehicles import router as rental_vehicles_router
from app.routers.rental_bookings import router as rental_bookings_router
from app.routers.rental_reviews import router as rental_reviews_router
from app.routers.rental_wishlists import router as rental_wishlists_router


# ==============================================================================
# Isolated SQLite Database Fixture
# ==============================================================================

@pytest.fixture
def isolated_db_session():
    """Creates a completely isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _):
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.create_function(
                "sysutcdatetime", 0,
                lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_test_client(db, current_user: User | None = None):
    app = FastAPI()
    app.include_router(rental_vehicles_router)
    app.include_router(rental_bookings_router)
    app.include_router(rental_reviews_router)
    app.include_router(rental_wishlists_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Users
    prov_user1 = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="service_provider")
    prov_user2 = User(id=2, full_name="Kamal Silva", email="kamal@rentals.lk", password_hash="pw", role="service_provider")
    traveler1 = User(id=3, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")
    traveler2 = User(id=4, full_name="Bob Jones", email="bob@traveler.com", password_hash="pw", role="traveler")
    db.add_all([prov_user1, prov_user2, traveler1, traveler2])
    db.flush()

    # Provider Profiles
    prov1 = ProviderProfile(
        id=1,
        user_id=prov_user1.id,
        business_name="Sunil Galle Rentals",
        provider_type="rental",
        phone="+94912233445",
        secondary_phone="+94771234567",
        district="Galle",
        business_city="Galle",
        business_province="Southern",
        business_landmark="Near Galle Fort Clock Tower",
        approval_status="approved",
    )
    prov2 = ProviderProfile(
        id=2,
        user_id=prov_user2.id,
        business_name="Kamal Kandy Rides",
        provider_type="rental",
        phone="+94812345678",
        district="Kandy",
        business_city="Kandy",
        business_province="Central",
        business_landmark="Near Dalada Maligawa",
        approval_status="pending",  # Unverified / pending provider
    )
    traveler_eligibility = RentalEligibilityCheck(
        id=1,
        user_id=traveler1.id,
        driver_license_number="B8889991",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    traveler_kyc = ProviderProfile(
        id=999,
        user_id=traveler1.id,
        business_name="Alice KYC",
        provider_type="rental",
        phone="+94771112233",
        district="Galle",
        date_of_birth=date(1995, 6, 15),
        approval_status="approved",
    )
    db.add_all([prov1, prov2, traveler_eligibility, traveler_kyc])
    db.flush()

    # Rental Locations
    loc1 = RentalLocation(
        id=1,
        provider_id=prov1.id,
        location_name="Galle Fort Hub",
        address="12 Hospital Street, Galle Fort",
        district="Galle",
        city="Galle",
        latitude=Decimal("6.032800"),
        longitude=Decimal("80.217000"),
        is_active=True,
    )
    loc2 = RentalLocation(
        id=2,
        provider_id=prov2.id,
        location_name="Kandy City Center Hub",
        address="Dalada Veediya, Kandy",
        district="Kandy",
        city="Kandy",
        latitude=Decimal("7.290600"),
        longitude=Decimal("80.633700"),
        is_active=True,
    )
    db.add_all([loc1, loc2])
    db.flush()

    # Vehicles
    v1 = RentalVehicle(
        id=1,
        provider_id=prov1.id,
        vehicle_type="bike",
        vehicle_name="Yamaha FZ 150cc",
        description="Premium motorbike for coastal exploration.",
        price_per_day=Decimal("7000.00"),
        status="available",
        approval_status="approved",
        district="Galle",
        city="Galle",
        location_id=loc1.id,
        cancellation_policy="Free cancellation up to 24 hours before pickup.",
        free_cancellation_hours=24,
    )
    v2 = RentalVehicle(
        id=2,
        provider_id=prov1.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4-Stroke Tuk-Tuk",
        description="Authentic Sri Lankan three-wheeler.",
        price_per_day=Decimal("10000.00"),
        status="available",
        approval_status="approved",
        district="Galle",
        city="Galle",
        location_id=loc1.id,
        cancellation_policy="Standard policy",
        free_cancellation_hours=48,
    )
    v3 = RentalVehicle(
        id=3,
        provider_id=prov2.id,
        vehicle_type="bicycle",
        vehicle_name="Giant Mountain Bike",
        description="Rugged hill-country bicycle.",
        price_per_day=Decimal("3000.00"),
        status="available",
        approval_status="approved",
        district="Kandy",
        city="Kandy",
        location_id=loc2.id,
    )
    v4_unapproved = RentalVehicle(
        id=4,
        provider_id=prov1.id,
        vehicle_type="bike",
        vehicle_name="Draft Honda Dio",
        description="Unapproved listing under review.",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="pending",
        district="Galle",
        city="Galle",
    )
    db.add_all([v1, v2, v3, v4_unapproved])
    db.flush()

    # Image for v1
    img1 = RentalVehicleImage(
        vehicle_id=v1.id,
        image_url="https://ceylongo.storage/vehicles/yamaha-fz-front.jpg",
        caption="Front angle",
        display_order=1,
        is_primary=True,
    )
    db.add(img1)
    db.commit()

    return {
        "prov1": prov1,
        "prov2": prov2,
        "prov_user1": prov_user1,
        "prov_user2": prov_user2,
        "traveler1": traveler1,
        "traveler2": traveler2,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4_unapproved": v4_unapproved,
    }


# ==============================================================================
# TR-15: RATINGS & REVIEWS TESTS
# ==============================================================================

def test_submit_review_for_completed_booking_success(isolated_db_session, seed_test_data):
    """TR-15: Verified traveler can submit a review for a completed rental booking."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    p_user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Seed completed booking
    booking = RentalBooking(
        id=101,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now - timedelta(days=5),
        return_at=now - timedelta(days=2),
        total_price=Decimal("21000.00"),
        status="completed",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    b_ref = BookingReference(
        booking_reference="RB-101-TESTREF",
        module_type="rental_vehicle",
        target_booking_id=booking.id,
        user_id=t1.id,
        provider_profile_id=v1.provider_id,
        total_price=booking.total_price,
        booking_status="completed",
    )
    db.add(b_ref)
    db.commit()

    client = make_test_client(db, current_user=t1)
    payload = {
        "booking_id": booking.id,
        "rating": 5.0,
        "title": "Fantastic Bike Rental!",
        "comment": "The Yamaha FZ was in pristine condition. Highly recommend Sunil's service!",
    }

    resp = client.post("/api/v1/rental-reviews", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["rating"] == "5.00" or float(data["rating"]) == 5.0
    assert data["reviewer_name"] == "Alice Smith"
    assert data["is_verified_booking"] is True
    assert data["title"] == "Fantastic Bike Rental!"

    # Verify review in DB
    rev = db.query(Review).filter(Review.id == data["id"]).first()
    assert rev is not None
    assert rev.user_id == t1.id
    assert rev.booking_reference_id == b_ref.id

    # Verify ListingReference was created/linked
    l_ref = db.query(ListingReference).filter(ListingReference.id == rev.listing_reference_id).first()
    assert l_ref is not None
    assert l_ref.module_type == "rental_vehicle"
    assert l_ref.target_id == v1.id

    # Verify provider notification was dispatched
    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == p_user.id,
            Notification.notification_type == "new_rental_review",
        )
        .first()
    )
    assert notif is not None
    assert "Alice Smith" in notif.message


def test_submit_review_for_uncompleted_booking_rejected(isolated_db_session, seed_test_data):
    """TR-15: Attempting to review an uncompleted (active/pending/confirmed) booking is rejected (400)."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Active booking (not completed)
    booking = RentalBooking(
        id=102,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now - timedelta(days=1),
        return_at=now + timedelta(days=2),
        total_price=Decimal("21000.00"),
        status="active",
    )
    db.add(booking)
    db.commit()

    client = make_test_client(db, current_user=t1)
    payload = {
        "booking_id": booking.id,
        "rating": 4.0,
        "title": "Early review",
        "comment": "Nice so far",
    }

    resp = client.post("/api/v1/rental-reviews", json=payload)
    assert resp.status_code == 400
    assert "completed" in resp.json()["detail"].lower()


def test_duplicate_review_blocked(isolated_db_session, seed_test_data):
    """TR-15: Submitting a second review for the same completed booking returns 409 Conflict."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    booking = RentalBooking(
        id=103,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now - timedelta(days=5),
        return_at=now - timedelta(days=3),
        total_price=Decimal("14000.00"),
        status="completed",
    )
    db.add(booking)
    db.flush()

    b_ref = BookingReference(
        booking_reference="RB-103-TESTREF",
        module_type="rental_vehicle",
        target_booking_id=booking.id,
        user_id=t1.id,
        provider_profile_id=v1.provider_id,
        total_price=booking.total_price,
        booking_status="completed",
    )
    db.add(b_ref)
    db.commit()

    client = make_test_client(db, current_user=t1)
    payload = {"booking_id": booking.id, "rating": 5.0, "title": "First review"}

    resp1 = client.post("/api/v1/rental-reviews", json=payload)
    assert resp1.status_code == 201

    # Second review attempt
    resp2 = client.post("/api/v1/rental-reviews", json=payload)
    assert resp2.status_code == 409
    assert "already submitted a review" in resp2.json()["detail"].lower()


def test_get_vehicle_reviews_pagination_and_average(isolated_db_session, seed_test_data):
    """TR-15: Public endpoint returns vehicle reviews, pagination, and calculated average rating."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    t2 = seed_test_data["traveler2"]
    v1 = seed_test_data["v1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Create listing reference
    l_ref = ListingReference(
        module_type="rental_vehicle",
        target_id=v1.id,
        provider_profile_id=v1.provider_id,
        title=v1.vehicle_name,
        district="Galle",
        is_active=True,
    )
    db.add(l_ref)
    db.flush()

    # Add 2 reviews (4.0 and 5.0) -> Average = 4.5
    r1 = Review(
        listing_reference_id=l_ref.id,
        user_id=t1.id,
        rating=Decimal("4.00"),
        title="Good experience",
        comment="Smooth pickup",
        is_verified_booking=True,
        is_published=True,
    )
    r2 = Review(
        listing_reference_id=l_ref.id,
        user_id=t2.id,
        rating=Decimal("5.00"),
        title="Outstanding!",
        comment="Loved the bike",
        is_verified_booking=True,
        is_published=True,
    )
    db.add_all([r1, r2])
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{v1.id}/reviews")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert data["average_rating"] == 4.5
    assert len(data["items"]) == 2


# ==============================================================================
# TR-16: WISHLIST / FAVORITES TESTS
# ==============================================================================

def test_add_and_remove_rental_wishlist(isolated_db_session, seed_test_data):
    """TR-16: Save and unsave rental listings to traveler's personal wishlist."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]

    client = make_test_client(db, current_user=t1)

    # 1. Add to wishlist
    resp_add = client.post(f"/api/v1/rental-wishlist/{v1.id}")
    assert resp_add.status_code == 201
    assert resp_add.json()["saved"] is True
    assert resp_add.json()["vehicle_id"] == v1.id

    # Verify status endpoint returns True
    resp_status = client.get(f"/api/v1/rental-wishlist/{v1.id}/status")
    assert resp_status.status_code == 200
    assert resp_status.json()["saved"] is True

    # 2. Duplicate add returns 409
    resp_dup = client.post(f"/api/v1/rental-wishlist/{v1.id}")
    assert resp_dup.status_code == 409
    assert "already in your wishlist" in resp_dup.json()["detail"].lower()

    # 3. Remove from wishlist
    resp_del = client.delete(f"/api/v1/rental-wishlist/{v1.id}")
    assert resp_del.status_code == 200
    assert resp_del.json()["saved"] is False

    # Verify status endpoint returns False
    resp_status2 = client.get(f"/api/v1/rental-wishlist/{v1.id}/status")
    assert resp_status2.json()["saved"] is False


def test_get_my_rental_wishlist_items(isolated_db_session, seed_test_data):
    """TR-16: List authenticated traveler's saved vehicles with details and pricing."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    v2 = seed_test_data["v2"]

    client = make_test_client(db, current_user=t1)
    client.post(f"/api/v1/rental-wishlist/{v1.id}")
    client.post(f"/api/v1/rental-wishlist/{v2.id}")

    resp = client.get("/api/v1/rental-wishlist")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Check that items contain vehicle attributes and primary image
    names = {item["vehicle_name"] for item in data["items"]}
    assert "Yamaha FZ 150cc" in names
    assert "Bajaj RE 4-Stroke Tuk-Tuk" in names

    # Verify primary image URL on v1
    v1_item = next(item for item in data["items"] if item["vehicle_id"] == v1.id)
    assert v1_item["primary_image_url"] == "https://ceylongo.storage/vehicles/yamaha-fz-front.jpg"
    assert v1_item["is_available"] is True


def test_add_unapproved_vehicle_to_wishlist_rejected(isolated_db_session, seed_test_data):
    """TR-16: Unapproved vehicles cannot be saved to wishlist (returns 404)."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v4 = seed_test_data["v4_unapproved"]

    client = make_test_client(db, current_user=t1)
    resp = client.post(f"/api/v1/rental-wishlist/{v4.id}")
    assert resp.status_code == 404


# ==============================================================================
# TR-17: CONTACT RENTAL PROVIDER TESTS
# ==============================================================================

def test_get_provider_contact_authenticated(isolated_db_session, seed_test_data):
    """TR-17: Authenticated travelers can view approved provider contact channels."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]

    client = make_test_client(db, current_user=t1)
    resp = client.get(f"/api/v1/rental-vehicles/{v1.id}/provider-contact")
    assert resp.status_code == 200
    data = resp.json()
    assert data["business_name"] == "Sunil Galle Rentals"
    assert data["phone"] == "+94912233445"
    assert data["secondary_phone"] == "+94771234567"
    assert data["district"] == "Galle"
    assert "Galle" in data["business_address"]
    assert data["is_verified"] is True
    assert data["approval_status"] == "approved"


def test_get_provider_contact_unauthenticated_rejected(isolated_db_session, seed_test_data):
    """TR-17: Unauthenticated callers cannot view provider contact info."""
    db = isolated_db_session
    v1 = seed_test_data["v1"]

    client = make_test_client(db, current_user=None)
    resp = client.get(f"/api/v1/rental-vehicles/{v1.id}/provider-contact")
    assert resp.status_code in (401, 403)


def test_get_provider_contact_unapproved_vehicle_returns_404(isolated_db_session, seed_test_data):
    """TR-17: Unapproved vehicle contact cannot be queried."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v4 = seed_test_data["v4_unapproved"]

    client = make_test_client(db, current_user=t1)
    resp = client.get(f"/api/v1/rental-vehicles/{v4.id}/provider-contact")
    assert resp.status_code == 404


# ==============================================================================
# TR-18: BOOKING NOTIFICATIONS TESTS
# ==============================================================================

def test_booking_creation_triggers_provider_notification(isolated_db_session, seed_test_data):
    """TR-18: Creating a booking sends an in-app notification to the vehicle's provider."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    p_user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    client = make_test_client(db, current_user=t1)
    payload = {
        "vehicle_id": v1.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "pay_at_location",
    }

    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 201
    booking_id = resp.json()["id"]

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == p_user.id,
            Notification.notification_type == "rental_booking_created",
        )
        .first()
    )
    assert notif is not None
    assert f"#{booking_id}" in notif.title
    assert "Yamaha FZ 150cc" in notif.message


def test_booking_cancellation_triggers_provider_notification(isolated_db_session, seed_test_data):
    """TR-18: Traveler cancelling a booking sends an in-app alert to the provider."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    p_user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    booking = RentalBooking(
        id=201,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now + timedelta(days=3),
        return_at=now + timedelta(days=5),
        total_price=Decimal("14000.00"),
        status="confirmed",
        free_cancellation_hours_snapshot=24,
    )
    db.add(booking)
    db.commit()

    client = make_test_client(db, current_user=t1)
    resp = client.patch(f"/api/v1/rental-bookings/{booking.id}/cancel")
    assert resp.status_code == 200

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == p_user.id,
            Notification.notification_type == "rental_booking_cancelled",
        )
        .first()
    )
    assert notif is not None
    assert f"#{booking.id}" in notif.title


def test_provider_rejection_triggers_traveler_notification(isolated_db_session, seed_test_data):
    """TR-18: Provider rejecting a pending booking dispatches a notification to the traveler."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    p_user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    booking = RentalBooking(
        id=202,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now + timedelta(days=3),
        return_at=now + timedelta(days=5),
        total_price=Decimal("14000.00"),
        status="pending",
    )
    db.add(booking)
    db.commit()

    # Provider client rejects booking
    client = make_test_client(db, current_user=p_user)
    payload = {"status": "rejected", "rejection_reason": "Vehicle booked for local festival"}
    resp = client.patch(f"/api/v1/rental-bookings/{booking.id}/status", json=payload)
    assert resp.status_code == 200

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == t1.id,
            Notification.notification_type == "rental_booking_rejected",
        )
        .first()
    )
    assert notif is not None
    assert "Vehicle booked for local festival" in notif.message


def test_provider_confirmation_triggers_traveler_notification(isolated_db_session, seed_test_data):
    """TR-18: Provider confirming a booking dispatches a confirmation notification to traveler."""
    db = isolated_db_session
    t1 = seed_test_data["traveler1"]
    v1 = seed_test_data["v1"]
    p_user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    booking = RentalBooking(
        id=203,
        vehicle_id=v1.id,
        user_id=t1.id,
        pickup_at=now + timedelta(days=3),
        return_at=now + timedelta(days=5),
        total_price=Decimal("14000.00"),
        status="pending",
        expires_at=now + timedelta(minutes=25),
    )
    db.add(booking)
    db.commit()

    client = make_test_client(db, current_user=p_user)
    payload = {"status": "confirmed"}
    resp = client.patch(f"/api/v1/rental-bookings/{booking.id}/status", json=payload)
    assert resp.status_code == 200

    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == t1.id,
            Notification.notification_type == "rental_booking_confirmed",
        )
        .first()
    )
    assert notif is not None
    assert "confirmed by the provider" in notif.message


# ==============================================================================
# TR-19: SPONSORED RENTAL LISTINGS TESTS
# ==============================================================================

def test_sponsored_vehicle_boosted_and_labeled(isolated_db_session, seed_test_data):
    """TR-19: Active rental promotion campaign boosts vehicle to top and labels is_sponsored=True."""
    db = isolated_db_session
    prov1 = seed_test_data["prov1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Provider 1 runs an active rental promotion campaign
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=prov1.id,
        campaign_name="Galle Summer Rental Spotlight",
        target_module="rental",
        budget=Decimal("50000.00"),
        spent_amount=Decimal("1200.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    db.add(campaign)
    db.commit()

    client = make_test_client(db)
    # Sort by price ascending: normally v3 (3000 LKR) would be first, but v1/v2 (sponsored) should be prioritized
    resp = client.get("/api/v1/rental-vehicles?sort_by=price_asc")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) >= 3

    # First item must be from sponsored provider
    assert items[0]["is_sponsored"] is True
    assert items[0]["provider_id"] == prov1.id

    # Non-sponsored vehicle (v3 from prov2) has is_sponsored=False
    v3_item = next(i for i in items if i["id"] == seed_test_data["v3"].id)
    assert v3_item["is_sponsored"] is False


# ==============================================================================
# TR-20: VERIFIED RENTAL PROVIDER BADGE TESTS
# ==============================================================================

def test_verified_provider_badge_flag_in_discovery_and_detail(isolated_db_session, seed_test_data):
    """TR-20: is_provider_verified is True for approved providers and False for unverified."""
    db = isolated_db_session
    v1 = seed_test_data["v1"]  # Provider 1 is approved
    v3 = seed_test_data["v3"]  # Provider 2 is pending

    client = make_test_client(db)

    # In detail endpoint
    resp1 = client.get(f"/api/v1/rental-vehicles/{v1.id}")
    assert resp1.status_code == 200
    assert resp1.json()["is_provider_verified"] is True
    assert resp1.json()["provider_business_name"] == "Sunil Galle Rentals"

    resp3 = client.get(f"/api/v1/rental-vehicles/{v3.id}")
    assert resp3.status_code == 200
    assert resp3.json()["is_provider_verified"] is False


def test_discovery_filter_verified_only(isolated_db_session, seed_test_data):
    """TR-20: verified_only=true returns only listings from admin-approved providers."""
    db = isolated_db_session
    client = make_test_client(db)

    # Without filter: returns both approved and unapproved providers' available vehicles
    resp_all = client.get("/api/v1/rental-vehicles")
    assert resp_all.status_code == 200
    all_provider_ids = {item["provider_id"] for item in resp_all.json()["items"]}
    assert 1 in all_provider_ids
    assert 2 in all_provider_ids

    # With verified_only=true: returns only vehicles from Provider 1
    resp_verified = client.get("/api/v1/rental-vehicles?verified_only=true")
    assert resp_verified.status_code == 200
    verified_items = resp_verified.json()["items"]
    assert len(verified_items) > 0
    for item in verified_items:
        assert item["is_provider_verified"] is True
        assert item["provider_id"] == 1
