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


class BookingCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cancellation_reason: str | None = Field(default=None, max_length=1000)


class BookingRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class CashPaymentRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    notes: str | None = Field(default=None, max_length=500)


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
    currency: str = "LKR"
    payment_method: str = "pay_at_location"
    payment_status: str = "unpaid"
    nightly_rate_snapshot: Decimal | None = None
    cancellation_policy_snapshot: str | None = None
    free_cancellation_hours_snapshot: int = 24
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    cancelled_by: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BookingListResponse(BaseModel):
    items: list[BookingResponse]
    total: int
    page: int
    page_size: int
