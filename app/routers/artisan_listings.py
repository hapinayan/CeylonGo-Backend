import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.dependencies import (
    require_admin,
    require_approved_provider,
)
from app.database.connection import get_db
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.artisan_listing import (
    VALID_CRAFT_CATEGORIES,
    ArtisanListingCreate,
    ArtisanListingPagination,
    ArtisanListingResponse,
    ArtisanListingStatusUpdate,
    ArtisanListingSummary,
    ArtisanListingUpdate,
)

router = APIRouter(prefix="/api/v1/artisan-listings", tags=["Artisan Listings"])


@router.get("", response_model=ArtisanListingPagination, summary="Browse active artisan listings")
def list_artisan_listings(
    db: Annotated[Session, Depends(get_db)],
    search: str | None = Query(default=None, max_length=100, description="Search in title, description, or city"),
    district: str | None = Query(default=None, max_length=100, description="Filter by district"),
    category: str | None = Query(default=None, max_length=50, description="Filter by craft category"),
    is_featured: bool | None = Query(default=None, description="Filter by featured status"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=12, ge=1, le=100, description="Items per page"),
):
    """
    Public discovery endpoint.
    Retrieves active artisan listings with search, category, district, and featured filters.
    """
    query = db.query(ArtisanListing).filter(ArtisanListing.is_active == True)

    if search and search.strip():
        search_pattern = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(ArtisanListing.title).like(search_pattern),
                func.lower(ArtisanListing.description).like(search_pattern),
                func.lower(ArtisanListing.city).like(search_pattern),
            )
        )

    if district and district.strip():
        query = query.filter(func.lower(ArtisanListing.district) == district.strip().lower())

    if category and category.strip():
        query = query.filter(func.lower(ArtisanListing.craft_category) == category.strip().lower())

    if is_featured is not None:
        query = query.filter(ArtisanListing.is_featured == is_featured)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(ArtisanListing.is_featured.desc(), ArtisanListing.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ArtisanListingPagination(
        items=[ArtisanListingSummary.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/categories", response_model=list[str], summary="Get list of available craft categories")
def get_craft_categories(db: Annotated[Session, Depends(get_db)]):
    """
    Returns standard and active craft categories for filter dropdowns.
    """
    db_categories = (
        db.query(ArtisanListing.craft_category)
        .filter(ArtisanListing.is_active == True)
        .distinct()
        .all()
    )
    found = {c[0] for c in db_categories if c[0]}
    all_categories = sorted(found.union(set(VALID_CRAFT_CATEGORIES)))
    return all_categories


@router.get("/districts", response_model=list[str], summary="Get list of districts with active artisan listings")
def get_active_districts(db: Annotated[Session, Depends(get_db)]):
    """
    Returns unique districts that currently have active artisan listings.
    """
    results = (
        db.query(ArtisanListing.district)
        .filter(ArtisanListing.is_active == True)
        .distinct()
        .order_by(ArtisanListing.district)
        .all()
    )
    return [r[0] for r in results if r[0]]


@router.get("/me/listings", response_model=list[ArtisanListingResponse], summary="Get authenticated artisan's own listings")
def get_my_artisan_listings(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns all listings created by the authenticated approved provider
    (including both active and inactive listings for management).
    """
    listings = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
        .order_by(ArtisanListing.created_at.desc())
        .all()
    )
    return [ArtisanListingResponse.model_validate(item) for item in listings]


@router.get("/{id}", response_model=ArtisanListingResponse, summary="Get public details of an artisan listing")
def get_artisan_listing(
    id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public endpoint: Returns full details of an active artisan listing.
    """
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == id, ArtisanListing.is_active == True)
        .first()
    )
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )
    return ArtisanListingResponse.model_validate(listing)


@router.post("", response_model=ArtisanListingResponse, status_code=status.HTTP_201_CREATED, summary="Create a new artisan listing")
def create_artisan_listing(
    payload: ArtisanListingCreate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan service provider to create a new artisan listing.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can create artisan listings",
        )

    new_listing = ArtisanListing(
        provider_profile_id=provider_profile.id,
        title=payload.title,
        craft_category=payload.craft_category,
        description=payload.description,
        district=payload.district,
        city=payload.city,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        phone=payload.phone or provider_profile.phone,
        email=payload.email,
        website_url=payload.website_url,
        opening_hours=payload.opening_hours,
        cover_image_url=payload.cover_image_url,
        is_active=True,
        is_verified=False,
        is_featured=False,
    )

    db.add(new_listing)
    db.commit()
    db.refresh(new_listing)
    return ArtisanListingResponse.model_validate(new_listing)


@router.put("/{id}", response_model=ArtisanListingResponse, summary="Update an existing artisan listing")
def update_artisan_listing(
    id: int,
    payload: ArtisanListingUpdate,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to update their own listing.
    Enforces ownership verification.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this listing",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(listing, key, value)

    db.commit()
    db.refresh(listing)
    return ArtisanListingResponse.model_validate(listing)


@router.delete("/{id}", summary="Soft delete (deactivate) an artisan listing")
def delete_artisan_listing(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Allows an approved artisan to soft delete (deactivate) their own listing.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    if listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this listing",
        )

    listing.is_active = False
    db.commit()

    return {
        "message": "Artisan listing deactivated successfully",
        "id": id,
        "is_active": False,
    }


@router.patch("/{id}/status", response_model=ArtisanListingResponse, summary="Admin verification and feature toggle")
def update_listing_status(
    id: int,
    payload: ArtisanListingStatusUpdate,
    admin_user: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Admin-only endpoint: Verify, feature, or toggle listing status.
    """
    listing = db.query(ArtisanListing).filter(ArtisanListing.id == id).first()
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(listing, key, value)

    db.commit()
    db.refresh(listing)
    return ArtisanListingResponse.model_validate(listing)
