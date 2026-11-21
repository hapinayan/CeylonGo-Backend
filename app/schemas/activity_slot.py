
from datetime import datetime, timezone

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
