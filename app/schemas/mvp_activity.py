from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ActivityCategory = Literal[
    "hiking-trekking", "wildlife-safari", "water-activities", "adventure-sports",
    "cycling", "cultural-village", "food-cooking", "nature-eco",
]
ActivityText = Annotated[str, Field(min_length=1, max_length=500)]


class ActivityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category: ActivityCategory
    meeting_point: str = Field(min_length=1, max_length=500)
    duration_minutes: int = Field(ge=1, le=10080)
    min_participants: int = Field(1, ge=1, le=1000)
    max_participants: int = Field(ge=1, le=1000)
    minimum_age: int | None = Field(None, ge=0, le=120)
    highlights: list[ActivityText] = Field(default_factory=list, max_length=30)
    included: list[ActivityText] = Field(default_factory=list, max_length=30)
    excluded: list[ActivityText] = Field(default_factory=list, max_length=30)
    equipment: str | None = Field(None, max_length=2000)
    facilities: str | None = Field(None, max_length=2000)
    safety_instructions: str | None = Field(None, max_length=2000)
    restrictions: str | None = Field(None, max_length=2000)
    cancellation_policy: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def validate_group(self):
        if self.min_participants > self.max_participants:
            raise ValueError("Minimum participants exceeds group capacity")
        return self


class ActivityPhoto(BaseModel):
    id: int
    image_url: str
    alt_text: str | None


class ActivityDetails(BaseModel):
    id: int
    title: str
    description: str
    district: str
    locality: str
    latitude: Decimal | None
    longitude: Decimal | None
    price_per_participant: Decimal
    currency: Literal["LKR"]
    activity: ActivityMetadata | None
    photos: list[ActivityPhoto]
    provider_id: int
    provider_name: str
    rating: None = None
    reviews: list = Field(default_factory=list)


class ActivityPage(BaseModel):
    items: list[ActivityDetails]
    next_offset: int | None


class ActivitySlotDetails(BaseModel):
    id: int
    date: date
    starts_at: datetime
    ends_at: datetime
    remaining_capacity: int
    available: bool
    total_amount: Decimal
    currency: Literal["LKR"] = "LKR"
