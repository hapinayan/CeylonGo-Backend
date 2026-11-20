from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.artisan_business_hours import ArtisanBusinessHoursResponse
from app.schemas.artisan_image import ArtisanBusinessImageResponse

# Standard reference craft categories for Sri Lankan artisans
VALID_CRAFT_CATEGORIES = (
    "Batik & Textiles",
    "Handloom & Weaving",
    "Pottery & Ceramics",
    "Woodcarving & Masks",
    "Jewelry & Gem Craft",
    "Brass & Metalware",
    "Cane, Bamboo & Coir",
    "Traditional Leather",
    "Spices & Herbal Products",
    "Traditional Art & Painting",
    "Other Traditional Craft",
)


class ArtisanListingBase(BaseModel):
    title: str = Field(..., min_length=2, max_length=150, description="Artisan business or workshop name")
    craft_category: str = Field(..., min_length=2, max_length=50, description="Category of artisan craft")
    description: str | None = Field(default=None, max_length=2000, description="Artisan craft story, background, and techniques")
    district: str = Field(..., min_length=2, max_length=100, description="District in Sri Lanka (e.g. Kandy, Galle, Colombo)")
    city: str = Field(..., min_length=2, max_length=100, description="City or town")
    address: str | None = Field(default=None, max_length=255, description="Physical street address")
    latitude: Decimal | None = Field(default=None, ge=-90.0, le=90.0, description="Latitude coordinate (-90 to +90)")
    longitude: Decimal | None = Field(default=None, ge=-180.0, le=180.0, description="Longitude coordinate (-180 to +180)")
    phone: str | None = Field(default=None, max_length=25, description="Contact phone number")
    email: EmailStr | None = Field(default=None, max_length=255, description="Contact email address")
    website_url: str | None = Field(default=None, max_length=500, description="Website or social media link")
    opening_hours: str | None = Field(default=None, max_length=255, description="e.g. Mon-Sat: 9:00 AM - 5:00 PM")
    cover_image_url: str | None = Field(default=None, max_length=1000, description="Cover image or workshop photo URL")

    @field_validator("title", "craft_category", "district", "city")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 2:
            raise ValueError("Field must contain at least 2 non-whitespace characters")
        return trimmed

    @field_validator("phone", "website_url", "opening_hours", "cover_image_url", "address", "description")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class ArtisanListingCreate(ArtisanListingBase):
    """Payload for creating a new artisan listing by an approved provider."""
    pass


class ArtisanListingUpdate(BaseModel):
    """Payload for partial/full updates of an existing artisan listing."""
    title: str | None = Field(default=None, min_length=2, max_length=150)
    craft_category: str | None = Field(default=None, min_length=2, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    district: str | None = Field(default=None, min_length=2, max_length=100)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    latitude: Decimal | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: Decimal | None = Field(default=None, ge=-180.0, le=180.0)
    phone: str | None = Field(default=None, max_length=25)
    email: EmailStr | None = Field(default=None, max_length=255)
    website_url: str | None = Field(default=None, max_length=500)
    opening_hours: str | None = Field(default=None, max_length=255)
    cover_image_url: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = Field(default=None, description="Publish/unpublish toggle")

    @field_validator("title", "craft_category", "district", "city")
    @classmethod
    def validate_non_empty(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            if len(trimmed) < 2:
                raise ValueError("Field must contain at least 2 non-whitespace characters")
            return trimmed
        return None

    @field_validator("phone", "website_url", "opening_hours", "cover_image_url", "address", "description")
    @classmethod
    def trim_optional_strings(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class ArtisanListingSummary(BaseModel):
    """Compact summary schema for catalog cards, search results, and map pins."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    craft_category: str
    district: str
    city: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    cover_image_url: str | None = None
    is_active: bool
    is_verified: bool
    is_featured: bool
    approval_status: str = "approved"
    average_rating: float | None = None
    total_reviews: int = 0
    distance_km: float | None = Field(default=None, description="Calculated distance in km when queried via nearby discovery")


class ArtisanListingResponse(ArtisanListingBase):
    """Full detailed response schema for an artisan listing."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    is_active: bool
    is_verified: bool
    is_featured: bool
    approval_status: str = "approved"
    verification_status: str = "unverified"
    rejection_reason: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    created_at: datetime
    updated_at: datetime
    provider_business_name: str | None = None
    average_rating: float | None = None
    total_reviews: int = 0
    distance_km: float | None = Field(default=None, description="Calculated distance in km when queried via nearby discovery")
    images: list[ArtisanBusinessImageResponse] = Field(default_factory=list)
    business_images: list[ArtisanBusinessImageResponse] = Field(default_factory=list)
    business_hours: list[ArtisanBusinessHoursResponse] = Field(default_factory=list)

    @field_validator("business_hours", mode="before")
    @classmethod
    def sort_business_hours(cls, v: object) -> list:
        if isinstance(v, (list, tuple, set)):
            try:
                return sorted(v, key=lambda x: getattr(x, "day_of_week", 0))
            except Exception:
                return list(v)
        return []


class ArtisanListingPagination(BaseModel):
    """Paginated listing response container."""
    items: list[ArtisanListingSummary]
    total: int = Field(ge=0, description="Total items matching filter")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, le=100, description="Number of items per page")
    total_pages: int = Field(ge=0, description="Total number of pages")


class ArtisanNearbyListingPagination(BaseModel):
    """Paginated listing response container for nearby discovery."""
    items: list[ArtisanListingSummary]
    total: int = Field(ge=0, description="Total items matching filter")
    page: int = Field(ge=1, description="Current page number")
    page_size: int = Field(ge=1, le=100, description="Number of items per page")
    total_pages: int = Field(ge=0, description="Total number of pages")
    origin_latitude: float = Field(description="Client search latitude")
    origin_longitude: float = Field(description="Client search longitude")
    radius_km: float = Field(description="Search radius in kilometers")


class ArtisanListingStatusUpdate(BaseModel):
    """Admin-only status management schema."""
    is_verified: bool | None = None
    is_featured: bool | None = None
    is_active: bool | None = None
    approval_status: str | None = None
    verification_status: str | None = None
    rejection_reason: str | None = None
