"""
CeylonGo Rental Backend - Phase 5 Batch 3 Test Suite
===================================================
Covers:
- AR-03: Helmet Availability (exposure in vehicle details, missing data handling, handover recording, two-wheeler applicability)
- AR-05: Fuel Policy (disclosure of same_to_same, full_to_full, not_applicable, return fuel deficit calculation, zero invented charges)
- AR-06: Vehicle Condition Checklist (traveler checklist & photo retrieval, digital handover acknowledgement, authorization & duplicate protection)
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
    RentalConditionEvidence,
    RentalEligibilityCheck,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import BookingReference, BookingStatusHistory
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.rental_bookings import router as rental_bookings_router
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
    app.include_router(rental_providers_router)

    app.dependency_overrides[get_db] = lambda: db_session
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_batch3_data(isolated_db_session):
    """Seed users, vehicles (bike with helmets, bike without helmets, bicycle, tuk-tuk), location, and driver eligibility."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    traveler = User(id=1, full_name="Kasun Silva", email="kasun@traveler.lk", password_hash="pw", role="traveler")
    traveler_other = User(id=2, full_name="Sneaky Intruder", email="sneaky@traveler.lk", password_hash="pw", role="traveler")
    provider_user = User(id=3, full_name="Fleet Host", email="host@rentals.lk", password_hash="pw", role="provider")

    db.add_all([traveler, traveler_other, provider_user])
    db.flush()

    # 2. Provider Profile
    provider = ProviderProfile(
        id=301,
        user_id=provider_user.id,
        business_name="Lanka Moto Rentals",
        provider_type="rental",
        phone="+94773456789",
        district="Galle",
        approval_status="approved",
    )
    # Traveler KYC profile for age verification (24 years old)
    traveler_kyc = ProviderProfile(
        id=302,
        user_id=traveler.id,
        business_name="Traveler KYC",
        provider_type="rental",
        phone="+94779876543",
        district="Galle",
        date_of_birth=date.today() - timedelta(days=365 * 24),
        approval_status="approved",
    )
    db.add_all([provider, traveler_kyc])
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=1,
        provider_id=provider.id,
        location_name="Galle Fort Hub",
        district="Galle",
        city="Galle",
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles
    # Motorbike with disclosed helmets and full_to_full policy in description
    bike_with_helmets = RentalVehicle(
        id=1,
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Yamaha RayZR 125cc",
        description="Comfortable scooter. Full-to-full fuel policy. Includes 2 helmets (sizes M and L) in clean condition.",
        price_per_day=Decimal("3200.00"),
        seating_capacity=2,
        fuel_type="petrol",
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Motorbike without helmet data (standard same_to_same policy)
    bike_no_helmets = RentalVehicle(
        id=2,
        provider_id=provider.id,
        vehicle_type="bike",
        vehicle_name="Honda Dio 110cc",
        description="Standard commuter scooter. Great mileage.",
        price_per_day=Decimal("2800.00"),
        seating_capacity=2,
        fuel_type="petrol",
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Bicycle (fuel policy not_applicable)
    bicycle = RentalVehicle(
        id=3,
        provider_id=provider.id,
        vehicle_type="bicycle",
        vehicle_name="Scott Sub Cross Tourer",
        description="Comfortable touring bicycle with helmet included.",
        price_per_day=Decimal("1200.00"),
        seating_capacity=1,
        fuel_type="petrol",  # ignored by vehicle_type logic
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    # Tuk-Tuk (helmets not required, same_to_same fuel policy)
    tuktuk = RentalVehicle(
        id=4,
        provider_id=provider.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj Maxima 4-Stroke",
        description="Reliable 3-wheeler for southern coastal trips.",
        price_per_day=Decimal("4200.00"),
        seating_capacity=3,
        fuel_type="petrol",
        status="available",
        approval_status="approved",
        location_id=loc.id,
    )
    db.add_all([bike_with_helmets, bike_no_helmets, bicycle, tuktuk])
    db.flush()

    # Rate plan for bike
    rate_plan = RentalRatePlan(
        id=1,
        vehicle_id=bike_with_helmets.id,
        plan_name="Daily Standard",
        duration_type="daily",
        rate=Decimal("3200.00"),
        included_km_per_day=100,
        extra_km_charge=Decimal("40.00"),
        is_active=True,
    )
    db.add(rate_plan)

    # 5. Traveler Approved Driving License Eligibility
    eligibility = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="LK-DL-B-112233",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365 * 3),
        verification_status="approved",
    )
    db.add(eligibility)
    db.commit()

    return {
        "traveler": traveler,
        "traveler_other": traveler_other,
        "provider_user": provider_user,
        "provider": provider,
        "loc": loc,
        "bike_with_helmets": bike_with_helmets,
        "bike_no_helmets": bike_no_helmets,
        "bicycle": bicycle,
        "tuktuk": tuktuk,
    }


# ==============================================================================
# 1. AR-03: HELMET AVAILABILITY TESTS
# ==============================================================================

def test_helmet_availability_disclosed_in_vehicle_details(isolated_db_session, seed_batch3_data):
    """AR-03: Expose available helmet quantities, sizes and condition for two-wheelers in vehicle details."""
    db = isolated_db_session
    bike = seed_batch3_data["bike_with_helmets"]

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike.id}")
    assert resp.status_code == 200
    data = resp.json()

    assert "helmets" in data
    helmets = data["helmets"]
    assert helmets["helmet_provided"] is True
    assert helmets["helmet_count"] == 2
    assert "M" in helmets["helmet_sizes"]
    assert "L" in helmets["helmet_sizes"]
    assert helmets["requires_helmet"] is True


def test_helmet_missing_data_handled_honestly_without_inventing(isolated_db_session, seed_batch3_data):
    """AR-03: If no helmet data is stored, system reports unprovided without inventing fake data."""
    db = isolated_db_session
    bike_empty = seed_batch3_data["bike_no_helmets"]

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{bike_empty.id}")
    assert resp.status_code == 200
    helmets = resp.json()["helmets"]

    assert helmets["helmet_provided"] is False
    assert helmets["helmet_count"] is None
    assert helmets["helmet_sizes"] is None
    assert helmets["requires_helmet"] is True
    assert "no helmet availability data recorded" in helmets["notes"].lower()


def test_helmet_not_required_for_tuktuk(isolated_db_session, seed_batch3_data):
    """AR-03: Tuk-tuks correctly report that helmets are not required."""
    db = isolated_db_session
    tuktuk = seed_batch3_data["tuktuk"]

    client = make_test_client(db)
    resp = client.get(f"/api/v1/rental-vehicles/{tuktuk.id}")
    assert resp.status_code == 200
    helmets = resp.json()["helmets"]

    assert helmets["helmet_provided"] is False
    assert helmets["requires_helmet"] is False
    assert "not required for tuk-tuks" in helmets["notes"].lower()


def test_provider_records_helmet_availability_at_handover(isolated_db_session, seed_batch3_data):
    """AR-03: Providers can record actual helmet count, sizes, and condition during handover inspection."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider = seed_batch3_data["provider"]
    provider_user = seed_batch3_data["provider_user"]
    bike = seed_batch3_data["bike_no_helmets"]

    now = datetime.now(timezone.utc)
    # Create confirmed booking
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now + timedelta(days=1),
        return_at=now + timedelta(days=3),
        total_price=Decimal("5600.00"),
        status="confirmed",
        payment_status="paid",
    )
    db.add(booking)
    db.commit()

    client = make_test_client(db, current_user=provider_user)
    handover_payload = {
        "odometer_reading": 12500,
        "fuel_level_percent": 85,
        "cleanliness_status": "clean",
        "notes": "Vehicle is in pristine condition.",
        "helmet_count": 2,
        "helmet_sizes": ["M", "L"],
        "helmet_condition": "Sanitized and spotless",
        "evidence": [
            {
                "evidence_type": "photo",
                "file_url": "https://storage.ceylongo.lk/inspections/helmets_verified.jpg",
                "area_of_vehicle": "helmet",
                "description": "2 sanitized helmets provided",
            }
        ],
    }

    resp = client.post(f"/api/v1/provider/rentals/bookings/{booking.id}/handover", json=handover_payload)
    assert resp.status_code == 200

    # Verify inspection stored the helmet note
    inspection = (
        db.query(RentalInspection)
        .filter(RentalInspection.booking_id == booking.id)
        .first()
    )
    assert inspection is not None
    assert "[Helmets: count=2, sizes=M,L, condition=Sanitized and spotless]" in inspection.notes


# ==============================================================================
# 2. AR-05: FUEL POLICY TESTS
# ==============================================================================

def test_fuel_policy_disclosed_before_booking_and_in_quote(isolated_db_session, seed_batch3_data):
    """AR-05: Disclose applicable fuel policy (same_to_same, full_to_full, not_applicable)."""
    db = isolated_db_session
    bike_full = seed_batch3_data["bike_with_helmets"]
    bike_same = seed_batch3_data["bike_no_helmets"]
    bicycle = seed_batch3_data["bicycle"]

    client = make_test_client(db)

    # 1. Full-to-full bike
    resp_full = client.get(f"/api/v1/rental-vehicles/{bike_full.id}")
    assert resp_full.status_code == 200
    assert resp_full.json()["fuel_policy"] == "full_to_full"

    # 2. Same-to-same bike
    resp_same = client.get(f"/api/v1/rental-vehicles/{bike_same.id}")
    assert resp_same.status_code == 200
    assert resp_same.json()["fuel_policy"] == "same_to_same"

    # 3. Not-applicable bicycle
    resp_cycle = client.get(f"/api/v1/rental-vehicles/{bicycle.id}")
    assert resp_cycle.status_code == 200
    assert resp_cycle.json()["fuel_policy"] == "not_applicable"

    # 4. Quote calculation discloses fuel_policy
    now = datetime.now(timezone.utc)
    quote_resp = client.post(
        f"/api/v1/rental-vehicles/{bike_full.id}/calculate-quote",
        json={
            "pickup_at": (now + timedelta(days=2)).isoformat(),
            "return_at": (now + timedelta(days=4)).isoformat(),
        },
    )
    assert quote_resp.status_code == 200
    assert quote_resp.json()["fuel_policy"] == "full_to_full"


def test_fuel_difference_calculation_on_return_same_to_same(isolated_db_session, seed_batch3_data):
    """AR-05: Calculate fuel difference and deficit according to same_to_same disclosed policy."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider = seed_batch3_data["provider"]
    provider_user = seed_batch3_data["provider_user"]
    bike_same = seed_batch3_data["bike_no_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike_same.id,
        user_id=traveler.id,
        pickup_at=now - timedelta(days=2),
        return_at=now,
        total_price=Decimal("5600.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    # Handover at 80% fuel
    pickup_insp = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=5000,
        fuel_level_percent=80,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
    )
    db.add(pickup_insp)
    db.commit()

    # Returned at 60% fuel (20% deficit under same_to_same)
    client = make_test_client(db, current_user=provider_user)
    return_payload = {
        "odometer_reading": 5150,
        "fuel_level_percent": 60,
        "cleanliness_status": "clean",
        "notes": "Vehicle returned with less fuel.",
        "evidence": [],
    }
    resp = client.post(f"/api/v1/provider/rentals/bookings/{booking.id}/return", json=return_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["fuel_policy"] == "same_to_same"
    assert data["pickup_fuel_percent"] == 80
    assert data["return_fuel_percent"] == 60
    assert data["fuel_difference_percent"] == -20
    assert data["fuel_deficit_percent"] == 20


def test_fuel_difference_calculation_on_return_not_applicable(isolated_db_session, seed_batch3_data):
    """AR-05: Bicycles and electric vehicles have fuel_policy='not_applicable' and 0 deficit."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider_user = seed_batch3_data["provider_user"]
    bicycle = seed_batch3_data["bicycle"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bicycle.id,
        user_id=traveler.id,
        pickup_at=now - timedelta(days=1),
        return_at=now,
        total_price=Decimal("1200.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    pickup_insp = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=0,
        fuel_level_percent=0,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
    )
    db.add(pickup_insp)
    db.commit()

    client = make_test_client(db, current_user=provider_user)
    return_payload = {
        "odometer_reading": 25,
        "fuel_level_percent": 0,
        "cleanliness_status": "clean",
        "notes": "Bicycle returned safely.",
        "evidence": [],
    }
    resp = client.post(f"/api/v1/provider/rentals/bookings/{booking.id}/return", json=return_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["fuel_policy"] == "not_applicable"
    assert data["fuel_difference_percent"] == 0
    assert data["fuel_deficit_percent"] == 0


def test_zero_invented_refueling_penalties(isolated_db_session, seed_batch3_data):
    """AR-05: System does not invent unauthorized refueling surcharge amounts or fuel market prices."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider_user = seed_batch3_data["provider_user"]
    bike = seed_batch3_data["bike_no_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now - timedelta(days=1),
        return_at=now,
        total_price=Decimal("2800.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    pickup_insp = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=1000,
        fuel_level_percent=100,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
    )
    db.add(pickup_insp)
    db.commit()

    client = make_test_client(db, current_user=provider_user)
    return_payload = {
        "odometer_reading": 1050,
        "fuel_level_percent": 50,
        "cleanliness_status": "clean",
        "notes": "Half tank missing.",
        "evidence": [],
    }
    resp = client.post(f"/api/v1/provider/rentals/bookings/{booking.id}/return", json=return_payload)
    assert resp.status_code == 200
    data = resp.json()

    # Deficit is reported accurately as 50%
    assert data["fuel_deficit_percent"] == 50
    # No invented financial penalty fields exist in response
    assert "refueling_charge" not in data
    assert "fuel_penalty_fee" not in data


# ==============================================================================
# 3. AR-06: VEHICLE CONDITION CHECKLIST & ACKNOWLEDGEMENT TESTS
# ==============================================================================

def test_traveler_can_view_own_booking_inspection_checklist(isolated_db_session, seed_batch3_data):
    """AR-06: Traveler can view digital handover inspection checklists and condition photos."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider_user = seed_batch3_data["provider_user"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    # Handover inspection with photo evidence
    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=8200,
        fuel_level_percent=95,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
        notes="Minor scratch on left mirror. [Helmets: count=2, sizes=M,L, condition=Good]",
    )
    db.add(inspection)
    db.flush()

    evidence1 = RentalConditionEvidence(
        inspection_id=inspection.id,
        evidence_type="photo",
        file_url="https://storage.ceylongo.lk/inspections/mirror_scratch.jpg",
        area_of_vehicle="left_mirror",
        description="Minor scratch on exterior rim",
    )
    evidence2 = RentalConditionEvidence(
        inspection_id=inspection.id,
        evidence_type="photo",
        file_url="https://storage.ceylongo.lk/inspections/odometer_pickup.jpg",
        area_of_vehicle="dashboard",
        description="Odometer and fuel gauge reading at handover",
    )
    db.add_all([evidence1, evidence2])
    db.commit()

    client = make_test_client(db, current_user=traveler)
    resp = client.get(f"/api/v1/rental-bookings/{booking.id}/inspections")
    assert resp.status_code == 200
    checklists = resp.json()

    assert len(checklists) == 1
    chk = checklists[0]
    assert chk["id"] == inspection.id
    assert chk["inspection_type"] == "pickup"
    assert chk["odometer_reading"] == 8200
    assert chk["fuel_level_percent"] == 95
    assert chk["is_acknowledged"] is False
    assert len(chk["evidence"]) == 2
    assert chk["evidence"][0]["area_of_vehicle"] == "left_mirror"
    assert chk["helmets"]["helmet_provided"] is True
    assert chk["helmets"]["helmet_count"] == 2


def test_inspection_access_control_unauthorized_traveler_forbidden(isolated_db_session, seed_batch3_data):
    """AR-06: Unauthorized traveler cannot view another traveler's inspection checklists (403 Forbidden)."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    traveler_other = seed_batch3_data["traveler_other"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=1000,
        fuel_level_percent=100,
        cleanliness_status="clean",
    )
    db.add(inspection)
    db.commit()

    # traveler_other attempts to view traveler's checklist
    client_intruder = make_test_client(db, current_user=traveler_other)
    resp = client_intruder.get(f"/api/v1/rental-bookings/{booking.id}/inspections")
    assert resp.status_code == 403
    assert "access denied" in resp.json()["detail"].lower()


def test_traveler_digital_handover_acknowledgement_success(isolated_db_session, seed_batch3_data):
    """AR-06: Traveler successfully acknowledges vehicle condition at handover with cryptographic signature."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    provider = seed_batch3_data["provider"]
    provider_user = seed_batch3_data["provider_user"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    # Add shared platform BookingReference
    booking_ref = BookingReference(
        booking_reference="RB-ACK-001",
        module_type="rental_vehicle",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=booking.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="paid",
    )
    db.add(booking_ref)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=8500,
        fuel_level_percent=90,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
        notes="All checked.",
    )
    db.add(inspection)
    db.commit()

    client = make_test_client(db, current_user=traveler)
    ack_payload = {
        "agreed_to_condition": True,
        "signature_name": "Kasun Silva",
        "signature_token": "WEB-SIGN-2026-X99",
    }
    resp = client.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json=ack_payload,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["is_acknowledged"] is True
    assert "SIG:SHA256:" in data["customer_signature"]
    assert "BY:Kasun Silva" in data["customer_signature"]

    # Verify persistent storage in inspection.customer_signature
    db.refresh(inspection)
    assert inspection.customer_signature == data["customer_signature"]

    # Verify persistent storage in BookingStatusHistory
    history = (
        db.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == booking_ref.id)
        .order_by(BookingStatusHistory.id.desc())
        .first()
    )
    assert history is not None
    assert "Traveler digital handover acknowledgement" in history.notes
    assert data["customer_signature"] in history.notes


def test_duplicate_acknowledgement_prevented_with_409(isolated_db_session, seed_batch3_data):
    """AR-06: Duplicate acknowledgement attempt on an already signed inspection returns 409 Conflict."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    # Pre-acknowledged inspection
    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=8500,
        fuel_level_percent=90,
        cleanliness_status="clean",
        customer_signature="SIG:SHA256:11223344|BY:Kasun Silva|AT:2026-11-19T10:00:00",
    )
    db.add(inspection)
    db.commit()

    client = make_test_client(db, current_user=traveler)
    ack_payload = {
        "agreed_to_condition": True,
        "signature_name": "Kasun Silva",
    }
    resp = client.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json=ack_payload,
    )
    assert resp.status_code == 409
    assert "already been acknowledged" in resp.json()["detail"].lower()


def test_unauthorized_traveler_cannot_acknowledge_inspection(isolated_db_session, seed_batch3_data):
    """AR-06: Another user cannot acknowledge an inspection that belongs to someone else's booking."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    traveler_other = seed_batch3_data["traveler_other"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=8500,
        fuel_level_percent=90,
        cleanliness_status="clean",
    )
    db.add(inspection)
    db.commit()

    client_intruder = make_test_client(db, current_user=traveler_other)
    ack_payload = {
        "agreed_to_condition": True,
        "signature_name": "Intruder User",
    }
    resp = client_intruder.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json=ack_payload,
    )
    assert resp.status_code == 403
    assert "only the traveler associated with this booking" in resp.json()["detail"].lower()


def test_acknowledgement_fails_when_traveler_does_not_agree(isolated_db_session, seed_batch3_data):
    """AR-06: If traveler sets agreed_to_condition=False, acknowledgement is rejected with 400 Bad Request."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=8500,
        fuel_level_percent=90,
        cleanliness_status="clean",
    )
    db.add(inspection)
    db.commit()

    client = make_test_client(db, current_user=traveler)
    ack_payload = {
        "agreed_to_condition": False,
        "signature_name": "Kasun Silva",
    }
    resp = client.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json=ack_payload,
    )
    assert resp.status_code == 400
    assert "must agree to the inspected vehicle condition" in resp.json()["detail"].lower()


def test_return_inspection_cannot_be_acknowledged_via_handover_endpoint(isolated_db_session, seed_batch3_data):
    """AR-06: Handover acknowledgement endpoint only accepts pickup inspections."""
    db = isolated_db_session
    traveler = seed_batch3_data["traveler"]
    bike = seed_batch3_data["bike_with_helmets"]

    now = datetime.now(timezone.utc)
    booking = RentalBooking(
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now,
        return_at=now + timedelta(days=2),
        total_price=Decimal("6400.00"),
        status="active",
        payment_status="paid",
    )
    db.add(booking)
    db.flush()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="return",  # Return inspection, not pickup!
        odometer_reading=8600,
        fuel_level_percent=90,
        cleanliness_status="clean",
    )
    db.add(inspection)
    db.commit()

    client = make_test_client(db, current_user=traveler)
    ack_payload = {
        "agreed_to_condition": True,
        "signature_name": "Kasun Silva",
    }
    resp = client.post(
        f"/api/v1/rental-bookings/{booking.id}/inspections/{inspection.id}/acknowledge",
        json=ack_payload,
    )
    assert resp.status_code == 400
    assert "only pickup handover inspection checklists can be acknowledged" in resp.json()["detail"].lower()
