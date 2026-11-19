"""
CeylonGo Rental Backend - Phase 6B Test Suite
=============================================
Verifies Phase 6B Offline Schema & Application Additions:
- AR-02: Verified Traveler Date of Birth (admin review persistence, masking, motorized age enforcement).
- AR-03: Structured Helmet Availability (vehicle create/update, fleet list/detail, public helmet info).
- AR-04: Vehicle-Specific Security Deposit Preference (quote calculation, vehicle detail, offline pickup collection).
- AR-05: Structured Fuel Policy (vehicle create/update, quote calculation, public fuel policy reflection).
- AR-06: Handover Inspection Digital Acknowledgement Metadata (acknowledged_at timestamp, acknowledgement_ip).
- AR-11: Foreign Tuk-Tuk Recognition Permit Verification (submission, admin review, masking, motorized booking check).
- AR-12: Structured Vehicle Insurance Metadata (coverage amount, deductible, provider, policy number, no invented numbers).
- Backward Compatibility: Vehicles without new columns behave safely using existing fallbacks.
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

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalDeposit,
    RentalEligibilityCheck,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
)
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.rental_bookings import router as rental_bookings_router
from app.routers.rental_eligibility import router as rental_eligibility_router
from app.routers.rental_providers import router as rental_providers_router
from app.routers.rental_vehicles import router as rental_vehicles_router


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
    app.include_router(rental_vehicles_router)
    app.include_router(rental_bookings_router)
    app.include_router(rental_eligibility_router)
    app.include_router(rental_providers_router)

    app.dependency_overrides[get_db] = lambda: db_session
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_phase6b_data(isolated_db_session):
    """Seed test users, provider, location, and baseline entities."""
    db = isolated_db_session

    traveler = User(id=1, full_name="Kamal Traveler", email="kamal@ceylon.lk", password_hash="pw", role="traveler")
    foreign_traveler = User(id=2, full_name="Alex Smith", email="alex@uk.com", password_hash="pw", role="traveler")
    provider_user = User(id=3, full_name="Rides Host", email="host@rides.lk", password_hash="pw", role="service_provider")
    admin_user = User(id=4, full_name="Admin Reviewer", email="admin@ceylongo.lk", password_hash="pw", role="admin")

    db.add_all([traveler, foreign_traveler, provider_user, admin_user])
    db.flush()

    provider = ProviderProfile(
        id=101,
        user_id=provider_user.id,
        business_name="Southern Fleet Co",
        provider_type="rental",
        phone="+94771239999",
        district="Galle",
        approval_status="approved",
    )
    db.add(provider)
    db.flush()

    loc = RentalLocation(
        id=201,
        provider_id=provider.id,
        location_name="Galle Fort Hub",
        district="Galle",
        city="Galle",
        address="12 Church Street",
        is_active=True,
    )
    db.add(loc)
    db.flush()

    # Base vehicle for inspections / general booking
    base_bike = RentalVehicle(
        id=301,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Honda Activa 125",
        vehicle_type="bike",
        price_per_day=Decimal("2000.00"),
        status="available",
        approval_status="approved",
        seating_capacity=2,
        fuel_type="petrol",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    db.add(base_bike)
    db.flush()

    plan_bike = RentalRatePlan(
        id=401,
        vehicle_id=base_bike.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("2000.00"),
        is_active=True,
    )
    db.add(plan_bike)
    db.flush()

    return {
        "traveler": traveler,
        "foreign_traveler": foreign_traveler,
        "provider_user": provider_user,
        "provider": provider,
        "admin_user": admin_user,
        "loc": loc,
        "base_bike": base_bike,
    }


# ==============================================================================
# 1. AR-02: VERIFIED TRAVELER DATE OF BIRTH TESTS
# ==============================================================================

def test_ar02_verified_traveler_dob_admin_review_and_masking(isolated_db_session, seed_phase6b_data):
    """Admin reviews eligibility and records verified date of birth; /me returns masked DOB."""
    db = isolated_db_session
    data = seed_phase6b_data
    traveler = data["traveler"]
    admin = data["admin_user"]

    # 1. Traveler submits driving license with date of birth
    client_traveler = make_test_client(db, current_user=traveler)
    exp = date.today() + timedelta(days=365)
    resp_submit = client_traveler.post(
        "/api/v1/rental-eligibility/license",
        json={
            "driver_license_number": "B8839201",
            "license_country": "Sri Lanka",
            "license_expiry_date": exp.isoformat(),
            "license_front_image": "https://storage.ceylongo.lk/lic_front.jpg",
            "license_back_image": "https://storage.ceylongo.lk/lic_back.jpg",
            "date_of_birth": "1997-04-12",
        },
    )
    assert resp_submit.status_code == 200

    # Verify eligibility record created with status pending
    check = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.user_id == traveler.id).first()
    assert check is not None
    assert check.verification_status == "pending"
    assert check.date_of_birth == date(1997, 4, 12)

    # 2. Admin reviews and approves with verified DOB
    client_admin = make_test_client(db, current_user=admin)
    resp_review = client_admin.post(
        f"/api/v1/rental-eligibility/admin/{check.id}/review",
        json={
            "decision": "approved",
            "verified_date_of_birth": "1997-04-12",
            "notes": "Verified against official driving license document",
        },
    )
    assert resp_review.status_code == 200
    db.refresh(check)
    assert check.verification_status == "approved"
    assert check.date_of_birth == date(1997, 4, 12)

    # 3. Traveler checks own status via /me -> DOB is masked
    resp_me = client_traveler.get("/api/v1/rental-eligibility/me")
    assert resp_me.status_code == 200
    res_me = resp_me.json()
    assert res_me["verification_status"] == "approved"
    assert res_me["date_of_birth_masked"] == "1997-**-**"


def test_ar02_verified_traveler_dob_motorized_booking_and_underage_block(isolated_db_session, seed_phase6b_data):
    """Motorized booking checks verified DOB on RentalEligibilityCheck; blocks driver under 18."""
    db = isolated_db_session
    data = seed_phase6b_data
    traveler = data["traveler"]
    admin = data["admin_user"]
    bike = data["base_bike"]
    loc = data["loc"]

    # 1. Traveler submits license with DOB making them 17 at pickup time
    pickup = datetime.now(timezone.utc) + timedelta(days=7)
    dob_underage = date(pickup.year - 17, pickup.month, pickup.day)

    client_traveler = make_test_client(db, current_user=traveler)
    exp = date.today() + timedelta(days=365)
    resp_submit = client_traveler.post(
        "/api/v1/rental-eligibility/license",
        json={
            "driver_license_number": "B9911223",
            "license_country": "Sri Lanka",
            "license_expiry_date": exp.isoformat(),
            "license_front_image": "https://storage.ceylongo.lk/lic_front.jpg",
            "date_of_birth": dob_underage.isoformat(),
        },
    )
    assert resp_submit.status_code == 200
    check = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.user_id == traveler.id).first()

    # Admin approves with the underage DOB
    client_admin = make_test_client(db, current_user=admin)
    client_admin.post(
        f"/api/v1/rental-eligibility/admin/{check.id}/review",
        json={
            "decision": "approved",
            "verified_date_of_birth": dob_underage.isoformat(),
        },
    )

    # Traveler attempts motorized bike booking -> Blocked with 403 Forbidden
    resp_book = client_traveler.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": bike.id,
            "pickup_location_id": loc.id,
            "return_location_id": loc.id,
            "pickup_at": pickup.isoformat(),
            "return_at": (pickup + timedelta(days=1)).isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp_book.status_code == 403
    assert "at least 18 years old" in resp_book.json()["detail"]


# ==============================================================================
# 2. AR-03: STRUCTURED HELMET AVAILABILITY TESTS
# ==============================================================================

def test_ar03_structured_helmet_availability_provider_crud_and_public_details(isolated_db_session, seed_phase6b_data):
    """Provider registers a bike with structured helmet fields; reflected in vehicle list, detail, and public detail."""
    db = isolated_db_session
    data = seed_phase6b_data
    provider_user = data["provider_user"]
    loc = data["loc"]

    client_provider = make_test_client(db, current_user=provider_user)

    # 1. Add bike with structured helmets
    resp_add = client_provider.post(
        "/api/v1/provider/rentals/vehicles",
        json={
            "vehicle_type": "bike",
            "vehicle_name": "Yamaha NMAX 155",
            "price_per_day": "3500.00",
            "seating_capacity": 2,
            "fuel_type": "petrol",
            "location_id": loc.id,
            "helmet_count": 2,
            "helmets_included": True,
            "helmet_sizes": "M, L",
            "security_deposit": "12000.00",
            "fuel_policy": "full_to_full",
        },
    )
    assert resp_add.status_code == 201
    veh_data = resp_add.json()
    new_id = veh_data["id"]
    assert veh_data["helmet_count"] == 2
    assert veh_data["helmets_included"] is True
    assert veh_data["helmet_sizes"] == "M, L"
    assert veh_data["security_deposit"] == "12000.00"
    assert veh_data["fuel_policy"] == "full_to_full"

    # 2. Check provider fleet list
    resp_list = client_provider.get("/api/v1/provider/rentals/vehicles")
    assert resp_list.status_code == 200
    items = resp_list.json()["items"]
    found = next(i for i in items if i["id"] == new_id)
    assert found["helmet_count"] == 2
    assert found["helmets_included"] is True
    assert found["helmet_sizes"] == "M, L"
    assert found["security_deposit"] == "12000.00"
    assert found["fuel_policy"] == "full_to_full"

    # 3. Approve vehicle so it becomes publicly discoverable
    veh_obj = db.get(RentalVehicle, new_id)
    veh_obj.approval_status = "approved"
    db.commit()

    # 4. Public detail endpoint uses structured attributes
    client_public = make_test_client(db)
    resp_detail = client_public.get(f"/api/v1/rental-vehicles/{new_id}")
    assert resp_detail.status_code == 200
    h_info = resp_detail.json()["helmets"]
    assert h_info["helmet_provided"] is True
    assert h_info["helmet_count"] == 2
    assert set(h_info["helmet_sizes"]) == {"M", "L"}
    assert h_info["requires_helmet"] is True
    assert "Provided with 2 helmet(s)" in h_info["notes"]


def test_ar03_unstructured_fallback_when_helmet_columns_null(isolated_db_session, seed_phase6b_data):
    """Vehicle with NULL helmet columns falls back gracefully to description heuristic or unprovided."""
    db = isolated_db_session
    data = seed_phase6b_data
    bike = data["base_bike"]  # created with NULL helmet_count and NULL helmet_sizes

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp.status_code == 200
    res = resp.json()["helmets"]
    assert res["requires_helmet"] is True
    assert res["helmet_provided"] is False
    assert res["helmet_count"] is None
    assert "No helmet availability data recorded" in res["notes"]


# ==============================================================================
# 3. AR-04: VEHICLE-SPECIFIC SECURITY DEPOSIT PREFERENCE TESTS
# ==============================================================================

def test_ar04_vehicle_specific_security_deposit_in_quote_and_details(isolated_db_session, seed_phase6b_data):
    """Vehicle-specific security_deposit is preferred over category default in quote calculation and vehicle detail."""
    db = isolated_db_session
    data = seed_phase6b_data
    bike = data["base_bike"]

    # Explicit vehicle-specific deposit: 18,500 LKR (category default for bike is 10,000 LKR)
    bike.security_deposit = Decimal("18500.00")
    db.commit()

    client = make_test_client(db)

    # 1. Vehicle Detail returns vehicle-specific deposit
    resp_detail = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["security_deposit"] == "18500.00"

    # 2. Quote Calculation uses 18,500 LKR
    now = datetime.now(timezone.utc)
    resp_quote = client.post(
        f"/api/v1/rental-vehicles/{bike.id}/calculate-quote",
        json={
            "pickup_at": (now + timedelta(days=2)).isoformat(),
            "return_at": (now + timedelta(days=4)).isoformat(),
        },
    )
    assert resp_quote.status_code == 200
    assert resp_quote.json()["security_deposit"] == "18500.00"


def test_ar04_vehicle_specific_deposit_offline_collection_at_pickup(isolated_db_session, seed_phase6b_data):
    """Provider collecting deposit without payload amount automatically picks up vehicle.security_deposit."""
    db = isolated_db_session
    data = seed_phase6b_data
    traveler = data["traveler"]
    provider = data["provider"]
    provider_user = data["provider_user"]
    bike = data["base_bike"]
    loc = data["loc"]

    # Configure vehicle-specific deposit: 14,000 LKR
    bike.security_deposit = Decimal("14000.00")
    db.commit()

    # Create active booking
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_location_id=loc.id,
        return_location_id=loc.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("4000.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    b_ref = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=booking.id,
        booking_reference=f"BK-TEST-{booking.id}",
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=booking.total_price,
    )
    db.add(b_ref)
    db.commit()

    client_provider = make_test_client(db, current_user=provider_user)

    # Provider collects deposit without payload.amount -> Should pick up 14,000.00 LKR
    resp_col = client_provider.post(
        f"/api/v1/provider/rentals/bookings/{booking.id}/deposit/collect",
        json={
            "notes": "Collected cash deposit at Galle hub",
        },
    )
    assert resp_col.status_code == 201
    res = resp_col.json()
    assert Decimal(str(res["deposit_amount"])) == Decimal("14000.00")
    assert res["deposit_status"] == "held"


# ==============================================================================
# 4. AR-05: STRUCTURED FUEL POLICY TESTS
# ==============================================================================

def test_ar05_structured_fuel_policy_configuration_and_reflection(isolated_db_session, seed_phase6b_data):
    """Vehicle with structured fuel_policy='full_to_full' reflects immediately in details and quote calculation."""
    db = isolated_db_session
    data = seed_phase6b_data
    bike = data["base_bike"]

    bike.fuel_policy = "full_to_full"
    db.commit()

    client = make_test_client(db)

    # 1. Detail endpoint
    resp_detail = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp_detail.status_code == 200
    assert resp_detail.json()["fuel_policy"] == "full_to_full"

    # 2. Quote calculation reflects fuel_policy
    now = datetime.now(timezone.utc)
    resp_quote = client.post(
        f"/api/v1/rental-vehicles/{bike.id}/calculate-quote",
        json={
            "pickup_at": (now + timedelta(days=2)).isoformat(),
            "return_at": (now + timedelta(days=4)).isoformat(),
        },
    )
    assert resp_quote.status_code == 200
    assert resp_quote.json()["fuel_policy"] == "full_to_full"


# ==============================================================================
# 5. AR-06: DIGITAL HANDOVER INSPECTION ACKNOWLEDGEMENT METADATA TESTS
# ==============================================================================

def test_ar06_digital_handover_inspection_acknowledgement_metadata(isolated_db_session, seed_phase6b_data):
    """Traveler acknowledges pickup inspection; records acknowledged_at UTC timestamp and acknowledgement_ip."""
    db = isolated_db_session
    data = seed_phase6b_data
    traveler = data["traveler"]
    admin = data["admin_user"]
    provider = data["provider"]
    bike = data["base_bike"]
    loc = data["loc"]

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_location_id=loc.id,
        return_location_id=loc.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("4000.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    b_ref = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=booking.id,
        booking_reference=f"BK-TEST-{booking.id}",
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=booking.total_price,
    )
    db.add(b_ref)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        inspected_by=admin.id,
        fuel_level_percent=100,
        odometer_reading=5200,
        cleanliness_status="clean",
        notes="All checked and verified spotless.",
    )
    db.add(inspection)
    db.commit()

    client_traveler = make_test_client(db, current_user=traveler)

    # Traveler acknowledges handover inspection
    resp_ack = client_traveler.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json={
            "agreed_to_condition": True,
            "signature_name": "Kamal Traveler",
            "signature_token": "token-hash-xyz-12345",
        },
    )
    assert resp_ack.status_code == 200
    res = resp_ack.json()
    assert res["is_acknowledged"] is True
    assert res["inspection_id"] == inspection.id
    assert res["acknowledged_at"] is not None

    # Verify ORM columns directly
    db.refresh(inspection)
    assert inspection.acknowledged_at is not None
    assert inspection.acknowledgement_ip is not None


# ==============================================================================
# 6. AR-11: FOREIGN TUK-TUK RECOGNITION PERMIT TESTS
# ==============================================================================

def test_ar11_foreign_tuk_tuk_aac_recognition_permit_verification(isolated_db_session, seed_phase6b_data):
    """Foreign driver submits recognition permit; admin verifies; traveler successfully books tuk-tuk."""
    db = isolated_db_session
    data = seed_phase6b_data
    foreign_traveler = data["foreign_traveler"]
    admin = data["admin_user"]
    provider = data["provider"]
    loc = data["loc"]

    # Add tuk-tuk vehicle
    tuktuk = RentalVehicle(
        id=302,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Bajaj RE 205cc",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=4,
        fuel_type="petrol",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    db.add(tuktuk)
    db.flush()

    plan_tuktuk = RentalRatePlan(
        id=402,
        vehicle_id=tuktuk.id,
        plan_name="Daily Tuk-Tuk",
        duration_type="daily",
        rate=Decimal("4500.00"),
        is_active=True,
    )
    db.add(plan_tuktuk)
    db.flush()

    # 1. Foreign traveler submits UK license + AAC Recognition Permit + DOB (age 28)
    client_foreign = make_test_client(db, current_user=foreign_traveler)
    exp = date.today() + timedelta(days=300)
    resp_submit = client_foreign.post(
        "/api/v1/rental-eligibility/license",
        json={
            "driver_license_number": "UK-5582910-A",
            "license_country": "United Kingdom",
            "license_expiry_date": exp.isoformat(),
            "license_front_image": "https://storage.ceylongo.lk/uk_lic.jpg",
            "recognition_permit_number": "AAC-2026-90412",
            "recognition_permit_document_url": "https://storage.ceylongo.lk/permits/aac_90412.pdf",
            "date_of_birth": "1996-03-20",
        },
    )
    assert resp_submit.status_code == 200

    check = db.query(RentalEligibilityCheck).filter(RentalEligibilityCheck.user_id == foreign_traveler.id).first()
    assert check is not None
    assert check.recognition_permit_number == "AAC-2026-90412"

    # 2. Admin approves foreign eligibility with recognition permit
    client_admin = make_test_client(db, current_user=admin)
    resp_review = client_admin.post(
        f"/api/v1/rental-eligibility/admin/{check.id}/review",
        json={
            "decision": "approved",
            "recognition_permit_number": "AAC-2026-90412",
            "verified_date_of_birth": "1996-03-20",
            "notes": "Verified against Automobile Association of Ceylon endorsement",
        },
    )
    assert resp_review.status_code == 200

    # 3. Foreign traveler checks /me -> has_recognition_permit=True, masked permit
    resp_me = client_foreign.get("/api/v1/rental-eligibility/me")
    assert resp_me.status_code == 200
    res_me = resp_me.json()
    assert res_me["has_recognition_permit"] is True
    assert res_me["recognition_permit_number_masked"] == "AA****0412"

    # 4. Foreign traveler creates tuk-tuk booking -> SUCCEEDS (201 Created)
    pickup = datetime.now(timezone.utc) + timedelta(days=5)
    resp_book = client_foreign.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": tuktuk.id,
            "pickup_location_id": loc.id,
            "return_location_id": loc.id,
            "pickup_at": pickup.isoformat(),
            "return_at": (pickup + timedelta(days=2)).isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
            "self_drive_agreement_accepted": True,
            "passenger_count": 3,
        },
    )
    assert resp_book.status_code == 201
    assert resp_book.json()["status"] == "pending"


def test_ar11_foreign_tuk_tuk_blocked_when_recognition_permit_missing(isolated_db_session, seed_phase6b_data):
    """Foreign driver with approved foreign license but WITHOUT recognition permit or IDP is blocked from tuk-tuk."""
    db = isolated_db_session
    data = seed_phase6b_data
    foreign_traveler = data["foreign_traveler"]
    admin = data["admin_user"]
    provider = data["provider"]
    loc = data["loc"]

    # Tuk-tuk
    tuktuk = RentalVehicle(
        id=303,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Bajaj Compact 4S",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=4,
        fuel_type="petrol",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    db.add(tuktuk)
    db.flush()

    plan_tuktuk = RentalRatePlan(
        id=403,
        vehicle_id=tuktuk.id,
        plan_name="Daily Tuk-Tuk",
        duration_type="daily",
        rate=Decimal("4500.00"),
        is_active=True,
    )
    db.add(plan_tuktuk)
    db.flush()

    # Foreign license without IDP or recognition permit
    check = RentalEligibilityCheck(
        user_id=foreign_traveler.id,
        driver_license_number="FRA-882194",
        license_country="France",
        license_expiry_date=date.today() + timedelta(days=400),
        license_front_image="https://storage.ceylongo.lk/fra.jpg",
        date_of_birth=date(1995, 6, 15),
        verification_status="approved",
        reviewed_by=admin.id,
        reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(check)
    db.commit()

    client_foreign = make_test_client(db, current_user=foreign_traveler)
    pickup = datetime.now(timezone.utc) + timedelta(days=5)
    resp_book = client_foreign.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": tuktuk.id,
            "pickup_location_id": loc.id,
            "return_location_id": loc.id,
            "pickup_at": pickup.isoformat(),
            "return_at": (pickup + timedelta(days=2)).isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
            "self_drive_agreement_accepted": True,
            "passenger_count": 3,
        },
    )
    assert resp_book.status_code == 403
    assert "require an International Driving Permit (IDP) and Sri Lankan recognition permit endorsement" in resp_book.json()["detail"]


# ==============================================================================
# 7. AR-12: STRUCTURED VEHICLE INSURANCE METADATA TESTS
# ==============================================================================

def test_ar12_structured_insurance_metadata_in_public_summary(isolated_db_session, seed_phase6b_data):
    """Approved insurance document with structured coverage and deductible is reflected in VehicleInsuranceSummary."""
    db = isolated_db_session
    data = seed_phase6b_data
    bike = data["base_bike"]

    # Approved insurance document with structured metadata
    ins_doc = RentalVehicleDocument(
        vehicle_id=bike.id,
        document_type="insurance_certificate",
        document_number="SLIC-MOT-2026-991204",
        expiry_date=date.today() + timedelta(days=200),
        file_reference="secure/insurance/slic_991204.pdf",
        is_verified=True,
        coverage_amount=Decimal("1500000.00"),
        deductible_amount=Decimal("25000.00"),
        insurance_provider="Sri Lanka Insurance Corporation",
        insurance_policy_number="SLIC-MOT-2026-991204",
    )
    db.add(ins_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp.status_code == 200
    ins_data = resp.json()["insurance"]

    assert ins_data["is_insured"] is True
    assert ins_data["insurance_status"] == "verified"
    assert ins_data["coverage_amount"] == "1500000.00"
    assert ins_data["deductible_amount"] == "25000.00"
    assert ins_data["insurance_provider"] == "Sri Lanka Insurance Corporation"
    # Policy number masked
    assert ins_data["policy_number_masked"] == "SL****1204"


def test_ar12_insurance_without_amounts_returns_none_not_fabricated(isolated_db_session, seed_phase6b_data):
    """Approved insurance without structured coverage returns None rather than inventing fake amounts."""
    db = isolated_db_session
    data = seed_phase6b_data
    bike = data["base_bike"]

    ins_doc = RentalVehicleDocument(
        vehicle_id=bike.id,
        document_type="insurance_certificate",
        document_number="COOP-INS-881234",
        expiry_date=date.today() + timedelta(days=150),
        file_reference="secure/insurance/coop_881234.pdf",
        is_verified=True,
        coverage_amount=None,
        deductible_amount=None,
        insurance_provider="Cooperative Insurance",
    )
    db.add(ins_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp.status_code == 200
    ins_data = resp.json()["insurance"]

    assert ins_data["is_insured"] is True
    assert ins_data["insurance_status"] == "verified"
    assert ins_data["coverage_amount"] is None
    assert ins_data["deductible_amount"] is None
    assert ins_data["insurance_provider"] == "Cooperative Insurance"
