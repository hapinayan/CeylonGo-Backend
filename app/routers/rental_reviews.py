"""
FastAPI Router for CeylonGo Module 09 — Rental Vehicle Ratings & Reviews (TR-15).

Requirements covered:
- TR-15: Ratings & Reviews (Verified travelers can review completed rentals; 1-5 stars;
  title, comment; calculate vehicle average rating; list published reviews).
"""

from datetime import datetime, timezone
from decimal import Decimal
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.notification_service import create_notification
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import BookingReference, ListingReference, Review
from app.models.user import User
from app.routers.auth import get_current_user

router = APIRouter(
    tags=["Rental Reviews"],
)


# ==============================================================================
# SCHEMAS
# ==============================================================================

class RentalReviewCreate(BaseModel):
    booking_id: int
    rating: Decimal = Field(..., ge=1.0, le=5.0, description="Rating from 1.0 to 5.0")
    title: str | None = Field(default=None, max_length=150)
    comment: str | None = Field(default=None, max_length=2000)


class RentalReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_reference_id: int
    booking_reference_id: int | None
    user_id: int
    reviewer_name: str | None = None
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool = True
    is_published: bool = True
    created_at: datetime


class RentalReviewListResponse(BaseModel):
    items: list[RentalReviewResponse]
    total: int
    average_rating: float
    page: int
    limit: int
    pages: int


# ==============================================================================
# HELPER: SYNC LISTING REFERENCE
# ==============================================================================

def sync_rental_listing_reference(db: Session, vehicle: RentalVehicle) -> ListingReference | None:
    """Synchronize an approved rental vehicle with central listing_references."""
    if vehicle.id is None:
        return None

    is_ref_active = bool(vehicle.approval_status == "approved" and vehicle.status == "available")
    district = vehicle.district or (vehicle.location.district if vehicle.location else "General")
    city = vehicle.city or (vehicle.location.city if vehicle.location else None)
    lat = vehicle.location.latitude if vehicle.location else None
    lon = vehicle.location.longitude if vehicle.location else None

    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id == vehicle.id,
        )
        .first()
    )

    if ref is None:
        ref = ListingReference(
            module_type="rental_vehicle",
            target_id=vehicle.id,
            provider_profile_id=vehicle.provider_id,
            title=vehicle.vehicle_name,
            district=district,
            city=city,
            latitude=lat,
            longitude=lon,
            is_active=is_ref_active,
        )
        db.add(ref)
        db.flush()
    else:
        ref.title = vehicle.vehicle_name
        ref.district = district
        ref.city = city
        if lat is not None:
            ref.latitude = lat
        if lon is not None:
            ref.longitude = lon
        ref.is_active = is_ref_active
        ref.provider_profile_id = vehicle.provider_id

    return ref


# ==============================================================================
# TR-15: SUBMIT RENTAL REVIEW
# ==============================================================================

@router.post(
    "/api/v1/rental-reviews",
    response_model=RentalReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit verified review for a completed rental booking (TR-15)",
)
def submit_rental_review(
    payload: RentalReviewCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Submits a review for a completed rental booking.
    SECURITY & INTEGRITY:
    - User must be the owner of the booking.
    - Booking status MUST be 'completed' (TR-15).
    - Enforces strictly one review per booking reference.
    - Stores verified review in central dbo.reviews table.
    - Dispatches notification to vehicle provider.
    """
    booking = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.id == payload.booking_id,
            RentalBooking.user_id == current_user.id,
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eligible rental booking not found",
        )

    # TR-15 Acceptance Criteria: Only completed rentals can be reviewed
    if booking.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reviews can only be submitted for completed rentals",
        )

    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found",
        )

    # Synchronize listing reference
    listing_ref = sync_rental_listing_reference(db, vehicle)
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Rental listing is not eligible for public reviews",
        )

    # Look up or resolve central booking_reference
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
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
        # Fallback check by listing and user
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
                detail="You have already submitted a review for this vehicle",
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

    # TR-18: Dispatch notification to rental provider
    provider_profile = db.get(ProviderProfile, vehicle.provider_id)
    if provider_profile and provider_profile.user_id:
        create_notification(
            db=db,
            user_id=provider_profile.user_id,
            title=f"New Review for {vehicle.vehicle_name}",
            message=f"{current_user.full_name} left a {payload.rating}-star review: \"{payload.title or 'Rental Review'}\"",
            notification_type="new_rental_review",
            link_url=f"/provider/rentals/vehicles/{vehicle.id}",
        )

    db.commit()
    db.refresh(review)

    return RentalReviewResponse(
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


# ==============================================================================
# TR-15: LIST VEHICLE REVIEWS & AVERAGE RATING
# ==============================================================================

@router.get(
    "/api/v1/rental-vehicles/{vehicle_id}/reviews",
    response_model=RentalReviewListResponse,
    summary="Get published reviews and average rating for rental vehicle (TR-15)",
)
def get_rental_vehicle_reviews(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=50, description="Items per page")] = 20,
):
    """
    Returns public paginated reviews for a rental vehicle along with the overall average rating.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id == vehicle_id,
        )
        .first()
    )

    if not listing_ref:
        return RentalReviewListResponse(
            items=[],
            total=0,
            average_rating=0.0,
            page=page,
            limit=limit,
            pages=0,
        )

    query = (
        db.query(Review, User.full_name)
        .join(User, Review.user_id == User.id)
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
    )

    total = query.count()
    if total == 0:
        return RentalReviewListResponse(
            items=[],
            total=0,
            average_rating=0.0,
            page=page,
            limit=limit,
            pages=0,
        )

    # Compute average rating
    avg_rating_raw = (
        db.query(func.avg(Review.rating))
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == True,
        )
        .scalar()
    )
    average_rating = round(float(avg_rating_raw), 2) if avg_rating_raw else 0.0

    pages = max(1, ceil(total / limit))
    reviews = (
        query.order_by(Review.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    items = [
        RentalReviewResponse(
            id=r.id,
            listing_reference_id=r.listing_reference_id,
            booking_reference_id=r.booking_reference_id,
            user_id=r.user_id,
            reviewer_name=user_name,
            rating=r.rating,
            title=r.title,
            comment=r.comment,
            is_verified_booking=r.is_verified_booking,
            is_published=r.is_published,
            created_at=r.created_at,
        )
        for r, user_name in reviews
    ]

    return RentalReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        page=page,
        limit=limit,
        pages=pages,
    )
