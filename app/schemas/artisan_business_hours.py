from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DAY_MAP = {
    0: "Sunday",
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
}

NAME_TO_DAY = {name.lower(): num for num, name in DAY_MAP.items()}


class ArtisanBusinessHoursBase(BaseModel):
    day_of_week: int = Field(..., ge=0, le=6, description="Day of week (0=Sunday, 1=Monday, ..., 6=Saturday)")
    open_time: time | None = Field(default=None, description="Opening time (HH:MM:SS)")
    close_time: time | None = Field(default=None, description="Closing time (HH:MM:SS)")
    is_closed: bool = Field(default=False, description="Whether business is closed on this day")

    @field_validator("day_of_week", mode="before")
    @classmethod
    def validate_day_of_week(cls, v: Any) -> int:
        if isinstance(v, str):
            clean = v.strip().lower()
            if clean in NAME_TO_DAY:
                return NAME_TO_DAY[clean]
            if clean.isdigit():
                v = int(clean)
        if isinstance(v, int):
            if 0 <= v <= 6:
                return v
            raise ValueError("day_of_week must be between 0 (Sunday) and 6 (Saturday)")
        raise ValueError(f"Invalid day_of_week: {v}")

    @model_validator(mode="after")
    def validate_hours_logic(self) -> "ArtisanBusinessHoursBase":
        if self.is_closed:
            return self
        if self.open_time is None or self.close_time is None:
            raise ValueError("open_time and close_time are required when is_closed is False")
        if self.close_time <= self.open_time:
            raise ValueError(f"close_time ({self.close_time}) must be after open_time ({self.open_time})")
        return self


class ArtisanBusinessHoursCreate(ArtisanBusinessHoursBase):
    pass


class ArtisanBusinessHoursUpdate(BaseModel):
    open_time: time | None = None
    close_time: time | None = None
    is_closed: bool | None = None

    @model_validator(mode="after")
    def validate_update_logic(self) -> "ArtisanBusinessHoursUpdate":
        if self.is_closed is False:
            if self.open_time is not None and self.close_time is not None:
                if self.close_time <= self.open_time:
                    raise ValueError(f"close_time ({self.close_time}) must be after open_time ({self.open_time})")
        return self


class ArtisanBusinessHoursResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    day_of_week: int
    day_name: str | None = None
    open_time: time | None = None
    close_time: time | None = None
    is_closed: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def populate_day_name(self) -> "ArtisanBusinessHoursResponse":
        if not self.day_name:
            self.day_name = DAY_MAP.get(self.day_of_week, f"Day {self.day_of_week}")
        return self


class ArtisanWeeklyBusinessHoursRequest(BaseModel):
    hours: list[ArtisanBusinessHoursCreate] = Field(
        ..., min_length=1, max_length=7, description="Weekly business hours schedule"
    )
