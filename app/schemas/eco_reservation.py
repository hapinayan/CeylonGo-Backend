from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class EcoSlotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: AwareDatetime
    capacity: int = Field(ge=1, le=50, strict=True)


class EcoSlotResponse(BaseModel):
    id: int
    experience_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    available_places: int
    is_open: bool


class EcoReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: int = Field(gt=0, le=2147483647, strict=True)
    participants: int = Field(ge=1, le=50, strict=True)
    accepts_safety_rules: bool = Field(default=False, strict=True)


class EcoReservationResponse(BaseModel):
    id: int
    module: Literal["eco_experience"]
    reference: str
    experience_id: int
    slot_id: int
    participants: int
    status: str
    payment_status: str
    payment_method: Literal["pay_at_location"]
    total: Decimal
    currency: str
    starts_at: datetime
    ends_at: datetime
