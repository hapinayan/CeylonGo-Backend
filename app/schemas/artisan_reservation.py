"""
Pydantic schemas for Artisan Product Reservations (Phase P0 - Step 3).
Defines request payloads, response schemas, and validation rules for
reserving handcrafted products for in-store pickup and cash payment.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtisanReservationBase(BaseModel):
    quantity: int = Field(default=1, ge=1, le=100, description="Quantity of items to reserve (must be >= 1)")
    pickup_date: date = Field(..., description="Estimated in-store pickup date")
    special_notes: str | None = Field(default=None, max_length=1000, description="Special instructions or requests for the artisan")

    @field_validator("special_notes")
    @classmethod
    def trim_special_notes(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None

    @field_validator("pickup_date")
    @classmethod
    def validate_pickup_date(cls, value: date) -> date:
        today = date.today()
        if value < today:
            raise ValueError("Pickup date cannot be in the past")
        return value


class ArtisanReservationCreate(ArtisanReservationBase):
    """Payload for creating a new product reservation."""
    model_config = ConfigDict(extra="forbid")

    showcase_item_id: int = Field(gt=0, le=2147483647, description="ID of the showcase product to reserve")


class ArtisanReservationCancelRequest(BaseModel):
    """Payload for cancelling an active reservation."""
    cancellation_reason: str | None = Field(default=None, max_length=1000, description="Reason for cancellation")

    @field_validator("cancellation_reason")
    @classmethod
    def trim_reason(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class ArtisanReservationStatusUpdate(BaseModel):
    """Payload for updating reservation status."""
    status: Literal["confirmed", "ready_for_pickup", "completed", "cancelled"] = Field(
        ..., description="Target status transition"
    )
    cancellation_reason: str | None = Field(default=None, max_length=1000)


class ArtisanReservationResponse(BaseModel):
    """Detailed response schema for an artisan product reservation."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    showcase_item_id: int
    artisan_listing_id: int
    traveler_id: int
    quantity: int
    total_price: Decimal
    currency: str
    reservation_status: str
    pickup_date: date | None = None
    special_notes: str | None = None
    expires_at: datetime | None = None
    cancelled_by: int | None = None
    cancellation_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    # Associated entity display metadata for frontend convenience
    item_title: str | None = None
    item_image_url: str | None = None
    listing_title: str | None = None
    provider_business_name: str | None = None
    traveler_name: str | None = None
    traveler_email: str | None = None

