"""
Pydantic Schemas for Artisan Wishlist & Favorites API (Phase P1 Step 11).
Integrates with dbo.wishlists and dbo.listing_references from SQL Server Migration 027.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtisanWishlistCreate(BaseModel):
    """Optional payload when adding an artisan business to traveler wishlist."""
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Optional traveler notes about the saved artisan business",
    )

    @field_validator("notes", mode="before")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is not None:
            trimmed = v.strip()
            return trimmed if trimmed else None
        return None


class ArtisanWishlistCheckResponse(BaseModel):
    """Status check indicating whether a given artisan listing is in traveler's wishlist."""
    model_config = ConfigDict(from_attributes=True)

    listing_id: int
    is_saved: bool
    wishlist_id: int | None = None


class ArtisanWishlistSavedListingSummary(BaseModel):
    """Detailed summary of the saved artisan business for catalog cards and map view."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    craft_category: str
    district: str
    city: str
    cover_image_url: str | None = None
    approval_status: str
    is_active: bool
    average_rating: float | None = None
    total_reviews: int = 0


class ArtisanWishlistItemResponse(BaseModel):
    """Response schema for a single wishlist entry."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    listing_reference_id: int
    artisan_listing_id: int
    notes: str | None = None
    created_at: datetime
    is_available: bool = True
    listing: ArtisanWishlistSavedListingSummary | None = None


class ArtisanWishlistPagination(BaseModel):
    """Paginated response containing saved artisan businesses."""
    model_config = ConfigDict(from_attributes=True)

    items: list[ArtisanWishlistItemResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    total_pages: int = 0


class ArtisanProductFavoriteCompatibilityResponse(BaseModel):
    """Contract documentation explaining product-level favorite limitations in Migration 027."""
    supported: bool = False
    reason: str
    recommended_alternative: str
