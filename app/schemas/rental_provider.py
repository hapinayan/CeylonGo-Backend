"""
Pydantic schemas for CeylonGo Module 09 — Rental Provider Backend (Phase 3).
Covers:
- SP-02: Add Rental Vehicle
- SP-03: Manage Rental Vehicles
- SP-04: Vehicle Photo Management
- SP-05: Category Management
- SP-06: Technical Specifications
- SP-07: Rental Pricing Management (Rate Plans)
- SP-08: Availability & Blackout Management
- SP-09: Pickup & Return Locations
- SP-10: Provider Booking Requests
- SP-11: Booking Approval & Rejection
- SP-12: Provider Booking History
- SP-13 & SP-14: Digital Handover & Return Inspections
- SP-15: Cash Payment Recording
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

VehicleType = Literal["bike", "bicycle", "tuk_tuk"]
VehicleStatus = Literal["available", "unavailable", "retired"]


# ==============================================================================
# SP-04: VEHICLE PHOTO SCHEMAS
# ==============================================================================

class ProviderVehicleImageCreate(BaseModel):
    image_url: str = Field(..., min_length=5, max_length=500, description="URL of the vehicle image")
    caption: str | None = Field(default=None, max_length=200, description="Optional photo caption")
    display_order: int = Field(default=0, ge=0, description="Display order index")
    is_primary: bool = Field(default=False, description="Designate as primary/cover image")


class ProviderVehicleImageUpdate(BaseModel):
    caption: str | None = Field(default=None, max_length=200)
    display_order: int | None = Field(default=None, ge=0)
    is_primary: bool | None = None


class ProviderVehicleImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    image_url: str
    caption: str | None = None
    display_order: int = 0
    is_primary: bool = False
    created_at: datetime


# ==============================================================================
# SP-02, SP-05, SP-06: VEHICLE CREATE & UPDATE SCHEMAS
# ==============================================================================

class ProviderVehicleCreate(BaseModel):
    vehicle_type: VehicleType
    vehicle_name: str = Field(..., min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    price_per_day: Decimal = Field(..., gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    model_year: int | None = Field(default=None, ge=1990, le=2030)
    license_plate: str | None = Field(default=None, max_length=50)
    seating_capacity: int = Field(default=2, ge=1, le=10)
    transmission: str = Field(default="manual", max_length=20)
    fuel_type: str = Field(default="petrol", max_length=20)
    district: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    location_id: int | None = None
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0)
    helmet_count: int | None = Field(default=0, ge=0)
    helmets_included: bool | None = Field(default=False)
    helmet_sizes: str | None = Field(default=None, max_length=100)
    security_deposit: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    fuel_policy: str | None = Field(default="same_to_same", max_length=30)

    @model_validator(mode="after")
    def validate_category_specs(self):
        vtype = self.vehicle_type
        seats = self.seating_capacity
        fuel = (self.fuel_type or "").lower().strip()

        if vtype == "bicycle":
            if seats > 2:
                raise ValueError("Bicycles cannot have a seating capacity greater than 2")
            if fuel not in ("none", "electric", "pedal", "n/a"):
                raise ValueError("Bicycles must have fuel_type as 'none', 'pedal', or 'electric'")
        elif vtype == "tuk_tuk":
            if seats < 3 or seats > 4:
                raise ValueError("Tuk-tuks must have a legal passenger seating capacity between 3 and 4")
        elif vtype == "bike":
            if seats > 2:
                raise ValueError("Motorbikes / scooters cannot exceed a seating capacity of 2")

        return self


class ProviderVehicleUpdate(BaseModel):
    vehicle_type: VehicleType | None = None
    vehicle_name: str | None = Field(default=None, min_length=2, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    price_per_day: Decimal | None = Field(default=None, gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    status: Literal["available", "unavailable", "retired"] | None = None
    model_year: int | None = Field(default=None, ge=1990, le=2030)
    license_plate: str | None = Field(default=None, max_length=50)
    seating_capacity: int | None = Field(default=None, ge=1, le=10)
    transmission: str | None = Field(default=None, max_length=20)
    fuel_type: str | None = Field(default=None, max_length=20)
    district: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    location_id: int | None = None
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0)
    helmet_count: int | None = Field(default=None, ge=0)
    helmets_included: bool | None = None
    helmet_sizes: str | None = Field(default=None, max_length=100)
    security_deposit: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    fuel_policy: str | None = Field(default=None, max_length=30)

    @model_validator(mode="after")
    def validate_category_specs_on_update(self):
        vtype = self.vehicle_type
        seats = self.seating_capacity
        fuel = (self.fuel_type or "").lower().strip() if self.fuel_type else None

        if vtype == "bicycle":
            if seats is not None and seats > 2:
                raise ValueError("Bicycles cannot have a seating capacity greater than 2")
            if fuel is not None and fuel not in ("none", "electric", "pedal", "n/a"):
                raise ValueError("Bicycles must have fuel_type as 'none', 'pedal', or 'electric'")
        elif vtype == "tuk_tuk":
            if seats is not None and (seats < 3 or seats > 4):
                raise ValueError("Tuk-tuks must have a legal passenger seating capacity between 3 and 4")
        elif vtype == "bike":
            if seats is not None and seats > 2:
                raise ValueError("Motorbikes / scooters cannot exceed a seating capacity of 2")

        return self


# ==============================================================================
# SP-03: FLEET RESPONSE & PAGINATION SCHEMAS
# ==============================================================================

class ProviderVehicleListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None = None
    price_per_day: Decimal
    currency: str = "LKR"
    status: str
    approval_status: str
    verification_status: str
    model_year: int | None = None
    license_plate: str | None = None
    seating_capacity: int = 2
    transmission: str = "manual"
    fuel_type: str = "petrol"
    district: str | None = None
    city: str | None = None
    location_id: int | None = None
    primary_image_url: str | None = None
    image_count: int = 0
    active_booking_count: int = 0
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    helmet_count: int | None = None
    helmets_included: bool | None = None
    helmet_sizes: str | None = None
    security_deposit: Decimal | None = None
    fuel_policy: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderVehicleDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None = None
    price_per_day: Decimal
    currency: str = "LKR"
    status: str
    approval_status: str
    verification_status: str
    model_year: int | None = None
    license_plate: str | None = None
    seating_capacity: int = 2
    transmission: str = "manual"
    fuel_type: str = "petrol"
    district: str | None = None
    city: str | None = None
    location_id: int | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    helmet_count: int | None = None
    helmets_included: bool | None = None
    helmet_sizes: str | None = None
    security_deposit: Decimal | None = None
    fuel_policy: str | None = None
    primary_image_url: str | None = None
    images: list[ProviderVehicleImageResponse] = []
    created_at: datetime
    updated_at: datetime


class ProviderVehiclePagination(BaseModel):
    items: list[ProviderVehicleListItem]
    total: int
    page: int
    limit: int
    pages: int


# ==============================================================================
# SP-07: RENTAL PRICING SCHEMAS (RATE PLANS)
# ==============================================================================

RatePlanDuration = Literal["hourly", "daily", "weekly", "monthly"]


class ProviderRatePlanCreate(BaseModel):
    plan_name: str = Field(..., min_length=2, max_length=100, description="Rate plan title (e.g. Standard Daily Rate)")
    duration_type: RatePlanDuration = Field(..., description="Duration tier: hourly, daily, weekly, or monthly")
    rate: Decimal = Field(..., gt=Decimal("0.00"), max_digits=12, decimal_places=2, description="Price per tier")
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    included_km_per_day: int | None = Field(default=None, ge=0, description="Included km allowance per day (optional)")
    extra_km_charge: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=10, decimal_places=2, description="Fee per extra km")
    is_active: bool = Field(default=True, description="Whether this rate plan is active")


class ProviderRatePlanUpdate(BaseModel):
    plan_name: str | None = Field(default=None, min_length=2, max_length=100)
    duration_type: RatePlanDuration | None = None
    rate: Decimal | None = Field(default=None, gt=Decimal("0.00"), max_digits=12, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    included_km_per_day: int | None = Field(default=None, ge=0)
    extra_km_charge: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=10, decimal_places=2)
    is_active: bool | None = None


class ProviderRatePlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    plan_name: str
    duration_type: str
    rate: Decimal
    currency: str = "LKR"
    included_km_per_day: int | None = None
    extra_km_charge: Decimal | None = None
    is_active: bool = True
    created_at: datetime


# ==============================================================================
# SP-08: AVAILABILITY & BLACKOUT SCHEMAS
# ==============================================================================

BlockType = Literal["maintenance", "service", "accident_repair", "owner_unavailable", "administrative"]


class ProviderAvailabilityBlockCreate(BaseModel):
    start_datetime: datetime = Field(..., description="Start of unavailable blackout period")
    end_datetime: datetime = Field(..., description="End of unavailable blackout period")
    block_type: BlockType = Field(default="maintenance", description="Reason category for availability block")
    reason: str | None = Field(default=None, max_length=500, description="Detailed explanation or service notes")
    is_active: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_datetime <= self.start_datetime:
            raise ValueError("end_datetime must be strictly greater than start_datetime")
        return self


class ProviderAvailabilityBlockUpdate(BaseModel):
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    block_type: BlockType | None = None
    reason: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.start_datetime is not None and self.end_datetime is not None:
            if self.end_datetime <= self.start_datetime:
                raise ValueError("end_datetime must be strictly greater than start_datetime")
        return self


class ProviderAvailabilityBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    start_datetime: datetime
    end_datetime: datetime
    block_type: str
    reason: str | None = None
    is_active: bool = True
    created_at: datetime


# ==============================================================================
# SP-09: PICKUP & RETURN LOCATION SCHEMAS
# ==============================================================================

class ProviderLocationCreate(BaseModel):
    location_name: str = Field(..., min_length=2, max_length=150, description="Designated rental station/hub name")
    district: str = Field(..., min_length=2, max_length=100, description="Administrative district")
    city: str = Field(..., min_length=2, max_length=100, description="City or town")
    address: str | None = Field(default=None, max_length=500, description="Street address")
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    is_active: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_coordinates(self):
        lat = self.latitude
        lng = self.longitude
        if (lat is None and lng is not None) or (lat is not None and lng is None):
            raise ValueError("Both latitude and longitude must be provided together, or both omitted")
        return self


class ProviderLocationUpdate(BaseModel):
    location_name: str | None = Field(default=None, min_length=2, max_length=150)
    district: str | None = Field(default=None, min_length=2, max_length=100)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    is_active: bool | None = None


class ProviderLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    location_name: str
    district: str
    city: str
    address: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    is_active: bool = True
    created_at: datetime


# ==============================================================================
# SP-10: PROVIDER BOOKING REQUEST SCHEMAS
# ==============================================================================

class ProviderBookingRequestItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = None
    vehicle_id: int
    vehicle_name: str
    vehicle_type: str
    license_plate: str | None = None
    traveler_id: int
    traveler_name: str
    traveler_email: str | None = None
    pickup_at: datetime
    return_at: datetime
    total_days: int
    daily_rate: Decimal | None = None
    total_price: Decimal
    currency: str = "LKR"
    payment_method: str
    payment_status: str
    status: str
    expires_at: datetime | None = None
    urgency: str = "normal"  # critical, urgent, normal, expired
    time_remaining_seconds: int | None = None
    created_at: datetime


class ProviderBookingRequestPagination(BaseModel):
    items: list[ProviderBookingRequestItem]
    total: int
    page: int
    limit: int
    pages: int


# ==============================================================================
# SP-11: BOOKING APPROVAL & REJECTION SCHEMAS
# ==============================================================================

class ProviderBookingRejectRequest(BaseModel):
    reason: str = Field(..., min_length=2, max_length=500, description="Clear reason for declining the rental request")


class ProviderBookingActionResponse(BaseModel):
    booking_id: int
    status: str
    message: str
    action_at: datetime
    cancellation_reason: str | None = None


# ==============================================================================
# SP-12: PROVIDER BOOKING HISTORY SCHEMAS
# ==============================================================================

class ProviderBookingHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = None
    vehicle_id: int
    vehicle_name: str
    vehicle_type: str
    traveler_id: int
    traveler_name: str
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    currency: str = "LKR"
    status: str
    payment_method: str
    payment_status: str
    created_at: datetime


class ProviderBookingHistoryPagination(BaseModel):
    items: list[ProviderBookingHistoryItem]
    total: int
    page: int
    limit: int
    pages: int


# ==============================================================================
# SP-13 & SP-14: DIGITAL HANDOVER & RETURN INSPECTION SCHEMAS
# ==============================================================================

class ProviderEvidenceItem(BaseModel):
    evidence_type: Literal["photo", "video", "document"] = "photo"
    file_url: str = Field(..., min_length=5, max_length=1000, description="Photo or video URL of vehicle condition")
    area_of_vehicle: str = Field(..., min_length=2, max_length=50, description="Area inspected (e.g. front_bumper, odometer, fuel_gauge)")
    description: str | None = Field(default=None, max_length=500)


class ProviderEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inspection_id: int
    evidence_type: str
    file_url: str
    area_of_vehicle: str
    description: str | None = None
    created_at: datetime


class ProviderInspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    inspection_type: str
    odometer_reading: int
    fuel_level_percent: int
    cleanliness_status: str
    inspected_by: int | None = None
    customer_signature: str | None = None
    notes: str | None = None
    evidence: list[ProviderEvidenceResponse] = []
    created_at: datetime


class ProviderHandoverInspectionRequest(BaseModel):
    odometer_reading: int = Field(..., ge=0, description="Current vehicle odometer reading at handover")
    fuel_level_percent: int = Field(..., ge=0, le=100, description="Fuel level percentage (0 to 100)")
    cleanliness_status: str = Field(default="clean", max_length=30)
    customer_signature: str | None = Field(default=None, max_length=1000, description="Signature reference or code")
    notes: str | None = Field(default=None, max_length=1000, description="Pre-existing scratch/dent notes")
    helmet_count: int | None = Field(default=None, ge=0, description="Quantity of helmets provided at handover")
    helmet_sizes: list[str] | None = Field(default=None, description="Sizes of helmets provided (e.g. ['M', 'L'])")
    helmet_condition: str | None = Field(default=None, max_length=100, description="Condition of helmets (e.g. 'Good', 'New')")
    evidence: list[ProviderEvidenceItem] = Field(default=[], description="Inspection photos and evidence")


class ProviderHandoverResponse(BaseModel):
    booking_id: int
    status: str
    handover_completed_at: datetime
    message: str
    inspection: ProviderInspectionResponse
    driver_verification: str | None = None


class ProviderReturnInspectionRequest(BaseModel):
    odometer_reading: int = Field(..., ge=0, description="Current vehicle odometer reading at return")
    fuel_level_percent: int = Field(..., ge=0, le=100, description="Fuel level percentage (0 to 100)")
    cleanliness_status: str = Field(default="clean", max_length=30)
    customer_signature: str | None = Field(default=None, max_length=1000, description="Return customer signature reference")
    notes: str | None = Field(default=None, max_length=1000, description="Return condition discrepancies or damage notes")
    evidence: list[ProviderEvidenceItem] = Field(default=[], description="Return inspection photos and evidence")
    actual_return_time: datetime | None = Field(default=None, description="Actual return timestamp (defaults to current server time if omitted)")


class ProviderReturnResponse(BaseModel):
    booking_id: int
    status: str
    return_completed_at: datetime
    km_driven: int
    excess_km: int = 0
    excess_km_charge: Decimal = Decimal("0.00")
    fuel_policy: str = "same_to_same"
    pickup_fuel_percent: int = 100
    return_fuel_percent: int = 100
    fuel_difference_percent: int = 0
    fuel_deficit_percent: int = 0
    is_late: bool = False
    late_minutes: int = 0
    grace_period_applied: bool = False
    billable_late_hours: int = 0
    late_hourly_rate: Decimal | None = None
    late_return_fee: Decimal = Decimal("0.00")
    late_fee_notes: str | None = None
    message: str
    inspection: ProviderInspectionResponse


# ==============================================================================
# SP-15: CASH PAYMENT RECORDING SCHEMAS
# ==============================================================================

class ProviderRecordCashPaymentRequest(BaseModel):
    amount: Decimal = Field(..., gt=Decimal("0.00"), max_digits=12, decimal_places=2, description="Cash amount collected at pickup")
    notes: str | None = Field(default=None, max_length=500, description="Cash receipt or transaction reference note")


class ProviderCashPaymentResponse(BaseModel):
    booking_id: int
    amount_received: Decimal
    currency: str = "LKR"
    payment_status: str
    gateway_transaction_id: str | None = None
    paid_at: datetime
    message: str


# ==============================================================================
# SP-01: RENTAL PROVIDER DASHBOARD SCHEMAS
# ==============================================================================

class RentalFleetMetrics(BaseModel):
    total_vehicles: int = 0
    available_vehicles: int = 0
    pending_approval_vehicles: int = 0
    maintenance_vehicles: int = 0
    retired_vehicles: int = 0


class RentalBookingMetrics(BaseModel):
    total_bookings: int = 0
    pending_requests: int = 0
    confirmed_bookings: int = 0
    active_rentals: int = 0
    completed_rentals: int = 0
    cancelled_bookings: int = 0


class RentalUtilizationMetrics(BaseModel):
    active_rentals_count: int = 0
    total_fleet_capacity: int = 0
    fleet_utilization_rate: float = Field(default=0.0, description="Percentage of fleet currently on active rental")


class RentalRevenueMetrics(BaseModel):
    gross_booking_revenue: Decimal = Decimal("0.00")
    online_paid_amount: Decimal = Decimal("0.00")
    cash_collected_amount: Decimal = Decimal("0.00")
    total_revenue_collected: Decimal = Decimal("0.00")
    currency: str = "LKR"


class RentalProviderDashboardResponse(BaseModel):
    provider_id: int
    business_name: str
    fleet: RentalFleetMetrics
    bookings: RentalBookingMetrics
    utilization: RentalUtilizationMetrics
    revenue: RentalRevenueMetrics
    timestamp: datetime


# ==============================================================================
# SP-16: ONLINE EARNINGS & SETTLEMENTS SCHEMAS
# ==============================================================================

class ProviderSettlementItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period_start: date
    period_end: date
    gross_amount: Decimal
    platform_fee: Decimal
    net_payable: Decimal
    currency: str
    settlement_status: str
    payout_reference: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class ProviderSettlementsSummaryResponse(BaseModel):
    provider_id: int
    gross_booking_revenue: Decimal = Decimal("0.00")
    confirmed_payments: Decimal = Decimal("0.00")
    online_paid_amount: Decimal = Decimal("0.00")
    cash_paid_amount: Decimal = Decimal("0.00")
    platform_commission: Decimal = Decimal("0.00")
    commission_policy_note: str = Field(
        default="Platform commission is computed strictly from verified settlement records in accordance with platform policy. No unverified commission rates are applied."
    )
    completed_payouts: Decimal = Decimal("0.00")
    pending_settlements: Decimal = Decimal("0.00")
    currency: str = "LKR"
    settlements: list[ProviderSettlementItem] = Field(default_factory=list)


# ==============================================================================
# SP-17: SPONSORED PROMOTIONS SCHEMAS
# ==============================================================================

class RentalPromotionPackageOption(BaseModel):
    package_key: str
    title: str
    description: str
    duration_days: int
    price: Decimal
    currency: str = "LKR"
    is_active: bool = True


class ProviderRentalCampaignCreateRequest(BaseModel):
    campaign_name: str = Field(..., min_length=3, max_length=150, description="Campaign display name")
    budget: Decimal = Field(..., gt=Decimal("0.00"), max_digits=12, decimal_places=2, description="Total campaign budget")
    starts_at: datetime = Field(..., description="Campaign start datetime")
    ends_at: datetime = Field(..., description="Campaign end datetime")
    currency: str = Field(default="LKR", min_length=3, max_length=3)


class ProviderRentalCampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    campaign_name: str
    target_module: str
    budget: Decimal
    spent_amount: Decimal
    currency: str
    status: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    impressions_count: int = 0
    clicks_count: int = 0


class ProviderRentalCampaignListResponse(BaseModel):
    items: list[ProviderRentalCampaignResponse]
    total: int


# ==============================================================================
# SP-18: RENTAL DISCOUNTS & OFFERS SCHEMAS
# ==============================================================================

class ProviderRentalDiscountCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=150, description="Discount package title")
    description: str | None = Field(default=None, max_length=2000, description="Optional description of promotion")
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00"), decimal_places=2)
    discount_amount: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    valid_from: date
    valid_to: date
    is_active: bool = True

    @model_validator(mode="after")
    def validate_discount_fields(self):
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")
        has_pct = self.discount_percent is not None and self.discount_percent > Decimal("0.00")
        has_amt = self.discount_amount is not None and self.discount_amount > Decimal("0.00")
        if not has_pct and not has_amt:
            raise ValueError("At least one of discount_percent or discount_amount must be specified and greater than zero.")
        return self


class ProviderRentalDiscountUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0.00"), le=Decimal("100.00"), decimal_places=2)
    discount_amount: Decimal | None = Field(default=None, ge=Decimal("0.00"), max_digits=12, decimal_places=2)
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_discount_fields(self):
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")
        return self


class ProviderRentalDiscountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    description: str | None
    discount_percent: Decimal | None
    discount_amount: Decimal | None
    currency: str
    valid_from: date
    valid_to: date
    is_active: bool
    created_at: datetime


class ProviderRentalDiscountListResponse(BaseModel):
    items: list[ProviderRentalDiscountResponse]
    total: int


# ==============================================================================
# SP-19: CUSTOMER REVIEWS DASHBOARD SCHEMAS
# ==============================================================================

class ProviderReviewItem(BaseModel):
    id: int
    vehicle_id: int | None
    vehicle_name: str | None
    reviewer_name: str
    rating: Decimal
    title: str | None
    comment: str | None
    is_verified_booking: bool
    created_at: datetime


class ProviderReviewsDashboardResponse(BaseModel):
    total_reviews: int
    average_rating: float
    rating_distribution: dict[str, int] = Field(
        default_factory=lambda: {"5_star": 0, "4_star": 0, "3_star": 0, "2_star": 0, "1_star": 0}
    )
    reviews: list[ProviderReviewItem] = Field(default_factory=list)
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# SP-20: PROVIDER & VEHICLE VERIFICATION STATUS SCHEMAS
# ==============================================================================

class VehicleVerificationSummary(BaseModel):
    vehicle_id: int
    vehicle_name: str
    vehicle_type: str
    license_plate: str | None
    status: str
    approval_status: str
    verification_status: str
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None


class ProviderVerificationStatusResponse(BaseModel):
    provider_id: int
    business_name: str
    kyc_approval_status: str
    is_profile_verified: bool
    kyc_reviewed_at: datetime | None = None
    kyc_rejection_reason: str | None = None
    documents_submitted: dict[str, bool]
    fleet_summary: dict[str, int]
    vehicles: list[VehicleVerificationSummary] = Field(default_factory=list)

