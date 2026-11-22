
import math
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user import User
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.routers.auth import get_current_user
from app.schemas.rental_booking import (
    RentalBookingCreate,
    RentalBookingProviderItem,
    RentalBookingProviderListResponse,
    RentalBookingResponse,
    RentalBookingStatusUpdate,
    RentalCustomerSummary,
    RentalVehicleSummary,
)


router = APIRouter(
    prefix="/api/v1/rental-bookings",
    tags=["Rental Bookings"],
)


# ==========================================
# HELPER: GET CURRENT UTC DATETIME
# ==========================================

def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==========================================
# HELPER: NORMALIZE DATETIME TO UTC
# ==========================================

def normalize_utc(value: datetime):
    if value.tzinfo is not None:
        return value.astimezone(
            timezone.utc
        ).replace(tzinfo=None)

    # Naive datetime values are interpreted as UTC.
    return value


# ==========================================
# HELPER: ACQUIRE VEHICLE BOOKING LOCK
# ==========================================

def lock_rental_vehicle(
    db: Session,
    vehicle_id: int,
):
    # sp_getapplock is a Microsoft SQL Server-specific procedure.
    # Detect dialect: bypass on SQLite (offline tests) while preserving
    # strict execution and locking error handling on SQL Server.
    bind = db.get_bind()
    if bind.dialect.name == "sqlite":
        return

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
            "resource": f"rental_vehicle_booking_{vehicle_id}"
        },
    ).scalar()

    if lock_result is None or lock_result < 0:
        raise HTTPException(
            status_code=503,
            detail="Booking service is busy. Please try again.",
        )


# ==========================================
# GET MY RENTAL BOOKINGS
# ==========================================

@router.get(
    "/me",
    response_model=list[RentalBookingResponse],
)
def get_my_rental_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    bookings = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.user_id == current_user.id
        )
        .order_by(
            RentalBooking.created_at.desc()
        )
        .all()
    )

    return bookings


# ==========================================
# PROVIDER FLEET BOOKINGS WORKSPACE
# ==========================================

@router.get(
    "/provider",
    response_model=RentalBookingProviderListResponse,
    summary="Provider view of fleet rental bookings",
)
def get_provider_rental_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    vehicle_id: Annotated[int | None, Query()] = None,
):
    """
    Provider-scoped rental bookings list.
    Enforces that rental providers view only bookings for vehicles they own.
    """
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    query = (
        db.query(RentalBooking)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(RentalVehicle.provider_id == provider.id)
    )

    if vehicle_id is not None:
        target_vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )
        if not target_vehicle:
            raise HTTPException(
                status_code=404,
                detail="Rental vehicle not found or not owned by you",
            )
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)

    if status and status.strip():
        clean_status = status.strip().lower()
        query = query.filter(RentalBooking.status == clean_status)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    bookings = (
        query.order_by(RentalBooking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b in bookings:
        v = b.vehicle
        u = b.user

        vehicle_summary = None
        if v:
            vehicle_summary = RentalVehicleSummary(
                id=v.id,
                vehicle_name=v.vehicle_name,
                vehicle_type=v.vehicle_type,
                price_per_day=v.price_per_day,
                license_plate=v.license_plate,
            )

        customer_summary = None
        if u:
            customer_summary = RentalCustomerSummary(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
            )

        items.append(
            RentalBookingProviderItem(
                id=b.id,
                vehicle_id=b.vehicle_id,
                user_id=b.user_id,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_price=b.total_price,
                status=b.status,
                created_at=b.created_at,
                vehicle=vehicle_summary,
                customer=customer_summary,
            )
        )

    return RentalBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/provider/{booking_id}",
    response_model=RentalBookingProviderItem,
    summary="Provider view of single rental booking",
)
def get_provider_rental_booking_detail(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get single booking details with rental provider ownership verification."""
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    booking = (
        db.query(RentalBooking)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(
            RentalBooking.id == booking_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Rental booking not found or not owned by you",
        )

    v = booking.vehicle
    u = booking.user

    vehicle_summary = None
    if v:
        vehicle_summary = RentalVehicleSummary(
            id=v.id,
            vehicle_name=v.vehicle_name,
            vehicle_type=v.vehicle_type,
            price_per_day=v.price_per_day,
            license_plate=v.license_plate,
        )

    customer_summary = None
    if u:
        customer_summary = RentalCustomerSummary(
            id=u.id,
            full_name=u.full_name,
            email=u.email,
        )

    return RentalBookingProviderItem(
        id=booking.id,
        vehicle_id=booking.vehicle_id,
        user_id=booking.user_id,
        pickup_at=booking.pickup_at,
        return_at=booking.return_at,
        total_price=booking.total_price,
        status=booking.status,
        created_at=booking.created_at,
        vehicle=vehicle_summary,
        customer=customer_summary,
    )


# ==========================================
# GET RENTAL BOOKING BY ID
# ==========================================

@router.get(
    "/{booking_id}",
    response_model=RentalBookingResponse,
)
def get_rental_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.id == booking_id,
            RentalBooking.user_id == current_user.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Rental booking not found",
        )

    return booking


# ==========================================
# CREATE RENTAL BOOKING
# ==========================================

@router.post(
    "",
    response_model=RentalBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rental_booking(
    booking_data: RentalBookingCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    pickup_at = normalize_utc(booking_data.pickup_at)
    return_at = normalize_utc(booking_data.return_at)

    if pickup_at <= utc_now():
        raise HTTPException(
            status_code=400,
            detail="Pickup date must be in the future",
        )

    if return_at <= pickup_at:
        raise HTTPException(
            status_code=400,
            detail="Return date must be after pickup date",
        )

    try:
        # Lock the vehicle before checking availability
        lock_rental_vehicle(
            db,
            booking_data.vehicle_id,
        )

        vehicle = db.get(
            RentalVehicle,
            booking_data.vehicle_id,
        )

        if not vehicle:
            raise HTTPException(
                status_code=404,
                detail="Rental vehicle not found",
            )

        if vehicle.status != "available":
            raise HTTPException(
                status_code=400,
                detail="Vehicle is currently unavailable",
            )

        # Prevent overlapping pending or confirmed bookings
        pickup_date = pickup_at.date()
        return_date = return_at.date()
        overlapping_booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status.in_(
                    ["pending", "confirmed"]
                ),
                RentalBooking.start_date <= return_date,
                RentalBooking.end_date >= pickup_date,
            )
            .first()
        )

        if overlapping_booking:
            raise HTTPException(
                status_code=409,
                detail="Vehicle is already booked for these dates",
            )

        # Calculate rental duration in days
        duration_seconds = (
            return_at - pickup_at
        ).total_seconds()

        rental_days = (
            Decimal(str(duration_seconds))
            / Decimal("86400")
        ).to_integral_value(
            rounding=ROUND_CEILING
        )

        # Calculate total price on the server
        total_price = (
            vehicle.price_per_day * rental_days
        ).quantize(
            Decimal("0.01")
        )

        new_booking = RentalBooking(
            vehicle_id=vehicle.id,
            user_id=current_user.id,
            start_date=pickup_date,
            end_date=return_date,
            total_price=total_price,
            status="pending",
        )

        db.add(new_booking)
        db.commit()
        db.refresh(new_booking)

        return new_booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# CANCEL RENTAL BOOKING
# BOOKING OWNER ONLY
# ==========================================

@router.patch(
    "/{booking_id}/cancel",
    response_model=RentalBookingResponse,
)
def cancel_rental_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    try:
        # Read vehicle ID for locking
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.user_id == current_user.id,
            )
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        # Use the same lock as booking creation
        lock_rental_vehicle(
            db,
            booking_vehicle_id,
        )

        # Re-read booking after acquiring lock
        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.user_id == current_user.id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        if booking.status not in ("pending", "confirmed"):
            raise HTTPException(
                status_code=400,
                detail="This booking cannot be cancelled",
            )

        if booking.pickup_at <= utc_now():
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel after pickup time",
            )

        booking.status = "cancelled"

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# PROVIDER UPDATE RENTAL BOOKING STATUS
# ==========================================

@router.patch(
    "/{booking_id}/status",
    response_model=RentalBookingResponse,
)
def update_rental_booking_status(
    booking_id: int,
    status_data: RentalBookingStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    # Providers can only confirm or complete bookings
    if status_data.status not in ("confirmed", "completed"):
        raise HTTPException(
            status_code=422,
            detail="Provider can only confirm or complete bookings",
        )

    # Verify approved rental provider
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    try:
        # Get vehicle ID for locking
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(RentalBooking.id == booking_id)
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        # Lock vehicle booking operations
        lock_rental_vehicle(
            db,
            booking_vehicle_id,
        )

        # Re-read booking after lock
        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        # Verify vehicle ownership
        vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == booking.vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        # Allowed booking status transitions
        allowed_transitions = {
            "pending": "confirmed",
            "confirmed": "completed",
        }

        expected_status = allowed_transitions.get(
            booking.status
        )

        if status_data.status != expected_status:
            raise HTTPException(
                status_code=400,
                detail="Invalid booking status transition",
            )

        # Booking can only be completed after return time
        if (
            status_data.status == "completed"
            and booking.return_at > utc_now()
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot complete booking before return time",
            )

        booking.status = status_data.status

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# CONFIRM VEHICLE HANDOVER
# ==========================================

@router.post(
    "/{booking_id}/handover",
    response_model=RentalBookingResponse,
    summary="Confirm vehicle handover to customer",
)
def confirm_rental_handover(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(RentalBooking.id == booking_id)
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(db, booking_vehicle_id)

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == booking.vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        if booking.status == "in_progress":
            raise HTTPException(
                status_code=400,
                detail="Vehicle has already been handed over",
            )

        if booking.status != "confirmed":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot handover booking with status '{booking.status}'. Booking must be 'confirmed'",
            )

        today = date.today()
        if booking.start_date and today < booking.start_date:
            raise HTTPException(
                status_code=400,
                detail="Cannot handover vehicle before booking start date",
            )

        booking.status = "in_progress"

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# CONFIRM VEHICLE RETURN
# ==========================================

@router.post(
    "/{booking_id}/return",
    response_model=RentalBookingResponse,
    summary="Confirm vehicle return from customer",
)
def confirm_rental_return(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(RentalBooking.id == booking_id)
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(db, booking_vehicle_id)

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == booking.vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        if booking.status == "completed":
            raise HTTPException(
                status_code=400,
                detail="Vehicle has already been returned",
            )

        if booking.status != "in_progress":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot return booking with status '{booking.status}'. Booking must be 'in_progress'",
            )

        booking.status = "completed"

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise
