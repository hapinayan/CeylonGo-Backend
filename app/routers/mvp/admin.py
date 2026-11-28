"""
CeylonGo Academic MVP — Admin Operations Router
Endpoints for Provider Approvals, Listing Approvals, Content Moderation, and Audit Logging.
"""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, undefer

from app.core.mvp_security import require_mvp_role
from app.core.email import send_provider_approval_email
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPAdminAuditLog, MVPProviderProfile, MVPUser
from app.routers.provider_registration import get_upload_directory
from app.schemas.mvp import (
    AdminAuditLogResponse,
    AdminListingDecision,
    AdminModerationRequest,
    AdminProviderDecision,
    ListingResponse,
    PostResponse,
    ProviderResponse,
)
from app.services.mvp_services import MVPAdminService
from app.services import mvp_community as community_service
from app.schemas.mvp_community import (
    AdminCommunityCommentModerationPatch,
    AdminCommunityPostModerationPatch,
    AdminCommunityReportDecision,
    CommunityPage,
    CommunityReportDetails,
)
from app.schemas.mvp_provider_account import AdminProviderApplication, AdminProviderSummary
from app.services import mvp_provider_registration as registration
from app.services import mvp_provider_account as provider_account
from app.services.mvp_provider_otp import PASSWORD_UNSET

router = APIRouter(prefix="/admin", tags=["MVP Admin Operations"])


@router.get("/providers", response_model=list[AdminProviderSummary])
def list_provider_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="Filter by status: pending, approved, rejected, suspended, all"),
    search: str | None = Query(None, description="Search by provider business name, full name, or email"),
    category: str | None = Query(None, description="Filter by provider category/type"),
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    try:
        registration.require_draft_storage(db)
        query = db.query(MVPProviderProfile).options(undefer(MVPProviderProfile.registration_details))
        if status and status != "all":
            query = query.filter(MVPProviderProfile.verification_status == status)
        else:
            query = query.filter(MVPProviderProfile.verification_status != "draft")

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.join(MVPProviderProfile.user).filter(
                (MVPProviderProfile.business_name.ilike(term))
                | (MVPUser.full_name.ilike(term))
                | (MVPUser.email.ilike(term))
            )

        profiles = query.order_by(MVPProviderProfile.id.desc()).offset(offset).limit(limit).all()
        results: list[AdminProviderSummary] = []
        for p in profiles:
            item = ProviderResponse.model_validate(p)
            if getattr(p, "user", None):
                item.email = p.user.email
                item.full_name = p.user.full_name
                item.role = p.user.role
                item.is_active = p.user.is_active
            draft_info = {}
            try:
                draft_info = registration.draft_data(p)
                item.provider_type = draft_info.get("provider_type")
            except Exception:
                pass
            if category and category != "all" and item.provider_type != category:
                continue
            summary_item = AdminProviderSummary(
                **item.model_dump(),
                profile_change_pending=(
                    draft_info.get("profile_change", {}).get("status") == "pending"
                ),
            )
            results.append(summary_item)
        return results
    except Exception as exc:
        db.rollback()
        msg = str(exc).lower()
        if any(pattern in msg for pattern in ["no such table", "no such column", "invalid column", "does not exist"]):
            return []
        raise HTTPException(status_code=500, detail="Database error retrieving provider applications")


def _review_profile(db, provider_id):
    profile = db.get(MVPProviderProfile, provider_id)
    if not profile or profile.verification_status == "draft":
        raise HTTPException(404, "Submitted provider application not found")
    registration.require_draft_storage(db)
    return profile


@router.get("/providers/{provider_id}", response_model=AdminProviderApplication)
def get_provider_details(
    provider_id: int,
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    profile = _review_profile(db, provider_id)
    return AdminProviderApplication(
        **registration.draft_response(profile.user.email, profile).model_dump(),
        profile_change=registration.draft_data(profile).get("profile_change"),
    )


@router.get("/providers/{provider_id}/application", response_model=AdminProviderApplication)
def provider_application(
    provider_id: int,
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    profile = _review_profile(db, provider_id)
    return AdminProviderApplication(
        **registration.draft_response(profile.user.email, profile).model_dump(),
        profile_change=registration.draft_data(profile).get("profile_change"),
    )


@router.get("/providers/{provider_id}/identity/{document}")
def provider_identity_image(
    provider_id: int,
    document: Literal["selfie", "nic_front", "nic_back"],
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    profile = _review_profile(db, provider_id)
    filename = registration.draft_data(profile).get("documents", {}).get(document)
    directory = Path(get_upload_directory()).resolve()
    if not filename:
        raise HTTPException(404, "Identity image not found")
    path = (directory / filename).resolve()
    if path.parent != directory or not path.is_file():
        raise HTTPException(404, "Identity image not found")
    return FileResponse(path, filename=path.name, headers={"Cache-Control": "no-store"})


@router.get("/providers/{provider_id}/documents/{document}")
def legacy_provider_document(
    provider_id: int,
    document: Literal["selfie", "nic_front", "nic_back"],
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    return provider_identity_image(provider_id=provider_id, document=document, _admin=_admin, db=db)


@router.post("/providers/{provider_id}/decision", response_model=ProviderResponse)
def decide_provider(
    provider_id: int,
    req: AdminProviderDecision,
    background: BackgroundTasks,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    if req.profile_change_request_id is not None:
        if req.verification_status not in {"approved", "rejected"}:
            raise HTTPException(422, "Profile changes can only be approved or rejected")
        return provider_account.review_change(db, admin, provider_id, req)
    candidate = db.get(MVPProviderProfile, provider_id)
    if not candidate:
        raise HTTPException(404, "Provider profile not found.")
    if candidate.verification_status == "draft":
        raise HTTPException(409, "The provider must submit the application before review.")

    # Validation: rejection requires a reason
    if req.verification_status == "rejected":
        if not req.decision_reason or len(req.decision_reason.strip()) < 5:
            raise HTTPException(422, "A rejection reason with at least 5 characters is required.")

    notify = (
        candidate is not None
        and candidate.user.hashed_password == PASSWORD_UNSET
        and candidate.verification_status != "approved"
        and req.verification_status == "approved"
    )
    if notify:
        registration.require_draft_storage(db)
        draft = registration.draft_response(candidate.user.email, candidate)
        if candidate.verification_status != "pending" or not draft.identity_completed:
            raise HTTPException(409, "Only submitted applications with identity images can be approved")
    profile = MVPAdminService.decide_provider(db, admin, provider_id, req)
    if notify:
        background.add_task(
            send_provider_approval_email, profile.user.email, profile.user.full_name,
            profile.business_name,
        )
    res = ProviderResponse.model_validate(profile)
    if getattr(profile, "user", None):
        res.email = profile.user.email
        res.full_name = profile.user.full_name
        res.role = profile.user.role
        res.is_active = profile.user.is_active
    return res


@router.post("/providers/{provider_id}/approve", response_model=ProviderResponse)
def legacy_approve_provider(
    provider_id: int,
    background: BackgroundTasks,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    return decide_provider(
        provider_id=provider_id,
        req=AdminProviderDecision(verification_status="approved"),
        background=background,
        admin=admin,
        db=db,
    )


@router.post("/providers/{provider_id}/reject", response_model=ProviderResponse)
def legacy_reject_provider(
    provider_id: int,
    payload: dict,
    background: BackgroundTasks,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    reason = payload.get("reason") or payload.get("decision_reason")
    return decide_provider(
        provider_id=provider_id,
        req=AdminProviderDecision(verification_status="rejected", decision_reason=reason),
        background=background,
        admin=admin,
        db=db,
    )


@router.post("/listings/{listing_id}/decision", response_model=ListingResponse)
def decide_listing(
    listing_id: int,
    req: AdminListingDecision,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    listing = MVPAdminService.decide_listing(db, admin, listing_id, req)
    item = ListingResponse.model_validate(listing)
    item.approval_status = req.approval_status
    return item


@router.post("/community/posts/{post_id}/moderate")
def moderate_post(
    post_id: int,
    req: AdminModerationRequest,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    post = community_service.admin_moderate_post(
        db, admin=admin, post_id=post_id, visibility=req.visibility, reason=req.reason,
    )
    return {"message": f"Post visibility updated to {post.visibility}.", "post_id": post.id, "visibility": post.visibility}


@router.patch("/community/posts/{post_id}")
def admin_patch_post_visibility(
    post_id: int,
    req: AdminCommunityPostModerationPatch,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    post = community_service.admin_moderate_post(
        db, admin=admin, post_id=post_id, visibility=req.visibility, reason=req.reason,
    )
    return {"message": f"Post visibility updated to {post.visibility}.", "post_id": post.id, "visibility": post.visibility}


@router.patch("/community/comments/{comment_id}")
def admin_moderate_comment(
    comment_id: int,
    req: AdminCommunityCommentModerationPatch,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    comment = community_service.admin_moderate_comment(db, admin=admin, comment_id=comment_id, payload=req)
    return {"message": f"Comment visibility updated to {comment.visibility}.", "comment_id": comment.id, "visibility": comment.visibility}


@router.delete("/community/comments/{comment_id}")
def admin_delete_comment(
    comment_id: int,
    reason: str = Query("Removed by moderator", min_length=3, max_length=500),
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    patch = AdminCommunityCommentModerationPatch(visibility="deleted", reason=reason)
    comment = community_service.admin_moderate_comment(db, admin=admin, comment_id=comment_id, payload=patch)
    return {"message": f"Comment visibility updated to {comment.visibility}.", "comment_id": comment.id, "visibility": comment.visibility}


@router.get("/community/reports", response_model=CommunityPage[CommunityReportDetails])
def admin_list_community_reports(
    report_status: Literal["pending", "reviewed", "resolved", "dismissed"] | None = Query(None, alias="status"),
    entity_type: Literal["post", "comment"] | None = None,
    page: int = Query(1, ge=1, le=100000),
    page_size: int = Query(20, ge=1, le=50),
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    return community_service.admin_list_reports(
        db, report_status=report_status, entity_type=entity_type, page=page, page_size=page_size,
    )


@router.patch("/community/reports/{report_id}", response_model=CommunityReportDetails)
def admin_review_community_report(
    report_id: int,
    req: AdminCommunityReportDecision,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    return community_service.admin_review_report(db, admin=admin, report_id=report_id, decision=req)


@router.get("/audit-logs", response_model=list[AdminAuditLogResponse])
def get_audit_logs(
    limit: int = Query(50, le=100),
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    logs = db.query(MVPAdminAuditLog).order_by(MVPAdminAuditLog.id.desc()).limit(limit).all()
    return logs


@router.get("/dashboard")
def get_mvp_admin_dashboard(
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    """
    Returns actual database-derived statistics across all unified 17+1 Academic MVP models.
    """
    from app.models.mvp import (
        MVPBooking, MVPCommunityPost, MVPListing, MVPProviderProfile, MVPUser,
    )
    from sqlalchemy import func

    # 1. Providers
    total_providers = db.query(func.count(MVPProviderProfile.id)).scalar() or 0
    pending_providers = db.query(func.count(MVPProviderProfile.id)).filter(
        MVPProviderProfile.verification_status == "pending"
    ).scalar() or 0

    # 2. Listings by category
    pending_homestays = db.query(func.count(MVPListing.id)).filter(
        MVPListing.category == "homestay", MVPListing.approval_status == "pending"
    ).scalar() or 0
    pending_activities = db.query(func.count(MVPListing.id)).filter(
        MVPListing.category == "adventure", MVPListing.approval_status == "pending"
    ).scalar() or 0
    pending_artisans = db.query(func.count(MVPListing.id)).filter(
        MVPListing.category == "artisan", MVPListing.approval_status == "pending"
    ).scalar() or 0
    pending_rentals = db.query(func.count(MVPListing.id)).filter(
        MVPListing.category.in_(("bike", "bicycle")), MVPListing.approval_status == "pending"
    ).scalar() or 0
    pending_guides = db.query(func.count(MVPListing.id)).filter(
        MVPListing.category == "guide", MVPListing.approval_status == "pending"
    ).scalar() or 0

    # 3. Bookings by category
    homestay_bookings = db.query(func.count(MVPBooking.id)).join(MVPListing).filter(
        MVPListing.category == "homestay"
    ).scalar() or 0
    rental_bookings = db.query(func.count(MVPBooking.id)).join(MVPListing).filter(
        MVPListing.category.in_(("bike", "bicycle"))
    ).scalar() or 0
    guide_bookings = db.query(func.count(MVPBooking.id)).join(MVPListing).filter(
        MVPListing.category == "guide"
    ).scalar() or 0

    return {
        "providers": {
            "total_profiles": total_providers,
            "pending_applications": pending_providers,
        },
        "pending_approvals": {
            "homestay_listings": pending_homestays,
            "activity_listings": pending_activities,
            "eco_experience_listings": 0,
            "artisan_listings": pending_artisans,
            "rental_vehicles": pending_rentals,
            "tour_guide_profiles": pending_guides,
        },
        "bookings": {
            "homestay": homestay_bookings,
            "rental_vehicle": rental_bookings,
            "tour_guide": guide_bookings,
        },
        "traveler_eligibility": {
            "pending_rental_checks": 0,
        },
    }
