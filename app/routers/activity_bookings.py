
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.publication import public_activity, public_eco
from app.core.dependencies import get_current_user
from app.database.connection import get_db

from app.models.activity import Activity
from app.models.eco_experience import EcoExperience
from app.models.activity_booking import ActivityBooking, ActivityBookingParticipant
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.user import User

from app.schemas.activity_booking import (
    ActivityBookingCreate,
    ActivityBookingResponse,
    ActivityBookingCancellation,
)

from app.services.activity_capacity import (
    reserve_activity_capacity,
    release_activity_capacity,
)


from app.services.provider_reservations import record_native_event

router = APIRouter(
    prefix="/api/v1/activity-bookings",
    tags=["Activity Bookings"],
)


# ==========================================================
# CREATE ACTIVITY BOOKING
# ==========================================================

@router.post(
    "",
    response_model=ActivityBookingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_activity_booking(
    payload: ActivityBookingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):

    # Only travelers can book activities.
    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can create activity bookings",
        )

    # Online payment is not enabled yet.
    if payload.payment_method != "pay_at_location":
        raise HTTPException(
            status_code=422,
            detail="Only pay-at-location is currently supported",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Find an active future slot with concurrency lock.
    slot = (
        db.query(ActivitySlot)
        .with_hint(ActivitySlot, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
        .filter(
            ActivitySlot.id == payload.activity_slot_id,
            ActivitySlot.is_active == True,
            ActivitySlot.starts_at > now,
        )
        .first()
    )

    if slot is None:
        raise HTTPException(
            status_code=404,
            detail="Active future activity slot not found",
        )

    # Only approved activities from approved providers.
    activity = (
        db.query(Activity)
        .with_hint(Activity, "WITH (HOLDLOCK)", "mssql")
        .join(
            ProviderProfile,
            Activity.provider_profile_id == ProviderProfile.id,
        )
        .filter(
            Activity.id == slot.activity_id,
            public_activity(hold=True),
            ProviderProfile.approval_status == "approved",
            ProviderProfile.provider_type == "activity",
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=404,
            detail="Approved activity not found",
        )

    if slot.eco_experience_id is not None and not (
        db.query(EcoExperience.id)
        .with_hint(EcoExperience, "WITH (HOLDLOCK)", "mssql")
        .filter(EcoExperience.id == slot.eco_experience_id, public_eco(hold=True))
        .first()
    ):
        raise HTTPException(404, detail="Approved Eco-Tourism experience not found")

    if payload.participants > slot.capacity:
        raise HTTPException(
            status_code=409,
            detail="Participants exceed slot capacity",
        )

    # Safety invariant: Adventure activities strictly require participant waivers and emergency contacts
    if activity.category == "adventure" and payload.participants_list is None:
        raise HTTPException(
            status_code=422,
            detail="Participant details, signed waivers, and emergency contacts are required for adventure activities",
        )

    # Safety invariant: Age-restricted activities strictly require participant details for age verification
    if activity.minimum_age > 0 and payload.participants_list is None:
        raise HTTPException(
            status_code=422,
            detail=f"Participant details and age verification are required for activities with a minimum age restriction ({activity.minimum_age}+)",
        )

    # Validate participant details and safety rules
    if payload.participants_list is not None:
        if len(payload.participants_list) != payload.participants:
            raise HTTPException(
                status_code=422,
                detail="Number of participants in participants_list must match participants count",
            )
        for idx, p in enumerate(payload.participants_list):
            name = (p.full_name or "").strip()
            if not name:
                raise HTTPException(
                    status_code=422,
                    detail=f"Participant at index {idx} must have a non-empty full_name",
                )
            if activity.minimum_age > 0:
                if p.age is None:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Participant {name} must provide age for age-restricted activities (minimum age: {activity.minimum_age})",
                    )
                if p.age < activity.minimum_age:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Participant {name} is below the minimum age of {activity.minimum_age}",
                    )
            if activity.category == "adventure":
                if not p.waiver_signed:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Waiver must be signed for participant {name} on adventure activities",
                    )
                if not (p.emergency_contact_name or "").strip() or not (
                    p.emergency_contact_phone or ""
                ).strip():
                    raise HTTPException(
                        status_code=422,
                        detail=f"Emergency contact name and phone are required for participant {name} on adventure activities",
                    )

    # Calculate price on the server.
    unit_price = Decimal(str(activity.price))

    if activity.pricing_type == "per_person":
        total_price = unit_price * payload.participants

    elif activity.pricing_type == "per_group":
        total_price = unit_price

    else:
        raise HTTPException(
            status_code=409,
            detail="Unsupported activity pricing type",
        )

    # Reserve capacity and insert booking
    # in the same database transaction.
    try:
        reserved = reserve_activity_capacity(
            db=db,
            slot_id=slot.id,
            participants=payload.participants,
        )

        if not reserved:
            raise HTTPException(
                status_code=409,
                detail="Not enough available places",
            )

        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=current_user.id,
            participants=payload.participants,
            total_price=total_price,
            currency=activity.currency,
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )

        db.add(booking)
        db.flush()

        if payload.participants_list is not None:
            for p in payload.participants_list:
                participant_record = ActivityBookingParticipant(
                    activity_booking_id=booking.id,
                    full_name=p.full_name.strip(),
                    age=p.age,
                    emergency_contact_name=p.emergency_contact_name,
                    emergency_contact_phone=p.emergency_contact_phone,
                    special_requirements=p.special_requirements,
                    waiver_signed=p.waiver_signed,
                )
                db.add(participant_record)
            db.flush()

        provider = db.get(ProviderProfile, activity.provider_profile_id)
        record_native_event(
            db, booking, "activity", provider.id, None, "pending",
            current_user.id, provider.user_id,
        )
        db.commit()
        db.refresh(booking)

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    return booking

# ==========================================================
# GET MY ACTIVITY BOOKINGS
# ==========================================================

@router.get(
    "/me",
    response_model=list[ActivityBookingResponse],
    summary="Get my activity bookings",
)
def get_my_activity_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can view their activity bookings",
        )

    bookings = (
        db.query(ActivityBooking)
        .filter(ActivityBooking.user_id == current_user.id)
        .order_by(ActivityBooking.created_at.desc())
        .all()
    )

    return bookings


# ==========================================================
# CANCEL ACTIVITY BOOKING
# ==========================================================

@router.post(
    "/{booking_id}/cancel",
    response_model=ActivityBookingResponse,
    summary="Cancel an activity booking",
)
def cancel_activity_booking(
    booking_id: int,
    payload: ActivityBookingCancellation,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):

    if current_user.role != "traveler":
        raise HTTPException(
            status_code=403,
            detail="Only travelers can cancel their bookings",
        )

    # Travelers can only cancel their own bookings.
    booking = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.id == booking_id,
            ActivityBooking.user_id == current_user.id,
        )
        .first()
    )

    if booking is None:
        raise HTTPException(
            status_code=404,
            detail="Booking not found",
        )

    if booking.booking_status == "cancelled":
        raise HTTPException(
            status_code=409,
            detail="Booking is already cancelled",
        )

    if booking.booking_status not in ("pending", "confirmed"):
        raise HTTPException(
            status_code=409,
            detail="This booking cannot be cancelled",
        )

    # Only unpaid pay-at-location bookings
    # are eligible under the temporary policy.
    if (
        booking.payment_method != "pay_at_location"
        or booking.payment_status != "unpaid"
    ):
        raise HTTPException(
            status_code=409,
            detail="This booking requires a separate cancellation policy",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    slot = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.id == booking.activity_slot_id,
        )
        .first()
    )

    if slot is None:
        raise HTTPException(
            status_code=409,
            detail="Booking slot not found",
        )

    # PROVISIONAL RULE:
    # Cancellation at least 24 hours before start.
    # Must be approved before production use.
    if slot.starts_at <= now + timedelta(hours=24):
        raise HTTPException(
            status_code=409,
            detail="Cancellation cutoff has passed",
        )

    try:
        # Conditional update prevents a booking
        # from being cancelled twice.
        statement = (
            update(ActivityBooking)
            .where(
                ActivityBooking.id == booking_id,
                ActivityBooking.user_id == current_user.id,
                ActivityBooking.booking_status.in_(
                    ["pending", "confirmed"]
                ),
                ActivityBooking.payment_method == "pay_at_location",
                ActivityBooking.payment_status == "unpaid",
            )
            .values(
                booking_status="cancelled",
                cancellation_reason=payload.reason,
                cancelled_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )

        result = db.execute(statement)

        if result.rowcount != 1:
            raise HTTPException(
                status_code=409,
                detail="Booking already cancelled or no longer eligible",
            )

        # Release reserved places in the same transaction.
        released = release_activity_capacity(
            db=db,
            slot_id=booking.activity_slot_id,
            participants=booking.participants,
        )

        if not released:
            raise HTTPException(
                status_code=409,
                detail="Unable to release reserved capacity",
            )

        activity = db.get(Activity, slot.activity_id)
        provider = db.get(ProviderProfile, activity.provider_profile_id)
        record_native_event(
            db, booking, "activity", provider.id, booking.booking_status,
            "cancelled", current_user.id, provider.user_id,
        )
        db.commit()
        db.expire_all()

        updated_booking = (
            db.query(ActivityBooking)
            .filter(ActivityBooking.id == booking_id)
            .first()
        )

        return updated_booking

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise
