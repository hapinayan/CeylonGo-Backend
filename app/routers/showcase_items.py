import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.publication import public_artisan
from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.schemas.showcase_item import (
    ShowcaseItemCreate,
    ShowcaseItemPagination,
    ShowcaseItemResponse,
    ShowcaseItemSummary,
    ShowcaseItemUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["Showcase Items"])


# ==============================================================================
# Public Endpoints
# ==============================================================================

@router.get(
    "/showcase-items",
    response_model=ShowcaseItemPagination,
    summary="Browse active showcase products and services",
)
def list_showcase_items(
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(default=None, max_length=100, description="Search in title, description, or materials"),
    item_type: str | None = Query(default=None, description="Filter by 'product' or 'service'"),
    is_featured: bool | None = Query(default=None, description="Filter by featured flag"),
    artisan_listing_id: int | None = Query(default=None, description="Filter by specific artisan listing ID"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=12, ge=1, le=100, description="Items per page"),
):
    """
    Public discovery endpoint.
    Retrieves active and approved showcase items across active and approved artisan listings with search, type, and listing filters.
    """
    query = (
        db.query(ShowcaseItem)
        .join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)
        .filter(
            ShowcaseItem.is_active == True,
            public_artisan(),
            ShowcaseItem.approval_status == "approved",
        )
    )

    if search and search.strip():
        search_pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(ShowcaseItem.title).like(search_pattern),
                func.lower(ShowcaseItem.description).like(search_pattern),
                func.lower(ShowcaseItem.materials).like(search_pattern),
            )
        )

    if item_type and item_type.strip():
        query = query.filter(ShowcaseItem.item_type == item_type.strip().lower())

    if is_featured is not None:
        query = query.filter(ShowcaseItem.is_featured == is_featured)

    if artisan_listing_id is not None:
        query = query.filter(ShowcaseItem.artisan_listing_id == artisan_listing_id)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(ShowcaseItem.is_featured.desc(), ShowcaseItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ShowcaseItemPagination(
        items=[ShowcaseItemSummary.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/showcase-items/me/items",
    response_model=list[ShowcaseItemResponse],
    summary="Get authenticated artisan's showcase items",
)
def get_my_showcase_items(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns all showcase products and services (active and inactive)
    created under any artisan listing owned by the authenticated approved provider.
    """
    items = (
        db.query(ShowcaseItem)
        .join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .order_by(ShowcaseItem.created_at.desc())
        .all()
    )
    return [ShowcaseItemResponse.model_validate(item) for item in items]


@router.get(
    "/showcase-items/{id}",
    response_model=ShowcaseItemResponse,
    summary="View details of a showcase product or service",
)
def get_showcase_item(
    id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public endpoint: Returns full details of an active, approved showcase item under an active, approved listing.
    """
    item = (
        db.query(ShowcaseItem)
        .join(ArtisanListing, ShowcaseItem.artisan_listing_id == ArtisanListing.id)
        .filter(
            ShowcaseItem.id == id,
            ShowcaseItem.is_active == True,
            public_artisan(),
            ShowcaseItem.approval_status == "approved",
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase item not found",
        )
    return ShowcaseItemResponse.model_validate(item)


@router.get(
    "/artisan-listings/{listing_id}/showcase-items",
    response_model=ShowcaseItemPagination,
    summary="View all showcase products and services of an artisan listing",
)
def get_listing_showcase_items(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
    item_type: str | None = Query(default=None, description="Filter by 'product' or 'service'"),
    is_featured: bool | None = Query(default=None, description="Filter by featured status"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=12, ge=1, le=100, description="Items per page"),
):
    """
    Public endpoint: Returns paginated active, approved showcase items for a specific active, approved artisan listing.
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

    query = db.query(ShowcaseItem).filter(
        ShowcaseItem.artisan_listing_id == listing_id,
        ShowcaseItem.is_active == True,
        ShowcaseItem.approval_status == "approved",
    )

    if item_type and item_type.strip():
        query = query.filter(ShowcaseItem.item_type == item_type.strip().lower())

    if is_featured is not None:
        query = query.filter(ShowcaseItem.is_featured == is_featured)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(ShowcaseItem.is_featured.desc(), ShowcaseItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ShowcaseItemPagination(
        items=[ShowcaseItemSummary.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==============================================================================
# Protected Endpoints (Owner Only)
# ==============================================================================

@router.post(
    "/showcase-items",
    response_model=ShowcaseItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new showcase product or service",
)
def create_showcase_item(
    payload: ShowcaseItemCreate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan provider to create a showcase item
    under an artisan listing they own.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can create showcase items",
        )

    listing = db.query(ArtisanListing).filter(ArtisanListing.id == payload.artisan_listing_id).first()
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

    new_item = ShowcaseItem(
        artisan_listing_id=payload.artisan_listing_id,
        title=payload.title,
        item_type=payload.item_type,
        description=payload.description,
        price=payload.price,
        currency=payload.currency,
        image_url=payload.image_url,
        materials=payload.materials,
        duration_minutes=payload.duration_minutes,
        is_available=payload.is_available,
        stock_quantity=payload.stock_quantity,
        sku=payload.sku,
        is_custom_order_available=payload.is_custom_order_available,
        approval_status="pending",
        is_featured=False,
        is_active=True,
    )

    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return ShowcaseItemResponse.model_validate(new_item)


@router.put(
    "/showcase-items/{id}",
    response_model=ShowcaseItemResponse,
    summary="Update an existing showcase product or service",
)
def update_showcase_item(
    id: int,
    payload: ShowcaseItemUpdate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to update their own showcase item.
    Enforces ownership through parent ArtisanListing.
    """
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase item not found",
        )

    if item.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this showcase item",
        )

    update_data = payload.model_dump(exclude_unset=True)
    # Providers cannot self-modify approval_status
    update_data.pop("approval_status", None)
    if "stock_quantity" in update_data:
        lock_booking_resource(db, f"artisan_stock_{item.id}")
        db.refresh(item)
        if item.artisan_listing.provider_profile_id != provider_profile.id:
            raise HTTPException(403, "You do not own this showcase item")

    for key, value in update_data.items():
        setattr(item, key, value)

    item.updated_at = func.sysutcdatetime()
    db.commit()
    db.refresh(item)
    return ShowcaseItemResponse.model_validate(item)


@router.delete(
    "/showcase-items/{id}",
    summary="Soft delete (deactivate) a showcase product or service",
)
def delete_showcase_item(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to soft delete (deactivate) their own showcase item.
    """
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase item not found",
        )

    if item.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this showcase item",
        )

    item.is_active = False
    item.updated_at = func.sysutcdatetime()
    db.commit()

    return {
        "message": "Showcase item deactivated successfully",
        "id": id,
        "is_active": False,
    }
