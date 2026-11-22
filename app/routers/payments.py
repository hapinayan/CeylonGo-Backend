import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.booking_transactions import (
    CAPTURED, EXECUTED_REFUNDS, RESERVED_REFUNDS, capture_status,
    executed_refund_status, lock_booking_resource,
)
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
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdminAuditLog,
    AdvertisingPayment,
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.tour_guide import TourGuideBooking
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
from app.schemas.tour_guide import TourGuidePaymentPrepareResponse
from app.services.booking_expiration import expire_single_booking
from app.services.guide_payments import handle_guide_callback
from app.services.payhere_bookings import capture_amount, checkout_response, lock_capture
from app.services.tour_guide_phase5 import (
    handle_tour_guide_advertising_payment_callback,
)
from app.services.activity_payments import (
    get_activity_payment_status,
    get_activity_provider_earnings_components,
    handle_activity_payhere_callback,
    prepare_activity_online_payment,
)
from app.services.tour_guide_payment import (
    get_tour_guide_online_earnings_components,
    handle_tour_guide_payhere_callback,
    prepare_tour_guide_online_payment,
    sync_tour_guide_refund_completion,
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

    lock_booking_resource(db, f"homestay_booking_{booking.homestay_id}")
    db.refresh(booking)
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
                payment_status=capture_status(db),
                payment_method="online",
                paid_at=now_utc,
            )
            db.add(tx)
        else:
            tx.payment_status = capture_status(db)
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

    return checkout_response(
        booking.id,
        booking_ref.booking_reference,
        f"Homestay: {homestay.name} ({booking.check_in} to {booking.check_out})",
        booking.total_price,
        booking.currency or "LKR",
        current_user,
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
# 1B. PAYHERE ONLINE PAYMENT INITIATION FOR RENTALS (TR-11)
# ==========================================

@router.post(
    "/rental/bookings/{booking_id}/initiate",
    response_model=PaymentInitiateResponse,
    summary="Initiate PayHere online payment for a rental booking (TR-11)",
)
def initiate_rental_booking_payment(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    rental_booking = db.query(RentalBooking).filter(RentalBooking.id == booking_id).first()
    if not rental_booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found",
        )

    if rental_booking.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate payment for your own bookings",
        )

    lock_booking_resource(db, f"rental_vehicle_booking_{rental_booking.vehicle_id}")
    db.refresh(rental_booking)

    if rental_booking.status in ("cancelled", "rejected", "expired", "completed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot initiate payment for rental booking with status '{rental_booking.status}'",
        )

    if rental_booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This rental booking has already been paid",
        )

    vehicle = rental_booking.vehicle or db.get(RentalVehicle, rental_booking.vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found",
        )

    # Locate or create BookingReference for this rental booking
    import uuid
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == rental_booking.id,
        )
        .first()
    )
    if not booking_ref:
        ref_code = f"RB-{rental_booking.id}-{uuid.uuid4().hex[:8].upper()}"
        booking_ref = BookingReference(
            booking_reference=ref_code,
            module_type="rental_vehicle",
            target_booking_id=rental_booking.id,
            user_id=rental_booking.user_id,
            provider_profile_id=vehicle.provider_id,
            total_price=rental_booking.total_price,
            currency=rental_booking.currency or "LKR",
            booking_status=rental_booking.status,
            payment_status=rental_booking.payment_status or "unpaid",
        )
        db.add(booking_ref)
        db.flush()

    rental_booking.payment_method = "online"

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
            user_id=rental_booking.user_id,
            gateway_name="payhere",
            amount=rental_booking.total_price,
            currency=rental_booking.currency or "LKR",
            payment_status="pending",
            payment_method="online",
        )
        db.add(tx)
    else:
        tx.amount = rental_booking.total_price
        tx.currency = rental_booking.currency or "LKR"

    db.commit()

    order_id = booking_ref.booking_reference
    checkout_hash = payhere_service.generate_checkout_hash(
        order_id=order_id,
        amount=rental_booking.total_price,
        currency=rental_booking.currency or "LKR",
    )

    name_parts = (current_user.full_name or "Traveler").split(" ", 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"

    return PaymentInitiateResponse(
        booking_id=rental_booking.id,
        booking_reference=order_id,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        order_id=order_id,
        items=f"Rental: {vehicle.vehicle_name} ({rental_booking.pickup_at.strftime('%Y-%m-%d')} to {rental_booking.return_at.strftime('%Y-%m-%d')})",
        amount=rental_booking.total_price,
        currency=rental_booking.currency or "LKR",
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
# 1C. PAYHERE ONLINE PAYMENT INITIATION FOR TOUR GUIDES (TR-12)
# ==========================================

@router.post(
    "/tour-guides/bookings/{booking_id}/initiate",
    response_model=TourGuidePaymentPrepareResponse,
    summary="Initiate PayHere online payment for an accepted tour guide booking (TR-12)",
)
def initiate_tour_guide_booking_payment(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return prepare_tour_guide_online_payment(
        db,
        booking_id=booking_id,
        current_user=current_user,
    )


# ==========================================
# 1D. PAYHERE ONLINE PAYMENT INITIATION FOR ACTIVITIES (A09)
# ==========================================

@router.post(
    "/activities/bookings/{booking_id}/initiate",
    response_model=PaymentInitiateResponse,
    summary="Initiate PayHere online payment for an activity booking (A09)",
)
def initiate_activity_booking_payment(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return prepare_activity_online_payment(
        db=db,
        booking_id=booking_id,
        current_user=current_user,
    )


# ==========================================
# 2. PAYHERE WEBHOOK / NOTIFICATION LISTENER
# ==========================================

def _handle_rental_capture(db, ref, payment_id, raw_amount, currency, status_code):
    booking = db.get(RentalBooking, ref.target_booking_id)
    if booking is None:
        raise HTTPException(404, "Associated rental booking not found")
    amount = capture_amount(raw_amount)
    if status_code == "2":
        lock_capture(db, payment_id, ref.id)
    lock_booking_resource(db, f"rental_vehicle_booking_{booking.vehicle_id}")
    db.refresh(booking)
    db.refresh(ref)
    if ref.user_id != booking.user_id or amount != booking.total_price or currency != (booking.currency or "LKR"):
        raise HTTPException(400, "Payment does not match rental booking")
    if booking.payment_status in ("paid", "refund_pending", "refunded"):
        return {"status": "already_processed"}
    tx = db.scalar(select(PaymentTransaction).where(
        PaymentTransaction.booking_reference_id == ref.id,
        PaymentTransaction.user_id == booking.user_id,
        PaymentTransaction.gateway_name == "payhere",
    ).order_by(PaymentTransaction.id.desc()).limit(1))
    if tx is None:
        tx = PaymentTransaction(
            booking_reference_id=ref.id, user_id=booking.user_id,
            gateway_name="payhere", payment_method="online", amount=amount, currency=currency,
        )
        db.add(tx)
    tx.gateway_transaction_id = payment_id
    if status_code == "2":
        tx.payment_status = capture_status(db)
        tx.paid_at = datetime.now(timezone.utc).replace(tzinfo=None)
        tx.gross_amount = amount
        tx.commission_rate = Decimal("10.00")
        tx.commission_amount = (amount * Decimal("0.10")).quantize(Decimal("0.01"))
        tx.provider_payable_amount = amount - tx.commission_amount
        if booking.status in ("cancelled", "rejected", "expired"):
            db.flush()
            db.add(PaymentRefund(
                payment_transaction_id=tx.id, amount=amount, currency=currency,
                refund_status="pending", refund_reason="Verified late rental capture requires reconciliation.",
            ))
            booking.payment_status = ref.payment_status = "refund_pending"
            record_booking_status_change(
                db, ref.id, booking.status, booking.status, None,
                notes="Late PayHere rental capture queued for reconciliation",
            )
            db.commit()
            return {"status": "requires_reconciliation", "payment_status": "refund_pending"}
        booking.payment_status = ref.payment_status = "paid"
        booking.payment_method = "online"
        record_booking_status_change(
            db, ref.id, booking.status, booking.status, None,
            notes="PayHere rental payment confirmed",
        )
        create_notification(db, booking.user_id, "Rental Payment Successful!",
                            "Your online rental payment was received.", "payment_success",
                            f"/rentals/bookings/{booking.id}")
        if booking.vehicle and booking.vehicle.provider:
            create_notification(db, booking.vehicle.provider.user_id, "Rental Payment Received",
                                "An online rental payment was received.", "payment_received",
                                f"/provider/rentals/bookings/{booking.id}")
    elif status_code in ("-1", "-2"):
        tx.payment_status = "failed"
    db.commit()
    return {"status": "ok", "payment_status": "paid" if tx.payment_status in CAPTURED else tx.payment_status}


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

    if merchant_id != payhere_service.merchant_id:
        raise HTTPException(400, "Payment merchant mismatch")

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
    if order_id.startswith("AD-TG-"):
        return handle_tour_guide_advertising_payment_callback(
            db=db,
            form_data=dict(form_data),
        )

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

    if booking_ref.module_type == "rental_vehicle":
        return _handle_rental_capture(
            db, booking_ref, payment_id, payhere_amount, payhere_currency, status_code,
        )
    if booking_ref.module_type == "tour_guide":
        # Explicit dual-system routing:
        # - Module 05 tour_guide_bookings use 'BK-TG-*' booking references -> isolated Module 05 handler.
        # - PR #25 unified provider_reservations use 'RS-*' booking references -> Phase 03B guide handler.
        if (booking_ref.booking_reference or "").startswith("BK-TG-"):
            return handle_tour_guide_payhere_callback(
                db=db,
                form_data=dict(form_data),
                booking_ref=booking_ref,
                verify_signature=True,
            )
        return handle_guide_callback(
            db, booking_ref, payment_id, payhere_amount, payhere_currency, status_code,
        )
    if booking_ref.module_type == "activity":
        return handle_activity_payhere_callback(
            db=db,
            form_data=dict(form_data),
            booking_ref=booking_ref,
        )
    if booking_ref.module_type != "homestay":
        raise HTTPException(400, "Unsupported booking reference module type")

    booking = db.query(Booking).filter(Booking.id == booking_ref.target_booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated booking not found",
        )

    # 3. Verify amount and currency
    received_amt = capture_amount(payhere_amount)
    if received_amt != booking.total_price or payhere_currency != (booking.currency or "LKR"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount or currency mismatch with booking record",
        )

    if booking_ref.user_id != booking.user_id or booking.payment_method != "online":
        raise HTTPException(409, "Payment does not match the booking checkout")
    if status_code == "2":
        lock_capture(db, payment_id, booking_ref.id)
    lock_booking_resource(db, f"homestay_booking_{booking.homestay_id}")
    db.refresh(booking)
    db.refresh(booking_ref)
    if booking_ref.user_id != booking.user_id or booking.payment_method != "online":
        raise HTTPException(409, "Checkout has changed")
    if received_amt != booking.total_price or payhere_currency != booking.currency:
        raise HTTPException(409, "Checkout amount or currency has changed")
    if booking.payment_status in ("paid", "refund_pending", "refunded"):
        return {"status": "already_processed"}

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

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

    # PayHere Status Code: 2 = Success, 0 = Pending, -1 = Canceled, -2 = Failed
    if status_code == "2":
        # Calculate 10% platform commission and 90% provider earnings snapshot
        commission_rate = Decimal("10.00")
        commission_amount = (received_amt * Decimal("0.10")).quantize(Decimal("0.01"))
        provider_payable_amount = received_amt - commission_amount

        tx.payment_status = capture_status(db)
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
        if booking.status not in ("awaiting_payment", "confirmed"):
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
            booking_ref.booking_status = booking.status

            # Create pending refund request for manual/automated execution
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
    public_status = "paid" if tx.payment_status in CAPTURED else tx.payment_status
    return {"status": "ok", "payment_status": public_status}


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
        require_homestay_manager(current_user, db)
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
            BookingReference.user_id == booking.user_id,
            PaymentTransaction.user_id == booking.user_id,
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


@router.get(
    "/activities/bookings/{booking_id}/status",
    response_model=PaymentStatusResponse,
    summary="Get payment and transaction status for an activity booking (A09)",
)
def get_activity_booking_payment_status(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    return get_activity_payment_status(
        db=db,
        booking_id=booking_id,
        current_user=current_user,
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

    lock_booking_resource(db, f"homestay_booking_{booking.homestay_id}")
    db.refresh(booking)
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
            PaymentTransaction.payment_status.in_(CAPTURED),
            PaymentTransaction.payment_method == "online",
            PaymentTransaction.user_id == booking.user_id,
            BookingReference.user_id == booking.user_id,
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
            PaymentRefund.refund_status.in_(RESERVED_REFUNDS),
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
    booking.payment_status = "refund_pending"
    db.get(BookingReference, tx.booking_reference_id).payment_status = "refund_pending"

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

    tx = db.get(PaymentTransaction, refund.payment_transaction_id)
    ref = db.get(BookingReference, tx.booking_reference_id) if tx else None

    if ref and ref.module_type == "tour_guide":
        tg_booking = db.get(TourGuideBooking, ref.target_booking_id)
        if (
            tg_booking is None
            or ref.user_id != tg_booking.traveler_id
            or tx.user_id != tg_booking.traveler_id
        ):
            raise HTTPException(409, "Refund has no matching Tour Guide booking")
        lock_booking_resource(db, f"tour_guide_booking_{tg_booking.tour_guide_id}")
        for record in (refund, tx, ref, tg_booking):
            db.refresh(record)
        if payload.status not in ("approved", "rejected", "completed"):
            raise HTTPException(400, "Invalid refund decision")
        if refund.refund_status not in ("pending", "approved"):
            raise HTTPException(409, "Refund already has a final decision")
        if tx.payment_status not in CAPTURED or refund.currency != tx.currency:
            raise HTTPException(409, "Refund does not match a captured payment")

        if payload.notes:
            note = payload.notes.strip()
            if note:
                base_reason = (refund.refund_reason or "")[:480]
                refund.refund_reason = (
                    f"{base_reason} | Admin Note: {note}" if base_reason else note
                )[:500]

        refund.refund_status = payload.status
        refund.processed_by = admin.id
        refund.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.flush()

        executed, awaiting = db.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                PaymentRefund.refund_status.in_(EXECUTED_REFUNDS),
                                PaymentRefund.amount,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(
                    case((PaymentRefund.refund_status.in_(("pending", "approved")), 1))
                ),
            ).where(PaymentRefund.payment_transaction_id == tx.id)
        ).one()
        if executed > tx.amount:
            db.rollback()
            raise HTTPException(409, "Refund exceeds captured amount")

        if payload.status == "completed":
            sync_tour_guide_refund_completion(
                db,
                booking_ref=ref,
                refund=refund,
                admin_id=admin.id,
            )
        elif payload.status == "rejected" and not awaiting and executed < tx.amount:
            tg_booking.payment_status = "paid"
            ref.payment_status = "paid"

        db.add(
            AdminAuditLog(
                admin_user_id=admin.id,
                action=f"refund_{payload.status}",
                entity_name="payment_refunds",
                entity_id=refund.id,
            )
        )
        create_notification(
            db=db,
            user_id=tg_booking.traveler_id,
            title=f"Refund Request {payload.status.title()}",
            message=(
                f"Your refund of {refund.amount} {refund.currency} was marked {payload.status}."
            ),
            notification_type=f"refund_{payload.status}",
            link_url=f"/tour-guide-bookings/{tg_booking.id}",
        )
        db.commit()
        db.refresh(refund)
        return refund

    booking = (
        db.get(Booking, ref.target_booking_id)
        if ref and ref.module_type == "homestay" else None
    )
    if booking is None or ref.user_id != booking.user_id or tx.user_id != booking.user_id:
        raise HTTPException(409, "Refund has no matching Homestay booking")
    lock_booking_resource(db, f"homestay_booking_{booking.homestay_id}")
    for record in (refund, tx, ref, booking):
        db.refresh(record)
    if payload.status not in ("approved", "rejected", "completed"):
        raise HTTPException(400, "Invalid refund decision")
    if refund.refund_status not in ("pending", "approved"):
        raise HTTPException(409, "Refund already has a final decision")
    if payload.status == "approved" and db.get_bind().dialect.name == "mssql":
        raise HTTPException(409, "Separate approval is unavailable for this refund ledger")
    if tx.payment_status not in CAPTURED or refund.currency != tx.currency:
        raise HTTPException(409, "Refund does not match a captured payment")
    if payload.status == "completed":
        evidence = payload.notes.strip() if payload.notes else ""
        if not re.fullmatch(
            r"(?:bank(?: transfer)?|gateway|payhere) "
            r"(?:ref(?:erence)?|transaction(?: id)?):\s*[a-z0-9][a-z0-9_/-]{4,100}",
            evidence, flags=re.IGNORECASE,
        ):
            raise HTTPException(400, "Provide externally verified refund execution evidence")
        lock_booking_resource(db, f"refund_external_{evidence.casefold()}")
        if db.scalar(select(PaymentRefund.id).where(
            PaymentRefund.id != refund.id,
            PaymentRefund.refund_status.in_(EXECUTED_REFUNDS),
            func.lower(PaymentRefund.refund_reason).endswith(
                f" | execution: {evidence.casefold()}", autoescape=True,
            ),
        ).limit(1)) is not None:
            raise HTTPException(409, "Refund execution evidence has already been used")
        refund.refund_reason = f"{refund.refund_reason[:480]} | Execution: {evidence}"
        refund.refund_status = executed_refund_status(db)
    else:
        refund.refund_status = payload.status
    refund.processed_by = admin.id
    refund.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.flush()

    executed, awaiting = db.execute(
        select(
            func.coalesce(func.sum(case(
                (PaymentRefund.refund_status.in_(EXECUTED_REFUNDS), PaymentRefund.amount),
                else_=0,
            )), 0),
            func.count(case((PaymentRefund.refund_status.in_(("pending", "approved")), 1))),
        ).where(PaymentRefund.payment_transaction_id == tx.id)
    ).one()
    if executed > tx.amount:
        db.rollback()
        raise HTTPException(409, "Refund exceeds captured amount")
    payment_status = "refund_pending" if awaiting else "paid"
    if executed == tx.amount:
        payment_status = "refunded"
    booking.payment_status = ref.payment_status = payment_status
    db.add(AdminAuditLog(
        admin_user_id=admin.id, action=f"refund_{payload.status}",
        entity_name="payment_refunds", entity_id=refund.id,
    ))
    create_notification(
        db=db, user_id=booking.user_id, title=f"Refund Request {payload.status.title()}",
        message=f"Your refund of {refund.amount} {refund.currency} was marked {payload.status}.",
        notification_type=f"refund_{payload.status}", link_url=f"/bookings/{booking.id}",
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
        .join(Homestay, Homestay.id == Booking.homestay_id)
        .join(ProviderProfile, ProviderProfile.id == BookingReference.provider_profile_id)
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.provider_profile_id == provider_profile_id,
            PaymentTransaction.payment_status.in_(CAPTURED),
            PaymentTransaction.payment_method == "online",
            PaymentTransaction.user_id == Booking.user_id,
            BookingReference.user_id == Booking.user_id,
            PaymentTransaction.currency == "LKR",
            BookingReference.currency == "LKR",
            Booking.currency == "LKR",
            or_(
                and_(
                    Homestay.provider_profile_id == ProviderProfile.id,
                    or_(Homestay.owner_id.is_(None), Homestay.owner_id == ProviderProfile.user_id),
                ),
                and_(Homestay.provider_profile_id.is_(None), Homestay.owner_id == ProviderProfile.user_id),
            ),
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
                PaymentRefund.refund_status.in_(EXECUTED_REFUNDS),
                PaymentRefund.currency == "LKR",
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

    # Additive Tour Guide online earnings contribution (excludes direct cash bookings)
    tg = get_tour_guide_online_earnings_components(
        db,
        provider_profile_id=provider_profile_id,
        period_start=period_start,
        period_end=period_end,
    )
    total_gross += tg["total_gross"]
    completed_gross += tg["completed_gross"]
    completed_commission += tg["completed_commission"]
    completed_provider_payable += tg["completed_provider_payable"]
    pending_earnings += tg["pending_earnings"]
    verified_refunds += tg["verified_refunds"]
    commission_reversal += tg["commission_reversal"]
    provider_refund_reduction += tg["provider_refund_reduction"]
    paid_count += tg["paid_count"]

    # Additive Activity online earnings contribution (excludes direct cash bookings)
    act = get_activity_provider_earnings_components(
        db,
        provider_profile_id=provider_profile_id,
        period_start=period_start,
        period_end=period_end,
    )
    total_gross += act["total_gross"]
    completed_gross += act["completed_gross"]
    completed_commission += act["completed_commission"]
    completed_provider_payable += act["completed_provider_payable"]
    pending_earnings += act["pending_earnings"]
    verified_refunds += act["verified_refunds"]
    commission_reversal += act["commission_reversal"]
    provider_refund_reduction += act["provider_refund_reduction"]
    paid_count += act["paid_count"]

    provider_net_earnings = max(
        Decimal("0.00"), completed_provider_payable - provider_refund_reduction
    )

    # Historical and reserved settlements
    settlements = (
        db.query(ProviderSettlement)
        .filter(
            ProviderSettlement.provider_profile_id == provider_profile_id,
            ProviderSettlement.currency == "LKR",
        )
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

    if payload.settlement_status not in ("pending", "approved", "completed", "paid"):
        raise HTTPException(422, "Invalid settlement status")
    lock_booking_resource(db, f"provider_settlement_{profile.id}")

    if payload.period_end < payload.period_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="period_end must be greater than or equal to period_start",
        )

    if payload.net_payable <= Decimal("0.00") or payload.net_payable > payload.gross_amount or payload.platform_fee < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Excessive settlement net_payable exceeds gross_amount, net_payable must be positive, or invalid platform fee",
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
    if settlement_status in ("completed", "paid"):
        if not payout_ref or not re.fullmatch(
            r"(?:BANK|WIRE|PAYOUT)[-_][a-z0-9][a-z0-9_/-]{3,90}",
            payout_ref.strip(), re.IGNORECASE,
        ):
            # Preserve pending status if external verified payout integration is unavailable
            settlement_status = "pending"
            payout_ref = "Awaiting independently verified bank payout reference"
        else:
            payout_ref = payout_ref.strip().upper()
            lock_booking_resource(db, f"settlement_payout_{payout_ref}")
            if db.scalar(select(ProviderSettlement.id).where(
                ProviderSettlement.payout_reference == payout_ref,
                ProviderSettlement.paid_at.is_not(None),
            ).limit(1)) is not None:
                raise HTTPException(409, "Payout reference has already been recorded")
            paid_at = now_utc
            if db.get_bind().dialect.name == "mssql":
                settlement_status = "paid"

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
    db.flush()
    db.add(AdminAuditLog(
        admin_user_id=admin.id, action=f"settlement_{settlement_status}",
        entity_name="provider_settlements", entity_id=settlement.id,
    ))

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
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            PaymentTransaction.payment_status.in_(CAPTURED),
            BookingReference.module_type != "eco_experience",
        )
        .all()
    )
    total_gross_booking = sum((t.gross_amount or t.amount or Decimal("0.00")) for t in tx_rows)
    total_commission = sum((t.commission_amount or Decimal("0.00")) for t in tx_rows)

    refund_rows = (
        db.query(PaymentRefund)
        .filter(PaymentRefund.refund_status.in_(EXECUTED_REFUNDS))
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
            PaymentTransaction.payment_status.in_(CAPTURED),
            BookingReference.module_type == "homestay",
            Booking.status.in_(["expired", "cancelled", "rejected"]),
        )
        .all()
    )
    for row in late_booking_txs:
        flagged_tx_ids.add(row[0])

    # 3. Paid Tour Guide transactions where booking is expired, cancelled, or rejected
    late_tg_txs = (
        db.query(PaymentTransaction.id)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(TourGuideBooking, BookingReference.target_booking_id == TourGuideBooking.id)
        .filter(
            BookingReference.module_type == "tour_guide",
            PaymentTransaction.payment_status.in_(CAPTURED),
            TourGuideBooking.booking_status.in_(["expired", "cancelled", "rejected"]),
        )
        .all()
    )
    for row in late_tg_txs:
        flagged_tx_ids.add(row[0])

    if not flagged_tx_ids:
        return []

    return (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.id.in_(flagged_tx_ids))
        .order_by(PaymentTransaction.created_at.desc())
        .all()
    )
