from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field


class PromotionPackageCreate(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=150)]
    description: Annotated[str | None, Field(default=None, max_length=2000)]
    discount_percent: Annotated[Decimal | None, Field(default=None, ge=0, le=100)]
    discount_amount: Annotated[Decimal | None, Field(default=None, ge=0)]
    currency: Annotated[str, Field(default="LKR", min_length=3, max_length=3)]
    valid_from: date
    valid_to: date
    is_active: bool = True


class PromotionPackageUpdate(BaseModel):
    title: Annotated[str | None, Field(default=None, min_length=1, max_length=150)]
    description: Annotated[str | None, Field(default=None, max_length=2000)]
    discount_percent: Annotated[Decimal | None, Field(default=None, ge=0, le=100)]
    discount_amount: Annotated[Decimal | None, Field(default=None, ge=0)]
    currency: Annotated[str | None, Field(default=None, min_length=3, max_length=3)]
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool | None = None


class PromotionPackageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    description: str | None
    discount_percent: Decimal | None
    discount_amount: Decimal | None
    currency: str
    valid_from: date
    valid_to: date
    is_active: bool
    created_at: datetime


class PromotionCampaignCreate(BaseModel):
    campaign_name: Annotated[str, Field(min_length=1, max_length=150)]
    homestay_id: int
    budget: Annotated[Decimal, Field(gt=0)]
    currency: Annotated[str, Field(default="LKR", min_length=3, max_length=3)]
    starts_at: datetime
    ends_at: datetime


class PromotionCampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    campaign_name: str
    target_module: str
    budget: Decimal
    spent_amount: Decimal
    currency: str
    status: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    impressions_count: int = 0
    clicks_count: int = 0
    bookings_count: int = 0


class PromotionEventCreate(BaseModel):
    event_type: Annotated[str, Field(pattern="^(impression|click|booking|inquiry)$")]


class PromotionEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    event_type: str
    user_id: int | None
    ip_address: str | None
    created_at: datetime


class PromotionAnalyticsSummary(BaseModel):
    campaign_id: int
    campaign_name: str
    status: str
    budget: Decimal
    spent_amount: Decimal
    currency: str
    impressions: int
    clicks: int
    bookings: int
    ctr: float
    conversion_rate: float


class CampaignPaymentInitiateResponse(BaseModel):
    campaign_id: int
    order_id: str
    amount: Decimal
    currency: str
    merchant_id: str
    hash_value: str
    sandbox: bool
    status: str
    note: str
