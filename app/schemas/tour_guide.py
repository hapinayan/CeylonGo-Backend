from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


LanguageProficiency = Literal["basic", "conversational", "fluent", "native"]
LicenseType = Literal["national", "chauffeur", "site", "area"]
PricingType = Literal["per_person", "per_group"]
GuideApprovalStatus = Literal["draft", "pending", "approved", "rejected", "suspended"]
GuideVerificationStatus = Literal["unverified", "pending", "verified", "rejected", "suspended"]
TourGuideBookingStatus = Literal[
    "pending", "confirmed", "in_progress", "completed", "cancelled", "rejected", "expired"
]
CredentialType = Literal[
    "sltda_license", "first_aid", "degree", "wildlife_permit", "driving_license", "other"
]


# ==========================================
# LANGUAGE SCHEMAS
# ==========================================

class TourGuideLanguageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_code: str = Field(min_length=2, max_length=10)
    language_name: str = Field(min_length=2, max_length=50)
    proficiency_level: LanguageProficiency = "fluent"
    is_primary: bool = False


class TourGuideLanguageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    language_code: str
    language_name: str
    proficiency_level: str
    is_primary: bool


# ==========================================
# SERVICE AREA SCHEMAS
# ==========================================

class TourGuideServiceAreaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str = Field(min_length=2, max_length=100)
    district: str = Field(min_length=2, max_length=100)
    city: str = Field(default="", max_length=100)
    is_primary: bool = False


class TourGuideServiceAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    province: str
    district: str
    city: str
    is_primary: bool


# ==========================================
# PACKAGE SCHEMAS
# ==========================================

class TourGuidePackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=4000)
    itinerary: str | None = None
    duration_hours: Decimal = Field(gt=Decimal("0.00"), le=Decimal("720.00"))
    pricing_type: PricingType = "per_person"
    price: Decimal = Field(ge=Decimal("0.00"))
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    max_people: int = Field(default=10, gt=0, le=200)
    included_services: str | None = Field(default=None, max_length=2000)
    excluded_services: str | None = Field(default=None, max_length=2000)
    meeting_point: str = Field(min_length=3, max_length=500)
    meeting_latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    meeting_longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    is_active: bool = True


class TourGuidePackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, min_length=10, max_length=4000)
    itinerary: str | None = None
    duration_hours: Decimal | None = Field(default=None, gt=Decimal("0.00"), le=Decimal("720.00"))
    pricing_type: PricingType | None = None
    price: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    max_people: int | None = Field(default=None, gt=0, le=200)
    included_services: str | None = Field(default=None, max_length=2000)
    excluded_services: str | None = Field(default=None, max_length=2000)
    meeting_point: str | None = Field(default=None, min_length=3, max_length=500)
    meeting_latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    meeting_longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    is_active: bool | None = None


class TourGuidePackageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    title: str
    description: str
    itinerary: str | None = None
    duration_hours: Decimal
    pricing_type: str
    price: Decimal
    currency: str
    max_people: int
    included_services: str | None = None
    excluded_services: str | None = None
    meeting_point: str
    meeting_latitude: Decimal | None = None
    meeting_longitude: Decimal | None = None
    is_active: bool


# ==========================================
# TOUR GUIDE PROFILE SCHEMAS
# ==========================================

class TourGuideProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bio: str = Field(min_length=10, max_length=2000)
    years_of_experience: int = Field(default=0, ge=0)
    specialties: str | None = Field(default=None, max_length=500)
    license_number: str | None = Field(default=None, max_length=100)
    license_type: LicenseType | None = None
    license_expiry_date: date | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    daily_rate: Decimal = Field(ge=Decimal("0.00"))
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    base_city: str = Field(min_length=2, max_length=100)
    base_district: str = Field(min_length=2, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    base_longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    max_group_size: int = Field(default=10, gt=0, le=100)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0)
    is_available: bool = True

    @model_validator(mode="after")
    def validate_coordinates(self):
        lat = self.base_latitude
        lng = self.base_longitude
        if (lat is None and lng is not None) or (lat is not None and lng is None):
            raise ValueError("Both base_latitude and base_longitude must be provided together")
        return self


class TourGuideProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bio: str | None = Field(default=None, min_length=10, max_length=2000)
    years_of_experience: int | None = Field(default=None, ge=0)
    specialties: str | None = Field(default=None, max_length=500)
    license_number: str | None = Field(default=None, max_length=100)
    license_type: LicenseType | None = None
    license_expiry_date: date | None = None
    hourly_rate: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    daily_rate: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    base_city: str | None = Field(default=None, min_length=2, max_length=100)
    base_district: str | None = Field(default=None, min_length=2, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=Decimal("-90.0"), le=Decimal("90.0"))
    base_longitude: Decimal | None = Field(default=None, ge=Decimal("-180.0"), le=Decimal("180.0"))
    max_group_size: int | None = Field(default=None, gt=0, le=100)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0)
    is_available: bool | None = None
    is_active: bool | None = None


class TourGuideProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    bio: str
    years_of_experience: int
    specialties: str | None = None
    license_number: str | None = None
    license_type: str | None = None
    license_expiry_date: date | None = None
    hourly_rate: Decimal | None = None
    daily_rate: Decimal
    currency: str
    base_city: str
    base_district: str
    base_latitude: Decimal | None = None
    base_longitude: Decimal | None = None
    is_available: bool
    is_active: bool
    approval_status: str
    verification_status: str
    rating_average: Decimal
    review_count: int
    cancellation_policy: str | None = None
    free_cancellation_hours: int
    max_group_size: int
    created_at: datetime
    updated_at: datetime
    languages: list[TourGuideLanguageResponse] = []
    service_areas: list[TourGuideServiceAreaResponse] = []
    packages: list[TourGuidePackageResponse] = []


# ==========================================
# BOOKING SCHEMAS
# ==========================================

class TourGuideBookingProviderItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    traveler_id: int
    package_id: int | None = None
    booking_date: date
    start_datetime: datetime
    end_datetime: datetime
    number_of_people: int
    pickup_location: str | None = None
    special_requests: str | None = None
    total_price: Decimal
    currency: str
    booking_status: str
    payment_method: str
    payment_status: str
    package_title: str | None = None
    traveler_name: str | None = None
    traveler_email: str | None = None


class TourGuideBookingProviderListResponse(BaseModel):
    items: list[TourGuideBookingProviderItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ==========================================
# CREDENTIAL SCHEMAS (TG-03)
# ==========================================

class TourGuideCredentialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_type: CredentialType
    title: str = Field(min_length=2, max_length=150)
    issuing_authority: str = Field(min_length=2, max_length=150)
    certificate_number: str | None = Field(default=None, max_length=100)
    issue_date: date | None = None
    expiry_date: date | None = None
    file_reference: str = Field(min_length=3, max_length=1000)
    is_confidential: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValueError("expiry_date cannot be earlier than issue_date")
        return self


class TourGuideCredentialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    credential_type: str
    title: str
    issuing_authority: str
    certificate_number: str | None
    issue_date: date | None
    expiry_date: date | None
    is_confidential: bool
    verification_status: str
    created_at: datetime
    updated_at: datetime


# ==========================================
# AVAILABILITY SCHEMAS (TG-05)
# ==========================================

class TourGuideAvailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_date: date
    start_time: time | None = None
    end_time: time | None = None
    is_available: bool = True
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_times(self):
        if (self.start_time is None and self.end_time is not None) or (
            self.start_time is not None and self.end_time is None
        ):
            raise ValueError(
                "start_time and end_time must either both be provided or both be null"
            )
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class TourGuideAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    available_date: date
    start_time: time | None
    end_time: time | None
    is_available: bool
    reason: str | None
    created_at: datetime


