
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
    "cancelled",
    "completed",
]


class ActivityBookingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_slot_id: int = Field(gt=0)

    participants: int = Field(
        ge=1,
        le=10000,
    )

    payment_method: PaymentMethod


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

    cancellation_reason: str | None
    cancelled_at: datetime | None

    created_at: datetime
    updated_at: datetime


class ActivityBookingCancellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=5,
        max_length=1000,
    )
