"""
Pydantic Schemas for Artisan Reviews & Ratings API (Phase P1 Step 10).
Conforms to dbo.reviews contract in SQL Server Migration 027:
- listing_reference_id (INT NOT NULL)
- booking_reference_id (INT NULL)
- user_id (INT NOT NULL)
- rating (DECIMAL(3,2) CHECK 1.00 <= rating <= 5.00)
- title (NVARCHAR(150) NULL)
- comment (NVARCHAR(2000) NULL)
- is_verified_booking (BIT NOT NULL DEFAULT 0)
- is_published (BIT NOT NULL DEFAULT 1)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtisanReviewBase(BaseModel):
    """Base fields for an artisan review."""
    rating: Decimal = Field(
        ...,
        ge=Decimal("1.0"),
        le=Decimal("5.0"),
        description="Rating score between 1.00 and 5.00 stars",
    )
    title: str | None = Field(
        default=None,
        max_length=150,
        description="Optional headline or title for the review",
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional detailed feedback or review text",
    )

    @field_validator("title", "comment", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | None) -> str | None:
        if v is not None:
            trimmed = v.strip()
            return trimmed if trimmed else None
        return None

    @field_validator("rating", mode="after")
    @classmethod
    def validate_and_round_rating(cls, v: Decimal) -> Decimal:
        if v < Decimal("1.00") or v > Decimal("5.00"):
            raise ValueError("Rating must be between 1.00 and 5.00")
        return round(v, 2)


class ArtisanReviewCreate(ArtisanReviewBase):
    """Payload to create an artisan review for a specific listing."""
    reservation_id: int = Field(
        ...,
        gt=0,
        description="ID of the completed Artisan Reservation that verifies this review",
    )


class ArtisanDirectReviewCreate(ArtisanReviewCreate):
    """Payload to create an artisan review directly via /api/v1/artisan-reviews."""
    listing_id: int | None = Field(
        default=None,
        gt=0,
        description="Optional artisan listing ID (resolved from reservation if omitted)",
    )


class ArtisanReviewUpdate(BaseModel):
    """Payload to update an existing artisan review."""
    rating: Decimal | None = Field(
        default=None,
        ge=Decimal("1.0"),
        le=Decimal("5.0"),
        description="Updated rating score between 1.00 and 5.00 stars",
    )
    title: str | None = Field(
        default=None,
        max_length=150,
        description="Updated headline or title",
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Updated detailed feedback",
    )

    @field_validator("title", "comment", mode="before")
    @classmethod
    def sanitize_strings(cls, v: str | None) -> str | None:
        if v is not None:
            trimmed = v.strip()
            return trimmed if trimmed else None
        return None

    @field_validator("rating", mode="after")
    @classmethod
    def validate_and_round_rating(cls, v: Decimal | None) -> Decimal | None:
        if v is not None:
            if v < Decimal("1.00") or v > Decimal("5.00"):
                raise ValueError("Rating must be between 1.00 and 5.00")
            return round(v, 2)
        return None


class ArtisanReviewResponse(BaseModel):
    """Structured response schema for an artisan review."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_reference_id: int
    booking_reference_id: int | None = None
    user_id: int
    user_name: str | None = None
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool = False
    is_published: bool = True
    created_at: datetime
    updated_at: datetime | None = None
    artisan_listing_id: int | None = None
    artisan_listing_title: str | None = None
    booking_reference_code: str | None = None


class ArtisanReviewSummaryResponse(BaseModel):
    """Aggregated review metrics and rating distribution breakdown for an artisan listing."""
    model_config = ConfigDict(from_attributes=True)

    listing_id: int
    listing_title: str
    average_rating: float = 0.0
    total_reviews: int = 0
    rating_breakdown: dict[str, int] = Field(
        default_factory=lambda: {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}
    )
    verified_reviews_count: int = 0


class ArtisanReviewPaginationResponse(BaseModel):
    """Paginated collection of reviews along with overall listing summary."""
    model_config = ConfigDict(from_attributes=True)

    items: list[ArtisanReviewResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 10
    total_pages: int = 0
    average_rating: float = 0.0
    total_reviews: int = 0

