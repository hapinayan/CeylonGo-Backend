"""CeylonGo Eco-Tourism Module 04 — Requirement T09: Offline Mock PayHere Payment Testing.

This dedicated suite verifies the full PayHere payment lifecycle for Eco-Tourism bookings
using FastAPI TestClient, isolated in-memory SQLite fixtures, and mock gateway credentials.
No real PayHere network requests or SQL Server connections are made.
"""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import md5
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.payhere import payhere_service
from app.models.eco_reservation import EcoAvailabilitySlot, EcoReservation
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
)
from app.services import eco_reservations
from tests.test_eco_reservations import (
    API,
    RESERVATIONS,
    action,
    catalog,
    eco_inventory,
    reservation,
    slot,
)


@pytest.fixture
def enabled(monkeypatch):
    """Enable native Eco payments with isolated synthetic credentials."""
    monkeypatch.setenv("ECO_NATIVE_PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("ECO_NATIVE_PAID_CANCELLATION_ENABLED", "1")
    monkeypatch.setattr(payhere_service, "merchant_id", "offline-eco-test")
    monkeypatch.setattr(payhere_service, "merchant_secret", "offline-synthetic-secret")


def confirm_booking(inventory):
    """Helper to set up slot, create reservation, and have provider accept it."""
    client, actor, _, day = inventory
    sid = slot(client, actor, day)["id"]
    booking = reservation(client, actor, sid, 2).json()
    result = action(client, actor, booking["id"], "accept")
    assert result.status_code == 200, f"Provider acceptance failed: {result.text}"
    actor["id"] = 5  # Switch back to traveler (User ID 5)
    return booking


def post_checkout(client, booking_id):
    """Initiate online checkout for an accepted booking."""
    return client.post(f"{RESERVATIONS}/{booking_id}/payments/initiate")


def send_ipn(
    client,
    booking,
    payment_id="PH-TEST-PAYMENT-001",
    *,
    code="2",
    amount=None,
    currency=None,
    order=None,
    merchant=None,
    secret=None,
    sig_override=None,
    extra_fields=None,
):
    """Simulate PayHere IPN webhook notification callback."""
    merchant_val = merchant if merchant is not None else payhere_service.merchant_id
    secret_val = secret if secret is not None else payhere_service.merchant_secret
    order_val = order if order is not None else booking["reference"]
    amount_val = amount if amount is not None else booking["total"]
    currency_val = currency if currency is not None else booking["currency"]

    if sig_override is not None:
        signature = sig_override
    else:
        secret_hash = md5(secret_val.encode("utf-8")).hexdigest().upper()
        raw = f"{merchant_val}{order_val}{amount_val}{currency_val}{code}{secret_hash}"
        signature = md5(raw.encode("utf-8")).hexdigest().upper()

    data = {
        "merchant_id": merchant_val,
        "order_id": order_val,
        "payment_id": payment_id,
        "payhere_amount": amount_val,
        "payhere_currency": currency_val,
        "status_code": code,
        "md5sig": signature,
    }
    if extra_fields:
        data.update(extra_fields)
    return client.post(f"{API}/payments/payhere/notify", data=data)


# ==============================================================================
# 1. SUCCESSFUL CHECKOUT FORM CONTRACT & PARAMETER VERIFICATION
# ==============================================================================


def test_t09_checkout_initiate_generates_valid_payhere_contract(eco_inventory, enabled):
    """Initiating checkout produces valid PayHere parameters and pending transaction."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)

    response = post_checkout(client, booking["id"])
    assert response.status_code == 200, response.text
    data = response.json()

    # Verify all expected contract fields in PayHere checkout form payload
    expected_fields = [
        "merchant_id",
        "return_url",
        "cancel_url",
        "notify_url",
        "order_id",
        "items",
        "currency",
        "amount",
        "hash",
        "first_name",
        "last_name",
        "email",
    ]
    for field in expected_fields:
        assert field in data, f"Missing required field {field} in checkout response"

    # Verify merchant ID and checkout hash formula
    assert data["merchant_id"] == "offline-eco-test"
    assert data["order_id"] == booking["reference"]
    assert data["currency"] == "LKR"
    assert Decimal(data["amount"]) == Decimal("3600.00")

    expected_hash = payhere_service.generate_checkout_hash(
        order_id=booking["reference"],
        amount=Decimal("3600.00"),
        currency="LKR",
    )
    assert data["hash"] == expected_hash

    # Verify pending PaymentTransaction record in database
    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        assert ref is not None
        tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        )
        assert tx is not None
        assert tx.payment_status == "pending"
        assert tx.payment_method == "online"
        assert tx.amount == Decimal("3600.00")
        assert tx.currency == "LKR"
        assert tx.gateway_name == "payhere"
        assert tx.gateway_transaction_id is None


# ==============================================================================
# 2. SUCCESSFUL PAYMENT LIFECYCLE (END-TO-END HTTP)
# ==============================================================================


def test_t09_successful_payment_lifecycle_http(eco_inventory, enabled):
    """Complete HTTP lifecycle: Initiate -> Pending -> IPN callback -> Paid status."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)

    # 1. Initiate checkout
    checkout_res = post_checkout(client, booking["id"])
    assert checkout_res.status_code == 200

    # 2. Query status prior to IPN -> pending
    status_url = f"{RESERVATIONS}/{booking['id']}/payments/status"
    status_res = client.get(status_url)
    assert status_res.status_code == 200
    assert status_res.json()["payment_status"] == "pending"
    assert status_res.json()["booking_status"] == "confirmed"

    # 3. Simulate PayHere webhook IPN notification (status_code = "2")
    ipn_res = send_ipn(client, booking, payment_id="PH-SUCCESS-001", code="2")
    assert ipn_res.status_code == 200, ipn_res.text
    assert ipn_res.json()["status"] == "ok"

    # 4. Query status after IPN -> paid
    status_after = client.get(status_url).json()
    assert status_after["payment_status"] == "paid"
    assert status_after["payment_method"] == "online"
    assert status_after["booking_status"] == "confirmed"


# ==============================================================================
# 3. FAILED PAYMENT CALLBACK (status_code = "-2")
# ==============================================================================


def test_t09_failed_payment_callback_records_failure_and_retains_pending_booking(
    eco_inventory, enabled
):
    """Gateway decline (status_code = -2) marks transaction failed but keeps booking pending."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)

    assert post_checkout(client, booking["id"]).status_code == 200

    # Deliver failed IPN callback
    fail_res = send_ipn(client, booking, payment_id="PH-FAIL-001", code="-2")
    assert fail_res.status_code == 200
    assert fail_res.json()["status"] == "ignored"

    # Transaction in DB should be marked failed
    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        )
        assert tx.payment_status == "failed"

        # EcoReservation holds capacity and remains confirmed for retry
        res = db.get(EcoReservation, booking["id"])
        assert res.status == "confirmed"
        assert res.payment_status == "unpaid"
        assert res.holds_capacity is True

        # Slot inventory remains held
        slot_row = db.get(EcoAvailabilitySlot, booking["slot_id"])
        assert slot_row.reserved_count == 2


# ==============================================================================
# 4. CANCELLED PAYMENT CALLBACK (status_code = "-1")
# ==============================================================================


def test_t09_cancelled_payment_callback_records_cancelled_transaction(
    eco_inventory, enabled
):
    """User cancellation at gateway (status_code = -1) records failed attempt without corrupting state."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)

    assert post_checkout(client, booking["id"]).status_code == 200

    # User pressed cancel on PayHere payment page
    cancel_res = send_ipn(client, booking, payment_id="PH-CANCEL-001", code="-1")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "ignored"

    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        )
        assert tx.payment_status == "failed"

        res = db.get(EcoReservation, booking["id"])
        assert res.status == "confirmed"
        assert res.payment_status == "unpaid"


# ==============================================================================
# 5. RETRY AFTER FAILED PAYMENT
# ==============================================================================


def test_t09_payment_retry_after_failure_generates_distinct_order_and_succeeds(
    eco_inventory, enabled
):
    """After a failed attempt, re-initiating checkout generates a distinct retry order that succeeds."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)

    # First attempt fails
    first_checkout = post_checkout(client, booking["id"]).json()
    assert send_ipn(client, booking, payment_id="PH-FAIL-001", code="-2").status_code == 200

    # Second attempt generates retry order ID
    retry_checkout = post_checkout(client, booking["id"]).json()
    retry_order_id = retry_checkout["order_id"]
    assert retry_order_id != first_checkout["order_id"]
    assert retry_order_id.startswith("ECO-RETRY-")

    # Retry IPN callback succeeds
    retry_ipn = send_ipn(
        client, booking, payment_id="PH-RETRY-OK", code="2", order=retry_order_id
    )
    assert retry_ipn.status_code == 200
    assert retry_ipn.json()["status"] == "ok"

    # Status check confirms paid
    status = client.get(f"{RESERVATIONS}/{booking['id']}/payments/status").json()
    assert status["payment_status"] == "paid"


# ==============================================================================
# 6. SIGNATURE VALIDATION & TAMPERING GUARDS
# ==============================================================================


def test_t09_webhook_rejects_corrupted_md5sig(eco_inventory, enabled):
    """Tampered MD5 signature is rejected with HTTP 400 and does not change state."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    corrupt_res = send_ipn(
        client,
        booking,
        payment_id="PH-CORRUPT",
        sig_override="BADC0FFEE00000000000000000000000",
    )
    assert corrupt_res.status_code == 400
    assert "Invalid PayHere MD5 signature" in corrupt_res.text

    # Transaction remains pending; booking remains unpaid
    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        )
        assert tx.payment_status == "pending"
        assert tx.gateway_transaction_id is None


def test_t09_webhook_rejects_empty_or_missing_signature(eco_inventory, enabled):
    """Missing or empty signature parameter causes HTTP 400 Bad Request."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    empty_sig_res = send_ipn(client, booking, payment_id="PH-EMPTY-SIG", sig_override="")
    assert empty_sig_res.status_code == 400
    assert "Missing required PayHere notification parameters" in empty_sig_res.text


def test_t09_webhook_rejects_signature_from_wrong_secret(eco_inventory, enabled):
    """Signature generated using an unauthorized secret is rejected."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    attacker_res = send_ipn(
        client,
        booking,
        payment_id="PH-ATTACKER",
        secret="attacker-forged-secret-12345",
    )
    assert attacker_res.status_code == 400
    assert "Invalid PayHere MD5 signature" in attacker_res.text


def test_t09_webhook_rejects_merchant_id_mismatch(eco_inventory, enabled):
    """Payload targeting a foreign merchant ID is rejected with HTTP 400."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    mismatch_res = send_ipn(
        client,
        booking,
        payment_id="PH-DIFF-MERCHANT",
        merchant="unauthorized-merchant-id",
    )
    assert mismatch_res.status_code == 400
    assert "Payment merchant mismatch" in mismatch_res.text


def test_t09_webhook_signature_case_normalization(eco_inventory, enabled):
    """Valid signature in lowercase hex is accepted due to case normalization."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    secret_hash = md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
    raw = f"{payhere_service.merchant_id}{booking['reference']}{booking['total']}{booking['currency']}2{secret_hash}"
    lowercase_sig = md5(raw.encode("utf-8")).hexdigest().lower()

    res = send_ipn(
        client,
        booking,
        payment_id="PH-CASE-NORM-001",
        code="2",
        sig_override=lowercase_sig,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


# ==============================================================================
# 7. DUPLICATE CALLBACK IDEMPOTENCY & CONFLICT HANDLING
# ==============================================================================


def test_t09_duplicate_callback_idempotency_exact_same_payment_id(eco_inventory, enabled):
    """Identical duplicate IPN deliveries return already_processed without duplicating records."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    # First delivery
    first_ipn = send_ipn(client, booking, payment_id="PH-IDEMP-001", code="2")
    assert first_ipn.status_code == 200
    assert first_ipn.json()["status"] == "ok"

    # Second identical delivery
    second_ipn = send_ipn(client, booking, payment_id="PH-IDEMP-001", code="2")
    assert second_ipn.status_code == 200
    assert second_ipn.json()["status"] == "already_processed"

    # Third delivery with case variation in payment_id
    third_ipn = send_ipn(client, booking, payment_id="ph-idemp-001", code="2")
    assert third_ipn.status_code == 200
    assert third_ipn.json()["status"] == "already_processed"

    # Assert exactly 1 transaction, 1 status history, 1 payment notification in DB
    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        transactions = db.scalars(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        ).all()
        assert len(transactions) == 1
        assert transactions[0].payment_status == "paid"

        histories = db.scalars(
            select(BookingStatusHistory).where(
                BookingStatusHistory.booking_reference_id == ref.id,
                BookingStatusHistory.notes == "online_payment_received",
            )
        ).all()
        assert len(histories) == 1

        notifications = db.scalars(
            select(Notification).where(
                Notification.notification_type == "reservation_online_payment_received"
            )
        ).all()
        traveler_notifications = [n for n in notifications if n.user_id == 5]
        assert len(traveler_notifications) == 1


def test_t09_duplicate_callback_different_payment_id_conflict_reconciliation(
    eco_inventory, enabled
):
    """Subsequent capture under a different payment ID flags conflict and queues refund record."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    # First payment captured
    assert send_ipn(client, booking, payment_id="PH-PRIMARY-CAPTURE", code="2").json()["status"] == "ok"

    # Second payment for the same booking arrives with different gateway transaction ID
    second_capture = send_ipn(client, booking, payment_id="PH-SECONDARY-CAPTURE", code="2")
    assert second_capture.status_code == 200
    assert second_capture.json()["status"] == "requires_reconciliation"

    # Re-sending the secondary capture is recognized as already_processed
    assert send_ipn(client, booking, payment_id="PH-SECONDARY-CAPTURE", code="2").json()["status"] == (
        "already_processed"
    )

    with Session(engine) as db:
        # A PaymentRefund record must be created for the duplicate amount
        refunds = db.scalars(select(PaymentRefund)).all()
        assert len(refunds) >= 1
        conflict_refund = [r for r in refunds if r.refund_reason == "Late or conflicting Eco capture"]
        assert len(conflict_refund) == 1
        assert conflict_refund[0].amount == Decimal(booking["total"])
        assert conflict_refund[0].refund_status == "pending"


# ==============================================================================
# 8. AMOUNT & CURRENCY TAMPERING GUARDS
# ==============================================================================


def test_t09_webhook_rejects_amount_tampering_underpayment(eco_inventory, enabled):
    """Underpayment attempt (even if signed) is rejected with HTTP 400."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    # Booking total is 3600.00; attempt to report 3500.00
    tampered_res = send_ipn(
        client,
        booking,
        payment_id="PH-UNDERPAY",
        code="2",
        amount="3500.00",
    )
    assert tampered_res.status_code == 400
    assert "Payment does not match the Eco reservation" in tampered_res.text


def test_t09_webhook_rejects_amount_tampering_overpayment(eco_inventory, enabled):
    """Overpayment amount mismatch is rejected with HTTP 400."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    tampered_res = send_ipn(
        client,
        booking,
        payment_id="PH-OVERPAY",
        code="2",
        amount="5000.00",
    )
    assert tampered_res.status_code == 400
    assert "Payment does not match the Eco reservation" in tampered_res.text


def test_t09_webhook_rejects_currency_tampering(eco_inventory, enabled):
    """Foreign currency tampering (e.g. USD instead of LKR) is rejected with HTTP 400."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    currency_res = send_ipn(
        client,
        booking,
        payment_id="PH-CURRENCY-TAMPER",
        code="2",
        currency="USD",
    )
    assert currency_res.status_code == 400
    assert "Payment does not match the Eco reservation" in currency_res.text


# ==============================================================================
# 9. MULTI-ENTITY CONSISTENCY & CAPACITY INVARIANTS
# ==============================================================================


def test_t09_multi_entity_consistency_after_successful_payment(eco_inventory, enabled):
    """Verify synchronized state across 6 entities upon successful online capture."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    booking_id = booking["id"]

    assert post_checkout(client, booking_id).status_code == 200
    assert send_ipn(client, booking, payment_id="PH-CONSISTENCY-01", code="2").status_code == 200

    with Session(engine) as db:
        # 1. EcoReservation
        res = db.get(EcoReservation, booking_id)
        assert res.status == "confirmed"
        assert res.payment_status == "paid"
        assert res.holds_capacity is True

        # 2. BookingReference
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == booking["reference"]
            )
        )
        assert ref.payment_status == "paid"

        # 3. PaymentTransaction
        tx = db.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id == ref.id
            )
        )
        assert tx.payment_status == "paid"
        assert tx.gateway_transaction_id == "PH-CONSISTENCY-01"
        assert tx.paid_at is not None
        assert tx.amount == Decimal(booking["total"])

        # 4. EcoAvailabilitySlot capacity invariant
        slot_row = db.get(EcoAvailabilitySlot, booking["slot_id"])
        assert slot_row.reserved_count == 2
        assert slot_row.capacity == 4

        # 5. BookingStatusHistory
        history = db.scalar(
            select(BookingStatusHistory).where(
                BookingStatusHistory.booking_reference_id == ref.id,
                BookingStatusHistory.notes == "online_payment_received",
            )
        )
        assert history is not None
        assert history.from_status == "confirmed"
        assert history.to_status == "confirmed"

        # 6. Notifications dispatched to traveler and provider
        notifs = db.scalars(
            select(Notification).where(
                Notification.notification_type == "reservation_online_payment_received"
            )
        ).all()
        assert len(notifs) >= 1
        traveler_notif = [n for n in notifs if n.user_id == 5]
        assert len(traveler_notif) == 1


def test_t09_cash_booking_cannot_initiate_online_checkout(eco_inventory, enabled):
    """Cash-confirmed bookings are strictly ineligible for online PayHere checkout."""
    client, actor, engine, day = eco_inventory
    sid = slot(client, actor, day)["id"]
    booking = reservation(client, actor, sid, 2).json()

    # Provider accepts booking and marks as cash
    assert action(client, actor, booking["id"], "accept").status_code == 200
    assert action(client, actor, booking["id"], "cash").status_code == 200
    actor["id"] = 5  # Switch back to traveler

    # Traveler attempts online checkout
    checkout_res = post_checkout(client, booking["id"])
    assert checkout_res.status_code == 409
    assert "Eco reservation is ineligible for online payment" in checkout_res.text


def test_t09_late_capture_on_cancelled_booking_triggers_conflict_refund(
    eco_inventory, enabled
):
    """Late IPN arrival after user cancellation preserves cancellation and triggers refund queue."""
    client, actor, engine, _ = eco_inventory
    booking = confirm_booking(eco_inventory)
    assert post_checkout(client, booking["id"]).status_code == 200

    # Traveler cancels the pending reservation before IPN arrives
    cancel_res = client.post(f"{RESERVATIONS}/{booking['id']}/cancel", json={})
    assert cancel_res.status_code == 200

    # Late IPN arrives for the cancelled booking
    late_ipn = send_ipn(client, booking, payment_id="PH-LATE-ARRIVAL", code="2")
    assert late_ipn.status_code == 200
    assert late_ipn.json()["status"] == "requires_reconciliation"

    with Session(engine) as db:
        # Booking remains cancelled
        res = db.get(EcoReservation, booking["id"])
        assert res.status == "cancelled"

        # Slot inventory was released and NOT re-acquired
        slot_row = db.get(EcoAvailabilitySlot, booking["slot_id"])
        assert slot_row.reserved_count == 0

        # Refund record created for traveler
        refund = db.scalar(
            select(PaymentRefund).where(
                PaymentRefund.refund_reason == "Late or conflicting Eco capture"
            )
        )
        assert refund is not None
        assert refund.refund_status == "pending"
        assert refund.amount == Decimal(booking["total"])
