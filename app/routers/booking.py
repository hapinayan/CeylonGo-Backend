
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user import User
from app.routers.auth import get_current_user

from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status

from app.models.booking import Booking
from app.models.homestay import Homestay
from app.schemas.booking import BookingCreate, BookingResponse
from app.core.promotion_pricing import calculate_booking_price
from sqlalchemy import text

router = APIRouter(
    prefix="/api/v1/bookings",
    tags=["Homestay Bookings"],
)



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
