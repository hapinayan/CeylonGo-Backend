from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import func
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
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.payment import (
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

    # Online booking rule: provider must approve before accepting payment
    if booking.status != "confirmed":
        if booking.status == "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Booking is pending provider approval. Online payment cannot be initiated until accepted.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot initiate payment for booking with status '{booking.status}'",
        )

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


# ==========================================
# 2. PAYHERE WEBHOOK / NOTIFICATION LISTENER
# ==========================================

@router.post(
    "/payhere/notify",
    summary="PayHere Instant Payment Notification (IPN) webhook listener",
)
async def payhere_webhook_listener(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
):
    form_data = await request.form()

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

    # 4. Idempotency: if already paid, return 200 immediately
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

    tx.gateway_transaction_id = payment_id
    tx.gateway_response_payload = str(dict(form_data))

    # PayHere Status Code: 2 = Success, 0 = Pending, -1 = Canceled, -2 = Failed
    if status_code == "2":
        tx.payment_status = "paid"
        tx.paid_at = now_utc
        booking.payment_status = "paid"
        booking.payment_method = "online"
        booking_ref.payment_status = "paid"

        record_booking_status_change(
            db,
            booking_reference_id=booking_ref.id,
            from_status=booking.status,
            to_status=booking.status,
            changed_by=None,
            notes=f"PayHere Online Payment Confirmed (Payment ID: {payment_id})",
        )

        homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
        h_name = homestay.name if homestay else ""

        # In-app notifications
        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Online Payment Successful!",
            message=f"Your payment of {payhere_amount} {payhere_currency} for {h_name} was received.",
            notification_type="payment_success",
            link_url=f"/bookings/{booking.id}",
        )

        provider_user_id = homestay.owner_id if homestay else None
        if not provider_user_id and homestay and homestay.provider_profile:
            provider_user_id = homestay.provider_profile.user_id

        if provider_user_id:
            create_notification(
                db=db,
                user_id=provider_user_id,
                title="Online Payment Received",
                message=f"Traveler paid {payhere_amount} {payhere_currency} online for booking #{booking.id}.",
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
            total_settled=Decimal("0.00"),
            pending_settlement=Decimal("0.00"),
            total_paid_bookings=0,
        )

    profile_id = profile.id if profile else None

    # Calculate total gross earnings from paid online transactions
    tx_query = (
        db.query(
            func.coalesce(func.sum(PaymentTransaction.amount), Decimal("0.00")),
            func.count(PaymentTransaction.id),
        )
        .join(
            BookingReference,
            PaymentTransaction.booking_reference_id == BookingReference.id,
        )
        .filter(
            BookingReference.module_type == "homestay",
            PaymentTransaction.payment_status == "paid",
        )
    )
    if profile_id:
        tx_query = tx_query.filter(BookingReference.provider_profile_id == profile_id)

    gross_earnings, paid_count = tx_query.first()

    # Calculate total settled amounts
    settle_query = db.query(
        func.coalesce(func.sum(ProviderSettlement.net_payable), Decimal("0.00"))
    ).filter(ProviderSettlement.settlement_status == "completed")
    if profile_id:
        settle_query = settle_query.filter(ProviderSettlement.provider_profile_id == profile_id)

    total_settled = settle_query.scalar()
    pending_settlement = max(Decimal("0.00"), gross_earnings - total_settled)

    return ProviderEarningsSummaryResponse(
        total_gross_earnings=gross_earnings,
        total_settled=total_settled,
        pending_settlement=pending_settlement,
        total_paid_bookings=paid_count,
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
            ProviderSettlement.settlement_status.in_(["pending", "completed"]),
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

    # 2. Prevent settlements exceeding the verified outstanding balance
    gross_earnings = (
        db.query(func.coalesce(func.sum(PaymentTransaction.amount), Decimal("0.00")))
        .join(
            BookingReference,
            PaymentTransaction.booking_reference_id == BookingReference.id,
        )
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.provider_profile_id == payload.provider_profile_id,
            PaymentTransaction.payment_status == "paid",
        )
        .scalar()
    ) or Decimal("0.00")

    already_settled = (
        db.query(func.coalesce(func.sum(ProviderSettlement.net_payable), Decimal("0.00")))
        .filter(
            ProviderSettlement.provider_profile_id == payload.provider_profile_id,
            ProviderSettlement.settlement_status.in_(["pending", "completed"]),
        )
        .scalar()
    ) or Decimal("0.00")

    verified_outstanding = max(Decimal("0.00"), gross_earnings - already_settled)
    if payload.net_payable > verified_outstanding:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Settlement net payable ({payload.net_payable} LKR) exceeds the verified outstanding balance ({verified_outstanding} LKR)",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    settlement = ProviderSettlement(
        provider_profile_id=payload.provider_profile_id,
        period_start=payload.period_start,
        period_end=payload.period_end,
        gross_amount=payload.gross_amount,
        platform_fee=payload.platform_fee,
        net_payable=payload.net_payable,
        currency="LKR",
        settlement_status=payload.settlement_status,
        payout_reference=payload.payout_reference,
        paid_at=now_utc if payload.settlement_status == "completed" else None,
    )
    db.add(settlement)

    create_notification(
        db=db,
        user_id=profile.user_id,
        title="Earnings Settlement Processed",
        message=f"A settlement of {payload.net_payable} LKR for period {payload.period_start} to {payload.period_end} has been recorded.",
        notification_type="settlement_processed",
        link_url="/provider/earnings",
    )

    db.commit()
    db.refresh(settlement)
    return settlement
