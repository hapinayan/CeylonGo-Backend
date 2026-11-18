from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.core.listing_sync import (
    record_booking_status_change,
    record_cash_payment_transaction,
    sync_homestay_booking_reference,
)
from app.core.notification_service import create_notification
from app.core.promotion_pricing import calculate_booking_price
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay, HomestayAvailabilityBlock
from app.models.provider_profile import ProviderProfile
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
    current_user: Annotated[User, Depends(get_current_user)],
):
    if booking_data.check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == booking_data.homestay_id,
            Homestay.is_active == True,
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

    # Acquire lock for concurrency check
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
        {"resource": f"homestay_booking_{booking_data.homestay_id}"},
    ).scalar()

    if lock_result is None or lock_result < 0:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Booking service is busy. Please try again.",
        )

    # Check for overlapping confirmed bookings
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == booking_data.homestay_id,
            Booking.status == "confirmed",
            Booking.check_in < booking_data.check_out,
            Booking.check_out > booking_data.check_in,
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

    # In Phase 2, new booking requests start in 'pending' status
    new_booking = Booking(
        homestay_id=booking_data.homestay_id,
        user_id=current_user.id,
        check_in=booking_data.check_in,
        check_out=booking_data.check_out,
        guests=booking_data.guests,
        total_price=total_price,
        status="pending",
        currency="LKR",
        payment_method="pay_at_location",
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
            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=None,
                to_status="pending",
                changed_by=current_user.id,
                notes="Booking requested by traveler",
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
            detail="Booking service is busy. Please try again.",
        )

    # Recheck overlapping confirmed bookings
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == booking.homestay_id,
            Booking.status == "confirmed",
            Booking.id != booking.id,
            Booking.check_in < booking.check_out,
            Booking.check_out > booking.check_in,
        )
        .first()
    )
    if overlapping_booking:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Dates conflict with an already confirmed booking",
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
    booking.status = "confirmed"

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_booking_status_change(
                db,
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status="confirmed",
                changed_by=current_user.id,
                notes="Booking accepted by provider",
            )

        create_notification(
            db=db,
            user_id=booking.user_id,
            title="Booking Confirmed!",
            message=f"Your booking for {homestay.name} ({booking.check_in} to {booking.check_out}) has been accepted.",
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


# TRAVELER: Cancel a booking
@router.post(
    "/{booking_id}/cancel",
    response_model=BookingResponse,
    summary="Traveler cancels an existing booking request or confirmation",
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

    if booking.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only cancel your own bookings",
        )

    if booking.status not in ("pending", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel booking with status '{booking.status}'",
        )

    homestay = db.query(Homestay).filter(Homestay.id == booking.homestay_id).first()
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

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
                    notes=payload.cancellation_reason or "Cancelled by traveler",
                )

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

    homestay = _verify_booking_provider_ownership(db, booking, current_user)

    if booking.status not in ("confirmed", "in_progress", "completed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot record payment for booking with status '{booking.status}'",
        )

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash payment has already been recorded as paid for this booking",
        )

    outstanding_amount = booking.total_price - (booking.cash_received_amount or Decimal("0.00"))
    if outstanding_amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This booking has no outstanding balance to pay",
        )

    payment_amount = payload.amount if payload.amount is not None else outstanding_amount
    if payment_amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment amount must be greater than zero",
        )

    if payment_amount != outstanding_amount:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cash payment amount ({payment_amount} LKR) does not match the actual outstanding booking amount ({outstanding_amount} LKR)",
        )

    booking.cash_received_amount = (booking.cash_received_amount or Decimal("0.00")) + payment_amount
    booking.payment_status = "paid"
    booking.payment_method = "pay_at_location"

    try:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_cash_payment_transaction(
                db,
                booking_reference=ref,
                user_id=booking.user_id,
                amount=payment_amount,
                notes=payload.notes or "Cash received at property",
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
