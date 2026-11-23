"""
CeylonGo Academic MVP — Admin Operations Router
Endpoints for Provider Approvals, Listing Approvals, Content Moderation, and Audit Logging.
"""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.mvp_security import require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPAdminAuditLog, MVPUser
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

router = APIRouter(prefix="/admin", tags=["MVP Admin Operations"])


@router.post("/providers/{provider_id}/decision", response_model=ProviderResponse)
def decide_provider(
    provider_id: int,
    req: AdminProviderDecision,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    profile = MVPAdminService.decide_provider(db, admin, provider_id, req)
    return profile


@router.post("/listings/{listing_id}/decision", response_model=ListingResponse)
def decide_listing(
    listing_id: int,
    req: AdminListingDecision,
    admin: MVPUser = Depends(require_mvp_role("admin")),
    db: Session = Depends(get_mvp_db),
):
    listing = MVPAdminService.decide_listing(db, admin, listing_id, req)
    return listing


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
