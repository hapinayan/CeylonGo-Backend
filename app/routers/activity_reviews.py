"""
FastAPI Router for Module 03 — Activity Ratings & Reviews (A13, B17).

Requirements covered:
- A13: Traveler Activity Ratings & Reviews (Submit verified reviews for completed bookings,
  public review list with average rating).
- B17: Provider Activity Reviews (Provider interface to inspect traveler feedback across owned activities).
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.dependencies import require_traveler
from app.core.listing_sync import (
    sync_activity_booking_reference,
    sync_activity_listing_reference,
)
from app.core.notification_service import create_notification
from app.core.publication import public_activity
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, ListingReference, Review
from app.models.user import User
from app.routers.provider_activities import require_activity_provider
from app.schemas.activity_review import (
    ActivityReviewCreateRequest,
    ActivityReviewListResponse,
    ActivityReviewResponse,
)

router = APIRouter(
    tags=["Activity Reviews"],
)


# ==============================================================================
# 1. Traveler: Submit Review for Completed Activity Booking (A13)
# ==============================================================================


@router.post(
    "/api/v1/activities/{activity_id}/reviews",
    response_model=ActivityReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a verified traveler review for a completed activity booking",
)
def submit_activity_review(
    activity_id: int,
    payload: ActivityReviewCreateRequest,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Allows a verified traveler to submit a 1.0 - 5.0 star rating and review for
    a completed activity booking.

    Rules & Guards:
    1. Authenticated user must have 'traveler' role.
    2. Activity must exist and be in 'approved' status (404 otherwise).
    3. Activity must be active (400 otherwise).
    4. Booking must exist, belong to this traveler, and be for this activity (404 otherwise).
    5. Booking status must be 'completed' (400 otherwise).
    6. Strictly one review per booking reference (409 Conflict on duplicates).
    7. Creates central review in dbo.reviews and notifies activity provider.
    """
    activity = (
        db.query(Activity)
        .filter(Activity.id == activity_id)
        .first()
    )
    if not activity or activity.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    if not activity.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Activity is currently inactive",
        )

    # Validate booking exists for this traveler and matches this activity
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .filter(
            ActivityBooking.id == payload.booking_id,
            ActivityBooking.user_id == current_user.id,
            ActivitySlot.activity_id == activity_id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eligible booking not found for this activity",
        )

    # Concurrency lock on booking resource for SQL Server
    lock_booking_resource(db, f"activity_booking_{payload.booking_id}")
    db.refresh(booking)

    # Eligibility: only completed bookings can be reviewed
    if booking.booking_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reviews can only be submitted for completed bookings",
        )

    # Resolve or synchronize listing reference
    listing_ref = sync_activity_listing_reference(db, activity)
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Activity listing is not eligible for public reviews",
        )

    # Resolve or synchronize central booking reference
    booking_ref = sync_activity_booking_reference(db, booking, activity)
    booking_ref_id = booking_ref.id if booking_ref else None

    # Enforce strictly one review per booking
    if booking_ref_id:
        existing_review = (
            db.query(Review)
            .filter(Review.booking_reference_id == booking_ref_id)
            .first()
        )
        if existing_review:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already submitted a review for this booking",
            )
    else:
        existing_review = (
            db.query(Review)
            .filter(
                Review.listing_reference_id == listing_ref.id,
                Review.user_id == current_user.id,
            )
            .first()
        )
        if existing_review:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already submitted a review for this activity",
            )

    review = Review(
        listing_reference_id=listing_ref.id,
        booking_reference_id=booking_ref_id,
        user_id=current_user.id,
        rating=payload.rating,
        title=payload.title,
        comment=payload.comment,
        is_verified_booking=True,
        is_published=True,
    )
    db.add(review)
    db.flush()

    # Notify activity provider
    provider_profile = db.get(ProviderProfile, activity.provider_profile_id)
    if provider_profile and provider_profile.user_id:
        create_notification(
            db=db,
            user_id=provider_profile.user_id,
            title=f"New Review for {activity.title}",
            message=f"{current_user.full_name or 'A traveler'} left a {payload.rating}-star review: \"{payload.title or 'Review'}\"",
            notification_type="new_review",
            link_url=f"/activities/{activity.id}",
        )

    db.commit()
    db.refresh(review)

    return ActivityReviewResponse(
        id=review.id,
        listing_reference_id=review.listing_reference_id,
        booking_reference_id=review.booking_reference_id,
        user_id=review.user_id,
        reviewer_name=current_user.full_name,
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        is_verified_booking=review.is_verified_booking,
        is_published=review.is_published,
        created_at=review.created_at,
        activity_id=activity.id,
        activity_title=activity.title,
    )


# ==============================================================================
# 2. Public: List Published Reviews for Activity (A13)
# ==============================================================================


@router.get(
    "/api/v1/activities/{activity_id}/reviews",
    response_model=ActivityReviewListResponse,
    summary="Get public published reviews and rating statistics for an activity",
)
def list_activity_reviews(
    activity_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
):
    """
    Public endpoint: Returns paginated published reviews and overall rating stats
    for an approved, active activity.
    """
    activity = (
        db.query(Activity)
        .filter(
            Activity.id == activity_id,
            public_activity(),
        )
        .first()
    )
    if not activity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.target_id == activity_id,
        )
        .first()
    )
    if not listing_ref:
        return ActivityReviewListResponse(
            items=[],
            total=0,
            average_rating=0.0,
            page=page,
            page_size=page_size,
        )

    query = (
        db.query(Review, User.full_name)
        .outerjoin(User, Review.user_id == User.id)
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
    )

    total = query.count()
    avg_rating_val = (
        db.query(func.avg(Review.rating))
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
        .scalar()
    )
    average_rating = round(float(avg_rating_val), 2) if avg_rating_val else 0.0

    items_tuples = (
        query.order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        ActivityReviewResponse(
            id=r.id,
            listing_reference_id=r.listing_reference_id,
            booking_reference_id=r.booking_reference_id,
            user_id=r.user_id,
            reviewer_name=author_name or "Verified Traveler",
            rating=r.rating,
            title=r.title,
            comment=r.comment,
            is_verified_booking=r.is_verified_booking,
            is_published=r.is_published,
            created_at=r.created_at,
            activity_id=activity.id,
            activity_title=activity.title,
        )
        for r, author_name in items_tuples
    ]

    return ActivityReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        page=page,
        page_size=page_size,
    )


# ==============================================================================
# 3. Provider: View Reviews Across Owned Activities (B17)
# ==============================================================================


@router.get(
    "/api/v1/provider/activities/reviews",
    response_model=ActivityReviewListResponse,
    summary="Provider views reviews across all owned activities",
)
@router.get(
    "/api/v1/provider/activity-reviews",
    response_model=ActivityReviewListResponse,
    summary="Provider views reviews across all owned activities (alternate path)",
    include_in_schema=False,
)
@router.get(
    "/api/v1/activities/reviews/mine",
    response_model=ActivityReviewListResponse,
    summary="Provider views reviews across all owned activities (alternate path)",
    include_in_schema=False,
)
def list_my_activity_reviews(
    provider_profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
    activity_id: int | None = Query(default=None, description="Optional filter by owned activity ID"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """
    Provider endpoint: Returns reviews submitted by travelers for the provider's activities.
    Allows filtering by a specific owned activity.
    """
    if activity_id is not None:
        # Validate that the activity belongs to this provider
        owned = (
            db.query(Activity)
            .filter(
                Activity.id == activity_id,
                Activity.provider_profile_id == provider_profile.id,
            )
            .first()
        )
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Activity not found",
            )

    ref_query = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "activity",
            ListingReference.provider_profile_id == provider_profile.id,
        )
    )
    if activity_id is not None:
        ref_query = ref_query.filter(ListingReference.target_id == activity_id)

    refs = ref_query.all()
    if not refs:
        return ActivityReviewListResponse(
            items=[],
            total=0,
            average_rating=0.0,
            page=page,
            page_size=page_size,
        )

    ref_map = {r.id: (r.target_id, r.title) for r in refs}
    ref_ids = list(ref_map.keys())

    query = (
        db.query(Review, User.full_name)
        .outerjoin(User, Review.user_id == User.id)
        .filter(Review.listing_reference_id.in_(ref_ids))
    )

    total = query.count()
    avg_rating_val = (
        db.query(func.avg(Review.rating))
        .filter(Review.listing_reference_id.in_(ref_ids))
        .scalar()
    )
    average_rating = round(float(avg_rating_val), 2) if avg_rating_val else 0.0

    items_tuples = (
        query.order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for r, author_name in items_tuples:
        target_id, title = ref_map.get(r.listing_reference_id, (None, None))
        items.append(
            ActivityReviewResponse(
                id=r.id,
                listing_reference_id=r.listing_reference_id,
                booking_reference_id=r.booking_reference_id,
                user_id=r.user_id,
                reviewer_name=author_name or "Verified Traveler",
                rating=r.rating,
                title=r.title,
                comment=r.comment,
                is_verified_booking=r.is_verified_booking,
                is_published=r.is_published,
                created_at=r.created_at,
                activity_id=target_id,
                activity_title=title,
            )
        )

    return ActivityReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        page=page,
        page_size=page_size,
    )
