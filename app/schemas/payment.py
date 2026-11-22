from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.eco_experience import EcoNativeFinanceTotals


class PaymentInitiateResponse(BaseModel):
    booking_id: int
    booking_reference: str
    merchant_id: str
    checkout_url: str
    order_id: str
    items: str
    amount: Decimal
    currency: str
    hash: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    return_url: str
    cancel_url: str
    notify_url: str


class PaymentStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    booking_id: int
    booking_status: str
    payment_method: str
    payment_status: str
    total_price: Decimal
    currency: str
    paid_at: datetime | None = None
    transaction_id: int | None = None


class GuidePaymentStatusResponse(PaymentStatusResponse):
    payment_expires_at: datetime | None = None


class RefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


class RefundProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Must be 'approved', 'rejected', or 'completed'")
    notes: str | None = Field(default=None, max_length=500)


class RefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_transaction_id: int
    amount: Decimal
    currency: str
    refund_reason: str
    refund_status: str
    processed_by: int | None = None
    processed_at: datetime | None = None
    created_at: datetime

    @field_validator("refund_status", mode="before")
    @classmethod
    def public_refund_status(cls, value: str) -> str:
        return "completed" if value == "processed" else value


class ProviderEarningsSummaryResponse(BaseModel):
    total_gross_earnings: Decimal
    completed_booking_earnings: Decimal = Decimal("0.00")
    ceylongo_commission: Decimal = Decimal("0.00")
    verified_refunds: Decimal = Decimal("0.00")
    provider_net_earnings: Decimal = Decimal("0.00")
    pending_earnings: Decimal = Decimal("0.00")
    available_settlement_balance: Decimal = Decimal("0.00")
    total_settled: Decimal
    pending_settlement: Decimal
    outstanding_liability: Decimal = Decimal("0.00")
    currency: str = "LKR"
    total_paid_bookings: int


class SettlementCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_profile_id: int
    period_start: date
    period_end: date
    gross_amount: Decimal = Field(ge=0, decimal_places=2)
    platform_fee: Decimal = Field(default=Decimal("0.00"), ge=0, decimal_places=2)
    net_payable: Decimal = Field(ge=0, decimal_places=2)
    payout_reference: str | None = Field(default=None, max_length=100)
    settlement_status: str = Field(default="pending")


class SettlementResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    period_start: date
    period_end: date
    gross_amount: Decimal
    platform_fee: Decimal
    net_payable: Decimal
    currency: str
    settlement_status: str
    payout_reference: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class PaymentTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference_id: int
    user_id: int
    gateway_name: str
    gateway_transaction_id: str | None = None
    amount: Decimal
    gross_amount: Decimal | None = None
    commission_rate: Decimal | None = None
    commission_amount: Decimal | None = None
    provider_payable_amount: Decimal | None = None
    currency: str
    payment_status: str
    payment_method: str
    paid_at: datetime | None = None
    created_at: datetime


class AdminFinancialSummaryResponse(BaseModel):
    total_gross_booking_revenue: Decimal
    total_ceylongo_commission: Decimal
    total_provider_liabilities: Decimal
    total_verified_refunds: Decimal
    total_settled_payouts: Decimal
    pending_settlement_payouts: Decimal
    total_advertising_revenue: Decimal
    currency: str = "LKR"
    native_eco_financials: list[EcoNativeFinanceTotals] = Field(default_factory=list)
