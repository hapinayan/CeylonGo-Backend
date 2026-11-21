"""
Pydantic schemas for Module 03 — Traveler Activity Wishlist / Favorites (A14).
"""

from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class ActivityWishlistSummary(BaseModel):
    """Summary of an activity saved in a traveler's wishlist."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: str
    district: str
    city: str
    price: Decimal
    currency: str = "LKR"
    duration_minutes: int
    minimum_age: int
    cover_image_url: str | None = None
    average_rating: float | None = None
    total_reviews: int = 0


class ActivityWishlistItemResponse(BaseModel):
    """Representation of an individual saved wishlist item."""

    model_config = ConfigDict(from_attributes=True)

    wishlist_id: int
    saved_at: datetime
    is_available: bool
    activity: ActivityWishlistSummary | None = None


class ActivityWishlistCheckResponse(BaseModel):
    """Status check whether an activity is saved in traveler's wishlist."""

    activity_id: int
    saved: bool


class ActivityWishlistActionResponse(BaseModel):
    """Result of adding or removing an activity to/from wishlist."""

    message: str
    activity_id: int
    saved: bool
    wishlist_id: int | None = None


class ActivityWishlistPagination(BaseModel):
    """Paginated response of traveler's saved activities."""

    items: list[ActivityWishlistItemResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
