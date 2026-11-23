from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.rental_vehicle import VehicleHelmetInfo
from app.schemas.rental_deposit import (
    RentalDepositCollectRequest,
    RentalDepositDetailResponse,
    RentalDepositResolveRequest,
    RentalDepositResponse,
)
from app.schemas.rental_incident import (
    RentalIncidentCreateRequest,
    RentalIncidentListResponse,
    RentalIncidentResponse,
    RentalIncidentType,
)


BookingStatus = Literal[
    "pending",
    "confirmed",
    "active",
    "in_progress",
    "cancelled",
    "completed",
    "rejected",
    "expired",
]


class RentalBookingCreate(BaseModel):
    vehicle_id: int = Field(gt=0)
    pickup_at: datetime
    return_at: datetime
    pickup_location_id: int | None = None
    return_location_id: int | None = None
    payment_method: str = "pay_at_location"
    self_drive_agreement_accepted: bool = False
    passenger_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_booking_dates(self):
        if self.return_at <= self.pickup_at:
            raise ValueError(
                "Return date must be after pickup date"
            )
        return self


class CashCollectionTerms(BaseModel):
    is_cash_on_pickup: bool = False
    payable_amount: Decimal
    currency: str = "LKR"
    security_deposit: Decimal = Decimal("0.00")
    instructions: str


class RentalDateValidationRequest(BaseModel):
    vehicle_id: int = Field(gt=0)
    pickup_at: datetime
    return_at: datetime

    @model_validator(mode="after")
    def validate_dates(self):
        if self.return_at <= self.pickup_at:
            raise ValueError("Return date must be strictly after pickup date")
        return self


class RentalDateValidationResponse(BaseModel):
    vehicle_id: int
    is_valid: bool
    pickup_at: datetime
    return_at: datetime
    duration_hours: float
    duration_days: int
    collision_type: Literal["none", "booked", "maintenance"] = "none"
    error_message: str | None = None


class RentalBookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = None
    vehicle_id: int
    user_id: int
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    status: BookingStatus
    pickup_location_id: int | None = None
    return_location_id: int | None = None
    currency: str = "LKR"
    payment_method: str = "pay_at_location"
    payment_status: str = "unpaid"
    expires_at: datetime | None = None
    cash_terms: CashCollectionTerms | None = None
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    cancelled_at: datetime | None = None
    daily_rate_snapshot: Decimal | None = None
    cancellation_policy_snapshot: str | None = None
    free_cancellation_hours_snapshot: int = 24
    self_drive_agreement_accepted: bool = False
    passenger_count: int | None = None
    fuel_policy: str = "same_to_same"
    security_deposit: Decimal = Decimal("0.00")
    deposit_status: str | None = None
    is_within_free_cancellation: bool | None = None
    refund_eligible: bool | None = None
    cancellation_fee: Decimal | None = None
    refund_amount: Decimal | None = None
    refund_record_created: bool = False
    created_at: datetime
    updated_at: datetime | None = None

    @field_validator("self_drive_agreement_accepted", mode="before")
    @classmethod
    def default_legacy_agreement(cls, value):
        return False if value is None else value


class RentalBookingCancelResponse(BaseModel):
    booking_id: int
    status: str = "cancelled"
    cancellation_reason: str
    cancelled_at: datetime
    cancelled_by: int
    is_within_free_cancellation: bool
    refund_eligible: bool
    cancellation_fee: Decimal
    refund_amount: Decimal
    currency: str = "LKR"
    refund_record_created: bool = False
    message: str


class RentalBookingHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = None
    vehicle_id: int
    vehicle_name: str
    vehicle_type: str
    cover_image_url: str | None = None
    pickup_location_name: str | None = None
    return_location_name: str | None = None
    provider_business_name: str | None = None
    provider_phone: str | None = None
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    currency: str = "LKR"
    status: BookingStatus
    payment_method: str
    payment_status: str
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    self_drive_agreement_accepted: bool = False
    passenger_count: int | None = None
    created_at: datetime


class RentalBookingHistoryPagination(BaseModel):
    total: int
    page: int
    limit: int
    pages: int
    items: list[RentalBookingHistoryItem]


class RentalBookingStatusUpdate(BaseModel):
    status: Literal["confirmed", "active", "in_progress", "completed", "rejected"]
    rejection_reason: str | None = None


class RentalCustomerSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str


class RentalVehicleSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_name: str
    vehicle_type: str
    price_per_day: Decimal
    license_plate: str | None = None


class RentalBookingProviderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    user_id: int
    pickup_at: datetime
    return_at: datetime
    total_price: Decimal
    status: str
    created_at: datetime
    vehicle: RentalVehicleSummary | None = None
    customer: RentalCustomerSummary | None = None


class RentalBookingProviderListResponse(BaseModel):
    items: list[RentalBookingProviderItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==============================================================================
# AR-06: TRAVELER INSPECTION CHECKLIST & ACKNOWLEDGEMENT SCHEMAS
# ==============================================================================

class InspectionEvidenceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inspection_id: int
    evidence_type: str
    file_url: str
    area_of_vehicle: str
    description: str | None = None
    created_at: datetime


class RentalInspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    inspection_type: Literal["pickup", "return"]
    odometer_reading: int
    fuel_level_percent: int
    cleanliness_status: str
    inspected_by: int | None = None
    customer_signature: str | None = None
    is_acknowledged: bool = False
    acknowledged_at: datetime | None = None
    notes: str | None = None
    helmets: VehicleHelmetInfo | None = None
    fuel_policy: str = "same_to_same"
    evidence: list[InspectionEvidenceItem] = []
    created_at: datetime


class TravelerInspectionAcknowledgementRequest(BaseModel):
    agreed_to_condition: bool = Field(..., description="Confirmation that traveler has inspected the vehicle and agreed to the condition")
    signature_name: str = Field(..., min_length=2, max_length=150, description="Full legal name of traveler signing acknowledgement")
    signature_token: str | None = Field(default=None, max_length=500, description="Optional digital verification token")


class TravelerInspectionAcknowledgementResponse(BaseModel):
    booking_id: int
    inspection_id: int
    inspection_type: str
    is_acknowledged: bool
    customer_signature: str
    acknowledged_at: datetime
    message: str
