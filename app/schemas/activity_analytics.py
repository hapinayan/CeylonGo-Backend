from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActivityAnalyticsOverview(BaseModel):
    """Activity listing status counts for the provider."""

    model_config = ConfigDict(extra="ignore")

    total_activities: int
    active_activities: int
    approved_activities: int
    pending_activities: int
    draft_activities: int
    suspended_activities: int
    rejected_activities: int


class BookingAnalyticsOverview(BaseModel):
    """Activity booking performance and conversion statistics."""

    model_config = ConfigDict(extra="ignore")

    total_bookings: int
    confirmed_bookings: int
    completed_bookings: int
    pending_bookings: int
    cancelled_bookings: int
    rejected_bookings: int
    total_participants: int
    confirmed_participants: int
    cancellation_rate: float = Field(
        ge=0.0,
        le=100.0,
        description="Cancellation rate percentage (0.0 to 100.0)",
    )


class CurrencyRevenueBreakdown(BaseModel):
    """Monetary breakdown for a specific currency."""

    model_config = ConfigDict(extra="ignore")

    currency: str
    total_gross_revenue: Decimal
    paid_revenue: Decimal
    unpaid_revenue: Decimal
    refunded_amount: Decimal = Decimal("0.00")


class RevenueAnalyticsOverview(BaseModel):
    """Financial breakdown for activity bookings."""

    model_config = ConfigDict(extra="ignore")

    total_gross_revenue: Decimal = Field(
        description="Gross booking value of non-cancelled, non-rejected bookings",
    )
    paid_revenue: Decimal = Field(
        description="Collected value of completed payments (payment_status == 'paid')",
    )
    unpaid_revenue: Decimal = Field(
        description="Uncollected value of pending/unpaid bookings (payment_status in ('unpaid', 'pending'))",
    )
    refunded_amount: Decimal = Field(
        default=Decimal("0.00"),
        description="Total refunded or refund-pending amount",
    )
    currency: str = "LKR"
    by_currency: list[CurrencyRevenueBreakdown] = []


class CapacityAnalyticsOverview(BaseModel):
    """Slot scheduling and capacity utilization metrics."""

    model_config = ConfigDict(extra="ignore")

    total_slots_offered: int
    total_capacity_offered: int
    total_capacity_reserved: int
    occupancy_rate: float = Field(
        ge=0.0,
        le=100.0,
        description="Capacity occupancy percentage (0.0 to 100.0)",
    )


class ActivityPerformanceMetric(BaseModel):
    """Per-activity granular performance metrics."""

    model_config = ConfigDict(extra="ignore")

    activity_id: int
    title: str
    category: str
    approval_status: str
    is_active: bool
    total_bookings: int
    confirmed_bookings: int
    total_participants: int
    gross_revenue: Decimal
    average_rating: float | None = None
    total_reviews: int = 0


class ProviderActivityAnalyticsResponse(BaseModel):
    """Comprehensive provider activity analytics response (B01)."""

    model_config = ConfigDict(extra="ignore")

    provider_profile_id: int
    business_name: str
    date_from: date | None = None
    date_to: date | None = None
    activity_filter_id: int | None = None

    activities: ActivityAnalyticsOverview
    bookings: BookingAnalyticsOverview
    financials: RevenueAnalyticsOverview
    capacity: CapacityAnalyticsOverview
    breakdown: list[ActivityPerformanceMetric] = []
