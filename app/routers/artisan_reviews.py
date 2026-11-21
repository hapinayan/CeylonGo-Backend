"""
Artisan Reviews & Ratings API (Phase P1 Step 10).
Integrates with CeylonGo Shared Infrastructure (dbo.reviews, dbo.listing_references, dbo.booking_references).

Security & Verification Guarantees:
1. Traveler Authentication: Verified booking reviews require an authenticated traveler.
2. Provider Self-Review Prevention: Providers cannot review their own businesses.
3. Strict Reservation Verification:
   - Traveler must own the reservation (reservation.traveler_id == current_user.id).
   - Reservation must match the artisan listing.
   - Reservation status must be 'completed' (in-store pickup and payment confirmed).
   - Duplicate reviews for the same completed reservation are strictly rejected (HTTP 409).
4. Public Approval Filtering:
   - Reviews for unapproved (pending, rejected, suspended) or inactive listings are hidden (HTTP 404).
   - Only published reviews (is_published == True) are visible on public discovery endpoints.
5. Rating Range & Aggregation:
   - Ratings are strictly bounded 1.00 <= rating <= 5.00 (enforced by Pydantic and CK_reviews_rating).
   - Public summary endpoint computes rounded average rating, total count, and 5-star distribution breakdown.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    ListingReference,
    Review,
)
from app.models.user import User
from app.schemas.artisan_review import (
    ArtisanDirectReviewCreate,
    ArtisanReviewCreate,
    ArtisanReviewPaginationResponse,
    ArtisanReviewResponse,
    ArtisanReviewSummaryResponse,
    ArtisanReviewUpdate,
)
from app.services.artisan_shared_reference import (
    ARTISAN_MODULE_TYPE,
    sync_artisan_booking_reference,
    sync_artisan_listing_reference,
)

router = APIRouter(tags=["Artisan Reviews"])


def _format_review_response(
    review: Review,
    listing: ArtisanListing | None = None,
    booking_ref: BookingReference | None = None,
) -> ArtisanReviewResponse:
    """Helper to convert Review ORM into ArtisanReviewResponse with populated context."""
    user_name = review.user.full_name if review.user else None
    artisan_listing_id = listing.id if listing else None
    artisan_listing_title = listing.title if listing else None
    booking_code = None

    if booking_ref:
        booking_code = booking_ref.booking_reference
    elif review.booking_reference:
        booking_code = review.booking_reference.booking_reference

    if not artisan_listing_id and review.listing_reference:
        artisan_listing_id = review.listing_reference.target_id
        artisan_listing_title = review.listing_reference.title

    return ArtisanReviewResponse(
        id=review.id,
        listing_reference_id=review.listing_reference_id,
        booking_reference_id=review.booking_reference_id,
        user_id=review.user_id,
        user_name=user_name,
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        is_verified_booking=review.is_verified_booking,
        is_published=review.is_published,
        created_at=review.created_at,
        updated_at=review.updated_at,
        artisan_listing_id=artisan_listing_id,
        artisan_listing_title=artisan_listing_title,
        booking_reference_code=booking_code,
    )


def _compute_rating_breakdown(reviews: list[Review]) -> dict[str, int]:
    """Computes distribution across 5, 4, 3, 2, 1 star tiers."""
    breakdown = {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}
    for r in reviews:
        val = float(r.rating)
        if val >= 4.5:
            breakdown["5"] += 1
        elif val >= 3.5:
            breakdown["4"] += 1
        elif val >= 2.5:
            breakdown["3"] += 1
        elif val >= 1.5:
            breakdown["2"] += 1
        else:
            breakdown["1"] += 1
    return breakdown


# ==============================================================================
# 1. Create Review for Specific Listing (POST /api/v1/artisan-listings/{id}/reviews)
# ==============================================================================

@router.post(
    "/api/v1/artisan-listings/{listing_id}/reviews",
    response_model=ArtisanReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a verified traveler review for an artisan business",
)
def create_artisan_listing_review(
    listing_id: int,
    payload: ArtisanReviewCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Submits a traveler review for an active, approved Artisan Business.

    Eligibility Rules:
    1. Authenticated traveler must own the specified reservation.
    2. Reservation must belong to the specified artisan listing.
    3. Reservation status must be 'completed' (in-store pickup finalized).
    4. Providers cannot review their own businesses.
    5. Duplicate reviews for the same reservation are rejected with HTTP 409 Conflict.
    6. Listing must be approved and active; unapproved listings return HTTP 404.
    """
    # 1. Validate listing exists, is active, and is approved
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == listing_id)
        .first()
    )
    if not listing or not listing.is_active or listing.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found or not eligible for public reviews",
        )

    # 2. Prevent provider from reviewing their own business
    provider_profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )
    if provider_profile and listing.provider_profile_id == provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Providers cannot review their own businesses",
        )

    # 3. Retrieve and validate reservation
    reservation = (
        db.query(ArtisanReservation)
        .filter(ArtisanReservation.id == payload.reservation_id)
        .first()
    )
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reservation with ID {payload.reservation_id} not found",
        )

    # 4. Check reservation ownership (traveler authorization)
    if reservation.traveler_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only submit reviews for reservations you personally booked and completed",
        )

    # 5. Check reservation belongs to this listing
    if reservation.artisan_listing_id != listing.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reservation does not belong to this artisan business",
        )

    # 6. Check reservation completion status
    if reservation.reservation_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Only completed reservations with confirmed in-store pickup can be reviewed. "
                f"Current status is '{reservation.reservation_status}'."
            ),
        )

    # 7. Synchronize shared references
    listing_ref = sync_artisan_listing_reference(db, listing)
    booking_ref = sync_artisan_booking_reference(db, reservation, changed_by=current_user.id)

    # 8. Check for duplicate review
    existing_review = (
        db.query(Review)
        .filter(Review.booking_reference_id == booking_ref.id)
        .first()
    )
    if existing_review:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A review has already been submitted for this reservation",
        )

    # 9. Create and persist review
    new_review = Review(
        listing_reference_id=listing_ref.id,
        booking_reference_id=booking_ref.id,
        user_id=current_user.id,
        rating=payload.rating,
        title=payload.title,
        comment=payload.comment,
        is_verified_booking=True,
        is_published=True,
    )
    db.add(new_review)
    db.commit()
    db.refresh(new_review)

    return _format_review_response(new_review, listing=listing, booking_ref=booking_ref)


# ==============================================================================
# 2. Direct Review Submission (POST /api/v1/artisan-reviews)
# ==============================================================================

@router.post(
    "/api/v1/artisan-reviews",
    response_model=ArtisanReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Directly submit a verified artisan review by reservation ID",
)
def create_artisan_direct_review(
    payload: ArtisanDirectReviewCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Convenience endpoint for submitting a review by reservation ID.
    If listing_id is omitted, it is automatically resolved from the reservation.
    """
    # 1. Retrieve reservation
    reservation = (
        db.query(ArtisanReservation)
        .filter(ArtisanReservation.id == payload.reservation_id)
        .first()
    )
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reservation with ID {payload.reservation_id} not found",
        )

    target_listing_id = payload.listing_id or reservation.artisan_listing_id

    review_create_payload = ArtisanReviewCreate(
        reservation_id=payload.reservation_id,
        rating=payload.rating,
        title=payload.title,
        comment=payload.comment,
    )

    return create_artisan_listing_review(
        listing_id=target_listing_id,
        payload=review_create_payload,
        current_user=current_user,
        db=db,
    )


# ==============================================================================
# 3. Public Review Listing (GET /api/v1/artisan-listings/{id}/reviews)
# ==============================================================================

@router.get(
    "/api/v1/artisan-listings/{listing_id}/reviews",
    response_model=ArtisanReviewPaginationResponse,
    summary="Get public reviews for an approved artisan business",
)
@router.get(
    "/api/v1/artisan-reviews/listing/{listing_id}",
    response_model=ArtisanReviewPaginationResponse,
    summary="Get public reviews for an approved artisan business (alias)",
)
def get_artisan_listing_reviews(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=10, ge=1, le=50, description="Items per page"),
    rating: float | None = Query(default=None, ge=1.0, le=5.0, description="Filter by minimum star rating"),
    sort_by: str = Query(
        default="newest",
        description="Sort order: newest, oldest, highest_rating, lowest_rating",
    ),
):
    """
    Public endpoint: Retrieves published reviews for an approved, active Artisan Business.
    Returns HTTP 404 for unapproved, pending, or suspended listings.
    """
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == listing_id)
        .first()
    )
    if not listing or not listing.is_active or listing.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing.id,
        )
        .first()
    )

    if not listing_ref:
        return ArtisanReviewPaginationResponse(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
            total_pages=0,
            average_rating=0.0,
            total_reviews=0,
        )

    base_query = (
        db.query(Review)
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
    )

    # Compute overall stats for the listing
    total_reviews = base_query.count()
    avg_rating_val = (
        db.query(func.avg(Review.rating))
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
        .scalar()
    )
    average_rating = round(float(avg_rating_val), 2) if total_reviews > 0 and avg_rating_val is not None else 0.0

    # Filter by rating if requested
    query = base_query
    if rating is not None:
        query = query.filter(Review.rating >= Decimal(str(rating)))

    # Sort
    if sort_by == "oldest":
        query = query.order_by(Review.created_at.asc())
    elif sort_by == "highest_rating":
        query = query.order_by(Review.rating.desc(), Review.created_at.desc())
    elif sort_by == "lowest_rating":
        query = query.order_by(Review.rating.asc(), Review.created_at.desc())
    else:  # newest
        query = query.order_by(Review.created_at.desc())

    filtered_total = query.count()
    total_pages = math.ceil(filtered_total / page_size) if filtered_total > 0 else 0

    reviews = (
        query.offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [_format_review_response(r, listing=listing) for r in reviews]

    return ArtisanReviewPaginationResponse(
        items=items,
        total=filtered_total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        average_rating=average_rating,
        total_reviews=total_reviews,
    )


# ==============================================================================
# 4. Public Review Summary & Rating Breakdown (GET /api/v1/artisan-listings/{id}/reviews/summary)
# ==============================================================================

@router.get(
    "/api/v1/artisan-listings/{listing_id}/reviews/summary",
    response_model=ArtisanReviewSummaryResponse,
    summary="Get aggregated rating summary and star breakdown for an artisan business",
)
@router.get(
    "/api/v1/artisan-reviews/listing/{listing_id}/summary",
    response_model=ArtisanReviewSummaryResponse,
    summary="Get aggregated rating summary and star breakdown for an artisan business (alias)",
)
def get_artisan_listing_reviews_summary(
    listing_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Public endpoint: Returns average star rating, total review count, and star distribution
    (5-star through 1-star counts) for an approved artisan business.
    """
    listing = (
        db.query(ArtisanListing)
        .filter(ArtisanListing.id == listing_id)
        .first()
    )
    if not listing or not listing.is_active or listing.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan listing not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing.id,
        )
        .first()
    )

    if not listing_ref:
        return ArtisanReviewSummaryResponse(
            listing_id=listing.id,
            listing_title=listing.title,
            average_rating=0.0,
            total_reviews=0,
            rating_breakdown={"5": 0, "4": 0, "3": 0, "2": 0, "1": 0},
            verified_reviews_count=0,
        )

    reviews = (
        db.query(Review)
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
        .all()
    )

    total_reviews = len(reviews)
    verified_count = sum(1 for r in reviews if r.is_verified_booking)
    breakdown = _compute_rating_breakdown(reviews)

    if total_reviews > 0:
        avg_rating = round(sum(float(r.rating) for r in reviews) / total_reviews, 2)
    else:
        avg_rating = 0.0

    return ArtisanReviewSummaryResponse(
        listing_id=listing.id,
        listing_title=listing.title,
        average_rating=avg_rating,
        total_reviews=total_reviews,
        rating_breakdown=breakdown,
        verified_reviews_count=verified_count,
    )


# ==============================================================================
# 5. Traveler's Own Reviews (GET /api/v1/artisan-reviews/me)
# ==============================================================================

@router.get(
    "/api/v1/artisan-reviews/me",
    response_model=list[ArtisanReviewResponse],
    summary="Get all artisan reviews submitted by the authenticated traveler",
)
def get_my_artisan_reviews(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    Authenticated endpoint: Returns all artisan reviews submitted by the current traveler,
    ordered by creation date descending.
    """
    query = (
        db.query(Review)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .filter(
            Review.user_id == current_user.id,
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
        )
        .order_by(Review.created_at.desc())
    )

    reviews = (
        query.offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return [_format_review_response(r) for r in reviews]


# ==============================================================================
# 6. Update Review (PUT /api/v1/artisan-reviews/{review_id})
# ==============================================================================

@router.put(
    "/api/v1/artisan-reviews/{review_id}",
    response_model=ArtisanReviewResponse,
    summary="Update a review previously submitted by the authenticated traveler",
)
def update_artisan_review(
    review_id: int,
    payload: ArtisanReviewUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Allows a traveler to edit their star rating, title, or review text.
    Only the author of the review can modify it.
    """
    review = (
        db.query(Review)
        .filter(Review.id == review_id)
        .first()
    )
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    if review.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit reviews you personally authored",
        )

    if payload.rating is not None:
        review.rating = payload.rating
    if payload.title is not None:
        review.title = payload.title
    if payload.comment is not None:
        review.comment = payload.comment

    review.updated_at = func.sysutcdatetime()
    db.commit()
    db.refresh(review)

    return _format_review_response(review)


# ==============================================================================
# 7. Delete Review (DELETE /api/v1/artisan-reviews/{review_id})
# ==============================================================================

@router.delete(
    "/api/v1/artisan-reviews/{review_id}",
    summary="Delete a review submitted by the authenticated traveler",
)
def delete_artisan_review(
    review_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Permanently deletes a review. Only the author of the review can delete it.
    """
    review = (
        db.query(Review)
        .filter(Review.id == review_id)
        .first()
    )
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    if review.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete reviews you personally authored",
        )

    db.delete(review)
    db.commit()

    return {"message": "Review deleted successfully", "id": review_id}
