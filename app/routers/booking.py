
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.core.promotion_pricing import calculate_booking_price
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.booking import (
    BookingCreate,
    BookingResponse,
    HomestayBookingCancellationRequest,
    HomestayBookingProviderItem,
    HomestayBookingProviderListResponse,
    HomestayCancellationResponse,
    HomestayCustomerSummary,
)

router = APIRouter(
    prefix="/api/v1/bookings",
    tags=["Homestay Bookings"],
)


def _acquire_homestay_lock(db: Session, homestay_id: int):
    """Best-effort transactional lock on SQL Server; safely ignores SQLite in offline tests."""
    try:
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
            {"resource": f"homestay_booking_{homestay_id}"},
        ).scalar()
        if lock_result is not None and lock_result < 0:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Booking service is busy. Please try again.",
            )
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        # Under SQLite offline testing, sp_getapplock does not exist; continue safely


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


@router.get("/provider", response_model=HomestayBookingProviderListResponse)
def get_provider_homestay_bookings(
    manager: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    homestay_id: Annotated[int | None, Query()] = None,
):
    """
    Provider-scoped homestay reservations workspace.
    Enforces that providers view only bookings for homestays they own.
    """
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == manager.id)
        .first()
    )

    query = (
        db.query(Booking)
        .join(Homestay, Booking.homestay_id == Homestay.id)
    )

    # Ownership enforcement
    if manager.role != "admin":
        if profile is not None:
            query = query.filter(
                or_(
                    Homestay.provider_profile_id == profile.id,
                    Homestay.owner_id == manager.id,
                )
            )
        else:
            query = query.filter(Homestay.owner_id == manager.id)

    # Optional homestay filter with ownership check
    if homestay_id is not None:
        target_homestay = db.query(Homestay).filter(Homestay.id == homestay_id).first()
        if not target_homestay:
            raise HTTPException(status_code=404, detail="Homestay not found")
        if not check_homestay_ownership(target_homestay, manager, profile):
            raise HTTPException(
                status_code=403,
                detail="You do not own this homestay",
            )
        query = query.filter(Booking.homestay_id == homestay_id)

    # Optional status filter
    if status and status.strip():
        clean_status = status.strip().lower()
        query = query.filter(Booking.status == clean_status)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    bookings = (
        query.order_by(Booking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b in bookings:
        customer_summary = None
        if b.user:
            customer_summary = HomestayCustomerSummary(
                id=b.user.id,
                full_name=b.user.full_name,
                email=b.user.email,
            )
        items.append(
            HomestayBookingProviderItem(
                id=b.id,
                homestay_id=b.homestay_id,
                homestay_name=b.homestay.name if b.homestay else "Homestay",
                user_id=b.user_id,
                check_in=b.check_in,
                check_out=b.check_out,
                guests=b.guests,
                total_price=b.total_price,
                currency=getattr(b, "currency", "LKR"),
                status=b.status,
                payment_method=getattr(b, "payment_method", "pay_at_location"),
                payment_status=getattr(b, "payment_status", "unpaid"),
                cancellation_reason=b.cancellation_reason,
                cancelled_by=b.cancelled_by,
                cancelled_at=b.cancelled_at,
                created_at=b.created_at,
                customer=customer_summary,
            )
        )

    return HomestayBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/provider/{booking_id}", response_model=HomestayBookingProviderItem)
def get_provider_homestay_booking_detail(
    booking_id: int,
    manager: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    """Get single booking details with ownership verification."""
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == manager.id)
        .first()
    )

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not check_homestay_ownership(booking.homestay, manager, profile):
        raise HTTPException(
            status_code=403,
            detail="You do not own the homestay for this booking",
        )

    customer_summary = None
    if booking.user:
        customer_summary = HomestayCustomerSummary(
            id=booking.user.id,
            full_name=booking.user.full_name,
            email=booking.user.email,
        )

    return HomestayBookingProviderItem(
        id=booking.id,
        homestay_id=booking.homestay_id,
        homestay_name=booking.homestay.name if booking.homestay else "Homestay",
        user_id=booking.user_id,
        check_in=booking.check_in,
        check_out=booking.check_out,
        guests=booking.guests,
        total_price=booking.total_price,
        currency=getattr(booking, "currency", "LKR"),
        status=booking.status,
        payment_method=getattr(booking, "payment_method", "pay_at_location"),
        payment_status=getattr(booking, "payment_status", "unpaid"),
        cancellation_reason=booking.cancellation_reason,
        cancelled_by=booking.cancelled_by,
        cancelled_at=booking.cancelled_at,
        created_at=booking.created_at,
        customer=customer_summary,
    )


@router.post("/{booking_id}/cancel", response_model=HomestayCancellationResponse)
def cancel_homestay_booking(
    booking_id: int,
    payload: HomestayBookingCancellationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Cancel a confirmed or pending homestay reservation.
    Authorized for:
    - The traveler who booked
    - The homestay host/manager who owns the listing
    - System administrators
    """
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Determine caller authorization
    is_traveler = booking.user_id == current_user.id
    is_admin = current_user.role == "admin"
    is_host = False

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )
    if booking.homestay and check_homestay_ownership(booking.homestay, current_user, profile):
        is_host = True

    if not (is_traveler or is_host or is_admin):
        raise HTTPException(
            status_code=403,
            detail="You are not authorized to cancel this booking",
        )

    if booking.status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Booking is already cancelled",
        )

    if booking.status not in ("pending", "confirmed"):
        raise HTTPException(
            status_code=400,
            detail=f"Booking in status '{booking.status}' cannot be cancelled",
        )

    # Date check for traveler: cannot cancel on or after check-in date
    if is_traveler and not is_admin:
        if booking.check_in <= date.today():
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel booking on or after check-in date",
            )

    _acquire_homestay_lock(db, booking.homestay_id)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    booking.status = "cancelled"
    booking.cancelled_by = current_user.id
    booking.cancelled_at = now
    booking.cancellation_reason = payload.reason.strip()

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return HomestayCancellationResponse(
        id=booking.id,
        homestay_id=booking.homestay_id,
        status=booking.status,
        cancelled_by=booking.cancelled_by,
        cancelled_at=booking.cancelled_at,
        cancellation_reason=booking.cancellation_reason,
        message="Booking cancelled successfully",
    )


@router.post(
    "/{booking_id}/check-in",
    response_model=BookingResponse,
    summary="Confirm guest check-in for homestay reservation",
)
def confirm_homestay_check_in(
    booking_id: int,
    manager: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == manager.id)
        .first()
    )

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not check_homestay_ownership(booking.homestay, manager, profile):
        raise HTTPException(
            status_code=403,
            detail="You do not own the homestay for this booking",
        )

    if booking.status == "in_progress":
        raise HTTPException(
            status_code=409,
            detail="Guest has already checked in",
        )

    if booking.status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot check in a booking with status '{booking.status}'",
        )

    today = date.today()
    if today < booking.check_in:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot check in before the scheduled check-in date ({booking.check_in})",
        )

    booking.status = "in_progress"
    booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


@router.post(
    "/{booking_id}/check-out",
    response_model=BookingResponse,
    summary="Confirm guest check-out for homestay reservation",
)
def confirm_homestay_check_out(
    booking_id: int,
    manager: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == manager.id)
        .first()
    )

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not check_homestay_ownership(booking.homestay, manager, profile):
        raise HTTPException(
            status_code=403,
            detail="You do not own the homestay for this booking",
        )

    if booking.status == "completed":
        raise HTTPException(
            status_code=409,
            detail="Guest has already checked out",
        )

    if booking.status != "in_progress":
        raise HTTPException(
            status_code=400,
            detail=f"Only in-progress bookings can be checked out (current status: '{booking.status}')",
        )

    booking.status = "completed"
    booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


@router.post(
    "/{booking_id}/no-show",
    response_model=BookingResponse,
    summary="Mark homestay reservation as guest no-show",
)
def mark_homestay_no_show(
    booking_id: int,
    manager: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == manager.id)
        .first()
    )

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if not check_homestay_ownership(booking.homestay, manager, profile):
        raise HTTPException(
            status_code=403,
            detail="You do not own the homestay for this booking",
        )

    if booking.status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail=f"Only confirmed bookings can be marked as no-show (current status: '{booking.status}')",
        )

    today = date.today()
    if today <= booking.check_in:
        raise HTTPException(
            status_code=400,
            detail="Cannot mark no-show before the check-in date has passed",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    booking.status = "cancelled"
    booking.cancellation_reason = "Guest marked as no-show by host"
    booking.cancelled_by = manager.id
    booking.cancelled_at = now
    booking.updated_at = now

    try:
        db.commit()
        db.refresh(booking)
    except Exception:
        db.rollback()
        raise

    return booking


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
    # Validate check-in date
    if booking_data.check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    # Find the selected homestay
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

    # Validate guest capacity
    if booking_data.guests > homestay.max_guests:
        raise HTTPException(
            status_code=422,
            detail="Guest count exceeds homestay capacity",
        )

    # Calculate the price on the server

    total_price = calculate_booking_price(
        db=db,
        homestay_id=homestay.id,
        check_in=booking_data.check_in,
        check_out=booking_data.check_out,
        price_per_night=homestay.price_per_night,
    )


    # Acquire an exclusive lock for this homestay.
    # The lock is held until the transaction ends.
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
        {
            "resource": f"homestay_booking_{booking_data.homestay_id}",
        },
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



    # Create booking after acquiring the lock
    # and checking for overlapping bookings.
    new_booking = Booking(
        homestay_id=booking_data.homestay_id,
        user_id=current_user.id,
        check_in=booking_data.check_in,
        check_out=booking_data.check_out,
        guests=booking_data.guests,
        total_price=total_price,
        status="confirmed",
    )

    try:
        db.add(new_booking)
        db.commit()
        db.refresh(new_booking)
    except Exception:
        db.rollback()
        raise

    return new_booking
