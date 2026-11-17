"""
Pydantic v2 schemas for Module 04 — Eco-Tourism Sponsored Promotions & Packages.

Phase P1.1B:
- EcoPromotionPackageCreate: Validation for new advertising package creation.
- EcoPromotionPackageUpdate: Validation for updating configurable package fields.
- EcoPromotionPackageResponse: Serialized representation of a promotion package.
- EcoPromotionPackageToggleRequest: Request schema for toggling package active status.
- EcoPromotionPackagePagination: Paginated list response for admin package catalog.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.models.eco_promotion import (
    VALID_CAMPAIGN_STATUSES,
    VALID_PAYMENT_STATUSES,
    VALID_PLACEMENT_TIERS,
)

PlacementTierLiteral = Literal["standard", "featured", "spotlight"]


class EcoPromotionPackageBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Unique advertising package name",
    )
    description: str | None = Field(
        None,
        max_length=500,
        description="Optional public-facing package description",
    )
    duration_days: int = Field(
        ...,
        gt=0,
        le=365,
        description="Duration in calendar days (must be positive)",
    )
    price: Decimal = Field(
        ...,
        ge=Decimal("0.00"),
        decimal_places=2,
        max_digits=12,
        description="Package price in LKR (must be non-negative)",
    )
    currency: str = Field(
        "LKR",
        description="Currency code (defaults to LKR)",
    )
    placement_tier: PlacementTierLiteral = Field(
        "standard",
        description="Placement visibility tier: standard, featured, or spotlight",
    )
    is_active: bool = Field(
        True,
        description="Whether package is currently active for provider selection",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Package name must be at least 2 characters long")
        return cleaned

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if cleaned != "LKR":
            raise ValueError("Only 'LKR' currency is currently supported")
        return cleaned

    @field_validator("placement_tier")
    @classmethod
    def validate_placement_tier(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_PLACEMENT_TIERS:
            raise ValueError(
                f"Invalid placement tier '{v}'. Allowed: {', '.join(VALID_PLACEMENT_TIERS)}"
            )
        return cleaned


class EcoPromotionPackageCreate(EcoPromotionPackageBase):
    """Schema for admin package creation."""
    pass


class EcoPromotionPackageUpdate(BaseModel):
    """Schema for admin package update with optional fields."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(
        None,
        min_length=2,
        max_length=100,
        description="Updated unique advertising package name",
    )
    description: str | None = Field(
        None,
        max_length=500,
        description="Updated package description",
    )
    duration_days: int | None = Field(
        None,
        gt=0,
        le=365,
        description="Updated duration in calendar days (must be positive)",
    )
    price: Decimal | None = Field(
        None,
        ge=Decimal("0.00"),
        decimal_places=2,
        max_digits=12,
        description="Updated package price in LKR (must be non-negative)",
    )
    currency: str | None = Field(
        None,
        description="Updated currency code (must be LKR)",
    )
    placement_tier: PlacementTierLiteral | None = Field(
        None,
        description="Updated placement visibility tier",
    )
    is_active: bool | None = Field(
        None,
        description="Updated package active status",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            if len(cleaned) < 2:
                raise ValueError("Package name must be at least 2 characters long")
            return cleaned
        return None

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip().upper()
            if cleaned != "LKR":
                raise ValueError("Only 'LKR' currency is currently supported")
            return cleaned
        return None

    @field_validator("placement_tier")
    @classmethod
    def validate_placement_tier(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip().lower()
            if cleaned not in VALID_PLACEMENT_TIERS:
                raise ValueError(
                    f"Invalid placement tier '{v}'. Allowed: {', '.join(VALID_PLACEMENT_TIERS)}"
                )
            return cleaned
        return None


class EcoPromotionPackageToggleRequest(BaseModel):
    """Schema for explicitly requesting package active/inactive state."""
    model_config = ConfigDict(extra="forbid")

    is_active: bool | None = Field(
        None,
        description="Optional target active state. If omitted, current state is inverted.",
    )


class EcoPromotionPackageResponse(BaseModel):
    """Serialized package representation for both admin and public catalogs."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    duration_days: int
    price: Decimal
    currency: str
    placement_tier: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class EcoPromotionPackagePagination(BaseModel):
    """Paginated list response for admin package catalog."""
    items: list[EcoPromotionPackageResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


# ==============================================================================
# Phase P1.1C: Campaign Request & Moderation Schemas
# ==============================================================================


class EcoSponsoredPromotionCreate(BaseModel):
    """
    Request schema for eco providers requesting a sponsored promotion campaign.
    Forbids client-supplied payment, status, price, or review fields (`extra='forbid'`).
    """
    model_config = ConfigDict(extra="forbid")

    eco_experience_id: int = Field(
        ..., gt=0, description="Target eco experience ID owned by the provider"
    )
    package_id: int = Field(
        ..., gt=0, description="Selected active promotion package ID"
    )


class EcoCampaignApproveRequest(BaseModel):
    """Request schema for admin editorial approval of a promotion campaign."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    notes: str | None = Field(
        None, max_length=1000, description="Optional administrative review notes"
    )


class EcoCampaignRejectRequest(BaseModel):
    """Request schema for admin rejection of a promotion campaign."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="Mandatory meaningful rejection reason",
    )

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Rejection reason must be at least 5 characters long")
        return cleaned


class EcoSponsoredPromotionResponse(BaseModel):
    """Response schema for eco provider viewing their own campaign."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    provider_profile_id: int
    package_id: int
    package_price_snapshot: Decimal
    currency_snapshot: str
    duration_days_snapshot: int
    campaign_status: str
    payment_status: str
    payment_reference: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    requested_at: datetime
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    created_at: datetime
    updated_at: datetime

    # Informational display helpers
    experience_title: str | None = None
    package_name: str | None = None
    placement_tier: str | None = None
    is_currently_active: bool = False


class EcoSponsoredPromotionAdminResponse(EcoSponsoredPromotionResponse):
    """Extended response schema for admin campaign review and audit."""
    reviewed_by: int | None = None
    reviewer_email: str | None = None
    provider_business_name: str | None = None


class EcoSponsoredPromotionPagination(BaseModel):
    """Paginated list response for provider campaign history."""
    items: list[EcoSponsoredPromotionResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


class EcoSponsoredPromotionAdminPagination(BaseModel):
    """Paginated list response for admin campaign moderation."""
    items: list[EcoSponsoredPromotionAdminResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


# ==============================================================================
# Phase P1.1E: Sponsored Promotions Analytics Schemas
# ==============================================================================


class EcoPromotionTrackRequest(BaseModel):
    """Request payload for recording a visible impression or click."""
    model_config = ConfigDict(extra="forbid")

    token: str = Field(
        ...,
        min_length=10,
        description="Cryptographic tracking token generated from a boosted sponsored placement",
    )


class EcoPromotionTrackResponse(BaseModel):
    """Response returned upon successfully recording a tracking event."""
    status: str = "recorded"
    event_type: str
    campaign_id: int
    eco_experience_id: int
    metric_date: date
    impressions_count: int
    clicks_count: int


class EcoPromotionDailyMetric(BaseModel):
    """Daily aggregated metrics for a sponsored promotion."""
    metric_date: date
    impressions_count: int = Field(ge=0)
    clicks_count: int = Field(ge=0)
    ctr_percent: Decimal = Field(
        ge=Decimal("0.00"),
        decimal_places=2,
        description="Click-through rate percentage: (clicks / impressions) * 100",
    )


class EcoPromotionCampaignAnalyticsResponse(BaseModel):
    """Detailed analytics response for a specific provider promotion campaign."""
    campaign_id: int
    eco_experience_id: int
    experience_title: str | None = None
    package_name: str | None = None
    campaign_status: str
    payment_status: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_currently_active: bool = False
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    ctr_percent: Decimal = Field(ge=Decimal("0.00"), decimal_places=2)
    start_date: date | None = None
    end_date: date | None = None
    daily_metrics: list[EcoPromotionDailyMetric] = Field(default_factory=list)


class EcoPromotionCampaignSummaryAnalytics(BaseModel):
    """Summary metrics for a single campaign within provider analytics overview."""
    campaign_id: int
    eco_experience_id: int
    experience_title: str | None = None
    package_name: str | None = None
    campaign_status: str
    payment_status: str
    is_currently_active: bool = False
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    ctr_percent: Decimal = Field(ge=Decimal("0.00"), decimal_places=2)


class EcoPromotionProviderAnalyticsResponse(BaseModel):
    """Aggregated performance dashboard for an eco provider across all owned campaigns."""
    provider_profile_id: int
    total_campaigns: int = Field(ge=0)
    active_campaigns_count: int = Field(ge=0)
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    average_ctr_percent: Decimal = Field(ge=Decimal("0.00"), decimal_places=2)
    start_date: date | None = None
    end_date: date | None = None
    campaigns: list[EcoPromotionCampaignSummaryAnalytics] = Field(default_factory=list)
    daily_metrics: list[EcoPromotionDailyMetric] = Field(default_factory=list)


class EcoPromotionAdminAnalyticsItem(BaseModel):
    """Campaign-level metrics row within admin platform analytics."""
    campaign_id: int
    provider_profile_id: int
    provider_business_name: str | None = None
    eco_experience_id: int
    experience_title: str | None = None
    package_name: str | None = None
    campaign_status: str
    payment_status: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    is_currently_active: bool = False
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    ctr_percent: Decimal = Field(ge=Decimal("0.00"), decimal_places=2)


class EcoPromotionAdminAnalyticsResponse(BaseModel):
    """Platform-wide aggregated sponsored promotion analytics for administrators."""
    total_campaigns: int = Field(ge=0)
    total_impressions: int = Field(ge=0)
    total_clicks: int = Field(ge=0)
    overall_ctr_percent: Decimal = Field(ge=Decimal("0.00"), decimal_places=2)
    start_date: date | None = None
    end_date: date | None = None
    items: list[EcoPromotionAdminAnalyticsItem] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)
