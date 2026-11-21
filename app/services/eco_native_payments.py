"""Native Eco checkout and ledger transitions using the shared PayHere merchant."""
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.booking_transactions import CAPTURED, capture_status, executed_refund_status
from app.core.payhere import payhere_service
from app.models.eco_experience import EcoSeasonRestriction
from app.models.eco_reservation import (
    EcoAvailabilitySlot,
    EcoCancellationSnapshot,
    EcoRefundReconciliation,
    EcoReservation,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentRefund, PaymentTransaction
from app.services import eco_reservations as reservations
from app.services.payhere_bookings import capture_amount, checkout_response, lock_capture

CHECKOUT_WINDOW = timedelta(minutes=30)
CANCELLATION_REFUND_REASON = "Eco free-window cancellation"


def require_activation():
    if os.getenv("ECO_NATIVE_PAYMENTS_ENABLED") != "1":
        raise HTTPException(503, "Native Eco online payment is unavailable")
    require_merchant()


def require_merchant():
    if (
        not payhere_service.merchant_id
        or payhere_service.merchant_id == "TEST_MERCHANT_ID"
        or not payhere_service.merchant_secret
        or payhere_service.merchant_secret == "TEST_MERCHANT_SECRET"
    ):
        raise HTTPException(503, "Eco platform payment is unavailable")


def online_transactions(reference_id):
    tx = PaymentTransaction
    return select(tx).where(
        tx.booking_reference_id == reference_id,
        tx.gateway_name == "payhere",
        tx.payment_method == "online",
    )


def attempt_for(db, ref):
    return db.scalar(
        online_transactions(ref.id)
        .order_by(PaymentTransaction.id)
        .limit(1)
    )


def locked_reservation(db, query):
    result = db.execute(query).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    reservations.lock_schedule(db, result[1].eco_experience_id)
    result = db.execute(query.execution_options(populate_existing=True)).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    return result


def deadline(attempt, slot):
    return min(attempt.created_at + CHECKOUT_WINDOW, slot.starts_at)


def checkout(db, traveler, booking_id):
    query = reservations.reservation_query(traveler_id=traveler.id).where(
        EcoReservation.id == booking_id
    )
    row, slot, ref, _ = locked_reservation(db, query)
    require_activation()
    now = reservations.utc_now()
    if (
        row.status != "confirmed" or row.payment_status != "unpaid"
        or not row.holds_capacity or slot.starts_at <= now or row.total_price <= 0
    ):
        raise HTTPException(409, "Eco reservation is ineligible for online payment")
    listing = reservations.experience(db, slot.eco_experience_id, hold=True)
    reservations.eligible(db, listing.id, slot.starts_at, slot.ends_at, row.participants)
    reservations.require_slot_clearance(db, listing, slot)
    attempt = attempt_for(db, ref)
    if attempt is None:
        attempt = PaymentTransaction(
            booking_reference_id=ref.id,
            user_id=row.traveler_id,
            gateway_name="payhere",
            payment_method="online",
            amount=row.total_price,
            currency=row.currency,
            payment_status="pending",
            created_at=now,
        )
        db.add(attempt)
    elif attempt.payment_status != "pending" or deadline(attempt, slot) <= now:
        raise HTTPException(409, "The Eco payment attempt has closed")
    response = checkout_response(
        row.id, ref.booking_reference, "Eco reservation", attempt.amount,
        attempt.currency, traveler,
    )
    db.commit()
    return response


def payment_status(db, traveler, booking_id):
    result = db.execute(
        reservations.reservation_query(traveler_id=traveler.id).where(
            EcoReservation.id == booking_id
        )
    ).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    row, slot, ref, online = result
    attempt = attempt_for(db, ref)
    status = ref.payment_status
    if attempt is not None and status == "unpaid":
        status = attempt.payment_status
        if status == "pending" and deadline(attempt, slot) <= reservations.utc_now():
            status = "expired"
    return {
        "booking_id": row.id, "booking_status": row.status,
        "payment_method": "online" if online else "pay_at_location",
        "payment_status": status, "total_price": row.total_price,
        "currency": row.currency,
    }


def capture(db, reference_id, payment_id, raw_amount, currency, gateway_status):
    """Handle an already authenticated shared PayHere callback."""
    require_merchant()
    amount = capture_amount(raw_amount)
    if not isinstance(payment_id, str) or payment_id.strip() != payment_id:
        raise HTTPException(400, "Invalid payment identifier")
    if gateway_status == "2":
        lock_capture(db, payment_id, reference_id)
        if db.scalar(select(PaymentTransaction.id).where(
            PaymentTransaction.gateway_name == "payhere",
            func.lower(PaymentTransaction.gateway_transaction_id) == payment_id.lower(),
            PaymentTransaction.booking_reference_id != reference_id,
            PaymentTransaction.payment_status.in_((*CAPTURED, "refunded")),
        ).limit(1)) is not None:
            raise HTTPException(409, "Payment identifier belongs to another order")
    row, slot, ref, _ = locked_reservation(
        db, reservations.reservation_query(reference_id=reference_id)
    )
    if amount != row.total_price or currency != row.currency:
        raise HTTPException(400, "Payment does not match the Eco reservation")
    prior = db.scalar(
        online_transactions(ref.id)
        .where(func.lower(PaymentTransaction.gateway_transaction_id) == payment_id.lower())
        .limit(1)
    ) if payment_id else None
    if prior is not None:
        return {"status": "already_processed"}
    attempt = attempt_for(db, ref)
    if attempt is None:
        raise HTTPException(409, "No Eco checkout exists for this order")
    if attempt.user_id != row.traveler_id or attempt.amount != amount or attempt.currency != currency:
        raise HTTPException(409, "Eco checkout does not match this payment")
    if gateway_status != "2":
        if gateway_status in ("-1", "-2") and attempt.payment_status == "pending":
            if row.payment_status == "unpaid" and ref.payment_status == "unpaid":
                attempt.payment_status = "failed"
                notify(db, row, ref, "online_payment_failed")
                db.commit()
        return {"status": "ignored"}
    now = reservations.utc_now()
    applicable = (
        row.status == "confirmed" and row.payment_status == "unpaid"
        and row.holds_capacity and attempt.payment_status == "pending"
        and now < deadline(attempt, slot)
    )
    if applicable:
        try:
            listing = reservations.experience(db, slot.eco_experience_id, hold=True)
            reservations.eligible(
                db, listing.id, slot.starts_at, slot.ends_at, row.participants
            )
            reservations.require_slot_clearance(db, listing, slot)
        except HTTPException as exc:
            if exc.status_code not in (404, 409):
                raise
            applicable = False
    if attempt.gateway_transaction_id is None:
        recorded = attempt
    else:
        recorded = PaymentTransaction(
            booking_reference_id=ref.id, user_id=row.traveler_id,
            gateway_name="payhere", payment_method="online", amount=amount,
            currency=currency,
        )
        db.add(recorded)
    recorded.gateway_transaction_id = payment_id
    recorded.payment_status = capture_status(db)
    recorded.paid_at = now
    db.flush()
    if applicable:
        row.payment_status = ref.payment_status = "paid"
        notify(db, row, ref, "online_payment_received")
        result = "ok"
    else:
        db.add(PaymentRefund(
            payment_transaction_id=recorded.id, amount=amount, currency=currency,
            refund_status="pending", refund_reason="Late or conflicting Eco capture",
        ))
        if ref.payment_status == "unpaid":
            ref.payment_status = "refund_pending"
        notify(db, row, ref, "online_payment_requires_reconciliation")
        result = "requires_reconciliation"
    db.commit()
    return {"status": result}


def notify(db, row, ref, event, *, actor=None, previous_status=None):
    provider = db.get(ProviderProfile, ref.provider_profile_id)
    reservations.save_event(
        db, ref, previous_status or row.status, row.status, actor, row.traveler_id, event
    )
    if provider is not None:
        reservations.notify_reservation_actor(db, provider.user_id, ref, event)


def cancel_paid(db, traveler, booking_id):
    row, slot, ref, _ = locked_reservation(
        db, reservations.reservation_query(traveler_id=traveler.id).where(
            EcoReservation.id == booking_id
        )
    )
    if os.getenv("ECO_NATIVE_PAID_CANCELLATION_ENABLED") != "1":
        raise HTTPException(503, "Eco paid cancellation is unavailable")
    if row.status == "cancelled" and row.payment_status != "unpaid":
        return reservations.reservation_result(row, slot, ref, True)
    now = reservations.utc_now()
    if (
        row.status != "confirmed" or row.payment_status != "paid" or not row.holds_capacity
        or slot.starts_at <= now
    ):
        raise HTTPException(409, "Paid cancellation is unavailable for this reservation")
    percentage = 100
    if slot.starts_at - now < timedelta(hours=row.free_cancellation_hours):
        snapshot = None
        if os.getenv("ECO_NATIVE_POLICY_SNAPSHOTS_ENABLED") == "1":
            snapshot = db.get(EcoCancellationSnapshot, row.id)
        if snapshot is None:
            raise HTTPException(409, "Paid cancellation is outside the free cancellation policy")
        percentage = snapshot.late_refund_percent
    cancel_held_reservation(
        db, row, slot, ref, traveler.id, now, online=True, percentage=percentage
    )
    result = reservations.reservation_result(row, slot, ref, True)
    db.commit()
    return result


def qualifying_capture(db, row, ref):
    payment = db.scalar(
        online_transactions(ref.id)
        .where(
            PaymentTransaction.payment_status.in_(CAPTURED),
            PaymentTransaction.gateway_transaction_id.is_not(None),
            ~select(PaymentRefund.id)
            .where(PaymentRefund.payment_transaction_id == PaymentTransaction.id)
            .exists(),
        )
        .order_by(PaymentTransaction.id)
        .limit(1)
    )
    if payment is None or payment.amount != row.total_price or payment.currency != row.currency:
        raise HTTPException(409, "No qualifying captured payment found")
    return payment


def cancel_held_reservation(db, row, slot, ref, actor_id, now, *, online, percentage=100):
    old_status = row.status
    payment = qualifying_capture(db, row, ref) if online else None
    reservations.release_capacity(db, slot.id, row.capacity_held)
    row.status = ref.booking_status = "cancelled"
    row.cancelled_at = now
    row.holds_capacity = False
    event = "cancelled"
    if payment is not None:
        amount = (payment.amount * percentage / 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if amount > 0:
            row.payment_status = ref.payment_status = "refund_pending"
            db.add(PaymentRefund(
                payment_transaction_id=payment.id, amount=amount,
                currency=payment.currency, refund_status="pending",
                refund_reason=CANCELLATION_REFUND_REASON,
            ))
            event = "cancelled_refund_requested"
        else:
            row.payment_status = "no_refund_due"
            event = "cancelled_no_refund_due"
    notify(db, row, ref, event, actor=actor_id, previous_status=old_status)


def cancel_restricted_slots(db, admin, experience_id, restriction_id):
    """Apply an existing active restriction to upcoming native reservations."""
    reservations.lock_schedule(db, experience_id)
    restriction = db.scalar(select(EcoSeasonRestriction).where(
        EcoSeasonRestriction.id == restriction_id,
        EcoSeasonRestriction.eco_experience_id == experience_id,
        EcoSeasonRestriction.is_active.is_(True),
    ))
    if restriction is None:
        raise HTTPException(404, "Active Eco restriction not found")
    now = reservations.utc_now()
    slot = EcoAvailabilitySlot
    slots = db.scalars(select(slot).where(
        slot.eco_experience_id == experience_id,
        slot.starts_at > now,
        slot.starts_at < reservations.local_midnight(restriction.end_date + timedelta(days=1)),
        slot.ends_at >= reservations.local_midnight(restriction.start_date),
    )).all()
    closed = cancelled = 0
    try:
        for affected in slots:
            rows = db.execute(reservations.reservation_query(
                experience_id=experience_id
            ).where(
                EcoReservation.slot_id == affected.id,
                EcoReservation.holds_capacity.is_(True),
                EcoReservation.status.in_(("pending", "confirmed")),
            )).all()
            if affected.is_open:
                affected.is_open = False
                closed += 1
            for row, _, ref, online in rows:
                paid = row.payment_status == "paid"
                if paid and (
                    not online or os.getenv("ECO_NATIVE_PAID_CANCELLATION_ENABLED") != "1"
                ):
                    raise HTTPException(409, "Paid reservation requires manual cancellation")
                cancel_held_reservation(db, row, affected, ref, admin.id, now, online=paid)
                cancelled += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"slots_closed": closed, "reservations_cancelled": cancelled}


def locked_refund(db, refund_id):
    reference_id = db.scalar(
        select(BookingReference.id)
        .join(PaymentTransaction, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(PaymentRefund, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
        .where(
            BookingReference.module_type == reservations.MODULE,
            PaymentTransaction.gateway_name == "payhere",
            PaymentTransaction.payment_method == "online",
            PaymentRefund.id == refund_id,
        )
    )
    if reference_id is None:
        raise HTTPException(404, "Eco refund not found")
    row, _, ref, _ = locked_reservation(
        db, reservations.reservation_query(reference_id=reference_id)
    )
    refund = db.get(PaymentRefund, refund_id, populate_existing=True)
    return row, ref, refund


def decide_refund(db, admin, refund_id, decision):
    row, ref, refund = locked_refund(db, refund_id)
    if refund.refund_status == decision:
        return refund
    if refund.refund_status != "pending":
        raise HTTPException(409, "Eco refund has already been decided")
    refund.refund_status = decision
    refund.processed_by = admin.id
    if (
        decision == "rejected" and refund.refund_reason == CANCELLATION_REFUND_REASON
        and row.status == "cancelled" and row.payment_status == "refund_pending"
    ):
        row.payment_status = ref.payment_status = "refund_rejected"
    notify(db, row, ref, f"refund_{decision}", actor=admin.id)
    db.commit()
    return refund


def reconcile_refund(db, admin, refund_id, evidence):
    """Record Admin-verified merchant evidence without invoking the gateway."""
    if os.getenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED") != "1":
        raise HTTPException(503, "Eco refund reconciliation is unavailable")
    row, ref, refund = locked_refund(db, refund_id)
    tx = db.get(PaymentTransaction, refund.payment_transaction_id, populate_existing=True)
    if (
        tx.user_id != row.traveler_id or tx.gateway_transaction_id != evidence.capture_reference
        or refund.amount != evidence.amount or refund.currency != evidence.currency
        or tx.currency != evidence.currency or refund.amount > tx.amount
    ):
        raise HTTPException(409, "Merchant evidence does not match the Eco refund")
    review = db.get(EcoRefundReconciliation, refund.id, populate_existing=True)
    old = review.state if review else refund.refund_status
    new = evidence.state
    if review is not None and old == new and review.gateway_refund_reference == evidence.refund_reference:
        return {"id": refund.id, "state": new}
    allowed = {
        "approved": {"processing", "reconciliation_required"},
        "processing": {"succeeded", "failed", "reconciliation_required"},
        "reconciliation_required": {"processing", "succeeded", "failed"},
    }
    if (
        new not in allowed.get(old, set()) or refund.refund_status != "approved"
        or tx.payment_status not in CAPTURED
        or (new == "succeeded") != (evidence.refund_reference is not None)
    ):
        raise HTTPException(409, "Invalid Eco refund reconciliation transition")
    if review is None:
        review = EcoRefundReconciliation(refund_id=refund.id)
        db.add(review)
    review.state = new
    review.gateway_refund_reference = evidence.refund_reference
    if new == "succeeded":
        refund.refund_status = executed_refund_status(db)
    elif new == "failed":
        refund.refund_status = "failed"
    if refund.refund_reason == CANCELLATION_REFUND_REASON and row.status == "cancelled":
        if new == "succeeded":
            row.payment_status = (
                "refunded" if refund.amount == tx.amount else "partially_refunded"
            )
            ref.payment_status = row.payment_status
        elif new == "failed":
            row.payment_status = ref.payment_status = "refund_failed"
    refund.processed_by = admin.id
    refund.processed_at = reservations.utc_now()
    notify(db, row, ref, f"refund_{new}", actor=admin.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Merchant refund reference already recorded") from exc
    return {"id": refund.id, "state": new}


def refund_statuses(db, traveler, booking_id):
    result = db.execute(
        reservations.reservation_query(traveler_id=traveler.id).where(
            EcoReservation.id == booking_id
        )
    ).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    tx_ids = online_transactions(result[2].id).with_only_columns(PaymentTransaction.id)
    refunds = db.scalars(
        select(PaymentRefund)
        .where(PaymentRefund.payment_transaction_id.in_(tx_ids))
        .order_by(PaymentRefund.id)
    ).all()
    if os.getenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED") != "1":
        return refunds
    states = dict(db.execute(select(
        EcoRefundReconciliation.refund_id, EcoRefundReconciliation.state
    ).where(EcoRefundReconciliation.refund_id.in_([item.id for item in refunds]))).all())
    return [
        {"id": item.id, "amount": item.amount, "currency": item.currency,
         "refund_status": states.get(item.id, item.refund_status)}
        for item in refunds
    ]
