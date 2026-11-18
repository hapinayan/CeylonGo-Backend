from decimal import Decimal
from pydantic import BaseModel, ConfigDict


class HomestayProviderListingSummary(BaseModel):
    total: int = 0
    approved: int = 0
    pending_approval: int = 0
    draft: int = 0
    rejected: int = 0
    suspended: int = 0


class HomestayProviderBookingSummary(BaseModel):
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    completed: int = 0
    cancelled: int = 0
    rejected: int = 0


class HomestayProviderFinancialSummary(BaseModel):
    online_earnings: Decimal = Decimal("0.00")
    cash_payments_received: Decimal = Decimal("0.00")
    total_earnings: Decimal = Decimal("0.00")
    pending_settlements_amount: Decimal = Decimal("0.00")
    pending_settlements_count: int = 0
    currency: str = "LKR"


class HomestayProviderMarketingSummary(BaseModel):
    active_discount_offers: int = 0
    active_sponsored_campaigns: int = 0


class HomestayProviderRatingSummary(BaseModel):
    average_rating: float = 0.0
    total_reviews: int = 0


class HomestayProviderDashboardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_id: int
    business_name: str | None = None
    listings: HomestayProviderListingSummary
    bookings: HomestayProviderBookingSummary
    finances: HomestayProviderFinancialSummary
    marketing: HomestayProviderMarketingSummary
    ratings: HomestayProviderRatingSummary


class HomestayAdminListingSummary(BaseModel):
    total: int = 0
    approved: int = 0
    pending_approval: int = 0
    draft: int = 0
    rejected: int = 0
    suspended: int = 0


class HomestayAdminBookingSummary(BaseModel):
    total: int = 0
    pending: int = 0
    confirmed: int = 0
    completed: int = 0
    cancelled: int = 0
    rejected: int = 0


class HomestayAdminPaymentSummary(BaseModel):
    total_online_payments_count: int = 0
    total_online_payments_volume: Decimal = Decimal("0.00")
    pending_refunds_count: int = 0
    pending_refunds_volume: Decimal = Decimal("0.00")
    currency: str = "LKR"


class HomestayAdminSettlementSummary(BaseModel):
    pending_settlements_count: int = 0
    pending_settlements_volume: Decimal = Decimal("0.00")
    completed_settlements_count: int = 0
    completed_settlements_volume: Decimal = Decimal("0.00")
    currency: str = "LKR"


class HomestayAdminCampaignSummary(BaseModel):
    total_campaigns: int = 0
    active_campaigns: int = 0
    pending_approval_campaigns: int = 0
    total_campaign_budget: Decimal = Decimal("0.00")
    currency: str = "LKR"


class HomestayAdminReviewSummary(BaseModel):
    total_reviews: int = 0
    pending_moderation: int = 0
    approved_reviews: int = 0
    rejected_reviews: int = 0


class HomestayAdminDashboardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listings: HomestayAdminListingSummary
    bookings: HomestayAdminBookingSummary
    payments: HomestayAdminPaymentSummary
    settlements: HomestayAdminSettlementSummary
    campaigns: HomestayAdminCampaignSummary
    reviews: HomestayAdminReviewSummary
