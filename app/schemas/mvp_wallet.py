"""API contracts for simulated wallet operations."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

TransactionType = Literal[
    "top_up",
    "free_booking",
    "commission_deduction",
    "commission_refund",
]


class TopUpRequest(BaseModel):
    amount: Annotated[
        Decimal,
        Field(ge=Decimal("0.01"), le=Decimal("1000000"), decimal_places=2),
    ]
    request_key: UUID
    simulate_outcome: Literal["success", "failure"]

    @field_validator("request_key", mode="before")
    @classmethod
    def canonical_uuid(cls, value):
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Use a lowercase, hyphenated UUID")
        return value


class WalletSummary(BaseModel):
    provider_id: int
    balance: Decimal
    currency: Literal["LKR"]
    commission_rate: Decimal
    free_bookings_used: int
    free_bookings_remaining: int
    total_top_ups: Decimal
    total_commission_deducted: Decimal
    total_commission_refunded: Decimal
    net_commission_paid: Decimal


class TransactionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int | None
    deduction_id: int | None
    transaction_type: TransactionType
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    commission_rate: Decimal | None
    booking_amount: Decimal | None
    request_key: UUID | None
    simulate_outcome: Literal["success", "failure"] | None
    description: str
    created_at: AwareDatetime

    @field_validator("created_at", mode="before")
    @classmethod
    def as_utc(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class TopUpResult(BaseModel):
    success: bool
    currency: Literal["LKR"]
    balance: Decimal
    message: str
    transaction: TransactionItem


class CommissionPreview(BaseModel):
    booking_amount: Decimal
    commission_rate: Decimal
    required_commission: Decimal
    free_booking_eligible: bool
    wallet_balance: Decimal
    shortfall: Decimal
    confirmation_allowed: bool
    currency: Literal["LKR"]


class AdminWalletTotals(BaseModel):
    currency: Literal["LKR"]
    total_provider_wallets: int
    total_wallet_balances: Decimal
    total_top_ups: Decimal
    gross_commission_collected: Decimal
    commission_refunds: Decimal
    net_commission_revenue: Decimal
    total_commission_free_bookings: int


class WalletPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[WalletSummary]


class TransactionPage(BaseModel):
    page: int
    page_size: int
    total: int
    items: list[TransactionItem]
