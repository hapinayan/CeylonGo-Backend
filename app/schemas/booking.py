
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BookingCreate(BaseModel):
    homestay_id: int = Field(gt=0)
    check_in: date
    check_out: date
    guests: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.check_out <= self.check_in:
            raise ValueError(
                "Check-out date must be after check-in date"
            )
        return self


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    homestay_id: int
    user_id: int
    check_in: date
    check_out: date
    guests: int
    total_price: Decimal
    status: str
    created_at: datetime


class BookingListResponse(BaseModel):
    items: list[BookingResponse]
    total: int
    page: int
    page_size: int


class HomestayCustomerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str


class HomestayBookingProviderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    homestay_id: int
    homestay_name: str
    user_id: int
    check_in: date
    check_out: date
    guests: int
    total_price: Decimal
    currency: str = "LKR"
    status: str
    payment_method: str = "pay_at_location"
    payment_status: str = "unpaid"
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    customer: HomestayCustomerSummary | None = None


class HomestayBookingProviderListResponse(BaseModel):
    items: list[HomestayBookingProviderItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class HomestayBookingCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=3,
        max_length=1000,
        description="Reason for cancelling the reservation",
    )


class HomestayCancellationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    homestay_id: int
    status: str
    cancelled_by: int
    cancelled_at: datetime
    cancellation_reason: str
    message: str = "Booking cancelled successfully"
