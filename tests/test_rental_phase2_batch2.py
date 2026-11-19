"""
CeylonGo Rental Backend - Phase 2 Batch 2 Test Suite
====================================================
Automated isolated unit & API tests verifying requirements TR-09 through TR-14:
- TR-09: Pickup & return date/time pre-validation without creating booking
- TR-10: Shared platform booking reference integration (RB-XXXX in dbo.booking_references)
- TR-11: Rental PayHere online payment initiation & polymorphic webhook processing
- TR-12: Cash-at-pickup handover terms & security deposit reminders
- TR-13: Traveler rental booking history with lifecycle filters & pagination
- TR-14: Policy-aware cancellation, fee calculation, and refund audit records

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
from app.models.rental import RentalLocation, RentalAvailabilityBlock, RentalEligibilityCheck
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    Notification,
)
from app.database.connection import get_db
from app.routers.auth import get_current_user
from app.routers.rental_bookings import router as rental_bookings_router
from app.routers.payments import router as payments_router
from app.core.payhere import payhere_service


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
    app.include_router(rental_bookings_router)
    app.include_router(payments_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Users
    prov_user = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="service_provider")
    traveler_user = User(id=2, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")
    admin_user = User(id=3, full_name="Admin CeylonGo", email="admin@ceylongo.com", password_hash="pw", role="admin")
    db.add_all([prov_user, traveler_user, admin_user])
    db.flush()

    # Provider profile
    provider = ProviderProfile(
        id=1, user_id=1, business_name="Southern Rides Galle",
        provider_type="rental", phone="+94771234567", district="Galle",
        approval_status="approved", identity_document_type="nic"
    )
    db.add(provider)

    # Seed approved driving eligibility and KYC profile for traveler (Phase 5 Batch 1 requirements)
    traveler_eligibility = RentalEligibilityCheck(
        id=1,
        user_id=traveler_user.id,
        driver_license_number="B7654321",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    db.add(traveler_eligibility)

    traveler_kyc = ProviderProfile(
        id=999,
        user_id=traveler_user.id,
        business_name="Alice KYC",
        provider_type="rental",
        phone="+94779998877",
        district="Galle",
        date_of_birth=date(1995, 6, 15),
        approval_status="approved",
    )
    db.add(traveler_kyc)
    db.flush()

    # Location
    loc = RentalLocation(
        id=1, provider_id=1, location_name="Galle Fort Hub",
        address="10 Rampart Street", city="Galle", district="Galle",
        latitude=Decimal("6.0328"), longitude=Decimal("80.2170")
    )
    db.add(loc)
    db.flush()

    # Approved Vehicle (Scooter, 3500 LKR/day, 24h free cancellation)
    vehicle = RentalVehicle(
        id=1, provider_id=1, vehicle_type="bike", vehicle_name="Yamaha RayZR 125",
        price_per_day=Decimal("3500.00"), status="available", approval_status="approved",
        district="Galle", city="Galle", location_id=1, free_cancellation_hours=24,
        cancellation_policy="Full refund up to 24h before pickup"
    )
    db.add(vehicle)
    db.commit()

    return {
        "db": db,
        "provider_user": prov_user,
        "traveler_user": traveler_user,
        "admin_user": admin_user,
        "provider": provider,
        "location": loc,
        "vehicle": vehicle,
    }


# ==============================================================================
# TR-09: DATE SELECTION & PRE-VALIDATION TESTS
# ==============================================================================

def test_validate_dates_success(seed_test_data):
    data = seed_test_data
    client = make_test_client(data["db"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=2)
    ret = now + timedelta(days=5)

    resp = client.post("/api/v1/rental-bookings/validate-dates", json={
        "vehicle_id": 1,
        "pickup_at": pickup.isoformat(),
        "return_at": ret.isoformat(),
    })
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_valid"] is True
    assert res["duration_days"] == 3
    assert res["duration_hours"] == 72.0
    assert res["collision_type"] == "none"
    assert res["error_message"] is None


def test_validate_dates_past_pickup_rejected(seed_test_data):
    data = seed_test_data
    client = make_test_client(data["db"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now - timedelta(hours=2)
    ret = now + timedelta(days=1)

    resp = client.post("/api/v1/rental-bookings/validate-dates", json={
        "vehicle_id": 1,
        "pickup_at": pickup.isoformat(),
        "return_at": ret.isoformat(),
    })
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_valid"] is False
    assert "in the future" in res["error_message"]


def test_validate_dates_detects_booking_collision(seed_test_data):
    data = seed_test_data
    db = data["db"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Existing active booking on days 3 to 6
    b = RentalBooking(
        vehicle_id=1, user_id=data["traveler_user"].id,
        pickup_at=now + timedelta(days=3), return_at=now + timedelta(days=6),
        total_price=Decimal("10500.00"), status="confirmed", currency="LKR"
    )
    db.add(b)
    db.commit()

    client = make_test_client(db)
    # Requested window days 4 to 7 overlaps
    resp = client.post("/api/v1/rental-bookings/validate-dates", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=4)).isoformat(),
        "return_at": (now + timedelta(days=7)).isoformat(),
    })
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_valid"] is False
    assert res["collision_type"] == "booked"
    assert "already booked" in res["error_message"]


def test_validate_dates_detects_maintenance_collision(seed_test_data):
    data = seed_test_data
    db = data["db"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Active maintenance block on days 5 to 7
    blk = RentalAvailabilityBlock(
        vehicle_id=1, block_type="maintenance",
        start_datetime=now + timedelta(days=5), end_datetime=now + timedelta(days=7),
        reason="Scheduled 10k km service", is_active=True
    )
    db.add(blk)
    db.commit()

    client = make_test_client(db)
    resp = client.post("/api/v1/rental-bookings/validate-dates", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=4)).isoformat(),
        "return_at": (now + timedelta(days=6)).isoformat(),
    })
    assert resp.status_code == 200
    res = resp.json()
    assert res["is_valid"] is False
    assert res["collision_type"] == "maintenance"
    assert "maintenance" in res["error_message"].lower()


# ==============================================================================
# TR-10: SHARED PLATFORM BOOKING REFERENCE INTEGRATION TESTS
# ==============================================================================

def test_create_booking_creates_and_links_shared_booking_reference(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=2)
    ret = now + timedelta(days=4)

    resp = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": pickup.isoformat(),
        "return_at": ret.isoformat(),
        "payment_method": "pay_at_location"
    })
    assert resp.status_code == 201
    booking_json = resp.json()

    # Response contains booking_reference
    assert booking_json["booking_reference"] is not None
    assert booking_json["booking_reference"].startswith(f"RB-{booking_json['id']}-")

    # Verify record in dbo.booking_references
    ref = (
        db.query(BookingReference)
        .filter(BookingReference.booking_reference == booking_json["booking_reference"])
        .first()
    )
    assert ref is not None
    assert ref.module_type == "rental_vehicle"
    assert ref.target_booking_id == booking_json["id"]
    assert ref.user_id == data["traveler_user"].id
    assert ref.provider_profile_id == data["provider"].id
    assert ref.total_price == Decimal("7000.00")
    assert ref.currency == "LKR"
    assert ref.booking_status == "pending"
    assert ref.payment_status == "unpaid"


def test_status_update_synchronizes_booking_reference(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=3)).isoformat(),
    })
    booking_id = resp.json()["id"]

    # Provider confirms booking
    client_provider = make_test_client(db, data["provider_user"])
    res_conf = client_provider.patch(
        f"/api/v1/rental-bookings/{booking_id}/status",
        json={"status": "confirmed"}
    )
    assert res_conf.status_code == 200

    # Verify BookingReference status updated to 'confirmed'
    ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking_id,
        )
        .first()
    )
    assert ref.booking_status == "confirmed"


# ==============================================================================
# TR-11: RENTAL ONLINE PAYMENT & PAYHERE WEBHOOK TESTS
# ==============================================================================

def test_initiate_rental_online_payment(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "online",
    })
    booking_id = resp_create.json()["id"]

    # Initiate payment via payments router
    resp_pay = client_traveler.post(f"/api/v1/payments/rental/bookings/{booking_id}/initiate")
    assert resp_pay.status_code == 200
    pay_data = resp_pay.json()

    assert pay_data["booking_id"] == booking_id
    assert float(pay_data["amount"]) == 7000.0
    assert pay_data["currency"] == "LKR"
    assert pay_data["merchant_id"] == payhere_service.merchant_id
    assert pay_data["hash"] is not None
    assert pay_data["order_id"].startswith(f"RB-{booking_id}-")

    # Pending PaymentTransaction created
    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.user_id == data["traveler_user"].id)
        .first()
    )
    assert tx is not None
    assert tx.amount == Decimal("7000.00")
    assert tx.payment_status == "pending"


def test_payhere_webhook_marks_rental_booking_paid_successfully(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "online",
    })
    booking_id = resp_create.json()["id"]

    resp_init = client_traveler.post(f"/api/v1/payments/rental/bookings/{booking_id}/initiate")
    order_id = resp_init.json()["order_id"]

    # Build valid PayHere MD5 signature
    import hashlib
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    amount_str = "7000.00"
    raw_sig = f"{payhere_service.merchant_id}{order_id}{amount_str}LKR2{secret_hash}"
    valid_sig = hashlib.md5(raw_sig.encode("utf-8")).hexdigest().upper()

    # Simulate PayHere Webhook IPN callback
    client_anon = make_test_client(db)
    webhook_resp = client_anon.post(
        "/api/v1/payments/payhere/notify",
        data={
            "merchant_id": payhere_service.merchant_id,
            "order_id": order_id,
            "payment_id": "PAYHERE_TXN_9988",
            "payhere_amount": amount_str,
            "payhere_currency": "LKR",
            "status_code": "2",  # 2 = success
            "md5sig": valid_sig,
        }
    )
    assert webhook_resp.status_code == 200
    assert webhook_resp.json()["payment_status"] == "paid"

    # Verify RentalBooking is marked paid
    booking = db.get(RentalBooking, booking_id)
    assert booking.payment_status == "paid"
    assert booking.payment_method == "online"

    # Verify PaymentTransaction updated
    tx = db.query(PaymentTransaction).filter(PaymentTransaction.gateway_transaction_id == "PAYHERE_TXN_9988").first()
    assert tx is not None
    assert tx.payment_status == "paid"
    assert tx.paid_at is not None

    # Verify BookingReference updated
    ref = db.query(BookingReference).filter(BookingReference.booking_reference == order_id).first()
    assert ref.payment_status == "paid"


def test_payhere_webhook_idempotency_duplicate_calls(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=3)).isoformat(),
    })
    booking_id = resp_create.json()["id"]
    resp_init = client_traveler.post(f"/api/v1/payments/rental/bookings/{booking_id}/initiate")
    order_id = resp_init.json()["order_id"]

    import hashlib
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    amount_str = "3500.00"
    raw_sig = f"{payhere_service.merchant_id}{order_id}{amount_str}LKR2{secret_hash}"
    valid_sig = hashlib.md5(raw_sig.encode("utf-8")).hexdigest().upper()

    client_anon = make_test_client(db)
    # 1st callback: success
    res1 = client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id, "order_id": order_id,
        "payment_id": "P_ID_1", "payhere_amount": amount_str,
        "payhere_currency": "LKR", "status_code": "2", "md5sig": valid_sig,
    })
    assert res1.status_code == 200

    # 2nd callback: idempotent duplicate
    res2 = client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id, "order_id": order_id,
        "payment_id": "P_ID_1", "payhere_amount": amount_str,
        "payhere_currency": "LKR", "status_code": "2", "md5sig": valid_sig,
    })
    assert res2.status_code == 200
    assert res2.json()["status"] == "already_processed"


def test_payhere_webhook_invalid_signature_rejected(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_anon = make_test_client(db)

    resp = client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id,
        "order_id": "RB-1-FAKE1234",
        "payment_id": "P_ID_1",
        "payhere_amount": "3500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": "COMPLETELY_INVALID_MD5_SIGNATURE",
    })
    assert resp.status_code == 400
    assert "Invalid PayHere MD5 signature" in resp.json()["detail"]


def test_payhere_webhook_unknown_module_type_rejected(seed_test_data):
    data = seed_test_data
    db = data["db"]

    # Manually create booking reference with unknown module type
    ref = BookingReference(
        booking_reference="EXP-UNKNOWN-1",
        module_type="unknown_module",
        target_booking_id=999,
        user_id=data["traveler_user"].id,
        provider_profile_id=data["provider"].id,
        total_price=Decimal("5000.00"),
        currency="LKR",
    )
    db.add(ref)
    db.commit()

    import hashlib
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    amount_str = "5000.00"
    raw_sig = f"{payhere_service.merchant_id}EXP-UNKNOWN-1{amount_str}LKR2{secret_hash}"
    valid_sig = hashlib.md5(raw_sig.encode("utf-8")).hexdigest().upper()

    client_anon = make_test_client(db)
    resp = client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id,
        "order_id": "EXP-UNKNOWN-1",
        "payment_id": "P_UNKNOWN",
        "payhere_amount": amount_str,
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": valid_sig,
    })
    assert resp.status_code == 400
    assert "Unsupported booking reference module type" in resp.json()["detail"]


def test_existing_homestay_payment_preserved(seed_test_data):
    """Verifies that homestay payments and webhook handling remain 100% operational."""
    data = seed_test_data
    db = data["db"]

    # Seed minimal homestay
    hs = Homestay(
        id=1, owner_id=data["provider_user"].id, name="Ocean Breeze Villa",
        location="Galle", price_per_night=Decimal("8000.00"), max_guests=2,
        is_active=True, approval_status="approved"
    )
    db.add(hs)
    db.flush()

    b_hs = Booking(
        id=1, homestay_id=1, user_id=data["traveler_user"].id,
        check_in=datetime.now().date() + timedelta(days=2),
        check_out=datetime.now().date() + timedelta(days=4),
        guests=2,
        total_price=Decimal("16000.00"), status="confirmed", payment_status="unpaid", currency="LKR"
    )
    db.add(b_hs)
    db.flush()

    ref_hs = BookingReference(
        booking_reference="BK-HS-1-TEST1234",
        module_type="homestay",
        target_booking_id=1,
        user_id=data["traveler_user"].id,
        provider_profile_id=data["provider"].id,
        total_price=Decimal("16000.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_status="unpaid"
    )
    db.add(ref_hs)
    db.commit()

    import hashlib
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    amount_str = "16000.00"
    raw_sig = f"{payhere_service.merchant_id}BK-HS-1-TEST1234{amount_str}LKR2{secret_hash}"
    valid_sig = hashlib.md5(raw_sig.encode("utf-8")).hexdigest().upper()

    client_anon = make_test_client(db)
    resp = client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id,
        "order_id": "BK-HS-1-TEST1234",
        "payment_id": "HS_PAY_88",
        "payhere_amount": amount_str,
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": valid_sig,
    })
    assert resp.status_code == 200
    assert resp.json()["payment_status"] == "paid"

    # Verify homestay booking is marked paid
    db.refresh(b_hs)
    assert b_hs.payment_status == "paid"
    assert b_hs.payment_method == "online"


# ==============================================================================
# TR-12: CASH-AT-PICKUP TERMS TESTS
# ==============================================================================

def test_cash_reservation_returns_handover_instructions_and_deposit_notice(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
        "payment_method": "pay_at_location",
    })
    assert resp.status_code == 201
    booking = resp.json()

    assert booking["payment_method"] == "pay_at_location"
    assert booking["payment_status"] == "unpaid"
    assert booking["cash_terms"] is not None
    assert booking["cash_terms"]["is_cash_on_pickup"] is True
    assert float(booking["cash_terms"]["payable_amount"]) == 7000.0
    assert float(booking["cash_terms"]["security_deposit"]) == 10000.0  # motorbike deposit
    assert "directly to the rental provider" in booking["cash_terms"]["instructions"]


# ==============================================================================
# TR-13: TRAVELER BOOKING HISTORY TESTS
# ==============================================================================

def test_my_rental_bookings_history_filtering_and_pagination(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Upcoming booking
    b_upcoming = RentalBooking(
        vehicle_id=1, user_id=data["traveler_user"].id,
        pickup_at=now + timedelta(days=3), return_at=now + timedelta(days=5),
        total_price=Decimal("7000.00"), status="confirmed", currency="LKR"
    )
    # 2. Active booking
    b_active = RentalBooking(
        vehicle_id=1, user_id=data["traveler_user"].id,
        pickup_at=now - timedelta(hours=5), return_at=now + timedelta(hours=20),
        total_price=Decimal("3500.00"), status="active", currency="LKR"
    )
    # 3. Past booking
    b_past = RentalBooking(
        vehicle_id=1, user_id=data["traveler_user"].id,
        pickup_at=now - timedelta(days=10), return_at=now - timedelta(days=8),
        total_price=Decimal("7000.00"), status="completed", currency="LKR"
    )
    # 4. Cancelled booking
    b_canc = RentalBooking(
        vehicle_id=1, user_id=data["traveler_user"].id,
        pickup_at=now + timedelta(days=10), return_at=now + timedelta(days=12),
        total_price=Decimal("7000.00"), status="cancelled", currency="LKR",
        cancellation_reason="Trip postponed"
    )
    db.add_all([b_upcoming, b_active, b_past, b_canc])
    db.commit()

    # All bookings (default)
    res_all = client_traveler.get("/api/v1/rental-bookings/me")
    assert res_all.status_code == 200
    assert res_all.json()["total"] == 4

    # Filter upcoming
    res_up = client_traveler.get("/api/v1/rental-bookings/me?status_filter=upcoming")
    assert res_up.status_code == 200
    assert len(res_up.json()["items"]) == 1
    assert res_up.json()["items"][0]["status"] == "confirmed"

    # Filter active
    res_act = client_traveler.get("/api/v1/rental-bookings/me?status_filter=active")
    assert res_act.status_code == 200
    assert len(res_act.json()["items"]) == 1
    assert res_act.json()["items"][0]["status"] == "active"

    # Filter past
    res_past = client_traveler.get("/api/v1/rental-bookings/me?status_filter=past")
    assert res_past.status_code == 200
    assert len(res_past.json()["items"]) == 1
    assert res_past.json()["items"][0]["status"] == "completed"

    # Filter cancelled
    res_canc = client_traveler.get("/api/v1/rental-bookings/me?status_filter=cancelled")
    assert res_canc.status_code == 200
    assert len(res_canc.json()["items"]) == 1
    assert res_canc.json()["items"][0]["status"] == "cancelled"

    # Enrichment verification
    first_item = res_all.json()["items"][0]
    assert first_item["vehicle_name"] == "Yamaha RayZR 125"
    assert first_item["pickup_location_name"] == "Galle Fort Hub"
    assert first_item["provider_business_name"] == "Southern Rides Galle"
    assert first_item["provider_phone"] == "+94771234567"


# ==============================================================================
# TR-14: POLICY-AWARE CANCELLATION & REFUND AUDIT TESTS
# ==============================================================================

def test_cancel_within_free_cancellation_window_full_refund(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Pickup is 48 hours away; free cancellation is 24 hours -> eligible for full refund
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(hours=48)).isoformat(),
        "return_at": (now + timedelta(hours=72)).isoformat(),
    })
    booking_id = resp_create.json()["id"]

    res_cancel = client_traveler.patch(f"/api/v1/rental-bookings/{booking_id}/cancel")
    assert res_cancel.status_code == 200
    canc_data = res_cancel.json()

    assert canc_data["status"] == "cancelled"
    assert canc_data["is_within_free_cancellation"] is True
    assert canc_data["refund_eligible"] is True
    assert float(canc_data["cancellation_fee"]) == 0.0
    assert float(canc_data["refund_amount"]) == 3500.0


def test_cancel_past_free_cancellation_window_applies_cancellation_fee(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Pickup is only 6 hours away; free cancellation window was 24 hours
    # 2 days total = 7000 LKR; daily rate snapshot = 3500 LKR
    # Penalty of 1 day (3500 LKR) applies, net refund = 3500 LKR
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(hours=6)).isoformat(),
        "return_at": (now + timedelta(hours=54)).isoformat(),
    })
    booking_id = resp_create.json()["id"]

    res_cancel = client_traveler.patch(f"/api/v1/rental-bookings/{booking_id}/cancel")
    assert res_cancel.status_code == 200
    canc_data = res_cancel.json()

    assert canc_data["status"] == "cancelled"
    assert canc_data["is_within_free_cancellation"] is False
    assert float(canc_data["cancellation_fee"]) == 3500.0
    assert float(canc_data["refund_amount"]) == 3500.0
    assert canc_data["refund_eligible"] is True


def test_cancel_paid_booking_creates_refund_audit_record(seed_test_data):
    data = seed_test_data
    db = data["db"]
    client_traveler = make_test_client(db, data["traveler_user"])

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    resp_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(hours=48)).isoformat(),
        "return_at": (now + timedelta(hours=72)).isoformat(),
        "payment_method": "online"
    })
    booking_id = resp_create.json()["id"]

    # Mark booking paid via webhook
    resp_init = client_traveler.post(f"/api/v1/payments/rental/bookings/{booking_id}/initiate")
    order_id = resp_init.json()["order_id"]

    import hashlib
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    amount_str = "3500.00"
    raw_sig = f"{payhere_service.merchant_id}{order_id}{amount_str}LKR2{secret_hash}"
    valid_sig = hashlib.md5(raw_sig.encode("utf-8")).hexdigest().upper()

    client_anon = make_test_client(db)
    client_anon.post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id, "order_id": order_id,
        "payment_id": "PAY_REFUND_TEST", "payhere_amount": amount_str,
        "payhere_currency": "LKR", "status_code": "2", "md5sig": valid_sig,
    })

    # Cancel the paid booking
    res_cancel = client_traveler.patch(f"/api/v1/rental-bookings/{booking_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["refund_record_created"] is True

    # Verify audit record in PaymentRefund (Condition 7)
    refund = db.query(PaymentRefund).first()
    assert refund is not None
    assert refund.amount == Decimal("3500.00")
    assert refund.refund_status == "pending"  # audit record only
    assert "Traveler cancellation refund audit" in refund.refund_reason
