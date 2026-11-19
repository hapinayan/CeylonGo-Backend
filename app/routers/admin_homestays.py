from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_admin
from app.core.listing_sync import sync_homestay_listing_reference
from app.core.location_validation import HOMESTAY_REVIEW_STATE, write_review_snapshot
from app.core.notification_service import create_notification
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.shared import (
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
    Review,
)
from app.models.user import User
from app.schemas.homestay import (
    HomestayResponse,
    HomestayReviewRequest,
    HomestayVerificationRequest,
)
from app.schemas.homestay_dashboard import (
    HomestayAdminBookingSummary,
    HomestayAdminCampaignSummary,
    HomestayAdminDashboardResponse,
    HomestayAdminListingSummary,
    HomestayAdminPaymentSummary,
    HomestayAdminReviewSummary,
    HomestayAdminSettlementSummary,
)

router = APIRouter(
    prefix="/api/v1/admin/homestays",
    tags=["Admin Homestays"],
    dependencies=[Depends(require_admin)],
)


def _get_homestay_or_404(homestay_id: int, db: Session) -> Homestay:
    homestay = (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay_id)
        .first()
    )
    if homestay is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found",
        )
    return homestay


@router.get(
    "/pending",
    response_model=list[HomestayResponse],
    summary="List homestays awaiting admin review",
)
def list_pending_homestays(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    limit: int = Query(default=50, ge=1, le=100),
):
    return (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.approval_status == "pending")
        .order_by(Homestay.created_at.asc())
        .limit(limit)
        .all()
    )


@router.get(
    "/dashboard",
    response_model=HomestayAdminDashboardResponse,
    summary="Get homestay administrative summary",
)
def get_admin_dashboard(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """Aggregate platform-wide homestay administrative metrics."""
    # 1. Homestays by approval status
    homestays = db.query(Homestay).all()
    total_homestays = len(homestays)
    approved_count = sum(1 for h in homestays if h.approval_status == "approved")
    pending_count = sum(1 for h in homestays if h.approval_status == "pending")
    draft_count = sum(1 for h in homestays if h.approval_status == "draft")
    rejected_count = sum(1 for h in homestays if h.approval_status == "rejected")
    suspended_count = sum(1 for h in homestays if h.approval_status == "suspended")

    listings_summary = HomestayAdminListingSummary(
        total=total_homestays,
        approved=approved_count,
        pending_approval=pending_count,
        draft=draft_count,
        rejected=rejected_count,
        suspended=suspended_count,
    )

    # 2. Bookings breakdown
    bookings = db.query(Booking).all()
    bookings_summary = HomestayAdminBookingSummary(
        total=len(bookings),
        pending=sum(1 for b in bookings if b.status == "pending"),
        confirmed=sum(1 for b in bookings if b.status == "confirmed"),
        completed=sum(1 for b in bookings if b.status == "completed"),
        cancelled=sum(1 for b in bookings if b.status == "cancelled"),
        rejected=sum(1 for b in bookings if b.status == "rejected"),
    )

    # 3. Payments and refunds
    payments = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.module_type == "homestay",
            PaymentTransaction.payment_status == "success",
        )
        .all()
    )
    total_payments_vol = sum((Decimal(str(p.amount)) for p in payments), Decimal("0.00"))

    refunds = (
        db.query(PaymentRefund)
        .filter(PaymentRefund.refund_status == "pending")
        .all()
    )
    total_refunds_vol = sum((Decimal(str(r.amount)) for r in refunds), Decimal("0.00"))

    payment_summary = HomestayAdminPaymentSummary(
        total_online_payments_count=len(payments),
        total_online_payments_volume=total_payments_vol,
        pending_refunds_count=len(refunds),
        pending_refunds_volume=total_refunds_vol,
        currency="LKR",
    )

    # 4. Provider settlements
    settlements = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.module_type == "homestay")
        .all()
    )
    pending_settlements = [s for s in settlements if s.settlement_status == "pending"]
    completed_settlements = [s for s in settlements if s.settlement_status == "paid"]

    settlement_summary = HomestayAdminSettlementSummary(
        pending_settlements_count=len(pending_settlements),
        pending_settlements_volume=sum((Decimal(str(s.net_amount)) for s in pending_settlements), Decimal("0.00")),
        completed_settlements_count=len(completed_settlements),
        completed_settlements_volume=sum((Decimal(str(s.net_amount)) for s in completed_settlements), Decimal("0.00")),
        currency="LKR",
    )

    # 5. Sponsored campaigns
    campaigns = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.target_module == "homestay")
        .all()
    )
    campaign_summary = HomestayAdminCampaignSummary(
        total_campaigns=len(campaigns),
        active_campaigns=sum(1 for c in campaigns if c.status == "active"),
        pending_approval_campaigns=sum(1 for c in campaigns if c.status == "pending_approval"),
        total_campaign_budget=sum((Decimal(str(c.budget)) for c in campaigns), Decimal("0.00")),
        currency="LKR",
    )

    # 6. Review moderation statistics
    homestay_listing_refs = (
        db.query(ListingReference.id)
        .filter(ListingReference.module_type == "homestay")
        .all()
    )
    listing_ref_ids = [r[0] for r in homestay_listing_refs]
    reviews = []
    if listing_ref_ids:
        reviews = (
            db.query(Review)
            .filter(Review.listing_reference_id.in_(listing_ref_ids))
            .all()
        )

    review_summary = HomestayAdminReviewSummary(
        total_reviews=len(reviews),
        pending_moderation=sum(1 for r in reviews if r.moderation_status == "pending"),
        approved_reviews=sum(1 for r in reviews if r.moderation_status == "approved"),
        rejected_reviews=sum(1 for r in reviews if r.moderation_status == "rejected"),
    )

    return HomestayAdminDashboardResponse(
        listings=listings_summary,
        bookings=bookings_summary,
        payments=payment_summary,
        settlements=settlement_summary,
        campaigns=campaign_summary,
        reviews=review_summary,
    )


@router.get(
    "/{homestay_id}",
    response_model=HomestayResponse,
    summary="View homestay listing details for admin review",
)
def get_homestay_review_details(
    homestay_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    return _get_homestay_or_404(homestay_id, db)


@router.post(
    "/{homestay_id}/review",
    response_model=HomestayResponse,
    summary="Review (approve, reject, or suspend) a homestay listing",
)
def review_homestay(
    homestay_id: int,
    payload: HomestayReviewRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    homestay = _get_homestay_or_404(homestay_id, db)

    changes = {
        "approval_status": payload.decision,
        "reviewed_by": admin.id,
        "reviewed_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }

    if payload.decision == "approved":
        changes.update(rejection_reason=None, is_active=True)
    elif payload.decision == "rejected":
        changes["rejection_reason"] = payload.reason
    elif payload.decision == "suspended":
        changes["is_active"] = False

    write_review_snapshot(db, homestay, HOMESTAY_REVIEW_STATE, changes)

    # Synchronize with central listing_references
    sync_homestay_listing_reference(db, homestay)

    # In-app notification to property provider
    provider_user_id = homestay.owner_id
    if not provider_user_id and homestay.provider_profile:
        provider_user_id = homestay.provider_profile.user_id

    if provider_user_id:
        if payload.decision == "approved":
            create_notification(
                db=db,
                user_id=provider_user_id,
                title="Homestay Listing Approved!",
                message=f"Your homestay '{homestay.name}' has been approved and is now live on CeylonGo.",
                notification_type="listing_approved",
                link_url=f"/homestays/{homestay.id}",
            )
        elif payload.decision == "rejected":
            create_notification(
                db=db,
                user_id=provider_user_id,
                title="Homestay Listing Not Approved",
                message=f"Your homestay '{homestay.name}' was not approved: {payload.reason or 'Requirements not met'}.",
                notification_type="listing_rejected",
                link_url=f"/provider/homestays/{homestay.id}",
            )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(homestay)
    return homestay


@router.post(
    "/{homestay_id}/verify",
    response_model=HomestayResponse,
    summary="Update verification status for a homestay listing",
)
def verify_homestay(
    homestay_id: int,
    payload: HomestayVerificationRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    homestay = _get_homestay_or_404(homestay_id, db)
    homestay.verification_status = payload.verification_status

    provider_user_id = homestay.owner_id
    if not provider_user_id and homestay.provider_profile:
        provider_user_id = homestay.provider_profile.user_id

    if provider_user_id:
        create_notification(
            db=db,
            user_id=provider_user_id,
            title=f"Verification Updated: {payload.verification_status.title()}",
            message=f"Verification status for '{homestay.name}' is now '{payload.verification_status}'.",
            notification_type="verification_updated",
            link_url=f"/homestays/{homestay.id}",
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(homestay)
    return homestay
