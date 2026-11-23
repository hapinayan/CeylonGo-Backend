"""
CeylonGo Rental Backend - Phase 6 Test Suite
============================================
Covers:
- Deducted Grace Period (AR-07):
    Exact 5-point boundary matrix:
    * 45 min late  -> 0 billable hours, 0.00 LKR fee, grace_period_applied=True
    * 60 min late  -> 0 billable hours, 0.00 LKR fee, grace_period_applied=True (exact boundary)
    * 90 min late  -> 1 billable hour (ceil(30/60)), 300.00 LKR fee, grace_period_applied=False
    * 120 min late -> 1 billable hour (ceil(60/60)), 300.00 LKR fee, grace_period_applied=False
    * 121 min late -> 2 billable hours (ceil(61/60)), 600.00 LKR fee, grace_period_applied=False
- Missing Hourly Rate Plan Zero-Fee Handling (AR-07):
    * Lateness logged in audit notes without generating synthetic daily_rate/24 fee.
- Pay-at-Pickup Security Deposit Lifecycle (AR-04):
    * Offline deposit collection at pickup (cash recording, status='held')
    * Duplicate collection prevention (HTTP 409 Conflict)
    * Deposit release at return (status='fully_refunded')
    * Damage deduction resolution (status='partially_refunded', mandatory audit reason)
    * Strict separation from rental revenue / settlements (zero online capture/escrow)
- Fail-Closed Minimum Age Verification (AR-02):
    * Block motorized booking when trusted KYC DOB is missing (HTTP 403 Forbidden)
    * Block motorized booking when driver is under category minimum (Bike 18+, Tuk-Tuk 21+)
    * Bicycle exemption preserved (no license or age KYC required)
- Fail-Closed Tuk-Tuk Foreign Permit Verification (AR-11):
    * Foreign driver without IDP / AAC recognition permit blocked from tuk-tuk (HTTP 403)
    * Domestic Sri Lankan driver with Class B1 license permitted without IDP
    * Handover recheck enforcement
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
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
    PaymentTransaction,
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
def seed_phase6_data(isolated_db_session):
    """Seed test users, vehicles, rate plans, and baseline bookings."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    traveler_adult = User(id=1, full_name="Saman Perera", email="saman@ceylon.lk", password_hash="pw", role="traveler")
    traveler_youth = User(id=2, full_name="Nimal Youth", email="nimal@ceylon.lk", password_hash="pw", role="traveler")
    traveler_foreign = User(id=3, full_name="John Traveler", email="john@uktravel.com", password_hash="pw", role="traveler")
    traveler_no_kyc = User(id=4, full_name="Anon Traveler", email="anon@traveler.lk", password_hash="pw", role="traveler")
    provider_user = User(id=5, full_name="Fleet Owner", email="owner@ceylonrentals.lk", password_hash="pw", role="provider")
    admin_user = User(id=6, full_name="Admin Reviewer", email="admin@ceylongo.lk", password_hash="pw", role="admin")

    db.add_all([traveler_adult, traveler_youth, traveler_foreign, traveler_no_kyc, provider_user, admin_user])
    db.flush()

    # 2. Provider Profile & KYC Profiles for DOB verification
    provider = ProviderProfile(
        id=101,
        user_id=provider_user.id,
        business_name="Southern Rides Ltd",
        provider_type="rental",
        phone="+94771234567",
        district="Galle",
        approval_status="approved",
    )
    # Traveler Adult KYC: age 26 (DOB 1998-05-10)
    traveler_adult_kyc = ProviderProfile(
        id=102,
        user_id=traveler_adult.id,
        business_name="Saman KYC",
        provider_type="rental",
        phone="+94771112222",
        district="Colombo",
        approval_status="approved",
        date_of_birth=date(1998, 5, 10),
    )
    # Traveler Youth KYC: age 17 in 2026 (DOB 2009-08-15)
    traveler_youth_kyc = ProviderProfile(
        id=103,
        user_id=traveler_youth.id,
        business_name="Nimal KYC",
        provider_type="rental",
        phone="+94773334444",
        district="Kandy",
        approval_status="approved",
        date_of_birth=date(2009, 8, 15),
    )
    # Foreign Traveler KYC: age 30 (DOB 1994-01-01)
    traveler_foreign_kyc = ProviderProfile(
        id=104,
        user_id=traveler_foreign.id,
        business_name="John KYC",
        provider_type="rental",
        phone="+447700900123",
        district="Colombo",
        approval_status="approved",
        date_of_birth=date(1994, 1, 1),
    )
    db.add_all([provider, traveler_adult_kyc, traveler_youth_kyc, traveler_foreign_kyc])
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=201,
        provider_id=provider.id,
        location_name="Fort Station Hub",
        district="Galle",
        city="Galle",
        address="10 Rampart Street",
        is_active=True,
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles
    # Bike: has hourly rate plan (300 LKR/hr) and daily rate plan (2500 LKR/day)
    bike = RentalVehicle(
        id=301,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Honda Dio 110",
        vehicle_type="bike",
        price_per_day=Decimal("2500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=2,
        fuel_type="petrol",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    # Tuk-Tuk: has only daily plan (4500 LKR/day), NO hourly plan
    tuktuk = RentalVehicle(
        id=302,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Bajaj RE 4S",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=4,
        fuel_type="petrol",
        cancellation_policy="moderate",
        free_cancellation_hours=48,
    )
    # Bicycle: no license required, 500 LKR/day
    bicycle = RentalVehicle(
        id=303,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Trek FX 2 Disc",
        vehicle_type="bicycle",
        price_per_day=Decimal("500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=1,
        fuel_type="other",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    db.add_all([bike, tuktuk, bicycle])
    db.flush()

    # 5. Rate Plans
    plan_bike_hr = RentalRatePlan(
        id=401,
        vehicle_id=bike.id,
        plan_name="Standard Hourly",
        duration_type="hourly",
        rate=Decimal("300.00"),
        is_active=True,
    )
    plan_bike_day = RentalRatePlan(
        id=402,
        vehicle_id=bike.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("2500.00"),
        is_active=True,
    )
    plan_tuk_day = RentalRatePlan(
        id=403,
        vehicle_id=tuktuk.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("4500.00"),
        is_active=True,
    )
    plan_bike_cyc = RentalRatePlan(
        id=404,
        vehicle_id=bicycle.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("500.00"),
        is_active=True,
    )
    db.add_all([plan_bike_hr, plan_bike_day, plan_tuk_day, plan_bike_cyc])
    db.flush()

    # 6. Driving Eligibility
    # Saman: Valid Sri Lankan driving license
    elig_saman = RentalEligibilityCheck(
        id=501,
        user_id=traveler_adult.id,
        driver_license_number="B1234567",
        license_country="Sri Lanka",
        license_expiry_date=date(2028, 12, 31),
        verification_status="approved",
        reviewed_at=now,
    )
    # Nimal: Valid Sri Lankan driving license (for youth age testing)
    elig_nimal = RentalEligibilityCheck(
        id=502,
        user_id=traveler_youth.id,
        driver_license_number="B7654321",
        license_country="Sri Lanka",
        license_expiry_date=date(2028, 12, 31),
        verification_status="approved",
        reviewed_at=now,
    )
    # John: UK driving license (has IDP)
    elig_john_with_idp = RentalEligibilityCheck(
        id=503,
        user_id=traveler_foreign.id,
        driver_license_number="UK99988877",
        license_country="United Kingdom",
        license_expiry_date=date(2028, 12, 31),
        idp_number="IDP-UK-2024-555",
        verification_status="approved",
        reviewed_at=now,
    )
    db.add_all([elig_saman, elig_nimal, elig_john_with_idp])
    db.flush()

    # 7. Seed active booking for late return and deposit testing
    pickup_time = now - timedelta(days=2)
    sched_return = now - timedelta(hours=2)

    booking_bike = RentalBooking(
        id=601,
        vehicle_id=bike.id,
        user_id=traveler_adult.id,
        pickup_at=pickup_time,
        return_at=sched_return,
        total_price=Decimal("5000.00"),
        status="active",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    booking_tuktuk = RentalBooking(
        id=602,
        vehicle_id=tuktuk.id,
        user_id=traveler_adult.id,
        pickup_at=pickup_time,
        return_at=sched_return,
        total_price=Decimal("9000.00"),
        status="active",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db.add_all([booking_bike, booking_tuktuk])
    db.flush()

    ref1 = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=booking_bike.id,
        booking_reference="RB-PHASE6-001",
        user_id=traveler_adult.id,
        provider_profile_id=provider.id,
        total_price=booking_bike.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="unpaid",
    )
    ref2 = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=booking_tuktuk.id,
        booking_reference="RB-PHASE6-002",
        user_id=traveler_adult.id,
        provider_profile_id=provider.id,
        total_price=booking_tuktuk.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="unpaid",
    )
    db.add_all([ref1, ref2])
    db.flush()

    # Pickup inspections for both active bookings
    insp_bike = RentalInspection(
        id=701,
        booking_id=booking_bike.id,
        inspection_type="pickup",
        odometer_reading=10000,
        fuel_level_percent=100,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
    )
    insp_tuk = RentalInspection(
        id=702,
        booking_id=booking_tuktuk.id,
        inspection_type="pickup",
        odometer_reading=25000,
        fuel_level_percent=100,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
    )
    db.add_all([insp_bike, insp_tuk])
    db.flush()

    return {
        "traveler_adult": traveler_adult,
        "traveler_youth": traveler_youth,
        "traveler_foreign": traveler_foreign,
        "traveler_no_kyc": traveler_no_kyc,
        "provider_user": provider_user,
        "admin_user": admin_user,
        "provider": provider,
        "bike": bike,
        "tuktuk": tuktuk,
        "bicycle": bicycle,
        "loc": loc,
        "b_bike": booking_bike,
        "b_tuktuk": booking_tuktuk,
    }


# ==============================================================================
# PART 1: DEDUCTED GRACE PERIOD BOUNDARY MATRIX (AR-07)
# Formula: billable_late_hours = ceil(max(0, lateness_seconds - 3600) / 3600.0)
# ==============================================================================

def test_late_return_45_min_deducted_grace_zero_hours_and_zero_fee(isolated_db_session, seed_phase6_data):
    """45 min late: within 60-min grace period -> 0 billable hours, 0.00 LKR fee, grace_period_applied=True."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_bike"].return_at + timedelta(minutes=45)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/return",
        json={
            "odometer_reading": 10050,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 45
    assert res["grace_period_applied"] is True
    assert res["billable_late_hours"] == 0
    assert Decimal(str(res["late_return_fee"])) == Decimal("0.00")
    assert "within 1-hour grace period" in res["late_fee_notes"]


def test_late_return_60_min_deducted_grace_exact_boundary(isolated_db_session, seed_phase6_data):
    """60 min late: exact grace boundary (3600s - 3600s = 0s) -> 0 billable hours, 0.00 LKR fee, grace_period_applied=True."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_bike"].return_at + timedelta(minutes=60)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/return",
        json={
            "odometer_reading": 10060,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 60
    assert res["grace_period_applied"] is True
    assert res["billable_late_hours"] == 0
    assert Decimal(str(res["late_return_fee"])) == Decimal("0.00")
    assert "within 1-hour grace period" in res["late_fee_notes"]


def test_late_return_90_min_deducted_grace_bills_one_hour(isolated_db_session, seed_phase6_data):
    """90 min late: 30m excess beyond 60m grace -> ceil(30/60) = 1 billable hour at 300.00 LKR/hr."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_bike"].return_at + timedelta(minutes=90)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/return",
        json={
            "odometer_reading": 10090,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 90
    assert res["grace_period_applied"] is False
    assert res["billable_late_hours"] == 1
    assert Decimal(str(res["late_hourly_rate"])) == Decimal("300.00")
    assert Decimal(str(res["late_return_fee"])) == Decimal("300.00")
    assert "1 billable hour(s) beyond 60-minute grace period" in res["late_fee_notes"]


def test_late_return_120_min_deducted_grace_bills_one_hour(isolated_db_session, seed_phase6_data):
    """120 min late: 60m excess beyond 60m grace -> ceil(60/60) = 1 billable hour at 300.00 LKR/hr."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_bike"].return_at + timedelta(minutes=120)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/return",
        json={
            "odometer_reading": 10120,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 120
    assert res["grace_period_applied"] is False
    assert res["billable_late_hours"] == 1
    assert Decimal(str(res["late_hourly_rate"])) == Decimal("300.00")
    assert Decimal(str(res["late_return_fee"])) == Decimal("300.00")
    assert "1 billable hour(s) beyond 60-minute grace period" in res["late_fee_notes"]


def test_late_return_121_min_deducted_grace_bills_two_hours(isolated_db_session, seed_phase6_data):
    """121 min late: 61m excess beyond 60m grace -> ceil(61/60) = 2 billable hours at 300.00 LKR/hr = 600.00 LKR."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_bike"].return_at + timedelta(minutes=121)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/return",
        json={
            "odometer_reading": 10125,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 121
    assert res["grace_period_applied"] is False
    assert res["billable_late_hours"] == 2
    assert Decimal(str(res["late_hourly_rate"])) == Decimal("300.00")
    assert Decimal(str(res["late_return_fee"])) == Decimal("600.00")
    assert "2 billable hour(s) beyond 60-minute grace period" in res["late_fee_notes"]


# ==============================================================================
# PART 2: MISSING HOURLY RATE PLAN ZERO-FEE HANDLING (AR-07)
# ==============================================================================

def test_late_return_without_hourly_rate_plan_zero_invented_fees(isolated_db_session, seed_phase6_data):
    """Vehicle has only daily plan (4500 LKR/day). Return 120m late logs lateness but generates zero fee without inventing rates."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    ret_time = data["b_tuktuk"].return_at + timedelta(minutes=120)
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_tuktuk'].id}/return",
        json={
            "odometer_reading": 25100,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "actual_return_time": ret_time.isoformat(),
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_late"] is True
    assert res["late_minutes"] == 120
    assert res["grace_period_applied"] is False
    assert res["billable_late_hours"] == 1
    assert res["late_hourly_rate"] is None
    assert Decimal(str(res["late_return_fee"])) == Decimal("0.00")
    assert "hourly rate plan is configured" in res["late_fee_notes"]
    assert "without monetary penalty" in res["late_fee_notes"]


# ==============================================================================
# PART 3: PAY-AT-PICKUP SECURITY DEPOSIT LIFECYCLE (AR-04)
# ==============================================================================

def test_pay_at_pickup_deposit_offline_collection_recorded(isolated_db_session, seed_phase6_data):
    """Provider records offline cash deposit collection at vehicle pickup (AR-04)."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    # Provider collects 10000.00 LKR cash at pickup
    payload = {
        "amount": 10000.00,
        "currency": "LKR",
        "notes": "Cash deposit received at Galle Fort Hub desk",
    }
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/collect",
        json=payload,
    )
    assert resp.status_code == 201
    res = resp.json()
    assert res["booking_id"] == data["b_bike"].id
    assert Decimal(str(res["deposit_amount"])) == Decimal("10000.00")
    assert res["deposit_status"] == "held"
    assert Decimal(str(res["refunded_amount"])) == Decimal("0.00")
    assert Decimal(str(res["deducted_amount"])) == Decimal("0.00")

    # Verify audit trail in BookingStatusHistory
    history = (
        db.query(BookingStatusHistory)
        .filter(BookingStatusHistory.notes.like("%Security deposit collected%"))
        .first()
    )
    assert history is not None
    assert "status: held" in history.notes
    assert "10000" in history.notes

    # Verify zero payment transactions created (strict separation from platform revenue)
    pay_tx = db.query(PaymentTransaction).all()
    assert len(pay_tx) == 0


def test_pay_at_pickup_duplicate_collection_prevented(isolated_db_session, seed_phase6_data):
    """Attempting duplicate deposit collection on a booking with a held deposit is rejected with 409 Conflict."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    # First collection
    client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Second collection attempt
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/collect",
        json={"amount": 10000.00},
    )
    assert resp.status_code == 409
    assert "Duplicate collection is not permitted" in resp.json()["detail"]


def test_pay_at_pickup_deposit_full_release_clean_inspection(isolated_db_session, seed_phase6_data):
    """Full offline deposit release upon clean return inspection records status='fully_refunded'."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    # Collect first
    client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Resolve: full release
    resolve_payload = {
        "action": "release",
        "deducted_amount": 0.00,
        "deduction_reason": "Clean inspection, full deposit returned in cash",
    }
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/resolve",
        json=resolve_payload,
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["deposit_status"] == "fully_refunded"
    assert Decimal(str(res["refunded_amount"])) == Decimal("10000.00")
    assert Decimal(str(res["deducted_amount"])) == Decimal("0.00")
    assert res["released_at"] is not None


def test_pay_at_pickup_deposit_partial_deduction_damage(isolated_db_session, seed_phase6_data):
    """Partial damage deduction records status='partially_refunded', withholding amount and releasing balance."""
    db = isolated_db_session
    data = seed_phase6_data
    client = make_test_client(db, current_user=data["provider_user"])

    # Collect first
    client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Resolve: deduct 3500 LKR for broken mirror
    resolve_payload = {
        "action": "deduct",
        "deducted_amount": 3500.00,
        "deduction_reason": "Broken left rearview mirror replacement cost",
    }
    resp = client.post(
        f"/api/v1/provider/rentals/bookings/{data['b_bike'].id}/deposit/resolve",
        json=resolve_payload,
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["deposit_status"] == "partially_refunded"
    assert Decimal(str(res["deducted_amount"])) == Decimal("3500.00")
    assert Decimal(str(res["refunded_amount"])) == Decimal("6500.00")
    assert "Broken left rearview mirror" in res["deduction_reason"]


# ==============================================================================
# PART 4: FAIL-CLOSED MINIMUM AGE VERIFICATION (AR-02)
# ==============================================================================

def test_fail_closed_ar02_motorized_booking_blocked_without_verified_dob(isolated_db_session, seed_phase6_data):
    """Motorized booking fails closed with 403 when trusted KYC DOB is missing, citing AR-02 dependency."""
    db = isolated_db_session
    data = seed_phase6_data

    # User 4 has no KYC profile and thus no trusted date_of_birth
    client = make_test_client(db, current_user=data["traveler_no_kyc"])

    # Give user 4 an approved license check so license check passes
    elig = RentalEligibilityCheck(
        id=599,
        user_id=data["traveler_no_kyc"].id,
        driver_license_number="B9999999",
        license_country="Sri Lanka",
        license_expiry_date=date(2028, 12, 31),
        verification_status="approved",
    )
    db.add(elig)
    db.flush()

    future_start = datetime.now(timezone.utc) + timedelta(days=5)
    future_end = future_start + timedelta(days=2)

    resp = client.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": data["bike"].id,
            "pickup_location_id": data["loc"].id,
            "return_location_id": data["loc"].id,
            "pickup_at": future_start.isoformat(),
            "return_at": future_end.isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp.status_code == 403
    assert "Age verification requires a trusted date of birth" in resp.json()["detail"]
    assert "AR-02 dependency" in resp.json()["detail"]


def test_ar02_bicycle_exemption_succeeds_without_dob_or_license(isolated_db_session, seed_phase6_data):
    """Bicycle rental is exempt from motorized license and age KYC requirements (succeeds with 201 Created)."""
    db = isolated_db_session
    data = seed_phase6_data

    # User 4 has NO license and NO KYC DOB
    client = make_test_client(db, current_user=data["traveler_no_kyc"])

    future_start = datetime.now(timezone.utc) + timedelta(days=10)
    future_end = future_start + timedelta(days=1)

    resp = client.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": data["bicycle"].id,
            "pickup_location_id": data["loc"].id,
            "return_location_id": data["loc"].id,
            "pickup_at": future_start.isoformat(),
            "return_at": future_end.isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


def test_fail_closed_ar02_underage_driver_blocked(isolated_db_session, seed_phase6_data):
    """Driver verified at age 17 is blocked from Bike rental (requires 18+) with 403 Forbidden."""
    db = isolated_db_session
    data = seed_phase6_data

    # Nimal is 17 years old
    client = make_test_client(db, current_user=data["traveler_youth"])

    future_start = datetime.now(timezone.utc) + timedelta(days=12)
    future_end = future_start + timedelta(days=2)

    resp = client.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": data["bike"].id,
            "pickup_location_id": data["loc"].id,
            "return_location_id": data["loc"].id,
            "pickup_at": future_start.isoformat(),
            "return_at": future_end.isoformat(),
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp.status_code == 403
    assert "Renter must be at least 18 years old" in resp.json()["detail"]


# ==============================================================================
# PART 5: FAIL-CLOSED TUK-TUK FOREIGN PERMIT VERIFICATION (AR-11)
# ==============================================================================

def test_fail_closed_ar11_foreign_tuk_tuk_missing_idp_or_recognition(isolated_db_session, seed_phase6_data):
    """Foreign driver without IDP / recognition permit endorsement is blocked from booking Tuk-Tuk with 403."""
    db = isolated_db_session
    data = seed_phase6_data

    # Create foreign driver without IDP
    foreign_user_no_idp = User(id=88, full_name="Foreigner No IDP", email="noidp@travel.com", password_hash="pw", role="traveler")
    foreign_kyc = ProviderProfile(
        id=188,
        user_id=foreign_user_no_idp.id,
        business_name="Foreigner KYC",
        provider_type="rental",
        phone="+447700900999",
        district="Colombo",
        approval_status="approved",
        date_of_birth=date(1990, 1, 1),
    )
    foreign_elig = RentalEligibilityCheck(
        id=588,
        user_id=foreign_user_no_idp.id,
        driver_license_number="FRA123456",
        license_country="France",
        license_expiry_date=date(2028, 12, 31),
        idp_number=None,  # Missing IDP
        verification_status="approved",
    )
    db.add_all([foreign_user_no_idp, foreign_kyc, foreign_elig])
    db.flush()

    client = make_test_client(db, current_user=foreign_user_no_idp)

    future_start = datetime.now(timezone.utc) + timedelta(days=20)
    future_end = future_start + timedelta(days=3)

    resp = client.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": data["tuktuk"].id,
            "pickup_location_id": data["loc"].id,
            "return_location_id": data["loc"].id,
            "pickup_at": future_start.isoformat(),
            "return_at": future_end.isoformat(),
            "self_drive_agreement_accepted": True,
            "passenger_count": 2,
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp.status_code == 403
    assert "require an approved International Driving Permit (IDP) or Sri Lankan recognition permit" in resp.json()["detail"]


def test_ar11_domestic_sri_lanka_tuk_tuk_booking_permitted_with_valid_license(isolated_db_session, seed_phase6_data):
    """Domestic Sri Lankan driver with Class B1 license books Tuk-Tuk without requiring IDP (succeeds with 201)."""
    db = isolated_db_session
    data = seed_phase6_data

    # Saman is Sri Lankan citizen, age 26, has approved Class B1 license
    client = make_test_client(db, current_user=data["traveler_adult"])

    future_start = datetime.now(timezone.utc) + timedelta(days=25)
    future_end = future_start + timedelta(days=2)

    resp = client.post(
        "/api/v1/rental-bookings",
        json={
            "vehicle_id": data["tuktuk"].id,
            "pickup_location_id": data["loc"].id,
            "return_location_id": data["loc"].id,
            "pickup_at": future_start.isoformat(),
            "return_at": future_end.isoformat(),
            "self_drive_agreement_accepted": True,
            "passenger_count": 2,
            "fuel_policy_accepted": True,
            "condition_checklist_acknowledged": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "pending"


def test_fail_closed_handover_recheck_blocked_if_license_expired(isolated_db_session, seed_phase6_data):
    """Handover recheck blocks vehicle handover if driver's license expires before the return date."""
    db = isolated_db_session
    data = seed_phase6_data

    # Create a booking where return date is AFTER license expiration
    traveler_expired = User(id=99, full_name="Expired License User", email="expired@travel.lk", password_hash="pw", role="traveler")
    traveler_expired_kyc = ProviderProfile(
        id=199,
        user_id=traveler_expired.id,
        business_name="Expired KYC",
        provider_type="rental",
        phone="+94770000000",
        district="Galle",
        approval_status="approved",
        date_of_birth=date(1995, 1, 1),
    )
    # License expires tomorrow
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date()
    traveler_expired_elig = RentalEligibilityCheck(
        id=598,
        user_id=traveler_expired.id,
        driver_license_number="EXP12345",
        license_country="Sri Lanka",
        license_expiry_date=tomorrow,
        verification_status="approved",
    )
    db.add_all([traveler_expired, traveler_expired_kyc, traveler_expired_elig])
    db.flush()

    # Create booking returning 5 days from now
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    booking_exp = RentalBooking(
        id=699,
        vehicle_id=data["bike"].id,
        user_id=traveler_expired.id,
        pickup_at=now,
        return_at=now + timedelta(days=5),
        total_price=Decimal("12500.00"),
        status="confirmed",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db.add(booking_exp)
    db.flush()
    ref_exp = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=booking_exp.id,
        booking_reference="RB-PHASE6-EXP",
        user_id=traveler_expired.id,
        provider_profile_id=data["provider"].id,
        total_price=booking_exp.total_price,
        currency="LKR",
        booking_status="confirmed",
        payment_status="unpaid",
    )
    db.add(ref_exp)
    db.flush()

    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Handover pickup inspection attempt
    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{booking_exp.id}/handover",
        json={
            "odometer_reading": 10000,
            "fuel_level_percent": 100,
            "cleanliness_status": "clean",
            "inspector_id": data["provider_user"].id,
            "helmet_quantity_provided": 1,
            "fuel_policy_confirmed": True,
            "condition_checklist_confirmed": True,
        },
    )
    assert resp.status_code == 400
    assert "Handover cannot proceed: Renter driving license expired" in resp.json()["detail"]
