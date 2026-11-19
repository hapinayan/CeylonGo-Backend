from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.core.listing_sync import (
    record_booking_status_change,
    sync_homestay_booking_reference,
)
from app.core.notification_service import create_notification
from app.core.payhere import payhere_service
from app.core.promotion_pricing import record_campaign_booking_conversion
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.payment import (
    AdminFinancialSummaryResponse,
    PaymentInitiateResponse,
    PaymentStatusResponse,
    PaymentTransactionResponse,
    ProviderEarningsSummaryResponse,
    RefundProcessRequest,
    RefundRequest,
    RefundResponse,
    SettlementCreateRequest,
    SettlementResponse,
)
from app.services.booking_expiration import expire_single_booking

router = APIRouter(
    prefix="/api/v1/payments",
    tags=["Payments & Settlements"],
)


# ==========================================
# 1. PAYHERE ONLINE PAYMENT INITIATION
# ==========================================

@router.post(
    "/bookings/{booking_id}/initiate",
    response_model=PaymentInitiateResponse,
    summary="Initiate PayHere online payment for an approved homestay booking",
)
def initiate_booking_payment(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    if booking.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate payment for your own bookings",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Permit online checkout only for valid awaiting_payment bookings (or unexpired legacy confirmed)
    if booking.status == "awaiting_payment":
        if booking.payment_expires_at is not None and booking.payment_expires_at <= now_utc:
            expire_single_booking(db, booking, now_utc)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Online payment deadline has expired for this booking. Availability has been released.",
            )
    elif booking.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is pending provider approval. Online payment cannot be initiated until accepted.",
        )
    elif booking.status == "expired":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online payment deadline has expired for this booking.",
        )
    elif booking.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate payment for a cancelled booking.",
        )
    elif booking.status == "confirmed":
        if booking.payment_status == "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This booking has already been paid",
            )
        if booking.payment_method == "pay_at_location":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This booking is configured for on-site cash payment.",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot initiate payment for booking with status '{booking.status}'",
        )

    # Concurrency lock on SQL Server
    if db.bind and db.bind.dialect.name == "mssql":
        lock_result = db.execute(
            text("""
                DECLARE @result INT;
                EXEC @result = sp_getapplock
                    @Resource = :resource,
                    @LockMode = 'Exclusive',
                    @LockOwner = 'Transaction',
                    @LockTimeout = 10000;
                SELECT @result;
            """),
            {"resource": f"homestay_booking_{booking.homestay_id}"},
        ).scalar()
        if lock_result is None or lock_result < 0:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Payment processing is busy. Please retry.",
            )
        db.refresh(booking)

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking has already been paid",
        )

    homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found",
        )

    booking.payment_method = "online"
    booking_ref = sync_homestay_booking_reference(db, booking, homestay)
    if not booking_ref:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to generate booking reference for payment",
        )

    name_parts = current_user.full_name.split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"
    order_id = booking_ref.booking_reference

    # Zero-value booking protection:
    # If total price is 0.00 (e.g. 100% discount promo voucher), confirm directly
    # without sending invalid zero-value transaction to PayHere gateway.
    if booking.total_price <= Decimal("0.00"):
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        booking.status = "confirmed"
        booking.payment_status = "paid"
        booking.commission_rate = Decimal("10.00")
        booking.commission_amount = Decimal("0.00")
        booking.provider_payable_amount = Decimal("0.00")

        booking_ref.booking_status = "confirmed"
        booking_ref.payment_status = "paid"

        tx = (
            db.query(PaymentTransaction)
            .filter(PaymentTransaction.booking_reference_id == booking_ref.id)
            .first()
        )
        if not tx:
            tx = PaymentTransaction(
                booking_reference_id=booking_ref.id,
                user_id=current_user.id,
                gateway_name="promotional_discount",
                gateway_transaction_id="PROMO_ZERO_VALUE",
                amount=Decimal("0.00"),
                gross_amount=Decimal("0.00"),
                commission_rate=Decimal("10.00"),
                commission_amount=Decimal("0.00"),
                provider_payable_amount=Decimal("0.00"),
                currency=booking.currency or "LKR",
                payment_status="paid",
                payment_method="online",
                paid_at=now_utc,
            )
            db.add(tx)
        else:
            tx.payment_status = "paid"
            tx.amount = Decimal("0.00")
            tx.gross_amount = Decimal("0.00")
            tx.commission_rate = Decimal("10.00")
            tx.commission_amount = Decimal("0.00")
            tx.provider_payable_amount = Decimal("0.00")
            tx.paid_at = now_utc

        record_booking_status_change(
            db,
            booking_reference_id=booking_ref.id,
            from_status="awaiting_payment",
            to_status="confirmed",
            changed_by=current_user.id,
            notes="Zero-value booking confirmed directly via 100% promotion discount",
        )

        # Record attributed booking conversion if applicable
        cid = None
        try:
            import re
            history = (
                db.query(BookingStatusHistory)
                .filter(
                    BookingStatusHistory.booking_reference_id == booking_ref.id,
                    BookingStatusHistory.notes.like("%campaign_id:%"),
                )
                .first()
            )
            if history and history.notes:
                m = re.search(r"campaign_id:(\d+)", history.notes)
                if m:
                    cid = int(m.group(1))
        except Exception:
            cid = None

        record_campaign_booking_conversion(
            db=db,
            homestay_id=booking.homestay_id,
            user_id=booking.user_id,
            booking_reference_id=booking_ref.id,
            campaign_id=cid,
        )

        db.commit()

        return PaymentInitiateResponse(
            booking_id=booking.id,
            booking_reference=order_id,
            merchant_id=payhere_service.merchant_id,
            checkout_url=payhere_service.return_url,
            order_id=order_id,
            items=f"Homestay: {homestay.name} ({booking.check_in} to {booking.check_out})",
            amount=Decimal("0.00"),
            currency=booking.currency or "LKR",
            hash="ZERO_VALUE_PROMOTION_CONFIRMED",
            first_name=first_name,
            last_name=last_name,
            email=current_user.email,
            phone=None,
            return_url=payhere_service.return_url,
            cancel_url=payhere_service.cancel_url,
            notify_url=payhere_service.notify_url,
        )

    # Upsert pending PaymentTransaction
    tx = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.booking_reference_id == booking_ref.id,
            PaymentTransaction.payment_status == "pending",
        )
        .first()
    )
    if not tx:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=current_user.id,
            gateway_name="payhere",
            amount=booking.total_price,
            currency=booking.currency or "LKR",
            payment_status="pending",
            payment_method="online",
        )
        db.add(tx)
    else:
        tx.amount = booking.total_price
        tx.currency = booking.currency or "LKR"

    db.commit()

    order_id = booking_ref.booking_reference
    checkout_hash = payhere_service.generate_checkout_hash(
        order_id=order_id,
        amount=booking.total_price,
        currency=booking.currency or "LKR",
    )

    name_parts = current_user.full_name.split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"

    return PaymentInitiateResponse(
        booking_id=booking.id,
        booking_reference=order_id,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        order_id=order_id,
        items=f"Homestay: {homestay.name} ({booking.check_in} to {booking.check_out})",
        amount=booking.total_price,
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


def handle_advertising_payment_callback(
    db: Session,
    order_id: str,
    payment_id: str,
    payhere_amount: str,
    payhere_currency: str,
    status_code: str,
    form_data: dict,
):
    """
    Process PayHere IPN callback for an advertising payment order reference.
    Updates AdvertisingPayment ledger, verifies gateway status, and activates campaign.
    """
    ad_payment = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.order_reference == order_id)
        .first()
    )
    if not ad_payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Advertising payment order reference '{order_id}' not found",
        )

    # 1. Verify amount & currency
    received_amt = Decimal(payhere_amount).quantize(Decimal("0.01"))
    expected_amt = Decimal(str(ad_payment.amount)).quantize(Decimal("0.01"))
    if received_amt != expected_amt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment amount mismatch: expected {expected_amt}, received {received_amt}",
        )
    if payhere_currency != (ad_payment.currency or "LKR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Currency mismatch: expected {ad_payment.currency}, received {payhere_currency}",
        )

    # 2. Concurrency & Idempotency: if already completed, do not duplicate actions
    if ad_payment.payment_status == "completed":
        return {"status": "success", "message": "Advertising payment callback already processed"}

    campaign = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.id == ad_payment.campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated promotion campaign not found",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    ad_payment.gateway_transaction_id = payment_id
    ad_payment.updated_at = now_utc

    if status_code == "2":
        ad_payment.payment_status = "completed"
        ad_payment.verified_at = now_utc

        # Validate campaign activation criteria:
        # - Associated listing must be approved and active
        # - Provider profile ownership must match
        # - Campaign schedule must not have expired
        is_eligible = True
        homestay = None
        if campaign.listing_reference_id:
            listing_ref = (
                db.query(ListingReference)
                .filter(ListingReference.id == campaign.listing_reference_id)
                .first()
            )
            if listing_ref:
                homestay = (
                    db.query(Homestay)
                    .filter(Homestay.id == listing_ref.target_id)
                    .first()
                )

        if not homestay or not homestay.is_active or homestay.approval_status != "approved":
            is_eligible = False

        if homestay and homestay.provider_profile_id != campaign.provider_profile_id:
            is_eligible = False

        if campaign.ends_at <= now_utc:
            is_eligible = False

        if is_eligible:
            if campaign.starts_at <= now_utc:
                campaign.status = "active"
            else:
                campaign.status = "approved"
            campaign.spent_amount = received_amt
        else:
            # Paid but expired, cancelled, or ineligible -> preserve payment record for reconciliation
            if campaign.ends_at <= now_utc:
                campaign.status = "completed"

    elif status_code in ("-1", "-2"):
        ad_payment.payment_status = "failed"

    db.commit()
    db.refresh(ad_payment)
    return {"status": "success", "message": "Advertising payment callback processed"}


# ==========================================
# 2. PAYHERE WEBHOOK / NOTIFICATION LISTENER
# ==========================================

def handle_payhere_callback(form_data: dict, db: Session):
    """
    Synchronous processing of PayHere IPN callback data for bookings and advertising.
    Can be invoked directly from FastAPI request handler or test fixtures.
    """
    merchant_id = form_data.get("merchant_id", "")
    order_id = form_data.get("order_id", "")
    payment_id = form_data.get("payment_id", "")
    payhere_amount = form_data.get("payhere_amount", "")
    payhere_currency = form_data.get("payhere_currency", "")
    status_code = form_data.get("status_code", "")
    md5sig = form_data.get("md5sig", "")

    if not all([merchant_id, order_id, payhere_amount, payhere_currency, status_code, md5sig]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required PayHere notification parameters",
        )

    # 1. Verify PayHere signature
    is_valid_sig = payhere_service.verify_ipn_signature(
        order_id=order_id,
        payhere_amount=payhere_amount,
        payhere_currency=payhere_currency,
        status_code=status_code,
        received_signature=md5sig,
    )
    if not is_valid_sig:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PayHere MD5 signature verification",
        )

    # 1b. Route advertising payment callbacks separately from booking callbacks
    if order_id.startswith("AD-"):
        return handle_advertising_payment_callback(
            db=db,
            order_id=order_id,
            payment_id=payment_id,
            payhere_amount=payhere_amount,
            payhere_currency=payhere_currency,
            status_code=status_code,
            form_data=dict(form_data),
        )

    # 2. Locate booking reference
    booking_ref = (
        db.query(BookingReference)
        .filter(BookingReference.booking_reference == order_id)
        .first()
    )
    if not booking_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking reference not found",
        )

    booking = db.query(Booking).filter(Booking.id == booking_ref.target_booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated booking not found",
        )

    # 3. Verify amount and currency
    received_amt = Decimal(payhere_amount)
    if received_amt != booking.total_price or payhere_currency != (booking.currency or "LKR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount or currency mismatch with booking record",
        )

    # 4. Idempotency & Concurrency: acquire transaction lock if on SQL Server
    if db.bind and db.bind.dialect.name == "mssql":
        lock_result = db.execute(
            text("""
                DECLARE @result INT;
                EXEC @result = sp_getapplock
                    @Resource = :resource,
                    @LockMode = 'Exclusive',
                    @LockOwner = 'Transaction',
                    @LockTimeout = 10000;
                SELECT @result;
            """),
            {"resource": f"homestay_booking_{booking.homestay_id}"},
        ).scalar()
        if lock_result is None or lock_result < 0:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Payment processing is busy. Please retry.",
            )
        # Re-read booking state under lock
        db.refresh(booking)

    # Idempotency: if already paid, return 200 immediately
    if booking.payment_status == "paid":
        return {"status": "already_processed"}

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.booking_reference_id == booking_ref.id)
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if not tx:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.user_id,
            gateway_name="payhere",
            amount=received_amt,
            currency=payhere_currency,
        )
        db.add(tx)
        db.flush()

    tx.gateway_transaction_id = payment_id
    tx.gateway_response_payload = str(dict(form_data))

    # PayHere Status Code: 2 = Success, 0 = Pending, -1 = Canceled, -2 = Failed
    if status_code == "2":
        # Calculate 10% platform commission and 90% provider earnings snapshot
        commission_rate = Decimal("10.00")
        commission_amount = (received_amt * Decimal("0.10")).quantize(Decimal("0.01"))
        provider_payable_amount = received_amt - commission_amount

        tx.payment_status = "paid"
        tx.paid_at = now_utc
        tx.gross_amount = received_amt
        tx.commission_rate = commission_rate
        tx.commission_amount = commission_amount
        tx.provider_payable_amount = provider_payable_amount

        homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
        h_name = homestay.name if homestay else ""
        provider_user_id = homestay.owner_id if homestay else None
        if not provider_user_id and homestay and homestay.provider_profile:
            provider_user_id = homestay.provider_profile.user_id

        # Check whether payment arrived after expiration, cancellation or rejection
        is_late_or_invalid = False
        if booking.status in ("expired", "cancelled", "rejected"):
            is_late_or_invalid = True
        elif (
            booking.status == "awaiting_payment"
            and booking.payment_expires_at is not None
            and booking.payment_expires_at <= now_utc
        ):
            is_late_or_invalid = True

        if is_late_or_invalid:
            # Verified late payment received after deadline:
            # Preserve verified payment transaction, DO NOT confirm or reactivate booking,
            # and flag for refund/reconciliation.
            if booking.status == "awaiting_payment":
                booking.status = "expired"
                booking.expired_at = booking.expired_at or now_utc

            booking.payment_status = "refund_pending"
            booking.payment_method = "online"
            booking_ref.payment_status = "refund_pending"

            # Create pending refund request for manual/automated execution
            existing_refund = (
                db.query(PaymentRefund)
                .filter(
                    PaymentRefund.payment_transaction_id == tx.id,
                    PaymentRefund.refund_status.in_(["pending", "completed"]),
                )
                .first()
            )
            if not existing_refund:
                refund = PaymentRefund(
                    payment_transaction_id=tx.id,
                    amount=received_amt,
                    currency=payhere_currency,
                    refund_reason=(
                        f"Late payment verified after booking {booking.status} status. "
                        f"Order: {order_id}. Flagged for reconciliation and refund."
                    ),
                    refund_status="pending",
                )
                db.add(refund)

            record_booking_status_change(
                db,
                booking_reference_id=booking_ref.id,
                from_status=booking.status,
                to_status=booking.status,
                changed_by=None,
                notes=f"PayHere Online Payment Received Late for {booking.status} booking (Payment ID: {payment_id}). Flagged for refund.",
            )

            create_notification(
                db=db,
                user_id=booking.user_id,
                title="Payment Received After Expiration",
                message=(
                    f"We received your payment of {payhere_amount} {payhere_currency} for booking #{booking.id}, "
                    f"but the payment window had already {booking.status}. Your payment has been flagged for refund."
                ),
                notification_type="payment_flagged_for_refund",
                link_url=f"/bookings/{booking.id}",
            )

            if provider_user_id:
                create_notification(
                    db=db,
                    user_id=provider_user_id,
                    title="Late Payment on Expired/Cancelled Booking",
                    message=(
                        f"A late online payment of {payhere_amount} {payhere_currency} arrived for booking #{booking.id} "
                        f"({booking.status}). It has been flagged for refund and did not reactivate the reservation."
                    ),
                    notification_type="payment_flagged_for_refund",
                    link_url=f"/provider/bookings/{booking.id}",
                )
        else:
            # Valid payment confirmed within deadline
            old_status = booking.status
            booking.status = "confirmed"
            booking.payment_status = "paid"
            booking.payment_method = "online"
            booking.commission_rate = commission_rate
            booking.commission_amount = commission_amount
            booking.provider_payable_amount = provider_payable_amount

            booking_ref.booking_status = "confirmed"
            booking_ref.payment_status = "paid"

            # Record attributed booking conversion if applicable
            cid = None
            try:
                import re
                history = (
                    db.query(BookingStatusHistory)
                    .filter(
                        BookingStatusHistory.booking_reference_id == booking_ref.id,
                        BookingStatusHistory.notes.like("%campaign_id:%"),
                    )
                    .first()
                )
                if history and history.notes:
                    m = re.search(r"campaign_id:(\d+)", history.notes)
                    if m:
                        cid = int(m.group(1))
            except Exception:
                cid = None

            record_campaign_booking_conversion(
                db=db,
                homestay_id=booking.homestay_id,
                user_id=booking.user_id,
                booking_reference_id=booking_ref.id,
                campaign_id=cid,
            )

            record_booking_status_change(
                db,
                booking_reference_id=booking_ref.id,
                from_status=old_status,
                to_status="confirmed",
                changed_by=None,
                notes=f"PayHere Online Payment Confirmed (Payment ID: {payment_id})",
            )

            # In-app notifications
            create_notification(
                db=db,
                user_id=booking.user_id,
                title="Online Payment Successful - Booking Confirmed!",
                message=f"Your payment of {payhere_amount} {payhere_currency} for {h_name} was received. Your booking is confirmed!",
                notification_type="payment_success",
                link_url=f"/bookings/{booking.id}",
            )

            if provider_user_id:
                create_notification(
                    db=db,
                    user_id=provider_user_id,
                    title="Online Payment Received - Booking Confirmed",
                    message=f"Traveler paid {payhere_amount} {payhere_currency} online for booking #{booking.id}. Booking is confirmed.",
                    notification_type="payment_received",
                    link_url=f"/provider/bookings/{booking.id}",
                )

    elif status_code in ("-1", "-2"):
        tx.payment_status = "failed"
        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Online Payment Failed",
            message=f"Payment for booking #{booking.id} was unsuccessful. You may retry payment.",
            notification_type="payment_failed",
            link_url=f"/bookings/{booking.id}",
        )

    db.commit()
    return {"status": "ok", "payment_status": tx.payment_status}


@router.post(
    "/payhere/notify",
    summary="PayHere Instant Payment Notification (IPN) webhook listener",
)
async def payhere_webhook_listener(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    form_data = await request.form()
    return handle_payhere_callback(form_data=dict(form_data), db=db)


# ==========================================
# 3. PAYMENT STATUS RETRIEVAL
# ==========================================

@router.get(
    "/bookings/{booking_id}/status",
    response_model=PaymentStatusResponse,
    summary="Get payment and transaction status for a homestay booking",
)
def get_booking_payment_status(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    # Permission check: traveler, homestay provider, or admin
    if booking.user_id != current_user.id and current_user.role != "admin":
        homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
        profile = None
        if homestay and (homestay.provider_profile_id or current_user.role == "provider"):
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        if not homestay or not check_homestay_ownership(homestay, current_user, profile):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to view payment status",
            )

    tx = (
        db.query(PaymentTransaction)
        .join(
            BookingReference,
            PaymentTransaction.booking_reference_id == BookingReference.id,
        )
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.target_booking_id == booking.id,
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )

    return PaymentStatusResponse(
        booking_id=booking.id,
        booking_status=booking.status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        total_price=booking.total_price,
        currency=booking.currency or "LKR",
        paid_at=tx.paid_at if tx else None,
        transaction_id=tx.id if tx else None,
    )


# ==========================================
# 4. REFUND MANAGEMENT
# ==========================================

@router.post(
    "/bookings/{booking_id}/request-refund",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a refund for a cancelled homestay booking",
)
def request_booking_refund(
    booking_id: int,
    payload: RefundRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    if booking.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only request refunds for your own bookings",
        )

    if booking.status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Refunds can only be requested for cancelled bookings",
        )

    # Locate paid transaction
    tx = (
        db.query(PaymentTransaction)
        .join(
            BookingReference,
            PaymentTransaction.booking_reference_id == BookingReference.id,
        )
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.target_booking_id == booking.id,
            PaymentTransaction.payment_status == "paid",
        )
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    if not tx:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No paid online payment transaction found to refund",
        )

    # Prevent duplicate or excessive refunds
    existing_refunds_total = (
        db.query(func.coalesce(func.sum(PaymentRefund.amount), Decimal("0.00")))
        .filter(
            PaymentRefund.payment_transaction_id == tx.id,
            PaymentRefund.refund_status.in_(["pending", "approved", "completed"]),
        )
        .scalar()
    )

    remaining_refundable = tx.amount - existing_refunds_total
    if remaining_refundable <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Full payment has already been refunded or is currently pending refund",
        )

    refund = PaymentRefund(
        payment_transaction_id=tx.id,
        amount=remaining_refundable,
        currency=tx.currency,
        refund_reason=payload.reason,
        refund_status="pending",
    )
    db.add(refund)

    # In-app notification to traveler and admin
    create_notification(
        db=db,
        user_id=booking.user_id,
        title="Refund Request Submitted",
        message=f"Your refund request for {remaining_refundable} {tx.currency} is under review.",
        notification_type="refund_requested",
        link_url=f"/bookings/{booking.id}",
    )

    db.commit()
    db.refresh(refund)
    return refund


@router.post(
    "/admin/refunds/{refund_id}/process",
    response_model=RefundResponse,
    summary="Admin review and status update for a refund request",
)
def process_refund(
    refund_id: int,
    payload: RefundProcessRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    refund = db.query(PaymentRefund).filter(PaymentRefund.id == refund_id).first()
    if not refund:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Refund record not found",
        )

    valid_statuses = {"approved", "rejected", "completed"}
    if payload.status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Refund status must be one of {valid_statuses}",
        )

    if payload.status == "completed":
        if refund.refund_status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot complete a rejected refund without reopening or re-approving it",
            )
        if not payload.notes or len(payload.notes.strip()) < 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Refund cannot be marked as completed without verified refund execution evidence (gateway transaction ID or bank transfer proof recorded in notes)",
            )
        refund.refund_reason = f"{refund.refund_reason} | Execution: {payload.notes.strip()}"

    refund.refund_status = payload.status
    refund.processed_by = admin.id
    refund.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Notify traveler
    tx = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id == refund.payment_transaction_id)
        .first()
    )
    if tx:
        create_notification(
            db=db,
            user_id=tx.user_id,
            title=f"Refund Request {payload.status.title()}",
            message=f"Your refund of {refund.amount} {refund.currency} has been marked {payload.status}.",
            notification_type=f"refund_{payload.status}",
            link_url="/notifications",
        )

    db.commit()
    db.refresh(refund)
    return refund


# ==========================================
# 5. PROVIDER EARNINGS & SETTLEMENTS
# ==========================================

def calculate_provider_earnings_balances(
    db: Session,
    provider_profile_id: int,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict:
    """
    Calculate server-authoritative provider earnings balances for online homestay bookings:
    - total_gross_earnings: all paid online transactions for homestays owned by provider
    - completed_booking_earnings: gross amount for bookings with status == 'completed'
    - ceylongo_commission: net 10% platform commission on completed bookings (after commission reversal)
    - completed_provider_payable: sum of provider_payable_amount (90%) on completed bookings
    - verified_refunds: total completed refunds on paid transactions
    - provider_refund_reduction: 90% provider share of completed refunds
    - provider_net_earnings: max(0, completed_provider_payable - provider_refund_reduction)
    - pending_earnings: sum of provider_payable_amount for paid bookings where status in ('confirmed', 'in_progress')
    - total_settled: sum of ProviderSettlement.net_payable where settlement_status in ('completed', 'paid')
    - reserved_settlement: sum of ProviderSettlement.net_payable where settlement_status in ('pending', 'approved')
    - available_settlement_balance: max(0, provider_net_earnings - total_settled - reserved_settlement)
    - outstanding_liability: max(0, (total_settled + reserved_settlement) - provider_net_earnings)
    """
    rows = (
        db.query(PaymentTransaction, Booking)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(Booking, BookingReference.target_booking_id == Booking.id)
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.provider_profile_id == provider_profile_id,
            PaymentTransaction.payment_status == "paid",
        )
        .all()
    )

    total_gross = Decimal("0.00")
    completed_gross = Decimal("0.00")
    completed_commission = Decimal("0.00")
    completed_provider_payable = Decimal("0.00")
    pending_earnings = Decimal("0.00")
    paid_count = 0
    tx_ids = []

    for tx, booking in rows:
        # If filtering by settlement period, check-out date must fall within the requested period
        if period_start and booking.check_out < period_start:
            continue
        if period_end and booking.check_out > period_end:
            continue

        paid_count += 1
        tx_ids.append(tx.id)
        gross = tx.gross_amount or tx.amount or Decimal("0.00")
        total_gross += gross

        if booking.status == "completed":
            completed_gross += gross
            comm = (
                tx.commission_amount
                if tx.commission_amount is not None
                else (gross * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
            completed_commission += comm
            payable = (
                tx.provider_payable_amount
                if tx.provider_payable_amount is not None
                else (gross - comm)
            )
            completed_provider_payable += payable
        elif booking.status in ("confirmed", "in_progress"):
            comm = (
                tx.commission_amount
                if tx.commission_amount is not None
                else (gross * Decimal("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            )
            payable = (
                tx.provider_payable_amount
                if tx.provider_payable_amount is not None
                else (gross - comm)
            )
            pending_earnings += payable

    # Verified completed refunds
    verified_refunds = Decimal("0.00")
    provider_refund_reduction = Decimal("0.00")
    commission_reversal = Decimal("0.00")
    if tx_ids:
        refund_rows = (
            db.query(PaymentRefund)
            .filter(
                PaymentRefund.payment_transaction_id.in_(tx_ids),
                PaymentRefund.refund_status == "completed",
            )
            .all()
        )
        for r in refund_rows:
            verified_refunds += r.amount
            comm_rev = (r.amount * Decimal("0.10")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            prov_deduct = r.amount - comm_rev
            commission_reversal += comm_rev
            provider_refund_reduction += prov_deduct

    provider_net_earnings = max(
        Decimal("0.00"), completed_provider_payable - provider_refund_reduction
    )

    # Historical and reserved settlements
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

    settlement_commitments = total_settled + reserved_settlement
    settlement_deficit = settlement_commitments - (
        completed_provider_payable - provider_refund_reduction
    )
    outstanding_liability = max(Decimal("0.00"), settlement_deficit)

    available_settlement_balance = max(
        Decimal("0.00"),
        (completed_provider_payable - provider_refund_reduction) - settlement_commitments,
    )

    net_ceylongo_commission = max(Decimal("0.00"), completed_commission - commission_reversal)

    return {
        "total_gross_earnings": total_gross,
        "completed_booking_earnings": completed_gross,
        "ceylongo_commission": net_ceylongo_commission,
        "commission_reversal": commission_reversal,
        "verified_refunds": verified_refunds,
        "provider_refund_reduction": provider_refund_reduction,
        "provider_net_earnings": provider_net_earnings,
        "pending_earnings": pending_earnings,
        "total_settled": total_settled,
        "reserved_settlement": reserved_settlement,
        "available_settlement_balance": available_settlement_balance,
        "outstanding_liability": outstanding_liability,
        "total_paid_bookings": paid_count,
    }


@router.get(
    "/provider/earnings",
    response_model=ProviderEarningsSummaryResponse,
    summary="Summary of online booking earnings and settlement balances for provider",
)
def get_provider_earnings_summary(
    current_user: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )
    if not profile and current_user.role != "admin":
        return ProviderEarningsSummaryResponse(
            total_gross_earnings=Decimal("0.00"),
            completed_booking_earnings=Decimal("0.00"),
            ceylongo_commission=Decimal("0.00"),
            verified_refunds=Decimal("0.00"),
            provider_net_earnings=Decimal("0.00"),
            pending_earnings=Decimal("0.00"),
            available_settlement_balance=Decimal("0.00"),
            total_settled=Decimal("0.00"),
            pending_settlement=Decimal("0.00"),
            total_paid_bookings=0,
        )

    profile_id = profile.id if profile else None
    if not profile_id:
        return ProviderEarningsSummaryResponse(
            total_gross_earnings=Decimal("0.00"),
            completed_booking_earnings=Decimal("0.00"),
            ceylongo_commission=Decimal("0.00"),
            verified_refunds=Decimal("0.00"),
            provider_net_earnings=Decimal("0.00"),
            pending_earnings=Decimal("0.00"),
            available_settlement_balance=Decimal("0.00"),
            total_settled=Decimal("0.00"),
            pending_settlement=Decimal("0.00"),
            total_paid_bookings=0,
        )

    b = calculate_provider_earnings_balances(db, profile_id)

    return ProviderEarningsSummaryResponse(
        total_gross_earnings=b["total_gross_earnings"],
        completed_booking_earnings=b["completed_booking_earnings"],
        ceylongo_commission=b["ceylongo_commission"],
        verified_refunds=b["verified_refunds"],
        provider_net_earnings=b["provider_net_earnings"],
        pending_earnings=b["pending_earnings"],
        available_settlement_balance=b["available_settlement_balance"],
        total_settled=b["total_settled"],
        pending_settlement=b["available_settlement_balance"],
        outstanding_liability=b["outstanding_liability"],
        total_paid_bookings=b["total_paid_bookings"],
    )


@router.get(
    "/provider/settlements",
    response_model=list[SettlementResponse],
    summary="List provider settlement records",
)
def list_provider_settlements(
    current_user: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )
    query = db.query(ProviderSettlement)
    if profile and current_user.role != "admin":
        query = query.filter(ProviderSettlement.provider_profile_id == profile.id)

    return query.order_by(ProviderSettlement.created_at.desc()).all()


@router.get(
    "/provider/settlements/{settlement_id}",
    response_model=SettlementResponse,
    summary="Get single settlement details",
)
def get_settlement_detail(
    settlement_id: int,
    current_user: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    settlement = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.id == settlement_id)
        .first()
    )
    if not settlement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement not found",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )
    if current_user.role != "admin" and (
        not profile or settlement.provider_profile_id != profile.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this settlement",
        )

    return settlement


# ==========================================
# 6. ADMIN PAYMENT & SETTLEMENT OVERSIGHT
# ==========================================

@router.get(
    "/admin/transactions",
    response_model=list[PaymentTransactionResponse],
    summary="Admin oversight: list all payment transactions",
)
def list_all_payment_transactions(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return (
        db.query(PaymentTransaction)
        .order_by(PaymentTransaction.created_at.desc())
        .limit(limit)
        .all()
    )


@router.get(
    "/admin/refunds",
    response_model=list[RefundResponse],
    summary="Admin oversight: list all refund requests",
)
def list_all_refunds(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return (
        db.query(PaymentRefund)
        .order_by(PaymentRefund.created_at.desc())
        .limit(limit)
        .all()
    )


@router.post(
    "/admin/settlements",
    response_model=SettlementResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: record provider earnings settlement payout",
)
def create_provider_settlement(
    payload: SettlementCreateRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == payload.provider_profile_id)
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider profile not found",
        )

    if payload.period_end < payload.period_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="period_end must be greater than or equal to period_start",
        )

    if payload.net_payable > payload.gross_amount or payload.platform_fee < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Excessive settlement net_payable exceeds gross_amount or invalid platform fee",
        )

    # 1. Prevent overlapping settlement periods for the provider
    overlapping = (
        db.query(ProviderSettlement)
        .filter(
            ProviderSettlement.provider_profile_id == payload.provider_profile_id,
            ProviderSettlement.settlement_status.in_(["pending", "approved", "completed", "paid"]),
            ProviderSettlement.period_start <= payload.period_end,
            ProviderSettlement.period_end >= payload.period_start,
        )
        .first()
    )
    if overlapping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Settlement period ({payload.period_start} to {payload.period_end}) overlaps with existing settlement #{overlapping.id} ({overlapping.period_start} to {overlapping.period_end})",
        )

    # 2. Prevent settlements exceeding the server-verified available balance
    balances = calculate_provider_earnings_balances(db, payload.provider_profile_id)
    verified_outstanding = balances["available_settlement_balance"]
    if payload.net_payable > verified_outstanding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Settlement net payable ({payload.net_payable} LKR) exceeds the verified available balance ({verified_outstanding} LKR)",
        )

    # 3. Prevent settlements exceeding eligible earnings within requested period
    period_balances = calculate_provider_earnings_balances(
        db, payload.provider_profile_id, period_start=payload.period_start, period_end=payload.period_end
    )
    period_available = period_balances["available_settlement_balance"]
    if payload.net_payable > period_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Settlement net payable ({payload.net_payable} LKR) exceeds eligible earnings for period {payload.period_start} to {payload.period_end} ({period_available} LKR)",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    settlement_status = payload.settlement_status
    payout_ref = payload.payout_reference
    paid_at = None

    # Payout completion requires independently verifiable payment evidence
    if settlement_status == "completed":
        if not payout_ref or len(payout_ref.strip()) < 5:
            # Preserve pending status if external verified payout integration is unavailable
            settlement_status = "pending"
            payout_ref = "Awaiting independently verified bank payout reference"
        else:
            paid_at = now_utc

    settlement = ProviderSettlement(
        provider_profile_id=payload.provider_profile_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        gross_amount=payload.gross_amount,
        platform_fee=payload.platform_fee,
        net_payable=payload.net_payable,
        currency="LKR",
        settlement_status=settlement_status,
        payout_reference=payout_ref,
        paid_at=paid_at,
    )
    db.add(settlement)

    create_notification(
        db=db,
        user_id=profile.user_id,
        title="Earnings Settlement Processed",
        message=f"A settlement of {payload.net_payable} LKR for period {payload.period_start} to {payload.period_end} has been recorded (Status: {settlement_status}).",
        notification_type="settlement_processed",
        link_url="/provider/earnings",
    )

    db.commit()
    db.refresh(settlement)
    return settlement


@router.get(
    "/admin/summary",
    response_model=AdminFinancialSummaryResponse,
    summary="Admin financial overview: gross revenue, commission, liabilities, settlements",
)
def admin_get_financial_summary(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Overview of CeylonGo financial positions across booking revenue, commission,
    refunds, provider liabilities, payouts, and advertising fees.
    """
    tx_rows = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.payment_status == "paid")
        .all()
    )
    total_gross_booking = sum((t.gross_amount or t.amount or Decimal("0.00")) for t in tx_rows)
    total_commission = sum((t.commission_amount or Decimal("0.00")) for t in tx_rows)

    refund_rows = (
        db.query(PaymentRefund)
        .filter(PaymentRefund.refund_status == "completed")
        .all()
    )
    total_refunds = sum(r.amount for r in refund_rows)
    refunded_commission = (total_refunds * Decimal("0.10")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    net_commission = max(Decimal("0.00"), total_commission - refunded_commission)

    settlement_rows = db.query(ProviderSettlement).all()
    total_settled = sum(
        s.net_payable for s in settlement_rows if s.settlement_status in ("completed", "paid")
    )
    pending_settlement = sum(
        s.net_payable for s in settlement_rows if s.settlement_status in ("pending", "approved")
    )

    providers = db.query(ProviderProfile).all()
    total_liabilities = sum(
        calculate_provider_earnings_balances(db, p.id)["available_settlement_balance"]
        for p in providers
    )

    ad_rows = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.payment_status == "completed")
        .all()
    )
    total_advertising = sum(a.amount for a in ad_rows)

    return AdminFinancialSummaryResponse(
        total_gross_booking_revenue=total_gross_booking,
        total_ceylongo_commission=net_commission,
        total_provider_liabilities=total_liabilities,
        total_verified_refunds=total_refunds,
        total_settled_payouts=total_settled,
        pending_settlement_payouts=pending_settlement,
        total_advertising_revenue=total_advertising,
        currency="LKR",
    )


@router.get(
    "/admin/reconciliation-queue",
    response_model=list[PaymentTransactionResponse],
    summary="Admin list of payments flagged for reconciliation or late verification",
)
def admin_get_reconciliation_queue(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    List online payment transactions flagged for financial reconciliation.
    Includes verified late payments on expired/cancelled bookings and pending refunds.
    """
    flagged_tx_ids = set()

    # 1. Transactions with pending or approved refunds
    pending_refund_txs = (
        db.query(PaymentRefund.payment_transaction_id)
        .filter(PaymentRefund.refund_status.in_(["pending", "approved"]))
        .all()
    )
    for row in pending_refund_txs:
        flagged_tx_ids.add(row[0])

    # 2. Paid transactions where booking is expired, cancelled, or rejected
    late_booking_txs = (
        db.query(PaymentTransaction.id)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(Booking, BookingReference.target_booking_id == Booking.id)
        .filter(
            PaymentTransaction.payment_status == "paid",
            Booking.status.in_(["expired", "cancelled", "rejected"]),
        )
        .all()
    )
    for row in late_booking_txs:
        flagged_tx_ids.add(row[0])

    if not flagged_tx_ids:
        return []

    return (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id.in_(flagged_tx_ids))
        .order_by(PaymentTransaction.created_at.desc())
        .all()
    )
