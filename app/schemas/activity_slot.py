
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


class ActivitySlotCreate(BaseModel):
    """Create a scheduled slot for an activity."""

    starts_at: datetime
    ends_at: datetime

    capacity: int = Field(
        ge=1,
        le=10000,
        description="Maximum participants allowed",
    )

    @model_validator(mode="after")
    def validate_schedule(self):
        if (
            self.starts_at.tzinfo is None
            or self.ends_at.tzinfo is None
        ):
            raise ValueError(
                "Slot timestamps must include a timezone offset"
            )

        start = self.starts_at.astimezone(timezone.utc)
        end = self.ends_at.astimezone(timezone.utc)
        now = datetime.now(timezone.utc)

        if start <= now:
            raise ValueError(
                "Slot start time must be in the future"
            )

        if end <= start:
            raise ValueError(
                "Slot end time must be after start time"
            )

        return self


class ActivitySlotUpdate(BaseModel):
    """Update a scheduled slot for an activity."""

    starts_at: datetime | None = None
    ends_at: datetime | None = None
    capacity: int | None = Field(
        default=None,
        ge=1,
        le=10000,
        description="Maximum participants allowed",
    )
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.starts_at is not None and self.ends_at is not None:
            if (
                self.starts_at.tzinfo is None
                or self.ends_at.tzinfo is None
            ):
                raise ValueError(
                    "Slot timestamps must include a timezone offset"
                )

            start = self.starts_at.astimezone(timezone.utc)
            end = self.ends_at.astimezone(timezone.utc)
            now = datetime.now(timezone.utc)

            if start <= now:
                raise ValueError(
                    "Slot start time must be in the future"
                )

            if end <= start:
                raise ValueError(
                    "Slot end time must be after start time"
                )

        return self


class ActivitySlotResponse(BaseModel):
    """Scheduled slot details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_id: int

    starts_at: datetime
    ends_at: datetime

    capacity: int
    reserved_count: int
    is_active: bool

    created_at: datetime


class ActivitySlotAvailability(BaseModel):
    """Public slot availability response."""

    id: int
    activity_id: int

    starts_at: datetime
    ends_at: datetime

    capacity: int
    reserved_count: int

    available_places: int = Field(ge=0)
    is_available: bool


class SessionCancellationRequest(BaseModel):
    """Request payload for cancelling an entire activity session due to weather/safety."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=5,
        max_length=1000,
        description="Reason for session cancellation (weather, safety, emergency)",
    )


class SessionCancellationResponse(BaseModel):
    """Response returned when an activity session is cancelled by provider."""

    slot_id: int
    activity_id: int
    cancelled_bookings_count: int
    released_capacity: int
    is_active: bool
    reason: str


# ==============================================================================
# Availability Calendar Schemas (A07)
# ==============================================================================


class ActivitySlotCalendarItem(BaseModel):
    """Granular slot availability details for calendar representation."""

    model_config = ConfigDict(extra="ignore")

    id: int
    activity_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    reserved_count: int
    remaining_capacity: int = Field(ge=0)
    availability_status: Literal["available", "limited", "sold_out"]
    is_available: bool


class ActivityDailyAvailabilitySummary(BaseModel):
    """Day-level calendar summary aggregating slots."""

    model_config = ConfigDict(extra="ignore")

    date: date
    total_slots: int
    available_slots: int
    total_capacity: int
    total_reserved: int
    total_remaining: int
    has_availability: bool


class ActivityAvailabilityResponse(BaseModel):
    """Public activity availability calendar response (A07)."""

    model_config = ConfigDict(extra="ignore")

    activity_id: int
    activity_title: str
    date_from: date
    date_to: date
    total_slots: int
    available_slots: int
    days: list[ActivityDailyAvailabilitySummary] = []
    slots: list[ActivitySlotCalendarItem] = []
