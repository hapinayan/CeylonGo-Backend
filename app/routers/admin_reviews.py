from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import require_content_moderator
from app.database.connection import get_db
from app.models.artisan_listing import ArtisanListing
from app.models.shared import ListingReference, Review
from app.models.user import User
from app.services.review_moderation import scoped_reviews

router = APIRouter(prefix="/api/v1/admin/reviews", tags=["Admin Reviews"])


class ReviewQueueItem(BaseModel):
    id: int
    listing_id: int
    rating: Decimal
    is_verified_booking: bool
    is_published: bool
    created_at: datetime
    updated_at: datetime


class ReviewQueuePage(BaseModel):
    module: Literal["artisan"]
    items: list[ReviewQueueItem]
    total: int
    page: int
    page_size: int
    total_pages: int


@router.get("", response_model=ReviewQueuePage)
def list_review_queue(
    _admin: Annotated[User, Depends(require_content_moderator)],
    db: Annotated[Session, Depends(get_db)],
    module: Literal["artisan"],
    is_published: bool | None = None,
    listing_id: Annotated[int | None, Query(ge=1, le=2_147_483_647)] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReviewQueuePage:
    query = scoped_reviews(db, module).join(ArtisanListing, ListingReference.target_id == ArtisanListing.id)
    if is_published is not None:
        query = query.filter(Review.is_published == is_published)
    if listing_id is not None:
        query = query.filter(ArtisanListing.id == listing_id)
    total = query.count()
    rows = (
        query.with_entities(Review.id, ArtisanListing.id.label("listing_id"), Review.rating,
                            Review.is_verified_booking, Review.is_published, Review.created_at, Review.updated_at)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return ReviewQueuePage(module=module, items=[ReviewQueueItem(**row._mapping) for row in rows],
                           total=total, page=page, page_size=page_size,
                           total_pages=(total + page_size - 1) // page_size)
