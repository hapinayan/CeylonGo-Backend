from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.booking_transactions import CAPTURED, RESERVED_REFUNDS, lock_booking_resource
from app.core.dependencies import require_traveler
from app.core.publication import public_homestay
from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.core.listing_sync import (
    record_booking_status_change,
    record_cash_payment_transaction,
    sync_homestay_booking_reference,
)
from app.core.attribution import verify_attribution_token
from app.core.notification_service import create_notification
from app.core.promotion_pricing import (
    calculate_booking_price,
    record_campaign_booking_conversion,
)
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay, HomestayAvailabilityBlock
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.booking import (
    BookingCancelRequest,
    BookingCreate,
    BookingListResponse,
    BookingRejectRequest,
    BookingResponse,
    CashPaymentRecordRequest,
)
from app.services.booking_expiration import expire_single_booking

router = APIRouter(
    prefix="/api/v1/bookings",
    tags=["Homestay Bookings"],
)


def _verify_booking_provider_ownership(
    db: Session,
    booking: Booking,
    current_user: User,
) -> Homestay:
    """Verify that current_user has manager/provider ownership of the homestay for this booking."""
    homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay for booking not found",
        )

    if current_user.role == "admin":
        return homestay

    profile = None
    if homestay.provider_profile_id is not None or current_user.role == "provider":
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to manage this booking",
        )

    return homestay


def _acquire_homestay_lock(db: Session, homestay_id: int):
    lock_booking_resource(db, f"homestay_booking_{homestay_id}")


# TRAVELER: Get personal booking history
@router.get("/me", response_model=list[BookingResponse])
def get_my_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    bookings = (
        db.query(Booking)
        .filter(Booking.user_id == current_user.id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return bookings


# PROVIDER: Incoming booking requests and history
@router.get("/provider", response_model=BookingListResponse)
@router.get("/provider/requests", response_model=BookingListResponse)
def list_provider_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    booking_status: Annotated[str | None, Query(alias="status")] = None,
    homestay_id: Annotated[int | None, Query()] = None,
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
):
    # Determine accessible homestays for current provider
    if current_user.role == "admin":
        homestay_query = db.query(Homestay.id)
    else:
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == current_user.id)
            .first()
        )
        if profile is not None:
            homestay_query = db.query(Homestay.id).filter(
                (Homestay.provider_profile_id == profile.id)
                | (
                    (Homestay.provider_profile_id == None)
                    & (Homestay.owner_id == current_user.id)
                )
            )
        else:
            homestay_query = db.query(Homestay.id).filter(
                Homestay.owner_id == current_user.id
            )

    accessible_ids = [h[0] for h in homestay_query.all()]
    if not accessible_ids:
        return BookingListResponse(items=[], total=0, page=page, page_size=page_size)

    query = db.query(Booking).filter(Booking.homestay_id.in_(accessible_ids))

    if homestay_id is not None:
        if homestay_id not in accessible_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You cannot access bookings for this homestay",
            )
        query = query.filter(Booking.homestay_id == homestay_id)

    if booking_status:
        query = query.filter(Booking.status == booking_status.strip().lower())

    if start_date:
        query = query.filter(Booking.check_in >= start_date)

    if end_date:
        query = query.filter(Booking.check_out <= end_date)

    total = query.count()
    items = (
        query.order_by(Booking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return BookingListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# TRAVELER / PROVIDER: Get individual booking details
@router.get("/{booking_id}", response_model=BookingResponse)
def get_booking(
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

    # Check permission: traveler who booked, homestay manager, or admin
    if booking.user_id != current_user.id and current_user.role != "admin":
        _verify_booking_provider_ownership(db, booking, current_user)

    # Lazy deadline enforcement: if payment deadline has elapsed, transition to expired
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        booking.status == "awaiting_payment"
        and booking.payment_status != "paid"
        and booking.payment_expires_at is not None
        and booking.payment_expires_at <= now_utc
    ):
        if expire_single_booking(db, booking, now_utc):
            db.commit()
            db.refresh(booking)

    return booking


# TRAVELER: Create a booking request (starts as 'pending')
@router.post(
    "",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_booking(
    booking_data: BookingCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_traveler)],
):
    if booking_data.check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    _acquire_homestay_lock(db, booking_data.homestay_id)
    homestay = (
        db.query(Homestay)
        .with_hint(Homestay, "WITH (HOLDLOCK)", "mssql")
        .filter(
            Homestay.id == booking_data.homestay_id,
            public_homestay(hold=True),
        )
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    if booking_data.guests > homestay.max_guests:
        raise HTTPException(
            status_code=422,
            detail="Guest count exceeds homestay capacity",
        )

    total_price = calculate_booking_price(
        db=db,
        homestay_id=homestay.id,
        check_in=booking_data.check_in,
        check_out=booking_data.check_out,
        price_per_night=homestay.price_per_night,
    )


    # Check for active overlapping inventory reservations:
    # Confirmed bookings, in-progress bookings, and unexpired awaiting_payment bookings.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == booking_data.homestay_id,
            Booking.check_in < booking_data.check_out,
            Booking.check_out > booking_data.check_in,
            or_(
                Booking.status.in_(["confirmed", "in_progress"]),
                and_(
                    Booking.status == "awaiting_payment",
                    or_(
                        Booking.payment_expires_at.is_(None),
                        Booking.payment_expires_at > now_utc,
                    ),
                ),
            ),
        )
        .first()
    )
    if overlapping_booking:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Homestay is already booked for these dates",
        )

    # Check for overlapping active availability blackout blocks
    overlapping_block = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.homestay_id == booking_data.homestay_id,
            HomestayAvailabilityBlock.is_active == True,
            HomestayAvailabilityBlock.start_date < booking_data.check_out,
            HomestayAvailabilityBlock.end_date > booking_data.check_in,
        )
        .first()
    )
    if overlapping_block:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Homestay is unavailable during the requested dates (blackout block)",
        )

    # In Phase 2 & 7, new booking requests start in 'pending' status
    new_booking = Booking(
        homestay_id=booking_data.homestay_id,
        user_id=current_user.id,
        check_in=booking_data.check_in,
        check_out=booking_data.check_out,
        guests=booking_data.guests,
        total_price=total_price,
        status="pending",
        currency="LKR",
        payment_method=booking_data.payment_method,
        payment_status="unpaid",
        nightly_rate_snapshot=homestay.price_per_night,
        cancellation_policy_snapshot=homestay.cancellation_policy,
        free_cancellation_hours_snapshot=(
            homestay.free_cancellation_hours
            if homestay.free_cancellation_hours is not None
            else 24
        ),
    )

    try:
        db.add(new_booking)
        db.flush()
        ref = sync_homestay_booking_reference(db, new_booking, homestay)
        if ref:
            verified_cid = None
            if booking_data.attribution_token:
                verified_cid = verify_attribution_token(booking_data.attribution_token, homestay.id)

            initial_notes = "Booking requested by traveler"
            if verified_cid:
                initial_notes += f" | campaign_id:{verified_cid}"

            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=None,
                to_status="pending",
                changed_by=current_user.id,
                notes=initial_notes,
            )

        # Notify property provider
        provider_user_id = homestay.owner_id
        if not provider_user_id and homestay.provider_profile:
            provider_user_id = homestay.provider_profile.user_id
        if provider_user_id:
            create_notification(
                db=db,
                user_id=provider_user_id,
                title=f"New Booking Request: {homestay.name}",
                message=f"Traveler requested booking #{new_booking.id} ({booking_data.check_in} to {booking_data.check_out}).",
                notification_type="booking_requested",
                link_url=f"/provider/bookings/{new_booking.id}",
            )

        db.commit()
        db.refresh(new_booking)
    except Exception:
        db.rollback()
        raise

    return new_booking


def extract_attributed_campaign_id(db: Session, booking_reference_id: int) -> int | None:
    import re
    history = (
        db.query(BookingStatusHistory)
        .filter(
            BookingStatusHistory.booking_reference_id == booking_reference_id,
            BookingStatusHistory.notes.like("%campaign_id:%"),
        )
        .first()
    )
    if history and history.notes:
        m = re.search(r"campaign_id:(\d+)", history.notes)
        if m:
            return int(m.group(1))
    return None


# PROVIDER: Accept a pending booking request
@router.post(
    "/{booking_id}/accept",
    response_model=BookingResponse,
    summary="Provider accepts an incoming booking request",
)
def accept_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    homestay = _verify_booking_provider_ownership(db, booking, current_user)

    if booking.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot accept booking with current status '{booking.status}'",
        )

    # Concurrency lock during acceptance
    _acquire_homestay_lock(db, booking.homestay_id)

    # Recheck active overlapping inventory reservations (confirmed, in-progress, unexpired awaiting_payment)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == booking.homestay_id,
            Booking.id != booking.id,
            Booking.check_in < booking.check_out,
            Booking.check_out > booking.check_in,
            or_(
                Booking.status.in_(["confirmed", "in_progress"]),
                and_(
                    Booking.status == "awaiting_payment",
                    or_(
                        Booking.payment_expires_at.is_(None),
                        Booking.payment_expires_at > now_utc,
                    ),
                ),
            ),
        )
        .first()
    )
    if overlapping_booking:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dates conflict with an already active booking reservation",
        )

    # Recheck active blackout blocks
    overlapping_block = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.homestay_id == booking.homestay_id,
            HomestayAvailabilityBlock.is_active == True,
            HomestayAvailabilityBlock.start_date < booking.check_out,
            HomestayAvailabilityBlock.end_date > booking.check_in,
        )
        .first()
    )
    if overlapping_block:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dates conflict with an active availability blackout block",
        )

    old_status = booking.status
    is_online = (booking.payment_method == "online")

    booking.approved_at = now_utc
    if is_online:
        new_status = "awaiting_payment"
        booking.payment_expires_at = now_utc + timedelta(minutes=30)
        notif_title = "Booking Approved — Payment Required"
        notif_message = (
            f"Your booking request for {homestay.name} ({booking.check_in} to {booking.check_out}) "
            f"has been accepted! Please complete online payment within 30 minutes to confirm your reservation."
        )
        status_notes = "Booking approved by provider - awaiting online payment within 30m window"
    else:
        new_status = "confirmed"
        booking.payment_expires_at = None
        notif_title = "Booking Confirmed!"
        notif_message = (
            f"Your booking for {homestay.name} ({booking.check_in} to {booking.check_out}) "
            f"has been accepted. Cash payment will be collected at check-in."
        )
        status_notes = "Booking accepted by provider - pay at location confirmed"

    booking.status = new_status

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status=new_status,
                changed_by=current_user.id,
                notes=status_notes,
            )
            if new_status == "confirmed":
                cid = extract_attributed_campaign_id(db, ref.id)
                record_campaign_booking_conversion(
                    db=db,
                    homestay_id=homestay.id,
                    user_id=booking.user_id,
                    booking_reference_id=ref.id,
                    campaign_id=cid,
                )

        create_notification(
            db=db,
            user_id=booking.user_id,
            title=notif_title,
            message=notif_message,
            notification_type="booking_accepted",
            link_url=f"/bookings/{booking.id}",
        )

        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


# PROVIDER: Reject a pending booking request
@router.post(
    "/{booking_id}/reject",
    response_model=BookingResponse,
    summary="Provider rejects an incoming booking request",
)
def reject_booking(
    booking_id: int,
    payload: BookingRejectRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    homestay = _verify_booking_provider_ownership(db, booking, current_user)

    if booking.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject booking with current status '{booking.status}'",
        )

    old_status = booking.status
    booking.status = "rejected"
    if payload.reason:
        booking.cancellation_reason = payload.reason

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status="rejected",
                changed_by=current_user.id,
                notes=payload.reason or "Rejected by provider",
            )

        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Booking Request Declined",
            message=f"Your booking request for {homestay.name} was declined: {payload.reason or 'Property unavailable'}.",
            notification_type="booking_rejected",
            link_url=f"/bookings/{booking.id}",
        )

        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


# PROVIDER: Mark eligible booking as completed
@router.post(
    "/{booking_id}/complete",
    response_model=BookingResponse,
    summary="Provider marks a booking as completed after stay",
)
def complete_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    homestay = _verify_booking_provider_ownership(db, booking, current_user)

    # Concurrency lock
    _acquire_homestay_lock(db, booking.homestay_id)
    db.refresh(booking)

    # Prevent premature completion before the stay ends
    today = date.today()
    if booking.check_out > today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot mark booking as completed before the check-out date ({booking.check_out})",
        )

    if booking.status not in ("confirmed", "in_progress"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot mark booking as completed from status '{booking.status}'",
        )

    old_status = booking.status
    booking.status = "completed"

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status="completed",
                changed_by=current_user.id,
                notes="Marked completed by provider",
            )

        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Stay Completed - Leave a Review!",
            message=f"Hope you enjoyed staying at {homestay.name}! Please take a moment to share your review.",
            notification_type="booking_completed",
            link_url=f"/homestays/{homestay.id}/reviews",
        )

        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


# TRAVELER / PROVIDER: Cancel a booking
@router.post(
    "/{booking_id}/cancel",
    response_model=BookingResponse,
    summary="Traveler or Provider cancels an existing booking request or confirmation",
)
def cancel_booking(
    booking_id: int,
    payload: BookingCancelRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    # Idempotent / Duplicate cancellation prevention
    if booking.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is already cancelled",
        )

    # Validate permitted status transitions
    if booking.status not in ("pending", "awaiting_payment", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel booking with status '{booking.status}'",
        )

    # Authorization: traveler who booked, homestay manager/owner, or admin
    is_authorized = False
    if current_user.role == "admin" or booking.user_id == current_user.id:
        is_authorized = True
    else:
        try:
            require_homestay_manager(current_user, db)
            _verify_booking_provider_ownership(db, booking, current_user)
            is_authorized = True
        except HTTPException:
            is_authorized = False

    if not is_authorized:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to cancel this booking",
        )

    # Concurrency lock during cancellation
    _acquire_homestay_lock(db, booking.homestay_id)
    db.refresh(booking)

    if booking.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking is already cancelled",
        )

    if booking.status not in ("pending", "awaiting_payment", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel booking with status '{booking.status}'",
        )

    homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # For paid bookings, handle refund request according to stored cancellation snapshot
    if booking.payment_status == "paid":
        check_in_dt = datetime.combine(booking.check_in, time.min)
        hours_before_checkin = (check_in_dt - now_utc).total_seconds() / 3600.0
        free_hours = booking.free_cancellation_hours_snapshot if booking.free_cancellation_hours_snapshot is not None else 24

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
                PaymentTransaction.currency == booking.currency,
                PaymentTransaction.payment_status.in_(CAPTURED),
                PaymentTransaction.payment_method == "online",
            )
            .order_by(PaymentTransaction.id.desc())
            .first()
        )

        if tx:
            existing_refund = (
                db.query(PaymentRefund)
                .filter(
                    PaymentRefund.payment_transaction_id == tx.id,
                    PaymentRefund.refund_status.in_(RESERVED_REFUNDS),
                )
                .first()
            )
            if not existing_refund:
                if hours_before_checkin >= free_hours:
                    reason = (
                        f"Cancelled within free cancellation window "
                        f"({hours_before_checkin:.1f}h before check-in >= {free_hours}h required). "
                        f"Full refund requested."
                    )
                else:
                    reason = (
                        f"Cancelled outside free cancellation window "
                        f"({hours_before_checkin:.1f}h before check-in < {free_hours}h required). "
                        f"Policy snapshot: '{booking.cancellation_policy_snapshot}'. Pending admin review."
                    )
                if payload.cancellation_reason:
                    reason += f" Reason: {payload.cancellation_reason}"

                refund = PaymentRefund(
                    payment_transaction_id=tx.id,
                    amount=tx.amount,
                    currency=tx.currency,
                    refund_reason=reason,
                    refund_status="pending",
                )
                db.add(refund)
                booking.payment_status = "refund_pending"

    old_status = booking.status
    booking.status = "cancelled"
    booking.cancelled_by = current_user.id
    booking.cancelled_at = now_utc
    booking.cancellation_reason = payload.cancellation_reason

    try:
        if homestay:
            ref = sync_homestay_booking_reference(db, booking, homestay)
            if ref:
                record_booking_status_change(
                    db,
                    booking_reference_id=ref.id,
                    from_status=old_status,
                    to_status="cancelled",
                    changed_by=current_user.id,
                    notes=payload.cancellation_reason or f"Cancelled by user #{current_user.id}",
                )

        # Notify other party
        actor_is_traveler = (current_user.id == booking.user_id)
        if actor_is_traveler:
            provider_user_id = homestay.owner_id if homestay else None
            if not provider_user_id and homestay and homestay.provider_profile:
                provider_user_id = homestay.provider_profile.user_id
            if provider_user_id:
                create_notification(
                    db=db,
                    user_id=provider_user_id,
                    title=f"Booking Cancelled: {homestay.name if homestay else ''}",
                    message=f"Booking #{booking.id} ({booking.check_in} to {booking.check_out}) was cancelled by the traveler.",
                    notification_type="booking_cancelled",
                    link_url=f"/provider/bookings/{booking.id}",
                )
        else:
            create_notification(
                db=db,
                user_id=booking.user_id,
                title=f"Booking Cancelled: {homestay.name if homestay else ''}",
                message=f"Your booking #{booking.id} ({booking.check_in} to {booking.check_out}) was cancelled.",
                notification_type="booking_cancelled",
                link_url=f"/bookings/{booking.id}",
            )

        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


# PROVIDER: Record cash payment received at property
@router.post(
    "/{booking_id}/record-cash-payment",
    response_model=BookingResponse,
    summary="Provider records cash payment received on-site",
)
def record_cash_payment(
    booking_id: int,
    payload: CashPaymentRecordRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    _acquire_homestay_lock(db, booking.homestay_id)
    db.refresh(booking)
    homestay = _verify_booking_provider_ownership(db, booking, current_user)

    if booking.status not in ("confirmed", "in_progress", "completed"):
        raise HTTPException(400, "Booking is not eligible for cash confirmation")
    if booking.payment_method != "pay_at_location":
        raise HTTPException(400, "This booking is configured for online payment")
    if booking.payment_status not in ("unpaid", "pending"):
        raise HTTPException(409, "Payment has already been recorded or is under review")

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref is None:
            raise HTTPException(409, "Booking has no financial reference")
        recorded = db.query(func.coalesce(func.sum(PaymentTransaction.amount), 0)).filter(
            PaymentTransaction.booking_reference_id == ref.id,
            PaymentTransaction.user_id == booking.user_id,
            PaymentTransaction.currency == ref.currency,
            PaymentTransaction.payment_method == "cash",
            PaymentTransaction.payment_status.in_(CAPTURED),
        ).scalar()
        outstanding_amount = booking.total_price - recorded
        payment_amount = payload.amount if payload.amount is not None else outstanding_amount
        if outstanding_amount <= 0 or payment_amount != outstanding_amount:
            raise HTTPException(400, "Cash payment must match the positive outstanding balance")

        booking.payment_status = ref.payment_status = "paid"
        record_cash_payment_transaction(
            db, booking_reference=ref, user_id=booking.user_id,
            amount=payment_amount, notes=payload.notes,
        )
        record_booking_status_change(
            db, ref.id, booking.status, booking.status, current_user.id,
            notes="Cash payment confirmed at property",
        )
        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Cash Payment Received",
            message=f"Payment of {payment_amount} LKR was recorded for your stay at {homestay.name}.",
            notification_type="payment_received",
            link_url=f"/bookings/{booking.id}",
        )

        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking
