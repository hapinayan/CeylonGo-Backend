from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TourGuideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bio: str = Field(min_length=10, max_length=2000)
    years_of_experience: int = Field(default=0, ge=0, le=2147483647)
    specialties: str | None = Field(default=None, max_length=500)

    license_number: str | None = Field(default=None, max_length=100)
    license_type: Literal["national", "chauffeur", "site", "area"] | None = None
    license_expiry_date: date | None = None

    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    daily_rate: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="LKR", min_length=3, max_length=3)

    base_city: str = Field(min_length=1, max_length=100)
    base_district: str = Field(min_length=1, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    base_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)

    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0, le=2147483647)
    max_group_size: int = Field(default=10, gt=0, le=2147483647)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.base_latitude is None) != (self.base_longitude is None):
            raise ValueError(
                "Latitude and longitude must be provided together"
            )
        return self


class TourGuideUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bio: str | None = Field(default=None, min_length=10, max_length=2000)
    years_of_experience: int | None = Field(default=None, ge=0, le=2147483647)
    specialties: str | None = Field(default=None, max_length=500)

    license_number: str | None = Field(default=None, max_length=100)
    license_type: Literal["national", "chauffeur", "site", "area"] | None = None
    license_expiry_date: date | None = None

    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    daily_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    base_city: str | None = Field(default=None, min_length=1, max_length=100)
    base_district: str | None = Field(default=None, min_length=1, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    base_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)

    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0, le=2147483647)
    max_group_size: int | None = Field(default=None, gt=0, le=2147483647)

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        required_fields = (
            "bio", "years_of_experience", "daily_rate", "currency", "base_city",
            "base_district", "free_cancellation_hours", "max_group_size",
        )
        for field in required_fields:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideResponse(TourGuideCreate):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    provider_profile_id: int
    is_available: bool
    is_active: bool
    approval_status: str
    verification_status: str
    rating_average: Decimal
    review_count: int
    created_at: datetime
    updated_at: datetime
