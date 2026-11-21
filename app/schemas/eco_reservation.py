from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class EcoSlotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: AwareDatetime
    capacity: int = Field(ge=1, le=50, strict=True)


class EcoSlotReschedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_starts_at: AwareDatetime
    starts_at: AwareDatetime


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
    sponsored_tracking_token: str | None = Field(default=None, max_length=2048)
    participant_ages: list[Annotated[int, Field(ge=0, le=100, strict=True)]] | None = Field(
        default=None, max_length=50
    )


class EcoReservationResponse(BaseModel):
    id: int
    module: Literal["eco_experience"]
    reference: str
    experience_id: int
    slot_id: int
    participants: int
    status: str
    payment_status: str
    payment_method: Literal["pay_at_location", "online"]
    total: Decimal
    currency: str
    starts_at: datetime
    ends_at: datetime


class EcoRefundDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]


class EcoRefundStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    currency: str
    refund_status: str


class EcoRefundReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["processing", "succeeded", "failed", "reconciliation_required"]
    capture_reference: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    refund_reference: str | None = Field(default=None, pattern=r"^[0-9]{1,50}$")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
