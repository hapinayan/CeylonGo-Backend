
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
