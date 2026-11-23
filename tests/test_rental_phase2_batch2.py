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
from app.routers.notifications import router as notifications_router
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
    app.include_router(notifications_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Users
    prov_user = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="provider")
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

@pytest.mark.parametrize("selection, expected", [
    ({"pickup_location_id": 2, "return_location_id": 1}, (2, 1)),
    ({"pickup_location_id": 2}, (2, 1)),
    ({}, (1, 1)),
])
def test_booking_accepts_owned_hubs_and_defaults(seed_test_data, selection, expected):
    db = seed_test_data["db"]
    db.add(RentalLocation(id=2, provider_id=1, location_name="Second hub", district="Galle", city="Galle"))
    db.commit()
    client = make_test_client(db, seed_test_data["traveler_user"])
    pickup = datetime.now(timezone.utc) + timedelta(days=2)

    response = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": pickup.isoformat(),
        "return_at": (pickup + timedelta(days=1)).isoformat(),
        **selection,
    })

    assert response.status_code == 201, response.text
    booking = db.get(RentalBooking, response.json()["id"])
    assert (booking.pickup_location_id, booking.return_location_id) == expected


@pytest.mark.parametrize("field", ["pickup_location_id", "return_location_id"])
@pytest.mark.parametrize("invalid_id", [2, 3, 1000, 0], ids=["inactive", "foreign", "unknown", "zero"])
def test_booking_rejects_invalid_hubs_without_writes(seed_test_data, field, invalid_id):
    db = seed_test_data["db"]
    db.add_all([
        RentalLocation(id=2, provider_id=1, location_name="Inactive hub", district="Galle", city="Galle",
                       is_active=False),
        RentalLocation(id=3, provider_id=999, location_name="Foreign hub", district="Galle", city="Galle"),
    ])
    db.commit()
    client = make_test_client(db, seed_test_data["traveler_user"])
    pickup = datetime.now(timezone.utc) + timedelta(days=2)
    counts = {model: db.query(model).count() for model in (RentalBooking, BookingReference, Notification)}

    response = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": pickup.isoformat(),
        "return_at": (pickup + timedelta(days=1)).isoformat(),
        "pickup_location_id": 1,
        "return_location_id": 1,
        field: invalid_id,
    })

    assert response.status_code == 400, response.text
    assert field in response.json()["detail"]
    assert {model: db.query(model).count() for model in counts} == counts


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
    assert pay_data["order_id"].startswith(f"RB-{booking_id}-")
    assert pay_data["booking_reference"] == pay_data["order_id"]
    assert pay_data["notify_url"] == payhere_service.notify_url

    from hashlib import md5

    secret_digest = md5(payhere_service.merchant_secret.encode()).hexdigest().upper()
    signing_input = f"{pay_data['merchant_id']}{pay_data['order_id']}7000.00LKR{secret_digest}"
    assert pay_data["hash"] == md5(signing_input.encode()).hexdigest().upper()

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
        "payment_method": "online",
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
        total_price=Decimal("16000.00"), status="confirmed", payment_status="unpaid", payment_method="online", currency="LKR"
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
    assert canc_data["refund_eligible"] is False
    assert float(canc_data["cancellation_fee"]) == 0.0
    assert float(canc_data["refund_amount"]) == 0.0


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
    assert float(canc_data["refund_amount"]) == 0.0
    assert canc_data["refund_eligible"] is False


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


def create_checkout_test_booking(data, *, payment_method="online", days_until_pickup=2):
    client = make_test_client(data["db"], data["traveler_user"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    response = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": 1,
        "pickup_at": (now + timedelta(days=days_until_pickup)).isoformat(),
        "return_at": (now + timedelta(days=days_until_pickup + 1)).isoformat(),
        "payment_method": payment_method,
    })
    assert response.status_code == 201, response.text
    return client, data["db"].get(RentalBooking, response.json()["id"])


def post_rental_capture(db, order_id, payment_id, *, amount="3500.00", currency="LKR"):
    return make_test_client(db).post("/api/v1/payments/payhere/notify", data={
        "merchant_id": payhere_service.merchant_id,
        "order_id": order_id,
        "payment_id": payment_id,
        "payhere_amount": amount,
        "payhere_currency": currency,
        "status_code": "2",
        "md5sig": rental_callback_signature(order_id, amount, currency),
    })


def rental_callback_signature(order_id, amount, currency):
    from hashlib import md5

    secret_hash = md5(payhere_service.merchant_secret.encode()).hexdigest().upper()
    body = f"{payhere_service.merchant_id}{order_id}{amount}{currency}2{secret_hash}"
    return md5(body.encode()).hexdigest().upper()


def test_rental_callback_rejects_ambiguous_and_nontext_fields(seed_test_data):
    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    checkout = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")
    assert checkout.status_code == 200, checkout.text
    order = checkout.json()["order_id"]
    form = {
        "merchant_id": payhere_service.merchant_id,
        "order_id": order,
        "payment_id": "SYNTHETIC-CALLBACK",
        "payhere_amount": "3500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": rental_callback_signature(order, "3500.00", "LKR"),
    }
    callback = make_test_client(db)
    route = "/api/v1/payments/payhere/notify"
    invalid_requests = (
        ({"data": {**form, "merchant_id": "UNEXPECTED-TEST-MERCHANT"}}, "Payment merchant mismatch"),
        ({"data": {**form, "order_id": [order, order]}}, "Invalid PayHere notification field"),
        (
            {"data": {key: value for key, value in form.items() if key != "md5sig"},
             "files": {"md5sig": ("test.txt", b"synthetic input")}},
            "Invalid PayHere notification field",
        ),
    )
    for payload, expected in invalid_requests:
        response = callback.post(route, **payload)
        assert response.status_code == 400
        assert response.json()["detail"] == expected
        assert db.query(PaymentTransaction).one().payment_status == "pending"
        assert db.get(RentalBooking, booking.id).payment_status == "unpaid"
    assert post_rental_capture(db, order, form["payment_id"]).status_code == 200


@pytest.mark.parametrize("booking_status", ["active", "in_progress", "cancelled", "completed", "expired"])
def test_rental_checkout_rejects_nonpayable_states(seed_test_data, booking_status):
    client, booking = create_checkout_test_booking(seed_test_data)
    booking.status = booking_status
    seed_test_data["db"].commit()

    response = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")

    assert response.status_code == 400
    assert seed_test_data["db"].query(PaymentTransaction).count() == 0


def test_rental_checkout_preserves_cash_and_rejects_elapsed_hold(seed_test_data):
    client, booking = create_checkout_test_booking(seed_test_data, payment_method="pay_at_location")
    db = seed_test_data["db"]
    route = f"/api/v1/payments/rental/bookings/{booking.id}/initiate"
    assert client.post(route).json()["detail"] == "Rental booking must use online payment"
    db.refresh(booking)
    assert booking.payment_method == "pay_at_location"
    booking.payment_method = "online"
    booking.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    assert client.post(route).status_code == 400
    assert db.query(PaymentTransaction).count() == 0


def test_rental_checkout_rejects_zero_total_and_rolls_back_if_form_cannot_be_signed(seed_test_data, monkeypatch):
    client, booking = create_checkout_test_booking(seed_test_data)
    db = seed_test_data["db"]
    original_amount = booking.total_price
    booking.total_price = Decimal("0.00")
    db.commit()
    route = f"/api/v1/payments/rental/bookings/{booking.id}/initiate"
    assert client.post(route).status_code == 400
    assert db.query(PaymentTransaction).count() == 0
    booking.total_price = original_amount
    db.commit()

    def signing_unavailable(**kwargs):
        raise RuntimeError("Checkout signing unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(payhere_service, "generate_checkout_hash", signing_unavailable)
        with pytest.raises(RuntimeError, match="Checkout signing unavailable"):
            client.post(route)
    assert db.query(PaymentTransaction).count() == 0
    assert client.post(route).status_code == 200
    assert db.query(PaymentTransaction).count() == 1


def test_rental_checkout_and_capture_reject_foreign_provider_reference(seed_test_data):
    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    route = f"/api/v1/payments/rental/bookings/{booking.id}/initiate"
    reference = db.query(BookingReference).filter_by(target_booking_id=booking.id).one()
    provider_id = reference.provider_profile_id
    unrelated_provider = db.query(ProviderProfile).filter_by(user_id=booking.user_id).one()
    reference.provider_profile_id = unrelated_provider.id
    db.commit()
    assert client.post(route).status_code == 409
    assert db.query(PaymentTransaction).count() == 0

    reference.provider_profile_id = provider_id
    db.commit()
    checkout = client.post(route)
    assert checkout.status_code == 200, checkout.text
    reference.provider_profile_id = unrelated_provider.id
    db.commit()
    order_id = checkout.json()["order_id"]
    assert post_rental_capture(db, order_id, "REF-SCOPE").status_code == 400
    assert db.query(PaymentTransaction).one().payment_status == "pending"
    assert db.get(RentalBooking, booking.id).payment_status == "unpaid"


def test_rental_late_capture_reconciles_once_after_reusable_checkout(seed_test_data):
    client, booking = create_checkout_test_booking(seed_test_data)
    db = seed_test_data["db"]
    route = f"/api/v1/payments/rental/bookings/{booking.id}/initiate"
    first = client.post(route)
    assert first.status_code == 200, first.text
    order = first.json()["order_id"]
    assert client.post(route).json()["order_id"] == order
    assert db.query(PaymentTransaction).count() == 1

    assert post_rental_capture(db, order, "WRONG", amount="3500.01").status_code == 400
    assert post_rental_capture(db, order, "WRONG", currency="USD").status_code == 400
    assert post_rental_capture(db, order, "").status_code == 400

    booking.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    assert post_rental_capture(db, order, "LATE").json()["status"] == "requires_reconciliation"
    assert post_rental_capture(db, order, "LATE").json()["status"] == "already_processed"
    assert post_rental_capture(db, order, "OTHER").status_code == 409
    db.refresh(booking)
    assert booking.status == "expired"
    assert booking.payment_status == "refund_pending"
    assert db.query(PaymentRefund).count() == 1
    assert db.query(PaymentTransaction).count() == 1


def test_distinct_rental_captures_notify_both_recipients_per_booking(seed_test_data):
    db = seed_test_data["db"]
    for days_until_pickup in (2, 5):
        client, booking = create_checkout_test_booking(seed_test_data, days_until_pickup=days_until_pickup)
        route = f"/api/v1/payments/rental/bookings/{booking.id}/initiate"
        assert make_test_client(db, seed_test_data["provider_user"]).post(route).status_code == 403
        started = client.post(route)
        assert started.status_code == 200, started.text
        assert post_rental_capture(db, started.json()["order_id"], f"PAID-{booking.id}").status_code == 200

    for kind, user_key in (("payment_success", "traveler_user"), ("payment_received", "provider_user")):
        alerts = db.query(Notification).filter_by(notification_type=kind).all()
        assert len(alerts) == 2
        assert {alert.user_id for alert in alerts} == {seed_test_data[user_key].id}
        assert len({alert.link_url for alert in alerts}) == 2


@pytest.mark.parametrize("free_hours,lead_hours", [(0, 1), (24, 24)])
def test_cancellation_keeps_booking_time_cutoff(seed_test_data, monkeypatch, free_hours, lead_hours):
    client, booking = create_checkout_test_booking(seed_test_data, payment_method="pay_at_location")
    db = seed_test_data["db"]
    booking.free_cancellation_hours_snapshot = free_hours
    booking.vehicle.free_cancellation_hours = 96
    now = booking.pickup_at - timedelta(hours=lead_hours)
    db.commit()
    monkeypatch.setattr("app.routers.rental_bookings.utc_now", lambda: now)

    response = client.patch(f"/api/v1/rental-bookings/{booking.id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["is_within_free_cancellation"] is True
    assert response.json()["refund_eligible"] is False
    assert db.query(PaymentRefund).count() == 0


def test_legacy_handover_and_return_emit_one_alert_each(seed_test_data, monkeypatch):
    _, booking = create_checkout_test_booking(seed_test_data)
    db = seed_test_data["db"]
    provider = make_test_client(db, seed_test_data["provider_user"])
    booking.status = "confirmed"
    monkeypatch.setattr("app.routers.rental_bookings.date", type("PickupDay", (date,), {
        "today": classmethod(lambda cls: booking.start_date),
    }))
    db.commit()
    handover = f"/api/v1/rental-bookings/{booking.id}/handover"
    return_route = f"/api/v1/rental-bookings/{booking.id}/return"
    assert provider.post(handover).json()["status"] == "in_progress"
    assert provider.post(handover).status_code == 400
    assert provider.post(return_route).json()["status"] == "completed"
    assert provider.post(return_route).status_code == 400
    alerts = db.query(Notification).filter(
        Notification.notification_type.in_(("rental_booking_active", "rental_booking_completed")),
    ).all()
    assert len(alerts) == 2
    assert {alert.user_id for alert in alerts} == {booking.user_id}


def test_notification_failure_rolls_back_booking_creation(seed_test_data, monkeypatch):
    db = seed_test_data["db"]

    def fail_notification(**kwargs):
        raise RuntimeError("Notification write failed")

    monkeypatch.setattr("app.routers.rental_bookings.create_notification", fail_notification)
    with pytest.raises(RuntimeError, match="Notification write failed"):
        create_checkout_test_booking(seed_test_data)
    assert db.query(RentalBooking).count() == 0
    assert db.query(BookingReference).count() == 0


@pytest.mark.parametrize("cancel_first", [True, False])
def test_rental_cancel_and_capture_serialized_outcomes(seed_test_data, cancel_first):
    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    checkout = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")
    assert checkout.status_code == 200, checkout.text
    order = checkout.json()["order_id"]
    cancellation_url = f"/api/v1/rental-bookings/{booking.id}/cancel"
    if cancel_first:
        cancelled = client.patch(cancellation_url)
        capture = post_rental_capture(db, order, "RECONCILE")
        assert capture.json()["status"] == "requires_reconciliation"
    else:
        capture = post_rental_capture(db, order, "RECONCILE")
        assert capture.json()["payment_status"] == "paid"
        cancelled = client.patch(cancellation_url)
    assert capture.status_code == cancelled.status_code == 200
    assert cancelled.json()["refund_record_created"] is (not cancel_first)
    assert client.patch(cancellation_url).status_code == 400
    assert post_rental_capture(db, order, "RECONCILE").json()["status"] == "already_processed"
    db.refresh(booking)
    reference = db.query(BookingReference).filter_by(booking_reference=order).one()
    transaction = db.query(PaymentTransaction).filter_by(booking_reference_id=reference.id).one()
    refund = db.query(PaymentRefund).filter_by(payment_transaction_id=transaction.id).one()
    assert booking.status == reference.booking_status == "cancelled"
    assert booking.payment_status == reference.payment_status == "refund_pending"
    assert refund.refund_status == "pending"
    assert refund.amount == transaction.amount == Decimal("3500.00")


def test_rental_payment_notification_failure_rolls_back_capture(seed_test_data, monkeypatch):
    from app.routers import payments

    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    checkout = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")
    order = checkout.json()["order_id"]
    emit = payments.create_notification

    def fail_provider_notice(session, user_id, *args):
        if user_id == seed_test_data["provider_user"].id:
            raise RuntimeError("Payment notification write failed")
        return emit(session, user_id, *args)

    with monkeypatch.context() as patch:
        patch.setattr(payments, "create_notification", fail_provider_notice)
        with pytest.raises(RuntimeError, match="Payment notification write failed"):
            post_rental_capture(db, order, "ROLLED-BACK")

    db.refresh(booking)
    assert booking.payment_status == "unpaid"
    assert db.query(PaymentTransaction).one().payment_status == "pending"
    assert db.query(Notification).filter_by(notification_type="payment_success").count() == 0
    assert post_rental_capture(db, order, "ROLLED-BACK").status_code == 200

    traveler_list = client.get("/api/v1/notifications").json()["items"]
    alert = next(item for item in traveler_list if item["notification_type"] == "payment_success")
    assert alert["link_url"] == f"/traveler/rental-bookings/{booking.id}"
    provider_client = make_test_client(db, seed_test_data["provider_user"])
    assert alert["id"] not in {item["id"] for item in provider_client.get("/api/v1/notifications").json()["items"]}
    assert provider_client.patch(f"/api/v1/notifications/{alert['id']}/read").status_code == 404
    assert client.patch(f"/api/v1/notifications/{alert['id']}/read").status_code == 200


def test_rental_cancellation_does_not_reserve_capture_from_mismatched_reference(seed_test_data):
    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    checkout = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")
    assert checkout.status_code == 200
    order_id = checkout.json()["order_id"]
    assert post_rental_capture(db, order_id, "CANCEL-REF").status_code == 200
    reference = db.query(BookingReference).filter_by(booking_reference=order_id).one()
    reference.provider_profile_id = db.query(ProviderProfile).filter_by(user_id=booking.user_id).one().id
    db.commit()

    response = client.patch(f"/api/v1/rental-bookings/{booking.id}/cancel")
    assert response.status_code == 409
    assert booking.status == reference.booking_status == "pending"
    assert db.query(PaymentRefund).count() == 0
    assert booking.payment_status == reference.payment_status == "paid"


def test_rental_cancellation_reserves_only_remaining_verified_capture(seed_test_data):
    db = seed_test_data["db"]
    client, booking = create_checkout_test_booking(seed_test_data)
    checkout = client.post(f"/api/v1/payments/rental/bookings/{booking.id}/initiate")
    order = checkout.json()["order_id"]
    assert post_rental_capture(db, order, "CAPTURED").status_code == 200
    captured = db.query(PaymentTransaction).one()
    db.add_all([
        PaymentRefund(
            payment_transaction_id=captured.id, amount=Decimal("1200.00"),
            currency="LKR", refund_status="pending", refund_reason="Existing allocation",
        ),
        PaymentTransaction(
            booking_reference_id=captured.booking_reference_id, user_id=booking.user_id,
            gateway_name="payhere", gateway_transaction_id="FAILED", amount=captured.amount,
            currency="LKR", payment_status="failed", payment_method="online",
        ),
    ])
    db.commit()
    route = f"/api/v1/rental-bookings/{booking.id}/cancel"
    reservation = db.query(PaymentRefund).one()
    reservation.currency = "USD"
    db.commit()
    conflict = client.patch(route)
    assert conflict.status_code == 409
    assert db.get(RentalBooking, booking.id).status == "pending"
    assert db.get(RentalBooking, booking.id).payment_status == "paid"
    assert db.query(PaymentRefund).count() == 1
    reservation.currency = "LKR"
    db.commit()

    response = client.patch(route)
    assert response.status_code == 200, response.text
    assert Decimal(response.json()["refund_amount"]) == Decimal("2300.00")
    assert client.patch(route).status_code == 400
    refunds = db.query(PaymentRefund).all()
    assert len(refunds) == 2
    assert all(refund.payment_transaction_id == captured.id for refund in refunds)
    assert sum(refund.amount for refund in refunds) == captured.amount
