
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


VehicleType = Literal["bike", "bicycle", "tuk_tuk"]
VehicleStatus = Literal["available", "unavailable"]


class RentalVehicleCreate(BaseModel):
    vehicle_type: VehicleType
    location_id: int | None = Field(default=None, gt=0)

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
    location_id: int | None = Field(default=None, gt=0)


class RentalVehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None
    price_per_day: Decimal
    status: VehicleStatus
    location_id: int | None = None
    created_at: datetime


class RentalPickupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    location_name: str = Field(min_length=2, max_length=150)
    district: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)


class RentalPickupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location_name: str
    district: str
    city: str
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    is_active: bool
