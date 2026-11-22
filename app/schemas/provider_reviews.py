from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProviderReviewItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    reviewer_name: str | None = None
    is_verified_booking: bool
    created_at: datetime
    listing_title: str | None = None
    module_type: str | None = None


class ProviderReviewDistribution(BaseModel):
    stars_1: int = 0
    stars_2: int = 0
    stars_3: int = 0
    stars_4: int = 0
    stars_5: int = 0


class ProviderReviewSummaryResponse(BaseModel):
    average_rating: Decimal
    total_reviews: int
    distribution: ProviderReviewDistribution


class ProviderReviewListResponse(BaseModel):
    items: list[ProviderReviewItem]
    total: int
    page: int
    page_size: int
    total_pages: int

