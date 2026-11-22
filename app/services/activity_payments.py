"""
CeylonGo Module 03: Activities & Experiences
PayHere Payment Gateway Integration Service (A09).

Encapsulates:
- PayHere online checkout preparation for activity bookings.
- Sandbox and mock gateway adapter compatibility.
- Instant Payment Notification (IPN) callback handler with constant-time signature verification.
- Concurrency locks and idempotent duplicate callback replay protection.
- State machine management (success, failure, cancellation, pending, late captures).
- Financial aggregation abstractions for Provider Payouts (B14) and Admin Payment Monitoring (C05).
- Safe audit logging ensuring zero credentials, tokens, or merchant secrets are exposed.
"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.booking_transactions import (
    CAPTURED,
    EXECUTED_REFUNDS,
    RESERVED_REFUNDS,
    capture_status,
    lock_booking_resource,
)
from app.core.listing_sync import (
    record_booking_status_change,
    sync_activity_booking_reference,
)
from app.core.notification_service import create_notification
from app.core.payhere import payhere_service
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.user import User
from app.schemas.payment import PaymentInitiateResponse, PaymentStatusResponse
from app.services.payhere_bookings import capture_amount, lock_capture

logger = logging.getLogger("ceylongo.activity_payments")

PLATFORM_COMMISSION_RATE = Decimal("10.00")  # 10% platform fee


def sanitize_payment_log_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Sanitize payment log dictionary to ensure zero secrets, passwords,
    or sensitive credentials are ever written to application logs.
    """
    sensitive_keys = {
        "merchant_secret",
        "secret",
        "password",
        "card_no",
        "card_number",
        "cvv",
        "cvv2",
        "token",
        "access_token",
        "md5sig",
    }
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if any(s in k.lower() for s in sensitive_keys):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized


def validate_payhere_activity_config() -> dict[str, Any]:
    """
    Validate PayHere environment configuration for activity payments.
    Identifies whether the system is running against live sandbox, production,
    or mock test credentials without revealing secrets.
    """
    config_info = payhere_service.validate_config()
    is_live_sandbox = (
        config_info["is_sandbox"]
        and config_info["has_credentials"]
        and not config_info["is_mock_or_test"]
    )
    is_live_production = (
        config_info["is_production"]
        and config_info["has_credentials"]
        and not config_info["is_mock_or_test"]
    )

    if is_live_production:
        gateway_status = "PRODUCTION_LIVE"
    elif is_live_sandbox:
        gateway_status = "SANDBOX_VERIFIED"
    elif config_info["is_mock_or_test"]:
        gateway_status = "SANDBOX_MOCK_OFFLINE"
    else:
        gateway_status = "NOT_CONFIGURED"

    return {
        "mode": config_info["mode"],
        "merchant_id": config_info["merchant_id"],
        "gateway_status": gateway_status,
        "is_production": config_info["is_production"],
        "is_sandbox": config_info["is_sandbox"],
        "is_mock_or_test": config_info["is_mock_or_test"],
        "has_credentials": config_info["has_credentials"],
        "checkout_url": payhere_service.checkout_url,
    }


# ==========================================================
# 1. PAYHERE ONLINE CHECKOUT PREPARATION (A09)
# ==========================================================

def prepare_activity_online_payment(
    db: Session,
    booking_id: int,
    current_user: User,
) -> PaymentInitiateResponse:
    """
    Prepare PayHere checkout session for an activity booking.
    Validates ownership, slot status, amount, and generates MD5 checkout hash.
    """
    booking = db.query(ActivityBooking).filter(ActivityBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity booking not found",
        )

    # Authorization: traveler ownership or admin
    if booking.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate payment for your own bookings",
        )

    # Serialize resource updates
    lock_booking_resource(db, f"activity_booking_{booking.id}")
    db.refresh(booking)

    # Status validation: cannot pay for cancelled, rejected, or completed bookings
    if booking.booking_status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate payment for a cancelled booking",
        )
    if booking.booking_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate payment for a rejected booking",
        )
    if booking.booking_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate payment for an already completed booking",
        )

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking has already been paid",
        )
    if booking.payment_status in ("refund_pending", "refunded"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking payment is being refunded or has already been refunded",
        )

    # Validate slot is still active and in the future
    slot = db.query(ActivitySlot).filter(ActivitySlot.id == booking.activity_slot_id).first()
    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated activity slot not found",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if slot.starts_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate payment for a past activity slot",
        )

    # Currency and amount validation
    if (booking.currency or "LKR") != "LKR":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only LKR currency is currently supported for PayHere online checkout",
        )

    amount = capture_amount(booking.total_price)
    if amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount must be greater than zero",
        )

    # Fetch associated activity
    activity = db.query(Activity).filter(Activity.id == slot.activity_id).first()
    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated activity not found",
        )

    # Mark booking payment method as online
    booking.payment_method = "online"

    # Synchronize or retrieve central booking reference
    booking_ref = sync_activity_booking_reference(db, booking, activity)
    if not booking_ref:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to synchronize booking reference for payment",
        )

    # Upsert pending PaymentTransaction
    tx = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.booking_reference_id == booking_ref.id,
            PaymentTransaction.user_id == booking.user_id,
            PaymentTransaction.gateway_name == "payhere",
            PaymentTransaction.payment_status == "pending",
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if not tx:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.user_id,
            gateway_name="payhere",
            amount=amount,
            currency=booking.currency or "LKR",
            payment_status="pending",
            payment_method="online",
        )
        db.add(tx)
    else:
        tx.amount = amount
        tx.currency = booking.currency or "LKR"

    db.commit()

    order_id = booking_ref.booking_reference
    checkout_hash = payhere_service.generate_checkout_hash(
        order_id=order_id,
        amount=amount,
        currency=booking.currency or "LKR",
    )

    name_parts = (current_user.full_name or "Traveler").strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"

    items_description = f"Activity: {activity.title} ({booking.participants} participant{'s' if booking.participants > 1 else ''})"

    logger.info(
        "PayHere checkout initiated: booking_id=%s, order_id=%s, amount=%s, currency=%s",
        booking.id,
        order_id,
        amount,
        booking.currency or "LKR",
    )

    return PaymentInitiateResponse(
        booking_id=booking.id,
        booking_reference=order_id,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        order_id=order_id,
        items=items_description,
        amount=amount,
        currency=booking.currency or "LKR",
        hash=checkout_hash,
        first_name=first_name,
        last_name=last_name,
        email=current_user.email,
        phone=None,
        return_url=payhere_service.return_url,
        cancel_url=payhere_service.cancel_url,
        notify_url=payhere_service.notify_url,
    )


# ==========================================================
# 2. PAYHERE IPN CALLBACK PROCESSING (A09)
# ==========================================================

def handle_activity_payhere_callback(
    db: Session,
    form_data: dict[str, str],
    booking_ref: BookingReference,
) -> dict[str, Any]:
    """
    Process validated PayHere Instant Payment Notification (IPN) callback for activities.
    Applies concurrency locks, verifies amount/currency, handles idempotency,
    and updates booking, transaction, status history, and in-app notifications.
    """
    payment_id = (form_data.get("payment_id") or "").strip()
    payhere_amount = (form_data.get("payhere_amount") or "").strip()
    payhere_currency = (form_data.get("payhere_currency") or "").strip()
    status_code = (form_data.get("status_code") or "").strip()

    # Capture lock to serialize gateway transaction ID
    if status_code == "2":
        lock_capture(db, payment_id, booking_ref.id)

    # Concurrency lock on activity booking
    lock_booking_resource(db, f"activity_booking_{booking_ref.target_booking_id}")

    booking = (
        db.query(ActivityBooking)
        .filter(ActivityBooking.id == booking_ref.target_booking_id)
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated activity booking not found",
        )

    db.refresh(booking)
    db.refresh(booking_ref)

    # Amount and currency validation
    received_amt = capture_amount(payhere_amount)
    if (
        received_amt != booking.total_price
        or payhere_currency != (booking.currency or "LKR")
    ):
        logger.warning(
            "Payment amount/currency mismatch: received=%s %s, expected=%s %s",
            received_amt,
            payhere_currency,
            booking.total_price,
            booking.currency,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount or currency mismatch with booking record",
        )

    # Verify traveler isolation
    if booking_ref.user_id != booking.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment checkout record does not match booking ownership",
        )

    # Idempotent replay protection
    if booking.payment_status in ("paid", "refund_pending", "refunded"):
        logger.info(
            "Idempotent PayHere callback ignored: booking_id=%s is already %s",
            booking.id,
            booking.payment_status,
        )
        return {"status": "already_processed", "payment_status": booking.payment_status}

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Find or create PaymentTransaction
    tx = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.booking_reference_id == booking_ref.id,
            PaymentTransaction.gateway_name == "payhere",
            PaymentTransaction.user_id == booking.user_id,
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if not tx:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.user_id,
            gateway_name="payhere",
            payment_method="online",
            amount=received_amt,
            currency=payhere_currency,
        )
        db.add(tx)
        db.flush()

    tx.gateway_transaction_id = payment_id

    # PayHere Status Codes:
    # '2' = Success (Captured)
    # '0' = Pending
    # '-1' = Canceled
    # '-2' = Failed
    if status_code == "2":
        # Calculate 10% platform commission and 90% provider payable
        commission_rate = PLATFORM_COMMISSION_RATE
        commission_amount = (received_amt * (commission_rate / Decimal("100"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        provider_payable_amount = received_amt - commission_amount

        tx.payment_status = capture_status(db)
        tx.paid_at = now_utc
        tx.gross_amount = received_amt
        tx.commission_rate = commission_rate
        tx.commission_amount = commission_amount
        tx.provider_payable_amount = provider_payable_amount

        # Out-of-order / Late payment verification:
        # If booking was cancelled or rejected before online payment arrived,
        # flag for reconciliation and refund. DO NOT reactivate cancelled booking.
        if booking.booking_status in ("cancelled", "rejected"):
            booking.payment_status = "refund_pending"
            booking_ref.payment_status = "refund_pending"
            booking_ref.booking_status = booking.booking_status

            existing_refund = (
                db.query(PaymentRefund)
                .filter(
                    PaymentRefund.payment_transaction_id == tx.id,
                    PaymentRefund.refund_status.in_(RESERVED_REFUNDS),
                )
                .first()
            )
            if not existing_refund:
                refund = PaymentRefund(
                    payment_transaction_id=tx.id,
                    amount=received_amt,
                    currency=payhere_currency,
                    refund_reason=(
                        f"Late online payment received for {booking.booking_status} "
                        f"activity booking #{booking.id}. Order: {booking_ref.booking_reference}. "
                        "Flagged for reconciliation and refund."
                    ),
                    refund_status="pending",
                )
                db.add(refund)

            record_booking_status_change(
                db=db,
                booking_reference_id=booking_ref.id,
                from_status=booking.booking_status,
                to_status=booking.booking_status,
                changed_by=None,
                notes=(
                    f"Late PayHere payment captured for {booking.booking_status} booking "
                    f"(Payment ID: {payment_id}). Queued for refund."
                ),
            )

            create_notification(
                db=db,
                user_id=booking.user_id,
                title="Payment Received on Inactive Booking",
                message=(
                    f"We received your payment of {payhere_amount} {payhere_currency} for activity "
                    f"booking #{booking.id}, but the reservation was {booking.booking_status}. "
                    "Your payment has been queued for reconciliation and refund."
                ),
                notification_type="payment_flagged_for_refund",
                link_url=f"/activity-bookings/{booking.id}",
            )

            db.commit()
            logger.warning(
                "Late payment on %s activity booking #%s. Flagged for refund.",
                booking.booking_status,
                booking.id,
            )
            return {
                "status": "requires_reconciliation",
                "payment_status": "refund_pending",
            }

        # Valid confirmed active booking
        old_status = booking.booking_status
        booking.payment_status = "paid"
        booking.payment_method = "online"
        booking.booking_status = "confirmed"

        booking_ref.booking_status = "confirmed"
        booking_ref.payment_status = "paid"

        record_booking_status_change(
            db=db,
            booking_reference_id=booking_ref.id,
            from_status=old_status,
            to_status="confirmed",
            changed_by=None,
            notes=f"PayHere online payment confirmed (Payment ID: {payment_id})",
        )

        # Notify traveler
        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Activity Booking Confirmed!",
            message=(
                f"Your online payment of {payhere_amount} {payhere_currency} was confirmed. "
                f"Activity booking #{booking.id} is confirmed."
            ),
            notification_type="payment_success",
            link_url=f"/activity-bookings/{booking.id}",
        )

        # Notify activity provider
        slot = db.query(ActivitySlot).filter(ActivitySlot.id == booking.activity_slot_id).first()
        activity = (
            db.query(Activity).filter(Activity.id == slot.activity_id).first()
            if slot
            else None
        )
        provider = (
            db.query(ProviderProfile).filter(ProviderProfile.id == activity.provider_profile_id).first()
            if activity
            else None
        )
        if provider and provider.user_id:
            create_notification(
                db=db,
                user_id=provider.user_id,
                title="Activity Booking Payment Received",
                message=(
                    f"Traveler paid {payhere_amount} {payhere_currency} online for activity "
                    f"'{activity.title}' (Booking #{booking.id}). Booking is confirmed."
                ),
                notification_type="payment_received",
                link_url=f"/provider/activities/bookings/{booking.id}",
            )

        db.commit()
        public_status = "paid" if tx.payment_status in CAPTURED else tx.payment_status
        logger.info(
            "PayHere payment captured: booking_id=%s, payment_id=%s, status=%s",
            booking.id,
            payment_id,
            public_status,
        )
        return {"status": "ok", "payment_status": public_status}

    elif status_code in ("-1", "-2"):
        tx.payment_status = "failed"
        booking.payment_status = "failed"
        booking_ref.payment_status = "failed"

        record_booking_status_change(
            db=db,
            booking_reference_id=booking_ref.id,
            from_status=booking.booking_status,
            to_status=booking.booking_status,
            changed_by=None,
            notes=f"PayHere online payment failed (status_code={status_code}, Payment ID: {payment_id})",
        )

        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Activity Online Payment Failed",
            message=(
                f"Payment for activity booking #{booking.id} was unsuccessful. "
                "You may retry your online checkout."
            ),
            notification_type="payment_failed",
            link_url=f"/activity-bookings/{booking.id}",
        )

        db.commit()
        logger.info("PayHere payment failed for booking_id=%s, code=%s", booking.id, status_code)
        return {"status": "ok", "payment_status": "failed"}

    elif status_code == "0":
        tx.payment_status = "pending"
        booking.payment_status = "pending"
        booking_ref.payment_status = "pending"
        db.commit()
        logger.info("PayHere payment marked pending for booking_id=%s", booking.id)
        return {"status": "ok", "payment_status": "pending"}

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported PayHere payment status_code '{status_code}'",
        )


# ==========================================================
# 3. PAYMENT STATUS RETRIEVAL (A09)
# ==========================================================

def get_activity_payment_status(
    db: Session,
    booking_id: int,
    current_user: User,
) -> PaymentStatusResponse:
    """
    Retrieve payment and transaction status for an activity booking.
    Enforces authorization for traveler, activity provider, or platform admin.
    """
    booking = db.query(ActivityBooking).filter(ActivityBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity booking not found",
        )

    # Permission check: traveler, provider owner, or admin
    is_owner = booking.user_id == current_user.id
    is_admin = current_user.role == "admin"
    is_authorized_provider = False

    if not is_owner and not is_admin:
        if current_user.role == "provider":
            slot = (
                db.query(ActivitySlot)
                .filter(ActivitySlot.id == booking.activity_slot_id)
                .first()
            )
            activity = (
                db.query(Activity).filter(Activity.id == slot.activity_id).first()
                if slot
                else None
            )
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
            if (
                profile
                and activity
                and activity.provider_profile_id == profile.id
            ):
                is_authorized_provider = True

        if not is_authorized_provider:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view payment status for this booking",
            )

    tx = (
        db.query(PaymentTransaction)
        .join(
            BookingReference,
            PaymentTransaction.booking_reference_id == BookingReference.id,
        )
        .filter(
            BookingReference.module_type == "activity",
            BookingReference.target_booking_id == booking.id,
            PaymentTransaction.user_id == booking.user_id,
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )

    return PaymentStatusResponse(
        booking_id=booking.id,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        total_price=booking.total_price,
        currency=booking.currency or "LKR",
        paid_at=tx.paid_at if tx else None,
        transaction_id=tx.id if tx else None,
    )


# ==========================================================
# 4. FINANCIAL ABSTRACTIONS FOR B14 & C05
# ==========================================================

def get_activity_provider_earnings_components(
    db: Session,
    provider_profile_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, Decimal | int]:
    """
    Additive online earnings breakdown for activity providers (B14 & C05).
    Aggregates captured activity payments, calculates 10% platform commission,
    90% net provider payable, pending earnings, and completed refunds.
    """
    rows = (
        db.query(PaymentTransaction, ActivityBooking, ActivitySlot)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(ActivityBooking, BookingReference.target_booking_id == ActivityBooking.id)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .filter(
            BookingReference.module_type == "activity",
            Activity.provider_profile_id == provider_profile_id,
            PaymentTransaction.payment_status.in_(CAPTURED),
            PaymentTransaction.payment_method == "online",
            PaymentTransaction.currency == "LKR",
            BookingReference.currency == "LKR",
            ActivityBooking.currency == "LKR",
        )
        .all()
    )

    total_gross = Decimal("0.00")
    completed_gross = Decimal("0.00")
    completed_commission = Decimal("0.00")
    completed_provider_payable = Decimal("0.00")
    pending_earnings = Decimal("0.00")
    paid_count = 0
    tx_ids: list[int] = []

    for tx, booking, slot in rows:
        # If filtering by settlement period, slot start date must fall in requested period
        slot_date = slot.starts_at.date() if slot.starts_at else None
        if period_start and slot_date and slot_date < period_start:
            continue
        if period_end and slot_date and slot_date > period_end:
            continue

        paid_count += 1
        tx_ids.append(tx.id)
        gross = tx.gross_amount or tx.amount or Decimal("0.00")
        total_gross += gross

        comm = (
            tx.commission_amount
            if tx.commission_amount is not None
            else (gross * (PLATFORM_COMMISSION_RATE / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        )
        payable = (
            tx.provider_payable_amount
            if tx.provider_payable_amount is not None
            else (gross - comm)
        )

        if booking.booking_status == "completed":
            completed_gross += gross
            completed_commission += comm
            completed_provider_payable += payable
        elif booking.booking_status in ("confirmed", "pending"):
            pending_earnings += payable

    # Verified refunds
    verified_refunds = Decimal("0.00")
    provider_refund_reduction = Decimal("0.00")
    commission_reversal = Decimal("0.00")

    if tx_ids:
        refund_rows = (
            db.query(PaymentRefund)
            .filter(
                PaymentRefund.payment_transaction_id.in_(tx_ids),
                PaymentRefund.refund_status.in_(EXECUTED_REFUNDS),
                PaymentRefund.currency == "LKR",
            )
            .all()
        )
        for r in refund_rows:
            verified_refunds += r.amount
            comm_rev = (r.amount * (PLATFORM_COMMISSION_RATE / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            prov_deduct = r.amount - comm_rev
            commission_reversal += comm_rev
            provider_refund_reduction += prov_deduct

    return {
        "total_gross": total_gross,
        "completed_gross": completed_gross,
        "completed_commission": completed_commission,
        "completed_provider_payable": completed_provider_payable,
        "pending_earnings": pending_earnings,
        "verified_refunds": verified_refunds,
        "commission_reversal": commission_reversal,
        "provider_refund_reduction": provider_refund_reduction,
        "paid_count": paid_count,
    }
