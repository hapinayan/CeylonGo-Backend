
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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
    RentalBookingResponse,
    RentalBookingStatusUpdate,
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
        overlapping_booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status.in_(
                    ["pending", "confirmed"]
                ),
                RentalBooking.pickup_at < return_at,
                RentalBooking.return_at > pickup_at,
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
            pickup_at=pickup_at,
            return_at=return_at,
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
