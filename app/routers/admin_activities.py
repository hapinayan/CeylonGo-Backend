
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.listing_sync import sync_activity_listing_reference
from app.core.location_validation import ACTIVITY_REVIEW_STATE, write_review_snapshot
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.user import User
from app.schemas.activity import ActivityResponse
from app.schemas.activity_admin import (
    ActivityApprovalRequest,
    ActivityRejectionRequest,
)


router = APIRouter(
    prefix="/api/v1/admin/activities",
    tags=["Admin Activities"],
)


def get_activity_for_review(
    activity_id: int,
    db: Session,
) -> Activity:
    activity = (
        db.query(Activity)
        .filter(Activity.id == activity_id)
        .first()
    )

    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    return activity


@router.get(
    "",
    response_model=list[ActivityResponse],
    summary="List activities awaiting admin review",
)
def list_pending_activities(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
):
    return (
        db.query(Activity)
        .filter(Activity.approval_status == "pending")
        .order_by(Activity.created_at.asc())
        .limit(limit)
        .all()
    )


@router.get(
    "/{activity_id}",
    response_model=ActivityResponse,
    summary="View activity for admin review",
)
def get_activity_review_details(
    activity_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return get_activity_for_review(activity_id, db)


@router.post(
    "/{activity_id}/approve",
    response_model=ActivityResponse,
    summary="Approve a pending activity",
)
def approve_activity(
    activity_id: int,
    payload: ActivityApprovalRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_activity_for_review(activity_id, db)

    if activity.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending activities can be approved",
        )

    try:
        write_review_snapshot(db, activity, ACTIVITY_REVIEW_STATE, {
            "approval_status": "approved",
            "is_active": True,
            "reviewed_by": admin.id,
            "reviewed_at": datetime.now(timezone.utc).replace(tzinfo=None),
            "rejection_reason": None,
        })
        sync_activity_listing_reference(db, activity)
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.post(
    "/{activity_id}/reject",
    response_model=ActivityResponse,
    summary="Reject a pending activity",
)
def reject_activity(
    activity_id: int,
    payload: ActivityRejectionRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    activity = get_activity_for_review(activity_id, db)

    if activity.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending activities can be rejected",
        )

    activity.approval_status = "rejected"
    activity.is_active = False
    activity.reviewed_by = admin.id
    activity.reviewed_at = datetime.now(timezone.utc).replace(
        tzinfo=None
    )
    activity.rejection_reason = payload.reason
    sync_activity_listing_reference(db, activity)

    try:
        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity
