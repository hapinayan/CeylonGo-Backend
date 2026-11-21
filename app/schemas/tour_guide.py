from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.schemas.provider_registration import validate_sri_lankan_phone
from app.services.tour_guide_sync import (
    classify_package_duration,
    extract_category_slugs,
    normalize_specialties_string,
    validate_currency_code,
    validate_language_code,
)

GuideCategorySlug = Literal[
    "cultural_heritage",
    "adventure_nature",
    "city_village",
    "wildlife_eco",
]

ProficiencyLevel = Literal["basic", "conversational", "fluent", "native"]

CredentialType = Literal[
    "sltda_license",
    "first_aid",
    "degree",
    "wildlife_permit",
    "driving_license",
    "other",
]

PackagePricingType = Literal["per_person", "per_group"]


# =============================================================================
# 1. CORE TOUR GUIDE PROFILE SCHEMAS
# =============================================================================

class TourGuideCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bio: str = Field(min_length=10, max_length=2000)
    years_of_experience: int = Field(default=0, ge=0, le=2147483647)
    specialties: str | None = Field(default=None, max_length=500)

    license_number: str | None = Field(default=None, max_length=100)
    license_type: Literal["national", "chauffeur", "site", "area"] | None = None
    license_expiry_date: date | None = None

    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    daily_rate: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="LKR", min_length=3, max_length=3)

    base_city: str = Field(min_length=1, max_length=100)
    base_district: str = Field(min_length=1, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    base_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)

    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0, le=2147483647)
    max_group_size: int = Field(default=10, gt=0, le=2147483647)

    @field_validator("specialties")
    @classmethod
    def validate_specialties(cls, value: str | None) -> str | None:
        return normalize_specialties_string(value)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.base_latitude is None) != (self.base_longitude is None):
            raise ValueError(
                "Latitude and longitude must be provided together"
            )
        return self


class TourGuideUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bio: str | None = Field(default=None, min_length=10, max_length=2000)
    years_of_experience: int | None = Field(default=None, ge=0, le=2147483647)
    specialties: str | None = Field(default=None, max_length=500)

    license_number: str | None = Field(default=None, max_length=100)
    license_type: Literal["national", "chauffeur", "site", "area"] | None = None
    license_expiry_date: date | None = None

    hourly_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    daily_rate: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    base_city: str | None = Field(default=None, min_length=1, max_length=100)
    base_district: str | None = Field(default=None, min_length=1, max_length=100)
    base_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    base_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)

    is_available: bool | None = None
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0, le=2147483647)
    max_group_size: int | None = Field(default=None, gt=0, le=2147483647)

    @field_validator("specialties")
    @classmethod
    def validate_specialties(cls, value: str | None) -> str | None:
        return normalize_specialties_string(value)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_currency_code(value)

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        required_fields = (
            "bio",
            "years_of_experience",
            "daily_rate",
            "currency",
            "base_city",
            "base_district",
            "is_available",
            "free_cancellation_hours",
            "max_group_size",
        )
        for field in required_fields:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

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
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    rating_average: Decimal
    review_count: int
    cancellation_policy: str | None = None
    free_cancellation_hours: int
    max_group_size: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def categories(self) -> list[str]:
        return extract_category_slugs(self.specialties)


# =============================================================================
# 2. GUIDE CATEGORIES SCHEMAS (PR-07 / TR-03)
# =============================================================================

class GuideCategoryInfo(BaseModel):
    slug: str
    name: str
    examples: str


class GuideCategoriesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[GuideCategorySlug] = Field(min_length=1, max_length=4)


# =============================================================================
# 3. LANGUAGES SCHEMAS (PR-05 / TR-06)
# =============================================================================

class TourGuideLanguageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_code: str = Field(min_length=2, max_length=10)
    language_name: str = Field(min_length=2, max_length=50)
    proficiency_level: ProficiencyLevel = "fluent"
    is_primary: bool = False

    @field_validator("language_code")
    @classmethod
    def check_language_code(cls, value: str) -> str:
        return validate_language_code(value)

    @field_validator("language_name")
    @classmethod
    def clean_language_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("language_name must be at least 2 characters")
        return cleaned


class TourGuideLanguageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language_name: str | None = Field(default=None, min_length=2, max_length=50)
    proficiency_level: ProficiencyLevel | None = None
    is_primary: bool | None = None

    @field_validator("language_name")
    @classmethod
    def clean_language_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("language_name must be at least 2 characters")
        return cleaned

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in ("language_name", "proficiency_level", "is_primary"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideLanguageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    language_code: str
    language_name: str
    proficiency_level: str
    is_primary: bool
    created_at: datetime


# =============================================================================
# 4. SERVICE AREAS SCHEMAS (PR-06 / TR-07)
# =============================================================================

class TourGuideServiceAreaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str = Field(min_length=2, max_length=100)
    district: str = Field(min_length=2, max_length=100)
    city: str = Field(default="", max_length=100)
    is_primary: bool = False

    @field_validator("province", "district")
    @classmethod
    def clean_required_place(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("Value must be at least 2 characters")
        return cleaned

    @field_validator("city")
    @classmethod
    def clean_city(cls, value: str) -> str:
        return " ".join(value.strip().split())


class TourGuideServiceAreaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    province: str | None = Field(default=None, min_length=2, max_length=100)
    district: str | None = Field(default=None, min_length=2, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    is_primary: bool | None = None

    @field_validator("province", "district")
    @classmethod
    def clean_required_place(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("Value must be at least 2 characters")
        return cleaned

    @field_validator("city")
    @classmethod
    def clean_city(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.strip().split())

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in ("province", "district", "city", "is_primary"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideServiceAreaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    province: str
    district: str
    city: str
    is_primary: bool
    created_at: datetime


# =============================================================================
# 5. PROFILE IMAGE GALLERY SCHEMAS (PR-03 / TR-05)
# =============================================================================

class TourGuideImageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str | None = Field(default=None, max_length=255)
    display_order: int | None = Field(default=None, ge=0, le=2147483647)
    is_profile: bool | None = None

    @model_validator(mode="after")
    def reject_nulls(self):
        for field in ("display_order", "is_profile"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    image_url: str
    caption: str | None = None
    display_order: int
    is_profile: bool
    created_at: datetime


# =============================================================================
# 6. PRIVATE CREDENTIALS & QUALIFICATIONS SCHEMAS (PR-04 / GS-02 / AD-02)
# =============================================================================

class TourGuideCredentialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_type: CredentialType | None = None
    title: str | None = Field(default=None, min_length=2, max_length=150)
    issuing_authority: str | None = Field(default=None, min_length=2, max_length=150)
    certificate_number: str | None = Field(default=None, max_length=100)
    issue_date: date | None = None
    expiry_date: date | None = None

    @model_validator(mode="after")
    def validate_dates_and_nulls(self):
        for field in ("credential_type", "title", "issuing_authority"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.issue_date and self.expiry_date and self.expiry_date < self.issue_date:
            raise ValueError("expiry_date must be on or after issue_date")
        return self


class TourGuideCredentialResponse(BaseModel):
    """
    Safe credential metadata response for owners & admins.
    NEVER exposes internal filesystem paths (file_reference).
    """
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: int
    tour_guide_id: int
    credential_type: str
    title: str
    issuing_authority: str
    certificate_number: str | None = None
    issue_date: date | None = None
    expiry_date: date | None = None
    is_confidential: bool
    verification_status: str
    has_document: bool = True
    created_at: datetime
    updated_at: datetime


class TourGuideVerificationRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    credential_id: int
    reviewed_by: int
    verification_type: str
    status: str
    notes: str | None = None
    rejection_reason: str | None = None
    verified_at: datetime
    created_at: datetime


# =============================================================================
# 7. TOUR PACKAGES SCHEMAS (PR-08 / PR-09 / GS-03 / GS-04 / GS-05)
# =============================================================================

class TourGuidePackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=150)
    description: str = Field(min_length=10, max_length=4000)
    itinerary: str | None = None
    duration_hours: Decimal = Field(gt=0, max_digits=5, decimal_places=2)
    pricing_type: PackagePricingType = "per_person"
    price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    max_people: int = Field(default=10, gt=0, le=2147483647)
    included_services: str | None = Field(default=None, max_length=2000)
    excluded_services: str | None = Field(default=None, max_length=2000)
    meeting_point: str = Field(min_length=3, max_length=500)
    meeting_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    meeting_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    is_active: bool = True

    @field_validator("currency")
    @classmethod
    def check_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_meeting_coordinates(self):
        if (self.meeting_latitude is None) != (self.meeting_longitude is None):
            raise ValueError(
                "meeting_latitude and meeting_longitude must be provided together"
            )
        return self


class TourGuidePackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, min_length=10, max_length=4000)
    itinerary: str | None = None
    duration_hours: Decimal | None = Field(default=None, gt=0, max_digits=5, decimal_places=2)
    pricing_type: PackagePricingType | None = None
    price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    max_people: int | None = Field(default=None, gt=0, le=2147483647)
    included_services: str | None = Field(default=None, max_length=2000)
    excluded_services: str | None = Field(default=None, max_length=2000)
    meeting_point: str | None = Field(default=None, min_length=3, max_length=500)
    meeting_latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    meeting_longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    is_active: bool | None = None

    @field_validator("currency")
    @classmethod
    def check_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_currency_code(value)

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        required_fields = (
            "title",
            "description",
            "duration_hours",
            "pricing_type",
            "price",
            "currency",
            "max_people",
            "meeting_point",
            "is_active",
        )
        for field in required_fields:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


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
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def duration_category(self) -> str:
        return classify_package_duration(float(self.duration_hours))


# =============================================================================
# 8. AVAILABILITY CALENDAR SCHEMAS (PR-10 / TR-10)
# =============================================================================

class TourGuideAvailabilityCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_date: date
    start_time: time | None = None
    end_time: time | None = None
    is_available: bool = True
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_times(self):
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must both be provided or both be null")
        if self.start_time is not None and self.end_time is not None and self.end_time <= self.start_time:
            raise ValueError("end_time must be strictly after start_time")
        return self


class TourGuideAvailabilityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    is_available: bool | None = None
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def reject_null_required_fields(self):
        for field in ("available_date", "is_available"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class TourGuideAvailabilityBulkBlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_date_range(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if (self.end_date - self.start_date).days > 90:
            raise ValueError("Bulk availability block cannot exceed 90 days per request")
        return self


class TourGuideAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tour_guide_id: int
    available_date: date
    start_time: time | None = None
    end_time: time | None = None
    is_available: bool
    reason: str | None = None
    created_at: datetime


# =============================================================================
# 9. PROVIDER CONTACT & VERIFICATION STATUS SCHEMAS (PR-17 / PR-18)
# =============================================================================

class TourGuideContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str | None = Field(default=None, min_length=9, max_length=25)
    secondary_phone: str | None = Field(default=None, max_length=25)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_sri_lankan_phone(value)

    @field_validator("secondary_phone")
    @classmethod
    def check_secondary_phone(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return validate_sri_lankan_phone(value)

    @model_validator(mode="after")
    def reject_null_primary_phone(self):
        if "phone" in self.model_fields_set and self.phone is None:
            raise ValueError("phone cannot be null")
        return self


class TourGuideContactResponse(BaseModel):
    tour_guide_id: int
    provider_profile_id: int
    business_name: str
    email: str
    phone: str
    secondary_phone: str | None = None
    base_city: str
    base_district: str


class TourGuideVerificationStatusResponse(BaseModel):
    tour_guide_id: int
    provider_approval_status: str
    guide_approval_status: str
    verification_status: str
    is_verified_badge: bool
    is_active: bool
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    years_of_experience: int
    experience_verified: bool
    credentials_summary: dict[str, int]
    credentials: list[TourGuideCredentialResponse]
    verification_history: list[TourGuideVerificationRecordResponse]


# =============================================================================
# 10. ADMIN REVIEW, MODERATION, CREDENTIAL & BADGE SCHEMAS (AD-01..AD-04)
# =============================================================================

class AdminTourGuideListItem(BaseModel):
    id: int
    provider_profile_id: int
    business_name: str
    provider_name: str
    email: str
    base_city: str
    base_district: str
    daily_rate: Decimal
    currency: str
    approval_status: str
    verification_status: str
    is_active: bool
    is_available: bool
    years_of_experience: int
    rating_average: Decimal
    review_count: int
    pending_credentials_count: int
    approved_credentials_count: int
    created_at: datetime


class AdminTourGuideListResponse(BaseModel):
    items: list[AdminTourGuideListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminTourGuideDetailResponse(BaseModel):
    guide: TourGuideResponse
    business_name: str
    provider_name: str
    email: str
    phone: str
    secondary_phone: str | None = None
    provider_approval_status: str
    languages: list[TourGuideLanguageResponse]
    service_areas: list[TourGuideServiceAreaResponse]
    images: list[TourGuideImageResponse]
    packages: list[TourGuidePackageResponse]
    credentials: list[TourGuideCredentialResponse]
    verification_history: list[TourGuideVerificationRecordResponse]


class AdminTourGuideDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class AdminTourGuideActionResponse(BaseModel):
    success: bool
    tour_guide_id: int
    approval_status: str
    verification_status: str
    is_active: bool
    rejection_reason: str | None = None
    listing_reference_synced: bool
    message: str


class AdminCredentialListItem(BaseModel):
    credential: TourGuideCredentialResponse
    business_name: str
    guide_approval_status: str
    guide_verification_status: str


class AdminCredentialListResponse(BaseModel):
    items: list[AdminCredentialListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class AdminCredentialVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["approved", "rejected", "requires_info"]
    verification_type: str = Field(default="credential_review", min_length=2, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    rejection_reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_reason_requirements(self):
        if self.status == "rejected":
            if not self.rejection_reason or len(self.rejection_reason.strip()) < 5:
                raise ValueError(
                    "rejection_reason (at least 5 characters) is required when rejecting a credential"
                )
        if self.status == "requires_info":
            if not self.notes or len(self.notes.strip()) < 5:
                raise ValueError(
                    "notes (at least 5 characters) are required when requesting additional info"
                )
        return self


class AdminCredentialVerifyResponse(BaseModel):
    credential: TourGuideCredentialResponse
    verification: TourGuideVerificationRecordResponse
    guide_verification_status: str
    message: str


class AdminBadgeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_status: Literal["verified", "rejected", "suspended", "unverified"]
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_revocation_reason(self):
        if self.verification_status in ("rejected", "suspended", "unverified"):
            if not self.reason or len(self.reason.strip()) < 5:
                raise ValueError(
                    "reason (at least 5 characters) is required when revoking or rejecting verification"
                )
        return self


class AdminBadgeUpdateResponse(BaseModel):
    success: bool
    tour_guide_id: int
    verification_status: str
    is_verified_badge: bool
    message: str


# =============================================================================
# 11. PUBLIC / TRAVELER DISCOVERY, DETAIL, PACKAGES, CALENDAR & PREVIEW (PHASE 2)
# =============================================================================

PublicGuideSortBy = Literal[
    "recommended",
    "price_asc",
    "price_desc",
    "rating_desc",
    "experience_desc",
    "newest",
]

PriceFilterBasis = Literal["daily", "hourly", "package", "any"]


class PublicGuideLanguageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    language_code: str
    language_name: str
    proficiency_level: str
    is_primary: bool


class PublicGuideServiceAreaSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    province: str
    district: str
    city: str
    is_primary: bool


class PublicGuideImageSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_url: str
    caption: str | None = None
    display_order: int
    is_profile: bool


class PublicGuideCredentialBadge(BaseModel):
    """
    Strictly public-safe representation of an admin-approved credential.
    NEVER includes file_reference, certificate_number, or unapproved items.
    """
    id: int
    credential_type: str
    title: str
    issuing_authority: str
    expiry_date: date | None = None
    verified: bool = True


class PublicTourGuideSummary(BaseModel):
    id: int
    business_name: str
    display_name: str
    bio: str
    years_of_experience: int
    verified_years_of_experience: int | None = None
    experience_is_verified: bool
    experience_label: str
    specialties: str | None = None
    categories: list[str]
    hourly_rate: Decimal | None = None
    daily_rate: Decimal
    currency: str
    base_city: str
    base_district: str
    base_latitude: Decimal | None = None
    base_longitude: Decimal | None = None
    is_available: bool
    is_verified: bool
    verification_status: str
    is_sponsored: bool = False
    rating_average: Decimal
    review_count: int
    max_group_size: int
    primary_image_url: str | None = None
    languages: list[PublicGuideLanguageSummary]
    service_areas: list[PublicGuideServiceAreaSummary]
    active_packages_count: int
    min_package_price: Decimal | None = None


class PublicTourGuideListResponse(BaseModel):
    items: list[PublicTourGuideSummary]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_more: bool


FitnessLevel = Literal["low", "moderate", "challenging", "strenuous"]


class PackageSpecialRequirementsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fitness_level: FitnessLevel | None = None
    clothing: str | None = Field(default=None, max_length=500)
    equipment: str | None = Field(default=None, max_length=500)
    other_notes: str | None = Field(default=None, max_length=600)

    @field_validator("clothing", "equipment", "other_notes")
    @classmethod
    def clean_req_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class PublicTourGuidePackageResponse(BaseModel):
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
    meeting_instructions: str | None = None
    special_requirements: PackageSpecialRequirementsPayload | None = None

    @computed_field
    @property
    def duration_category(self) -> str:
        return classify_package_duration(float(self.duration_hours))


class PublicTourGuidePackageDetailResponse(BaseModel):
    package: PublicTourGuidePackageResponse
    guide_id: int
    guide_business_name: str
    guide_display_name: str
    guide_base_city: str
    guide_base_district: str
    guide_is_verified: bool
    guide_rating_average: Decimal
    guide_review_count: int
    cancellation_policy: str | None = None
    free_cancellation_hours: int


class PublicTourGuideDetailResponse(BaseModel):
    id: int
    business_name: str
    display_name: str
    bio: str
    years_of_experience: int
    verified_years_of_experience: int | None = None
    experience_is_verified: bool
    experience_label: str
    specialties: str | None = None
    categories: list[str]
    license_type: str | None = None
    hourly_rate: Decimal | None = None
    daily_rate: Decimal
    currency: str
    base_city: str
    base_district: str
    base_latitude: Decimal | None = None
    base_longitude: Decimal | None = None
    is_available: bool
    is_verified: bool
    verification_status: str
    is_sponsored: bool = False
    rating_average: Decimal
    review_count: int
    cancellation_policy: str | None = None
    free_cancellation_hours: int
    max_group_size: int
    images: list[PublicGuideImageSummary]
    languages: list[PublicGuideLanguageSummary]
    service_areas: list[PublicGuideServiceAreaSummary]
    verified_credentials: list[PublicGuideCredentialBadge]
    packages: list[PublicTourGuidePackageResponse]


class PublicTimeWindow(BaseModel):
    start_time: time | None = None
    end_time: time | None = None
    full_day: bool = False
    status: Literal["open", "blocked", "booked"]


class PublicAvailabilityDay(BaseModel):
    date: date
    status: Literal["available", "partially_available", "unavailable", "booked"]
    is_bookable: bool
    open_windows: list[PublicTimeWindow]
    blocked_windows: list[PublicTimeWindow]
    booked_windows: list[PublicTimeWindow]


class PublicGuideAvailabilityCalendarResponse(BaseModel):
    tour_guide_id: int
    date_from: date
    date_to: date
    guide_is_available: bool
    days: list[PublicAvailabilityDay]


class TourGuidePricePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_id: int | None = Field(default=None, gt=0)
    rate_type: Literal["package", "hourly", "daily"] = "package"
    number_of_people: int = Field(ge=1, le=2147483647)
    duration_hours: Decimal | None = Field(default=None, gt=0, le=Decimal("240.00"))
    duration_days: int | None = Field(default=None, ge=1, le=30)
    booking_date: date | None = None
    start_time: time | None = None

    @model_validator(mode="after")
    def validate_rate_inputs(self):
        if self.package_id is not None and self.rate_type != "package":
            raise ValueError("package_id can only be supplied when rate_type is 'package'")
        if self.rate_type == "package" and self.package_id is None:
            raise ValueError("package_id is required when rate_type is 'package'")
        if self.rate_type == "hourly" and self.duration_hours is None:
            raise ValueError("duration_hours is required when rate_type is 'hourly'")
        if self.rate_type == "daily" and self.duration_days is None:
            raise ValueError("duration_days is required when rate_type is 'daily'")
        return self


class TourGuidePricePreviewResponse(BaseModel):
    tour_guide_id: int
    package_id: int | None = None
    package_title: str | None = None
    rate_type: Literal["package", "hourly", "daily"]
    pricing_type: str
    unit_price: Decimal
    quantity_description: str
    number_of_people: int
    max_group_size: int
    duration_hours: Decimal
    duration_category: str
    subtotal: Decimal
    total_price: Decimal
    currency: str
    estimated_start_datetime: datetime | None = None
    estimated_end_datetime: datetime | None = None
    is_available_for_requested_window: bool | None = None
    availability_note: str | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int


class PublicGuideContactResponse(BaseModel):
    tour_guide_id: int
    business_name: str
    display_name: str
    base_city: str
    base_district: str
    is_verified: bool
    verification_status: str
    is_authenticated_view: bool
    requires_authentication_for_direct_contact: bool
    phone: str
    secondary_phone: str | None = None
    whatsapp_available: bool = False
    whatsapp_number: str | None = None
    whatsapp_link: str | None = None
    available_channels: list[str]
    privacy_note: str


# =============================================================================
# 12. BOOKING ENGINE, CASH PAYMENT, CANCELLATION & ADMIN MONITORING (PHASE 3)
# =============================================================================

BookingPaymentMethod = Literal["pay_at_location", "online"]
TravelerBookingBucket = Literal["all", "upcoming", "past", "cancelled"]
ProviderBookingBucket = Literal["all", "requests", "upcoming", "completed", "cancelled", "past"]


class TourGuideBookingCreate(BaseModel):
    """
    Traveler booking creation payload.
    Strict extra='forbid' prevents client-side price, status, or currency tampering.
    """
    model_config = ConfigDict(extra="forbid")

    package_id: int | None = Field(default=None, gt=0)
    rate_type: Literal["package", "hourly", "daily"] = "package"
    booking_date: date
    start_time: time = Field(default=time(8, 0))
    duration_hours: Decimal | None = Field(default=None, gt=0, le=Decimal("240.00"))
    duration_days: int | None = Field(default=None, ge=1, le=30)
    number_of_people: int = Field(ge=1, le=2147483647)
    pickup_location: str | None = Field(default=None, max_length=500)
    special_requests: str | None = Field(default=None, max_length=1000)
    payment_method: BookingPaymentMethod = "pay_at_location"

    @model_validator(mode="after")
    def validate_booking_rate_mode(self):
        if self.package_id is not None and self.rate_type != "package":
            raise ValueError("package_id can only be supplied when rate_type is 'package'")
        if self.rate_type == "package" and self.package_id is None:
            raise ValueError("package_id is required when rate_type is 'package'")
        if self.rate_type == "hourly" and self.duration_hours is None:
            raise ValueError("duration_hours is required when rate_type is 'hourly'")
        if self.rate_type == "daily" and self.duration_days is None:
            raise ValueError("duration_days is required when rate_type is 'daily'")
        return self


class BookingReasonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("reason must be at least 5 characters")
        return cleaned


class BookingOptionalNotesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class CashPaymentRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class BookingStatusHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_status: str | None = None
    to_status: str
    changed_by: int | None = None
    notes: str | None = None
    created_at: datetime


class RefundEligibilitySummary(BaseModel):
    is_free_cancellation: bool
    free_cancellation_hours: int
    hours_until_start: float
    eligible_refund_percent: int
    eligible_refund_amount: Decimal
    currency: str
    policy_note: str


class TourGuideBookingResponse(BaseModel):
    id: int
    booking_reference: str | None = None
    tour_guide_id: int
    guide_business_name: str
    guide_display_name: str
    guide_base_city: str
    guide_base_district: str
    traveler_id: int
    traveler_name: str
    package_id: int | None = None
    package_title: str | None = None
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
    is_online_payment: bool
    cash_payment_status: str | None = None
    expires_at: datetime | None = None
    cancellation_policy_snapshot: str | None = None
    free_cancellation_hours_snapshot: int
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TourGuideBookingDetailResponse(TourGuideBookingResponse):
    refund_eligibility: RefundEligibilitySummary | None = None
    status_history: list[BookingStatusHistoryItem] = Field(default_factory=list)


class TourGuideBookingListResponse(BaseModel):
    items: list[TourGuideBookingResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_more: bool


class TourGuideBookingCancelResponse(BaseModel):
    booking: TourGuideBookingDetailResponse
    refund_eligibility: RefundEligibilitySummary
    availability_released: bool
    message: str


class ProviderCalendarBookingItem(BaseModel):
    booking_id: int
    booking_reference: str | None = None
    traveler_name: str
    package_title: str | None = None
    start_datetime: datetime
    end_datetime: datetime
    number_of_people: int
    booking_status: str
    payment_method: str
    payment_status: str


class ProviderCalendarDay(BaseModel):
    date: date
    availability_slots: list[TourGuideAvailabilityResponse]
    bookings: list[ProviderCalendarBookingItem]
    has_confirmed_booking: bool
    has_pending_request: bool
    is_blocked: bool


class ProviderBookingCalendarResponse(BaseModel):
    tour_guide_id: int
    date_from: date
    date_to: date
    guide_is_available: bool
    days: list[ProviderCalendarDay]


class AdminTourGuideBookingListResponse(BaseModel):
    items: list[TourGuideBookingResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_more: bool
    status_counts: dict[str, int]
    issues_count: int


# =============================================================================
# 13. ONLINE PAYMENT PREPARATION, COMMISSION, REFUNDS & EARNINGS (PHASE 4)
# =============================================================================

class TourGuideCommissionBreakdown(BaseModel):
    gross_amount: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    provider_payable_amount: Decimal
    currency: str = "LKR"


class TourGuidePaymentPrepareResponse(BaseModel):
    booking_id: int
    booking_reference: str
    payment_transaction_id: int
    payment_mode: str
    merchant_id: str
    checkout_url: str
    order_id: str
    items: str
    amount: Decimal
    currency: str
    hash: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    return_url: str
    cancel_url: str
    notify_url: str
    commission_preview: TourGuideCommissionBreakdown


class TourGuideRefundRecordItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_transaction_id: int
    amount: Decimal
    currency: str
    refund_reason: str
    refund_status: str
    processed_by: int | None = None
    processed_at: datetime | None = None
    created_at: datetime


class TourGuidePaymentStatusResponse(BaseModel):
    booking_id: int
    booking_reference: str | None = None
    booking_status: str
    payment_method: str
    payment_status: str
    is_online_payment: bool
    cash_payment_status: str | None = None
    total_price: Decimal
    currency: str
    paid_at: datetime | None = None
    transaction_id: int | None = None
    gateway_transaction_id: str | None = None
    commission_breakdown: TourGuideCommissionBreakdown | None = None
    refund_eligibility: RefundEligibilitySummary | None = None
    refunds: list[TourGuideRefundRecordItem] = Field(default_factory=list)


class TourGuideRefundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=5, max_length=1000)

    @field_validator("reason")
    @classmethod
    def clean_refund_reason(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 5:
            raise ValueError("reason must be at least 5 characters")
        return cleaned


class TourGuideRefundResponse(BaseModel):
    id: int
    booking_id: int
    booking_reference: str
    payment_transaction_id: int
    amount: Decimal
    currency: str
    refund_reason: str
    refund_status: str
    refund_eligibility: RefundEligibilitySummary
    processed_by: int | None = None
    processed_at: datetime | None = None
    created_at: datetime


class TourGuideProviderEarningsResponse(BaseModel):
    total_online_gross_earnings: Decimal
    completed_online_booking_earnings: Decimal
    ceylongo_commission: Decimal
    verified_refunds: Decimal
    provider_refund_reduction: Decimal
    provider_net_online_earnings: Decimal
    pending_online_earnings: Decimal
    total_settled: Decimal
    reserved_settlement: Decimal
    available_settlement_balance: Decimal
    outstanding_liability: Decimal
    total_paid_online_bookings: int
    cash_collected_total: Decimal
    total_cash_paid_bookings: int
    currency: str = "LKR"


# =============================================================================
# 14. REVIEWS, WISHLISTS, DASHBOARD, PROMOTIONS & ADMIN REPORTS (PHASE 5)
# =============================================================================

class TourGuideReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    booking_id: int = Field(gt=0)
    rating: Decimal = Field(ge=Decimal("1.00"), le=Decimal("5.00"))
    title: str | None = Field(default=None, max_length=150)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "comment")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class TourGuideReviewResponse(BaseModel):
    id: int
    tour_guide_id: int
    listing_reference_id: int
    booking_reference_id: int | None = None
    user_id: int
    reviewer_name: str
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool
    is_published: bool
    created_at: datetime


class TourGuideReviewListResponse(BaseModel):
    items: list[TourGuideReviewResponse]
    total: int
    average_rating: Decimal
    rating_distribution: dict[str, int]
    page: int
    page_size: int
    total_pages: int
    has_more: bool


class TourGuideReviewModerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_published: bool
    reason: str | None = Field(default=None, max_length=500)


class TourGuideWishlistAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str | None = Field(default=None, max_length=500)


class TourGuideWishlistActionResponse(BaseModel):
    message: str
    tour_guide_id: int
    saved: bool
    wishlist_id: int | None = None


class TourGuideWishlistStatusResponse(BaseModel):
    tour_guide_id: int
    saved: bool
    wishlist_id: int | None = None


class SavedTourGuideItem(BaseModel):
    wishlist_id: int
    saved_at: datetime
    notes: str | None = None
    tour_guide_id: int
    business_name: str
    display_name: str
    base_city: str
    base_district: str
    daily_rate: Decimal
    hourly_rate: Decimal | None = None
    currency: str
    rating_average: Decimal
    review_count: int
    is_verified: bool
    is_available: bool
    profile_image_url: str | None = None


class TourGuideWishlistListResponse(BaseModel):
    items: list[SavedTourGuideItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_more: bool


class ProviderOnboardingChecklist(BaseModel):
    has_profile: bool
    has_languages: bool
    has_service_areas: bool
    has_profile_image: bool
    has_credentials: bool
    has_active_package: bool
    submitted_for_approval: bool
    is_approved: bool
    is_verified: bool
    completion_percent: int
    next_steps: list[str]


class ProviderDashboardBookingSummary(BaseModel):
    total: int
    pending: int
    confirmed: int
    in_progress: int
    completed: int
    cancelled: int
    rejected: int
    expired: int
    upcoming_bookings: list[TourGuideBookingResponse] = Field(default_factory=list)


class ProviderDashboardPromotionSummary(BaseModel):
    active_campaigns: int
    pending_campaigns: int
    total_impressions: int
    total_clicks: int
    total_attributed_bookings: int


class ProviderTourGuideDashboardResponse(BaseModel):
    provider_profile_id: int
    business_name: str
    provider_approval_status: str
    tour_guide_id: int | None = None
    guide_approval_status: str | None = None
    guide_verification_status: str | None = None
    is_active: bool
    is_available: bool
    is_verified_badge: bool
    rating_average: Decimal
    review_count: int
    wishlist_saves_count: int
    onboarding: ProviderOnboardingChecklist
    bookings: ProviderDashboardBookingSummary
    earnings: TourGuideProviderEarningsResponse
    promotions: ProviderDashboardPromotionSummary


class TourGuidePromotionPackageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    discount_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    valid_from: date
    valid_to: date
    is_active: bool = True

    @field_validator("currency")
    @classmethod
    def check_promo_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        return self


class TourGuidePromotionPackageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=2000)
    discount_percent: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("100"))
    discount_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool | None = None


class TourGuidePromotionPackageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    title: str
    description: str | None = None
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    currency: str
    valid_from: date
    valid_to: date
    is_active: bool
    created_at: datetime


class TourGuideCampaignCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_name: str = Field(min_length=3, max_length=150)
    budget: Decimal = Field(gt=Decimal("0.00"))
    currency: str = Field(default="LKR", min_length=3, max_length=3)
    starts_at: datetime
    ends_at: datetime
    submit_immediately: bool = False

    @field_validator("currency")
    @classmethod
    def check_campaign_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_campaign_window(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be strictly after starts_at")
        return self


class TourGuideCampaignStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["paused", "active", "cancelled"]


class AdminCampaignDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject", "pause", "cancel"]
    reason: str | None = Field(default=None, max_length=500)


class TourGuideCampaignResponse(BaseModel):
    id: int
    provider_profile_id: int
    tour_guide_id: int | None = None
    guide_business_name: str | None = None
    listing_reference_id: int | None = None
    campaign_name: str
    target_module: str
    budget: Decimal
    spent_amount: Decimal
    currency: str
    status: str
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    impressions: int = 0
    clicks: int = 0
    inquiries: int = 0
    bookings: int = 0
    latest_payment_status: str | None = None


class TourGuideCampaignListResponse(BaseModel):
    items: list[TourGuideCampaignResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_more: bool


class TourGuideCampaignPaymentPrepareResponse(BaseModel):
    campaign_id: int
    advertising_payment_id: int
    order_reference: str
    payment_mode: str
    merchant_id: str
    checkout_url: str
    amount: Decimal
    currency: str
    hash: str
    items: str
    return_url: str
    cancel_url: str
    notify_url: str


class TourGuidePromotionEventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: int = Field(gt=0)
    event_type: Literal["impression", "click", "inquiry", "booking"]
    booking_id: int | None = Field(default=None, gt=0)


class TourGuidePromotionEventResponse(BaseModel):
    id: int
    campaign_id: int
    booking_reference_id: int | None = None
    event_type: str
    user_id: int | None = None
    created_at: datetime


class TourGuideCampaignAnalyticsResponse(BaseModel):
    campaign_id: int
    campaign_name: str
    status: str
    starts_at: datetime
    ends_at: datetime
    budget: Decimal
    spent_amount: Decimal
    remaining_budget: Decimal
    currency: str
    impressions: int
    clicks: int
    inquiries: int
    bookings: int
    ctr_percent: float
    conversion_rate_percent: float


class ProviderTourGuideAnalyticsResponse(BaseModel):
    tour_guide_id: int
    date_from: date | None = None
    date_to: date | None = None
    total_bookings: int
    confirmed_or_completed_bookings: int
    completed_bookings: int
    cancelled_bookings: int
    rejected_bookings: int
    acceptance_rate_percent: float
    completion_rate_percent: float
    cancellation_rate_percent: float
    average_booking_value: Decimal
    online_gross_revenue: Decimal
    cash_collected_revenue: Decimal
    currency: str = "LKR"
    rating_average: Decimal
    review_count: int
    rating_distribution: dict[str, int]
    wishlist_saves_count: int
    total_campaigns: int
    active_campaigns: int
    promotion_impressions: int
    promotion_clicks: int
    promotion_inquiries: int
    promotion_bookings: int
    promotion_ctr_percent: float
    promotion_conversion_rate_percent: float


class AdminAdPaymentItem(BaseModel):
    id: int
    campaign_id: int
    campaign_name: str
    provider_profile_id: int
    order_reference: str
    gateway_transaction_id: str | None = None
    amount: Decimal
    currency: str
    payment_status: str
    initiated_at: datetime
    verified_at: datetime | None = None


class AdminAdPaymentListResponse(BaseModel):
    items: list[AdminAdPaymentItem]
    total: int
    total_verified_revenue: Decimal
    currency: str = "LKR"
    page: int
    page_size: int
    total_pages: int
    has_more: bool


class AdminTourGuideReportsOverviewResponse(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    total_guides: int
    active_guides: int
    available_guides: int
    guides_by_approval_status: dict[str, int]
    guides_by_verification_status: dict[str, int]
    guides_by_district: dict[str, int]
    total_bookings: int
    bookings_by_status: dict[str, int]
    bookings_by_payment_method: dict[str, int]
    booking_completion_rate_percent: float
    booking_cancellation_rate_percent: float
    online_gross_revenue: Decimal
    ceylongo_commission_revenue: Decimal
    verified_refunds_total: Decimal
    direct_cash_collected_total: Decimal
    settled_payouts_total: Decimal
    verified_advertising_revenue: Decimal
    currency: str = "LKR"
    total_published_reviews: int
    platform_average_rating: Decimal
    total_wishlist_saves: int
    active_sponsored_campaigns: int


# =============================================================================
# 15. MIGRATION 034 SCHEMA-GUARDED EXTENSIONS (TR-18, PR-17, GS-01, GS-03, GS-06)
# =============================================================================


class ProviderWhatsAppSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    whatsapp_number: str | None = Field(default=None, max_length=30)
    allow_whatsapp_contact: bool = False

    @model_validator(mode="after")
    def validate_opt_in_requires_number(self):
        cleaned = self.whatsapp_number.strip() if self.whatsapp_number else None
        self.whatsapp_number = cleaned if cleaned else None
        if self.allow_whatsapp_contact and not self.whatsapp_number:
            raise ValueError(
                "whatsapp_number is required when allow_whatsapp_contact is enabled"
            )
        return self


class ProviderWhatsAppSettingsResponse(BaseModel):
    tour_guide_id: int
    primary_phone: str
    secondary_phone: str | None = None
    whatsapp_number: str | None = None
    allow_whatsapp_contact: bool = False
    migration_034_active: bool
    status_note: str


class AdminVerifiedExperienceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified_years_of_experience: int | None = Field(default=None, ge=0, le=80)
    notes: str | None = Field(default=None, max_length=500)


class AdminVerifiedExperienceResponse(BaseModel):
    tour_guide_id: int
    self_reported_years_of_experience: int
    verified_years_of_experience: int | None = None
    experience_is_verified: bool
    migration_034_active: bool
    message: str


class PackageExtendedRequirementsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_type: Literal["half_day", "full_day", "custom"] | None = None
    meeting_instructions: str | None = Field(default=None, max_length=1000)
    special_requirements: PackageSpecialRequirementsPayload | None = None

    @field_validator("meeting_instructions")
    @classmethod
    def clean_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class PackageExtendedRequirementsResponse(BaseModel):
    package_id: int
    tour_guide_id: int
    meeting_point: str
    package_type: str
    meeting_instructions: str | None = None
    special_requirements: PackageSpecialRequirementsPayload | None = None
    migration_034_active: bool
    status_note: str
