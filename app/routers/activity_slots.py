
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.publication import public_activity, public_eco
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.eco_experience import EcoExperience
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.routers.provider_activities import (
    get_owned_activity,
    require_activity_provider,
)
from app.schemas.activity_booking import (
    ActivitySlotAttendeeItem,
    ActivitySlotAttendeeListResponse,
)
from app.schemas.activity_slot import (
    ActivitySlotAvailability,
    ActivitySlotBatchCreate,
    ActivitySlotBatchResponse,
    ActivitySlotCreate,
    ActivitySlotResponse,
)


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


@router.post(
    "/api/v1/provider/activities/{activity_id}/slots/batch",
    response_model=ActivitySlotBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Batch generate recurring activity slots over a date range",
)
def batch_create_activity_slots(
    activity_id: int,
    payload: ActivitySlotBatchCreate,
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

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    created_slots: list[ActivitySlot] = []

    current_date = payload.start_date
    one_day = timedelta(days=1)

    while current_date <= payload.end_date:
        if payload.days_of_week is None or current_date.weekday() in payload.days_of_week:
            slot_start = datetime.combine(current_date, payload.start_time)
            slot_end = datetime.combine(current_date, payload.end_time)

            # Skip slots whose start time has already passed
            if slot_start > now:
                # Check for overlapping active slots for this activity
                overlapping = (
                    db.query(ActivitySlot)
                    .filter(
                        ActivitySlot.activity_id == activity_id,
                        ActivitySlot.is_active.is_(True),
                        ActivitySlot.starts_at < slot_end,
                        ActivitySlot.ends_at > slot_start,
                    )
                    .first()
                )
                if not overlapping:
                    slot = ActivitySlot(
                        activity_id=activity_id,
                        starts_at=slot_start,
                        ends_at=slot_end,
                        capacity=payload.capacity,
                        reserved_count=0,
                        is_active=True,
                    )
                    db.add(slot)
                    created_slots.append(slot)

        current_date += one_day

    if created_slots:
        try:
            db.commit()
            for s in created_slots:
                db.refresh(s)
        except Exception:
            db.rollback()
            raise

    return ActivitySlotBatchResponse(
        created_count=len(created_slots),
        slots=created_slots,
        message=f"Successfully generated {len(created_slots)} recurring slot(s)",
    )


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


@router.get(
    "/api/v1/provider/activity-slots/{slot_id}/attendees",
    response_model=ActivitySlotAttendeeListResponse,
    summary="Get attendee list for an activity slot",
)
def get_activity_slot_attendees(
    slot_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    slot = db.query(ActivitySlot).filter(ActivitySlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Activity slot not found")

    activity = slot.activity
    if not activity or activity.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=403,
            detail="You do not own the activity for this slot",
        )

    bookings = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.activity_slot_id == slot_id,
            ActivityBooking.booking_status.in_(["pending", "confirmed", "completed"]),
        )
        .order_by(ActivityBooking.created_at.asc())
        .all()
    )

    attendees = []
    total_attendees = 0
    for b in bookings:
        user = b.traveler
        total_attendees += b.participants
        attendees.append(
            ActivitySlotAttendeeItem(
                booking_id=b.id,
                user_id=b.user_id,
                full_name=user.full_name if user else "Traveler",
                email=user.email if user else "",
                participants=b.participants,
                booking_status=b.booking_status,
                payment_status=b.payment_status,
                total_price=b.total_price,
                currency=b.currency,
                created_at=b.created_at,
            )
        )

    return ActivitySlotAttendeeListResponse(
        slot_id=slot.id,
        activity_id=activity.id,
        activity_title=activity.title,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        capacity=slot.capacity,
        reserved_count=slot.reserved_count,
        total_attendees=total_attendees,
        attendees=attendees,
    )
