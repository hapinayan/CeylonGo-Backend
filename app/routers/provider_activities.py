
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.provider_profile import ProviderProfile
from app.schemas.activity import (
    ActivityCreate,
    ActivityResponse,
    ActivityUpdate,
)


router = APIRouter(
    prefix="/api/v1/provider/activities",
    tags=["Provider Activities"],
)


def require_activity_provider(
    profile: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
) -> ProviderProfile:
    """Allow only approved activity providers."""
    if profile.provider_type != "activity":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Activity provider access required",
        )

    return profile


def get_owned_activity(
    activity_id: int,
    provider_profile: ProviderProfile,
    db: Session,
) -> Activity:
    """Find an activity owned by the current provider."""
    activity = (
        db.query(Activity)
        .filter(
            Activity.id == activity_id,
            Activity.provider_profile_id == provider_profile.id,
        )
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    return activity


@router.post(
    "",
    response_model=ActivityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a draft activity",
)
def create_activity(
    payload: ActivityCreate,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    activity = Activity(
        **payload.model_dump(),
        provider_profile_id=provider_profile.id,
        approval_status="draft",
        is_active=False,
    )

    db.add(activity)

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.get(
    "",
    response_model=list[ActivityResponse],
    summary="List my activities",
)
def list_my_activities(
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    return (
        db.query(Activity)
        .filter(
            Activity.provider_profile_id == provider_profile.id
        )
        .order_by(Activity.created_at.desc())
        .all()
    )


@router.get(
    "/{activity_id}",
    response_model=ActivityResponse,
    summary="Get my activity details",
)
def get_my_activity(
    activity_id: int,
    provider_profile: Annotated[
        ProviderProfile,
        Depends(require_activity_provider),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    return get_owned_activity(
        activity_id,
        provider_profile,
        db,
    )


@router.patch(
    "/{activity_id}",
    response_model=ActivityResponse,
    summary="Update a draft or rejected activity",
)
def update_activity(
    activity_id: int,
    payload: ActivityUpdate,
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

    if activity.approval_status not in (
        "draft",
        "rejected",
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Only draft or rejected activities "
                "can be edited"
            ),
        )

    updates = payload.model_dump(exclude_unset=True)

    for field, value in updates.items():
        setattr(activity, field, value)

    if activity.approval_status == "rejected":
        activity.approval_status = "draft"
        activity.rejection_reason = None
        activity.reviewed_by = None
        activity.reviewed_at = None
        activity.is_active = False

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.post(
    "/{activity_id}/submit",
    response_model=ActivityResponse,
    summary="Submit an activity for admin review",
)
def submit_activity(
    activity_id: int,
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

    if activity.approval_status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft activities can be submitted",
        )

    activity.approval_status = "pending"
    activity.is_active = False

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity
