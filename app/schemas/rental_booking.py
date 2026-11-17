
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)


BookingStatus = Literal[
    "pending",
    "confirmed",
    "cancelled",
    "completed",
]


class RentalBookingCreate(BaseModel):
    vehicle_id: int = Field(gt=0)
    pickup_at: datetime
    return_at: datetime

    @model_validator(mode="after")
    def validate_booking_dates(self):
        if self.return_at <= self.pickup_at:
            raise ValueError(
                "Return date must be after pickup date"
            )
        return self


class RentalBookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    user_id: int
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    status: BookingStatus
    created_at: datetime


class RentalBookingStatusUpdate(BaseModel):
    status: BookingStatus
