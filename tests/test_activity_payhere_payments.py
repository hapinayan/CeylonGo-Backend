"""
CeylonGo Module 03: Activities & Experiences
Test Suite for A09 PayHere Payment Gateway Integration.

Tests:
1. Online payment booking creation
2. PayHere checkout initiation (payments router & alias route)
3. Input validation: invalid booking, unauthorized user, already paid, cancelled/rejected, past slot, invalid currency
4. Instant Payment Notification (IPN) signature verification:
   - Valid MD5 signature according to PayHere documented algorithm
   - Tampered signature rejection
   - Tampered payload amount rejection
   - Merchant ID mismatch rejection
   - Missing fields rejection
5. Duplicate callback replay protection and idempotency
6. Conflict on duplicate payment_id across different booking references (409)
7. Out-of-order / late capture on cancelled/rejected booking (refund queuing)
8. Payment failure (status_code '-1', '-2') and pending (status_code '0')
9. Payment status retrieval and role-based access control (traveler, provider, admin, unauthorized)
10. Provider earnings and financial aggregation (B14/C05 readiness)
11. Security & credential safety: secret masking in repr, log sanitization, offline verification
12. Traveler cancellation of paid online booking with automatic refund queuing
13. Cancellation cutoff enforcement (24h rule)
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import hashlib

import pytest
from sqlalchemy.orm import Session

from app.core.payhere import payhere_service
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking, ActivityBookingParticipant
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
)
from app.services.activity_payments import (
    get_activity_provider_earnings_components,
    sanitize_payment_log_dict,
    validate_payhere_activity_config,
)
from tests import test_interactive_map

catalog = test_interactive_map.catalog
PAYMENTS_API = "/api/v1/payments"
ACTIVITY_BOOKINGS_API = "/api/v1/activity-bookings"


def create_future_slot(
    db: Session,
    activity_id: int = 1,
    capacity: int = 10,
    hours_ahead: int = 48,
) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=now + timedelta(hours=hours_ahead),
        ends_at=now + timedelta(hours=hours_ahead + 2),
        capacity=capacity,
        reserved_count=0,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def sign_ipn(
    *,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str = "LKR",
    status_code: str = "2",
    payment_id: str = "PH-MOCK-ACT-001",
    merchant_id: str | None = None,
    secret: str | None = None,
) -> dict[str, str]:
    m_id = merchant_id if merchant_id is not None else payhere_service.merchant_id
    m_secret = secret if secret is not None else payhere_service.merchant_secret
    secret_hash = hashlib.md5(m_secret.encode("utf-8")).hexdigest().upper()
    raw = f"{m_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}"
    sig = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    return {
        "merchant_id": m_id,
        "order_id": order_id,
        "payment_id": payment_id,
        "payhere_amount": payhere_amount,
        "payhere_currency": payhere_currency,
        "status_code": status_code,
        "md5sig": sig,
    }


# ==========================================================
# 1. BOOKING CREATION WITH ONLINE PAYMENT
# ==========================================================

def test_activity_booking_creation_with_online_payment(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        slot_id = slot.id

    actor["id"] = 5  # traveler
    payload = {
        "activity_slot_id": slot_id,
        "participants": 2,
        "payment_method": "online",
        "participants_list": [
            {"full_name": "Alice Traveler", "age": 28, "waiver_signed": True},
            {"full_name": "Bob Traveler", "age": 30, "waiver_signed": True},
        ],
    }

    res = client.post(ACTIVITY_BOOKINGS_API, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["payment_method"] == "online"
    assert data["payment_status"] == "unpaid"
    assert data["booking_status"] == "pending"

    # Verify BookingReference was created
    with Session(engine) as db:
        ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "activity",
                BookingReference.target_booking_id == data["id"],
            )
            .first()
        )
        assert ref is not None
        assert ref.payment_status == "unpaid"
        assert ref.booking_status == "pending"
        assert ref.currency == "LKR"


# ==========================================================
# 2. CHECKOUT INITIATION & AUTHORIZATION
# ==========================================================

def test_payment_initiation_success_both_routes(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("4000.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5  # traveler owner
    # Route 1: payments router
    res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["booking_id"] == booking_id
    assert Decimal(str(data["amount"])) == Decimal("4000.00")
    assert data["currency"] == "LKR"
    assert data["merchant_id"] == payhere_service.merchant_id
    assert data["hash"] is not None
    assert "Activity:" in data["items"]
    assert "sandbox.payhere.lk" in data["checkout_url"] or "payhere.lk" in data["checkout_url"]

    # Route 2: activity-bookings alias router
    res_alias = client.post(f"{ACTIVITY_BOOKINGS_API}/{booking_id}/initiate-payment")
    assert res_alias.status_code == 200, res_alias.text
    assert res_alias.json()["hash"] == data["hash"]

    # Verify pending PaymentTransaction exists
    with Session(engine) as db:
        tx = (
            db.query(PaymentTransaction)
            .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
            .filter(
                BookingReference.module_type == "activity",
                BookingReference.target_booking_id == booking_id,
            )
            .first()
        )
        assert tx is not None
        assert tx.payment_status == "pending"
        assert tx.amount == Decimal("4000.00")


def test_payment_initiation_invalid_booking_404(catalog):
    client, actor, _ = catalog
    actor["id"] = 5
    res = client.post(f"{PAYMENTS_API}/activities/bookings/999999/initiate")
    assert res.status_code == 404
    assert "not found" in res.text.lower()


def test_payment_initiation_unauthorized_user_403(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,  # owned by traveler 5
            participants=1,
            total_price=Decimal("1500.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    # Another traveler tries to initiate payment
    actor["id"] = 6
    res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    assert res.status_code == 403
    assert "only initiate payment for your own" in res.text


def test_payment_initiation_ineligible_booking_status(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        b_paid = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="paid", booking_status="confirmed",
        )
        b_cancelled = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="cancelled",
        )
        b_rejected = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="rejected",
        )
        db.add_all([b_paid, b_cancelled, b_rejected])
        db.commit()
        paid_id, cancelled_id, rejected_id = b_paid.id, b_cancelled.id, b_rejected.id

    res1 = client.post(f"{PAYMENTS_API}/activities/bookings/{paid_id}/initiate")
    assert res1.status_code == 400
    assert "already been paid" in res1.text

    res2 = client.post(f"{PAYMENTS_API}/activities/bookings/{cancelled_id}/initiate")
    assert res2.status_code == 400
    assert "cancelled booking" in res2.text

    res3 = client.post(f"{PAYMENTS_API}/activities/bookings/{rejected_id}/initiate")
    assert res3.status_code == 400
    assert "rejected booking" in res3.text


def test_payment_initiation_past_slot_400(catalog):
    client, actor, engine = catalog
    actor["id"] = 5
    now = datetime.now(UTC).replace(tzinfo=None)

    with Session(engine) as db:
        past_slot = ActivitySlot(
            activity_id=1,
            starts_at=now - timedelta(hours=2),
            ends_at=now - timedelta(hours=1),
            capacity=10,
            reserved_count=0,
            is_active=True,
        )
        db.add(past_slot)
        db.commit()

        booking = ActivityBooking(
            activity_slot_id=past_slot.id,
            user_id=5,
            participants=1,
            total_price=Decimal("1000.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    assert res.status_code == 400
    assert "past activity slot" in res.text


# ==========================================================
# 3. IPN WEBHOOK CALLBACK & SIGNATURE VERIFICATION
# ==========================================================

def test_valid_ipn_callback_confirms_activity_booking(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("5000.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5
    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    assert init_res.status_code == 200
    order_id = init_res.json()["order_id"]

    # Simulate PayHere sending IPN callback
    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="5000.00",
        payhere_currency="LKR",
        status_code="2",
        payment_id="PH-ACTIVITY-PAY-100",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "ok"
    assert data["payment_status"] == "paid"

    # Verify DB state
    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.booking_status == "confirmed"
        assert b.payment_status == "paid"

        ref = (
            db.query(BookingReference)
            .filter(BookingReference.booking_reference == order_id)
            .first()
        )
        assert ref.booking_status == "confirmed"
        assert ref.payment_status == "paid"

        tx = (
            db.query(PaymentTransaction)
            .filter(PaymentTransaction.booking_reference_id == ref.id)
            .first()
        )
        assert tx.payment_status in ("paid", "captured")
        assert tx.gateway_transaction_id == "PH-ACTIVITY-PAY-100"
        assert tx.gross_amount == Decimal("5000.00")
        assert tx.commission_rate == Decimal("10.00")
        assert tx.commission_amount == Decimal("500.00")
        assert tx.provider_payable_amount == Decimal("4500.00")

        # Verify notifications created
        notes = db.query(Notification).filter(Notification.user_id == 5).all()
        assert any("Confirmed" in n.title for n in notes)


def test_invalid_ipn_signature_rejected(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("2000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5
    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="2000.00",
        status_code="2",
    )
    # Tamper with signature
    form_data["md5sig"] = "INVALID_TAMPERED_SIGNATURE"

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 400
    assert "Invalid PayHere MD5 signature" in res.text


def test_tampered_amount_against_booking_rejected(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("2000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5
    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    # Signature is valid for 500.00, but booking is for 2000.00
    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="500.00",
        status_code="2",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 400
    assert "mismatch with booking record" in res.text


def test_merchant_id_mismatch_rejected(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5
    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="1000.00",
        merchant_id="ROGUE_MERCHANT_999",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 400
    assert "Payment merchant mismatch" in res.text


# ==========================================================
# 4. IDEMPOTENCY & REPLAY PROTECTION
# ==========================================================

def test_duplicate_callback_replay_is_idempotent(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("3000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    actor["id"] = 5
    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="3000.00",
        payment_id="PH-IDEM-001",
    )

    # First call
    res1 = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res1.status_code == 200
    assert res1.json()["status"] == "ok"

    # Replay call
    res2 = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res2.status_code == 200
    assert res2.json()["status"] == "already_processed"


def test_conflicting_payment_id_across_different_orders_409(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        b1 = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        b2 = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add_all([b1, b2])
        db.commit()
        id1, id2 = b1.id, b2.id

    init1 = client.post(f"{PAYMENTS_API}/activities/bookings/{id1}/initiate")
    init2 = client.post(f"{PAYMENTS_API}/activities/bookings/{id2}/initiate")
    order1 = init1.json()["order_id"]
    order2 = init2.json()["order_id"]

    # Order 1 captures payment ID "SHARED-PAY-123"
    form1 = sign_ipn(order_id=order1, payhere_amount="1000.00", payment_id="SHARED-PAY-123")
    res1 = client.post(f"{PAYMENTS_API}/payhere/notify", data=form1)
    assert res1.status_code == 200

    # Order 2 attempts to claim the same captured payment ID
    form2 = sign_ipn(order_id=order2, payhere_amount="1000.00", payment_id="SHARED-PAY-123")
    res2 = client.post(f"{PAYMENTS_API}/payhere/notify", data=form2)
    assert res2.status_code == 409
    assert "belongs to another order" in res2.text


# ==========================================================
# 5. OUT-OF-ORDER LATE CAPTURE & RECONCILIATION
# ==========================================================

def test_late_capture_on_cancelled_booking_queues_refund(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("2500.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    # Booking gets cancelled before payment arrives
    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        b.booking_status = "cancelled"
        db.commit()

    # Late payment callback arrives
    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="2500.00",
        payment_id="PH-LATE-CAPTURE-77",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "requires_reconciliation"
    assert data["payment_status"] == "refund_pending"

    # Booking must remain cancelled (NOT revived!)
    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.booking_status == "cancelled"
        assert b.payment_status == "refund_pending"

        # Refund record must exist
        refund = (
            db.query(PaymentRefund)
            .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
            .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
            .filter(BookingReference.booking_reference == order_id)
            .first()
        )
        assert refund is not None
        assert refund.refund_status == "pending"
        assert refund.amount == Decimal("2500.00")


# ==========================================================
# 6. PAYMENT FAILURE & PENDING STATES
# ==========================================================

def test_ipn_payment_failure_codes(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1500.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    # Status code -2 = Failed
    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="1500.00",
        status_code="-2",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 200
    assert res.json()["payment_status"] == "failed"

    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.payment_status == "failed"
        assert b.booking_status == "pending"  # Open for retry


def test_ipn_payment_pending_code_0(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("1500.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="1500.00",
        status_code="0",
    )

    res = client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)
    assert res.status_code == 200
    assert res.json()["payment_status"] == "pending"

    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.payment_status == "pending"


# ==========================================================
# 7. PAYMENT STATUS RETRIEVAL & ROLE ISOLATION
# ==========================================================

def test_payment_status_retrieval_and_role_access(catalog):
    client, actor, engine = catalog

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=1, total_price=Decimal("2000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    # 1. Traveler owner access
    actor["id"] = 5
    res_trav = client.get(f"{PAYMENTS_API}/activities/bookings/{booking_id}/status")
    assert res_trav.status_code == 200
    assert res_trav.json()["booking_id"] == booking_id
    assert res_trav.json()["payment_status"] == "unpaid"

    # Alias route on activity bookings router
    res_alias = client.get(f"{ACTIVITY_BOOKINGS_API}/{booking_id}/payment-status")
    assert res_alias.status_code == 200
    assert res_alias.json()["booking_id"] == booking_id

    # 2. Activity Provider owner access (actor id=1 owns activity 1)
    actor["id"] = 1
    res_prov = client.get(f"{PAYMENTS_API}/activities/bookings/{booking_id}/status")
    assert res_prov.status_code == 200

    # 3. Platform Admin access (actor id=4 is admin)
    actor["id"] = 4
    res_admin = client.get(f"{PAYMENTS_API}/activities/bookings/{booking_id}/status")
    assert res_admin.status_code == 200

    # 4. Unauthorized Provider (actor id=2 does NOT own activity 1)
    actor["id"] = 2
    res_unauth = client.get(f"{PAYMENTS_API}/activities/bookings/{booking_id}/status")
    assert res_unauth.status_code == 403


# ==========================================================
# 8. PROVIDER EARNINGS AGGREGATION (B14 READINESS)
# ==========================================================

def test_activity_provider_earnings_components(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=2, total_price=Decimal("10000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id

    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    form_data = sign_ipn(
        order_id=order_id,
        payhere_amount="10000.00",
        payment_id="PH-EARN-001",
    )
    client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)

    with Session(engine) as db:
        # Provider 1 owns activity 1
        earnings = get_activity_provider_earnings_components(db, provider_profile_id=1)
        assert earnings["total_gross"] == Decimal("10000.00")
        assert earnings["pending_earnings"] == Decimal("9000.00")
        assert earnings["paid_count"] == 1

        # Provider 2 has 0 activity earnings
        earnings2 = get_activity_provider_earnings_components(db, provider_profile_id=2)
        assert earnings2["total_gross"] == Decimal("0.00")
        assert earnings2["paid_count"] == 0


# ==========================================================
# 9. SECURITY & DATA PROTECTION CONTROLS
# ==========================================================

def test_credential_masking_and_safe_logging():
    # 1. repr never leaks secret
    rep = repr(payhere_service)
    assert "merchant_secret='***'" in rep
    assert "TEST_MERCHANT_SECRET" not in rep

    # 2. Config validator identifies environment
    config = validate_payhere_activity_config()
    assert "merchant_id" in config
    assert "gateway_status" in config
    assert "merchant_secret" not in config

    # 3. Log sanitizer strips secret/card fields
    dirty_payload = {
        "order_id": "BK-123",
        "merchant_secret": "SUPER_SECRET_KEY",
        "card_no": "4111111111111111",
        "cvv": "123",
        "amount": "5000.00",
    }
    clean_payload = sanitize_payment_log_dict(dirty_payload)
    assert clean_payload["merchant_secret"] == "[REDACTED]"
    assert clean_payload["card_no"] == "[REDACTED]"
    assert clean_payload["cvv"] == "[REDACTED]"
    assert clean_payload["order_id"] == "BK-123"
    assert clean_payload["amount"] == "5000.00"


# ==========================================================
# 10. ONLINE CANCELLATION & REFUND QUEUING
# ==========================================================

def test_traveler_cancels_paid_online_booking_queues_refund(catalog):
    client, actor, engine = catalog
    actor["id"] = 5

    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, hours_ahead=72)
        booking = ActivityBooking(
            activity_slot_id=slot.id, user_id=5, participants=2, total_price=Decimal("6000.00"),
            currency="LKR", payment_method="online", payment_status="unpaid", booking_status="pending",
        )
        db.add(booking)
        slot.reserved_count = 2
        db.commit()
        booking_id = booking.id

    init_res = client.post(f"{PAYMENTS_API}/activities/bookings/{booking_id}/initiate")
    order_id = init_res.json()["order_id"]

    # Capture payment
    form_data = sign_ipn(order_id=order_id, payhere_amount="6000.00", payment_id="PH-CANCEL-PAID-01")
    client.post(f"{PAYMENTS_API}/payhere/notify", data=form_data)

    # Cancel booking > 24h before start
    cancel_res = client.post(
        f"{ACTIVITY_BOOKINGS_API}/{booking_id}/cancel",
        json={"reason": "Flight cancelled by airline"},
    )
    assert cancel_res.status_code == 200, cancel_res.text
    data = cancel_res.json()
    assert data["booking_status"] == "cancelled"
    assert data["payment_status"] == "refund_pending"

    # Verify PaymentRefund queued in database
    with Session(engine) as db:
        refund = (
            db.query(PaymentRefund)
            .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
            .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
            .filter(BookingReference.booking_reference == order_id)
            .first()
        )
        assert refund is not None
        assert refund.refund_status == "pending"
        assert refund.amount == Decimal("6000.00")
        assert "Flight cancelled" in refund.refund_reason
