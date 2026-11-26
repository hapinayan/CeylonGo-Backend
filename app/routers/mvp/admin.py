"""
CeylonGo Academic MVP — Admin Operations Router
Endpoints for Provider Approvals, Listing Approvals, Content Moderation, and Audit Logging.
"""

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

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
from app.schemas.provider_registration import DraftResponse
from app.services import mvp_provider_registration as registration
from app.services.mvp_provider_otp import PASSWORD_UNSET

router = APIRouter(prefix="/admin", tags=["MVP Admin Operations"])


@router.get("/providers", response_model=list[ProviderResponse])
def list_provider_applications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    return (
        db.query(MVPProviderProfile).filter(MVPProviderProfile.verification_status != "draft")
        .order_by(MVPProviderProfile.id.desc()).offset(offset).limit(limit).all()
    )


def _review_profile(db, provider_id):
    profile = db.get(MVPProviderProfile, provider_id)
    if not profile or profile.verification_status == "draft":
        raise HTTPException(404, "Submitted provider application not found")
    registration.require_draft_storage(db)
    return profile


@router.get("/providers/{provider_id}/application", response_model=DraftResponse)
def provider_application(
    provider_id: int,
    _admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    profile = _review_profile(db, provider_id)
    return registration.draft_response(profile.user.email, profile)


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


@router.post("/providers/{provider_id}/decision", response_model=ProviderResponse)
def decide_provider(
    provider_id: int,
    req: AdminProviderDecision,
    background: BackgroundTasks,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    candidate = db.get(MVPProviderProfile, provider_id)
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
    return profile


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
