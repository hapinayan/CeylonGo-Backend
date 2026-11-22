import math
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, Review
from app.models.user import User
from app.schemas.provider_reviews import (
    ProviderReviewDistribution,
    ProviderReviewItem,
    ProviderReviewListResponse,
    ProviderReviewSummaryResponse,
)

router = APIRouter(
    prefix="/api/v1/providers/me/reviews",
    tags=["Provider Reviews"],
)


@router.get(
    "",
    response_model=ProviderReviewListResponse,
    summary="List reviews received for provider's listings",
)
def list_provider_reviews(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    listing_id: Annotated[int | None, Query(description="Filter by target listing ID")] = None,
    min_rating: Annotated[Decimal | None, Query(ge=Decimal("1.0"), le=Decimal("5.0"))] = None,
    sort_by: Annotated[
        Literal["newest", "oldest", "rating_high", "rating_low"], Query()
    ] = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """
    Retrieves published customer reviews for all listings owned by the authenticated provider.
    Enforces strict provider ownership isolation through ListingReference.
    """
    query = (
        db.query(Review, ListingReference, User)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .join(User, Review.user_id == User.id)
        .filter(
            ListingReference.provider_profile_id == provider_profile.id,
            Review.is_published.is_(True),
        )
    )

    if listing_id is not None:
        query = query.filter(ListingReference.target_id == listing_id)

    if min_rating is not None:
        query = query.filter(Review.rating >= min_rating)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    if sort_by == "oldest":
        query = query.order_by(Review.created_at.asc())
    elif sort_by == "rating_high":
        query = query.order_by(Review.rating.desc(), Review.created_at.desc())
    elif sort_by == "rating_low":
        query = query.order_by(Review.rating.asc(), Review.created_at.desc())
    else:
        query = query.order_by(Review.created_at.desc())

    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for review, listing_ref, reviewer in rows:
        items.append(
            ProviderReviewItem(
                id=review.id,
                rating=review.rating,
                title=review.title,
                comment=review.comment,
                reviewer_name=reviewer.full_name,
                is_verified_booking=review.is_verified_booking,
                created_at=review.created_at,
                listing_title=listing_ref.title,
                module_type=listing_ref.module_type,
            )
        )

    return ProviderReviewListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/summary",
    response_model=ProviderReviewSummaryResponse,
    summary="Get rating summary and star distribution for provider listings",
)
def get_provider_review_summary(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Aggregates overall rating average and 1-star through 5-star distribution
    for all reviews received by the provider's listings.
    """
    reviews = (
        db.query(Review.rating)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .filter(
            ListingReference.provider_profile_id == provider_profile.id,
            Review.is_published.is_(True),
        )
        .all()
    )

    total = len(reviews)
    if total == 0:
        return ProviderReviewSummaryResponse(
            average_rating=Decimal("0.00"),
            total_reviews=0,
            distribution=ProviderReviewDistribution(),
        )

    sum_ratings = sum(r[0] for r in reviews)
    avg_rating = round(Decimal(sum_ratings) / Decimal(total), 2)

    dist = ProviderReviewDistribution()
    for (r,) in reviews:
        val = float(r)
        if 1.0 <= val < 2.0:
            dist.stars_1 += 1
        elif 2.0 <= val < 3.0:
            dist.stars_2 += 1
        elif 3.0 <= val < 4.0:
            dist.stars_3 += 1
        elif 4.0 <= val < 5.0:
            dist.stars_4 += 1
        elif val >= 5.0:
            dist.stars_5 += 1

    return ProviderReviewSummaryResponse(
        average_rating=avg_rating,
        total_reviews=total,
        distribution=dist,
    )

