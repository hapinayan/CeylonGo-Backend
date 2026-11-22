
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VehicleType = Literal["bike", "bicycle", "tuk_tuk"]
VehicleStatus = Literal["available", "unavailable"]


class RentalVehicleCreate(BaseModel):
    vehicle_type: VehicleType

    vehicle_name: str = Field(
        min_length=2,
        max_length=150,
    )

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    price_per_day: Decimal = Field(
        ge=0,
        max_digits=12,
        decimal_places=2,
    )


class RentalVehicleUpdate(BaseModel):
    vehicle_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=150,
    )

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    price_per_day: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    status: VehicleStatus | None = None


class RentalVehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None
    price_per_day: Decimal
    status: VehicleStatus
    created_at: datetime


class RentalAvailabilityBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    block_type: str = Field(default="maintenance", max_length=30)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        return self


class RentalAvailabilityBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    start_date: date
    end_date: date
    block_type: str
    reason: str | None = None
    is_active: bool
    created_at: datetime


class RentalVehicleDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(min_length=2, max_length=50)
    document_url: str = Field(min_length=5, max_length=1000)
    expiry_date: date | None = None

    @field_validator("expiry_date")
    @classmethod
    def validate_expiry(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("Document expiry date cannot be in the past")
        return v


class RentalVehicleDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    document_type: str
    document_url: str
    expiry_date: date | None = None
    is_verified: bool = False
    created_at: datetime
