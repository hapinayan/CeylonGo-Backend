
from datetime import date, datetime
from decimal import Decimal
from math import ceil
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VehicleType = Literal["bike", "bicycle", "tuk_tuk"]
VehicleStatus = Literal["available", "unavailable"]
FuelPolicy = Literal["same_to_same", "full_to_full", "not_applicable"]


class VehicleHelmetInfo(BaseModel):
    helmet_provided: bool = False
    helmet_count: int | None = None
    helmet_sizes: list[str] | None = None
    helmet_condition: str | None = None
    requires_helmet: bool = False
    notes: str | None = None


# ==============================================================================
# LOCATION SCHEMAS
# ==============================================================================

class RentalLocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    location_name: str
    address: str | None = None
    city: str | None = None
    district: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    created_at: datetime


# ==============================================================================
# IMAGE & GALLERY SCHEMAS
# ==============================================================================

class RentalVehicleImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    image_url: str
    caption: str | None = None
    display_order: int = 0
    is_primary: bool = False
    created_at: datetime


# ==============================================================================
# RATE PLAN SCHEMAS
# ==============================================================================

class RentalRatePlanResponse(BaseModel):
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
# QUOTE & PRICING SCHEMAS
# ==============================================================================

class RentalQuoteRequest(BaseModel):
    pickup_at: datetime
    return_at: datetime

    @model_validator(mode="after")
    def validate_quote_dates(self):
        if self.return_at <= self.pickup_at:
            raise ValueError("Return date must be strictly after pickup date")
        return self


class RentalQuoteResponse(BaseModel):
    vehicle_id: int
    pickup_at: datetime
    return_at: datetime
    duration_hours: float
    duration_days: int
    selected_plan_name: str
    duration_type: str
    rate: Decimal
    base_price: Decimal
    total_price: Decimal
    currency: str = "LKR"
    included_km: int | None = None
    extra_km_charge: Decimal | None = None
    security_deposit: Decimal = Decimal("0.00")
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    security_deposit: Decimal | None = None
    is_insured: bool = False
    insurance_status: str | None = None
    fuel_policy: FuelPolicy = "same_to_same"


# ==============================================================================
# AVAILABILITY CALENDAR SCHEMAS
# ==============================================================================

class UnavailablePeriod(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: Literal["booked", "maintenance"]


class VehicleAvailabilityResponse(BaseModel):
    vehicle_id: int
    is_available: bool
    query_start: datetime | None = None
    query_end: datetime | None = None
    unavailable_periods: list[UnavailablePeriod]


# ==============================================================================
# CATEGORY SCHEMAS
# ==============================================================================

class RentalCategoryItem(BaseModel):
    category: str
    name: str
    description: str
    requires_license: bool
    requires_helmet: bool
    active_fleet_count: int
    sample_image_url: str | None = None


# ==============================================================================
# CORE VEHICLE SCHEMAS
# ==============================================================================

class RentalVehicleCreate(BaseModel):
    vehicle_type: VehicleType
    location_id: int | None = Field(default=None, gt=0)

    vehicle_name: str = Field(
        min_length=2,
        max_length=150,
    )

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    price_per_day: Decimal = Field(
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    model_year: int | None = None
    license_plate: str | None = Field(default=None, max_length=50)
    seating_capacity: int = Field(default=4, ge=1, le=20)
    transmission: str = Field(default="manual", max_length=20)
    fuel_type: str = Field(default="petrol", max_length=20)
    district: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0)
    helmet_count: int | None = Field(default=0, ge=0)
    helmets_included: bool | None = Field(default=False)
    helmet_sizes: str | None = Field(default=None, max_length=100)
    security_deposit: Decimal | None = Field(default=None, ge=0)
    fuel_policy: FuelPolicy | None = Field(default="same_to_same")


class RentalVehicleUpdate(BaseModel):
    vehicle_name: str | None = Field(
        default=None,
        min_length=2,
        max_length=150,
    )

    description: str | None = Field(
        default=None,
        max_length=1000,
    )

    price_per_day: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=12,
        decimal_places=2,
    )

    status: VehicleStatus | None = None
    model_year: int | None = None
    license_plate: str | None = Field(default=None, max_length=50)
    seating_capacity: int | None = Field(default=None, ge=1, le=20)
    transmission: str | None = Field(default=None, max_length=20)
    fuel_type: str | None = Field(default=None, max_length=20)
    district: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    location_id: int | None = Field(default=None, gt=0)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0)
    helmet_count: int | None = Field(default=None, ge=0)
    helmets_included: bool | None = None
    helmet_sizes: str | None = Field(default=None, max_length=100)
    security_deposit: Decimal | None = Field(default=None, ge=0)
    fuel_policy: FuelPolicy | None = None


class RentalVehicleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None
    price_per_day: Decimal
    status: VehicleStatus
    location_id: int | None = None
    created_at: datetime


class RentalAvailabilityBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    block_type: str = Field(default="maintenance", max_length=30)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        if self.start_date < date.today():
            raise ValueError("Start date cannot be in the past")
        return self


class RentalAvailabilityBlockResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    start_date: date
    end_date: date
    block_type: str
    reason: str | None = None
    is_active: bool
    created_at: datetime


class RentalVehicleDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str = Field(min_length=2, max_length=50)
    document_url: str = Field(min_length=5, max_length=1000)
    expiry_date: date | None = None

    @field_validator("expiry_date")
    @classmethod
    def validate_expiry(cls, v: date | None) -> date | None:
        if v is not None and v < date.today():
            raise ValueError("Document expiry date cannot be in the past")
        return v


class RentalVehicleDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vehicle_id: int
    document_type: str
    document_url: str
    expiry_date: date | None = None
    is_verified: bool = False
    created_at: datetime


class RentalVehicleListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    provider_business_name: str | None = None
    is_provider_verified: bool = False
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None = None
    price_per_day: Decimal
    currency: str = "LKR"
    status: VehicleStatus
    model_year: int | None = None
    seating_capacity: int = 4
    transmission: str = "manual"
    fuel_type: str = "petrol"
    district: str | None = None
    city: str | None = None
    location_id: int | None = None
    location_name: str | None = None
    primary_image_url: str | None = None
    is_sponsored: bool = False
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    security_deposit: Decimal | None = None
    is_insured: bool = False
    insurance_status: str | None = None
    fuel_policy: FuelPolicy = "same_to_same"
    helmets: VehicleHelmetInfo | None = None
    created_at: datetime


class RentalVehiclePagination(BaseModel):
    total: int
    page: int
    limit: int
    pages: int
    items: list[RentalVehicleListItem]


class VehicleInsuranceSummary(BaseModel):
    is_insured: bool = False
    insurance_status: Literal["verified", "unverified", "expired", "not_provided"] = "not_provided"
    policy_number_masked: str | None = None
    expiry_date: date | None = None
    coverage_amount: Decimal | None = None
    deductible_amount: Decimal | None = None
    insurance_provider: str | None = None


class RentalVehicleDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    provider_business_name: str | None = None
    is_provider_verified: bool = False
    vehicle_type: VehicleType
    vehicle_name: str
    description: str | None = None
    price_per_day: Decimal
    currency: str = "LKR"
    status: VehicleStatus
    model_year: int | None = None
    seating_capacity: int = 4
    transmission: str = "manual"
    fuel_type: str = "petrol"
    district: str | None = None
    city: str | None = None
    location_id: int | None = None
    location: RentalLocationResponse | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int = 24
    security_deposit: Decimal = Decimal("0.00")
    security_deposit_terms: str = "Refundable security deposit collected at vehicle pickup and returned following clean vehicle return inspection."
    insurance: VehicleInsuranceSummary | None = None
    fuel_policy: FuelPolicy = "same_to_same"
    helmets: VehicleHelmetInfo | None = None
    primary_image_url: str | None = None
    images: list[RentalVehicleImageResponse] = []
    rate_plans: list[RentalRatePlanResponse] = []
    approval_status: str
    is_sponsored: bool = False
    created_at: datetime
    updated_at: datetime


# ==============================================================================
# TR-17: PROVIDER CONTACT SCHEMAS
# ==============================================================================

class RentalProviderContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider_id: int
    business_name: str
    phone: str
    secondary_phone: str | None = None
    district: str
    city: str | None = None
    province: str | None = None
    business_address: str | None = None
    approval_status: str
    is_verified: bool


class RentalPickupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    location_name: str = Field(min_length=2, max_length=150)
    district: str = Field(min_length=2, max_length=100)
    city: str = Field(min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    latitude: Decimal = Field(ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal = Field(ge=-180, le=180, max_digits=9, decimal_places=6)


class RentalPickupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    location_name: str
    district: str
    city: str
    address: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    is_active: bool
