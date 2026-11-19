"""
CeylonGo Rental Backend - Phase 5 Batch 4 Test Suite
===================================================
Covers:
- AR-04: Security Deposit Management (disclosure, collection, duplicate prevention, release, deduction limits, separation from revenue, provider ownership)
- AR-07: Late Return Charges (1-hour grace period, scheduled vs actual timestamps, hourly rate plan lookup, zero invented rates, duplicate return prevention)
- AR-08: Damage & Accident Reporting (traveler and provider reporting, evidence validation, audit trails, AD-05 admin review integration, terminal state protection)
"""

from datetime import datetime, timedelta, timezone
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
    RentalDeposit,
    RentalEligibilityCheck,
    RentalIncident,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.user import Base, User
from app.routers.admin_rentals import router as admin_rentals_router
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
    app.include_router(admin_rentals_router)

    app.dependency_overrides[get_db] = lambda: db_session
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_batch4_data(isolated_db_session):
    """Seed users, vehicles, locations, rate plans, and bookings."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    traveler = User(id=1, full_name="Kasun Silva", email="kasun@traveler.lk", password_hash="pw", role="traveler")
    traveler_other = User(id=2, full_name="Sneaky Intruder", email="sneaky@traveler.lk", password_hash="pw", role="traveler")
    provider_user = User(id=3, full_name="Lanka Fleet Owner", email="owner@rentals.lk", password_hash="pw", role="provider")
    other_provider_user = User(id=4, full_name="Other Provider", email="other@rentals.lk", password_hash="pw", role="provider")
    admin_user = User(id=5, full_name="Platform Admin", email="admin@ceylongo.lk", password_hash="pw", role="admin")

    db.add_all([traveler, traveler_other, provider_user, other_provider_user, admin_user])
    db.flush()

    # 2. Provider Profiles
    provider = ProviderProfile(
        id=101,
        user_id=provider_user.id,
        business_name="Lanka Moto Rentals",
        provider_type="rental",
        phone="+94773456789",
        district="Galle",
        approval_status="approved",
    )
    other_provider = ProviderProfile(
        id=102,
        user_id=other_provider_user.id,
        business_name="Rival Rentals",
        provider_type="rental",
        phone="+94779998888",
        district="Colombo",
        approval_status="approved",
    )
    db.add_all([provider, other_provider])
    db.flush()

    # 3. Location
    loc = RentalLocation(
        id=201,
        provider_id=provider.id,
        location_name="Galle Fort Hub",
        district="Galle",
        city="Galle",
        address="12 Lighthouse St",
        is_active=True,
    )
    db.add(loc)
    db.flush()

    # 4. Vehicles
    # Vehicle 1: Bike with both hourly and daily rate plans
    bike = RentalVehicle(
        id=301,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Honda Dio 110",
        vehicle_type="bike",
        price_per_day=Decimal("3500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=2,
        fuel_type="petrol",
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    # Vehicle 2: Tuk-Tuk with only daily rate plan (no hourly rate)
    tuktuk = RentalVehicle(
        id=302,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Bajaj RE 205G",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("5000.00"),
        status="available",
        approval_status="approved",
        seating_capacity=3,
        fuel_type="petrol",
        cancellation_policy="moderate",
        free_cancellation_hours=48,
    )
    # Vehicle 3: Bicycle
    bicycle = RentalVehicle(
        id=303,
        provider_id=provider.id,
        location_id=loc.id,
        vehicle_name="Trek Mountain Bike",
        vehicle_type="bicycle",
        price_per_day=Decimal("1500.00"),
        status="available",
        approval_status="approved",
        seating_capacity=1,
        fuel_type=None,
        cancellation_policy="flexible",
        free_cancellation_hours=24,
    )
    db.add_all([bike, tuktuk, bicycle])
    db.flush()

    # 5. Rate Plans
    daily_plan_bike = RentalRatePlan(
        vehicle_id=bike.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("3500.00"),
        included_km_per_day=100,
        extra_km_charge=Decimal("40.00"),
        is_active=True,
    )
    hourly_plan_bike = RentalRatePlan(
        vehicle_id=bike.id,
        plan_name="Standard Hourly",
        duration_type="hourly",
        rate=Decimal("300.00"),
        included_km_per_day=None,
        extra_km_charge=None,
        is_active=True,
    )
    daily_plan_tuktuk = RentalRatePlan(
        vehicle_id=tuktuk.id,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("5000.00"),
        included_km_per_day=80,
        extra_km_charge=Decimal("50.00"),
        is_active=True,
    )
    db.add_all([daily_plan_bike, hourly_plan_bike, daily_plan_tuktuk])
    db.flush()

    # 6. Driving eligibility check for traveler
    elig = RentalEligibilityCheck(
        user_id=traveler.id,
        driver_license_number="B-1234567-LK",
        license_country="Sri Lanka",
        license_expiry_date=(now + timedelta(days=365)).date(),
        verification_status="approved",
    )
    db.add(elig)
    db.flush()

    # 7. Bookings
    # Booking 1: Active bike booking (for deposit & return tests)
    b1_start = now - timedelta(days=1)
    b1_end = now - timedelta(hours=2)  # Scheduled return was 2 hours ago
    b1 = RentalBooking(
        id=401,
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=b1_start,
        return_at=b1_end,
        total_price=Decimal("3500.00"),
        status="active",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    # Booking 2: Active tuk-tuk booking (no hourly plan)
    b2_start = now - timedelta(days=2)
    b2_end = now - timedelta(hours=3)
    b2 = RentalBooking(
        id=402,
        vehicle_id=tuktuk.id,
        user_id=traveler.id,
        pickup_at=b2_start,
        return_at=b2_end,
        total_price=Decimal("10000.00"),
        status="active",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    # Booking 3: Cancelled booking (for invalid lifecycle tests)
    b3 = RentalBooking(
        id=403,
        vehicle_id=bike.id,
        user_id=traveler.id,
        pickup_at=now + timedelta(days=1),
        return_at=now + timedelta(days=3),
        total_price=Decimal("7000.00"),
        status="cancelled",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db.add_all([b1, b2, b3])
    db.flush()

    # Booking references & initial history
    ref1 = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=b1.id,
        booking_reference="CG-RENT-401",
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=b1.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="unpaid",
    )
    ref2 = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=b2.id,
        booking_reference="CG-RENT-402",
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=b2.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="unpaid",
    )
    ref3 = BookingReference(
        module_type="rental_vehicle",
        target_booking_id=b3.id,
        booking_reference="CG-RENT-403",
        user_id=traveler.id,
        provider_profile_id=provider.id,
        total_price=b3.total_price,
        currency="LKR",
        booking_status="cancelled",
        payment_status="unpaid",
    )
    db.add_all([ref1, ref2, ref3])
    db.flush()

    # Pickup inspection for b1
    insp_pickup_b1 = RentalInspection(
        booking_id=b1.id,
        inspection_type="pickup",
        odometer_reading=12000,
        fuel_level_percent=100,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
        notes="Pickup inspection passed",
    )
    # Pickup inspection for b2
    insp_pickup_b2 = RentalInspection(
        booking_id=b2.id,
        inspection_type="pickup",
        odometer_reading=25000,
        fuel_level_percent=100,
        cleanliness_status="clean",
        inspected_by=provider_user.id,
        notes="Pickup inspection passed",
    )
    db.add_all([insp_pickup_b1, insp_pickup_b2])
    db.commit()

    return {
        "traveler": traveler,
        "traveler_other": traveler_other,
        "provider_user": provider_user,
        "other_provider_user": other_provider_user,
        "admin_user": admin_user,
        "bike": bike,
        "tuktuk": tuktuk,
        "bicycle": bicycle,
        "b1": b1,
        "b2": b2,
        "b3": b3,
    }


# ==============================================================================
# PART A: AR-04 — SECURITY DEPOSIT MANAGEMENT TESTS
# ==============================================================================

def test_security_deposit_terms_disclosure(isolated_db_session, seed_batch4_data):
    """Verify security deposit amount and terms are disclosed in vehicle details, quotes, and booking responses."""
    db = isolated_db_session
    data = seed_batch4_data
    client = make_test_client(db, current_user=data["traveler"])

    # 1. Vehicle detail disclosure
    resp = client.get(f"/api/v1/rental-vehicles/{data['bike'].id}")
    assert resp.status_code == 200
    veh_data = resp.json()
    assert Decimal(str(veh_data["security_deposit"])) == Decimal("10000.00")
    assert "security_deposit_terms" in veh_data
    assert "Refundable" in veh_data["security_deposit_terms"]

    # Tuk-tuk deposit
    resp_tuk = client.get(f"/api/v1/rental-vehicles/{data['tuktuk'].id}")
    assert resp_tuk.status_code == 200
    assert Decimal(str(resp_tuk.json()["security_deposit"])) == Decimal("15000.00")

    # 2. Quote disclosure
    pickup = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    ret = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
    quote_resp = client.post(
        f"/api/v1/rental-vehicles/{data['bike'].id}/calculate-quote",
        json={"pickup_at": pickup, "return_at": ret},
    )
    assert quote_resp.status_code == 200
    assert Decimal(str(quote_resp.json()["security_deposit"])) == Decimal("10000.00")

    # 3. Booking retrieval disclosure (not yet collected)
    b_resp = client.get(f"/api/v1/rental-bookings/{data['b1'].id}")
    assert b_resp.status_code == 200
    b_data = b_resp.json()
    assert Decimal(str(b_data["security_deposit"])) == Decimal("10000.00")
    assert b_data["deposit_status"] == "not_collected"


def test_deposit_collection_lifecycle_and_audit(isolated_db_session, seed_batch4_data):
    """Verify provider can record security deposit collection at pickup with audit trail."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])
    traveler_client = make_test_client(db, current_user=data["traveler"])

    # Collect custom deposit amount
    collect_payload = {
        "amount": 12000.00,
        "currency": "LKR",
        "notes": "Cash deposit collected and kept in safe",
    }
    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json=collect_payload,
    )
    assert resp.status_code == 201
    dep_data = resp.json()
    assert dep_data["booking_id"] == data["b1"].id
    assert Decimal(str(dep_data["deposit_amount"])) == Decimal("12000.00")
    assert dep_data["deposit_status"] == "held"
    assert dep_data["currency"] == "LKR"
    assert Decimal(str(dep_data["deducted_amount"])) == Decimal("0.00")
    assert Decimal(str(dep_data["refunded_amount"])) == Decimal("0.00")
    assert dep_data["released_at"] is None

    # Verify traveler can view active held deposit
    dep_view = traveler_client.get(f"/api/v1/rental-bookings/{data['b1'].id}/deposit")
    assert dep_view.status_code == 200
    v_data = dep_view.json()
    assert v_data["deposit_status"] == "held"
    assert v_data["active_deposit"] is not None
    assert Decimal(str(v_data["active_deposit"]["deposit_amount"])) == Decimal("12000.00")

    # Verify audit trail in BookingStatusHistory
    ref = db.query(BookingReference).filter(BookingReference.target_booking_id == data["b1"].id).first()
    history = db.query(BookingStatusHistory).filter(BookingStatusHistory.booking_reference_id == ref.id).all()
    assert any("Security deposit collected" in (h.notes or "") for h in history)


def test_deposit_collection_default_category_amount(isolated_db_session, seed_batch4_data):
    """Verify deposit collection without explicit amount applies the standard category deposit."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Collect without specifying amount on tuk-tuk booking (category default: 15,000 LKR)
    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b2'].id}/deposit/collect",
        json={},
    )
    assert resp.status_code == 201
    dep_data = resp.json()
    assert Decimal(str(dep_data["deposit_amount"])) == Decimal("15000.00")
    assert dep_data["deposit_status"] == "held"


def test_duplicate_deposit_collection_prevention(isolated_db_session, seed_batch4_data):
    """Verify system blocks duplicate deposit collection when a deposit is already held."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # First collection
    r1 = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )
    assert r1.status_code == 201

    # Attempt duplicate collection
    r2 = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )
    assert r2.status_code == 409
    assert "already held" in r2.json()["detail"]


def test_deposit_release_full_refund(isolated_db_session, seed_batch4_data):
    """Verify clean return allows full deposit release with status 'fully_refunded'."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Collect deposit
    provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Resolve with full release
    res_resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/resolve",
        json={"action": "release", "deduction_reason": "Clean inspection, returned undamaged."},
    )
    assert res_resp.status_code == 200
    res_data = res_resp.json()
    assert res_data["deposit_status"] == "fully_refunded"
    assert Decimal(str(res_data["refunded_amount"])) == Decimal("10000.00")
    assert Decimal(str(res_data["deducted_amount"])) == Decimal("0.00")
    assert res_data["released_at"] is not None


def test_deposit_deduction_and_partial_refund(isolated_db_session, seed_batch4_data):
    """Verify damage deduction with partial refund and reason enforcement."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Collect 10,000 LKR deposit
    provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Deduct 3,500 LKR for mirror damage
    res_resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/resolve",
        json={
            "action": "deduct",
            "deducted_amount": 3500.00,
            "deduction_reason": "Broken right side mirror replaced.",
        },
    )
    assert res_resp.status_code == 200
    res_data = res_resp.json()
    assert res_data["deposit_status"] == "partially_refunded"
    assert Decimal(str(res_data["deducted_amount"])) == Decimal("3500.00")
    assert Decimal(str(res_data["refunded_amount"])) == Decimal("6500.00")
    assert res_data["deduction_reason"] == "Broken right side mirror replaced."


def test_deposit_deduction_limits_and_validation(isolated_db_session, seed_batch4_data):
    """Verify deduction cannot exceed recorded deposit and mandatory reason is required."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Collect 10,000 deposit
    provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # 1. Deduct more than recorded deposit (15,000 > 10,000)
    over_resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/resolve",
        json={"action": "deduct", "deducted_amount": 15000.00, "deduction_reason": "Severe frame damage"},
    )
    assert over_resp.status_code == 400
    assert "cannot exceed" in over_resp.json()["detail"]

    # 2. Deduct without explanation
    no_reason_resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/resolve",
        json={"action": "deduct", "deducted_amount": 2000.00, "deduction_reason": ""},
    )
    assert no_reason_resp.status_code == 400
    assert "mandatory" in no_reason_resp.json()["detail"]


def test_deposit_provider_authorization_and_cross_booking(isolated_db_session, seed_batch4_data):
    """Verify unauthorized providers and third-party users cannot access or mutate deposits."""
    db = isolated_db_session
    data = seed_batch4_data
    rival_provider_client = make_test_client(db, current_user=data["other_provider_user"])
    intruder_client = make_test_client(db, current_user=data["traveler_other"])

    # Rival provider cannot collect deposit on data['b1']
    resp1 = rival_provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )
    assert resp1.status_code == 404

    # Unrelated traveler cannot view deposit for booking 401
    resp2 = intruder_client.get(f"/api/v1/rental-bookings/{data['b1'].id}/deposit")
    assert resp2.status_code == 403


def test_deposit_separation_from_rental_revenue(isolated_db_session, seed_batch4_data):
    """Verify security deposits are NOT recorded as rental gross revenue or platform commission."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Collect 10,000 deposit
    provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/deposit/collect",
        json={"amount": 10000.00},
    )

    # Check PaymentTransaction table — no transaction with transaction_type='payment' for the deposit
    txs = db.query(PaymentTransaction).all()
    # Confirm zero deposit amounts recorded as platform booking payment
    deposit_txs = [tx for tx in txs if tx.amount == Decimal("10000.00")]
    assert len(deposit_txs) == 0


# ==============================================================================
# PART B: AR-07 — LATE RETURN CHARGES TESTS
# ==============================================================================

def test_late_return_within_grace_period(isolated_db_session, seed_batch4_data):
    """Return within 1-hour grace period generates ZERO late fees."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Booking 401 return_at was 2 hours ago.
    # We simulate return 45 minutes after scheduled return_at (within 60m grace period)
    ret_time = data["b1"].return_at + timedelta(minutes=45)
    payload = {
        "odometer_reading": 12050,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
        "actual_return_time": ret_time.isoformat(),
        "notes": "Returned safely, 45 minutes past return_at",
    }

    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/return",
        json=payload,
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["status"] == "completed"
    assert res["is_late"] is True
    assert res["late_minutes"] == 45
    assert res["grace_period_applied"] is True
    assert res["billable_late_hours"] == 0
    assert Decimal(str(res["late_return_fee"])) == Decimal("0.00")
    assert "within 1-hour grace period" in res["late_fee_notes"]


def test_late_return_exact_grace_period_boundary(isolated_db_session, seed_batch4_data):
    """Return exactly at the 60-minute mark applies grace period with zero fee."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Exactly 60 minutes late
    ret_time = data["b1"].return_at + timedelta(minutes=60)
    payload = {
        "odometer_reading": 12060,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
        "actual_return_time": ret_time.isoformat(),
    }

    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/return",
        json=payload,
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["grace_period_applied"] is True
    assert res["billable_late_hours"] == 0
    assert Decimal(str(res["late_return_fee"])) == Decimal("0.00")


def test_late_return_beyond_grace_period_with_hourly_rate(isolated_db_session, seed_batch4_data):
    """Return 90 minutes late bills 1 hour (deducted grace period) at the vehicle's approved hourly rate."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # 90 minutes late (30 minutes beyond 60m grace -> ceil to 1 billable hour)
    # Bike hourly rate = 300.00 LKR/hr. Expected fee = 300.00 LKR
    ret_time = data["b1"].return_at + timedelta(minutes=90)
    payload = {
        "odometer_reading": 12080,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
        "actual_return_time": ret_time.isoformat(),
    }

    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/return",
        json=payload,
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


def test_late_return_without_hourly_rate_plan_zero_invented_fees(isolated_db_session, seed_batch4_data):
    """Return 120m late on vehicle without hourly rate logs lateness but generates zero fee."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # Booking 402 is Tuk-tuk (has only daily plan, NO hourly plan)
    # Return 120 minutes late
    ret_time = data["b2"].return_at + timedelta(minutes=120)
    payload = {
        "odometer_reading": 25050,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
        "actual_return_time": ret_time.isoformat(),
    }

    resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b2'].id}/return",
        json=payload,
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


def test_duplicate_return_fee_prevention(isolated_db_session, seed_batch4_data):
    """Calling return on an already completed booking is rejected with 409 Conflict."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    # First return
    provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/return",
        json={"odometer_reading": 12020, "fuel_level_percent": 100},
    )

    # Second return attempt
    dup_resp = provider_client.post(
        f"/api/v1/provider/rentals/bookings/{data['b1'].id}/return",
        json={"odometer_reading": 12020, "fuel_level_percent": 100},
    )
    assert dup_resp.status_code == 409
    assert "already been completed" in dup_resp.json()["detail"]


# ==============================================================================
# PART C: AR-08 — DAMAGE & ACCIDENT REPORTING TESTS
# ==============================================================================

def test_traveler_incident_submission(isolated_db_session, seed_batch4_data):
    """Traveler can submit damage incident report without automatic deposit deduction."""
    db = isolated_db_session
    data = seed_batch4_data
    traveler_client = make_test_client(db, current_user=data["traveler"])

    payload = {
        "incident_type": "scratch",
        "description": "Right side panel scratched while navigating through narrow road in Galle Fort.",
        "estimated_damage_cost": 3000.00,
        "evidence_urls": ["https://s3.ceylongo.lk/rentals/scratch_right.jpg"],
    }

    resp = traveler_client.post(
        f"/api/v1/rental-bookings/{data['b1'].id}/incidents",
        json=payload,
    )
    assert resp.status_code == 201
    inc = resp.json()
    assert inc["booking_id"] == data["b1"].id
    assert inc["incident_type"] == "scratch"
    assert Decimal(str(inc["estimated_damage_cost"])) == Decimal("3000.00")
    assert inc["resolution_status"] == "reported"
    assert inc["actual_repair_cost"] is None

    # Verify zero automatic deposit deductions or payment charges occurred
    deps = db.query(RentalDeposit).filter(RentalDeposit.booking_id == data["b1"].id).all()
    assert len(deps) == 0  # No deposit was arbitrarily created or deducted


def test_provider_incident_submission_with_police_report(isolated_db_session, seed_batch4_data):
    """Provider can submit major incident report with police reference number."""
    db = isolated_db_session
    data = seed_batch4_data
    provider_client = make_test_client(db, current_user=data["provider_user"])

    payload = {
        "incident_type": "accident",
        "description": "Rear bumper impact caused by third party car at traffic signal.",
        "estimated_damage_cost": 25000.00,
        "police_report_reference": "POL-GALLE-2026-9812",
        "evidence_urls": ["https://s3.ceylongo.lk/rentals/accident_rear.jpg"],
    }

    resp = provider_client.post(
        f"/api/v1/rental-bookings/{data['b1'].id}/incidents",
        json=payload,
    )
    assert resp.status_code == 201
    inc = resp.json()
    assert inc["police_report_reference"] == "POL-GALLE-2026-9812"
    assert inc["resolution_status"] == "reported"


def test_incident_evidence_path_traversal_protection(isolated_db_session, seed_batch4_data):
    """Submitting evidence URLs containing path traversal or insecure schemes is rejected."""
    db = isolated_db_session
    data = seed_batch4_data
    traveler_client = make_test_client(db, current_user=data["traveler"])

    # Path traversal attack URL
    payload = {
        "incident_type": "dent",
        "description": "Vehicle dented near fuel tank while parked.",
        "evidence_urls": ["../../etc/shadow", "file:///c:/passwords.txt"],
    }
    resp = traveler_client.post(
        f"/api/v1/rental-bookings/{data['b1'].id}/incidents",
        json=payload,
    )
    assert resp.status_code == 400
    assert "Path traversal" in resp.json()["detail"]


def test_incident_access_control_and_cross_booking_protection(isolated_db_session, seed_batch4_data):
    """Unrelated users cannot report or view incidents on another traveler's booking."""
    db = isolated_db_session
    data = seed_batch4_data
    intruder_client = make_test_client(db, current_user=data["traveler_other"])

    # 1. Intruder attempts to submit incident on booking 401
    resp1 = intruder_client.post(
        f"/api/v1/rental-bookings/{data['b1'].id}/incidents",
        json={"incident_type": "scratch", "description": "Malicious incident report."},
    )
    assert resp1.status_code == 403

    # 2. Intruder attempts to list incidents on booking 401
    resp2 = intruder_client.get(f"/api/v1/rental-bookings/{data['b1'].id}/incidents")
    assert resp2.status_code == 403


def test_cannot_report_incident_on_cancelled_booking(isolated_db_session, seed_batch4_data):
    """Reporting incident on cancelled booking is rejected with 400."""
    db = isolated_db_session
    data = seed_batch4_data
    traveler_client = make_test_client(db, current_user=data["traveler"])

    resp = traveler_client.post(
        f"/api/v1/rental-bookings/{data['b3'].id}/incidents",
        json={"incident_type": "scratch", "description": "Scratch report on cancelled booking."},
    )
    assert resp.status_code == 400
    assert "cancelled" in resp.json()["detail"]


def test_admin_incident_review_and_status_transitions(isolated_db_session, seed_batch4_data):
    """Admin reviews incident from AD-05 queue and updates status to resolved with audit log."""
    db = isolated_db_session
    data = seed_batch4_data
    admin_client = make_test_client(db, current_user=data["admin_user"])
    traveler_client = make_test_client(db, current_user=data["traveler"])

    # 1. Traveler reports incident
    rep_resp = traveler_client.post(
        f"/api/v1/rental-bookings/{data['b1'].id}/incidents",
        json={"incident_type": "dent", "description": "Minor dent on side cover.", "estimated_damage_cost": 4500.00},
    )
    incident_id = rep_resp.json()["id"]

    # 2. Admin inspects incident list
    list_resp = admin_client.get(f"/api/v1/admin/rentals/incidents?booking_id={data['b1'].id}")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # 3. Admin transitions to under_investigation
    patch1 = admin_client.patch(
        f"/api/v1/admin/rentals/incidents/{incident_id}",
        json={"resolution_status": "under_investigation", "admin_notes": "Assigned to claims investigator."},
    )
    assert patch1.status_code == 200
    assert patch1.json()["new_status"] == "under_investigation"

    # 4. Admin resolves incident with actual repair cost
    patch2 = admin_client.patch(
        f"/api/v1/admin/rentals/incidents/{incident_id}",
        json={
            "resolution_status": "resolved",
            "actual_repair_cost": 4200.00,
            "admin_notes": "Workshop repair invoice verified and cleared.",
        },
    )
    assert patch2.status_code == 200
    assert patch2.json()["new_status"] == "resolved"
    assert Decimal(str(patch2.json()["actual_repair_cost"])) == Decimal("4200.00")

    # 5. Verify audit log entry created in AdminAuditLog
    audits = db.query(AdminAuditLog).filter(AdminAuditLog.entity_name == "rental_incidents").all()
    assert len(audits) >= 2


def test_prevent_modifying_finalized_incidents(isolated_db_session, seed_batch4_data):
    """Admin cannot modify an incident once it is finalized with status 'resolved'."""
    db = isolated_db_session
    data = seed_batch4_data
    admin_client = make_test_client(db, current_user=data["admin_user"])

    # Create resolved incident directly
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    inc = RentalIncident(
        booking_id=data["b1"].id,
        incident_type="scratch",
        incident_datetime=now,
        description="Resolved incident",
        estimated_damage_cost=Decimal("1000.00"),
        resolution_status="resolved",
    )
    db.add(inc)
    db.commit()
    db.refresh(inc)

    # Attempt to reopen/modify resolved incident
    reopen_resp = admin_client.patch(
        f"/api/v1/admin/rentals/incidents/{inc.id}",
        json={"resolution_status": "under_investigation", "admin_notes": "Attempt to reopen."},
    )
    assert reopen_resp.status_code == 400
    assert "already in a terminal status" in reopen_resp.json()["detail"]
