"""
Local Tour Guide Module 05 — Phase 4 Online Payment Preparation,
Commission Calculation, IPN State Machine & Refund Services.

Designed to keep all Tour Guide payment rules isolated from Homestay and Rental
so that future integration into app/routers/payments.py requires only minimal
delegation hooks without risking cross-module regressions.

IMPORTANT:
- Never executes real outbound payment or payout transactions.
- Never writes direct cash (pay_at_location) bookings into dbo.payment_transactions.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import hmac

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.notification_service import create_notification
from app.core.payhere import payhere_service
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.models.tour_guide import TourGuide, TourGuideBooking
from app.models.user import User
from app.schemas.tour_guide import (
    TourGuideCommissionBreakdown,
    TourGuidePaymentPrepareResponse,
    TourGuidePaymentStatusResponse,
    TourGuideProviderEarningsResponse,
    TourGuideRefundRecordItem,
    TourGuideRefundResponse,
)
from app.services.tour_guide_booking import (
    _acquire_guide_booking_lock,
    _booking_eager_query,
    _cash_payment_label,
    _commit,
    calculate_refund_eligibility,
    expire_stale_pending_bookings,
    record_tour_guide_status_change,
    sync_tour_guide_booking_reference,
)

DEFAULT_PLATFORM_COMMISSION_RATE = Decimal("10.00")
VALID_PAYHERE_STATUS_CODES = {"2", "0", "-1", "-2"}
PAID_TRANSACTION_STATUSES = ("paid", "completed")
ACTIVE_REFUND_STATUSES = ("pending", "approved", "completed", "processed")


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# =============================================================================
# 1. COMMISSION & FINANCIAL SNAPSHOT CALCULATIONS
# =============================================================================

def calculate_tour_guide_commission(
    gross_amount: Decimal,
    *,
    commission_rate: Decimal = DEFAULT_PLATFORM_COMMISSION_RATE,
    currency: str = "LKR",
) -> TourGuideCommissionBreakdown:
    """
    Calculate server-authoritative CeylonGo platform commission (default 10%)
    and provider net payable (90%) with exact two-decimal reconciliation:
    commission_amount + provider_payable_amount == gross_amount.
    """
    gross = Decimal(str(gross_amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rate = Decimal(str(commission_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if gross < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Gross payment amount cannot be negative.",
        )
    if rate < Decimal("0.00") or rate > Decimal("100.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Commission rate must be between 0.00 and 100.00.",
        )

    commission_amount = (gross * rate / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    provider_payable_amount = (gross - commission_amount).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return TourGuideCommissionBreakdown(
        gross_amount=gross,
        commission_rate=rate,
        commission_amount=commission_amount,
        provider_payable_amount=provider_payable_amount,
        currency=(currency or "LKR").upper(),
    )


# =============================================================================
# 2. PAYHERE IPN PAYLOAD & CONSTANT-TIME SIGNATURE VERIFICATION
# =============================================================================

def _compute_expected_ipn_signature(
    *,
    merchant_id: str,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str,
    status_code: str,
    merchant_secret: str,
) -> str:
    secret_hash = hashlib.md5(merchant_secret.encode("utf-8")).hexdigest().upper()
    raw = (
        f"{merchant_id}{order_id}{payhere_amount}"
        f"{payhere_currency}{status_code}{secret_hash}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


def verify_payhere_ipn_payload(form_data: dict) -> dict:
    """
    Validate and cryptographically verify an incoming PayHere IPN payload.

    Enforces:
    - Presence of all required PayHere fields
    - Strict constant-time merchant_id verification against configured merchant
    - Constant-time MD5 signature verification
    - Valid non-negative two-decimal amount and 3-letter ISO currency
    - Recognized PayHere status code ('2', '0', '-1', '-2')
    """
    merchant_id = str(form_data.get("merchant_id", "")).strip()
    order_id = str(form_data.get("order_id", "")).strip()
    payment_id = str(form_data.get("payment_id", "")).strip()
    payhere_amount = str(form_data.get("payhere_amount", "")).strip()
    payhere_currency = str(form_data.get("payhere_currency", "")).strip().upper()
    status_code = str(form_data.get("status_code", "")).strip()
    md5sig = str(form_data.get("md5sig", "")).strip()

    if not all(
        [merchant_id, order_id, payhere_amount, payhere_currency, status_code, md5sig]
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required PayHere notification parameters.",
        )

    if not hmac.compare_digest(merchant_id, payhere_service.merchant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayHere merchant identifier.",
        )

    expected_sig = _compute_expected_ipn_signature(
        merchant_id=merchant_id,
        order_id=order_id,
        payhere_amount=payhere_amount,
        payhere_currency=payhere_currency,
        status_code=status_code,
        merchant_secret=payhere_service.merchant_secret,
    )
    if not hmac.compare_digest(expected_sig, md5sig.upper()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayHere MD5 signature verification.",
        )

    if status_code not in VALID_PAYHERE_STATUS_CODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported PayHere status code '{status_code}'.",
        )

    try:
        parsed_amount = Decimal(payhere_amount).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed PayHere payment amount.",
        )

    if parsed_amount < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PayHere payment amount cannot be negative.",
        )

    if len(payhere_currency) != 3 or not payhere_currency.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed PayHere currency code.",
        )

    return {
        "merchant_id": merchant_id,
        "order_id": order_id,
        "payment_id": payment_id or None,
        "amount": parsed_amount,
        "payhere_amount": payhere_amount,
        "currency": payhere_currency,
        "status_code": status_code,
    }


# =============================================================================
# 3. ONLINE CHECKOUT PREPARATION & VALIDATION (TR-12 PREP)
# =============================================================================

def prepare_tour_guide_online_payment(
    db: Session,
    *,
    booking_id: int,
    current_user: User,
) -> TourGuidePaymentPrepareResponse:
    """
    Validate booking eligibility, upsert an idempotent pending PaymentTransaction,
    and generate signed PayHere checkout parameters for an accepted online
    tour guide booking. Never executes an external payment call.
    """
    booking = _booking_eager_query(db).filter(TourGuideBooking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )

    if booking.traveler_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate online payment for your own tour guide bookings.",
        )

    expire_stale_pending_bookings(db, tour_guide_id=booking.tour_guide_id)
    db.refresh(booking)

    if booking.payment_method != "online":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This booking is configured for direct cash payment (pay_at_location). "
                "Online checkout is not permitted for cash bookings."
            ),
        )

    if booking.booking_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is pending guide approval. Online payment can only be initiated after acceptance.",
        )

    if booking.booking_status not in ("confirmed", "awaiting_payment"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot prepare online payment for booking with status '{booking.booking_status}'.",
        )

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This tour guide booking has already been paid.",
        )

    if booking.payment_status in ("refund_pending", "refunded"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot initiate payment when payment status is '{booking.payment_status}'.",
        )

    if booking.total_price <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking total price must be greater than zero for online gateway checkout.",
        )

    _acquire_guide_booking_lock(db, booking.tour_guide_id)
    db.refresh(booking)
    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This tour guide booking has already been paid.",
        )

    guide = booking.tour_guide
    booking_ref = sync_tour_guide_booking_reference(db, booking, guide)
    currency = (booking.currency or "LKR").upper()
    commission = calculate_tour_guide_commission(booking.total_price, currency=currency)

    # Idempotent upsert of pending PaymentTransaction
    tx = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.booking_reference_id == booking_ref.id,
            PaymentTransaction.payment_status == "pending",
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if tx is None:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.traveler_id,
            gateway_name="payhere",
            amount=booking.total_price,
            gross_amount=commission.gross_amount,
            commission_rate=commission.commission_rate,
            commission_amount=commission.commission_amount,
            provider_payable_amount=commission.provider_payable_amount,
            currency=currency,
            payment_status="pending",
            payment_method="online",
        )
        db.add(tx)
        db.flush()
    else:
        tx.amount = booking.total_price
        tx.gross_amount = commission.gross_amount
        tx.commission_rate = commission.commission_rate
        tx.commission_amount = commission.commission_amount
        tx.provider_payable_amount = commission.provider_payable_amount
        tx.currency = currency

    if booking.payment_status in ("unpaid", "failed"):
        booking.payment_status = "pending"
        booking_ref.payment_status = "pending"

    _commit(db)
    db.refresh(tx)

    order_id = booking_ref.booking_reference
    checkout_hash = payhere_service.generate_checkout_hash(
        order_id=order_id,
        amount=booking.total_price,
        currency=currency,
    )

    traveler = booking.traveler
    name_parts = (traveler.full_name or "Traveler").strip().split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"
    item_label = (
        f"Tour Guide: {guide.provider_profile.business_name} — {booking.package.title} ({booking.booking_date})"
        if booking.package
        else f"Tour Guide: {guide.provider_profile.business_name} ({booking.booking_date})"
    )

    return TourGuidePaymentPrepareResponse(
        booking_id=booking.id,
        booking_reference=order_id,
        payment_transaction_id=tx.id,
        payment_mode=payhere_service.mode,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        order_id=order_id,
        items=item_label,
        amount=booking.total_price,
        currency=currency,
        hash=checkout_hash,
        first_name=first_name,
        last_name=last_name,
        email=traveler.email,
        phone=None,
        return_url=payhere_service.return_url,
        cancel_url=payhere_service.cancel_url,
        notify_url=payhere_service.notify_url,
        commission_preview=commission,
    )


# =============================================================================
# 4. PAYHERE IPN CALLBACK STATE HANDLER (ISOLATED FOR TOUR GUIDE MODULE)
# =============================================================================

def handle_tour_guide_payhere_callback(
    db: Session,
    *,
    form_data: dict,
    booking_ref: BookingReference | None = None,
    verify_signature: bool = True,
) -> dict:
    """
    Process a verified PayHere IPN callback for a tour_guide booking.

    Safe to call directly in tests (verify_signature=True) or as a delegated
    branch from app/routers/payments.py::handle_payhere_callback once approved.
    """
    if verify_signature:
        verified = verify_payhere_ipn_payload(form_data)
    else:
        # Even when caller already ran signature check, still validate fields & amount
        verified = verify_payhere_ipn_payload(form_data)

    order_id = verified["order_id"]
    payment_id = verified["payment_id"]
    received_amt = verified["amount"]
    received_currency = verified["currency"]
    status_code = verified["status_code"]

    if booking_ref is None:
        booking_ref = (
            db.query(BookingReference)
            .filter(BookingReference.booking_reference == order_id)
            .first()
        )
    if booking_ref is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking reference not found.",
        )

    if booking_ref.module_type != "tour_guide":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Expected 'tour_guide' booking reference, received '{booking_ref.module_type}'.",
        )

    booking = (
        _booking_eager_query(db)
        .filter(TourGuideBooking.id == booking_ref.target_booking_id)
        .first()
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated tour guide booking not found.",
        )

    if booking.payment_method != "online":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot apply online gateway callback to a direct cash (pay_at_location) booking.",
        )

    expected_total = Decimal(str(booking.total_price)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    expected_currency = (booking.currency or "LKR").upper()
    if received_amt != expected_total or received_currency != expected_currency:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount or currency mismatch with tour guide booking record.",
        )

    _acquire_guide_booking_lock(db, booking.tour_guide_id)
    db.refresh(booking)

    # Idempotency guard #1: booking already settled as paid or refunded/refund_pending
    if booking.payment_status in ("paid", "refund_pending", "refunded"):
        return {
            "status": "already_processed",
            "booking_id": booking.id,
            "payment_status": booking.payment_status,
        }

    # Idempotency guard #2: duplicate gateway_transaction_id already recorded as paid
    if payment_id:
        existing_paid_tx = (
            db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.booking_reference_id == booking_ref.id,
                PaymentTransaction.gateway_transaction_id == payment_id,
                PaymentTransaction.payment_status == "paid",
            )
            .first()
        )
        if existing_paid_tx is not None:
            return {
                "status": "already_processed",
                "booking_id": booking.id,
                "payment_status": existing_paid_tx.payment_status,
            }

    now_utc = _now_utc()
    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.booking_reference_id == booking_ref.id)
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if tx is None:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.traveler_id,
            gateway_name="payhere",
            amount=received_amt,
            currency=received_currency,
            payment_method="online",
        )
        db.add(tx)
        db.flush()

    tx.gateway_transaction_id = payment_id
    tx.gateway_response_payload = str(dict(form_data))

    guide = booking.tour_guide
    provider_user_id = guide.provider_profile.user_id

    if status_code == "2":
        commission = calculate_tour_guide_commission(
            received_amt, currency=received_currency
        )
        tx.payment_status = "paid"
        tx.paid_at = now_utc
        tx.gross_amount = commission.gross_amount
        tx.commission_rate = commission.commission_rate
        tx.commission_amount = commission.commission_amount
        tx.provider_payable_amount = commission.provider_payable_amount

        is_late_or_invalid = booking.booking_status in (
            "expired",
            "cancelled",
            "rejected",
            "pending",
        )

        if is_late_or_invalid:
            # Preserve verified payment record, DO NOT confirm/reactivate booking,
            # and flag for reconciliation & refund.
            booking.payment_status = "refund_pending"
            booking_ref.payment_status = "refund_pending"

            existing_refund = (
                db.query(PaymentRefund)
                .filter(
                    PaymentRefund.payment_transaction_id == tx.id,
                    PaymentRefund.refund_status.in_(ACTIVE_REFUND_STATUSES),
                )
                .first()
            )
            if existing_refund is None:
                db.add(
                    PaymentRefund(
                        payment_transaction_id=tx.id,
                        amount=received_amt,
                        currency=received_currency,
                        refund_reason=(
                            f"Late or invalid online payment verified while tour guide booking "
                            f"was '{booking.booking_status}' (Order: {order_id}). Flagged for refund."
                        ),
                        refund_status="pending",
                    )
                )

            record_tour_guide_status_change(
                db,
                booking_reference_id=booking_ref.id,
                from_status=booking.booking_status,
                to_status=booking.booking_status,
                changed_by=None,
                notes=(
                    f"PayHere payment received for '{booking.booking_status}' booking "
                    f"(Payment ID: {payment_id}). Flagged for refund; booking not reactivated."
                ),
            )
            create_notification(
                db=db,
                user_id=booking.traveler_id,
                title="Payment Flagged for Refund",
                message=(
                    f"We received your online payment of {received_amt} {received_currency} "
                    f"for tour guide booking #{booking.id}, but the booking is '{booking.booking_status}'. "
                    "A refund request has been opened for review."
                ),
                notification_type="payment_flagged_for_refund",
                link_url=f"/tour-guide-bookings/{booking.id}",
            )
            create_notification(
                db=db,
                user_id=provider_user_id,
                title="Late Payment Flagged for Reconciliation",
                message=(
                    f"An online payment of {received_amt} {received_currency} arrived for "
                    f"{booking.booking_status} booking #{booking.id} and was flagged for refund."
                ),
                notification_type="payment_flagged_for_refund",
                link_url=f"/provider/tour-guides/bookings/{booking.id}",
            )
        else:
            old_status = booking.booking_status
            if booking.booking_status == "awaiting_payment":
                booking.booking_status = "confirmed"
                booking_ref.booking_status = "confirmed"
            booking.payment_status = "paid"
            booking_ref.payment_status = "paid"

            record_tour_guide_status_change(
                db,
                booking_reference_id=booking_ref.id,
                from_status=old_status,
                to_status=booking.booking_status,
                changed_by=None,
                notes=(
                    f"PayHere Online Payment Confirmed (Payment ID: {payment_id}; "
                    f"Gross: {commission.gross_amount}, Commission: {commission.commission_amount}, "
                    f"Provider Payable: {commission.provider_payable_amount} {commission.currency})."
                ),
            )
            create_notification(
                db=db,
                user_id=booking.traveler_id,
                title="Tour Guide Online Payment Successful",
                message=(
                    f"Your online payment of {received_amt} {received_currency} for "
                    f"{guide.provider_profile.business_name} on {booking.booking_date} was confirmed."
                ),
                notification_type="payment_success",
                link_url=f"/tour-guide-bookings/{booking.id}",
            )
            create_notification(
                db=db,
                user_id=provider_user_id,
                title="Tour Guide Online Payment Received",
                message=(
                    f"Traveler paid {received_amt} {received_currency} online for booking #{booking.id} "
                    f"(Net payable after {commission.commission_rate}% fee: "
                    f"{commission.provider_payable_amount} {commission.currency})."
                ),
                notification_type="payment_received",
                link_url=f"/provider/tour-guides/bookings/{booking.id}",
            )

    elif status_code in ("-1", "-2"):
        tx.payment_status = "failed"
        booking.payment_status = "failed"
        booking_ref.payment_status = "failed"
        record_tour_guide_status_change(
            db,
            booking_reference_id=booking_ref.id,
            from_status=booking.booking_status,
            to_status=booking.booking_status,
            changed_by=None,
            notes=f"PayHere online payment failed or cancelled (status_code={status_code}).",
        )
        create_notification(
            db=db,
            user_id=booking.traveler_id,
            title="Tour Guide Online Payment Unsuccessful",
            message=(
                f"Your online payment for booking #{booking.id} was unsuccessful. "
                "You may retry payment."
            ),
            notification_type="payment_failed",
            link_url=f"/tour-guide-bookings/{booking.id}",
        )
    else:  # status_code == "0" (pending)
        tx.payment_status = "pending"
        booking.payment_status = "pending"
        booking_ref.payment_status = "pending"

    _commit(db)
    return {
        "status": "ok",
        "booking_id": booking.id,
        "booking_status": booking.booking_status,
        "payment_status": booking.payment_status,
        "transaction_status": tx.payment_status,
    }


# =============================================================================
# 5. PAYMENT STATUS, REFUND REQUESTS & COMPLETION SYNC
# =============================================================================

def _evaluate_booking_refund_eligibility(booking: TourGuideBooking):
    if booking.booking_status == "cancelled":
        guide_user_id = booking.tour_guide.provider_profile.user_id
        actor_role = "provider" if booking.cancelled_by == guide_user_id else "traveler"
        return calculate_refund_eligibility(
            booking,
            cancelled_by_role=actor_role,
            as_of=booking.cancelled_at or _now_utc(),
        )
    if booking.booking_status in ("pending", "confirmed"):
        return calculate_refund_eligibility(booking)
    return None


def get_tour_guide_payment_status(
    db: Session,
    *,
    booking_id: int,
    current_user: User,
) -> TourGuidePaymentStatusResponse:
    booking = _booking_eager_query(db).filter(TourGuideBooking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )

    guide_user_id = booking.tour_guide.provider_profile.user_id
    is_traveler = booking.traveler_id == current_user.id
    is_provider_owner = guide_user_id == current_user.id
    is_admin = current_user.role == "admin"

    if not (is_traveler or is_provider_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view payment status for this tour guide booking.",
        )

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )

    tx = None
    refunds: list[TourGuideRefundRecordItem] = []
    if booking_ref is not None:
        tx = (
            db.query(PaymentTransaction)
            .filter(PaymentTransaction.booking_reference_id == booking_ref.id)
            .order_by(PaymentTransaction.id.desc())
            .first()
        )
        if tx is not None:
            refund_rows = (
                db.query(PaymentRefund)
                .filter(PaymentRefund.payment_transaction_id == tx.id)
                .order_by(PaymentRefund.created_at.desc())
                .all()
            )
            refunds = [TourGuideRefundRecordItem.model_validate(r) for r in refund_rows]

    commission_breakdown = None
    if (is_provider_owner or is_admin) and booking.payment_method == "online":
        if tx is not None and tx.commission_amount is not None and tx.provider_payable_amount is not None:
            commission_breakdown = TourGuideCommissionBreakdown(
                gross_amount=tx.gross_amount or tx.amount,
                commission_rate=tx.commission_rate or DEFAULT_PLATFORM_COMMISSION_RATE,
                commission_amount=tx.commission_amount,
                provider_payable_amount=tx.provider_payable_amount,
                currency=tx.currency or booking.currency or "LKR",
            )
        else:
            commission_breakdown = calculate_tour_guide_commission(
                booking.total_price, currency=booking.currency or "LKR"
            )

    return TourGuidePaymentStatusResponse(
        booking_id=booking.id,
        booking_reference=booking_ref.booking_reference if booking_ref else None,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        is_online_payment=(booking.payment_method == "online"),
        cash_payment_status=_cash_payment_label(booking),
        total_price=booking.total_price,
        currency=booking.currency or "LKR",
        paid_at=tx.paid_at if tx else None,
        transaction_id=tx.id if tx else None,
        gateway_transaction_id=tx.gateway_transaction_id if tx else None,
        commission_breakdown=commission_breakdown,
        refund_eligibility=_evaluate_booking_refund_eligibility(booking),
        refunds=refunds,
    )


def request_tour_guide_booking_refund(
    db: Session,
    *,
    booking_id: int,
    reason: str,
    current_user: User,
) -> TourGuideRefundResponse:
    """
    Create a policy-verified pending PaymentRefund for a cancelled, online-paid
    tour guide booking without executing any real payment gateway refund.
    """
    booking = _booking_eager_query(db).filter(TourGuideBooking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )

    if booking.traveler_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only request refunds for your own tour guide bookings.",
        )

    if booking.booking_status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refunds can only be requested for cancelled tour guide bookings.",
        )

    if booking.payment_method != "online":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online refunds are not applicable to direct cash (pay_at_location) bookings.",
        )

    eligibility = _evaluate_booking_refund_eligibility(booking)
    if eligibility is None or eligibility.eligible_refund_amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                eligibility.policy_note
                if eligibility
                else "This booking is not eligible for an online refund under its cancellation policy."
            ),
        )

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No booking reference or online transaction found for this booking.",
        )

    tx = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.booking_reference_id == booking_ref.id,
            PaymentTransaction.payment_status.in_(PAID_TRANSACTION_STATUSES),
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if tx is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No paid online payment transaction found to refund.",
        )

    existing_refunds_total = (
        db.query(func.coalesce(func.sum(PaymentRefund.amount), Decimal("0.00")))
        .filter(
            PaymentRefund.payment_transaction_id == tx.id,
            PaymentRefund.refund_status.in_(ACTIVE_REFUND_STATUSES),
        )
        .scalar()
    )
    remaining_refundable = min(
        eligibility.eligible_refund_amount,
        tx.amount - existing_refunds_total,
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if remaining_refundable <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full eligible amount has already been refunded or is currently pending refund.",
        )

    refund = PaymentRefund(
        payment_transaction_id=tx.id,
        amount=remaining_refundable,
        currency=tx.currency or booking.currency or "LKR",
        refund_reason=reason,
        refund_status="pending",
    )
    db.add(refund)
    db.flush()

    booking.payment_status = "refund_pending"
    booking_ref.payment_status = "refund_pending"
    record_tour_guide_status_change(
        db,
        booking_reference_id=booking_ref.id,
        from_status=booking.booking_status,
        to_status=booking.booking_status,
        changed_by=current_user.id,
        notes=(
            f"Online refund requested ({remaining_refundable} {refund.currency}): {reason}"
        ),
    )

    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Tour Guide Refund Request Submitted",
        message=(
            f"Your refund request of {remaining_refundable} {refund.currency} "
            f"for booking #{booking.id} is under admin review."
        ),
        notification_type="refund_requested",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(refund)

    return TourGuideRefundResponse(
        id=refund.id,
        booking_id=booking.id,
        booking_reference=booking_ref.booking_reference,
        payment_transaction_id=refund.payment_transaction_id,
        amount=refund.amount,
        currency=refund.currency,
        refund_reason=refund.refund_reason,
        refund_status=refund.refund_status,
        refund_eligibility=eligibility,
        processed_by=refund.processed_by,
        processed_at=refund.processed_at,
        created_at=refund.created_at,
    )


def sync_tour_guide_refund_completion(
    db: Session,
    *,
    booking_ref: BookingReference,
    refund: PaymentRefund,
    admin_id: int,
) -> None:
    """
    Hook ready for app/routers/payments.py::process_refund (after approval):
    when an admin marks a tour_guide PaymentRefund as 'completed', synchronize
    TourGuideBooking.payment_status and BookingReference.payment_status to 'refunded'.
    """
    if booking_ref.module_type != "tour_guide":
        return

    # Ensure caller's in-transaction refund_status update is visible to SUM query
    db.flush()

    booking = (
        db.query(TourGuideBooking)
        .filter(TourGuideBooking.id == booking_ref.target_booking_id)
        .first()
    )
    if booking is None:
        return

    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id == refund.payment_transaction_id)
        .first()
    )
    if tx is None:
        return

    total_completed_refunded = (
        db.query(func.coalesce(func.sum(PaymentRefund.amount), Decimal("0.00")))
        .filter(
            PaymentRefund.payment_transaction_id == tx.id,
            PaymentRefund.refund_status.in_(("completed", "processed")),
        )
        .scalar()
    )
    if total_completed_refunded >= tx.amount:
        booking.payment_status = "refunded"
        booking_ref.payment_status = "refunded"
        record_tour_guide_status_change(
            db,
            booking_reference_id=booking_ref.id,
            from_status=booking.booking_status,
            to_status=booking.booking_status,
            changed_by=admin_id,
            notes=f"Refund #{refund.id} completed ({refund.amount} {refund.currency}); payment_status='refunded'.",
        )


# =============================================================================
# 6. PROVIDER ONLINE EARNINGS & SETTLEMENT BALANCE CALCULATION
# =============================================================================

def get_tour_guide_online_earnings_components(
    db: Session,
    *,
    provider_profile_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict:
    """
    Return additive Tour Guide online earnings components (excluding settlements
    and excluding direct cash bookings) so shared calculate_provider_earnings_balances
    and calculate_tour_guide_provider_earnings share one authoritative calculation.
    """
    rows = (
        db.query(PaymentTransaction, TourGuideBooking)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(TourGuideBooking, BookingReference.target_booking_id == TourGuideBooking.id)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.provider_profile_id == provider_profile_id,
            PaymentTransaction.payment_status.in_(PAID_TRANSACTION_STATUSES),
            PaymentTransaction.payment_method == "online",
        )
        .all()
    )

    total_online_gross = Decimal("0.00")
    completed_online_gross = Decimal("0.00")
    completed_commission = Decimal("0.00")
    completed_provider_payable = Decimal("0.00")
    pending_online_earnings = Decimal("0.00")
    paid_online_count = 0
    tx_ids: list[int] = []

    for tx, booking in rows:
        if period_start and booking.booking_date < period_start:
            continue
        if period_end and booking.booking_date > period_end:
            continue

        paid_online_count += 1
        tx_ids.append(tx.id)
        gross = tx.gross_amount or tx.amount or Decimal("0.00")
        total_online_gross += gross

        breakdown = (
            TourGuideCommissionBreakdown(
                gross_amount=gross,
                commission_rate=tx.commission_rate or DEFAULT_PLATFORM_COMMISSION_RATE,
                commission_amount=tx.commission_amount,
                provider_payable_amount=tx.provider_payable_amount,
                currency=tx.currency or "LKR",
            )
            if tx.commission_amount is not None and tx.provider_payable_amount is not None
            else calculate_tour_guide_commission(gross, currency=tx.currency or "LKR")
        )

        if booking.booking_status == "completed":
            completed_online_gross += gross
            completed_commission += breakdown.commission_amount
            completed_provider_payable += breakdown.provider_payable_amount
        elif booking.booking_status in ("confirmed", "in_progress"):
            pending_online_earnings += breakdown.provider_payable_amount

    verified_refunds = Decimal("0.00")
    commission_reversal = Decimal("0.00")
    provider_refund_reduction = Decimal("0.00")
    if tx_ids:
        refund_rows = (
            db.query(PaymentRefund)
            .filter(
                PaymentRefund.payment_transaction_id.in_(tx_ids),
                PaymentRefund.refund_status.in_(("completed", "processed")),
            )
            .all()
        )
        for r in refund_rows:
            verified_refunds += r.amount
            rev = calculate_tour_guide_commission(r.amount, currency=r.currency or "LKR")
            commission_reversal += rev.commission_amount
            provider_refund_reduction += rev.provider_payable_amount

    return {
        "total_gross": total_online_gross,
        "completed_gross": completed_online_gross,
        "completed_commission": completed_commission,
        "completed_provider_payable": completed_provider_payable,
        "pending_earnings": pending_online_earnings,
        "verified_refunds": verified_refunds,
        "commission_reversal": commission_reversal,
        "provider_refund_reduction": provider_refund_reduction,
        "paid_count": paid_online_count,
    }


def calculate_tour_guide_provider_earnings(
    db: Session,
    *,
    provider_profile_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> TourGuideProviderEarningsResponse:
    """
    Calculate server-authoritative online earnings, 10% platform commission,
    verified refunds, and settlement availability for a tour guide provider.

    Direct cash (pay_at_location) bookings are reported separately as
    cash_collected_total and are NEVER included in CeylonGo settlement liabilities.
    """
    c = get_tour_guide_online_earnings_components(
        db,
        provider_profile_id=provider_profile_id,
        period_start=period_start,
        period_end=period_end,
    )
    total_online_gross = c["total_gross"]
    completed_online_gross = c["completed_gross"]
    completed_commission = c["completed_commission"]
    completed_provider_payable = c["completed_provider_payable"]
    pending_online_earnings = c["pending_earnings"]
    verified_refunds = c["verified_refunds"]
    commission_reversal = c["commission_reversal"]
    provider_refund_reduction = c["provider_refund_reduction"]
    paid_online_count = c["paid_count"]

    provider_net_online = max(
        Decimal("0.00"), completed_provider_payable - provider_refund_reduction
    )
    net_ceylongo_commission = max(
        Decimal("0.00"), completed_commission - commission_reversal
    )

    settlements = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.provider_profile_id == provider_profile_id)
        .all()
    )
    total_settled = Decimal("0.00")
    reserved_settlement = Decimal("0.00")
    for s in settlements:
        if s.settlement_status in ("completed", "paid"):
            total_settled += s.net_payable
        elif s.settlement_status in ("pending", "approved"):
            reserved_settlement += s.net_payable

    commitments = total_settled + reserved_settlement
    available_balance = max(
        Decimal("0.00"),
        (completed_provider_payable - provider_refund_reduction) - commitments,
    )
    outstanding_liability = max(
        Decimal("0.00"),
        commitments - (completed_provider_payable - provider_refund_reduction),
    )

    # Informational direct-cash total (strictly separate from platform settlement ledger)
    guide = (
        db.query(TourGuide)
        .filter(TourGuide.provider_profile_id == provider_profile_id)
        .first()
    )
    cash_total = Decimal("0.00")
    cash_count = 0
    if guide is not None:
        cash_q = db.query(TourGuideBooking).filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.payment_method == "pay_at_location",
            TourGuideBooking.payment_status == "paid",
        )
        if period_start:
            cash_q = cash_q.filter(TourGuideBooking.booking_date >= period_start)
        if period_end:
            cash_q = cash_q.filter(TourGuideBooking.booking_date <= period_end)
        cash_rows = cash_q.all()
        cash_count = len(cash_rows)
        cash_total = sum((b.total_price for b in cash_rows), Decimal("0.00"))

    return TourGuideProviderEarningsResponse(
        total_online_gross_earnings=total_online_gross,
        completed_online_booking_earnings=completed_online_gross,
        ceylongo_commission=net_ceylongo_commission,
        verified_refunds=verified_refunds,
        provider_refund_reduction=provider_refund_reduction,
        provider_net_online_earnings=provider_net_online,
        pending_online_earnings=pending_online_earnings,
        total_settled=total_settled,
        reserved_settlement=reserved_settlement,
        available_settlement_balance=available_balance,
        outstanding_liability=outstanding_liability,
        total_paid_online_bookings=paid_online_count,
        cash_collected_total=cash_total,
        total_cash_paid_bookings=cash_count,
        currency="LKR",
    )
