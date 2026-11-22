
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


class ActivityCustomerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str


class ActivitySlotSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    reserved_count: int


class ActivityBookingProviderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    activity_id: int
    activity_title: str
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
    slot: ActivitySlotSummary | None = None
    customer: ActivityCustomerSummary | None = None


class ActivityBookingProviderListResponse(BaseModel):
    items: list[ActivityBookingProviderItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ActivitySlotAttendeeItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    booking_id: int
    user_id: int
    full_name: str
    email: str
    participants: int
    booking_status: str
    payment_status: str
    total_price: Decimal
    currency: str
    created_at: datetime


class ActivitySlotAttendeeListResponse(BaseModel):
    slot_id: int
    activity_id: int
    activity_title: str
    starts_at: datetime
    ends_at: datetime
    capacity: int
    reserved_count: int
    total_attendees: int
    attendees: list[ActivitySlotAttendeeItem]
