"""
Pydantic v2 schemas for Module 04 — Eco-Tourism Slot Management and Availability.

Strict Isolation:
- No database models or migrations are modified.
- Strict input sanitization with extra='forbid' to prevent client privilege escalation.
- Enforces timezone awareness on all scheduling datetimes.
- Enforces platform environmental ceiling of <= 50 participants per slot.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

ENVIRONMENTAL_MAX_SLOT_CAPACITY = 50


class EcoSlotCreate(BaseModel):
    """
    Payload for creating an Eco-Tourism scheduling slot.
    Submitted by authenticated eco_tourism providers for their own approved listings.
    """

    model_config = ConfigDict(extra="forbid")

    starts_at: datetime = Field(
        ...,
        description="Slot start time. Must include a timezone offset and be in the future.",
    )
    ends_at: datetime = Field(
        ...,
        description="Slot end time. Must include a timezone offset and be strictly after starts_at.",
    )
    capacity: int = Field(
        ...,
        ge=1,
        le=ENVIRONMENTAL_MAX_SLOT_CAPACITY,
        description="Maximum participant capacity for this slot (1 to 50).",
    )

    @model_validator(mode="after")
    def validate_slot_timing(self) -> "EcoSlotCreate":
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Slot timestamps must include an explicit timezone offset.")

        start_utc = self.starts_at.astimezone(timezone.utc)
        end_utc = self.ends_at.astimezone(timezone.utc)
        now_utc = datetime.now(timezone.utc)

        if start_utc <= now_utc:
            raise ValueError("Slot start time must be strictly in the future.")

        if end_utc <= start_utc:
            raise ValueError("Slot end time must be strictly after the start time.")

        return self


class EcoSlotResponse(BaseModel):
    """
    Management response representation of a scheduled eco-experience slot.
    Returned to authorized providers and administrators.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(..., description="Unique slot identifier")
    eco_experience_id: int = Field(..., description="Associated EcoExperience ID")
    activity_id: int | None = Field(
        default=None,
        description="Underlying activity identifier when linked, or None for independent eco slots",
    )
    starts_at: datetime
    ends_at: datetime
    capacity: int
    reserved_count: int
    is_active: bool
    created_at: datetime

    @property
    def available_places(self) -> int:
        """Dynamically compute remaining open places."""
        return max(0, self.capacity - self.reserved_count)


class EcoSlotAvailabilityQuery(BaseModel):
    """
    Query parameters for public eco slot availability lookups.
    """

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(
        default=None,
        description="Optional filter: earliest slot date (inclusive)",
    )
    end_date: date | None = Field(
        default=None,
        description="Optional filter: latest slot date (inclusive)",
    )
    min_available_places: int | None = Field(
        default=None,
        ge=1,
        le=ENVIRONMENTAL_MAX_SLOT_CAPACITY,
        description="Optional filter: minimum open capacity required",
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "EcoSlotAvailabilityQuery":
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date cannot be earlier than start_date.")
        return self


class EcoSlotPublicItem(BaseModel):
    """
    Public representation of an eco slot returned to travelers browsing availability.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    reserved_count: int
    available_places: int = Field(ge=0, description="Available unreserved seats in this slot")
    is_available: bool = Field(
        description="True if slot is active, has available places, and starts in the future"
    )


class EcoSlotWeatherCancelRequest(BaseModel):
    """
    Payload for emergency weather/hazard cancellation of a single slot (E06).
    """

    model_config = ConfigDict(extra="forbid")

    hazard_type: str = Field(
        default="weather_alert",
        description="One of: 'weather_alert', 'monsoon_season', 'wildlife_breeding', 'park_closure'",
    )
    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Reason for emergency weather cancellation",
    )

    @field_validator("hazard_type")
    @classmethod
    def validate_hazard(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in ("weather_alert", "monsoon_season", "wildlife_breeding", "park_closure"):
            raise ValueError(
                f"Invalid hazard_type '{v}'. Allowed: weather_alert, monsoon_season, wildlife_breeding, park_closure."
            )
        return cleaned


class EcoSlotWeatherCancelResponse(BaseModel):
    """
    Response returned after emergency slot weather cancellation (E06).
    """

    model_config = ConfigDict(from_attributes=True)

    slot_id: int
    eco_experience_id: int
    is_active: bool
    hazard_type: str
    reason: str
    cancelled_bookings_count: int
    released_capacity: int
    refunds_initiated_count: int
    total_refund_amount: Decimal
    cancelled_booking_ids: list[int]
    status: str = "success"
    message: str

