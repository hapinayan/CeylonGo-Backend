from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.core.audit import record_admin_audit_log
from app.core.booking_transactions import CAPTURED
from app.core.dependencies import (
    require_admin,
    require_operations_admin,
    require_operations_or_finance_admin,
)
from app.core.listing_sync import sync_homestay_listing_reference
from app.core.location_validation import HOMESTAY_REVIEW_STATE, write_review_snapshot
from app.core.notification_service import create_notification
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.shared import (
    BookingReference,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
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
    admin: Annotated[User, Depends(require_operations_admin)],
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
    description=(
        "Payment and refund figures cover LKR only. Homestay settlement figures "
        "are unavailable because settlements are recorded per provider without a "
        "module allocation. The settlement fields return zero placeholders."
    ),
)
def get_admin_dashboard(
    admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
    currency = "LKR"
    payment_count, payment_volume = (
        db.query(func.count(PaymentTransaction.id), func.sum(PaymentTransaction.amount))
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            BookingReference.module_type == "homestay",
            PaymentTransaction.payment_status.in_(CAPTURED),
            PaymentTransaction.payment_method == "online",
            PaymentTransaction.currency == currency,
        )
        .one()
    )

    refund_count, refund_volume = (
        db.query(func.count(PaymentRefund.id), func.sum(PaymentRefund.amount))
        .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            BookingReference.module_type == "homestay",
            PaymentRefund.refund_status == "pending",
            PaymentRefund.currency == currency,
        )
        .one()
    )

    payment_summary = HomestayAdminPaymentSummary(
        total_online_payments_count=payment_count,
        total_online_payments_volume=payment_volume or Decimal("0.00"),
        pending_refunds_count=refund_count,
        pending_refunds_volume=refund_volume or Decimal("0.00"),
        currency=currency,
    )

    # 4. Provider settlements have no module allocation. Keep the legacy numeric fields.
    settlement_summary = HomestayAdminSettlementSummary(currency=currency)

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
        pending_moderation=0,
        approved_reviews=sum(1 for r in reviews if r.is_published),
        rejected_reviews=sum(1 for r in reviews if not r.is_published),
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
    admin: Annotated[User, Depends(require_operations_admin)],
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
    request: Request,
    admin: Annotated[User, Depends(require_operations_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    homestay = _get_homestay_or_404(homestay_id, db)
    previous_status = homestay.approval_status

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

    record_admin_audit_log(
        db,
        admin_user_id=admin.id,
        action=f"homestay_{payload.decision}",
        entity_name="homestays",
        entity_id=homestay.id,
        details={
            "previous_approval_status": previous_status,
            "new_approval_status": payload.decision,
            "reason": payload.reason,
        },
        request=request,
    )

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
    request: Request,
    admin: Annotated[User, Depends(require_operations_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    homestay = _get_homestay_or_404(homestay_id, db)
    previous_verification = homestay.verification_status
    homestay.verification_status = payload.verification_status

    record_admin_audit_log(
        db,
        admin_user_id=admin.id,
        action=f"homestay_verification_{payload.verification_status}",
        entity_name="homestays",
        entity_id=homestay.id,
        details={
            "previous_verification_status": previous_verification,
            "new_verification_status": payload.verification_status,
        },
        request=request,
    )

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
