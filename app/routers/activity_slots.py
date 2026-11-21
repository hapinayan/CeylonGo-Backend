
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.publication import public_activity, public_eco
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.eco_experience import EcoExperience
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.routers.provider_activities import (
    get_owned_activity,
    require_activity_provider,
)
from app.schemas.activity_slot import (
    ActivitySlotAvailability,
    ActivitySlotCreate,
    ActivitySlotResponse,
    ActivitySlotUpdate,
    SessionCancellationRequest,
    SessionCancellationResponse,
)
from app.services.activity_capacity import update_slot_capacity
from app.services.provider_reservations import save_event


router = APIRouter(tags=["Activity Slots"])


@router.post(
    "/api/v1/provider/activities/{activity_id}/slots",
    response_model=ActivitySlotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_activity_slot(
    activity_id: int,
    payload: ActivitySlotCreate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    if activity.approval_status != "approved" or not activity.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Activity must be approved and active",
        )

    starts_at = payload.starts_at.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    ends_at = payload.ends_at.astimezone(
        timezone.utc
    ).replace(tzinfo=None)

    # Recheck the start time because it may have
    # passed since request validation.
    if starts_at <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Slot start time must be in the future",
        )

    if ends_at <= starts_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Slot end time must be after start time",
        )

    # Prevent overlapping active slots for the same activity.
    overlapping = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.activity_id == activity_id,
            ActivitySlot.is_active == True,
            ActivitySlot.starts_at < ends_at,
            ActivitySlot.ends_at > starts_at,
        )
        .first()
    )

    if overlapping:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This slot overlaps an existing active slot",
        )

    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=starts_at,
        ends_at=ends_at,
        capacity=payload.capacity,
        reserved_count=0,
        is_active=True,
    )

    db.add(slot)

    try:
        db.commit()
        db.refresh(slot)
    except Exception:
        db.rollback()
        raise

    return slot


@router.get(
    "/api/v1/provider/activities/{activity_id}/slots",
    response_model=list[ActivitySlotResponse],
)
def list_provider_activity_slots(
    activity_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )

    return (
        db.query(ActivitySlot)
        .filter(ActivitySlot.activity_id == activity_id)
        .order_by(ActivitySlot.starts_at.asc())
        .all()
    )


@router.get(
    "/api/v1/activities/{activity_id}/slots",
    response_model=list[ActivitySlotAvailability],
)
def list_public_activity_slots(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    activity = (
        db.query(Activity)
        .filter(
            Activity.id == activity_id,
            public_activity(),
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    slots = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.activity_id == activity_id,
            ActivitySlot.is_active == True,
            ActivitySlot.starts_at > now,
            or_(ActivitySlot.eco_experience_id.is_(None), db.query(EcoExperience.id).filter(
                EcoExperience.id == ActivitySlot.eco_experience_id, public_eco(),
            ).exists()),
        )
        .order_by(ActivitySlot.starts_at.asc())
        .all()
    )

    return [
        ActivitySlotAvailability(
            id=slot.id,
            activity_id=slot.activity_id,
            starts_at=slot.starts_at,
            ends_at=slot.ends_at,
            capacity=slot.capacity,
            reserved_count=slot.reserved_count,
            available_places=max(
                0,
                slot.capacity - slot.reserved_count,
            ),
            is_available=(
                slot.reserved_count < slot.capacity
            ),
        )
        for slot in slots
    ]


@router.patch(
    "/api/v1/provider/activities/{activity_id}/slots/{slot_id}",
    response_model=ActivitySlotResponse,
    summary="Update a provider's scheduled activity slot",
)
def update_activity_slot(
    activity_id: int,
    slot_id: int,
    payload: ActivitySlotUpdate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
        for_update=True,
    )

    slot = (
        db.query(ActivitySlot)
        .with_hint(ActivitySlot, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
        .filter(
            ActivitySlot.id == slot_id,
            ActivitySlot.activity_id == activity.id,
        )
        .with_for_update()
        .first()
    )

    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity slot not found",
        )

    # Validate time changes
    if payload.starts_at is not None or payload.ends_at is not None:
        new_starts = (
            payload.starts_at.astimezone(timezone.utc).replace(tzinfo=None)
            if payload.starts_at
            else slot.starts_at
        )
        new_ends = (
            payload.ends_at.astimezone(timezone.utc).replace(tzinfo=None)
            if payload.ends_at
            else slot.ends_at
        )

        is_time_changed = (new_starts != slot.starts_at) or (new_ends != slot.ends_at)
        if is_time_changed:
            if slot.reserved_count > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot reschedule a slot with existing reservations",
                )

            active_booking = (
                db.query(ActivityBooking)
                .filter(
                    ActivityBooking.activity_slot_id == slot.id,
                    ActivityBooking.booking_status.in_(["pending", "confirmed"]),
                )
                .first()
            )
            if active_booking:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot reschedule a slot with active bookings",
                )

            now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
            if new_starts <= now_utc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Slot start time must be in the future",
                )
            if new_ends <= new_starts:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Slot end time must be after start time",
                )

            # Check overlapping active slots for this activity
            overlapping = (
                db.query(ActivitySlot)
                .filter(
                    ActivitySlot.activity_id == activity.id,
                    ActivitySlot.id != slot.id,
                    ActivitySlot.is_active == True,
                    ActivitySlot.starts_at < new_ends,
                    ActivitySlot.ends_at > new_starts,
                )
                .first()
            )
            if overlapping:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This slot overlaps an existing active slot",
                )

            slot.starts_at = new_starts
            slot.ends_at = new_ends

    # Validate capacity change
    if payload.capacity is not None:
        if payload.capacity < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Capacity must be at least 1",
            )
        success = update_slot_capacity(
            db, slot_id=slot.id, new_capacity=payload.capacity
        )
        if not success:
            db.refresh(slot)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot reduce capacity to {payload.capacity}; {slot.reserved_count} spots already reserved",
            )
        slot.capacity = payload.capacity

    # Availability toggle
    if payload.is_active is not None:
        slot.is_active = payload.is_active

    try:
        db.commit()
        db.refresh(slot)
    except Exception:
        db.rollback()
        raise

    return slot


@router.delete(
    "/api/v1/provider/activities/{activity_id}/slots/{slot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unbooked activity slot",
)
def delete_activity_slot(
    activity_id: int,
    slot_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
        for_update=True,
    )

    slot = (
        db.query(ActivitySlot)
        .with_hint(ActivitySlot, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
        .filter(
            ActivitySlot.id == slot_id,
            ActivitySlot.activity_id == activity.id,
        )
        .with_for_update()
        .first()
    )

    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity slot not found",
        )

    # Protect existing reservations
    if slot.reserved_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete slot with existing reservations",
        )

    # Protect booking history and foreign key integrity
    any_booking = (
        db.query(ActivityBooking.id)
        .filter(ActivityBooking.activity_slot_id == slot.id)
        .first()
    )
    if any_booking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete slot with existing bookings. Deactivate the slot instead.",
        )

    try:
        db.delete(slot)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/v1/provider/activities/{activity_id}/slots/{slot_id}/cancel-session",
    response_model=SessionCancellationResponse,
    summary="Cancel an activity session due to adverse weather or safety conditions",
)
def cancel_activity_session(
    activity_id: int,
    slot_id: int,
    payload: SessionCancellationRequest,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_owned_activity(
        activity_id,
        provider_profile,
        db,
        for_update=True,
    )

    slot = (
        db.query(ActivitySlot)
        .with_hint(ActivitySlot, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
        .filter(
            ActivitySlot.id == slot_id,
            ActivitySlot.activity_id == activity.id,
        )
        .with_for_update()
        .first()
    )

    if not slot:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity slot not found",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Check if slot already ended
    if slot.ends_at <= now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot cancel an activity session that has already concluded",
        )

    # If slot is already inactive and has no active bookings, return idempotent response
    active_bookings = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.activity_slot_id == slot.id,
            ActivityBooking.booking_status.in_(["pending", "confirmed"]),
        )
        .all()
    )

    if not slot.is_active and len(active_bookings) == 0:
        return SessionCancellationResponse(
            slot_id=slot.id,
            activity_id=activity.id,
            cancelled_bookings_count=0,
            released_capacity=0,
            is_active=False,
            reason=payload.reason,
        )

    released_capacity = slot.reserved_count

    try:
        # Mark slot inactive and reset reserved capacity
        slot.is_active = False
        slot.reserved_count = 0

        # Cancel all active bookings and notify travelers
        cancellation_msg = f"Session cancelled by organizer: {payload.reason}"
        for b in active_bookings:
            old_status = b.booking_status
            b.booking_status = "cancelled"
            b.cancellation_reason = cancellation_msg
            b.cancelled_at = now
            b.updated_at = now

            # Sync shared booking reference if present
            ref = db.scalar(
                select(BookingReference)
                .where(
                    BookingReference.module_type == "activity",
                    BookingReference.target_booking_id == b.id,
                    BookingReference.user_id == b.user_id,
                    BookingReference.provider_profile_id == provider_profile.id,
                )
                .order_by(BookingReference.id)
                .limit(1)
            )
            if ref is not None:
                ref.booking_status = "cancelled"
                save_event(
                    db=db,
                    ref=ref,
                    old=old_status,
                    new="cancelled",
                    actor=provider_profile.user_id,
                    recipient=b.user_id,
                    action="session_cancelled",
                )

        db.commit()

        return SessionCancellationResponse(
            slot_id=slot.id,
            activity_id=activity.id,
            cancelled_bookings_count=len(active_bookings),
            released_capacity=released_capacity,
            is_active=False,
            reason=payload.reason,
        )

    except Exception:
        db.rollback()
        raise

