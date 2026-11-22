from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class WorkspacePerson(BaseModel):
    user_id: int
    name: str
    email: str


class WorkspaceBusiness(BaseModel):
    name: str
    description: str | None
    phone: str
    province: str | None
    district: str
    city: str | None


class WorkspaceNavigation(BaseModel):
    id: str
    method: Literal["GET", "POST", "PATCH"]
    path: str


class ProviderWorkspace(BaseModel):
    provider_id: int
    category: str
    personal: WorkspacePerson
    business: WorkspaceBusiness
    account_active: bool
    approval_status: str
    verification_status: Literal["approved", "unrecorded"]
    reviewed_at: datetime | None
    permissions: list[str]
    navigation: list[WorkspaceNavigation]
    unavailable_metrics: dict[str, str]


class DashboardPeriod(BaseModel):
    start_date: date | None = None
    end_date: date | None = None


class ListingMetrics(BaseModel):
    total: int
    active: int
    pending_approval: int


class BookingMetrics(BaseModel):
    total: int
    pending: int
    upcoming: int
    active: int
    completed: int
    cancelled: int


class PublishedReviews(BaseModel):
    count: int
    average_rating: Decimal | None


class ReservationSummary(BaseModel):
    booking_id: int
    listing_id: int
    status: str
    starts_on: date | None = None
    ends_on: date | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class ActivityItem(BaseModel):
    kind: Literal["listing_created", "booking_created", "booking_status_changed"]
    occurred_at: datetime
    listing_id: int
    booking_id: int | None
    status: str | None


class ActivityPage(BaseModel):
    period: DashboardPeriod
    items: list[ActivityItem]
    page: int
    page_size: int
    has_more: bool


class FinanceTotals(BaseModel):
    currency: str
    verified_revenue: Decimal = Decimal("0.00")
    cash_collections: Decimal = Decimal("0.00")
    verified_online_refunds: Decimal = Decimal("0.00")
    verified_cash_refunds: Decimal = Decimal("0.00")
    commission: Decimal = Decimal("0.00")
    provider_earnings: Decimal = Decimal("0.00")


class SettlementTotals(BaseModel):
    currency: str
    pending_count: int
    pending_amount: Decimal
    paid_count: int
    paid_amount: Decimal


class CashCollection(BaseModel):
    currency: str
    amount: Decimal


class MonthlyMetrics(BaseModel):
    month: date
    bookings: int | None
    revenue: list[FinanceTotals] | None
    sponsored_impressions: int | None
    cash_receipts: list[CashCollection] | None = None


class DashboardAnalytics(BaseModel):
    period: DashboardPeriod
    months: list[MonthlyMetrics]
    unavailable_metrics: dict[str, str]


class ProviderDashboard(BaseModel):
    provider_id: int
    category: str
    period: DashboardPeriod
    listings: ListingMetrics
    bookings: BookingMetrics | None
    finance: list[FinanceTotals] | None
    settlements: list[SettlementTotals] | None
    reviews: PublishedReviews | None
    unread_notifications: int
    recent_activity: list[ActivityItem]
    upcoming_reservations: list[ReservationSummary] | None
    sponsored_impressions: int | None
    cash_receipts: list[CashCollection] | None = None
    unavailable_metrics: dict[str, str]
