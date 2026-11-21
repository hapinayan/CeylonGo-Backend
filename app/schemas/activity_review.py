"""
Pydantic schemas for Module 03 — Activity Ratings & Reviews (A13, B17).
"""

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class ActivityReviewCreateRequest(BaseModel):
    """Payload for verified traveler submitting a review for a completed activity booking."""

    booking_id: int = Field(..., description="ID of the completed activity booking")
    rating: Decimal = Field(..., ge=1.0, le=5.0, description="Rating from 1.0 to 5.0")
    title: str | None = Field(default=None, max_length=150, description="Optional review title")
    comment: str | None = Field(default=None, max_length=2000, description="Optional review comment")


class ActivityReviewResponse(BaseModel):
    """Response representation of an activity review."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_reference_id: int
    booking_reference_id: int | None = None
    user_id: int
    reviewer_name: str | None = None
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool = True
    is_published: bool = True
    created_at: datetime
    activity_id: int | None = None
    activity_title: str | None = None


class ActivityReviewListResponse(BaseModel):
    """Paginated list of activity reviews with average rating summary."""

    items: list[ActivityReviewResponse]
    total: int = Field(ge=0)
    average_rating: float = Field(ge=0.0, le=5.0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
