"""
CeylonGo Rental Backend - Phase 5 Batch 2 Test Suite
===================================================
Covers:
- AR-09: Tuk-Tuk Self-Drive Agreement (mandatory agreement acceptance, persistent audit trail in BookingStatusHistory)
- AR-10: Tuk-Tuk Passenger Capacity (dynamic seating_capacity - 1 validation, rejection of invalid counts, category compatibility)
- AR-12: Vehicle Insurance Information (verified status, masked policy numbers, validity dates, quote and search sync, no invented terms)
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
from app.core.dependencies import require_admin
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalEligibilityCheck,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import AdminAuditLog, BookingReference, BookingStatusHistory
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.admin_rentals import router as admin_rentals_router
from app.routers.rental_bookings import router as rental_bookings_router
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


def make_test_client(db_session, current_user=None, admin_user=None):
    """FastAPI test client with dependency overrides."""
    app = FastAPI()
    app.include_router(rental_vehicles_router)
    app.include_router(rental_bookings_router)
    app.include_router(admin_rentals_router)

    app.dependency_overrides[get_db] = lambda: db_session
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    if admin_user:
        app.dependency_overrides[require_admin] = lambda: admin_user
    return TestClient(app)


@pytest.fixture
def seed_batch2_data(isolated_db_session):
    """Seed users, vehicles (3-seater tuktuk, 4-seater tuktuk, bike, bicycle), location, and driver eligibility."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    traveler = User(id=1, full_name="Saman Kumara", email="saman@traveler.lk", password_hash="pw", role="traveler")
    admin_user = User(id=2, full_name="System Admin", email="admin@ceylongo.lk", password_hash="pw", role="admin")
    provider_user = User(id=3, full_name="Rental Host", email="host@rentals.lk", password_hash="pw", role="service_provider")

    db.add_all([traveler, admin_user, provider_user])
    db.flush()

    # 2. Provider Profile
    provider = ProviderProfile(
        id=201,
        user_id=provider_user.id,
        business_name="Colombo Auto Fleet",
        provider_type="rental",
        phone="+94772345678",
        district="Colombo",
        approval_status="approved",
    )
    # Traveler KYC profile for age verification (26 years old)
    traveler_kyc = ProviderProfile(
        id=202,
        user_id=traveler.id,
        business_name="Traveler KYC",
        provider_type="rental",
        phone="+94771112222",
        district="Colombo",
        date_of_birth=date.today() - timedelta(days=365 * 26),
        approval_status="approved",
    )
    db.add_all([provider, traveler_kyc])
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=1,
        provider_id=provider.id,
        location_name="Colombo Fort Terminal",
        district="Colombo",
        city="Colombo 01",
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles
    # Standard 3-seater Tuk-Tuk (seating_capacity = 3 -> driver + 2 passengers)
    tuktuk_3s = RentalVehicle(
        id=1,
        provider_id=provider.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4-Stroke 3-Seater",
        price_per_day=Decimal("4000.00"),
        seating_capacity=3,
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Large 4-seater Tuk-Tuk (seating_capacity = 4 -> driver + 3 passengers)
    tuktuk_4s = RentalVehicle(
        id=2,
        provider_id=provider.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Piaggio Ape City Plus 4-Seater",
        price_per_day=Decimal("4500.00"),
        seating_capacity=4,
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Bike (seating_capacity = 2 -> driver + 1 passenger)
    bike = RentalVehicle(
        id=3,
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Yamaha FZ-S 150cc",
        price_per_day=Decimal("3500.00"),
        seating_capacity=2,
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Bicycle (seating_capacity = 1)
    bicycle = RentalVehicle(
        id=4,
        provider_id=provider.id,
        vehicle_type="bicycle",
        vehicle_name="Giant Escape City Hybrid",
        price_per_day=Decimal("1500.00"),
        seating_capacity=1,
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    db.add_all([tuktuk_3s, tuktuk_4s, bike, bicycle])
    db.flush()

    # 5. Traveler Approved Driving License Eligibility
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="LK-DL-B1-778899",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365 * 2),
        verification_status="approved",
    )
    db.add(eligibility)
    db.commit()

    return {
        "traveler": traveler,
        "admin_user": admin_user,
        "provider_user": provider_user,
        "provider": provider,
        "loc": loc,
        "tuktuk_3s": tuktuk_3s,
        "tuktuk_4s": tuktuk_4s,
        "bike": bike,
        "bicycle": bicycle,
    }


# ==============================================================================
# 1. AR-09: TUK-TUK SELF-DRIVE AGREEMENT TESTS
# ==============================================================================

def test_tuktuk_booking_rejected_when_agreement_omitted_or_false(isolated_db_session, seed_batch2_data):
    """AR-09: Tuk-tuk booking without accepted self-drive agreement is rejected with HTTP 400."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    # 1. Agreement omitted
    payload_omitted = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "passenger_count": 2,
    }
    resp1 = client.post("/api/v1/rental-bookings", json=payload_omitted)
    assert resp1.status_code == 400
    assert "self-drive" in resp1.json()["detail"].lower()
    assert "agreement" in resp1.json()["detail"].lower()

    # 2. Agreement explicitly False
    payload_false = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": False,
        "passenger_count": 2,
    }
    resp2 = client.post("/api/v1/rental-bookings", json=payload_false)
    assert resp2.status_code == 400
    assert "self-drive" in resp2.json()["detail"].lower()
    assert "agreement" in resp2.json()["detail"].lower()


def test_tuktuk_booking_succeeds_when_agreement_accepted_and_passenger_count_valid(isolated_db_session, seed_batch2_data):
    """AR-09: Tuk-tuk booking succeeds when agreement is accepted and passenger count is valid."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    payload = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 2,
    }
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["self_drive_agreement_accepted"] is True
    assert data["passenger_count"] == 2
    assert data["status"] == "pending"


def test_agreement_acceptance_and_passenger_count_persisted_in_booking_status_history(isolated_db_session, seed_batch2_data):
    """AR-09 & AR-10: Persistent agreement acceptance and passenger count stored in BookingStatusHistory."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    payload = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=5)).isoformat(),
        "return_at": (now + timedelta(days=7)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 1,
    }
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 201
    booking_id = resp.json()["id"]

    # Verify BookingReference exists
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking_id,
        )
        .first()
    )
    assert booking_ref is not None

    # Verify BookingStatusHistory contains auditable note
    history = (
        db.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == booking_ref.id)
        .order_by(BookingStatusHistory.created_at.asc())
        .first()
    )
    assert history is not None
    assert history.from_status is None
    assert history.to_status == "pending"
    assert history.changed_by == traveler.id
    assert "Self-drive agreement accepted: True" in history.notes
    assert "Passenger count: 1" in history.notes


def test_get_rental_booking_retrieves_persisted_agreement_and_passengers(isolated_db_session, seed_batch2_data):
    """AR-09 & AR-10: GET /api/v1/rental-bookings/{id} reconstructs agreement and passenger attributes."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    payload = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=10)).isoformat(),
        "return_at": (now + timedelta(days=12)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 2,
    }
    create_resp = client.post("/api/v1/rental-bookings", json=payload)
    assert create_resp.status_code == 201
    booking_id = create_resp.json()["id"]

    # Retrieve booking details
    get_resp = client.get(f"/api/v1/rental-bookings/{booking_id}")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == booking_id
    assert data["self_drive_agreement_accepted"] is True
    assert data["passenger_count"] == 2


# ==============================================================================
# 2. AR-10: TUK-TUK PASSENGER CAPACITY TESTS
# ==============================================================================

def test_tuktuk_booking_rejected_when_passenger_count_missing(isolated_db_session, seed_batch2_data):
    """AR-10: Tuk-tuk booking without passenger_count is rejected with HTTP 400."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    payload = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": None,
    }
    resp = client.post("/api/v1/rental-bookings", json=payload)
    assert resp.status_code == 400
    assert "passenger count" in resp.json()["detail"].lower()


def test_tuktuk_booking_rejected_when_passenger_count_zero_or_negative(isolated_db_session, seed_batch2_data):
    """AR-10: Pydantic ge=1 constraint rejects 0 or negative passenger count with HTTP 422."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    payload_zero = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 0,
    }
    resp = client.post("/api/v1/rental-bookings", json=payload_zero)
    assert resp.status_code == 422

    payload_neg = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": -2,
    }
    resp_neg = client.post("/api/v1/rental-bookings", json=payload_neg)
    assert resp_neg.status_code == 422


def test_tuktuk_passenger_count_exceeds_3seater_capacity_rejected(isolated_db_session, seed_batch2_data):
    """AR-10: 3-seater tuk-tuk (capacity 3, driver takes 1) allows max 2 passengers. 3 passengers rejected."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    # 3 passengers in a 3-seater tuk-tuk (Total 3 passengers + 1 driver = 4 > 3)
    payload_exceed = {
        "vehicle_id": tuktuk.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 3,
    }
    resp = client.post("/api/v1/rental-bookings", json=payload_exceed)
    assert resp.status_code == 400
    assert "exceeds vehicle passenger capacity of 2" in resp.json()["detail"].lower()


def test_tuktuk_dynamic_seating_capacity_4seater(isolated_db_session, seed_batch2_data):
    """AR-10: Dynamic per-vehicle seating capacity: 4-seater tuk-tuk allows 3 passengers, rejects 4."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    tuktuk_4s = seed_batch2_data["tuktuk_4s"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    # 1. 3 passengers on 4-seater -> SUCCEEDS (3 + 1 driver = 4 <= 4)
    payload_ok = {
        "vehicle_id": tuktuk_4s.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 3,
    }
    resp_ok = client.post("/api/v1/rental-bookings", json=payload_ok)
    assert resp_ok.status_code == 201
    assert resp_ok.json()["passenger_count"] == 3

    # 2. 4 passengers on 4-seater -> REJECTED (4 + 1 driver = 5 > 4)
    payload_exceed = {
        "vehicle_id": tuktuk_4s.id,
        "pickup_at": (now + timedelta(days=6)).isoformat(),
        "return_at": (now + timedelta(days=8)).isoformat(),
        "self_drive_agreement_accepted": True,
        "passenger_count": 4,
    }
    resp_exceed = client.post("/api/v1/rental-bookings", json=payload_exceed)
    assert resp_exceed.status_code == 400
    assert "exceeds vehicle passenger capacity of 3" in resp_exceed.json()["detail"].lower()


def test_bike_and_bicycle_compatibility_without_agreement(isolated_db_session, seed_batch2_data):
    """AR-09 & AR-10: Non-tuk-tuk categories (bike, bicycle) do not require agreement or passenger count."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    bike = seed_batch2_data["bike"]
    bicycle = seed_batch2_data["bicycle"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    # 1. Bike booking without self_drive_agreement_accepted succeeds
    payload_bike = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
    }
    resp_bike = client.post("/api/v1/rental-bookings", json=payload_bike)
    assert resp_bike.status_code == 201
    assert resp_bike.json()["self_drive_agreement_accepted"] is False

    # 2. Bicycle booking without agreement or passenger count succeeds
    payload_bicycle = {
        "vehicle_id": bicycle.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
    }
    resp_bicycle = client.post("/api/v1/rental-bookings", json=payload_bicycle)
    assert resp_bicycle.status_code == 201


def test_bike_passenger_capacity_validation(isolated_db_session, seed_batch2_data):
    """AR-10: Bike with seating_capacity=2 allows max 1 passenger. 2 passengers rejected."""
    db = isolated_db_session
    traveler = seed_batch2_data["traveler"]
    bike = seed_batch2_data["bike"]

    now = datetime.now(timezone.utc)
    client = make_test_client(db, current_user=traveler)

    # 1 passenger on 2-seater bike succeeds
    payload_ok = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=5)).isoformat(),
        "return_at": (now + timedelta(days=7)).isoformat(),
        "passenger_count": 1,
    }
    resp_ok = client.post("/api/v1/rental-bookings", json=payload_ok)
    assert resp_ok.status_code == 201
    assert resp_ok.json()["passenger_count"] == 1

    # 2 passengers on 2-seater bike rejected (2 + driver = 3 > 2)
    payload_exceed = {
        "vehicle_id": bike.id,
        "pickup_at": (now + timedelta(days=8)).isoformat(),
        "return_at": (now + timedelta(days=10)).isoformat(),
        "passenger_count": 2,
    }
    resp_exceed = client.post("/api/v1/rental-bookings", json=payload_exceed)
    assert resp_exceed.status_code == 400
    assert "exceeds vehicle passenger capacity of 1" in resp_exceed.json()["detail"].lower()


# ==============================================================================
# 3. AR-12: VEHICLE INSURANCE INFORMATION TESTS
# ==============================================================================

def test_verified_vehicle_insurance_information(isolated_db_session, seed_batch2_data):
    """AR-12: Vehicle with verified insurance certificate displays masked policy and verified status."""
    db = isolated_db_session
    tuktuk = seed_batch2_data["tuktuk_3s"]

    # Add verified insurance document
    future_expiry = date.today() + timedelta(days=180)
    insurance_doc = RentalVehicleDocument(
        vehicle_id=tuktuk.id,
        document_type="insurance_certificate",
        document_number="POL-LK-2026-987654",
        expiry_date=future_expiry,
        file_reference="secure/rentals/docs/v1_insurance_secret_path.pdf",
        is_verified=True,
    )
    db.add(insurance_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp.status_code == 200
    data = resp.json()

    assert "insurance" in data
    insurance = data["insurance"]
    assert insurance["is_insured"] is True
    assert insurance["insurance_status"] == "verified"
    assert insurance["policy_number_masked"] == "PO****7654"
    assert insurance["expiry_date"] == future_expiry.isoformat()

    # Security check: never leak raw file_reference path
    assert "file_reference" not in str(data)
    assert "secure/rentals/docs" not in resp.text


def test_unverified_vehicle_insurance_information(isolated_db_session, seed_batch2_data):
    """AR-12: Unverified insurance document returns insurance_status='unverified' and is_insured=False."""
    db = isolated_db_session
    bike = seed_batch2_data["bike"]

    future_expiry = date.today() + timedelta(days=90)
    insurance_doc = RentalVehicleDocument(
        vehicle_id=bike.id,
        document_type="insurance_certificate",
        document_number="BIKE-INS-112233",
        expiry_date=future_expiry,
        file_reference="secure/rentals/docs/bike_unverified.pdf",
        is_verified=False,
    )
    db.add(insurance_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp.status_code == 200
    insurance = resp.json()["insurance"]
    assert insurance["is_insured"] is False
    assert insurance["insurance_status"] == "unverified"
    assert insurance["policy_number_masked"] == "BI****2233"


def test_expired_vehicle_insurance_information(isolated_db_session, seed_batch2_data):
    """AR-12: Expired insurance document returns insurance_status='expired' and is_insured=False."""
    db = isolated_db_session
    tuktuk = seed_batch2_data["tuktuk_4s"]

    past_expiry = date.today() - timedelta(days=10)
    insurance_doc = RentalVehicleDocument(
        vehicle_id=tuktuk.id,
        document_type="insurance_certificate",
        document_number="OLD-INS-998877",
        expiry_date=past_expiry,
        file_reference="secure/rentals/docs/tuktuk_expired.pdf",
        is_verified=True,  # Even if marked verified historically, expired date invalidates active insurance
    )
    db.add(insurance_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp.status_code == 200
    insurance = resp.json()["insurance"]
    assert insurance["is_insured"] is False
    assert insurance["insurance_status"] == "expired"


def test_not_provided_vehicle_insurance_information(isolated_db_session, seed_batch2_data):
    """AR-12: Vehicle with no insurance certificate returns insurance_status='not_provided'."""
    db = isolated_db_session
    bicycle = seed_batch2_data["bicycle"]

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bicycle.id}")
    assert resp.status_code == 200
    insurance = resp.json()["insurance"]
    assert insurance["is_insured"] is False
    assert insurance["insurance_status"] == "not_provided"
    assert insurance["policy_number_masked"] is None
    assert insurance["expiry_date"] is None


def test_admin_verification_synchronizes_traveler_insurance_and_quote(isolated_db_session, seed_batch2_data):
    """AR-12: Admin verification updates traveler insurance status, list items, and quote calculator."""
    db = isolated_db_session
    admin = seed_batch2_data["admin_user"]
    tuktuk = seed_batch2_data["tuktuk_3s"]

    # 1. Initially unverified document
    future_expiry = date.today() + timedelta(days=200)
    insurance_doc = RentalVehicleDocument(
        vehicle_id=tuktuk.id,
        document_type="insurance_certificate",
        document_number="CEY-INS-445566",
        expiry_date=future_expiry,
        file_reference="secure/rentals/docs/initial_doc.pdf",
        is_verified=False,
    )
    db.add(insurance_doc)
    db.commit()

    # Public client views unverified
    public_client = make_test_client(db)
    resp_init = public_client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp_init.status_code == 200
    assert resp_init.json()["insurance"]["is_insured"] is False
    assert resp_init.json()["insurance"]["insurance_status"] == "unverified"

    # Quote also shows unverified
    now = datetime.now(timezone.utc)
    quote_payload = {
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=5)).isoformat(),
    }
    resp_quote_init = public_client.post(f"/api/v1/rental-vehicles/{tuktuk.id}/calculate-quote", json=quote_payload)
    assert resp_quote_init.status_code == 200
    assert resp_quote_init.json()["is_insured"] is False
    assert resp_quote_init.json()["insurance_status"] == "unverified"

    # 2. Admin verifies document
    admin_client = make_test_client(db, admin_user=admin)
    verify_resp = admin_client.post(
        f"/api/v1/admin/rentals/documents/{insurance_doc.id}/verify",
        json={"decision": "approved", "notes": "Valid insurance certificate."},
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["is_verified"] is True

    # Check AdminAuditLog
    audit_entry = (
        db.query(AdminAuditLog)
        .filter(
            AdminAuditLog.entity_name == "rental_vehicle_documents",
            AdminAuditLog.entity_id == insurance_doc.id,
        )
        .first()
    )
    assert audit_entry is not None
    assert audit_entry.admin_user_id == admin.id

    # 3. Public vehicle details immediately reflect verified status
    resp_verified = public_client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp_verified.status_code == 200
    assert resp_verified.json()["insurance"]["is_insured"] is True
    assert resp_verified.json()["insurance"]["insurance_status"] == "verified"

    # 4. Quote calculator reflects verified insurance
    resp_quote_verified = public_client.post(f"/api/v1/rental-vehicles/{tuktuk.id}/calculate-quote", json=quote_payload)
    assert resp_quote_verified.status_code == 200
    assert resp_quote_verified.json()["is_insured"] is True
    assert resp_quote_verified.json()["insurance_status"] == "verified"

    # 5. List endpoint reflects verified insurance
    resp_list = public_client.get("/api/v1/rental-vehicles")
    assert resp_list.status_code == 200
    list_items = resp_list.json()["items"]
    tuktuk_item = next((item for item in list_items if item["id"] == tuktuk.id), None)
    assert tuktuk_item is not None
    assert tuktuk_item["is_insured"] is True
    assert tuktuk_item["insurance_status"] == "verified"


def test_insurance_schema_fidelity_guard(isolated_db_session, seed_batch2_data):
    """AR-12: Schema fidelity guard - verify no invented coverage amounts, deductibles, or policy clauses exist."""
    db = isolated_db_session
    tuktuk = seed_batch2_data["tuktuk_3s"]

    insurance_doc = RentalVehicleDocument(
        vehicle_id=tuktuk.id,
        document_type="insurance_certificate",
        document_number="POL-VALID-123456",
        expiry_date=date.today() + timedelta(days=100),
        file_reference="secure/path.pdf",
        is_verified=True,
    )
    db.add(insurance_doc)
    db.commit()

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp.status_code == 200
    insurance_data = resp.json()["insurance"]

    # Phase 6B: VehicleInsuranceSummary now includes structured coverage_amount, deductible_amount, insurance_provider
    # Replaced Phase 5 Batch 2 assertion where those fields were absent from schema
    expected_fields = {
        "is_insured",
        "insurance_status",
        "policy_number_masked",
        "expiry_date",
        "coverage_amount",
        "deductible_amount",
        "insurance_provider",
    }
    assert set(insurance_data.keys()) == expected_fields

    # Assert no invented coverage amount or deductible was fabricated when not stored
    assert insurance_data["coverage_amount"] is None
    assert insurance_data["deductible_amount"] is None
    assert insurance_data["insurance_provider"] is None
    assert "deductible" not in insurance_data
    assert "comprehensive" not in insurance_data
