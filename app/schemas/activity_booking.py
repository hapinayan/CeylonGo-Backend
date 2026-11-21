
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


PaymentMethod = Literal[
    "pay_at_location",
    "online",
]

PaymentStatus = Literal[
    "unpaid",
    "pending",
    "paid",
    "failed",
    "refunded",
]

BookingStatus = Literal[
    "pending",
    "confirmed",
    "rejected",
    "cancelled",
    "completed",
]


class ActivityParticipantCreate(BaseModel):
    """Schema for supplying individual participant details during booking."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=150)
    age: int | None = Field(default=None, ge=0, le=130)
    emergency_contact_name: str | None = Field(default=None, max_length=150)
    emergency_contact_phone: str | None = Field(default=None, max_length=30)
    special_requirements: str | None = Field(default=None, max_length=1000)
    waiver_signed: bool = Field(default=False)


class ActivityParticipantResponse(BaseModel):
    """Schema for returning participant details."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_booking_id: int
    full_name: str
    age: int | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None
    special_requirements: str | None = None
    waiver_signed: bool
    created_at: datetime


class ActivityBookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_slot_id: int = Field(gt=0)

    participants: int = Field(
        ge=1,
        le=10000,
    )

    payment_method: PaymentMethod = "pay_at_location"

    participants_list: list[ActivityParticipantCreate] | None = Field(
        default=None,
        description="Optional list of participant records. If provided, length must equal participants count.",
    )


class ActivityBookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_slot_id: int
    user_id: int

    participants: int

    total_price: Decimal
    currency: str

    payment_method: str
    payment_status: str
    booking_status: str

    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None

    created_at: datetime
    updated_at: datetime

    participants_list: list[ActivityParticipantResponse] = Field(default_factory=list)


class ActivityBookingCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=5,
        max_length=1000,
    )


class ActivityBookingRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=3,
        max_length=1000,
        description="Reason for rejecting booking",
    )
