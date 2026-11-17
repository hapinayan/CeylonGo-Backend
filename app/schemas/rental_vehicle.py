
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
