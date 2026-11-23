"""
CeylonGo Rental Backend - Phase 3 Batch 3 Test Suite
====================================================
Automated isolated unit & API tests verifying Service Provider requirements SP-10 through SP-15:
- SP-10: Provider Booking Requests (inbox with urgency indicators: critical, urgent, normal, expired)
- SP-11: Booking Approval and Rejection (accept, decline with reason, hold expiry, status lifecycle guards)
- SP-12: Provider Booking History (filters by status, vehicle, payment status, dates, pagination)
- SP-13: Digital Vehicle Handover (pickup inspection, odometer, fuel, signature, evidence, active transition)
- SP-14: Digital Vehicle Return (return inspection, mileage & excess km charge calculation, fuel difference, completed transition)
- SP-15: Cash Payment Recording (cash collection on pickup, balance checks, overpayment guard, audit transaction)

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

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalConditionEvidence,
    RentalEligibilityCheck,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentTransaction,
)
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers import rental_providers
from app.routers.rental_locations import router as rental_locations_router
from app.routers.rental_providers import router as rental_providers_router
from app.routers.rental_vehicles import router as rental_vehicles_router


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
    app.include_router(rental_providers_router)
    app.include_router(rental_vehicles_router)
    app.include_router(rental_locations_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    prov_user1 = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="provider")
    prov_user2 = User(id=2, full_name="Kamal Silva", email="kamal@rentals.lk", password_hash="pw", role="provider")
    traveler_user = User(id=3, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")

    # 2. Provider Profiles
    prov_profile1 = ProviderProfile(
        id=101,
        user_id=1,
        business_name="Sunil Southern Scooters",
        phone="+94771234567",
        district="Galle",
        provider_type="rental",
        approval_status="approved",
    )
    prov_profile2 = ProviderProfile(
        id=102,
        user_id=2,
        business_name="Kamal Tuk Tuks",
        phone="+94772345678",
        district="Matara",
        provider_type="rental",
        approval_status="approved",
    )

    # Seed approved driving eligibility for traveler Alice (user_id=3)
    alice_eligibility = RentalEligibilityCheck(
        id=1,
        user_id=3,
        driver_license_number="B4455667",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    db.add(alice_eligibility)

    # 3. Location
    loc1 = RentalLocation(
        id=1,
        provider_id=101,
        location_name="Galle Fort Hub",
        district="Galle",
        city="Galle",
        address="12 Church St, Galle",
        latitude=Decimal("6.026700"),
        longitude=Decimal("80.217000"),
        is_active=True,
    )

    # 4. Vehicles
    v1 = RentalVehicle(
        id=1,
        provider_id=101,
        vehicle_type="bike",
        vehicle_name="Honda Dio 110cc",
        description="Reliable scooter in Galle",
        price_per_day=Decimal("2500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        location_id=1,
        license_plate="SP-BDC-1234",
    )
    v2 = RentalVehicle(
        id=2,
        provider_id=102,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4-Stroke",
        description="Mirissa tuk-tuk",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        license_plate="SP-AA-9999",
    )

    # 5. Rate Plan for Vehicle 1 (with 100 km/day included, 25 LKR extra km charge)
    rp1 = RentalRatePlan(
        id=1,
        vehicle_id=1,
        plan_name="Standard Daily with 100km",
        duration_type="daily",
        rate=Decimal("2500.00"),
        currency="LKR",
        included_km_per_day=100,
        extra_km_charge=Decimal("25.00"),
        is_active=True,
    )

    # 6. Bookings
    # B1: Pending with Critical hold (< 30 min)
    b1 = RentalBooking(
        id=1,
        vehicle_id=1,
        user_id=3,
        pickup_at=now + timedelta(days=2),
        return_at=now + timedelta(days=4),
        total_price=Decimal("5000.00"),
        currency="LKR",
        status="pending",
        payment_method="pay_at_location",
        payment_status="unpaid",
        expires_at=now + timedelta(minutes=15),
    )
    ref1 = BookingReference(
        id=1,
        booking_reference="RB-REQ-001",
        module_type="rental_vehicle",
        target_booking_id=1,
        user_id=3,
        provider_profile_id=101,
        total_price=Decimal("5000.00"),
        booking_status="pending",
    )

    # B2: Pending with Normal hold (> 2 hours)
    b2 = RentalBooking(
        id=2,
        vehicle_id=1,
        user_id=3,
        pickup_at=now + timedelta(days=10),
        return_at=now + timedelta(days=12),
        total_price=Decimal("5000.00"),
        currency="LKR",
        status="pending",
        payment_method="pay_at_location",
        payment_status="unpaid",
        expires_at=now + timedelta(hours=5),
    )
    ref2 = BookingReference(
        id=2,
        booking_reference="RB-REQ-002",
        module_type="rental_vehicle",
        target_booking_id=2,
        user_id=3,
        provider_profile_id=101,
        total_price=Decimal("5000.00"),
        booking_status="pending",
    )

    # B3: Confirmed booking ready for handover
    b3 = RentalBooking(
        id=3,
        vehicle_id=1,
        user_id=3,
        pickup_at=now + timedelta(days=15),
        return_at=now + timedelta(days=17),
        total_price=Decimal("5000.00"),
        currency="LKR",
        status="confirmed",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    ref3 = BookingReference(
        id=3,
        booking_reference="RB-CONF-003",
        module_type="rental_vehicle",
        target_booking_id=3,
        user_id=3,
        provider_profile_id=101,
        total_price=Decimal("5000.00"),
        booking_status="confirmed",
    )

    # B4: Booking on Provider 2's vehicle
    b4 = RentalBooking(
        id=4,
        vehicle_id=2,
        user_id=3,
        pickup_at=now + timedelta(days=5),
        return_at=now + timedelta(days=7),
        total_price=Decimal("9000.00"),
        currency="LKR",
        status="pending",
        payment_method="pay_at_location",
        payment_status="unpaid",
        expires_at=now + timedelta(hours=2),
    )
    ref4 = BookingReference(
        id=4,
        booking_reference="RB-PROV2-004",
        module_type="rental_vehicle",
        target_booking_id=4,
        user_id=3,
        provider_profile_id=102,
        total_price=Decimal("9000.00"),
        booking_status="pending",
    )

    db.add_all([
        prov_user1, prov_user2, traveler_user,
        prov_profile1, prov_profile2,
        loc1,
        v1, v2,
        rp1,
        b1, b2, b3, b4,
        ref1, ref2, ref3, ref4,
    ])
    db.commit()

    return {
        "prov_user1": prov_user1,
        "prov_user2": prov_user2,
        "traveler_user": traveler_user,
        "v1": v1,
        "v2": v2,
        "rp1": rp1,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b4": b4,
        "ref1": ref1,
        "ref2": ref2,
        "ref3": ref3,
        "ref4": ref4,
    }


# ==============================================================================
# SP-10: PROVIDER BOOKING REQUEST INBOX TESTS
# ==============================================================================

def test_get_provider_booking_requests_inbox(isolated_db_session, seed_test_data):
    """SP-10: Provider lists pending booking requests with urgency indicators and isolation."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    resp = client1.get("/api/v1/provider/rentals/bookings/requests")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2  # Provider 1 has B1 and B2
    items = data["items"]
    b_ids = [item["id"] for item in items]
    assert 1 in b_ids
    assert 2 in b_ids
    assert 4 not in b_ids  # B4 belongs to Provider 2!

    # Verify urgency calculation: B1 has 15 min remaining -> 'critical'
    b1_item = next(i for i in items if i["id"] == 1)
    assert b1_item["urgency"] == "critical"
    assert b1_item["time_remaining_seconds"] is not None
    assert b1_item["time_remaining_seconds"] <= 1800
    assert b1_item["booking_reference"] == "RB-REQ-001"
    assert b1_item["traveler_name"] == "Alice Smith"

    # Verify urgency filter: ?urgency=critical returns only B1
    resp_crit = client1.get("/api/v1/provider/rentals/bookings/requests?urgency=critical")
    assert resp_crit.status_code == 200
    assert resp_crit.json()["total"] == 1
    assert resp_crit.json()["items"][0]["id"] == 1


# ==============================================================================
# SP-11: BOOKING APPROVAL & REJECTION TESTS
# ==============================================================================

def test_accept_provider_booking_success(isolated_db_session, seed_test_data):
    """SP-11: Provider accepts pending booking; transitions to confirmed, syncs BookingRef & notifies traveler."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    resp = client1.post("/api/v1/provider/rentals/bookings/2/accept")
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_id"] == 2
    assert data["status"] == "confirmed"
    assert "successfully confirmed" in data["message"]

    # Verify DB state
    booking2 = db.get(RentalBooking, 2)
    assert booking2.status == "confirmed"

    # Verify BookingReference sync
    ref2 = db.query(BookingReference).filter(BookingReference.target_booking_id == 2).first()
    assert ref2.booking_status == "confirmed"

    # Verify in-app notification created for traveler (id=3)
    notif = db.query(Notification).filter(Notification.user_id == 3, Notification.notification_type == "rental_booking_confirmed").first()
    assert notif is not None
    assert "Confirmed" in notif.title


def test_reject_provider_booking_success(isolated_db_session, seed_test_data):
    """SP-11: Provider declines pending booking with reason; transitions to rejected & notifies traveler."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    payload = {"reason": "Vehicle booked for private family emergency"}
    resp = client1.post("/api/v1/provider/rentals/bookings/1/reject", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_id"] == 1
    assert data["status"] == "rejected"
    assert data["cancellation_reason"] == "Vehicle booked for private family emergency"

    # Verify DB state
    booking1 = db.get(RentalBooking, 1)
    assert booking1.status == "rejected"
    assert booking1.cancellation_reason == "Vehicle booked for private family emergency"


@pytest.mark.parametrize("elapsed_pickup", [False, True], ids=["elapsed-hold", "past-pickup-without-hold"])
def test_accept_expired_booking_rejected(isolated_db_session, seed_test_data, elapsed_pickup):
    """SP-11: Expired pending booking cannot be accepted; marks status as expired."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # Artificially expire booking 1 hold
    booking1 = db.get(RentalBooking, 1)
    if elapsed_pickup:
        booking1.pickup_at = now - timedelta(minutes=5)
        booking1.expires_at = None
    else:
        booking1.expires_at = now - timedelta(minutes=5)
    db.commit()

    resp = client1.post("/api/v1/provider/rentals/bookings/1/accept")
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"]

    # Status automatically updated to expired
    db.refresh(booking1)
    assert booking1.status == "expired"


def test_cross_provider_accept_reject_isolation(isolated_db_session, seed_test_data):
    """SP-11: Cross-provider access blocked."""
    db = isolated_db_session
    user2 = seed_test_data["prov_user2"]
    client2 = make_test_client(db, current_user=user2)

    # Provider 2 attempts to accept Provider 1's booking (id=2) -> 404
    resp_acc = client2.post("/api/v1/provider/rentals/bookings/2/accept")
    assert resp_acc.status_code == 404

    # Provider 2 attempts to reject Provider 1's booking (id=2) -> 404
    resp_rej = client2.post("/api/v1/provider/rentals/bookings/2/reject", json={"reason": "Hacking attempt"})
    assert resp_rej.status_code == 404


# ==============================================================================
# SP-12: PROVIDER BOOKING HISTORY TESTS
# ==============================================================================

def test_get_provider_booking_history_filters_and_pagination(isolated_db_session, seed_test_data):
    """SP-12: Provider lists booking archive with status filters, vehicle filters, and pagination."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # All bookings for Provider 1 (B1, B2, B3)
    resp_all = client1.get("/api/v1/provider/rentals/bookings")
    assert resp_all.status_code == 200
    data = resp_all.json()
    assert data["total"] == 3

    # Filter by status='confirmed' (B3)
    resp_conf = client1.get("/api/v1/provider/rentals/bookings?status=confirmed")
    assert resp_conf.status_code == 200
    assert resp_conf.json()["total"] == 1
    assert resp_conf.json()["items"][0]["id"] == 3

    # Pagination
    resp_p = client1.get("/api/v1/provider/rentals/bookings?page=1&limit=2")
    assert resp_p.status_code == 200
    assert len(resp_p.json()["items"]) == 2
    assert resp_p.json()["pages"] == 2


# ==============================================================================
# SP-13: DIGITAL VEHICLE HANDOVER TESTS
# ==============================================================================

def test_record_vehicle_handover_success_and_lifecycle(isolated_db_session, seed_test_data, monkeypatch):
    """SP-13: Provider records pickup inspection; transitions booking to 'active'."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    payload = {
        "odometer_reading": 12500,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
        "customer_signature": "SIG-ALICE-HANDOVER-99238",
        "notes": "Small stone chip on front mudguard noted",
        "evidence": [
            {
                "evidence_type": "photo",
                "file_url": "https://images.ceylongo.lk/evidence/handover_front.jpg",
                "area_of_vehicle": "front_mudguard",
                "description": "Pre-existing scratch on mudguard",
            }
        ],
    }

    invalid = client1.post("/api/v1/provider/rentals/bookings/3/handover", json={
        **payload, "evidence": [{**payload["evidence"][0], "file_url": "file:///private.jpg"}],
    })
    assert invalid.status_code == 422
    assert db.get(RentalBooking, 3).status == "confirmed"

    # Booking 3 is confirmed -> handover succeeds
    monkeypatch.setattr(rental_providers, "utc_now", lambda: datetime(2090, 12, 5, 3, 30))
    resp = client1.post("/api/v1/provider/rentals/bookings/3/handover", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_id"] == 3
    assert data["status"] == "active"
    assert data["handover_completed_at"] == "2090-12-05T03:30:00Z"
    insp = data["inspection"]
    assert insp["odometer_reading"] == 12500
    assert insp["fuel_level_percent"] == 100
    assert insp["customer_signature"] == "SIG-ALICE-HANDOVER-99238"
    assert len(insp["evidence"]) == 1
    assert insp["evidence"][0]["created_at"]

    # Verify DB booking status
    booking3 = db.get(RentalBooking, 3)
    assert booking3.status == "active"

    # Duplicate handover attempt on now 'active' booking MUST be rejected with 409
    resp_dup = client1.post("/api/v1/provider/rentals/bookings/3/handover", json=payload)
    assert resp_dup.status_code == 409
    assert "already been completed" in resp_dup.json()["detail"]


def test_handover_invalid_status_guards(isolated_db_session, seed_test_data):
    """SP-13: Handover cannot be completed on pending bookings."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    payload = {
        "odometer_reading": 12500,
        "fuel_level_percent": 100,
    }

    # Booking 1 is pending -> Handover must fail
    resp = client1.post("/api/v1/provider/rentals/bookings/1/handover", json=payload)
    assert resp.status_code == 400
    assert "confirmed" in resp.json()["detail"]


# ==============================================================================
# SP-14: DIGITAL VEHICLE RETURN TESTS
# ==============================================================================

def test_record_vehicle_return_and_mileage_calculation(isolated_db_session, seed_test_data):
    """SP-14: Return inspection on active booking; computes km driven, excess km charges, and fuel discrepancy."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # 1. Complete handover first (Odo: 10,000 km, Fuel: 100%)
    handover_payload = {
        "odometer_reading": 10000,
        "fuel_level_percent": 100,
        "cleanliness_status": "clean",
    }
    resp_ho = client1.post("/api/v1/provider/rentals/bookings/3/handover", json=handover_payload)
    assert resp_ho.status_code == 200

    # 2. Return vehicle:
    # Booking 3 duration is 2 days (15th to 17th). RatePlan 1 allows 100 km/day -> Total allowed = 200 km.
    # Return odometer: 10,250 km -> 250 km driven.
    # Excess km = 250 - 200 = 50 km.
    # Excess km charge = 50 * 25.00 LKR = 1,250.00 LKR.
    # Return fuel: 80% -> Fuel discrepancy = -20%.
    return_payload = {
        "odometer_reading": 10250,
        "fuel_level_percent": 80,
        "cleanliness_status": "clean",
        "customer_signature": "SIG-ALICE-RETURN-10293",
        "notes": "Vehicle returned in good condition; fuel at 80%",
        "evidence": [],
    }

    resp_ret = client1.post("/api/v1/provider/rentals/bookings/3/return", json=return_payload)
    assert resp_ret.status_code == 200
    data = resp_ret.json()
    assert data["booking_id"] == 3
    assert data["status"] == "completed"
    assert data["km_driven"] == 250
    assert data["excess_km"] == 50
    assert Decimal(str(data["excess_km_charge"])) == Decimal("1250.00")
    assert data["fuel_difference_percent"] == -20

    # Verify DB status
    booking3 = db.get(RentalBooking, 3)
    assert booking3.status == "completed"

    # Duplicate return rejected with 409 Conflict
    resp_dup = client1.post("/api/v1/provider/rentals/bookings/3/return", json=return_payload)
    assert resp_dup.status_code == 409


def test_return_odometer_less_than_pickup_rejected(isolated_db_session, seed_test_data):
    """SP-14: Return odometer cannot be less than pickup odometer reading."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # Handover at 10,000 km
    client1.post("/api/v1/provider/rentals/bookings/3/handover", json={"odometer_reading": 10000, "fuel_level_percent": 100})

    # Return at 9,500 km (invalid tamper attempt) -> 422
    resp = client1.post("/api/v1/provider/rentals/bookings/3/return", json={"odometer_reading": 9500, "fuel_level_percent": 100})
    assert resp.status_code == 422
    assert "cannot be less than pickup reading" in resp.json()["detail"]


# ==============================================================================
# SP-15: CASH PAYMENT RECORDING TESTS
# ==============================================================================

def test_record_cash_payment_success_and_audit(isolated_db_session, seed_test_data):
    """SP-15: Provider records cash payment on pickup; validates amount, updates payment status & logs transaction."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # Booking 3 total_price is 5000.00 LKR, payment_status is 'unpaid'
    cash_payload = {
        "amount": "5000.00",
        "notes": "Cash collected in full at Galle Fort Station",
    }
    resp = client1.post("/api/v1/provider/rentals/bookings/3/record-cash", json=cash_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["booking_id"] == 3
    assert Decimal(str(data["amount_received"])) == Decimal("5000.00")
    assert data["payment_status"] == "paid"
    assert data["gateway_transaction_id"].startswith("CASH-3-")

    # Verify DB state
    booking3 = db.get(RentalBooking, 3)
    assert booking3.payment_status == "paid"

    # Verify PaymentTransaction audit record exists
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.gateway_name == "cash_on_pickup").first()
    assert tx is not None
    assert tx.amount == Decimal("5000.00")
    assert tx.payment_status == "paid"

    # Attempting to record cash on already fully paid booking raises 409 Conflict
    resp_dup = client1.post("/api/v1/provider/rentals/bookings/3/record-cash", json=cash_payload)
    assert resp_dup.status_code == 409
    assert "already marked as fully paid" in resp_dup.json()["detail"]


def test_record_cash_overpayment_rejected(isolated_db_session, seed_test_data):
    """SP-15: Overpayments exceeding outstanding balance are rejected."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # Booking 3 total_price is 5000.00. Attempt to record 6000.00 cash -> 422
    resp = client1.post("/api/v1/provider/rentals/bookings/3/record-cash", json={"amount": "6000.00"})
    assert resp.status_code == 422
    assert "exceeds outstanding booking balance" in resp.json()["detail"]


def test_partial_cash_receipts_replay_and_use_remaining_balance(isolated_db_session, seed_test_data):
    db = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])
    route = "/api/v1/provider/rentals/bookings/3/record-cash"
    key = {"Idempotency-Key": "register-slip-1"}
    first = client.post(route, headers=key, json={"amount": "3000.00"})
    assert first.status_code == 200, first.text
    assert first.json()["payment_status"] == "partially_paid"
    replay = client.post(route, headers=key, json={"amount": "3000.00"})
    assert replay.status_code == 200, replay.text
    receipt_id = first.json()["gateway_transaction_id"]
    assert isinstance(receipt_id, str) and receipt_id
    assert replay.json()["gateway_transaction_id"] == receipt_id
    assert replay.json()["amount_received"] == first.json()["amount_received"]
    assert client.post(route, headers=key, json={"amount": "2000.00"}).status_code == 409
    assert client.post(route, json={"amount": "2000.01"}).status_code == 422
    final = client.post(route, headers={"Idempotency-Key": "register-slip-2"}, json={"amount": "2000.00"})
    assert final.status_code == 200
    assert final.json()["payment_status"] == "paid"
    final_receipt_id = final.json()["gateway_transaction_id"]
    assert final_receipt_id != receipt_id

    booking = db.get(RentalBooking, 3)
    reference = db.query(BookingReference).filter_by(
        module_type="rental_vehicle", target_booking_id=booking.id,
    ).one()
    receipts = db.query(PaymentTransaction).filter_by(
        booking_reference_id=reference.id, gateway_name="cash_on_pickup",
    )
    audit = db.query(BookingStatusHistory).filter_by(booking_reference_id=reference.id)
    notifications = db.query(Notification).filter_by(
        user_id=booking.user_id, notification_type="rental_payment_received",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )
    assert receipts.count() == audit.count() == notifications.count() == 2
    assert booking.payment_status == reference.payment_status == "paid"
    expected_receipts = {receipt_id: Decimal("3000.00"), final_receipt_id: Decimal("2000.00")}
    assert {row.gateway_transaction_id: row.amount for row in receipts} == expected_receipts
    assert booking.total_price == sum(expected_receipts.values()) == Decimal("5000.00")

    replay_after_final = client.post(route, headers=key, json={"amount": "3000.00"})
    assert replay_after_final.status_code == 200, replay_after_final.text
    replayed_receipt = replay_after_final.json()
    assert replayed_receipt["gateway_transaction_id"] == receipt_id
    assert replayed_receipt["amount_received"] == first.json()["amount_received"]
    assert replayed_receipt["payment_status"] == "paid"

    conflict = client.post(route, headers=key, json={"amount": "2000.00"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "Idempotency key already used for a different amount"
    assert receipts.count() == audit.count() == notifications.count() == 2
    ledger_after = {row.gateway_transaction_id: row.amount for row in receipts}
    assert ledger_after == expected_receipts
    db.refresh(booking)
    db.refresh(reference)
    assert booking.payment_status == reference.payment_status == "paid"
    assert booking.total_price == sum(ledger_after.values()) == Decimal("5000.00")


def test_cash_collection_rejects_inconsistent_booking_reference(isolated_db_session, seed_test_data):
    db = isolated_db_session
    booking = db.get(RentalBooking, 3)
    reference = db.query(BookingReference).filter_by(
        module_type="rental_vehicle", target_booking_id=booking.id,
    ).one()
    reference.user_id = seed_test_data["prov_user1"].id
    db.commit()
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])

    response = client.post(
        "/api/v1/provider/rentals/bookings/3/record-cash",
        json={"amount": "1000.00"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Rental booking reference requires reconciliation before cash collection"
    assert db.get(RentalBooking, 3).payment_status == "unpaid"
    assert db.query(PaymentTransaction).filter_by(gateway_name="cash_on_pickup").count() == 0


def test_cross_provider_cash_recording_blocked(isolated_db_session, seed_test_data):
    """SP-15: Cross-provider payment recording blocked."""
    db = isolated_db_session
    user2 = seed_test_data["prov_user2"]
    client2 = make_test_client(db, current_user=user2)

    # Provider 2 attempts to record cash on Provider 1's booking (id=3) -> 404
    resp = client2.post("/api/v1/provider/rentals/bookings/3/record-cash", json={"amount": "5000.00"})
    assert resp.status_code == 404
