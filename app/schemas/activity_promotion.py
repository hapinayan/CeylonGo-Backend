"""
Pydantic Schemas for Module 03 — Provider Activity Discounts & Sponsored Campaigns (B15, B16).

Complies strictly with the physical schema of Microsoft SQL Server 2025 Express:
- dbo.promotion_packages (provider-level discounts)
- dbo.promotion_campaigns (provider-level activity sponsored campaigns)
"""

from datetime import date, datetime
from decimal import Decimal
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ==============================================================================
# B15: Provider Discount Schemas (dbo.promotion_packages)
# ==============================================================================


class ActivityDiscountCreate(BaseModel):
    """Payload for creating a provider discount package (B15)."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=150, description="Discount package title")]
    description: Annotated[str | None, Field(default=None, max_length=2000, description="Optional description")]
    discount_percent: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00"), description="Discount percentage (0-100)"),
    ] = None
    discount_amount: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00"), description="Fixed discount amount"),
    ] = None
    currency: Annotated[str, Field(default="LKR", min_length=3, max_length=3, description="ISO currency code")] = "LKR"
    valid_from: Annotated[date, Field(description="Start date of discount validity")]
    valid_to: Annotated[date, Field(description="End date of discount validity (inclusive)")]
    is_active: bool = True
    activity_id: Annotated[
        int | None,
        Field(default=None, ge=1, description="Optional targeted activity ID (B15)"),
    ] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if len(clean) != 3:
            raise ValueError("Currency must be a 3-character ISO code (e.g. LKR, USD)")
        return clean

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Title cannot be blank")
        return clean


class ActivityDiscountUpdate(BaseModel):
    """Payload for updating an existing provider discount package (B15)."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str | None, Field(default=None, min_length=1, max_length=150)] = None
    description: Annotated[str | None, Field(default=None, max_length=2000)] = None
    discount_percent: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00")),
    ] = None
    discount_amount: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00")),
    ] = None
    currency: Annotated[str | None, Field(default=None, min_length=3, max_length=3)] = None
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool | None = None
    activity_id: Annotated[
        int | None,
        Field(default=None, ge=1, description="Optional targeted activity ID (B15)"),
    ] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().upper()
        if len(clean) != 3:
            raise ValueError("Currency must be a 3-character ISO code (e.g. LKR, USD)")
        return clean

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("Title cannot be blank")
        return clean


class ActivityDiscountResponse(BaseModel):
    """Response representation of a provider discount package (B15)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    provider_profile_id: int
    title: str
    description: str | None = None
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    currency: str
    valid_from: date
    valid_to: date
    is_active: bool
    created_at: datetime
    activity_id: int | None = None
    activity_title: str | None = None
    original_price: Decimal | None = None
    discounted_price: Decimal | None = None
    targeting_scope: str = "provider"
    activity_targeting_status: str = "BLOCKED_BY_SCHEMA"


class ActivityDiscountPreviewRequest(BaseModel):
    """Request payload to preview activity-specific discount pricing and eligibility (B15)."""

    model_config = ConfigDict(extra="forbid")

    activity_id: Annotated[int, Field(ge=1, description="Targeted activity ID")]
    discount_percent: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00")),
    ] = None
    discount_amount: Annotated[
        Decimal | None,
        Field(default=None, ge=Decimal("0.00")),
    ] = None
    currency: Annotated[str, Field(default="LKR", min_length=3, max_length=3)] = "LKR"
    valid_from: date
    valid_to: date


class ActivityDiscountPreviewResponse(BaseModel):
    """Preview calculation of activity-specific discount pricing and eligibility (B15)."""

    model_config = ConfigDict(extra="ignore")

    activity_id: int
    activity_title: str
    original_price: Decimal
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    discounted_price: Decimal
    currency: str
    valid_from: date
    valid_to: date
    is_eligible: bool
    ineligibility_reason: str | None = None
    targeting_scope: str = "activity"
    activity_targeting_status: str = "PREVIEW_ONLY_MIGRATION_PENDING"
    is_simulation: bool = True
    simulation_notice: str = (
        "Calculated pricing is a non-persistent preview simulation. "
        "Persistent activity-specific targeting is pending Migration 041."
    )


class ActivityDiscountPagination(BaseModel):
    """Paginated list of provider discounts."""

    model_config = ConfigDict(extra="ignore")

    items: list[ActivityDiscountResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# B16: Provider Sponsored Campaign Schemas (dbo.promotion_campaigns)
# ==============================================================================


class ActivityCampaignCreate(BaseModel):
    """Payload for submitting a sponsored campaign application (B16)."""

    model_config = ConfigDict(extra="forbid")

    campaign_name: Annotated[
        str,
        Field(min_length=1, max_length=150, description="Campaign descriptive name"),
    ]
    budget: Annotated[
        Decimal,
        Field(gt=Decimal("0.00"), description="Total promotion budget"),
    ]
    currency: Annotated[
        str,
        Field(default="LKR", min_length=3, max_length=3, description="ISO currency code"),
    ] = "LKR"
    starts_at: Annotated[datetime, Field(description="Campaign scheduled start time (UTC)")]
    ends_at: Annotated[datetime, Field(description="Campaign scheduled end time (UTC)")]
    submit_for_review: Annotated[
        bool,
        Field(
            default=True,
            description="True to submit directly as pending_approval; False to save as draft",
        ),
    ] = True
    activity_id: Annotated[
        int | None,
        Field(default=None, ge=1, description="Optional targeted activity ID (B16)"),
    ] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if len(clean) != 3:
            raise ValueError("Currency must be a 3-character ISO code (e.g. LKR, USD)")
        return clean

    @field_validator("campaign_name")
    @classmethod
    def validate_campaign_name(cls, v: str) -> str:
        clean = v.strip()
        if not clean:
            raise ValueError("Campaign name cannot be blank")
        return clean


class ActivityCampaignUpdate(BaseModel):
    """Payload for editing a draft sponsored campaign (B16)."""

    model_config = ConfigDict(extra="forbid")

    campaign_name: Annotated[str | None, Field(default=None, min_length=1, max_length=150)] = None
    budget: Annotated[Decimal | None, Field(default=None, gt=Decimal("0.00"))] = None
    currency: Annotated[str | None, Field(default=None, min_length=3, max_length=3)] = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: Annotated[
        str | None,
        Field(
            default=None,
            description="Provider may only transition from 'draft' to 'pending_approval'",
        ),
    ] = None
    activity_id: Annotated[
        int | None,
        Field(default=None, ge=1, description="Optional targeted activity ID (B16)"),
    ] = None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip().upper()
        if len(clean) != 3:
            raise ValueError("Currency must be a 3-character ISO code (e.g. LKR, USD)")
        return clean

    @field_validator("campaign_name")
    @classmethod
    def validate_campaign_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        if not clean:
            raise ValueError("Campaign name cannot be blank")
        return clean


class ActivityCampaignResponse(BaseModel):
    """Response representation of an activity sponsored campaign (B16)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

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
    activity_id: int | None = None
    activity_title: str | None = None
    targeting_scope: str = "provider"
    activity_targeting_status: str = "BLOCKED_BY_SCHEMA"


class ActivityCampaignPreviewRequest(BaseModel):
    """Request payload to preview activity sponsored campaign performance and reach (B16)."""

    model_config = ConfigDict(extra="forbid")

    activity_id: Annotated[int, Field(ge=1, description="Targeted activity ID")]
    budget: Annotated[Decimal, Field(gt=Decimal("0.00"), description="Total promotion budget")]
    currency: Annotated[str, Field(default="LKR", min_length=3, max_length=3)] = "LKR"
    starts_at: datetime
    ends_at: datetime


class ActivityCampaignPreviewResponse(BaseModel):
    """Preview simulation of activity sponsored campaign reach and budget burn rate (B16)."""

    model_config = ConfigDict(extra="ignore")

    activity_id: int
    activity_title: str
    budget: Decimal
    currency: str
    duration_days: float
    daily_budget: Decimal
    estimated_impressions: int
    estimated_clicks: int
    starts_at: datetime
    ends_at: datetime
    is_eligible: bool
    ineligibility_reason: str | None = None
    targeting_scope: str = "activity"
    activity_targeting_status: str = "PREVIEW_ONLY_MIGRATION_PENDING"
    is_simulation: bool = True
    simulation_notice: str = (
        "Campaign reach and click projections are simulated estimates based on budget and algorithmic averages, "
        "not guaranteed delivery outcomes. Persistent activity-specific targeting is pending Migration 030/041."
    )


class ActivityCampaignPagination(BaseModel):
    """Paginated list of provider activity sponsored campaigns."""

    model_config = ConfigDict(extra="ignore")

    items: list[ActivityCampaignResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# A17: Promotion Event Tracking Schemas (dbo.promotion_events)
# ==============================================================================


class ActivityPromotionEventCreate(BaseModel):
    """Payload for recording a public activity promotion event (A17)."""

    model_config = ConfigDict(extra="forbid")

    event_type: Annotated[
        Literal["impression", "click", "inquiry"],
        Field(description="Event type to record: impression, click, inquiry"),
    ]


class ActivityPromotionEventResponse(BaseModel):
    """Response after recording a promotion event (A17)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    campaign_id: int
    event_type: str
    user_id: int | None = None
    ip_address: str | None = None
    created_at: datetime


# ==============================================================================
# B18: Provider Promotion Analytics Schemas (dbo.promotion_events)
# ==============================================================================


class ActivityCampaignAnalyticsSummary(BaseModel):
    """Performance metrics for an individual activity sponsored campaign (B18)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    campaign_id: int
    campaign_name: str
    status: str
    budget: Decimal
    spent_amount: Decimal
    currency: str
    impressions: int
    clicks: int
    inquiries: int
    bookings: int
    ctr: float
    conversion_rate: float


class ActivityProviderPromotionsAnalytics(BaseModel):
    """Provider-level aggregate performance analytics across all activity campaigns (B18)."""

    model_config = ConfigDict(extra="ignore")

    total_campaigns: int
    active_campaigns: int
    total_budget: Decimal
    total_spent: Decimal
    currency: str = "LKR"
    total_impressions: int
    total_clicks: int
    total_inquiries: int
    total_bookings: int
    overall_ctr: float
    overall_conversion_rate: float
    campaigns: list[ActivityCampaignAnalyticsSummary]
