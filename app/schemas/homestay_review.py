from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class HomestayReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: int = Field(description="Eligible completed homestay booking ID")
    rating: Decimal = Field(ge=Decimal("1.00"), le=Decimal("5.00"), decimal_places=2)
    title: str | None = Field(default=None, max_length=150)
    comment: str | None = Field(default=None, max_length=2000)


class ReviewModerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_published: bool


class HomestayReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_reference_id: int
    booking_reference_id: int | None = None
    user_id: int
    reviewer_name: str | None = None
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool
    is_published: bool
    created_at: datetime


class HomestayReviewListResponse(BaseModel):
    items: list[HomestayReviewResponse]
    total: int
    average_rating: float
    page: int
    page_size: int
