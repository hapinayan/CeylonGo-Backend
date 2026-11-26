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
    post = MVPAdminService.moderate_community_post(db, admin, post_id, req)
    return {"message": f"Post visibility updated to {post.visibility}.", "post_id": post.id}


@router.get("/audit-logs", response_model=list[AdminAuditLogResponse])
def get_audit_logs(
    limit: int = Query(50, le=100),
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    logs = db.query(MVPAdminAuditLog).order_by(MVPAdminAuditLog.id.desc()).limit(limit).all()
    return logs
