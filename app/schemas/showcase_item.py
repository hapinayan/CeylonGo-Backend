from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ShowcaseItemBase(BaseModel):
    title: str = Field(..., min_length=2, max_length=150, description="Showcase product or service title")
    item_type: Literal["product", "service"] = Field(
        default="product", description="Item classification: 'product' (handcrafted item) or 'service' (workshop/demonstration)"
    )
    description: str | None = Field(default=None, max_length=2000, description="Detailed story, technique, and cultural significance")
    price: Decimal | None = Field(default=None, ge=Decimal("0.0"), description="Estimated display price")
    currency: str = Field(default="LKR", max_length=10, description="Currency code (defaults to LKR)")
    image_url: str | None = Field(default=None, max_length=1000, description="Showcase image URL")
    materials: str | None = Field(default=None, max_length=500, description="Materials, tools, or timber types used")
    duration_minutes: int | None = Field(default=None, ge=0, description="Duration in minutes for services/workshops")
    is_available: bool = Field(default=True, description="Availability flag")

    @field_validator("title", "currency")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("Field cannot be empty or blank")
        return trimmed

    @field_validator("description", "image_url", "materials")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class ShowcaseItemCreate(ShowcaseItemBase):
    """Payload for creating a new showcase item under an artisan listing."""
    artisan_listing_id: int = Field(..., description="ID of the parent artisan listing owned by the caller")


class ShowcaseItemUpdate(BaseModel):
    """Payload for partial or full updates of a showcase item."""
    title: str | None = Field(default=None, min_length=2, max_length=150)
    item_type: Literal["product", "service"] | None = Field(default=None)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, ge=Decimal("0.0"))
    currency: str | None = Field(default=None, max_length=10)
    image_url: str | None = Field(default=None, max_length=1000)
    materials: str | None = Field(default=None, max_length=500)
    duration_minutes: int | None = Field(default=None, ge=0)
    is_available: bool | None = Field(default=None)
    is_featured: bool | None = Field(default=None)
    is_active: bool | None = Field(default=None, description="Soft-delete toggle")

    @field_validator("title", "currency")
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            if not trimmed:
                raise ValueError("Field cannot be empty or blank")
            return trimmed
        return None

    @field_validator("description", "image_url", "materials")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class ShowcaseItemSummary(BaseModel):
    """Compact summary schema for catalog grids, spotlights, and cards."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    title: str
    item_type: str
    price: Decimal | None = None
    currency: str
    image_url: str | None = None
    is_available: bool
    is_featured: bool
    is_active: bool


class ShowcaseItemResponse(ShowcaseItemBase):
    """Full detailed response schema for a showcase product or service."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    is_featured: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    listing_title: str | None = None


class ShowcaseItemPagination(BaseModel):
    """Paginated container for showcase items."""
    items: list[ShowcaseItemSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)
