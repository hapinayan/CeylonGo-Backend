from datetime import UTC, datetime
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.publication import public_artisan
from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.artisan_listing import ArtisanListing
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.schemas.promotion import (
    PromotionCreate,
    PromotionPagination,
    PromotionResponse,
    PromotionSummary,
    PromotionUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["Promotions"])


# ==============================================================================
# Public Endpoints
# ==============================================================================

@router.get(
    "/promotions",
    response_model=PromotionPagination,
    summary="Browse active and non-expired artisan promotions",
)
def list_promotions(
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(default=None, max_length=100, description="Search in promotion title or promo code"),
    district: str | None = Query(default=None, description="Filter by artisan listing district"),
    craft_category: str | None = Query(default=None, description="Filter by artisan craft category"),
    artisan_listing_id: int | None = Query(default=None, description="Filter by parent artisan listing ID"),
    showcase_item_id: int | None = Query(default=None, description="Filter by target showcase item ID"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=12, ge=1, le=100, description="Items per page"),
):
    """
    Public discovery endpoint.
    Retrieves currently active and non-expired promotions.
    Excludes inactive promotions, unstarted/expired promotions, and promotions
    attached to inactive artisan listings or inactive showcase items.
    """
    now = datetime.now(UTC)

    query = (
        db.query(Promotion)
        .join(ArtisanListing, Promotion.artisan_listing_id == ArtisanListing.id)
        .outerjoin(ShowcaseItem, Promotion.showcase_item_id == ShowcaseItem.id)
        .filter(
            Promotion.is_active == True,
            Promotion.start_date <= now,
            Promotion.end_date >= now,
            public_artisan(),
            or_(Promotion.showcase_item_id == None, ShowcaseItem.is_active == True),
        )
    )

    if search and search.strip():
        search_pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Promotion.title).like(search_pattern),
                func.lower(Promotion.promo_code).like(search_pattern),
                func.lower(Promotion.description).like(search_pattern),
            )
        )

    if district and district.strip():
        query = query.filter(func.lower(ArtisanListing.district) == district.strip().lower())

    if craft_category and craft_category.strip():
        query = query.filter(func.lower(ArtisanListing.craft_category) == craft_category.strip().lower())

    if artisan_listing_id is not None:
        query = query.filter(Promotion.artisan_listing_id == artisan_listing_id)

    if showcase_item_id is not None:
        query = query.filter(Promotion.showcase_item_id == showcase_item_id)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    promotions = (
        query.order_by(Promotion.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PromotionPagination(
        items=[PromotionSummary.model_validate(p) for p in promotions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/promotions/me/items",
    response_model=list[PromotionResponse],
    summary="Get authenticated provider's promotions",
)
@router.get(
    "/promotions/me/promotions",
    response_model=list[PromotionResponse],
    include_in_schema=False,
)
def get_my_promotions(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns all promotions (active, inactive, scheduled, expired)
    belonging to any artisan listing owned by the authenticated approved provider.
    """
    promotions = (
        db.query(Promotion)
        .join(ArtisanListing, Promotion.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .order_by(Promotion.created_at.desc())
        .all()
    )
    return [PromotionResponse.model_validate(p) for p in promotions]


@router.get(
    "/promotions/{id}",
    response_model=PromotionResponse,
    summary="Retrieve promotion details",
)
def get_promotion(
    id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public endpoint: Returns details of an active, non-expired promotion.
    Excludes inactive promotions, expired promotions, and promotions
    attached to inactive listings or inactive showcase items.
    """
    now = datetime.now(UTC)

    promo = (
        db.query(Promotion)
        .join(ArtisanListing, Promotion.artisan_listing_id == ArtisanListing.id)
        .outerjoin(ShowcaseItem, Promotion.showcase_item_id == ShowcaseItem.id)
        .filter(
            Promotion.id == id,
            Promotion.is_active == True,
            Promotion.start_date <= now,
            Promotion.end_date >= now,
            public_artisan(),
            or_(Promotion.showcase_item_id == None, ShowcaseItem.is_active == True),
        )
        .first()
    )
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion not found or has expired",
        )
    return PromotionResponse.model_validate(promo)


@router.get(
    "/artisan-listings/{listing_id}/promotions",
    response_model=PromotionPagination,
    summary="Browse promotions belonging to an artisan listing",
)
def get_listing_promotions(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=12, ge=1, le=100, description="Items per page"),
):
    """
    Public endpoint: Returns active and currently valid promotions for a specific artisan listing.
    """
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == listing_id, public_artisan())
        .first()
    )
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    now = datetime.now(UTC)

    query = (
        db.query(Promotion)
        .outerjoin(ShowcaseItem, Promotion.showcase_item_id == ShowcaseItem.id)
        .filter(
            Promotion.artisan_listing_id == listing_id,
            Promotion.is_active == True,
            Promotion.start_date <= now,
            Promotion.end_date >= now,
            or_(Promotion.showcase_item_id == None, ShowcaseItem.is_active == True),
        )
    )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    promotions = (
        query.order_by(Promotion.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PromotionPagination(
        items=[PromotionSummary.model_validate(p) for p in promotions],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==============================================================================
# Protected Endpoints (Owner Only)
# ==============================================================================

@router.post(
    "/promotions",
    response_model=PromotionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new promotion for an artisan listing",
)
def create_promotion(
    payload: PromotionCreate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan service provider to create
    a promotion for an artisan listing they own.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can create promotions",
        )

    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == payload.artisan_listing_id)
        .first()
    )
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this artisan listing",
        )

    # Validate showcase_item_id belongs to the same artisan_listing_id
    if payload.showcase_item_id is not None:
        item = (
            db.query(ShowcaseItem)
            .filter(ShowcaseItem.id == payload.showcase_item_id)
            .first()
        )
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Showcase item not found",
            )
        if item.artisan_listing_id != payload.artisan_listing_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Showcase item does not belong to the specified artisan listing",
            )

    new_promo = Promotion(
        artisan_listing_id=payload.artisan_listing_id,
        showcase_item_id=payload.showcase_item_id,
        title=payload.title,
        description=payload.description,
        promo_code=payload.promo_code,
        discount_percentage=payload.discount_percentage,
        discount_amount=payload.discount_amount,
        start_date=payload.start_date,
        end_date=payload.end_date,
        is_active=True,
    )

    db.add(new_promo)
    db.commit()
    db.refresh(new_promo)
    return PromotionResponse.model_validate(new_promo)


@router.put(
    "/promotions/{id}",
    response_model=PromotionResponse,
    summary="Update an existing promotion",
)
def update_promotion(
    id: int,
    payload: PromotionUpdate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to update their own promotion.
    Enforces ownership through parent ArtisanListing.
    """
    promo = db.query(Promotion).filter(Promotion.id == id).first()
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion not found",
        )

    if promo.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this promotion",
        )

    # Validate showcase_item_id if updated
    update_data = payload.model_dump(exclude_unset=True)
    if "showcase_item_id" in update_data and update_data["showcase_item_id"] is not None:
        target_item_id = update_data["showcase_item_id"]
        item = db.query(ShowcaseItem).filter(ShowcaseItem.id == target_item_id).first()
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Showcase item not found",
            )
        if item.artisan_listing_id != promo.artisan_listing_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Showcase item does not belong to the specified artisan listing",
            )

    # Validate merged date range
    merged_start = update_data.get("start_date", promo.start_date)
    merged_end = update_data.get("end_date", promo.end_date)
    if merged_end < merged_start:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_date must be greater than or equal to start_date",
        )

    # Validate merged discount exclusivity
    merged_pct = update_data.get("discount_percentage", promo.discount_percentage)
    merged_amt = update_data.get("discount_amount", promo.discount_amount)
    if merged_pct is not None and merged_amt is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A promotion cannot have both discount_percentage and discount_amount simultaneously. Choose one discount type.",
        )

    for key, value in update_data.items():
        setattr(promo, key, value)

    promo.updated_at = func.sysutcdatetime()
    db.commit()
    db.refresh(promo)
    return PromotionResponse.model_validate(promo)


@router.delete(
    "/promotions/{id}",
    summary="Soft delete (deactivate) an artisan promotion",
)
def delete_promotion(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to soft delete their own promotion.
    """
    promo = db.query(Promotion).filter(Promotion.id == id).first()
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion not found",
        )

    if promo.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this promotion",
        )

    promo.is_active = False
    promo.updated_at = func.sysutcdatetime()
    db.commit()

    return {
        "message": "Promotion deactivated successfully",
        "id": id,
        "is_active": False,
    }
