"""
Artisan Wishlist & Favorites API (Phase P1 Step 11).
Integrates with CeylonGo Shared Infrastructure (dbo.wishlists, dbo.listing_references).

Contract Guarantees:
1. Traveler Authentication: Only authenticated users with the 'traveler' role can add, delete, or inspect wishlists.
2. User Isolation: All operations resolve user_id strictly from the verified JWT token (current_user.id).
3. Centralized Shared Storage: Reuses dbo.wishlists and dbo.listing_references (Migration 027).
4. Public Approval Guard:
   - Unapproved (pending, rejected, suspended) businesses cannot be added (HTTP 404 Not Found).
   - Inactive businesses cannot be added (HTTP 400 Bad Request).
   - Businesses that become unapproved/inactive after being saved are marked is_available=False with listing=None.
5. Duplicate Prevention: Unique constraint UQ_wishlists_user_listing prevents duplicate saves (HTTP 409 Conflict).
6. Deletion Guard: Travelers can only delete items from their own wishlist (HTTP 404 Not Found otherwise).
7. Product favorites use dedicated storage introduced by migration 038.
"""

from __future__ import annotations

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import delete, func, select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import require_traveler
from app.core.publication import public_artisan
from app.database.connection import get_db
from app.models.artisan import ArtisanProductFavorite
from app.models.artisan_listing import ArtisanListing
from app.models.shared import (
    ListingReference,
    Review,
    Wishlist,
)
from app.models.showcase_item import ShowcaseItem
from app.models.user import User
from app.schemas.artisan_favorites import ArtisanFavoritePage, ArtisanFavoriteResponse
from app.schemas.artisan_wishlist import (
    ArtisanProductFavoriteCompatibilityResponse,
    ArtisanWishlistCheckResponse,
    ArtisanWishlistCreate,
    ArtisanWishlistItemResponse,
    ArtisanWishlistPagination,
    ArtisanWishlistSavedListingSummary,
)
from app.schemas.showcase_item import ShowcaseItemSummary
from app.services.artisan_shared_reference import (
    ARTISAN_MODULE_TYPE,
    sync_artisan_listing_reference,
)

router = APIRouter(tags=["Artisan Wishlists"])


def _get_listing_rating_stats(db: Session, listing_id: int) -> tuple[float | None, int]:
    """Helper to compute average rating and total review count for an artisan listing."""
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing_id,
        )
        .first()
    )
    if not listing_ref:
        return None, 0

    stats = (
        db.query(
            func.count(Review.id).label("total"),
            func.avg(Review.rating).label("avg_rating"),
        )
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
        .first()
    )
    if stats and stats.total and stats.total > 0:
        return round(float(stats.avg_rating), 2), stats.total
    return None, 0


def _build_saved_listing_summary(
    db: Session,
    listing: ArtisanListing,
) -> ArtisanWishlistSavedListingSummary:
    """Helper to convert ArtisanListing ORM model to rich wishlist summary."""
    avg_rating, total_reviews = _get_listing_rating_stats(db, listing.id)
    return ArtisanWishlistSavedListingSummary(
        id=listing.id,
        provider_profile_id=listing.provider_profile_id,
        title=listing.title,
        craft_category=listing.craft_category,
        district=listing.district,
        city=listing.city,
        cover_image_url=listing.cover_image_url,
        approval_status=listing.approval_status,
        is_active=listing.is_active,
        average_rating=avg_rating,
        total_reviews=total_reviews,
    )


# ==============================================================================
# 1. Add Artisan Business to Wishlist
# ==============================================================================

@router.post(
    "/api/v1/artisan-listings/{listing_id}/wishlist",
    response_model=ArtisanWishlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an artisan business to traveler's personal wishlist",
)
@router.post(
    "/api/v1/wishlists/artisan/{listing_id}",
    response_model=ArtisanWishlistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an artisan business to traveler's personal wishlist (alternate path)",
    include_in_schema=False,
)
def add_artisan_listing_to_wishlist(
    listing_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    payload: ArtisanWishlistCreate | None = None,
):
    """
    Protected endpoint: Allows an authenticated traveler to save an approved, active
    Artisan Business to their personal wishlist.

    SECURITY & INTEGRITY RULES:
    1. Authenticated caller must have the 'traveler' role (403 for providers/admins).
    2. Listing must exist and be in 'approved' status (404 to avoid leaking unapproved listings).
    3. Listing must be active (inactive listings return 400 Bad Request).
    4. Duplicates return 409 Conflict.
    5. Database unique constraint prevents race conditions under concurrent calls.
    """
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == listing_id)
        .first()
    )
    if not listing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    # Never leak unapproved listings
    if listing.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    # Inactive listings cannot be newly added
    if not listing.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This artisan business is currently inactive",
        )

    # Synchronize shared ListingReference
    listing_ref = sync_artisan_listing_reference(db, listing)

    # Check for existing duplicate before insert
    existing = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Artisan business is already in your wishlist",
        )

    wishlist_item = Wishlist(
        user_id=current_user.id,
        listing_reference_id=listing_ref.id,
        notes=payload.notes if payload else None,
    )
    db.add(wishlist_item)
    try:
        db.commit()
        db.refresh(wishlist_item)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Artisan business is already in your wishlist",
        ) from exc

    listing_summary = _build_saved_listing_summary(db, listing)

    return ArtisanWishlistItemResponse(
        id=wishlist_item.id,
        user_id=wishlist_item.user_id,
        listing_reference_id=wishlist_item.listing_reference_id,
        artisan_listing_id=listing.id,
        notes=wishlist_item.notes,
        created_at=wishlist_item.created_at,
        is_available=True,
        listing=listing_summary,
    )


# ==============================================================================
# 2. Remove Artisan Business from Wishlist
# ==============================================================================

@router.delete(
    "/api/v1/artisan-listings/{listing_id}/wishlist",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an artisan business from traveler's personal wishlist",
)
@router.delete(
    "/api/v1/wishlists/artisan/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an artisan business from traveler's personal wishlist (alternate path)",
    include_in_schema=False,
)
def remove_artisan_listing_from_wishlist(
    listing_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Removes an artisan business from the authenticated traveler's wishlist.
    Rejects requests with HTTP 404 if the item is not currently saved in the caller's wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing_id,
        )
        .first()
    )
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan business not found in your wishlist",
        )

    item = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan business not found in your wishlist",
        )

    db.delete(item)
    db.commit()
    return None


# ==============================================================================
# 3. Check Whether an Artisan Business is Saved
# ==============================================================================

@router.get(
    "/api/v1/artisan-listings/{listing_id}/wishlist/check",
    response_model=ArtisanWishlistCheckResponse,
    summary="Check whether an artisan business is saved in traveler's wishlist",
)
@router.get(
    "/api/v1/wishlists/artisan/{listing_id}/check",
    response_model=ArtisanWishlistCheckResponse,
    summary="Check whether an artisan business is saved in traveler's wishlist (alternate path)",
    include_in_schema=False,
)
def check_artisan_listing_wishlist_status(
    listing_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Protected endpoint: Returns boolean is_saved indicating whether the artisan business
    is currently saved in the caller's wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing_id,
        )
        .first()
    )
    if not listing_ref:
        return ArtisanWishlistCheckResponse(
            listing_id=listing_id,
            is_saved=False,
            wishlist_id=None,
        )

    item = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    return ArtisanWishlistCheckResponse(
        listing_id=listing_id,
        is_saved=bool(item),
        wishlist_id=item.id if item else None,
    )


# ==============================================================================
# 4. List Authenticated Traveler's Saved Artisan Businesses
# ==============================================================================

@router.get(
    "/api/v1/artisan-wishlists/me",
    response_model=ArtisanWishlistPagination,
    summary="List authenticated traveler's saved artisan businesses",
)
@router.get(
    "/api/v1/artisan-listings/me/wishlist",
    response_model=ArtisanWishlistPagination,
    summary="List authenticated traveler's saved artisan businesses (alias 1)",
    include_in_schema=False,
)
@router.get(
    "/api/v1/wishlists/artisan/me",
    response_model=ArtisanWishlistPagination,
    summary="List authenticated traveler's saved artisan businesses (alias 2)",
    include_in_schema=False,
)
def list_my_artisan_wishlist(
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    available_only: bool = Query(
        default=False,
        description="Filter only currently approved and active artisan businesses",
    ),
):
    """
    Protected endpoint: Returns a paginated list of the authenticated traveler's saved
    artisan businesses ordered by most recently added.

    SAFETY & PRIVACY RULES:
    1. Only the authenticated traveler's own items are returned (strict cross-user isolation).
    2. If a saved business was subsequently deactivated or rejected by an admin:
       - If available_only=True: Excluded from results and total count.
       - If available_only=False: Included with is_available=False and listing=None,
         allowing the client UI to handle unavailable items gracefully without crashes.
    """
    query = (
        db.query(Wishlist, ListingReference)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .filter(
            Wishlist.user_id == current_user.id,
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
        )
    )

    if available_only:
        query = query.join(
            ArtisanListing, ListingReference.target_id == ArtisanListing.id
        ).filter(
            ArtisanListing.approval_status == "approved",
            ArtisanListing.is_active == True,
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    entries = (
        query.order_by(Wishlist.created_at.desc(), Wishlist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for wish, ref in entries:
        listing = (
            db.query(ArtisanListing)
            .filter(ArtisanListing.id == ref.target_id)
            .first()
        )
        is_available = bool(
            listing and listing.is_active and listing.approval_status == "approved"
        )
        listing_summary = None
        if listing and is_available:
            listing_summary = _build_saved_listing_summary(db, listing)

        items.append(
            ArtisanWishlistItemResponse(
                id=wish.id,
                user_id=wish.user_id,
                listing_reference_id=wish.listing_reference_id,
                artisan_listing_id=ref.target_id,
                notes=wish.notes,
                created_at=wish.created_at,
                is_available=is_available,
                listing=listing_summary,
            )
        )

    return ArtisanWishlistPagination(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==============================================================================
# 5. Product favorites
# ==============================================================================

FavoriteTraveler = Annotated[User, Depends(require_traveler)]
FavoriteDB = Annotated[Session, Depends(get_db)]
ProductID = Annotated[int, Path(gt=0, le=2147483647)]


def public_products():
    return (
        select(ShowcaseItem)
        .join(ArtisanListing, ArtisanListing.id == ShowcaseItem.artisan_listing_id)
        .where(
            public_artisan(),
            ShowcaseItem.is_active == true(),
            ShowcaseItem.approval_status == "approved",
            ShowcaseItem.item_type == "product",
        )
    )


def favorite_response(favorite, product):
    return ArtisanFavoriteResponse(
        product_id=favorite.product_id,
        created_at=favorite.created_at,
        product=ShowcaseItemSummary.model_validate(product),
    )


@router.get(
    "/api/v1/artisan-wishlists/product-favorites-compatibility",
    response_model=ArtisanProductFavoriteCompatibilityResponse,
)
def get_product_favorites_compatibility():
    return ArtisanProductFavoriteCompatibilityResponse(
        supported=True,
        reason="Product favorites use dedicated storage introduced by migration 038.",
        recommended_alternative="Business wishlists remain available for saving shops.",
    )


@router.put("/api/v1/artisan-product-favorites/{product_id}")
@router.post("/api/v1/artisan-products/{product_id}/favorite", include_in_schema=False)
@router.post("/api/v1/wishlists/artisan/products/{product_id}", include_in_schema=False)
def add_product_favorite(
    product_id: ProductID, current_user: FavoriteTraveler, db: FavoriteDB
) -> ArtisanFavoriteResponse:
    product = db.scalar(public_products().where(ShowcaseItem.id == product_id))
    if product is None:
        raise HTTPException(404, "Public artisan product not found")
    lookup = select(ArtisanProductFavorite).where(
        ArtisanProductFavorite.user_id == current_user.id,
        ArtisanProductFavorite.product_id == product_id,
    )
    favorite = db.scalar(lookup)
    if favorite is None:
        favorite = ArtisanProductFavorite(user_id=current_user.id, product_id=product_id)
        db.add(favorite)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            favorite = db.scalar(lookup)
            if favorite is None:
                raise
            product = db.scalar(public_products().where(ShowcaseItem.id == product_id))
            if product is None:
                raise HTTPException(404, "Public artisan product not found")
    return favorite_response(favorite, product)


@router.delete("/api/v1/artisan-product-favorites/{product_id}", status_code=204)
def remove_product_favorite(
    product_id: ProductID, current_user: FavoriteTraveler, db: FavoriteDB
) -> None:
    db.execute(
        delete(ArtisanProductFavorite).where(
            ArtisanProductFavorite.user_id == current_user.id,
            ArtisanProductFavorite.product_id == product_id,
        )
    )
    db.commit()


@router.get("/api/v1/artisan-product-favorites")
def list_product_favorites(
    current_user: FavoriteTraveler,
    db: FavoriteDB,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ArtisanFavoritePage:
    query = (
        public_products()
        .add_columns(ArtisanProductFavorite)
        .join(ArtisanProductFavorite, ArtisanProductFavorite.product_id == ShowcaseItem.id)
        .where(ArtisanProductFavorite.user_id == current_user.id)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(ArtisanProductFavorite.created_at.desc(), ArtisanProductFavorite.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return ArtisanFavoritePage(
        items=[favorite_response(favorite, product) for product, favorite in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
