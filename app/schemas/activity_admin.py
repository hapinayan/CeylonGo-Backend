from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActivityApprovalRequest(BaseModel):
    """Admin approval request."""

    model_config = ConfigDict(extra="forbid")


class ActivityRejectionRequest(BaseModel):
    """Admin rejection request with a mandatory reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=10,
        max_length=500,
        description="Reason for rejecting the activity",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()

        if len(value) < 10:
            raise ValueError(
                "Rejection reason must contain at least 10 characters"
            )

        return value


class ActivitySuspensionRequest(BaseModel):
    """Admin suspension request with a mandatory reason (C03)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=10,
        max_length=500,
        description="Reason for suspending the activity",
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()

        if len(value) < 10:
            raise ValueError(
                "Suspension reason must contain at least 10 characters"
            )

        return value


class ActivityUnsuspensionRequest(BaseModel):
    """Admin reinstatement / unsuspension request with optional reason (C03)."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Optional reason or notes for reinstating the activity",
    )

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value if value else None


# ==============================================================================
# Admin Booking Monitoring Schemas (C04)
# ==============================================================================


class AdminParticipantSummary(BaseModel):
    """Safe participant representation strictly excluding sensitive personal data."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    full_name: str
    age: int | None = None


class AdminBookingStatusHistoryItem(BaseModel):
    """Audit entry representing a lifecycle transition in booking_status_history."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    from_status: str | None = None
    to_status: str
    changed_by: int | None = None
    notes: str | None = None
    created_at: datetime


class AdminActivityBookingSummary(BaseModel):
    """Summary item for platform-wide admin booking monitoring listing (C04)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    booking_reference: str | None = None
    activity_id: int
    activity_title: str
    provider_profile_id: int
    provider_business_name: str | None = None
    traveler_id: int
    traveler_name: str
    traveler_email: str
    booking_status: str
    created_at: datetime
    slot_id: int
    starts_at: datetime
    ends_at: datetime
    participants: int
    total_price: Decimal
    currency: str
    payment_status: str
    payment_method: str
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None


class AdminActivityBookingPagination(BaseModel):
    """Pagination metadata for admin booking listing."""

    model_config = ConfigDict(extra="ignore")

    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class AdminActivityBookingListResponse(BaseModel):
    """Paginated list response for platform-wide booking monitoring."""

    model_config = ConfigDict(extra="ignore")

    items: list[AdminActivityBookingSummary]
    pagination: AdminActivityBookingPagination


class AdminActivityBookingDetail(BaseModel):
    """Detailed view of an activity booking for authorized administrators (C04)."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    booking_reference: str | None = None

    # Activity Information
    activity_id: int
    activity_title: str
    activity_category: str | None = None
    district: str | None = None
    city: str | None = None

    # Provider Information
    provider_profile_id: int
    provider_business_name: str
    provider_phone: str | None = None

    # Traveler Information (strictly permitted display information)
    traveler_id: int
    traveler_name: str
    traveler_email: str

    # Slot / Session Information
    slot_id: int
    starts_at: datetime
    ends_at: datetime

    # Participant Details (Sensitive fields excluded)
    participants: int
    participants_list: list[AdminParticipantSummary] = []

    # Financial & Payment Information
    total_price: Decimal
    currency: str
    payment_status: str
    payment_method: str

    # Booking Status & Audit
    booking_status: str
    created_at: datetime
    updated_at: datetime | None = None
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    status_history: list[AdminBookingStatusHistoryItem] = []


# ==============================================================================
# Platform-Wide Admin Reporting Schemas (C07)
# ==============================================================================


class AdminActivityInventoryOverview(BaseModel):
    """Platform-wide activity inventory aggregated by approval and publication status."""

    model_config = ConfigDict(extra="ignore")

    total_activities: int = Field(ge=0)
    approved_activities: int = Field(ge=0)
    pending_activities: int = Field(ge=0)
    draft_activities: int = Field(ge=0)
    suspended_activities: int = Field(ge=0)
    rejected_activities: int = Field(ge=0)
    active_published_activities: int = Field(ge=0)
    inactive_activities: int = Field(ge=0)


class AdminActivityBookingOverview(BaseModel):
    """Platform-wide activity booking lifecycle counts and conversion metrics."""

    model_config = ConfigDict(extra="ignore")

    total_bookings: int = Field(ge=0)
    confirmed_bookings: int = Field(ge=0)
    completed_bookings: int = Field(ge=0)
    pending_bookings: int = Field(ge=0)
    cancelled_bookings: int = Field(ge=0)
    rejected_bookings: int = Field(ge=0)
    total_participants: int = Field(ge=0)
    confirmed_participants: int = Field(ge=0)
    booking_conversion_rate: float = Field(ge=0.0, le=100.0)
    cancellation_rate: float = Field(ge=0.0, le=100.0)


class AdminActivityCapacityUtilization(BaseModel):
    """Aggregate scheduled slot capacity and occupancy metrics across activities."""

    model_config = ConfigDict(extra="ignore")

    total_slots: int = Field(ge=0)
    active_slots: int = Field(ge=0)
    total_capacity: int = Field(ge=0)
    total_reserved: int = Field(ge=0)
    total_remaining: int = Field(ge=0)
    occupancy_rate: float = Field(ge=0.0, le=100.0)


class AdminActivityCategoryReport(BaseModel):
    """Activity and booking metrics broken down by canonical activity category."""

    model_config = ConfigDict(extra="ignore")

    category: str
    activity_count: int = Field(ge=0)
    active_activity_count: int = Field(ge=0)
    booking_count: int = Field(ge=0)
    total_participants: int = Field(ge=0)
    gross_revenue: Decimal = Field(ge=Decimal("0.00"), default=Decimal("0.00"))
    currency: str = "LKR"


class AdminActivityRegionalReport(BaseModel):
    """Activity and booking distribution aggregated by administrative district."""

    model_config = ConfigDict(extra="ignore")

    district: str
    activity_count: int = Field(ge=0)
    booking_count: int = Field(ge=0)
    total_participants: int = Field(ge=0)


class AdminActivityFinancialBreakdown(BaseModel):
    """Monetary booking aggregates partitioned strictly by currency to prevent cross-contamination."""

    model_config = ConfigDict(extra="ignore")

    currency: str
    total_gross_revenue: Decimal = Field(ge=Decimal("0.00"))
    paid_revenue: Decimal = Field(ge=Decimal("0.00"))
    unpaid_revenue: Decimal = Field(ge=Decimal("0.00"))
    refunded_amount: Decimal = Field(ge=Decimal("0.00"))


class AdminTopActivitySummary(BaseModel):
    """Top-performing activity summary for administrative ranking."""

    model_config = ConfigDict(extra="ignore")

    activity_id: int
    activity_title: str
    category: str
    district: str
    provider_profile_id: int
    provider_business_name: str | None = None
    booking_count: int = Field(ge=0)
    total_participants: int = Field(ge=0)
    gross_revenue: Decimal = Field(ge=Decimal("0.00"))
    currency: str = "LKR"


class AdminTopProviderSummary(BaseModel):
    """Top-performing activity provider summary for platform monitoring."""

    model_config = ConfigDict(extra="ignore")

    provider_profile_id: int
    business_name: str
    total_activities: int = Field(ge=0)
    active_activities: int = Field(ge=0)
    booking_count: int = Field(ge=0)
    total_participants: int = Field(ge=0)
    gross_revenue: Decimal = Field(ge=Decimal("0.00"))
    currency: str = "LKR"


class AdminActivityPlatformReportResponse(BaseModel):
    """Comprehensive executive activity platform report response (C07)."""

    model_config = ConfigDict(extra="ignore")

    generated_at: datetime
    date_from: date | None = None
    date_to: date | None = None
    activity_inventory: AdminActivityInventoryOverview
    booking_overview: AdminActivityBookingOverview
    capacity_utilization: AdminActivityCapacityUtilization
    category_distribution: list[AdminActivityCategoryReport] = []
    regional_distribution: list[AdminActivityRegionalReport] = []
    financial_overview: list[AdminActivityFinancialBreakdown] = []
    top_activities: list[AdminTopActivitySummary] = []
    top_providers: list[AdminTopProviderSummary] = []


# ==============================================================================
# Admin Promotion Management Schemas (C06)
# ==============================================================================

ADMIN_CAMPAIGN_VALID_ACTIONS = ("approve", "reject", "pause", "resume")

# Allowed physical campaign statuses on SQL Server 2025 Express
PHYSICAL_CAMPAIGN_STATUSES = (
    "draft",
    "pending_approval",
    "active",
    "paused",
    "completed",
    "cancelled",
)

# Eligible source statuses for each admin review action
CAMPAIGN_ACTION_ELIGIBLE_STATUSES: dict[str, tuple[str, ...]] = {
    "approve": ("pending_approval",),
    "reject": ("pending_approval",),
    "pause": ("active",),
    "resume": ("paused",),
}


class AdminCampaignReviewRequest(BaseModel):
    """
    Reusable validation schema for administrative campaign moderation decisions (C06).
    Prepared for future Phase 5D-4 write workflows.
    """

    model_config = ConfigDict(extra="forbid")

    action: Annotated[
        str,
        Field(description="Review decision action: approve, reject, pause, resume"),
    ]
    reason: Annotated[
        str | None,
        Field(
            default=None,
            max_length=500,
            description="Mandatory reason when rejecting; optional notes for other actions",
        ),
    ] = None
    notes: Annotated[
        str | None,
        Field(
            default=None,
            max_length=1000,
            description="Optional internal administrator notes",
        ),
    ] = None

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        clean = v.strip().lower()
        if clean not in ADMIN_CAMPAIGN_VALID_ACTIONS:
            raise ValueError(
                f"Action '{clean}' is not supported. Must be one of: {', '.join(ADMIN_CAMPAIGN_VALID_ACTIONS)}"
            )
        return clean

    @field_validator("reason")
    @classmethod
    def sanitize_reason(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        return clean if clean else None

    @field_validator("notes")
    @classmethod
    def sanitize_notes(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        return clean if clean else None

    @model_validator(mode="after")
    def validate_rejection_reason(self) -> "AdminCampaignReviewRequest":
        if self.action == "reject":
            if not self.reason or len(self.reason) < 10:
                raise ValueError("Rejection reason is mandatory and must contain at least 10 characters")
        return self

    def validate_status_eligibility(self, current_status: str) -> None:
        """
        Validate whether the campaign's current status permits the requested action.
        Raises ValueError if status is ineligible.
        """
        eligible = CAMPAIGN_ACTION_ELIGIBLE_STATUSES.get(self.action, ())
        normalized = current_status.strip().lower()
        if normalized not in eligible:
            raise ValueError(
                f"Cannot execute '{self.action}' on campaign with status '{current_status}'. "
                f"Eligible status(es): {', '.join(eligible)}"
            )


# ==============================================================================
# Admin Activity Payment Monitoring & Financial Reconciliation Schemas (C05)
# ==============================================================================


class AdminActivityPaymentOverviewResponse(BaseModel):
    """
    Platform-wide administrative financial overview for activity bookings (C05).
    Aggregates captured online payments, unverified pay-at-location cash,
    earned/reversed commission, provider payable, settlements, and refund liabilities.
    """

    model_config = ConfigDict(extra="ignore")

    captured_online_revenue: Decimal = Field(ge=0, decimal_places=2)
    online_transaction_count: int = Field(ge=0)
    pay_at_location_unverified_value: Decimal = Field(
        ge=0,
        decimal_places=2,
        description="Booking value with pay_at_location method, representing unverified on-site cash collections rather than confirmed platform revenue.",
    )
    pay_at_location_booking_count: int = Field(ge=0)
    platform_commission_earned: Decimal = Field(ge=0, decimal_places=2)
    commission_reversed: Decimal = Field(ge=0, decimal_places=2)
    net_platform_commission: Decimal = Field(ge=0, decimal_places=2)
    provider_payable_amount: Decimal = Field(ge=0, decimal_places=2)
    total_settled: Decimal = Field(ge=0, decimal_places=2)
    pending_settlement_liability: Decimal = Field(ge=0, decimal_places=2)
    completed_refunds_amount: Decimal = Field(ge=0, decimal_places=2)
    completed_refunds_count: int = Field(ge=0)
    pending_refund_obligations: Decimal = Field(ge=0, decimal_places=2)
    pending_refunds_count: int = Field(ge=0)
    failed_payment_count: int = Field(ge=0)
    pending_payment_count: int = Field(ge=0)
    refunded_transaction_count: int = Field(ge=0)
    net_platform_revenue: Decimal = Field(ge=0, decimal_places=2)
    outstanding_payout_exposure: Decimal = Field(ge=0, decimal_places=2)
    currency: str = "LKR"
    date_from: date | None = None
    date_to: date | None = None
    provider_profile_id: int | None = None
    activity_id: int | None = None


class AdminActivityPaymentTransactionItem(BaseModel):
    """Detailed activity payment transaction record for admin audit and monitoring (C05)."""

    model_config = ConfigDict(extra="ignore")

    transaction_id: int
    gateway_transaction_id: str | None = None
    booking_id: int
    booking_reference: str
    activity_id: int
    activity_title: str
    provider_profile_id: int
    provider_business_name: str
    payment_method: str
    payment_status: str
    booking_status: str
    amount: Decimal
    gross_amount: Decimal | None = None
    currency: str = "LKR"
    commission_rate: Decimal | None = None
    commission_amount: Decimal | None = None
    provider_payable_amount: Decimal | None = None
    refund_status: str | None = None
    refund_amount: Decimal | None = None
    paid_at: datetime | None = None
    created_at: datetime


class AdminActivityPaymentTransactionListResponse(BaseModel):
    """Paginated list of activity payment transactions for administrative review (C05)."""

    model_config = ConfigDict(extra="ignore")

    items: list[AdminActivityPaymentTransactionItem]
    total: int
    page: int
    page_size: int


class AdminActivityPaymentReconciliationResponse(BaseModel):
    """
    Read-only activity payment reconciliation report detecting anomalies,
    mismatches, unreviewed refunds, and pending gateway verifications (C05).
    """

    model_config = ConfigDict(extra="ignore")

    healthy: bool
    total_discrepancies: int
    is_live_gateway_checked: bool = False
    unmatched_paid_bookings: list[dict[str, Any]] = []
    inconsistent_status_transactions: list[dict[str, Any]] = []
    duplicate_gateway_references: list[dict[str, Any]] = []
    amount_currency_mismatches: list[dict[str, Any]] = []
    unreviewed_refund_requests: list[dict[str, Any]] = []
    excessive_settlements: list[dict[str, Any]] = []
    gateway_verification_pending: list[dict[str, Any]] = []
    generated_at: datetime
