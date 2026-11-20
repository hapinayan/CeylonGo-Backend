from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field


class ArtisanBusinessOverview(BaseModel):
    """Breakdown of listings owned by the artisan provider."""
    total_listings: int = 0
    approved_listings: int = 0
    pending_listings: int = 0
    rejected_listings: int = 0
    suspended_listings: int = 0
    active_listings: int = 0
    inactive_listings: int = 0
    verified_listings: int = 0
    unverified_listings: int = 0


class ArtisanProductOverview(BaseModel):
    """Breakdown of showcase products and inventory."""
    total_products: int = 0
    active_products: int = 0
    inactive_products: int = 0
    available_products: int = 0
    in_stock_products: int = 0
    out_of_stock_products: int = 0
    pending_products: int = 0
    approved_products: int = 0
    rejected_products: int = 0


class ArtisanReservationOverview(BaseModel):
    """Breakdown of product reservations across all business listings."""
    total_reservations: int = 0
    pending_reservations: int = 0
    confirmed_reservations: int = 0
    ready_for_pickup_reservations: int = 0
    completed_reservations: int = 0
    cancelled_reservations: int = 0
    expired_reservations: int = 0


class ArtisanFinancialSummary(BaseModel):
    """Summary of recorded completed reservation values (no invented figures)."""
    recorded_completed_pickup_value_lkr: Decimal = Decimal("0.00")
    currency: str = "LKR"
    note: str = "Total recorded pickup value for completed reservations. Offline cash payments are recorded upon pickup."


class ArtisanDashboardRecentReservation(BaseModel):
    """Compact summary of a recent product reservation."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    showcase_item_id: int
    quantity: int
    total_price: Decimal
    currency: str = "LKR"
    reservation_status: str
    pickup_date: date | None = None
    created_at: datetime
    item_title: str | None = None
    listing_title: str | None = None
    traveler_name: str | None = None


class ArtisanDashboardRecentProduct(BaseModel):
    """Compact summary of a recently created showcase item."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    title: str
    item_type: str
    price: Decimal | None = None
    currency: str = "LKR"
    stock_quantity: int = 0
    is_active: bool
    is_available: bool
    approval_status: str
    created_at: datetime
    listing_title: str | None = None


class ArtisanPendingApprovalSummary(BaseModel):
    """Summary of items awaiting administrator review."""
    pending_listings_count: int = 0
    pending_products_count: int = 0
    requires_action: bool = False


class ArtisanPromotionItem(BaseModel):
    """Individual promotion item metadata."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    showcase_item_id: int | None = None
    title: str
    promo_code: str | None = None
    discount_percentage: Decimal | None = None
    discount_amount: Decimal | None = None
    start_date: datetime
    end_date: datetime
    is_active: bool
    listing_title: str | None = None
    showcase_item_title: str | None = None


class ArtisanPromotionOverview(BaseModel):
    """Overview of discounts and promotions linked to the provider's listings."""
    total_promotions: int = 0
    active_promotions_count: int = 0
    active_promotions: list[ArtisanPromotionItem] = Field(default_factory=list)


class ArtisanDashboardResponse(BaseModel):
    """Full structured provider dashboard summary response."""
    provider_id: int
    provider_business_name: str | None = None
    business_overview: ArtisanBusinessOverview
    product_overview: ArtisanProductOverview
    reservation_overview: ArtisanReservationOverview
    financial_summary: ArtisanFinancialSummary
    recent_reservations: list[ArtisanDashboardRecentReservation] = Field(default_factory=list)
    recent_products: list[ArtisanDashboardRecentProduct] = Field(default_factory=list)
    pending_approval: ArtisanPendingApprovalSummary
    promotions_overview: ArtisanPromotionOverview

