from datetime import UTC

from fastapi import HTTPException
from sqlalchemy import case, select, true

from app.core.booking_transactions import CAPTURED, capture_status
from app.core.listing_sync import record_booking_status_change
from app.core.notification_service import create_notification
from app.core.publication import approved_provider
from app.models.shared import PaymentRefund, PaymentTransaction
from app.models.tour_guide import TourGuide, TourGuideBooking
from app.services.guide_booking import expire_hold, lock_guide, require_availability
from app.services.payhere_bookings import (
    capture_amount,
    checkout_response,
    lock_capture,
)
from app.services.provider_reservations import GUIDE, reservations, utc_now


def guide_checkout(db, booking_id, traveler):
    row, ref = owned_checkout(db, booking_id, traveler)
    now = utc_now()
    if expire_hold(db, row, ref, now):
        db.commit()
        raise HTTPException(409, "Tour checkout hold has expired")
    if not (
        row.payment_method == "online"
        and row.booking_status == "pending"
        and row.payment_status in ("pending", "failed")
        and row.expires_at is not None
        and row.expires_at > now
        and row.start_datetime > now
    ):
        raise HTTPException(409, "Tour reservation is not awaiting online payment")
    ensure_reference_amount(row, ref)
    if not guide_is_bookable(db, row):
        raise HTTPException(409, "The guide is unavailable for checkout")
    amount = capture_amount(row.total_price)
    attempts = guide_transactions(ref)
    tx = db.scalar(attempts.order_by(PaymentTransaction.id.desc()).limit(1))
    if tx is None:
        db.add(
            PaymentTransaction(
                booking_reference_id=ref.id,
                user_id=row.traveler_id,
                amount=amount,
                currency=row.currency,
                gateway_name="payhere",
                payment_method="online",
                payment_status="pending",
            )
        )
    elif tx.payment_status in CAPTURED:
        raise HTTPException(409, "Tour payment already captured")
    elif tx.amount != amount or tx.currency != row.currency:
        raise HTTPException(409, "Existing checkout amount does not match reservation")
    elif tx.payment_status == "failed":
        tx.payment_status = "pending"
    row.payment_status = ref.payment_status = "pending"
    result = checkout_response(
        row.id,
        ref.booking_reference,
        f"Tour Guide reservation #{row.id}",
        amount,
        row.currency,
        traveler,
    )
    db.commit()
    return result


def owned_checkout(db, booking_id, traveler):
    query = reservations(db, GUIDE, user_id=traveler.id).where(
        TourGuideBooking.id == booking_id
    )
    result = db.execute(query).first()
    if result is None:
        raise HTTPException(404, "Owned tour reservation not found")
    lock_guide(db, result[0].tour_guide_id)
    refreshed = db.execute(query.execution_options(populate_existing=True)).first()
    if refreshed is None:
        raise HTTPException(404, "Owned tour reservation not found")
    return refreshed


def guide_payment_status(db, booking_id, traveler):
    row, ref = owned_checkout(db, booking_id, traveler)
    expired = expire_hold(db, row, ref, utc_now())
    tx = db.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.booking_reference_id == ref.id,
            PaymentTransaction.user_id == row.traveler_id,
        )
        .order_by(
            case((PaymentTransaction.paid_at.is_(None), 1), else_=0),
            PaymentTransaction.paid_at,
            PaymentTransaction.id,
        )
        .limit(1)
    )
    state = row.booking_status
    if (
        state == "pending"
        and row.payment_method == "online"
        and row.expires_at is not None
    ):
        state = "awaiting_payment"
    result = {
        "booking_id": row.id,
        "booking_status": state,
        "payment_status": row.payment_status,
        "payment_method": row.payment_method,
        "total_price": row.total_price,
        "currency": row.currency,
        "payment_expires_at": utc(row.expires_at),
        "paid_at": utc(tx.paid_at) if tx else None,
        "transaction_id": tx.id if tx else None,
    }
    if expired:
        db.commit()
    return result


def utc(value):
    return value.replace(tzinfo=UTC) if value is not None else None


def guide_transactions(ref):
    return select(PaymentTransaction).where(
        PaymentTransaction.booking_reference_id == ref.id,
        PaymentTransaction.user_id == ref.user_id,
        PaymentTransaction.gateway_name == "payhere",
        PaymentTransaction.payment_method == "online",
    )


def ensure_reference_amount(row, ref):
    if row.total_price != ref.total_price or row.currency != ref.currency:
        raise HTTPException(409, "Tour reservation and payment reference do not match")


def guide_is_bookable(db, row):
    listed = (
        db.scalar(
            select(TourGuide.id)
            .with_hint(TourGuide, "WITH (HOLDLOCK)", "mssql")
            .where(
                TourGuide.id == row.tour_guide_id,
                TourGuide.approval_status == "approved",
                TourGuide.is_active == true(),
                TourGuide.is_available == true(),
                approved_provider(TourGuide.provider_profile_id, hold=True),
            )
        )
        is not None
    )
    if not listed:
        return False
    try:
        require_availability(
            db, row.tour_guide_id, row.start_datetime, row.end_datetime
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return False
    return True


def handle_guide_callback(db, ref, payment_id, raw_amount, currency, status_code):
    if status_code not in ("0", "2", "-1", "-2"):
        raise HTTPException(400, "Unsupported tour payment status")
    amount = capture_amount(raw_amount)
    if status_code == "2":
        lock_capture(db, payment_id, ref.id)

    row = db.get(TourGuideBooking, ref.target_booking_id)
    if row is None:
        raise HTTPException(404, "Tour reservation not found")
    lock_guide(db, row.tour_guide_id)
    db.refresh(row)
    db.refresh(ref)
    guide = db.get(TourGuide, row.tour_guide_id)
    if (
        guide is None
        or guide.provider_profile_id != ref.provider_profile_id
        or row.traveler_id != ref.user_id
        or row.payment_method != "online"
    ):
        raise HTTPException(409, "Payment does not match the tour reservation")
    ensure_reference_amount(row, ref)
    if amount != row.total_price or currency != row.currency:
        raise HTTPException(
            400, "Payment amount or currency mismatch with tour reservation"
        )

    attempts = guide_transactions(ref)
    previous = db.scalars(attempts.order_by(PaymentTransaction.id.desc())).all()
    if not previous:
        raise HTTPException(409, "Tour checkout has not been initiated")
    if any(tx.amount != amount or tx.currency != currency for tx in previous):
        raise HTTPException(409, "Recorded tour checkout does not match the callback")
    receipts = [tx for tx in previous if tx.payment_status in CAPTURED]
    if status_code == "2" and any(
        (tx.gateway_transaction_id or "").casefold() == payment_id.casefold()
        for tx in receipts
    ):
        return {"status": "already_processed"}
    now = utc_now()
    expire_hold(db, row, ref, now)
    awaiting_payment = (
        row.booking_status == "pending"
        and row.payment_status in ("pending", "failed")
        and row.expires_at is not None
        and row.expires_at > now
        and row.start_datetime > now
    )
    if status_code != "2":
        if receipts:
            return {"status": "already_processed"}
        if (
            status_code in ("-1", "-2") and awaiting_payment
            and previous[0].payment_status != "failed"
        ):
            previous[0].payment_status = "failed"
            row.payment_status = ref.payment_status = "failed"
            record_booking_status_change(
                db, ref.id, "awaiting_payment", "awaiting_payment", None,
                notes="Tour payment attempt failed",
            )
        db.commit()
        return {"status": "ok", "payment_status": previous[0].payment_status}

    if receipts:
        tx = PaymentTransaction(
            booking_reference_id=ref.id,
            user_id=row.traveler_id,
            amount=amount,
            currency=currency,
            gateway_name="payhere",
            payment_method="online",
        )
        db.add(tx)
    else:
        tx = previous[0]
    tx.payment_status = capture_status(db)
    tx.gateway_transaction_id = payment_id
    tx.paid_at = now
    tx.gross_amount = amount
    reconcile = not awaiting_payment or bool(receipts) or not guide_is_bookable(db, row)
    if reconcile:
        db.flush()
        db.add(
            PaymentRefund(
                payment_transaction_id=tx.id,
                amount=amount,
                currency=currency,
                refund_status="pending",
                refund_reason="Tour online capture requires reconciliation after expiry, cancellation or an earlier capture.",
            )
        )
        if not receipts:
            row.payment_status = ref.payment_status = "refund_pending"
            if awaiting_payment:
                row.booking_status = ref.booking_status = "cancelled"
                row.cancelled_at = now
        record_booking_status_change(
            db,
            ref.id,
            "awaiting_payment" if awaiting_payment else row.booking_status,
            row.booking_status,
            None,
            notes="Tour capture queued for refund reconciliation",
        )
    else:
        row.booking_status = ref.booking_status = "confirmed"
        row.payment_status = ref.payment_status = "paid"
        record_booking_status_change(
            db,
            ref.id,
            "awaiting_payment",
            "confirmed",
            None,
            notes="Tour online payment verified",
        )
    create_notification(
        db,
        row.traveler_id,
        title=(
            "Tour payment requires reconciliation"
            if reconcile
            else "Tour booking confirmed"
        ),
        message=f"Payment received for tour reservation {ref.booking_reference}.",
        notification_type="guide_payment_reconciliation"
        if reconcile
        else "guide_payment_confirmed",
    )
    db.commit()
    return {
        "status": "requires_reconciliation" if reconcile else "ok",
        "payment_status": row.payment_status,
    }
