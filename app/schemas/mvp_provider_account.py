from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.mvp import ProviderResponse
from app.schemas.mvp_account import PasswordChange
from app.schemas.provider_auth import validate_provider_password_strength
from app.schemas.mvp_homestay import HomestayBookingDetails
from app.schemas.provider_registration import DraftResponse, validate_sri_lankan_phone

ProviderCategory = Literal[
    "homestay", "tour_guide", "activity", "eco_tourism", "artisan", "rental"
]
Address = Annotated[str, Field(min_length=2, max_length=255)]
District = Annotated[str, Field(min_length=2, max_length=80)]


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    full_name: str | None = Field(None, min_length=2, max_length=120)
    personal_phone: str | None = Field(None, max_length=32)
    personal_address: Address | None = None
    personal_district: District | None = None
    business_name: str | None = Field(None, min_length=2, max_length=150)
    business_category: ProviderCategory | None = None
    business_description: str | None = Field(None, max_length=1000)
    business_phone: str | None = Field(None, max_length=32)
    business_address: Address | None = None
    business_district: District | None = None
    website_url: str | None = Field(None, max_length=500)
    social_links: list[Annotated[str, Field(max_length=500)]] = Field(
        default_factory=list,
        max_length=8,
    )

    @field_validator(
        "full_name",
        "business_name",
        "business_category",
        "business_phone",
        "business_district",
    )
    @classmethod
    def required_when_supplied(cls, value):
        if value is None:
            raise ValueError("This field cannot be cleared")
        return value

    @field_validator("personal_phone", "business_phone")
    @classmethod
    def phone(cls, value):
        return None if value is None else validate_sri_lankan_phone(value)

    @field_validator("website_url", "social_links")
    @classmethod
    def links(cls, value):
        links = value if isinstance(value, list) else [value] if value else []
        try:
            for link in links:
                parsed = urlsplit(link)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or any(
                        char.isspace() or not char.isprintable() or char == chr(92)
                        for char in link
                    )
                ):
                    raise ValueError
                _ = parsed.port
        except ValueError:
            raise ValueError(
                "Use valid HTTP or HTTPS links without credentials"
            ) from None
        return list(dict.fromkeys(links)) if isinstance(value, list) else value or None


class ProviderPasswordChange(PasswordChange):
    new_password: SecretStr = Field(min_length=12, max_length=128)
    confirm_password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("new_password")
    @classmethod
    def strength(cls, value):
        validate_provider_password_strength(value.get_secret_value())
        return value


class ProfileChange(BaseModel):
    request_id: UUID
    status: Literal["pending", "approved", "rejected"]
    changes: dict[str, str | None]
    requested_at: datetime
    decided_at: datetime | None = None
    decision_reason: str | None = None


class ProviderAccountProfile(ProviderResponse):
    email: str
    full_name: str
    is_active: bool
    avatar_url: str | None = None
    personal_phone: str | None = None
    personal_address: str | None = None
    personal_district: str | None = None
    business_category: str | None = None
    business_description: str | None = None
    business_phone: str
    business_address: str | None = None
    business_district: str
    website_url: str | None = None
    social_links: list[str] = Field(default_factory=list)
    approval_status: str
    business_verification_status: str | None = None
    profile_change: ProfileChange | None = None


class ProviderSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_booking_limit: int = Field(5, ge=1, le=20, strict=True)


class ProviderSettings(ProviderSettingsPatch):
    provider_notifications_available: Literal[False] = False


class ProviderDashboard(BaseModel):
    total_services: int
    published_services: int
    pending_verification_services: int
    rejected_services: int
    total_bookings: int
    pending_bookings: int
    confirmed_bookings: int
    completed_bookings: int
    cancelled_bookings: int
    recent_bookings: list[HomestayBookingDetails]
    upcoming_bookings: list[HomestayBookingDetails]
    wallet_balance: Decimal
    currency: Literal["LKR"] = "LKR"
    free_bookings_remaining: int
    net_commission_paid: Decimal


class AdminProviderSummary(ProviderResponse):
    profile_change_pending: bool = False


class AdminProviderApplication(DraftResponse):
    profile_change: ProfileChange | None = None
