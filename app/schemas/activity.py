
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


ActivityCategory = Literal[
    "cultural",
    "adventure",
    "local",
    "community",
]

PricingType = Literal["per_person", "per_group"]

ActivityStatus = Literal[
    "draft",
    "pending",
    "approved",
    "rejected",
    "suspended",
]


class ActivityBase(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=10, max_length=4000)

    category: ActivityCategory

    district: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    meeting_point: str = Field(min_length=2, max_length=500)

    latitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-90"),
        le=Decimal("90"),
        max_digits=9,
        decimal_places=6,
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-180"),
        le=Decimal("180"),
        max_digits=9,
        decimal_places=6,
    )

    duration_minutes: int = Field(gt=0)

    itinerary: str | None = None

    requirements: str | None = Field(
        default=None,
        max_length=2000,
    )
    safety_rules: str | None = Field(
        default=None,
        max_length=2000,
    )
    required_equipment: str | None = Field(
        default=None,
        max_length=1000,
    )

    minimum_age: int = Field(default=0, ge=0, le=120)

    pricing_type: PricingType
    price: Decimal = Field(
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    @field_validator(
        "title",
        "description",
        "district",
        "city",
        "meeting_point",
    )
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError(
                "Field must contain at least 2 characters"
            )
        return value

    @field_validator(
        "itinerary",
        "requirements",
        "safety_rules",
        "required_equipment",
    )
    @classmethod
    def trim_optional_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ActivityCreate(ActivityBase):
    """Create a new activity in draft status."""

    pass


class ActivityUpdate(BaseModel):
    """Update an existing activity using PATCH."""

    title: str | None = Field(
        default=None,
        min_length=2,
        max_length=150,
    )
    description: str | None = Field(
        default=None,
        min_length=10,
        max_length=4000,
    )

    category: ActivityCategory | None = None

    district: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )
    city: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
    )
    meeting_point: str | None = Field(
        default=None,
        min_length=2,
        max_length=500,
    )

    latitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-90"),
        le=Decimal("90"),
        max_digits=9,
        decimal_places=6,
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-180"),
        le=Decimal("180"),
        max_digits=9,
        decimal_places=6,
    )

    duration_minutes: int | None = Field(
        default=None,
        gt=0,
    )

    itinerary: str | None = None
    requirements: str | None = Field(
        default=None,
        max_length=2000,
    )
    safety_rules: str | None = Field(
        default=None,
        max_length=2000,
    )
    required_equipment: str | None = Field(
        default=None,
        max_length=1000,
    )

    minimum_age: int | None = Field(
        default=None,
        ge=0,
        le=120,
    )

    pricing_type: PricingType | None = None

    price: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    @field_validator(
        "title",
        "description",
        "district",
        "city",
        "meeting_point",
    )
    @classmethod
    def validate_required_text(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        if len(value) < 2:
            raise ValueError(
                "Field must contain at least 2 characters"
            )
        return value

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        required_fields = {
            "title",
            "description",
            "category",
            "district",
            "city",
            "meeting_point",
            "duration_minutes",
            "minimum_age",
            "pricing_type",
            "price",
        }

        for field_name in required_fields:
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(
                    f"{field_name} cannot be null"
                )

        return self


class ActivityResponse(ActivityBase):
    """Activity details returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int

    currency: str
    approval_status: ActivityStatus
    is_active: bool

    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None

    created_at: datetime
    updated_at: datetime


class ActivitySummary(BaseModel):
    """Compact activity information for listings."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: ActivityCategory
    district: str
    city: str
    duration_minutes: int
    pricing_type: PricingType
    price: Decimal
    currency: str
    approval_status: ActivityStatus
    is_active: bool


class ActivityActiveUpdate(BaseModel):
    """Schema for activating or deactivating an activity."""

    is_active: bool


class ActivityImageResponse(BaseModel):
    """Image response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_id: int
    image_url: str
    alt_text: str | None = None
    display_order: int = 0
    is_cover: bool = False


class ActivityImageUpdate(BaseModel):
    """Schema for updating image metadata."""

    alt_text: str | None = None
    display_order: int | None = Field(default=None, ge=0)
    is_cover: bool | None = None


class ActivityOrganizerContact(BaseModel):
    """Public approved organizer contact channels."""

    model_config = ConfigDict(from_attributes=True)

    business_name: str
    phone: str
    secondary_phone: str | None = None
    description: str | None = None


class ActivityCategoryDetail(BaseModel):
    """Approved activity category taxonomy definition."""

    slug: ActivityCategory
    name: str
    description: str
    examples: list[str]


class ActivityPublicSummary(BaseModel):
    """Compact summary schema for public discovery cards and search."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    category: ActivityCategory
    district: str
    city: str
    duration_minutes: int
    pricing_type: PricingType
    price: Decimal
    currency: str
    minimum_age: int
    cover_image_url: str | None = None
    short_description: str
    provider_business_name: str | None = None


class ActivityPagination(BaseModel):
    """Paginated response for public activity discovery."""

    items: list[ActivityPublicSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


class ActivityPublicDetail(BaseModel):
    """Full public detail schema for travelers viewing an approved activity."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    category: ActivityCategory
    district: str
    city: str
    duration_minutes: int
    itinerary: str | None = None
    requirements: str | None = None
    safety_rules: str | None = None
    required_equipment: str | None = None
    minimum_age: int
    pricing_type: PricingType
    price: Decimal
    currency: str
    meeting_point: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    organizer: ActivityOrganizerContact | None = None
    images: list[ActivityImageResponse] = Field(default_factory=list)
    created_at: datetime


APPROVED_ACTIVITY_CATEGORIES: list[ActivityCategoryDetail] = [
    ActivityCategoryDetail(
        slug="cultural",
        name="Cultural Activities",
        description="Traditional dance, heritage experiences, festivals and cultural shows",
        examples=[
            "Traditional dance",
            "Heritage experiences",
            "Festivals",
            "Cultural shows",
        ],
    ),
    ActivityCategoryDetail(
        slug="adventure",
        name="Adventure Activities",
        description="Rafting, surfing, hiking, rock climbing and ziplining",
        examples=[
            "Rafting",
            "Surfing",
            "Hiking",
            "Rock climbing",
            "Ziplining",
        ],
    ),
    ActivityCategoryDetail(
        slug="local",
        name="Local Experiences",
        description="Cooking classes, village tours, farming and craft workshops",
        examples=[
            "Cooking classes",
            "Village tours",
            "Farming",
            "Craft workshops",
        ],
    ),
    ActivityCategoryDetail(
        slug="community",
        name="Community Experiences",
        description="Tea experiences, fishing communities and community-led visits",
        examples=[
            "Tea experiences",
            "Fishing communities",
            "Community-led visits",
        ],
    ),
]


def make_short_description(description: str, max_length: int = 200) -> str:
    """Clean and truncate text to a maximum length with ellipsis."""
    cleaned = " ".join(description.split())
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 3].rstrip() + "..."
