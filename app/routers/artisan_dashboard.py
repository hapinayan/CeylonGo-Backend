from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.schemas.artisan_dashboard import (
    ArtisanBusinessOverview,
    ArtisanDashboardRecentProduct,
    ArtisanDashboardRecentReservation,
    ArtisanDashboardResponse,
    ArtisanFinancialSummary,
    ArtisanPendingApprovalSummary,
    ArtisanProductOverview,
    ArtisanPromotionItem,
    ArtisanPromotionOverview,
    ArtisanReservationOverview,
)

router = APIRouter(prefix="/api/v1", tags=["Artisan Dashboard"])


@router.get(
    "/artisan/dashboard",
    response_model=ArtisanDashboardResponse,
    summary="Get authenticated artisan provider dashboard summary metrics",
)
@router.get(
    "/artisan-listings/me/dashboard",
    response_model=ArtisanDashboardResponse,
    include_in_schema=False,
)
@router.get(
    "/artisan-dashboard",
    response_model=ArtisanDashboardResponse,
    include_in_schema=False,
)
def get_artisan_dashboard(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns a structured operational, inventory, reservation, and
    financial metrics overview for the authenticated artisan provider.
    Strictly scoped to businesses, products, and reservations owned by the caller.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can access the artisan dashboard",
        )

    # 1. Business Listings Aggregation
    listing_metrics = (
        db.query(
            func.count(ArtisanListing.id).label("total"),
            func.sum(case((ArtisanListing.approval_status == "approved", 1), else_=0)).label("approved"),
            func.sum(case((ArtisanListing.approval_status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((ArtisanListing.approval_status == "rejected", 1), else_=0)).label("rejected"),
            func.sum(case((ArtisanListing.approval_status == "suspended", 1), else_=0)).label("suspended"),
            func.sum(case((ArtisanListing.is_active == True, 1), else_=0)).label("active"),
            func.sum(case((ArtisanListing.is_active == False, 1), else_=0)).label("inactive"),
            func.sum(
                case(
                    (
                        or_(
                            ArtisanListing.is_verified == True,
                            ArtisanListing.verification_status == "verified",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("verified"),
            func.sum(
                case(
                    (
                        and_(
                            ArtisanListing.is_verified == False,
                            ArtisanListing.verification_status != "verified",
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("unverified"),
        )
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .first()
    )

    business_overview = ArtisanBusinessOverview(
        total_listings=int(listing_metrics.total or 0) if listing_metrics else 0,
        approved_listings=int(listing_metrics.approved or 0) if listing_metrics else 0,
        pending_listings=int(listing_metrics.pending or 0) if listing_metrics else 0,
        rejected_listings=int(listing_metrics.rejected or 0) if listing_metrics else 0,
        suspended_listings=int(listing_metrics.suspended or 0) if listing_metrics else 0,
        active_listings=int(listing_metrics.active or 0) if listing_metrics else 0,
        inactive_listings=int(listing_metrics.inactive or 0) if listing_metrics else 0,
        verified_listings=int(listing_metrics.verified or 0) if listing_metrics else 0,
        unverified_listings=int(listing_metrics.unverified or 0) if listing_metrics else 0,
    )

    # 2. Showcase Products Aggregation
    product_metrics = (
        db.query(
            func.count(ShowcaseItem.id).label("total"),
            func.sum(case((ShowcaseItem.is_active == True, 1), else_=0)).label("active"),
            func.sum(case((ShowcaseItem.is_active == False, 1), else_=0)).label("inactive"),
            func.sum(case((ShowcaseItem.stock_quantity > 0, 1), else_=0)).label("in_stock"),
            func.sum(case((ShowcaseItem.stock_quantity == 0, 1), else_=0)).label("out_of_stock"),
            func.sum(case((ShowcaseItem.is_available == True, 1), else_=0)).label("available"),
            func.sum(case((ShowcaseItem.approval_status == "approved", 1), else_=0)).label("approved"),
            func.sum(case((ShowcaseItem.approval_status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((ShowcaseItem.approval_status == "rejected", 1), else_=0)).label("rejected"),
        )
        .join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .first()
    )

    product_overview = ArtisanProductOverview(
        total_products=int(product_metrics.total or 0) if product_metrics else 0,
        active_products=int(product_metrics.active or 0) if product_metrics else 0,
        inactive_products=int(product_metrics.inactive or 0) if product_metrics else 0,
        available_products=int(product_metrics.available or 0) if product_metrics else 0,
        in_stock_products=int(product_metrics.in_stock or 0) if product_metrics else 0,
        out_of_stock_products=int(product_metrics.out_of_stock or 0) if product_metrics else 0,
        approved_products=int(product_metrics.approved or 0) if product_metrics else 0,
        pending_products=int(product_metrics.pending or 0) if product_metrics else 0,
        rejected_products=int(product_metrics.rejected or 0) if product_metrics else 0,
    )

    # 3. Product Reservations & Financial Value Aggregation
    reservation_metrics = (
        db.query(
            func.count(ArtisanReservation.id).label("total"),
            func.sum(case((ArtisanReservation.reservation_status == "pending", 1), else_=0)).label("pending"),
            func.sum(case((ArtisanReservation.reservation_status == "confirmed", 1), else_=0)).label("confirmed"),
            func.sum(case((ArtisanReservation.reservation_status == "ready_for_pickup", 1), else_=0)).label("ready_for_pickup"),
            func.sum(case((ArtisanReservation.reservation_status == "completed", 1), else_=0)).label("completed"),
            func.sum(case((ArtisanReservation.reservation_status == "cancelled", 1), else_=0)).label("cancelled"),
            func.sum(case((ArtisanReservation.reservation_status == "expired", 1), else_=0)).label("expired"),
            func.sum(
                case(
                    (ArtisanReservation.reservation_status == "completed", ArtisanReservation.total_price),
                    else_=Decimal("0.00"),
                )
            ).label("completed_value"),
        )
        .join(ArtisanListing, ArtisanReservation.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .first()
    )

    completed_val_raw = (
        reservation_metrics.completed_value
        if reservation_metrics and reservation_metrics.completed_value is not None
        else Decimal("0.00")
    )
    completed_val = Decimal(str(completed_val_raw)).quantize(Decimal("0.01"))

    reservation_overview = ArtisanReservationOverview(
        total_reservations=int(reservation_metrics.total or 0) if reservation_metrics else 0,
        pending_reservations=int(reservation_metrics.pending or 0) if reservation_metrics else 0,
        confirmed_reservations=int(reservation_metrics.confirmed or 0) if reservation_metrics else 0,
        ready_for_pickup_reservations=int(reservation_metrics.ready_for_pickup or 0) if reservation_metrics else 0,
        completed_reservations=int(reservation_metrics.completed or 0) if reservation_metrics else 0,
        cancelled_reservations=int(reservation_metrics.cancelled or 0) if reservation_metrics else 0,
        expired_reservations=int(reservation_metrics.expired or 0) if reservation_metrics else 0,
    )

    financial_summary = ArtisanFinancialSummary(
        recorded_completed_pickup_value_lkr=completed_val,
        currency="LKR",
    )

    # 4. Recent Reservations (up to 5)
    recent_reservations_db = (
        db.query(ArtisanReservation)
        .join(ArtisanListing, ArtisanReservation.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .order_by(ArtisanReservation.created_at.desc())
        .limit(5)
        .all()
    )
    recent_reservations = [
        ArtisanDashboardRecentReservation.model_validate(r) for r in recent_reservations_db
    ]

    # 5. Recent Products (up to 5)
    recent_products_db = (
        db.query(ShowcaseItem)
        .join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .order_by(ShowcaseItem.created_at.desc())
        .limit(5)
        .all()
    )
    recent_products = [
        ArtisanDashboardRecentProduct.model_validate(p) for p in recent_products_db
    ]

    # 6. Pending Approval Summary
    pending_listings_count = business_overview.pending_listings
    pending_products_count = product_overview.pending_products
    pending_approval = ArtisanPendingApprovalSummary(
        pending_listings_count=pending_listings_count,
        pending_products_count=pending_products_count,
        requires_action=(pending_listings_count > 0 or pending_products_count > 0),
    )

    # 7. Promotions Overview
    now = datetime.now(timezone.utc)
    active_promos_db = (
        db.query(Promotion)
        .join(ArtisanListing, Promotion.artisan_listing_id == ArtisanListing.id)
        .filter(
            ArtisanListing.provider_profile_id == provider_profile.id,
            Promotion.is_active == True,
            Promotion.start_date <= now,
            Promotion.end_date >= now,
        )
        .order_by(Promotion.created_at.desc())
        .all()
    )
    total_promos_count = (
        db.query(func.count(Promotion.id))
        .join(ArtisanListing, Promotion.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .scalar()
        or 0
    )

    promotions_overview = ArtisanPromotionOverview(
        total_promotions=total_promos_count,
        active_promotions_count=len(active_promos_db),
        active_promotions=[ArtisanPromotionItem.model_validate(p) for p in active_promos_db],
    )

    return ArtisanDashboardResponse(
        provider_id=provider_profile.id,
        provider_business_name=provider_profile.business_name,
        business_overview=business_overview,
        product_overview=product_overview,
        reservation_overview=reservation_overview,
        financial_summary=financial_summary,
        recent_reservations=recent_reservations,
        recent_products=recent_products,
        pending_approval=pending_approval,
        promotions_overview=promotions_overview,
    )
