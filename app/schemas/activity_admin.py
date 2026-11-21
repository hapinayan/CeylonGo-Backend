from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, field_validator


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
