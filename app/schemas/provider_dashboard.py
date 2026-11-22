from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ==========================================
# NOTIFICATION SCHEMAS
# ==========================================

class NotificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    message: str
    notification_type: str
    reference_module: str | None = None
    reference_id: int | None = None
    is_read: bool
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    total: int
    page: int
    page_size: int
    total_pages: int


class UnreadCountResponse(BaseModel):
    unread_count: int


# ==========================================
# DASHBOARD SCHEMAS
# ==========================================

class ProviderProfileSummary(BaseModel):
    id: int
    business_name: str
    provider_type: str
    approval_status: str
    district: str


class ListingMetricsSummary(BaseModel):
    total: int = 0
    active: int = 0
    pending: int = 0


class BookingMetricsSummary(BaseModel):
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    completed: int = 0
    cancelled: int = 0


class RecentBookingSummary(BaseModel):
    id: int
    booking_date: date | datetime | None = None
    customer_name: str | None = None
    status: str
    total_price: Decimal | None = None
    currency: str = "LKR"


class SharedProviderDashboardResponse(BaseModel):
    provider: ProviderProfileSummary
    listings: ListingMetricsSummary
    bookings: BookingMetricsSummary
    recent_bookings: list[RecentBookingSummary] = []


# ==========================================
# REPORTING & ANALYTICS SCHEMAS
# ==========================================

class BookingReportSummary(BaseModel):
    total_bookings: int
    pending: int
    confirmed: int
    completed: int
    cancelled: int
    total_revenue: Decimal
    currency: str
    start_date: date | None = None
    end_date: date | None = None


# ==========================================
# SETTLEMENTS & EARNINGS SCHEMAS
# ==========================================

class ProviderSettlementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    period_start: date
    period_end: date
    gross_earnings: Decimal
    platform_commission: Decimal
    net_payable: Decimal
    currency: str
    status: str
    settlement_reference: str | None = None
    settled_at: datetime | None = None
    created_at: datetime


class ProviderSettlementListResponse(BaseModel):
    items: list[ProviderSettlementItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class ProviderEarningsSummaryResponse(BaseModel):
    gross_earnings: Decimal
    platform_commission: Decimal
    net_payable: Decimal
    currency: str
    total_settlements: int
    pending_settlements: int
    completed_settlements: int


# ==========================================
# DOCUMENT EXPIRY & ALERTS SCHEMAS (SH-07)
# ==========================================

class ExpiringDocumentItem(BaseModel):
    document_id: int
    document_type: str
    title: str
    entity_type: str  # "rental_vehicle", "tour_guide", "provider_profile"
    entity_id: int
    expiry_date: date
    days_remaining: int
    status: str  # "expired", "urgent", "expiring_soon", "valid"


class ExpiringDocumentsResponse(BaseModel):
    total_expiring: int
    expired_count: int
    urgent_count: int
    expiring_soon_count: int
    documents: list[ExpiringDocumentItem]



