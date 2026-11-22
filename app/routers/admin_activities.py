from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.core.listing_sync import sync_activity_listing_reference
from app.core.location_validation import ACTIVITY_REVIEW_STATE, write_review_snapshot
from app.core.notification_service import create_notification
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.schemas.activity import ActivityResponse
from app.schemas.activity_admin import (
    ActivityApprovalRequest,
    ActivityRejectionRequest,
    ActivitySuspensionRequest,
    ActivityUnsuspensionRequest,
    AdminActivityPlatformReportResponse,
)
from app.schemas.activity_promotion import (
    ActivityCampaignPagination,
    ActivityCampaignResponse,
)
from app.services.activity_promotions import (
    get_admin_campaign,
    list_admin_campaigns,
)
from app.services.activity_reports import generate_platform_activity_report


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
    "/reports",
    response_model=AdminActivityPlatformReportResponse,
    summary="Get platform-wide activity reports and executive metrics (C07)",
)
def get_platform_activity_reports(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    date_from: date | None = Query(
        default=None,
        description="Start date filter (YYYY-MM-DD)",
    ),
    date_to: date | None = Query(
        default=None,
        description="End date filter (YYYY-MM-DD)",
    ),
    start_date: date | None = Query(
        default=None,
        description="Alias for date_from",
    ),
    end_date: date | None = Query(
        default=None,
        description="Alias for date_to",
    ),
):
    """
    Platform-wide administrative activity reporting endpoint (C07).
    Aggregates activity inventory, booking lifecycle metrics, slot occupancy,
    canonical category distribution, regional heatmaps, multi-currency financials,
    and top activities / providers with strict admin authorization.
    """
    eff_from = date_from or start_date
    eff_to = date_to or end_date
    return generate_platform_activity_report(
        db,
        date_from=eff_from,
        date_to=eff_to,
    )


# ==============================================================================
# Admin Activity Promotions Management (C06 Foundation)
# ==============================================================================


@router.get(
    "/promotions/campaigns",
    response_model=ActivityCampaignPagination,
    summary="List activity sponsored campaigns for admin review (C06)",
)
def list_activity_promotions_campaigns(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(
        default=None,
        description="Filter by campaign status (draft, pending_approval, active, paused, completed, cancelled)",
    ),
    provider_profile_id: int | None = Query(
        default=None,
        ge=1,
        description="Filter by provider profile ID",
    ),
    search: str | None = Query(
        default=None,
        max_length=150,
        description="Search campaign name (case-insensitive substring)",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    Admin-only listing of activity sponsored campaigns across all providers (C06).
    Enforces target_module = 'activity', admin authentication, and physical schema safety.
    """
    return list_admin_campaigns(
        db,
        status_filter=status,
        provider_profile_id=provider_profile_id,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/promotions/campaigns/{campaign_id}",
    response_model=ActivityCampaignResponse,
    summary="Get activity sponsored campaign details for admin review (C06)",
)
def get_activity_promotions_campaign(
    campaign_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only retrieval of a single activity sponsored campaign (C06).
    Returns 404 for nonexistent or non-activity campaigns.
    """
    return get_admin_campaign(db, campaign_id=campaign_id)


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


# ==============================================================================
# Admin Activity Moderation (C03)
# ==============================================================================


@router.post(
    "/{activity_id}/suspend",
    response_model=ActivityResponse,
    summary="Suspend an approved activity (C03)",
)
def suspend_activity(
    activity_id: int,
    payload: ActivitySuspensionRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only activity suspension.

    Rules:
    1. Only approved activities can be suspended.
    2. Repeated suspension on already suspended activity returns 409 Conflict.
    3. Non-approved activities (draft, pending, rejected) cannot be suspended (409 Conflict).
    4. Updates approval_status to 'suspended', is_active to False.
    5. Synchronizes central dbo.listing_references (is_active = False).
    6. Dispatches in-app notification to provider.
    7. Creates audit log entry in dbo.admin_audit_logs.
    8. Preserves existing bookings, slots, and capacity.
    """
    activity = get_activity_for_review(activity_id, db)

    if activity.approval_status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Activity is already suspended",
        )

    if activity.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only approved activities can be suspended",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        write_review_snapshot(db, activity, ACTIVITY_REVIEW_STATE, {
            "approval_status": "suspended",
            "is_active": False,
            "reviewed_by": admin.id,
            "reviewed_at": now,
            "rejection_reason": payload.reason,
        })
        sync_activity_listing_reference(db, activity)

        # Notify provider
        provider_profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.id == activity.provider_profile_id)
            .first()
        )
        if provider_profile and provider_profile.user_id:
            create_notification(
                db,
                user_id=provider_profile.user_id,
                title="Activity Suspended",
                message=(
                    f"Your activity '{activity.title}' has been suspended "
                    f"by platform administration. Reason: {payload.reason}"
                ),
                notification_type="activity_suspended",
                link_url=f"/provider/activities/{activity.id}",
            )

        # Admin audit log
        audit_entry = AdminAuditLog(
            admin_user_id=admin.id,
            action="activity_suspend",
            entity_name="activity",
            entity_id=activity.id,
            details=f"Activity '{activity.title}' suspended. Reason: {payload.reason}",
        )
        db.add(audit_entry)

        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity


@router.post(
    "/{activity_id}/unsuspend",
    response_model=ActivityResponse,
    summary="Unsuspend / reinstate a suspended activity (C03)",
)
def unsuspend_activity(
    activity_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    payload: ActivityUnsuspensionRequest = Body(default_factory=ActivityUnsuspensionRequest),
):
    """
    Admin-only activity unsuspension / reinstatement.

    Rules:
    1. Only currently suspended activities can be unsuspended.
    2. Non-suspended activities (draft, pending, rejected, approved) return 409 Conflict.
    3. Provider profile must still be approved.
    4. Updates approval_status to 'approved', is_active to True, rejection_reason to None.
    5. Synchronizes central dbo.listing_references (is_active = True).
    6. Dispatches in-app notification to provider.
    7. Creates audit log entry in dbo.admin_audit_logs.
    """
    activity = get_activity_for_review(activity_id, db)

    if activity.approval_status != "suspended":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only suspended activities can be unsuspended",
        )

    provider_profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.id == activity.provider_profile_id)
        .first()
    )
    if not provider_profile or provider_profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot unsuspend activity because provider profile is not approved",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    try:
        write_review_snapshot(db, activity, ACTIVITY_REVIEW_STATE, {
            "approval_status": "approved",
            "is_active": True,
            "reviewed_by": admin.id,
            "reviewed_at": now,
            "rejection_reason": None,
        })
        sync_activity_listing_reference(db, activity)

        # Notify provider
        if provider_profile.user_id:
            msg = f"Your activity '{activity.title}' has been reinstated and is now active."
            if payload and payload.reason:
                msg += f" Note: {payload.reason}"
            create_notification(
                db,
                user_id=provider_profile.user_id,
                title="Activity Reinstated",
                message=msg,
                notification_type="activity_unsuspended",
                link_url=f"/provider/activities/{activity.id}",
            )

        # Admin audit log
        audit_entry = AdminAuditLog(
            admin_user_id=admin.id,
            action="activity_unsuspend",
            entity_name="activity",
            entity_id=activity.id,
            details=(
                f"Activity '{activity.title}' unsuspended/reinstated."
                + (f" Note: {payload.reason}" if payload and payload.reason else "")
            ),
        )
        db.add(audit_entry)

        db.commit()
        db.refresh(activity)
    except Exception:
        db.rollback()
        raise

    return activity
