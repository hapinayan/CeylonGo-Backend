from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RentalDepositResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    deposit_amount: Decimal
    currency: str = "LKR"
    deposit_status: str  # 'held', 'partially_refunded', 'fully_refunded', 'forfeited', 'deducted'
    deducted_amount: Decimal = Decimal("0.00")
    deduction_reason: str | None = None
    refunded_amount: Decimal = Decimal("0.00")
    held_at: datetime
    released_at: datetime | None = None


class RentalDepositCollectRequest(BaseModel):
    amount: Decimal | None = Field(
        default=None,
        gt=Decimal("0.00"),
        description="Deposit amount collected in cash/offline at handover (defaults to category default if omitted)",
    )
    currency: str = Field(default="LKR", max_length=3)
    notes: str | None = Field(
        default=None,
        max_length=500,
        description="Optional provider notes or receipt reference",
    )


class RentalDepositResolveRequest(BaseModel):
    action: Literal["release", "deduct"] = Field(
        ...,
        description="'release' to refund full deposit upon clean inspection, 'deduct' to withhold an amount for damages or penalties",
    )
    deducted_amount: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0.00"),
        description="Amount to deduct. Must be <= recorded deposit_amount. Required to be > 0 if action='deduct'",
    )
    deduction_reason: str | None = Field(
        default=None,
        max_length=1000,
        description="Detailed explanation for the deduction. Mandatory if deducted_amount > 0",
    )


class RentalDepositDetailResponse(BaseModel):
    booking_id: int
    required_deposit_amount: Decimal
    currency: str = "LKR"
    deposit_status: str
    active_deposit: RentalDepositResponse | None = None
    history: list[RentalDepositResponse] = []
    message: str
