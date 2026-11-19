from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.dependencies import require_admin
from app.core.homestay_permissions import require_homestay_manager
from app.core.listing_sync import (
    sync_homestay_booking_reference,
    sync_homestay_listing_reference,
)
from app.core.notification_service import create_notification
from app.core.publication import public_homestay
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, ListingReference, Review
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.homestay_review import (
    HomestayReviewCreateRequest,
    HomestayReviewListResponse,
    HomestayReviewResponse,
    ReviewModerationRequest,
)

router = APIRouter(
    prefix="/api/v1/homestays",
    tags=["Homestay Reviews"],
)


def _get_homestay_listing_ref(
    db: Session, homestay_id: int, *, public: bool = False,
) -> tuple[Homestay, ListingReference]:
    query = db.query(Homestay).filter(Homestay.id == homestay_id)
    if public:
        query = query.filter(public_homestay())
    homestay = query.first()
    if not homestay or not homestay.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found",
        )

    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay_id,
        )
        .first()
    )
    if not ref:
        ref = sync_homestay_listing_reference(db, homestay)
        if not ref:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Homestay listing is not eligible for public reviews",
            )
        db.flush()

    return homestay, ref


# TRAVELER: Submit review for eligible completed booking
@router.post(
    "/{homestay_id}/reviews",
    response_model=HomestayReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a verified traveler review for a completed homestay booking",
)
def submit_homestay_review(
    homestay_id: int,
    payload: HomestayReviewCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):

    # Validate booking exists and belongs to this traveler
    booking = (
        db.query(Booking)
        .filter(
            Booking.id == payload.booking_id,
            Booking.homestay_id == homestay_id,
            Booking.user_id == current_user.id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eligible booking not found for this homestay",
        )

    lock_booking_resource(db, f"homestay_booking_{homestay_id}")
    db.refresh(booking)
    homestay, listing_ref = _get_homestay_listing_ref(db, homestay_id)

    # Review eligibility: only completed bookings can be reviewed
    if booking.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reviews can only be submitted for completed bookings",
        )

    booking_ref = sync_homestay_booking_reference(db, booking, homestay)
    if booking_ref is None:
        raise HTTPException(409, "Booking has no review reference")
    booking_ref_id = booking_ref.id

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

    review = Review(
        listing_reference_id=listing_ref.id,
        booking_reference_id=booking_ref_id,
        user_id=current_user.id,
        rating=payload.rating,
        title=payload.title,
        comment=payload.comment,
        is_verified_booking=True,
        is_published=True,
        updated_at=func.sysutcdatetime(),
    )
    db.add(review)
    db.flush()

    # Notify property provider
    provider_user_id = homestay.owner_id
    if not provider_user_id and homestay.provider_profile:
        provider_user_id = homestay.provider_profile.user_id

    if provider_user_id:
        create_notification(
            db=db,
            user_id=provider_user_id,
            title=f"New Review for {homestay.name}",
            message=f"{current_user.full_name} left a {payload.rating}-star review: \"{payload.title or 'Review'}\"",
            notification_type="new_review",
            link_url=f"/homestays/{homestay.id}",
        )

    db.commit()
    db.refresh(review)

    return HomestayReviewResponse(
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
    )


# PUBLIC: List approved reviews for a homestay
@router.get(
    "/{homestay_id}/reviews",
    response_model=HomestayReviewListResponse,
    summary="Get public published reviews and rating statistics for a homestay",
)
def list_homestay_reviews(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
):
    _, listing_ref = _get_homestay_listing_ref(db, homestay_id, public=True)

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
        HomestayReviewResponse(
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
        )
        for r, author_name in items_tuples
    ]

    return HomestayReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        page=page,
        page_size=page_size,
    )


# PROVIDER: View all reviews for owned homestays
@router.get(
    "/reviews/mine",
    response_model=HomestayReviewListResponse,
    summary="Provider views reviews across all owned homestays",
)
def list_my_homestay_reviews(
    current_user: Annotated[User, Depends(require_homestay_manager)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if profile is not None:
        target_ref_ids = (
            db.query(ListingReference.id)
            .filter(
                ListingReference.module_type == "homestay",
                ListingReference.provider_profile_id == profile.id,
            )
            .all()
        )
    else:
        # Fallback to homestays owned directly
        homestay_ids = [
            h.id
            for h in db.query(Homestay.id)
            .filter(Homestay.owner_id == current_user.id)
            .all()
        ]
        target_ref_ids = (
            db.query(ListingReference.id)
            .filter(
                ListingReference.module_type == "homestay",
                ListingReference.target_id.in_(homestay_ids),
            )
            .all()
        )

    ref_ids = [t[0] for t in target_ref_ids]
    if not ref_ids:
        return HomestayReviewListResponse(
            items=[], total=0, average_rating=0.0, page=page, page_size=page_size
        )

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

    items = [
        HomestayReviewResponse(
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
        )
        for r, author_name in items_tuples
    ]

    return HomestayReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        page=page,
        page_size=page_size,
    )


# ADMIN: Moderate review
@router.post(
    "/reviews/{review_id}/moderate",
    response_model=HomestayReviewResponse,
    summary="Admin moderation of a review (publish / unpublish)",
)
def moderate_review(
    review_id: int,
    payload: ReviewModerationRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    review = db.query(Review).filter(Review.id == review_id).first()
    if not review:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Review not found",
        )

    review.is_published = payload.is_published
    db.commit()
    db.refresh(review)

    return HomestayReviewResponse(
        id=review.id,
        listing_reference_id=review.listing_reference_id,
        booking_reference_id=review.booking_reference_id,
        user_id=review.user_id,
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        is_verified_booking=review.is_verified_booking,
        is_published=review.is_published,
        created_at=review.created_at,
    )
