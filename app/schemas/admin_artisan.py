"""
Pydantic schemas for Admin Artisan & Small Business Moderation (Phase P1 - Step 1).
Defines request payloads and review response schemas for admin listing approvals,
rejections, suspensions, and showcase product moderation.
"""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtisanRejectionRequest(BaseModel):
    """Payload for rejecting an artisan business listing or showcase product."""
    reason: str = Field(..., min_length=3, max_length=1000, description="Mandatory reason for rejection")

    @field_validator("reason")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 3:
            raise ValueError("Rejection reason must contain at least 3 characters")
        return trimmed


class ArtisanSuspendRequest(BaseModel):
    """Payload for suspending an artisan business listing."""
    reason: str | None = Field(default=None, max_length=1000, description="Optional administrative reason for suspension")

    @field_validator("reason")
    @classmethod
    def trim_optional(cls, value: str | None) -> str | None:
        if value is not None:
            trimmed = value.strip()
            return trimmed if trimmed else None
        return None


class AdminArtisanReviewResponse(BaseModel):
    """Standardized action response for admin moderation operations."""
    success: bool
    id: int
    entity_type: str  # 'artisan_listing' or 'showcase_item'
    approval_status: str
    rejection_reason: str | None = None
    message: str
    reviewed_at: datetime | None = None


class AdminArtisanListingItemResponse(BaseModel):
    """Admin summary view of an artisan listing with provider audit context."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    craft_category: str
    district: str
    city: str
    is_active: bool
    is_verified: bool
    is_featured: bool
    approval_status: str
    verification_status: str
    rejection_reason: str | None = None
    reviewed_by: int | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    provider_business_name: str | None = None


class AdminArtisanProductItemResponse(BaseModel):
    """Admin summary view of a showcase item with parent listing context."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    artisan_listing_id: int
    title: str
    item_type: str
    price: Decimal | None = None
    currency: str
    stock_quantity: int = 0
    sku: str | None = None
    is_available: bool
    is_active: bool
    approval_status: str
    created_at: datetime
    updated_at: datetime
    listing_title: str | None = None


class AdminArtisanReservationExpiryResponse(BaseModel):
    """Response schema for administrative reservation auto-expiry batch runs."""
    scanned_count: int
    expired_count: int
    skipped_count: int
    restored_stock_total: int
    message: str


# ==============================================================================
# Admin Basic Reports Schemas (Phase P2 Step 16)
# ==============================================================================

class AdminArtisanBusinessReportSummary(BaseModel):
    """Summary KPI metrics for artisan businesses."""
    total_businesses: int
    approved_count: int
    pending_count: int
    rejected_count: int
    suspended_count: int
    verified_count: int
    featured_count: int


class AdminArtisanProductReportSummary(BaseModel):
    """Summary KPI metrics for showcase products."""
    total_products: int
    active_count: int
    inactive_count: int
    out_of_stock_count: int
    approved_count: int
    pending_count: int
    rejected_count: int


class AdminArtisanReservationReportSummary(BaseModel):
    """Summary KPI metrics for artisan pickup reservations."""
    total_reservations: int
    pending_count: int
    confirmed_count: int
    ready_count: int
    completed_count: int
    cancelled_count: int
    expired_count: int
    total_collected_lkr: Decimal


class AdminArtisanPromotionReportSummary(BaseModel):
    """Summary KPI metrics for artisan discount promotions."""
    total_promotions: int
    active_count: int
    inactive_count: int


class AdminArtisanReportsSummaryResponse(BaseModel):
    """Combined administrative summary report across all four domains."""
    businesses: AdminArtisanBusinessReportSummary
    products: AdminArtisanProductReportSummary
    reservations: AdminArtisanReservationReportSummary
    promotions: AdminArtisanPromotionReportSummary
    generated_at: datetime


# ==============================================================================
# Admin Review Moderation Schemas (Phase P2 Step 16)
# ==============================================================================

class AdminArtisanReviewItemResponse(BaseModel):
    """Detailed review response schema for administrative audit and moderation."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    listing_title: str
    user_id: int
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool
    is_published: bool
    created_at: datetime
    updated_at: datetime


class AdminArtisanReviewPaginationResponse(BaseModel):
    """Paginated response for administrative review moderation list."""
    items: list[AdminArtisanReviewItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminArtisanReviewModerationRequest(BaseModel):
    """Payload to toggle review publication visibility."""
    is_published: bool
    moderation_reason: str | None = Field(default=None, max_length=500, description="Optional admin moderation reason")


class AdminArtisanReviewModerationResponse(BaseModel):
    """Response confirming review moderation action."""
    success: bool
    review_id: int
    is_published: bool
    message: str
    moderated_at: datetime
