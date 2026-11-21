"""
FastAPI Router for Module 03 — Traveler Activity Wishlist & Favorites (A14).
Integrates with CeylonGo Shared Infrastructure (dbo.wishlists, dbo.listing_references).

Requirements covered:
- A14: Traveler Activity Wishlist (Add, remove, check status, and list saved activities).
"""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_traveler
from app.core.listing_sync import sync_activity_listing_reference
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.shared import ListingReference, Review, Wishlist
from app.models.user import User
from app.schemas.activity_wishlist import (
    ActivityWishlistActionResponse,
    ActivityWishlistCheckResponse,
    ActivityWishlistItemResponse,
    ActivityWishlistPagination,
    ActivityWishlistSummary,
)

router = APIRouter(
    tags=["Activity Wishlists"],
)


def _resolve_activity_cover_url(images: list[ActivityImage]) -> str | None:
    if not images:
        return None
    for img in images:
        if img.is_cover:
            return img.image_url
    sorted_imgs = sorted(images, key=lambda x: (x.display_order, x.id))
    return sorted_imgs[0].image_url


def _get_activity_rating_stats(db: Session, listing_ref_id: int) -> tuple[float | None, int]:
    stats = (
        db.query(
            func.count(Review.id).label("total"),
            func.avg(Review.rating).label("avg_rating"),
        )
        .filter(
            Review.listing_reference_id == listing_ref_id,
            Review.is_published == True,
        )
        .first()
    )
    if stats and stats.total and stats.total > 0:
        return round(float(stats.avg_rating), 2), stats.total
    return None, 0


# ==============================================================================
# 1. Add Activity to Wishlist (A14)
# ==============================================================================


@router.post(
    "/api/v1/activities/{activity_id}/wishlist",
    response_model=ActivityWishlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an activity to traveler's personal wishlist",
)
@router.post(
    "/api/v1/wishlists/activities/{activity_id}",
    response_model=ActivityWishlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an activity to traveler's personal wishlist (alternate path)",
    include_in_schema=False,
)
def add_activity_to_wishlist(
    activity_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Allows an authenticated traveler to save an approved, active activity to their wishlist.

    Rules & Guards:
    1. Authenticated user must have 'traveler' role.
    2. Activity must exist and be in 'approved' status (404 otherwise).
    3. Activity must be active (400 otherwise).
    4. Duplicates return 409 Conflict.
    5. Saves to dbo.wishlists with foreign key to central dbo.listing_references.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if not activity or activity.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    if not activity.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This activity is currently not active",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == activity.id,
        )
        .first()
    )
    if not listing_ref:
        listing_ref = sync_activity_listing_reference(db, activity)
        if not listing_ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Activity cannot be saved at this time",
            )
        db.flush()

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
            detail="Activity is already in your wishlist",
        )

    entry = Wishlist(
        user_id=current_user.id,
        listing_reference_id=listing_ref.id,
    )
    db.add(entry)
    try:
        db.commit()
        db.refresh(entry)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Activity is already in your wishlist",
        ) from exc

    return ActivityWishlistActionResponse(
        message="Activity added to wishlist",
        activity_id=activity_id,
        saved=True,
        wishlist_id=entry.id,
    )


# ==============================================================================
# 2. Remove Activity from Wishlist (A14)
# ==============================================================================


@router.delete(
    "/api/v1/activities/{activity_id}/wishlist",
    response_model=ActivityWishlistActionResponse,
    summary="Remove an activity from traveler's personal wishlist",
)
@router.delete(
    "/api/v1/wishlists/activities/{activity_id}",
    response_model=ActivityWishlistActionResponse,
    summary="Remove an activity from traveler's personal wishlist (alternate path)",
    include_in_schema=False,
)
def remove_activity_from_wishlist(
    activity_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Removes an activity from the authenticated traveler's wishlist.
    Rejects with 404 if not found in the caller's wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == activity_id,
        )
        .first()
    )
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found in your wishlist",
        )

    entry = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found in your wishlist",
        )

    db.delete(entry)
    db.commit()

    return ActivityWishlistActionResponse(
        message="Activity removed from wishlist",
        activity_id=activity_id,
        saved=False,
        wishlist_id=None,
    )


# ==============================================================================
# 3. Check Wishlist Status (A14)
# ==============================================================================


@router.get(
    "/api/v1/activities/{activity_id}/wishlist/status",
    response_model=ActivityWishlistCheckResponse,
    summary="Check whether an activity is saved in traveler's wishlist",
)
@router.get(
    "/api/v1/wishlists/activities/{activity_id}/status",
    response_model=ActivityWishlistCheckResponse,
    summary="Check whether an activity is saved in traveler's wishlist (alternate path)",
    include_in_schema=False,
)
def check_activity_wishlist_status(
    activity_id: int,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns boolean indicating whether the specified activity is saved in the caller's wishlist.
    """
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == activity_id,
        )
        .first()
    )
    if not listing_ref:
        return ActivityWishlistCheckResponse(activity_id=activity_id, saved=False)

    saved = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == current_user.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
        is not None
    )
    return ActivityWishlistCheckResponse(activity_id=activity_id, saved=saved)


# ==============================================================================
# 4. List Traveler Saved Activities (A14)
# ==============================================================================


@router.get(
    "/api/v1/activities/wishlist/me",
    response_model=ActivityWishlistPagination,
    summary="List authenticated traveler's saved activities",
)
@router.get(
    "/api/v1/wishlists/activities",
    response_model=ActivityWishlistPagination,
    summary="List authenticated traveler's saved activities (alternate path)",
    include_in_schema=False,
)
def list_my_activity_wishlist(
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    available_only: bool = Query(
        default=False,
        description="Filter only currently approved and active activities",
    ),
):
    """
    Returns a paginated list of activities saved in the authenticated traveler's wishlist.
    Ordered by most recently added.
    """
    query = (
        db.query(Wishlist, Activity, ListingReference)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .outerjoin(Activity, ListingReference.target_id == Activity.id)
        .filter(
            Wishlist.user_id == current_user.id,
            ListingReference.module_type == "activity",
        )
    )

    if available_only:
        query = query.filter(
            Activity.id != None,
            Activity.approval_status == "approved",
            Activity.is_active == True,
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    results = (
        query.order_by(Wishlist.created_at.desc(), Wishlist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for w, act, ref in results:
        is_available = bool(
            act and act.is_active and act.approval_status == "approved"
        )
        act_summary = None
        if act and is_available:
            # Query images and ratings for summary
            images = (
                db.query(ActivityImage)
                .filter(ActivityImage.activity_id == act.id)
                .all()
            )
            cover_url = _resolve_activity_cover_url(images)
            avg_rating, total_reviews = _get_activity_rating_stats(db, ref.id)

            act_summary = ActivityWishlistSummary(
                id=act.id,
                title=act.title,
                category=act.category,
                district=act.district,
                city=act.city,
                price=act.price,
                currency=act.currency or "LKR",
                duration_minutes=act.duration_minutes,
                minimum_age=act.minimum_age,
                cover_image_url=cover_url,
                average_rating=avg_rating,
                total_reviews=total_reviews,
            )

        items.append(
            ActivityWishlistItemResponse(
                wishlist_id=w.id,
                saved_at=w.created_at,
                is_available=is_available,
                activity=act_summary,
            )
        )

    return ActivityWishlistPagination(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
