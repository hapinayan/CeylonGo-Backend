"""
Pydantic v2 schemas for CeylonGo Rental Admin Portal (Phase 4).
Covers:
- AD-02: Rental Vehicle Listing Approval & Moderation
- AD-03: Rental Vehicle Document Verification
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.rental_vehicle import VehicleType

VehicleApprovalDecision = Literal["approved", "rejected", "suspended"]
VehicleVerificationDecision = Literal["verified", "rejected", "unverified", "suspended"]
DocumentVerifyDecision = Literal["approved", "rejected"]


class RentalAgeRuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_age: int = Field(strict=True, ge=0, le=120)


class RentalAgeRuleResponse(RentalAgeRuleUpdate):
    vehicle_type: VehicleType
    requires_verified_dob: bool


# ==============================================================================
# AD-02: VEHICLE LISTING MODERATION SCHEMAS
# ==============================================================================

class AdminVehicleReviewRequest(BaseModel):
    decision: VehicleApprovalDecision = Field(..., description="Decision: approved, rejected, or suspended")
    reason: str | None = Field(default=None, max_length=1000, description="Mandatory reason if rejected or suspended")


class AdminVehicleReviewResponse(BaseModel):
    vehicle_id: int
    vehicle_name: str
    previous_approval_status: str
    new_approval_status: str
    reviewed_at: datetime
    reviewed_by: int
    reviewer_name: str
    rejection_reason: str | None = None
    message: str


class AdminVehicleDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    provider_business_name: str
    provider_name: str
    provider_phone: str
    provider_approval_status: str
    vehicle_name: str
    vehicle_type: str
    model_year: int | None = None
    license_plate: str | None = None
    seating_capacity: int
    transmission: str
    fuel_type: str
    price_per_day: Decimal
    district: str | None = None
    city: str | None = None
    location_name: str | None = None
    status: str
    approval_status: str
    verification_status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    rejection_reason: str | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    images_count: int = 0
    primary_image_url: str | None = None
    documents_count: int = 0
    verified_documents_count: int = 0
    rate_plans_count: int = 0
    created_at: datetime
    updated_at: datetime


class AdminVehicleListItem(BaseModel):
    id: int
    provider_id: int
    provider_business_name: str
    vehicle_name: str
    vehicle_type: str
    license_plate: str | None = None
    price_per_day: Decimal
    district: str | None = None
    status: str
    approval_status: str
    verification_status: str
    primary_image_url: str | None = None
    created_at: datetime


class AdminVehicleListResponse(BaseModel):
    items: list[AdminVehicleListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# AD-03: VEHICLE DOCUMENT VERIFICATION SCHEMAS
# ==============================================================================

class AdminVehicleVerifyStatusRequest(BaseModel):
    decision: VehicleVerificationDecision = Field(..., description="Decision: verified, rejected, unverified, or suspended")
    reason: str | None = Field(default=None, max_length=1000, description="Mandatory reason if rejected")


class AdminVehicleVerifyStatusResponse(BaseModel):
    vehicle_id: int
    previous_verification_status: str
    new_verification_status: str
    verified_documents_count: int
    total_documents_count: int
    message: str


class AdminDocumentVerifyRequest(BaseModel):
    decision: DocumentVerifyDecision = Field(..., description="Decision: approved or rejected")
    notes: str | None = Field(default=None, max_length=1000, description="Optional notes or rejection explanation")


class AdminDocumentVerifyResponse(BaseModel):
    document_id: int
    vehicle_id: int
    document_type: str
    is_verified: bool
    reviewed_at: datetime
    reviewed_by: int
    notes: str | None = None
    message: str


class AdminVehicleDocumentItem(BaseModel):
    id: int
    vehicle_id: int
    vehicle_name: str
    provider_id: int
    provider_business_name: str
    document_type: str
    document_number: str | None = None
    file_reference: str
    expiry_date: date | None = None
    insurance_effective_date: date | None = None
    insurance_exclusions: str | None = None
    coverage_amount: Decimal | None = None
    deductible_amount: Decimal | None = None
    insurance_provider: str | None = None
    is_verified: bool
    created_at: datetime


class AdminPendingDocumentsResponse(BaseModel):
    items: list[AdminVehicleDocumentItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# AD-05: RENTAL BOOKING & INCIDENT MONITORING SCHEMAS
# ==============================================================================

class AdminRentalBookingListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str
    vehicle_id: int
    vehicle_name: str
    license_plate: str | None = None
    vehicle_type: str
    provider_id: int
    provider_business_name: str
    user_id: int
    customer_name: str
    customer_email: str
    customer_phone: str | None = None
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    currency: str = "LKR"
    status: str
    payment_method: str
    payment_status: str
    has_incidents: bool = False
    incident_count: int = 0
    created_at: datetime


class AdminRentalBookingListResponse(BaseModel):
    items: list[AdminRentalBookingListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalBookingInspectionItem(BaseModel):
    id: int
    inspection_type: str
    odometer_reading: int
    fuel_level_percent: int
    cleanliness_status: str
    customer_signature: str | None = None
    notes: str | None = None
    created_at: datetime
    evidence_count: int = 0


class AdminRentalIncidentItem(BaseModel):
    id: int
    booking_id: int
    vehicle_id: int | None = None
    vehicle_name: str | None = None
    incident_type: str
    incident_datetime: datetime
    description: str
    estimated_damage_cost: Decimal
    actual_repair_cost: Decimal | None = None
    police_report_reference: str | None = None
    resolution_status: str
    created_at: datetime
    updated_at: datetime


class AdminRentalDepositItem(BaseModel):
    id: int
    booking_id: int
    deposit_amount: Decimal
    currency: str = "LKR"
    deposit_status: str
    deducted_amount: Decimal = Decimal("0.00")
    deduction_reason: str | None = None
    refunded_amount: Decimal = Decimal("0.00")
    held_at: datetime
    released_at: datetime | None = None


class AdminBookingStatusHistoryItem(BaseModel):
    id: int
    from_status: str | None = None
    to_status: str
    changed_by: int | None = None
    notes: str | None = None
    created_at: datetime


class AdminRentalBookingDetailResponse(BaseModel):
    booking: AdminRentalBookingListItem
    pickup_location: str | None = None
    return_location: str | None = None
    daily_rate_snapshot: Decimal | None = None
    cancellation_policy_snapshot: str | None = None
    free_cancellation_hours_snapshot: int = 24
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    cancelled_by: int | None = None
    inspections: list[AdminRentalBookingInspectionItem] = []
    incidents: list[AdminRentalIncidentItem] = []
    deposits: list[AdminRentalDepositItem] = []
    status_history: list[AdminBookingStatusHistoryItem] = []
    paid_online_amount: Decimal = Decimal("0.00")
    cash_collected_amount: Decimal = Decimal("0.00")


class AdminIncidentUpdateRequest(BaseModel):
    resolution_status: str = Field(
        ...,
        description="Resolution status: reported, under_investigation, insurance_claimed, customer_billed, resolved, dismissed",
    )
    actual_repair_cost: Decimal | None = Field(default=None, ge=0, description="Actual documented repair cost")
    police_report_reference: str | None = Field(default=None, max_length=100, description="Police report or entry ID")
    admin_notes: str | None = Field(default=None, max_length=1000, description="Administrative resolution commentary")


class AdminIncidentListResponse(BaseModel):
    items: list[AdminRentalIncidentItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminIncidentUpdateResponse(BaseModel):
    incident_id: int
    previous_status: str
    new_status: str
    actual_repair_cost: Decimal | None = None
    police_report_reference: str | None = None
    message: str


# ==============================================================================
# AD-06: PAYMENT, SETTLEMENT & REFUND MONITORING SCHEMAS
# ==============================================================================

class AdminRentalPaymentListItem(BaseModel):
    id: int
    booking_reference_id: int
    booking_reference: str
    target_booking_id: int
    gateway_name: str
    gateway_transaction_id: str | None = None
    amount: Decimal
    currency: str = "LKR"
    payment_status: str
    payment_method: str
    paid_at: datetime | None = None
    created_at: datetime
    customer_name: str
    provider_business_name: str


class AdminRentalPaymentListResponse(BaseModel):
    items: list[AdminRentalPaymentListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalSettlementListItem(BaseModel):
    id: int
    provider_profile_id: int
    provider_business_name: str
    period_start: date
    period_end: date
    gross_amount: Decimal
    platform_fee: Decimal
    net_payable: Decimal
    currency: str = "LKR"
    settlement_status: str
    payout_reference: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class AdminRentalSettlementListResponse(BaseModel):
    items: list[AdminRentalSettlementListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalDepositListResponse(BaseModel):
    items: list[AdminRentalDepositItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalRefundListItem(BaseModel):
    id: int
    payment_transaction_id: int
    booking_reference: str
    target_booking_id: int
    amount: Decimal
    currency: str = "LKR"
    refund_reason: str
    refund_status: str
    processed_by: int | None = None
    processed_at: datetime | None = None
    created_at: datetime
    customer_name: str


class AdminRentalRefundListResponse(BaseModel):
    items: list[AdminRentalRefundListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalRefundReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"] = Field(..., description="Audit review decision: approved or rejected")
    admin_notes: str | None = Field(default=None, max_length=1000, description="Administrative audit commentary")


class AdminRentalRefundReviewResponse(BaseModel):
    refund_id: int
    previous_status: str
    new_status: str
    processed_by: int
    processed_at: datetime
    message: str


# ==============================================================================
# AD-07: SPONSORED PROMOTION MANAGEMENT SCHEMAS
# ==============================================================================

class AdminRentalPromotionListItem(BaseModel):
    id: int
    provider_profile_id: int
    provider_business_name: str
    campaign_name: str
    target_module: str
    budget: Decimal
    spent_amount: Decimal
    currency: str = "LKR"
    status: str
    starts_at: datetime
    ends_at: datetime
    is_paid: bool = False
    impressions_count: int = 0
    clicks_count: int = 0
    created_at: datetime


class AdminRentalPromotionListResponse(BaseModel):
    items: list[AdminRentalPromotionListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminRentalPromotionReviewRequest(BaseModel):
    decision: Literal["approve", "reject", "activate", "pause"] = Field(
        ..., description="Action: approve, reject, activate, or pause"
    )
    reason: str | None = Field(default=None, max_length=1000, description="Mandatory reason if rejected")
    payment_reference: str | None = Field(
        default=None, max_length=100, description="Receipt or transaction reference confirming promotion package payment"
    )


class AdminRentalPromotionReviewResponse(BaseModel):
    campaign_id: int
    campaign_name: str
    previous_status: str
    new_status: str
    is_paid: bool
    message: str


# ==============================================================================
# AD-08: RENTAL REPORTS & ANALYTICS SCHEMAS
# ==============================================================================

class AdminRentalFleetSummaryResponse(BaseModel):
    total_vehicles: int
    approved_vehicles: int
    pending_approval_vehicles: int
    rejected_vehicles: int
    suspended_vehicles: int
    verified_vehicles: int
    unverified_vehicles: int
    available_vehicles: int
    maintenance_vehicles: int
    currently_rented_vehicles: int
    category_distribution: dict[str, int]
    district_distribution: dict[str, int]


class AdminRentalRevenueAnalyticsResponse(BaseModel):
    total_bookings: int
    confirmed_bookings: int
    completed_bookings: int
    cancelled_bookings: int
    gross_booking_value: Decimal
    confirmed_online_payments: Decimal
    cash_collected: Decimal
    total_collected_revenue: Decimal
    platform_commission: Decimal
    completed_settlement_payouts: Decimal
    pending_settlements: Decimal
    security_deposits_held: Decimal
    security_deposits_refunded: Decimal
    currency: str = "LKR"

