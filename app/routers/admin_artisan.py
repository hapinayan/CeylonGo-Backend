"""
Admin Artisan & Small Business Moderation Router (Phase P1 - Step 1).
Provides authenticated admin endpoints for moderating artisan businesses and showcase products:
- Listing pending/all artisan businesses and products
- Approving, rejecting (with stated reason), and suspending artisan businesses
- Approving, rejecting, and hiding showcase products
"""

import csv
import io
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin
from app.database.connection import get_db
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, Review
from app.models.showcase_item import ShowcaseItem
from app.models.user import User
from app.schemas.admin_artisan import (
    AdminArtisanBusinessReportSummary,
    AdminArtisanListingItemResponse,
    AdminArtisanProductItemResponse,
    AdminArtisanProductReportSummary,
    AdminArtisanPromotionReportSummary,
    AdminArtisanReportsSummaryResponse,
    AdminArtisanReservationExpiryResponse,
    AdminArtisanReservationReportSummary,
    AdminArtisanReviewItemResponse,
    AdminArtisanReviewModerationRequest,
    AdminArtisanReviewModerationResponse,
    AdminArtisanReviewPaginationResponse,
    AdminArtisanReviewResponse,
    ArtisanRejectionRequest,
    ArtisanSuspendRequest,
)
from app.schemas.artisan_listing import ArtisanListingResponse
from app.services.artisan_reservation_expiry import process_expired_reservations
from app.services.artisan_shared_reference import sync_artisan_listing_reference

router = APIRouter(
    prefix="/api/v1/admin/artisan",
    tags=["Admin Artisan Moderation"],
    dependencies=[Depends(require_admin)],
)


# ==============================================================================
# 1. Artisan Business Listings Moderation
# ==============================================================================

@router.get(
    "/listings",
    response_model=list[AdminArtisanListingItemResponse],
    summary="List artisan listings for admin review with filtering",
)
def list_artisan_listings_for_review(
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default="pending", alias="status", description="Filter by approval status: pending, approved, rejected, suspended, or all"),
    search: str | None = Query(default=None, description="Search in title, description, city, or provider business name"),
    district: str | None = Query(default=None, description="Filter by district"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    Admin endpoint: Retrieve artisan listings awaiting review or filtered by moderation status.
    """
    query = db.query(ArtisanListing).join(ProviderProfile, ArtisanListing.provider_profile_id == ProviderProfile.id)

    if status_filter and status_filter.lower() != "all":
        query = query.filter(ArtisanListing.approval_status == status_filter.strip().lower())

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(ArtisanListing.title).like(term),
                func.lower(ArtisanListing.description).like(term),
                func.lower(ArtisanListing.city).like(term),
                func.lower(ProviderProfile.business_name).like(term),
            )
        )

    if district and district.strip():
        query = query.filter(func.lower(ArtisanListing.district) == district.strip().lower())

    listings = (
        query.order_by(ArtisanListing.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return [AdminArtisanListingItemResponse.model_validate(item) for item in listings]


@router.get(
    "/listings/{id}",
    response_model=ArtisanListingResponse,
    summary="View full details of an artisan listing for review",
)
def get_artisan_listing_for_review(
    id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: View full listing details regardless of approval state.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )
    return ArtisanListingResponse.model_validate(listing)


@router.post(
    "/listings/{id}/approve",
    response_model=AdminArtisanReviewResponse,
    summary="Approve an artisan business listing",
)
def approve_artisan_listing(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Approve an artisan business listing to publish it to public discovery.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    if listing.approval_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artisan listing is already approved",
        )

    listing.approval_status = "approved"
    listing.is_active = True
    listing.reviewed_by = admin_user.id
    listing.reviewed_at = func.sysutcdatetime()
    listing.rejection_reason = None

    sync_artisan_listing_reference(db, listing)
    db.commit()
    db.refresh(listing)

    return AdminArtisanReviewResponse(
        success=True,
        id=listing.id,
        entity_type="artisan_listing",
        approval_status="approved",
        rejection_reason=None,
        message="Artisan business listing approved successfully.",
        reviewed_at=listing.reviewed_at,
    )


@router.post(
    "/listings/{id}/reject",
    response_model=AdminArtisanReviewResponse,
    summary="Reject an artisan business listing with a mandatory reason",
)
def reject_artisan_listing(
    id: int,
    payload: ArtisanRejectionRequest,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Reject an artisan business listing with a stated audit reason.
    Hides the listing from public discovery while preserving provider visibility.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    listing.approval_status = "rejected"
    listing.is_active = False
    listing.is_featured = False
    listing.rejection_reason = payload.reason
    listing.reviewed_by = admin_user.id
    listing.reviewed_at = func.sysutcdatetime()

    sync_artisan_listing_reference(db, listing)
    db.commit()
    db.refresh(listing)

    return AdminArtisanReviewResponse(
        success=True,
        id=listing.id,
        entity_type="artisan_listing",
        approval_status="rejected",
        rejection_reason=payload.reason,
        message="Artisan business listing rejected.",
        reviewed_at=listing.reviewed_at,
    )


@router.post(
    "/listings/{id}/suspend",
    response_model=AdminArtisanReviewResponse,
    summary="Suspend an active artisan business listing",
)
def suspend_artisan_listing(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    payload: ArtisanSuspendRequest | None = None,
):
    """
    Admin endpoint: Suspend an artisan business listing. Deactivates public visibility.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    reason = payload.reason if payload and payload.reason else "Suspended by administrator"
    listing.approval_status = "suspended"
    listing.is_active = False
    listing.is_featured = False
    listing.rejection_reason = reason
    listing.reviewed_by = admin_user.id
    listing.reviewed_at = func.sysutcdatetime()

    sync_artisan_listing_reference(db, listing)
    db.commit()
    db.refresh(listing)

    return AdminArtisanReviewResponse(
        success=True,
        id=listing.id,
        entity_type="artisan_listing",
        approval_status="suspended",
        rejection_reason=reason,
        message="Artisan business listing suspended.",
        reviewed_at=listing.reviewed_at,
    )


@router.post(
    "/listings/{id}/feature",
    response_model=AdminArtisanReviewResponse,
    summary="Feature an approved artisan business listing",
)
def feature_artisan_listing(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Mark an approved, active artisan business listing as featured.
    Rejects unapproved or inactive listings with HTTP 400 Bad Request.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    if listing.approval_status != "approved" or not listing.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot feature an artisan business that is not approved and active",
        )

    listing.is_featured = True
    sync_artisan_listing_reference(db, listing)
    db.commit()
    db.refresh(listing)

    return AdminArtisanReviewResponse(
        success=True,
        id=listing.id,
        entity_type="artisan_listing",
        approval_status=listing.approval_status,
        message="Artisan business listing marked as featured.",
        reviewed_at=listing.reviewed_at,
    )


@router.post(
    "/listings/{id}/unfeature",
    response_model=AdminArtisanReviewResponse,
    summary="Remove featured status from an artisan business listing",
)
def unfeature_artisan_listing(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Remove featured status from an artisan business listing.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    listing.is_featured = False
    sync_artisan_listing_reference(db, listing)
    db.commit()
    db.refresh(listing)

    return AdminArtisanReviewResponse(
        success=True,
        id=listing.id,
        entity_type="artisan_listing",
        approval_status=listing.approval_status,
        message="Artisan business listing unfeatured.",
        reviewed_at=listing.reviewed_at,
    )


# ==============================================================================
# 2. Showcase Product Moderation
# ==============================================================================

@router.get(
    "/products",
    response_model=list[AdminArtisanProductItemResponse],
    summary="List showcase items for admin review with filtering",
)
def list_showcase_products_for_review(
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default="pending", alias="status", description="Filter by approval status: pending, approved, rejected, or all"),
    listing_id: int | None = Query(default=None, description="Filter by parent artisan listing ID"),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    Admin endpoint: Retrieve showcase products and services filtered by approval status.
    """
    query = db.query(ShowcaseItem).join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)

    if status_filter and status_filter.lower() != "all":
        query = query.filter(ShowcaseItem.approval_status == status_filter.strip().lower())

    if listing_id is not None:
        query = query.filter(ShowcaseItem.artisan_listing_id == listing_id)

    items = (
        query.order_by(ShowcaseItem.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return [AdminArtisanProductItemResponse.model_validate(item) for item in items]


@router.post(
    "/products/{id}/approve",
    response_model=AdminArtisanReviewResponse,
    summary="Approve a showcase product or service",
)
def approve_showcase_product(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Approve a showcase product, making it available in the catalog.
    """
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase product not found",
        )

    if item.approval_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Showcase product is already approved",
        )

    item.approval_status = "approved"
    item.is_active = True
    item.updated_at = func.sysutcdatetime()

    db.commit()
    db.refresh(item)

    return AdminArtisanReviewResponse(
        success=True,
        id=item.id,
        entity_type="showcase_item",
        approval_status="approved",
        message="Showcase product approved successfully.",
        reviewed_at=item.updated_at,
    )


@router.post(
    "/products/{id}/reject",
    response_model=AdminArtisanReviewResponse,
    summary="Reject a showcase product or service",
)
def reject_showcase_product(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    payload: ArtisanRejectionRequest | None = None,
):
    """
    Admin endpoint: Reject a showcase product. Hides it from public catalog.
    """
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase product not found",
        )

    reason = payload.reason if payload else "Rejected by administrator"
    item.approval_status = "rejected"
    item.is_active = False
    item.updated_at = func.sysutcdatetime()

    db.commit()
    db.refresh(item)

    return AdminArtisanReviewResponse(
        success=True,
        id=item.id,
        entity_type="showcase_item",
        approval_status="rejected",
        rejection_reason=reason,
        message="Showcase product rejected.",
        reviewed_at=item.updated_at,
    )


@router.post(
    "/products/{id}/hide",
    response_model=AdminArtisanReviewResponse,
    summary="Hide an active showcase product",
)
def hide_showcase_product(
    id: int,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Soft deactivate an item from public catalog without changing approval status.
    """
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase product not found",
        )

    item.is_active = False
    item.updated_at = func.sysutcdatetime()

    db.commit()
    db.refresh(item)

    return AdminArtisanReviewResponse(
        success=True,
        id=item.id,
        entity_type="showcase_item",
        approval_status=item.approval_status,
        message="Showcase product hidden successfully.",
        reviewed_at=item.updated_at,
    )


# ==============================================================================
# 3. Reservation Auto-Expiry Trigger
# ==============================================================================

@router.post(
    "/reservations/process-expired",
    response_model=AdminArtisanReservationExpiryResponse,
    summary="Batch-expire past-due artisan reservations and restore stock",
)
def admin_process_expired_reservations(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    batch_size: int = Query(default=100, ge=1, le=1000, description="Max reservations to expire"),
):
    """
    Admin endpoint: Manually or via webhook/cron trigger batch expiration of past-due reservations.
    """
    result = process_expired_reservations(db, batch_size=batch_size)
    return AdminArtisanReservationExpiryResponse(
        scanned_count=result.scanned_count,
        expired_count=result.expired_count,
        skipped_count=result.skipped_count,
        restored_stock_total=result.restored_stock_total,
        message=f"Successfully processed {result.scanned_count} past-due reservations: {result.expired_count} expired, {result.restored_stock_total} items restored to stock.",
    )


# ==============================================================================
# 4. Safe Admin Basic Reports & CSV Exports (Phase P2 Step 16)
# ==============================================================================

def safe_csv_cell(value: object) -> str:
    """
    Sanitizes a CSV cell to prevent spreadsheet formula injection attacks (CSV Injection / DDE).
    If a string value starts with '=', '+', '-', '@', tab, or newline, prepends a single quote.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s


@router.get(
    "/reports/summary",
    response_model=AdminArtisanReportsSummaryResponse,
    summary="Admin summary reports across businesses, products, reservations, and promotions",
)
def get_admin_reports_summary(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Computes aggregate KPI metrics and status breakdowns across
    Artisan businesses, showcase products, pickup reservations, and discount promotions.
    Does not invent payment/revenue statistics; strictly reports real collected totals.
    """
    # 1. Businesses
    total_biz = db.query(ArtisanListing).count()
    approved_biz = db.query(ArtisanListing).filter(ArtisanListing.approval_status == "approved").count()
    pending_biz = db.query(ArtisanListing).filter(ArtisanListing.approval_status == "pending").count()
    rejected_biz = db.query(ArtisanListing).filter(ArtisanListing.approval_status == "rejected").count()
    suspended_biz = db.query(ArtisanListing).filter(ArtisanListing.approval_status == "suspended").count()
    verified_biz = db.query(ArtisanListing).filter(ArtisanListing.is_verified == True).count()
    featured_biz = db.query(ArtisanListing).filter(ArtisanListing.is_featured == True).count()

    biz_summary = AdminArtisanBusinessReportSummary(
        total_businesses=total_biz,
        approved_count=approved_biz,
        pending_count=pending_biz,
        rejected_count=rejected_biz,
        suspended_count=suspended_biz,
        verified_count=verified_biz,
        featured_count=featured_biz,
    )

    # 2. Products
    total_prod = db.query(ShowcaseItem).count()
    active_prod = db.query(ShowcaseItem).filter(ShowcaseItem.is_active == True).count()
    inactive_prod = db.query(ShowcaseItem).filter(ShowcaseItem.is_active == False).count()
    out_of_stock_prod = db.query(ShowcaseItem).filter(
        ShowcaseItem.is_active == True,
        or_(ShowcaseItem.stock_quantity <= 0, ShowcaseItem.is_available == False),
    ).count()
    approved_prod = db.query(ShowcaseItem).filter(ShowcaseItem.approval_status == "approved").count()
    pending_prod = db.query(ShowcaseItem).filter(ShowcaseItem.approval_status == "pending").count()
    rejected_prod = db.query(ShowcaseItem).filter(ShowcaseItem.approval_status == "rejected").count()

    prod_summary = AdminArtisanProductReportSummary(
        total_products=total_prod,
        active_count=active_prod,
        inactive_count=inactive_prod,
        out_of_stock_count=out_of_stock_prod,
        approved_count=approved_prod,
        pending_count=pending_prod,
        rejected_count=rejected_prod,
    )

    # 3. Reservations
    total_res = db.query(ArtisanReservation).count()
    pending_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "pending").count()
    confirmed_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "confirmed").count()
    ready_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "ready_for_pickup").count()
    completed_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "completed").count()
    cancelled_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "cancelled").count()
    expired_res = db.query(ArtisanReservation).filter(ArtisanReservation.reservation_status == "expired").count()

    collected_sum = (
        db.query(func.sum(ArtisanReservation.total_price))
        .filter(ArtisanReservation.reservation_status == "completed")
        .scalar()
    )
    total_collected = Decimal(str(collected_sum)) if collected_sum is not None else Decimal("0.00")

    res_summary = AdminArtisanReservationReportSummary(
        total_reservations=total_res,
        pending_count=pending_res,
        confirmed_count=confirmed_res,
        ready_count=ready_res,
        completed_count=completed_res,
        cancelled_count=cancelled_res,
        expired_count=expired_res,
        total_collected_lkr=total_collected,
    )

    # 4. Promotions
    total_promo = db.query(Promotion).count()
    active_promo = db.query(Promotion).filter(Promotion.is_active == True).count()
    inactive_promo = db.query(Promotion).filter(Promotion.is_active == False).count()

    promo_summary = AdminArtisanPromotionReportSummary(
        total_promotions=total_promo,
        active_count=active_promo,
        inactive_count=inactive_promo,
    )

    return AdminArtisanReportsSummaryResponse(
        businesses=biz_summary,
        products=prod_summary,
        reservations=res_summary,
        promotions=promo_summary,
        generated_at=datetime.now(UTC),
    )


@router.get(
    "/reports/export/{report_type}",
    summary="Export admin tabular CSV report for businesses, products, reservations, or promotions",
)
def export_admin_report_csv(
    report_type: str,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default=None, alias="status", description="Filter by status"),
    start_date: date | None = Query(default=None, description="Start date filter"),
    end_date: date | None = Query(default=None, description="End date filter"),
    district: str | None = Query(default=None, description="District filter for businesses"),
):
    """
    Admin-only endpoint: Exports safe, formula-injection-protected tabular CSV report.
    Guarantees no unnecessary personal data disclosure (no passwords, tokens, full traveler identities).
    """
    clean_type = report_type.lower().strip()
    if clean_type not in ("businesses", "products", "reservations", "promotions"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid report type '{report_type}'. Must be one of: businesses, products, reservations, promotions.",
        )

    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    if clean_type == "businesses":
        writer.writerow([
            "Business ID",
            "Business Title",
            "Craft Category",
            "District",
            "City",
            "Approval Status",
            "Active Status",
            "Verified",
            "Featured",
            "Contact Phone",
            "Contact Email",
            "Created At (UTC)",
        ])
        query = db.query(ArtisanListing)
        if status_filter and isinstance(status_filter, str):
            query = query.filter(ArtisanListing.approval_status == status_filter)
        if district and isinstance(district, str):
            query = query.filter(ArtisanListing.district == district)
        if start_date and isinstance(start_date, date):
            query = query.filter(ArtisanListing.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date and isinstance(end_date, date):
            query = query.filter(ArtisanListing.created_at <= datetime.combine(end_date, datetime.max.time()))

        for item in query.order_by(ArtisanListing.id.asc()).all():
            writer.writerow([
                safe_csv_cell(item.id),
                safe_csv_cell(item.title),
                safe_csv_cell(item.craft_category),
                safe_csv_cell(item.district),
                safe_csv_cell(item.city),
                safe_csv_cell(item.approval_status),
                safe_csv_cell("Active" if item.is_active else "Inactive"),
                safe_csv_cell("Yes" if item.is_verified else "No"),
                safe_csv_cell("Yes" if item.is_featured else "No"),
                safe_csv_cell(getattr(item, "phone", "")),
                safe_csv_cell(getattr(item, "email", "")),
                safe_csv_cell(item.created_at.isoformat() if item.created_at else ""),
            ])

    elif clean_type == "products":
        writer.writerow([
            "Product ID",
            "Listing ID",
            "Product Name",
            "Craft Type",
            "Price (LKR)",
            "Stock Quantity",
            "Available Status",
            "Active Status",
            "Approval Status",
            "Created At (UTC)",
        ])
        query = db.query(ShowcaseItem)
        if status_filter and isinstance(status_filter, str):
            query = query.filter(ShowcaseItem.approval_status == status_filter)
        if start_date and isinstance(start_date, date):
            query = query.filter(ShowcaseItem.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date and isinstance(end_date, date):
            query = query.filter(ShowcaseItem.created_at <= datetime.combine(end_date, datetime.max.time()))

        for prod in query.order_by(ShowcaseItem.id.asc()).all():
            writer.writerow([
                safe_csv_cell(prod.id),
                safe_csv_cell(prod.artisan_listing_id),
                safe_csv_cell(prod.title),
                safe_csv_cell(prod.item_type),
                safe_csv_cell(prod.price),
                safe_csv_cell(prod.stock_quantity),
                safe_csv_cell("Available" if prod.is_available else "Unavailable"),
                safe_csv_cell("Active" if prod.is_active else "Inactive"),
                safe_csv_cell(prod.approval_status),
                safe_csv_cell(prod.created_at.isoformat() if prod.created_at else ""),
            ])

    elif clean_type == "reservations":
        writer.writerow([
            "Reservation ID",
            "Listing ID",
            "Showcase Item ID",
            "Quantity",
            "Total Price (LKR)",
            "Currency",
            "Reservation Status",
            "Pickup Date",
            "Created At (UTC)",
        ])
        query = db.query(ArtisanReservation)
        if status_filter and isinstance(status_filter, str):
            query = query.filter(ArtisanReservation.reservation_status == status_filter)
        if start_date and isinstance(start_date, date):
            query = query.filter(ArtisanReservation.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date and isinstance(end_date, date):
            query = query.filter(ArtisanReservation.created_at <= datetime.combine(end_date, datetime.max.time()))

        for res in query.order_by(ArtisanReservation.id.asc()).all():
            writer.writerow([
                safe_csv_cell(res.id),
                safe_csv_cell(res.artisan_listing_id),
                safe_csv_cell(res.showcase_item_id),
                safe_csv_cell(res.quantity),
                safe_csv_cell(res.total_price),
                safe_csv_cell(res.currency),
                safe_csv_cell(res.reservation_status),
                safe_csv_cell(str(res.pickup_date) if res.pickup_date else ""),
                safe_csv_cell(res.created_at.isoformat() if res.created_at else ""),
            ])

    elif clean_type == "promotions":
        writer.writerow([
            "Promotion ID",
            "Listing ID",
            "Title",
            "Promo Code",
            "Discount Type",
            "Discount Value",
            "Start Date",
            "End Date",
            "Active Status",
            "Created At (UTC)",
        ])
        query = db.query(Promotion)
        if status_filter == "active":
            query = query.filter(Promotion.is_active == True)
        elif status_filter == "inactive":
            query = query.filter(Promotion.is_active == False)
        if start_date and isinstance(start_date, date):
            query = query.filter(Promotion.start_date >= start_date)
        if end_date and isinstance(end_date, date):
            query = query.filter(Promotion.end_date <= end_date)

        for promo in query.order_by(Promotion.id.asc()).all():
            disc_type = "percentage" if promo.discount_percentage is not None else ("fixed" if promo.discount_amount is not None else "custom")
            disc_val = f"{promo.discount_percentage}%" if promo.discount_percentage is not None else (f"LKR {promo.discount_amount}" if promo.discount_amount is not None else "")
            writer.writerow([
                safe_csv_cell(promo.id),
                safe_csv_cell(promo.artisan_listing_id),
                safe_csv_cell(promo.title),
                safe_csv_cell(promo.promo_code if promo.promo_code else ""),
                safe_csv_cell(disc_type),
                safe_csv_cell(disc_val),
                safe_csv_cell(str(promo.start_date) if promo.start_date else ""),
                safe_csv_cell(str(promo.end_date) if promo.end_date else ""),
                safe_csv_cell("Active" if promo.is_active else "Inactive"),
                safe_csv_cell(promo.created_at.isoformat() if promo.created_at else ""),
            ])

    csv_text = output.getvalue()
    filename = f"artisan_{clean_type}_report_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


# ==============================================================================
# 5. Review Moderation (Phase P2 Step 16)
# ==============================================================================

@router.get(
    "/reviews",
    response_model=AdminArtisanReviewPaginationResponse,
    summary="List customer reviews for artisan listings with moderation status",
)
def list_artisan_reviews_for_moderation(
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    is_published: bool | None = Query(default=None, description="Filter by publication visibility"),
    listing_id: int | None = Query(default=None, description="Filter by artisan listing ID"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    Admin endpoint: Query reviews on artisan listings (via dbo.reviews and dbo.listing_references).
    Enables moderation oversight without altering underlying database schemas.
    """
    query = (
        db.query(Review, ListingReference, ArtisanListing)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .join(ArtisanListing, ListingReference.target_id == ArtisanListing.id)
        .filter(ListingReference.module_type == "artisan")
    )

    p = page if isinstance(page, int) else 1
    ps = page_size if isinstance(page_size, int) else 20
    is_pub = is_published if isinstance(is_published, bool) else None
    l_id = listing_id if isinstance(listing_id, int) else None

    if is_pub is not None:
        query = query.filter(Review.is_published == is_pub)
    if l_id is not None:
        query = query.filter(ArtisanListing.id == l_id)

    total = query.count()
    total_pages = math.ceil(total / ps) if total > 0 else 0

    entries = (
        query.order_by(Review.created_at.desc(), Review.id.desc())
        .offset((p - 1) * ps)
        .limit(ps)
        .all()
    )

    items = []
    for rev, l_ref, listing in entries:
        items.append(
            AdminArtisanReviewItemResponse(
                id=rev.id,
                listing_id=listing.id,
                listing_title=listing.title,
                user_id=rev.user_id,
                rating=Decimal(str(rev.rating)),
                title=rev.title,
                comment=rev.comment,
                is_verified_booking=rev.is_verified_booking,
                is_published=rev.is_published,
                created_at=rev.created_at,
                updated_at=rev.updated_at,
            )
        )

    return AdminArtisanReviewPaginationResponse(
        items=items,
        total=total,
        page=p,
        page_size=ps,
        total_pages=total_pages,
    )


@router.patch(
    "/reviews/{id}/publish",
    response_model=AdminArtisanReviewModerationResponse,
    summary="Moderate an artisan review (publish or hide)",
)
def moderate_artisan_review(
    id: int,
    payload: AdminArtisanReviewModerationRequest,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin endpoint: Toggle is_published for an artisan review.
    When is_published=False, the review is strictly excluded from public lists and rating averages.
    """
    review = (
        db.query(Review)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .filter(
            Review.id == id,
            ListingReference.module_type == "artisan",
        )
        .first()
    )
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan review not found",
        )

    review.is_published = payload.is_published
    review.updated_at = func.sysutcdatetime()

    db.commit()
    db.refresh(review)

    status_str = "published" if review.is_published else "hidden"
    return AdminArtisanReviewModerationResponse(
        success=True,
        review_id=review.id,
        is_published=review.is_published,
        message=f"Review #{review.id} successfully {status_str}.",
        moderated_at=review.updated_at,
    )



