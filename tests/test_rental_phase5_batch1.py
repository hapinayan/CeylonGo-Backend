"""
CeylonGo Rental Backend - Phase 5 Batch 1 Test Suite
===================================================
Covers:
- AR-01: Driving License Verification (traveler submission, admin review, booking gate)
- AR-02: Minimum Age Requirements (fail-closed trusted KYC verification, category policy: Bike 18+, Tuk-Tuk 21+, Bicycle 16+)
- AR-11: Tuk-Tuk Permit Verification (domestic Class B1 vs foreign IDP / recognition permit, handover recheck)
- Security: Path traversal protection on documents, credential masking, audit logging
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalConditionEvidence,
    RentalEligibilityCheck,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import AdminAuditLog, BookingReference, Notification
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.rental_bookings import router as rental_bookings_router
from app.routers.rental_eligibility import router as rental_eligibility_router
from app.routers.rental_providers import router as rental_providers_router


@pytest.fixture
def isolated_db_session():
    """In-memory SQLite session with full table schema."""
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

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def make_test_client(db_session, current_user=None):
    """FastAPI test client with dependency overrides."""
    app = FastAPI()
    app.include_router(rental_eligibility_router)
    app.include_router(rental_bookings_router)
    app.include_router(rental_providers_router)

    app.dependency_overrides[get_db] = lambda: db_session
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_phase5_data(isolated_db_session):
    """Seed users, vehicles, and provider infrastructure."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    traveler_local = User(id=1, full_name="Nimal Perera", email="nimal@traveler.lk", password_hash="pw", role="traveler")
    traveler_foreign = User(id=2, full_name="John Doe", email="john@uktraveler.com", password_hash="pw", role="traveler")
    traveler_underage = User(id=3, full_name="Minor Driver", email="minor@traveler.lk", password_hash="pw", role="traveler")
    admin_user = User(id=4, full_name="Admin Lanka", email="admin@ceylongo.lk", password_hash="pw", role="admin")
    provider_user = User(id=5, full_name="Provider Owner", email="owner@rentals.lk", password_hash="pw", role="provider")

    db.add_all([traveler_local, traveler_foreign, traveler_underage, admin_user, provider_user])
    db.flush()

    # 2. Provider Profile
    provider = ProviderProfile(
        id=101,
        user_id=provider_user.id,
        business_name="Lanka Ride Solutions",
        provider_type="rental",
        phone="+94771234567",
        district="Colombo",
        approval_status="approved",
    )
    db.add(provider)
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=1,
        provider_id=provider.id,
        location_name="Colombo Central Station",
        district="Colombo",
        city="Colombo 01",
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles (Bike, Tuk-Tuk, Bicycle)
    bike = RentalVehicle(
        id=1,
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Honda Dio Scooter",
        price_per_day=Decimal("3000.00"),
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    tuktuk = RentalVehicle(
        id=2,
        provider_id=provider.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4S Tuk-Tuk",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    bicycle = RentalVehicle(
        id=3,
        provider_id=provider.id,
        vehicle_type="bicycle",
        vehicle_name="Trek Mountain Bicycle",
        price_per_day=Decimal("1200.00"),
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    db.add_all([bike, tuktuk, bicycle])
    db.commit()

    return {
        "traveler_local": traveler_local,
        "traveler_foreign": traveler_foreign,
        "traveler_underage": traveler_underage,
        "admin_user": admin_user,
        "provider_user": provider_user,
        "provider": provider,
        "loc": loc,
        "bike": bike,
        "tuktuk": tuktuk,
        "bicycle": bicycle,
    }


# ==============================================================================
# 1. TRAVELER LICENSE SUBMISSION & RESUBMISSION TESTS (AR-01, Condition 10)
# ==============================================================================

def test_traveler_submit_driving_license_success(isolated_db_session, seed_phase5_data):
    """AR-01: Traveler submits valid driving credentials; returns masked data in pending status."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    client = make_test_client(db, traveler)

    payload = {
        "driver_license_number": "B9876543",
        "license_country": "Sri Lanka",
        "license_expiry_date": (date.today() + timedelta(days=365)).isoformat(),
        "license_front_image": "/uploads/rentals/license_front.jpg",
        "license_back_image": "/uploads/rentals/license_back.jpg",
    }
    resp = client.post("/api/v1/rental-eligibility/license", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["user_id"] == traveler.id
    assert data["verification_status"] == "pending"
    assert data["license_country"] == "Sri Lanka"
    # Masked credential: "B9****6543"
    assert "B9" in data["driver_license_number_masked"]
    assert "6543" in data["driver_license_number_masked"]
    assert "****" in data["driver_license_number_masked"]
    assert data["is_eligible_for_motorcycles"] is False  # Pending is not yet approved
    assert data["is_eligible_for_bicycles"] is True  # Bicycles are always eligible


def test_submit_license_expired_date_rejected(isolated_db_session, seed_phase5_data):
    """AR-01: Submitting an already expired driving license is immediately rejected with 400."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    client = make_test_client(db, traveler)

    payload = {
        "driver_license_number": "B9876543",
        "license_country": "Sri Lanka",
        "license_expiry_date": (date.today() - timedelta(days=5)).isoformat(),
    }
    resp = client.post("/api/v1/rental-eligibility/license", json=payload)
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_resubmit_license_invalidates_prior_approval_resets_to_pending(isolated_db_session, seed_phase5_data):
    """Condition 10: Resubmitting license invalidates any prior approval, resetting status to 'pending'."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]

    # 1. Traveler previously had approved license
    existing = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B1111111",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=100),
        verification_status="approved",
        reviewed_by=4,
        reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(existing)
    db.commit()

    # 2. Resubmits updated credentials
    client = make_test_client(db, traveler)
    resubmit_payload = {
        "driver_license_number": "B2222222",
        "license_country": "Sri Lanka",
        "license_expiry_date": (date.today() + timedelta(days=365)).isoformat(),
    }
    resp = client.post("/api/v1/rental-eligibility/license", json=resubmit_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["verification_status"] == "pending"
    assert data["reviewed_at"] is None

    # Check DB state directly
    record = db.query(RentalEligibilityCheck).filter_by(user_id=traveler.id).first()
    assert record.verification_status == "pending"
    assert record.reviewed_by is None
    assert record.reviewed_at is None
    assert record.driver_license_number == "B2222222"


def test_get_my_eligibility_status_unregistered(isolated_db_session, seed_phase5_data):
    """Traveler who has not submitted credentials receives unregistered status with bicycle permission."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    client = make_test_client(db, traveler)

    resp = client.get("/api/v1/rental-eligibility/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_status"] == "unregistered"
    assert data["is_eligible_for_motorcycles"] is False
    assert data["is_eligible_for_bicycles"] is True


# ==============================================================================
# 2. ADMIN ELIGIBILITY MODERATION & AUDIT LOGGING (AR-01, Condition 4, 5)
# ==============================================================================

def test_admin_list_eligibility_queue(isolated_db_session, seed_phase5_data):
    """Admin can query paginated driving license submissions; non-admin receives 403."""
    db = isolated_db_session
    admin = seed_phase5_data["admin_user"]
    traveler = seed_phase5_data["traveler_local"]

    # Seed pending submission
    sub = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B5556667",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=300),
        verification_status="pending",
    )
    db.add(sub)
    db.commit()

    # Admin check
    admin_client = make_test_client(db, admin)
    resp = admin_client.get("/api/v1/rental-eligibility/admin/all")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["driver_license_number"] == "B5556667"

    # Non-admin rejected
    traveler_client = make_test_client(db, traveler)
    resp_trav = traveler_client.get("/api/v1/rental-eligibility/admin/all")
    assert resp_trav.status_code == 403


def test_admin_review_rejection_requires_explicit_reason(isolated_db_session, seed_phase5_data):
    """Condition 4: Rejecting a driving license requires an explicit rejection reason."""
    db = isolated_db_session
    admin = seed_phase5_data["admin_user"]
    traveler = seed_phase5_data["traveler_local"]

    sub = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B9998881",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=300),
        verification_status="pending",
    )
    db.add(sub)
    db.commit()

    admin_client = make_test_client(db, admin)

    # 1. Missing rejection reason -> 400
    resp_empty = admin_client.post(
        f"/api/v1/rental-eligibility/admin/{sub.id}/review",
        json={"decision": "rejected", "rejection_reason": "   "},
    )
    assert resp_empty.status_code == 400
    assert "mandatory" in resp_empty.json()["detail"].lower()

    # 2. Provided rejection reason -> 200 OK & Audit log recorded
    resp_valid = admin_client.post(
        f"/api/v1/rental-eligibility/admin/{sub.id}/review",
        json={"decision": "rejected", "rejection_reason": "License image is blurry and illegible."},
    )
    assert resp_valid.status_code == 200
    assert resp_valid.json()["new_status"] == "rejected"

    # Verify AdminAuditLog record (Condition 5)
    audit = db.query(AdminAuditLog).filter_by(entity_name="rental_eligibility_checks", entity_id=sub.id).first()
    assert audit is not None
    assert audit.action == "reject_driver_eligibility"
    assert "blurry" in audit.details


def test_admin_review_approval_and_expired_guard(isolated_db_session, seed_phase5_data):
    """Admin approval approves license and creates audit log; cannot approve expired license."""
    db = isolated_db_session
    admin = seed_phase5_data["admin_user"]
    traveler = seed_phase5_data["traveler_local"]

    # 1. Valid pending submission
    sub = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B3334445",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=200),
        verification_status="pending",
    )
    db.add(sub)
    db.commit()

    admin_client = make_test_client(db, admin)
    resp = admin_client.post(
        f"/api/v1/rental-eligibility/admin/{sub.id}/review",
        json={"decision": "approved"},
    )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "approved"

    audit = db.query(AdminAuditLog).filter_by(entity_name="rental_eligibility_checks", entity_id=sub.id).first()
    assert audit is not None
    assert audit.action == "approve_driver_eligibility"

    # 2. Expired license cannot be approved
    sub.license_expiry_date = date.today() - timedelta(days=1)
    db.commit()

    resp_expired = admin_client.post(
        f"/api/v1/rental-eligibility/admin/{sub.id}/review",
        json={"decision": "approved"},
    )
    assert resp_expired.status_code == 400
    assert "expired" in resp_expired.json()["detail"].lower()


# ==============================================================================
# 3. MOTORIZED BOOKING GATING & EXCLUSIONS (AR-01, AR-02, AR-11, Condition 6, 8, 14)
# ==============================================================================

def test_motorized_booking_blocked_without_approved_license(isolated_db_session, seed_phase5_data):
    """Condition 6: Motorized booking (bike) is blocked with 403 if traveler has no approved license."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    bike = seed_phase5_data["bike"]

    # Seed trusted DOB profile so we isolate the license check
    profile = ProviderProfile(
        id=501,
        user_id=traveler.id,
        business_name="Traveler KYC",
        provider_type="rental",
        phone="+94771111111",
        district="Colombo",
        date_of_birth=date(1995, 1, 1),
        approval_status="approved",
    )
    db.add(profile)
    db.commit()

    client = make_test_client(db, traveler)
    now = datetime.now(timezone.utc)
    payload = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "pay_at_location",
    }

    # No license -> 403
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 403
    assert "approved driving license" in resp.json()["detail"].lower()


def test_motorized_booking_blocked_with_license_expiring_before_return(isolated_db_session, seed_phase5_data):
    """License expiring before rental return date blocks motorized booking with 400."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    bike = seed_phase5_data["bike"]

    now = datetime.now(timezone.utc)
    pickup = now + timedelta(days=2)
    return_at = now + timedelta(days=5)

    # License expires on day 4 (before return on day 5)
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B7778889",
        license_country="Sri Lanka",
        license_expiry_date=pickup.date() + timedelta(days=2),
        verification_status="approved",
    )
    profile = ProviderProfile(
        id=502,
        user_id=traveler.id,
        business_name="Traveler KYC",
        provider_type="rental",
        phone="+94771111111",
        district="Colombo",
        date_of_birth=date(1995, 1, 1),
        approval_status="approved",
    )
    db.add_all([eligibility, profile])
    db.commit()

    client = make_test_client(db, traveler)
    payload = {
        "vehicle_id": bike.id,
        "pickup_at": pickup.isoformat(),
        "return_at": return_at.isoformat(),
        "payment_method": "pay_at_location",
    }
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 400
    assert "expires on" in resp.json()["detail"].lower()


def test_bicycle_booking_exempt_from_license_and_permit_checks(isolated_db_session, seed_phase5_data):
    """Condition 8: Bicycles are strictly exempt from driving license and permit requirements."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    bicycle = seed_phase5_data["bicycle"]

    # Traveler has NO license and NO KYC profile in DB
    client = make_test_client(db, traveler)
    now = datetime.now(timezone.utc)
    payload = {
        "vehicle_id": bicycle.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "pay_at_location",
    }

    # Bicycle booking succeeds!
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 201
    assert resp.json()["vehicle_id"] == bicycle.id


def test_age_verification_fails_closed_when_verified_dob_unavailable(isolated_db_session, seed_phase5_data):
    """Condition 14: Motorized booking fails closed with 403 when trusted KYC DOB is missing."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    bike = seed_phase5_data["bike"]

    # Traveler has approved license, but NO trusted KYC DOB profile
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B9990001",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    db.add(eligibility)
    db.commit()

    client = make_test_client(db, traveler)
    now = datetime.now(timezone.utc)
    payload = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "pay_at_location",
    }

    # Fails closed reporting missing KYC date of birth dependency
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 403
    assert "trusted date of birth" in resp.json()["detail"].lower()
    assert "ar-02" in resp.json()["detail"].lower()


def test_age_verification_enforces_category_minimum_age(isolated_db_session, seed_phase5_data):
    """Condition 13: Enforces Bike (18+) and Tuk-Tuk (21+) minimum age requirements."""
    db = isolated_db_session
    minor = seed_phase5_data["traveler_underage"]
    bike = seed_phase5_data["bike"]
    tuktuk = seed_phase5_data["tuktuk"]

    now = datetime.now(timezone.utc)
    pickup_date = (now + timedelta(days=2)).date()

    # 1. 17-year old driver (Under 18)
    dob_17 = date(pickup_date.year - 17, pickup_date.month, pickup_date.day)
    profile_17 = ProviderProfile(
        id=503,
        user_id=minor.id,
        business_name="Minor KYC",
        provider_type="rental",
        phone="+94770000000",
        district="Colombo",
        date_of_birth=dob_17,
        approval_status="approved",
    )
    eligibility = RentalEligibilityCheck(
        user_id=minor.id,
        driver_license_number="B0001112",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    db.add_all([profile_17, eligibility])
    db.commit()

    client = make_test_client(db, minor)
    payload_bike = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
    }
    resp_bike = client.post("/api/v1/rental-bookings", json=payload_bike)
    assert resp_bike.status_code == 403
    assert "at least 18 years old" in resp_bike.json()["detail"].lower()

    # 2. 20-year old driver (Eligible for Bike 18+, but Underage for Tuk-Tuk 21+)
    dob_20 = date(pickup_date.year - 20, pickup_date.month, pickup_date.day)
    profile_17.date_of_birth = dob_20
    db.commit()

    # Bike succeeds at 20
    resp_bike_ok = client.post("/api/v1/rental-bookings", json=payload_bike)
    assert resp_bike_ok.status_code == 201

    # Tuk-Tuk fails at 20 (requires 21+)
    payload_tuk = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=10)).isoformat(),
        "return_at": (now + timedelta(days=12)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 2,
    }
    resp_tuk = client.post("/api/v1/rental-bookings", json=payload_tuk)
    assert resp_tuk.status_code == 403
    assert "at least 21 years old" in resp_tuk.json()["detail"].lower()


# ==============================================================================
# 4. TUK-TUK FOREIGN PERMIT & HANDOVER RECHECK TESTS (AR-11, Condition 7, 15)
# ==============================================================================

def test_tuktuk_foreign_traveler_requires_idp(isolated_db_session, seed_phase5_data):
    """Condition 15 & AR-11: Foreign traveler booking a tuk-tuk requires an IDP / permit endorsement."""
    db = isolated_db_session
    foreign_traveler = seed_phase5_data["traveler_foreign"]
    tuktuk = seed_phase5_data["tuktuk"]

    now = datetime.now(timezone.utc)
    pickup_date = (now + timedelta(days=2)).date()

    # Verified 25-year old adult
    profile = ProviderProfile(
        id=504,
        user_id=foreign_traveler.id,
        business_name="Foreign Traveler KYC",
        provider_type="rental",
        phone="+447712345678",
        district="Colombo",
        date_of_birth=date(pickup_date.year - 25, pickup_date.month, pickup_date.day),
        approval_status="approved",
    )
    # Foreign UK license WITHOUT IDP
    eligibility = RentalEligibilityCheck(
        user_id=foreign_traveler.id,
        driver_license_number="UK-DL-998877",
        license_country="United Kingdom",
        license_expiry_date=date.today() + timedelta(days=365),
        idp_number=None,  # Missing IDP
        verification_status="approved",
    )
    db.add_all([profile, eligibility])
    db.commit()

    client = make_test_client(db, foreign_traveler)
    payload = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 2,
    }

    # 1. Missing IDP -> 403 Forbidden
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 403
    assert "international driving permit" in resp.json()["detail"].lower()

    # 2. Traveler provides IDP and admin verifies -> Booking succeeds
    eligibility.idp_number = "IDP-UK-2026-991"
    db.commit()

    resp_ok = client.post("/api/v1/rental-bookings", json=payload)
    assert resp_ok.status_code == 201


def test_handover_rechecks_license_eligibility_and_sanitizes_response(isolated_db_session, seed_phase5_data):
    """Condition 7: Handover rechecks driving eligibility; returns sanitized confirmation without raw image leak."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    provider_user = seed_phase5_data["provider_user"]
    provider = seed_phase5_data["provider"]
    bike = seed_phase5_data["bike"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Confirmed booking
    booking = RentalBooking(
        id=10,
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now + timedelta(days=1),
        return_at=now + timedelta(days=3),
        total_price=Decimal("6000.00"),
        status="confirmed",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    ref = BookingReference(
        id=10,
        booking_reference="RB-CONF-010",
        module_type="rental_vehicle",
        target_booking_id=10,
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=Decimal("6000.00"),
        booking_status="confirmed",
    )
    db.add_all([booking, ref])
    db.commit()

    provider_client = make_test_client(db, provider_user)
    handover_payload = {
        "odometer_reading": 15000,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
    }

    # 2. Handover attempt WITHOUT approved license -> 400 Bad Request
    resp_no_lic = provider_client.post("/api/v1/provider/rentals/bookings/10/handover", json=handover_payload)
    assert resp_no_lic.status_code == 400
    assert "license verification is missing or not approved" in resp_no_lic.json()["detail"].lower()

    # 3. Add approved license -> Handover succeeds with sanitized verification confirmation
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B5544332",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        license_front_image="/private/uploads/secret_front.jpg",
        license_back_image="/private/uploads/secret_back.jpg",
        verification_status="approved",
    )
    db.add(eligibility)
    db.commit()

    resp_success = provider_client.post("/api/v1/provider/rentals/bookings/10/handover", json=handover_payload)
    assert resp_success.status_code == 200
    data = resp_success.json()
    assert data["status"] == "active"
    assert "Verified (License: Sri Lanka - Approved)" in data["driver_verification"]
    # Verify raw license images are NOT exposed in handover response
    assert "secret_front" not in str(data)
    assert "secret_back" not in str(data)


# ==============================================================================
# 5. SECURE DOCUMENT ACCESS & PATH TRAVERSAL TESTS (Condition 9)
# ==============================================================================

def test_secure_document_access_and_path_traversal_protection(isolated_db_session, seed_phase5_data):
    """Condition 9: Verifies path traversal prevention and RBAC on uploaded license files."""
    db = isolated_db_session
    traveler = seed_phase5_data["traveler_local"]
    other_traveler = seed_phase5_data["traveler_foreign"]

    # Seed record with safe filename
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B1212121",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        license_front_image="/uploads/license_traveler1.jpg",
        verification_status="pending",
    )
    db.add(eligibility)
    db.commit()

    # 1. Path traversal attempt -> 400 Bad Request or 404
    client = make_test_client(db, traveler)
    resp_traversal = client.get("/api/v1/rental-eligibility/document/..%2f..%2fsecret.txt")
    assert resp_traversal.status_code in [400, 404]

    # 2. Other traveler attempting to access traveler 1's document -> 403 Forbidden
    other_client = make_test_client(db, other_traveler)
    resp_forbidden = other_client.get("/api/v1/rental-eligibility/document/license_traveler1.jpg")
    assert resp_forbidden.status_code == 403
