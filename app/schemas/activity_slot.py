
from datetime import date, datetime, time, timezone

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


class ActivitySlotBatchCreate(BaseModel):
    """Batch generate recurring slots across a date range."""

    start_date: date
    end_date: date
    start_time: time
    end_time: time
    capacity: int = Field(
        ge=1,
        le=10000,
        description="Maximum participants allowed per slot",
    )
    days_of_week: list[int] | None = Field(
        default=None,
        description="Optional day of week filters: 0=Monday, 6=Sunday",
    )

    @model_validator(mode="after")
    def validate_batch(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        if (self.end_date - self.start_date).days > 90:
            raise ValueError("Batch slot generation cannot exceed 90 days")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.days_of_week is not None:
            for day in self.days_of_week:
                if day < 0 or day > 6:
                    raise ValueError(
                        "days_of_week elements must be between 0 (Monday) and 6 (Sunday)"
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


class ActivitySlotBatchResponse(BaseModel):
    """Response summary for batch slot generation."""

    created_count: int
    slots: list[ActivitySlotResponse]
    message: str


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
