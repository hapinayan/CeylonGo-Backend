"""
CeylonGo Academic MVP — Pydantic Request & Response Schemas
Type-safe API contracts for Traveler, Provider, Admin, Listings, Bookings, Payments, Community, and AI Itinerary.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.auth import RegisterRequest


# ============================================================================
# 1. Authentication & Users
# ============================================================================
class CheckEmailRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class CheckEmailResponse(BaseModel):
    email: str
    is_registered: bool


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., min_length=10)


class GenericMessageResponse(BaseModel):
    message: str


class VerifyResetTokenResponse(BaseModel):
    valid: bool
    email: str
    message: str


class UserRegisterRequest(BaseModel):
    email: str = Field(..., max_length=254)
    full_name: str = Field(..., min_length=2, max_length=120)
    phone: str | None = Field(None, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    role: Literal["traveler", "provider", "admin"] = "traveler"


class TravelerRegisterRequest(RegisterRequest):
    full_name: str = Field(min_length=2, max_length=120)
    phone: str | None = Field(None, max_length=32)
    password: str = Field(min_length=6, max_length=128)
    role: Literal["traveler"] = "traveler"


class NonTravelerRegisterRequest(UserRegisterRequest):
    role: Literal["provider", "admin"]


class UserLoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    phone: str | None = None
    role: str
    is_active: bool
    auth_version: int
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(..., min_length=6, max_length=128)


# ============================================================================
# 2. Providers & Documents
# ============================================================================
class ProviderRegisterRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=160)
    phone: str = Field(..., min_length=6, max_length=32)
    district: str = Field(..., min_length=2, max_length=80)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    business_name: str
    phone: str
    district: str
    verification_status: str
    submitted_at: datetime | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None
    created_at: datetime


class ProviderDocumentUpload(BaseModel):
    kind: Literal["identity", "business", "revenue_license", "insurance"]
    storage_key: str = Field(..., max_length=450)
    sha256_hex: str = Field(..., min_length=64, max_length=64)
    unit_id: int | None = None
    expires_on: date | None = None


class ProviderDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    unit_id: int | None = None
    kind: str
    storage_key: str
    status: str
    expires_on: date | None = None
    created_at: datetime


class EmailChallengeVerify(BaseModel):
    email: EmailStr = Field(max_length=254)
    code: str = Field(pattern=r"^[0-9]{6}$")


# ============================================================================
# 3. Listings, Media & Inventory
# ============================================================================
class ListingMediaCreate(BaseModel):
    storage_key: str = Field(..., max_length=450)
    sort_order: int = Field(0, ge=0)
    alt_text: str | None = Field(None, max_length=160)


class ListingMediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    storage_key: str
    sort_order: int
    alt_text: str | None = None
    image_url: str | None = None


class InventoryUnitCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=100)
    capacity: int = Field(1, gt=0)


class InventoryUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    label: str
    capacity: int
    is_active: bool


class AvailabilitySlotCreate(BaseModel):
    unit_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int = Field(..., gt=0)
    is_open: bool = True


class AvailabilitySlotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    unit_id: int
    listing_id: int
    starts_at: datetime
    ends_at: datetime
    capacity: int
    is_open: bool


ServiceCategory = Literal["homestay", "bike", "bicycle", "adventure", "guide", "artisan"]


class ListingCreateRequest(BaseModel):
    category: ServiceCategory
    title: str = Field("", max_length=160)
    description: str = Field("", max_length=2000)
    district: str = Field("", max_length=80)
    locality: str = Field("", max_length=100)
    latitude: Decimal | None = Field(None, ge=-90, le=90, decimal_places=6)
    longitude: Decimal | None = Field(None, ge=-180, le=180, decimal_places=6)
    price: Decimal = Field(0, ge=0, max_digits=12, decimal_places=2)
    currency: Literal["LKR"] = "LKR"


class ListingUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    price: Decimal | None = None
    is_active: bool | None = None


class ListingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_id: int
    category: str
    title: str
    description: str
    district: str
    locality: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    price: Decimal
    currency: str
    approval_status: str
    is_active: bool
    media: list[ListingMediaResponse] = []
    units: list[InventoryUnitResponse] = []
    created_at: datetime


# ============================================================================
# 4. Bookings & Allocations
# ============================================================================
class BookingCreateRequest(BaseModel):
    listing_id: int
    starts_at: datetime
    ends_at: datetime
    quantity: int = Field(1, gt=0)
    request_key: str = Field(..., min_length=10, max_length=36)


class BookingStatusUpdateRequest(BaseModel):
    to_status: Literal["confirmed", "rejected", "cancelled", "completed"]
    reason: str | None = Field(None, max_length=500)


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    traveler_id: int
    request_key: str
    starts_at: datetime
    ends_at: datetime
    quantity: int
    total_amount: Decimal
    currency: str
    status: str
    created_at: datetime


# ============================================================================
# 5. Payments
# ============================================================================
class SimulatedPaymentRequest(BaseModel):
    booking_id: int
    request_key: str = Field(..., min_length=10, max_length=36)
    attempt_no: int = Field(1, ge=1)
    simulate_outcome: Literal["success", "failure"] = "success"


class AtLocationPaymentRequest(BaseModel):
    booking_id: int
    request_key: str = Field(..., min_length=10, max_length=36)


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_id: int
    request_key: str
    attempt_no: int
    method: str
    status: str
    display_amount: Decimal
    currency: str
    created_at: datetime
    resolved_at: datetime | None = None


# ============================================================================
# 6. Community Feed
# ============================================================================
class PostCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


class CommentCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    post_id: int
    author_id: int
    body: str
    visibility: str
    created_at: datetime


class PostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author_id: int
    body: str
    visibility: str
    created_at: datetime
    likes_count: int = 0
    comments_count: int = 0


# ============================================================================
# 7. Admin Moderation & Audit
# ============================================================================
class AdminProviderDecision(BaseModel):
    verification_status: Literal["approved", "rejected", "suspended"]
    decision_reason: str | None = Field(None, max_length=500)


class AdminListingDecision(BaseModel):
    approval_status: Literal["approved", "rejected", "more_evidence_required", "suspended"]
    decision_reason: str | None = Field(None, max_length=460)


class AdminModerationRequest(BaseModel):
    visibility: Literal["hidden", "deleted"]
    reason: str = Field(..., max_length=500)


class AdminAuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: int
    action: str
    subject_user_id: int | None = None
    subject_provider_id: int | None = None
    subject_listing_id: int | None = None
    subject_post_id: int | None = None
    reason: str
    occurred_at: datetime


# ============================================================================
# 8. AI Itinerary Planner
# ============================================================================
class ItineraryRequest(BaseModel):
    destination: str = Field(..., min_length=2, max_length=100)
    duration_days: int = Field(3, ge=1, le=14)
    budget_level: Literal["budget", "moderate", "luxury"] = "moderate"
    interests: list[str] = ["nature", "culture", "food"]


class ItineraryDayPlan(BaseModel):
    day: int
    title: str
    morning: str
    afternoon: str
    evening: str
    recommended_categories: list[str] = []
    estimated_day_cost_lkr: Decimal


class ItineraryResponse(BaseModel):
    destination: str
    duration_days: int
    budget_level: str
    currency: str = "LKR"
    total_estimated_cost_lkr: Decimal
    days: list[ItineraryDayPlan]
    tips: list[str]
    generated_at: datetime
