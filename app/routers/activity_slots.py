
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.routers.provider_activities import (
    get_owned_activity,
    require_activity_provider,
)
from app.schemas.activity_slot import (
    ActivitySlotAvailability,
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
            ActivitySlot.is_active.is_(True),
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
            Activity.approval_status == "approved",
            Activity.is_active.is_(True),
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
            ActivitySlot.is_active.is_(True),
            ActivitySlot.starts_at > now,
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
