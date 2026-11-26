from datetime import datetime
from typing import Annotated, Literal
import re

from pydantic import (
    BaseModel, ConfigDict, Field, SecretStr, StrictBool, StringConstraints, field_validator,
    model_validator,
)

from app.schemas.mvp import UserResponse


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(UserResponse):
    country: str | None = None
    avatar_url: str | None = None
    email_verified: bool = False
    authentication_method: Literal["google", "password"] | None = None
    updated_at: datetime


class ProfilePatch(Input):
    full_name: str = Field(default="", min_length=2, max_length=120)
    phone: str | None = Field(None, max_length=32)
    country: str | None = Field(None, max_length=80)

    @field_validator("full_name", "phone", "country", mode="before")
    @classmethod
    def sanitize(cls, value, info):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if info.field_name == "phone" and value:
            value = re.sub(r"[ ()-]", "", value)
            if not re.fullmatch(r"\+?[0-9]{7,15}", value):
                raise ValueError("Enter a valid phone number")
        if info.field_name == "country" and value:
            if not any(c.isalpha() for c in value) or not all(
                c.isalpha() or c in " .'-" for c in value
            ):
                raise ValueError("Enter a valid country name")
        return value if info.field_name == "full_name" else value or None


class PasswordChange(Input):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=6, max_length=128)
    confirm_password: SecretStr = Field(min_length=6, max_length=128)


Interest = Literal["culture", "adventure", "wildlife", "nature", "eco_tourism",
                   "local_food", "heritage", "beaches", "village_experiences",
                   "artisan_handicrafts", "cycling"]
Accommodation = Literal["homestay", "guesthouse", "hotel", "hostel", "other"]
Transport = Literal["walking", "bicycle", "motorbike", "scooter", "public_transport", "other"]
Style = Literal["budget", "adventure", "cultural", "family", "relaxation", "local_experiences"]
Category = Literal["booking", "payment", "community", "account"]
Destination = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class Preferences(Input):
    preferred_destinations: list[Destination] = Field(default_factory=list, max_length=20)
    interests: list[Interest] = Field(default_factory=list, max_length=20)
    accommodation_preferences: list[Accommodation] = Field(default_factory=list, max_length=10)
    transport_preferences: list[Transport] = Field(default_factory=list, max_length=10)
    budget_min: int | None = Field(None, strict=True, ge=0, le=1_000_000_000)
    budget_max: int | None = Field(None, strict=True, ge=0, le=1_000_000_000)
    travel_style: Style | None = None

    @field_validator("preferred_destinations")
    @classmethod
    def destinations(cls, values):
        unique = {}
        for value in values:
            value = " ".join(value.split())
            if not any(c.isalpha() for c in value) or not all(
                c.isalpha() or c in " .'-" for c in value
            ):
                raise ValueError("Enter a district or destination name")
            unique.setdefault(value.casefold(), value)
        return list(unique.values())

    @field_validator("interests", "accommodation_preferences", "transport_preferences")
    @classmethod
    def deduplicate(cls, values):
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def budget(self):
        if self.budget_min is not None and self.budget_max is not None:
            if self.budget_min > self.budget_max:
                raise ValueError("Minimum budget exceeds maximum budget")
        return self


class NotificationSettings(Input):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    booking_updates: StrictBool = True
    payment_updates: StrictBool = True
    community_activity: StrictBool = True
    promotional_updates: StrictBool = False


class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    notification_type: str
    category: Category
    title: str
    message: str
    related_entity_type: Literal["booking", "community_post", "account"]
    related_entity_id: int
    is_read: bool
    created_at: datetime
    read_at: datetime | None


class UnreadCount(BaseModel):
    unread_count: int


class NotificationPage(UnreadCount):
    items: list[NotificationItem]
    total: int
    page: int
    page_size: int


class ReadAll(UnreadCount):
    updated_count: int


class PasswordChanged(BaseModel):
    message: str
    login_required: bool

