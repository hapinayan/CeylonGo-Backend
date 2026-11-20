from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ArtisanBusinessImageResponse(BaseModel):
    """Detailed response schema for an artisan business gallery image."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    image_url: str
    caption: str | None = None
    display_order: int = 0
    is_primary: bool = False
    is_cover: bool = False  # Backward-compatible property alias
    created_at: datetime


class ArtisanProductImageResponse(BaseModel):
    """Detailed response schema for a showcase product gallery image."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    showcase_item_id: int
    image_url: str
    caption: str | None = None
    display_order: int = 0
    is_primary: bool = False
    created_at: datetime


class ArtisanImageReorderItem(BaseModel):
    """Individual item for batch gallery reordering."""
    id: int
    display_order: int = Field(ge=0, description="New non-negative display order")


class ArtisanImageReorderRequest(BaseModel):
    """Batch reordering request schema."""
    images: list[ArtisanImageReorderItem] = Field(
        ..., min_length=1, description="List of image IDs and their new display orders"
    )


class ArtisanImageUpdateRequest(BaseModel):
    """Individual image metadata update schema."""
    caption: str | None = Field(default=None, max_length=255)
    display_order: int | None = Field(default=None, ge=0)
    is_primary: bool | None = None
