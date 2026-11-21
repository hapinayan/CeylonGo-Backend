"""
Pydantic v2 schemas for Module 04 — Eco-Tourism Core Entities.

Enforces:
- Six approved Eco-Tourism categories
- Pricing constraints ('per_person' | 'per_group', non-negative price, positive duration)
- Coordinate bounds (latitude -90..90, longitude -180..180)
- Environmental group size limits (1 <= min_participants <= max_group_size <= 50)
- Separation of factual conservation contributions vs marketing statements
- Separation of public response schemas (no private verification evidence or unverified badge leakage)
  vs provider/admin management schemas
- Prohibition of client-supplied privilege escalation fields (extra='forbid' on write schemas)
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.eco_experience import (
    VALID_ECO_CATEGORIES,
    VALID_EVIDENCE_DOCUMENT_TYPES,
    VALID_PRACTICE_CATEGORIES,
    VALID_PRICING_TYPES,
    VALID_SAFETY_RULE_TYPES,
    VALID_SEASON_RESTRICTION_TYPES,
    VALID_VERIFICATION_DECISIONS,
)

EcoCategoryLiteral = Literal[
    "Forest & Nature Trails",
    "Wildlife & Birdwatching",
    "Sustainable Village Experiences",
    "Conservation Activities",
    "Eco-Friendly Stays",
    "Community Eco-Tourism",
]

PricingTypeLiteral = Literal["per_person", "per_group"]

SafetyRuleTypeLiteral = Literal[
    "wildlife_no_feeding",
    "environmental_conduct",
    "operator_instruction",
    "equipment_requirement",
    "health_age_restriction",
]

SeasonRestrictionTypeLiteral = Literal[
    "monsoon_season",
    "wildlife_breeding",
    "weather_alert",
    "park_closure",
]

PracticeCategoryLiteral = Literal[
    "waste_reduction",
    "renewable_energy",
    "community_employment",
    "wildlife_protection",
    "habitat_restoration",
    "sustainable_sourcing",
]

EvidenceDocumentTypeLiteral = Literal[
    "certificate",
    "permit",
    "photo_proof",
    "audit_report",
]


# ============================================================
# Child Entity Input & Response Schemas
# ============================================================


class EcoExperienceImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_url: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Approved image URL or storage reference",
    )
    caption: str | None = Field(default=None, max_length=255)
    is_primary: bool = False
    sort_order: int = Field(default=0, ge=0)

    @field_validator("image_url")
    @classmethod
    def clean_image_url(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("image_url cannot be empty or whitespace")
        return cleaned

    @field_validator("caption")
    @classmethod
    def clean_caption(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class EcoExperienceImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    image_url: str
    caption: str | None = None
    is_primary: bool
    sort_order: int
    created_at: datetime


class EcoSafetyRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_type: str = Field(
        ...,
        description=(
            "Rule classification: 'wildlife_no_feeding', 'environmental_conduct', "
            "'operator_instruction', 'equipment_requirement', 'health_age_restriction'"
        ),
    )
    title: str = Field(..., min_length=2, max_length=150)
    description: str = Field(..., min_length=5, max_length=1000)
    is_mandatory: bool = True

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_SAFETY_RULE_TYPES:
            raise ValueError(
                f"Invalid safety rule_type '{v}'. Allowed: {', '.join(VALID_SAFETY_RULE_TYPES)}"
            )
        return cleaned

    @field_validator("title", "description")
    @classmethod
    def clean_required_text(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Field cannot be empty or blank")
        return cleaned


class EcoSafetyRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    rule_type: str
    title: str
    description: str
    is_mandatory: bool
    created_at: datetime


class EcoSeasonRestrictionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    restriction_type: str = Field(
        ...,
        description="One of: 'monsoon_season', 'wildlife_breeding', 'weather_alert', 'park_closure'",
    )
    reason: str = Field(..., min_length=5, max_length=500)
    is_active: bool = True

    @field_validator("restriction_type")
    @classmethod
    def validate_restriction_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_SEASON_RESTRICTION_TYPES:
            raise ValueError(
                f"Invalid restriction_type '{v}'. Allowed: {', '.join(VALID_SEASON_RESTRICTION_TYPES)}"
            )
        return cleaned

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Restriction reason must be at least 5 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_date_range(self) -> "EcoSeasonRestrictionInput":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date")
        return self


class EcoSeasonRestrictionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    start_date: date
    end_date: date
    restriction_type: str
    reason: str
    is_active: bool
    created_by: int | None = None
    created_at: datetime


# ============================================================
# Sustainability Practices, Private Evidence & Verification
# ============================================================


class EcoPracticeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    practice_category: str = Field(
        ...,
        description=(
            "One of: 'waste_reduction', 'renewable_energy', 'community_employment', "
            "'wildlife_protection', 'habitat_restoration', 'sustainable_sourcing'"
        ),
    )
    claim_description: str = Field(..., min_length=10, max_length=1000)

    @field_validator("practice_category")
    @classmethod
    def validate_practice_category(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_PRACTICE_CATEGORIES:
            raise ValueError(
                f"Invalid practice_category '{v}'. Allowed: {', '.join(VALID_PRACTICE_CATEGORIES)}"
            )
        return cleaned

    @field_validator("claim_description")
    @classmethod
    def clean_claim(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 10:
            raise ValueError("claim_description must be at least 10 characters")
        return cleaned


class EcoPracticePublicResponse(BaseModel):
    """
    Public representation of an eco practice.
    SECURITY RULE: Never includes private EcoEvidence records or raw file paths.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    practice_category: str
    claim_description: str
    is_verified: bool


class EcoEvidenceMetadataResponse(BaseModel):
    """
    Provider/Admin metadata representation of an uploaded evidence document.
    SECURITY RULE: Exposes document id, type, and upload timestamp for admin review,
    but NEVER exposes raw server filesystem paths (file_reference).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_practice_id: int
    document_type: str
    notes: str | None = None
    uploaded_at: datetime


class EcoPracticeManagementResponse(BaseModel):
    """
    Provider/Admin view of an eco practice with safe evidence metadata indicators.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    practice_category: str
    claim_description: str
    is_verified: bool
    created_at: datetime
    evidences: list[EcoEvidenceMetadataResponse] = Field(default_factory=list)


class EcoVerificationDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["granted", "rejected", "revoked"]
    reason: str = Field(..., min_length=5, max_length=1000)
    valid_until: datetime | None = None

    @field_validator("reason")
    @classmethod
    def clean_decision_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Verification decision reason must be at least 5 characters")
        return cleaned


class EcoVerificationHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    submitted_by_provider_id: int
    reviewed_by_admin_id: int | None = None
    decision: str
    reason: str | None = None
    valid_until: datetime | None = None
    created_at: datetime


# ============================================================
# Conservation Information Structured Schema
# ============================================================


class EcoConservationInformation(BaseModel):
    """
    Structured representation of conservation benefits that explicitly separates
    factual contributions from general marketing claims.
    """

    factual_contribution: str | None = Field(
        default=None,
        description="Verified factual conservation benefit (e.g., permit fee, % to community fund)",
    )
    marketing_statement: str | None = Field(
        default=None,
        description="General operator conservation narrative (clearly separated from factual claims)",
    )
    protected_area_permit_required: bool = False
    certified_guide_required: bool = False
    permit_details: str | None = None


# ============================================================
# Eco Experience Create, Update & Response Schemas
# ============================================================


class EcoExperienceCreate(BaseModel):
    """
    Payload for an approved eco-tourism provider to create a new draft eco listing.
    Forbids client-supplied status/badge/owner fields (`extra='forbid'`).
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=150)
    eco_category: str = Field(
        ...,
        description="Must be one of the 6 approved CeylonGo Eco-Tourism categories",
    )
    description: str | None = Field(default=None, max_length=5000)
    itinerary: str | None = Field(default=None, max_length=5000)
    duration_hours: Decimal = Field(
        ...,
        gt=Decimal("0.0"),
        le=Decimal("720.0"),
        max_digits=5,
        decimal_places=2,
    )

    # Location & Maps
    district: str = Field(..., min_length=2, max_length=100)
    city: str = Field(..., min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    meeting_point: str = Field(..., min_length=3, max_length=500)
    directions_info: str | None = Field(default=None, max_length=1000)
    latitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-90.0"),
        le=Decimal("90.0"),
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-180.0"),
        le=Decimal("180.0"),
    )

    # Pricing & Environmental Group Size Limits
    pricing_type: str = Field(default="per_person")
    base_price: Decimal = Field(
        ...,
        ge=Decimal("0.0"),
        max_digits=12,
        decimal_places=2,
    )
    currency: str = Field(default="LKR", min_length=3, max_length=10)
    max_group_size: int = Field(
        ...,
        ge=1,
        le=50,
        description="Environmentally appropriate maximum participants per slot (1 to 50)",
    )
    min_participants: int = Field(default=1, ge=1, le=50)
    min_age: int | None = Field(default=None, ge=0, le=100)

    # Eco-Specific Guidelines, Safety & Conservation
    environmental_guidelines: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Site-specific responsible travel instructions and visitor conduct rules",
    )
    wildlife_safety_summary: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Safety precautions, no-feeding rules, and operator instructions",
    )
    conservation_factual_contribution: str | None = Field(
        default=None,
        max_length=1000,
        description="Factual conservation contribution (distinguished from marketing claims)",
    )
    conservation_marketing_statement: str | None = Field(
        default=None,
        max_length=1000,
        description="General marketing statement regarding sustainability",
    )
    protected_area_permit_required: bool = False
    certified_guide_required: bool = False
    permit_details: str | None = Field(default=None, max_length=500)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int = Field(default=24, ge=0, le=720)

    # Optional link to Homestay module for 'Eco-Friendly Stays'
    linked_homestay_id: int | None = Field(default=None, gt=0)

    # Optional initial child collections
    images: list[EcoExperienceImageInput] = Field(default_factory=list, max_length=20)
    safety_rules: list[EcoSafetyRuleInput] = Field(default_factory=list, max_length=30)
    season_restrictions: list[EcoSeasonRestrictionInput] = Field(
        default_factory=list, max_length=20
    )
    practices: list[EcoPracticeInput] = Field(default_factory=list, max_length=20)

    @field_validator("eco_category")
    @classmethod
    def validate_eco_category(cls, v: str) -> str:
        cleaned = v.strip()
        if cleaned not in VALID_ECO_CATEGORIES:
            raise ValueError(
                f"Invalid eco_category '{v}'. Must be one of: {', '.join(VALID_ECO_CATEGORIES)}"
            )
        return cleaned

    @field_validator("pricing_type")
    @classmethod
    def validate_pricing_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_PRICING_TYPES:
            raise ValueError(
                f"Invalid pricing_type '{v}'. Allowed: {', '.join(VALID_PRICING_TYPES)}"
            )
        return cleaned

    @field_validator(
        "title",
        "district",
        "city",
        "meeting_point",
        "currency",
        "environmental_guidelines",
        "wildlife_safety_summary",
    )
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Field cannot be empty or blank")
        return cleaned

    @field_validator(
        "description",
        "itinerary",
        "address",
        "directions_info",
        "conservation_factual_contribution",
        "conservation_marketing_statement",
        "permit_details",
        "cancellation_policy",
    )
    @classmethod
    def clean_optional_strings(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None

    @model_validator(mode="after")
    def validate_eco_business_rules(self) -> "EcoExperienceCreate":
        if self.min_participants > self.max_group_size:
            raise ValueError(
                "min_participants cannot exceed max_group_size"
            )

        if (
            self. linked_homestay_id is not None
            and self.eco_category != "Eco-Friendly Stays"
        ):
            raise ValueError(
                "linked_homestay_id can only be set when eco_category is 'Eco-Friendly Stays'"
            )

        if sum(img.is_primary for img in self.images) > 1:
            raise ValueError("Only one image can be marked as is_primary")

        if (
            self.conservation_factual_contribution
            and self.conservation_marketing_statement
            and self.conservation_factual_contribution.lower()
            == self.conservation_marketing_statement.lower()
        ):
            raise ValueError(
                "conservation_factual_contribution must be distinct from conservation_marketing_statement"
            )

        return self


class EcoExperienceUpdate(BaseModel):
    """
    Partial/full update schema for an existing EcoExperience owned by the provider.
    Forbids client-supplied privilege escalation fields (`extra='forbid'`).
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=3, max_length=150)
    eco_category: str | None = None
    description: str | None = Field(default=None, max_length=5000)
    itinerary: str | None = Field(default=None, max_length=5000)
    duration_hours: Decimal | None = Field(
        default=None,
        gt=Decimal("0.0"),
        le=Decimal("720.0"),
        max_digits=5,
        decimal_places=2,
    )

    district: str | None = Field(default=None, min_length=2, max_length=100)
    city: str | None = Field(default=None, min_length=2, max_length=100)
    address: str | None = Field(default=None, max_length=255)
    meeting_point: str | None = Field(default=None, min_length=3, max_length=500)
    directions_info: str | None = Field(default=None, max_length=1000)
    latitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-90.0"),
        le=Decimal("90.0"),
    )
    longitude: Decimal | None = Field(
        default=None,
        ge=Decimal("-180.0"),
        le=Decimal("180.0"),
    )

    pricing_type: str | None = None
    base_price: Decimal | None = Field(
        default=None,
        ge=Decimal("0.0"),
        max_digits=12,
        decimal_places=2,
    )
    currency: str | None = Field(default=None, min_length=3, max_length=10)
    max_group_size: int | None = Field(default=None, ge=1, le=50)
    min_participants: int | None = Field(default=None, ge=1, le=50)
    min_age: int | None = Field(default=None, ge=0, le=100)

    environmental_guidelines: str | None = Field(
        default=None, min_length=10, max_length=2000
    )
    wildlife_safety_summary: str | None = Field(
        default=None, min_length=10, max_length=2000
    )
    conservation_factual_contribution: str | None = Field(
        default=None, max_length=1000
    )
    conservation_marketing_statement: str | None = Field(
        default=None, max_length=1000
    )
    protected_area_permit_required: bool | None = None
    certified_guide_required: bool | None = None
    permit_details: str | None = Field(default=None, max_length=500)
    cancellation_policy: str | None = Field(default=None, max_length=1000)
    free_cancellation_hours: int | None = Field(default=None, ge=0, le=720)
    linked_homestay_id: int | None = Field(default=None, gt=0)

    @field_validator("eco_category")
    @classmethod
    def validate_eco_category(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            if cleaned not in VALID_ECO_CATEGORIES:
                raise ValueError(
                    f"Invalid eco_category '{v}'. Must be one of: {', '.join(VALID_ECO_CATEGORIES)}"
                )
            return cleaned
        return None

    @field_validator("pricing_type")
    @classmethod
    def validate_pricing_type(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip().lower()
            if cleaned not in VALID_PRICING_TYPES:
                raise ValueError(
                    f"Invalid pricing_type '{v}'. Allowed: {', '.join(VALID_PRICING_TYPES)}"
                )
            return cleaned
        return None

    @field_validator(
        "title",
        "district",
        "city",
        "meeting_point",
        "currency",
        "environmental_guidelines",
        "wildlife_safety_summary",
    )
    @classmethod
    def clean_non_empty(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            if len(cleaned) < 2:
                raise ValueError("Field cannot be empty or blank")
            return cleaned
        return None

    @field_validator(
        "description",
        "itinerary",
        "address",
        "directions_info",
        "conservation_factual_contribution",
        "conservation_marketing_statement",
        "permit_details",
        "cancellation_policy",
    )
    @classmethod
    def clean_optional_strings(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None

    @model_validator(mode="after")
    def validate_update_rules(self) -> "EcoExperienceUpdate":
        if (
            self.min_participants is not None
            and self.max_group_size is not None
            and self.min_participants > self.max_group_size
        ):
            raise ValueError("min_participants cannot exceed max_group_size")

        if (
            self.conservation_factual_contribution
            and self.conservation_marketing_statement
            and self.conservation_factual_contribution.lower()
            == self.conservation_marketing_statement.lower()
        ):
            raise ValueError(
                "conservation_factual_contribution must be distinct from conservation_marketing_statement"
            )
        return self


class EcoExperiencePublicSummary(BaseModel):
    """
    Compact summary schema for public traveler discovery cards, search results, and maps.
    Exposes `has_verified_eco_badge` (strictly admin-verified) and never exposes private evidence.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    linked_homestay_id: int | None = None
    title: str
    eco_category: str
    district: str
    city: str
    duration_hours: Decimal
    pricing_type: str
    base_price: Decimal
    currency: str
    max_group_size: int
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    has_verified_eco_badge: bool
    is_sponsored: bool = False
    sponsored_tracking_token: str | None = None
    provider_business_name: str | None = None


class EcoExperiencePublicDetailResponse(BaseModel):
    """
    Full public detail schema for travelers viewing an approved & published EcoExperience.
    Includes environmental guidelines, wildlife safety rules, structured conservation info,
    and public practices, while strictly excluding private verification evidence.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    linked_homestay_id: int | None = None
    title: str
    eco_category: str
    description: str | None = None
    itinerary: str | None = None
    duration_hours: Decimal

    district: str
    city: str
    address: str | None = None
    meeting_point: str
    directions_info: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None

    pricing_type: str
    base_price: Decimal
    currency: str
    max_group_size: int
    min_participants: int
    min_age: int | None = None

    environmental_guidelines: str
    wildlife_safety_summary: str
    conservation_factual_contribution: str | None = None
    conservation_marketing_statement: str | None = None
    protected_area_permit_required: bool
    certified_guide_required: bool
    permit_details: str | None = None
    conservation_information: EcoConservationInformation
    cancellation_policy: str | None = None
    free_cancellation_hours: int

    has_verified_eco_badge: bool
    is_sponsored: bool = False
    sponsored_tracking_token: str | None = None
    provider_business_name: str | None = None
    provider_phone: str | None = None

    images: list[EcoExperienceImageResponse] = Field(default_factory=list)
    safety_rules: list[EcoSafetyRuleResponse] = Field(default_factory=list)
    season_restrictions: list[EcoSeasonRestrictionResponse] = Field(
        default_factory=list
    )
    practices: list[EcoPracticePublicResponse] = Field(default_factory=list)


class EcoExperiencePublishRequest(BaseModel):
    """
    Request payload for publishing or deactivating an admin-approved eco experience.
    """

    model_config = ConfigDict(extra="forbid")

    is_published: bool


class AdminRejectEcoExperienceRequest(BaseModel):
    """
    Request payload for an admin to reject a submitted eco experience with a stated reason.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Rejection reason must be at least 5 characters")
        return cleaned


class EcoExperienceManagementResponse(BaseModel):
    """
    Detailed response schema for the owning Service Provider or Admin.
    Includes draft/moderation status, badge status, practice evidence metadata (without raw file paths),
    and verification decision history.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
    linked_homestay_id: int | None = None
    title: str
    eco_category: str
    description: str | None = None
    itinerary: str | None = None
    duration_hours: Decimal

    district: str
    city: str
    address: str | None = None
    meeting_point: str
    directions_info: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None

    pricing_type: str
    base_price: Decimal
    currency: str
    max_group_size: int
    min_participants: int
    min_age: int | None = None

    environmental_guidelines: str
    wildlife_safety_summary: str
    conservation_factual_contribution: str | None = None
    conservation_marketing_statement: str | None = None
    protected_area_permit_required: bool
    certified_guide_required: bool
    permit_details: str | None = None
    cancellation_policy: str | None = None
    free_cancellation_hours: int

    listing_status: str
    is_published: bool
    is_publicly_visible: bool
    approval_reviewed_by: int | None = None
    approval_reviewed_at: datetime | None = None
    approval_rejection_reason: str | None = None

    eco_badge_status: str
    has_verified_eco_badge: bool
    created_at: datetime
    updated_at: datetime

    images: list[EcoExperienceImageResponse] = Field(default_factory=list)
    safety_rules: list[EcoSafetyRuleResponse] = Field(default_factory=list)
    season_restrictions: list[EcoSeasonRestrictionResponse] = Field(
        default_factory=list
    )
    practices: list[EcoPracticeManagementResponse] = Field(default_factory=list)
    verifications: list[EcoVerificationHistoryResponse] = Field(
        default_factory=list
    )


class EcoExperienceAdminPagination(BaseModel):
    items: list[EcoExperienceManagementResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


class EcoExperiencePagination(BaseModel):
    items: list[EcoExperiencePublicSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_pages: int = Field(ge=0)


# ============================================================
# Dashboard & Overview Aggregation Schemas (P0.7)
# ============================================================


class EcoProviderListingSummary(BaseModel):
    """
    Summary metrics for an authenticated eco-tourism provider's dashboard.
    """

    model_config = ConfigDict(from_attributes=True)

    total: int = Field(default=0, ge=0)
    draft: int = Field(default=0, ge=0)
    pending_approval: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    deactivated: int = Field(default=0, ge=0)
    published: int = Field(default=0, ge=0)
    verified_badges: int = Field(default=0, ge=0)
    pending_badges: int = Field(default=0, ge=0)

    # P01: Booking & Revenue metrics
    total_bookings: int = Field(default=0, ge=0)
    pending_bookings: int = Field(default=0, ge=0)
    confirmed_bookings: int = Field(default=0, ge=0)
    completed_bookings: int = Field(default=0, ge=0)
    cancelled_bookings: int = Field(default=0, ge=0)
    rejected_bookings: int = Field(default=0, ge=0)
    total_revenue: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    pending_cash_revenue: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    refunded_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))


class EcoAdminStatusBreakdown(BaseModel):
    """
    Status breakdown for admin eco-tourism overview.
    """

    model_config = ConfigDict(from_attributes=True)

    draft: int = Field(default=0, ge=0)
    pending_approval: int = Field(default=0, ge=0)
    approved: int = Field(default=0, ge=0)
    rejected: int = Field(default=0, ge=0)
    deactivated: int = Field(default=0, ge=0)


class EcoAdminCategoryBreakdown(BaseModel):
    """
    Category listing count breakdown.
    """

    model_config = ConfigDict(from_attributes=True)

    category: str
    count: int = Field(default=0, ge=0)


class EcoAdminDistrictBreakdown(BaseModel):
    """
    District listing count breakdown.
    """

    model_config = ConfigDict(from_attributes=True)

    district: str
    count: int = Field(default=0, ge=0)


class EcoAdminBookingMetrics(BaseModel):
    """
    Booking volume and status breakdown for admin overview (A08).
    """

    model_config = ConfigDict(from_attributes=True)

    total_bookings: int = Field(default=0, ge=0)
    pending_bookings: int = Field(default=0, ge=0)
    confirmed_bookings: int = Field(default=0, ge=0)
    completed_bookings: int = Field(default=0, ge=0)
    cancelled_bookings: int = Field(default=0, ge=0)
    rejected_bookings: int = Field(default=0, ge=0)


class EcoAdminFinancialMetrics(BaseModel):
    """
    Platform-wide financial metrics for admin overview (A08).
    """

    model_config = ConfigDict(from_attributes=True)

    gross_booking_value: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    paid_revenue: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    pending_cash_volume: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    refunded_amount: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    estimated_platform_commission: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    recorded_platform_fees: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    total_disbursed_settlements: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    approved_pending_settlements: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    net_provider_payable: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))


class EcoAdminOverviewResponse(BaseModel):
    """
    Comprehensive platform-wide metrics for admin eco-tourism dashboard.
    """

    model_config = ConfigDict(from_attributes=True)

    total_listings: int = Field(default=0, ge=0)
    published_listings: int = Field(default=0, ge=0)
    verified_badges: int = Field(default=0, ge=0)
    pending_badges: int = Field(default=0, ge=0)
    total_active_providers: int = Field(default=0, ge=0)
    by_status: EcoAdminStatusBreakdown
    by_category: list[EcoAdminCategoryBreakdown]
    by_district: list[EcoAdminDistrictBreakdown]
    booking_metrics: EcoAdminBookingMetrics = Field(default_factory=EcoAdminBookingMetrics)
    financial_metrics: EcoAdminFinancialMetrics = Field(default_factory=EcoAdminFinancialMetrics)


# ============================================================
# Phase 5A: Admin Monitoring & Provider Earnings Schemas
# ============================================================


class EcoAdminBookingItem(BaseModel):
    """
    Detailed booking monitoring item for admin oversight (A05).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str | None = None
    eco_experience_id: int
    eco_experience_title: str
    provider_profile_id: int
    provider_name: str
    user_id: int
    traveler_name: str
    traveler_email: str
    participants: int
    total_price: Decimal
    currency: str = "LKR"
    payment_method: str
    payment_status: str
    booking_status: str
    cancellation_reason: str | None = None
    cancelled_at: datetime | None = None
    created_at: datetime


class EcoAdminBookingPagination(BaseModel):
    """
    Paginated admin booking monitoring response (A05).
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[EcoAdminBookingItem]
    total: int
    page: int
    page_size: int
    total_pages: int


class EcoAdminPaymentItem(BaseModel):
    """
    Payment and commission audit record for admin monitoring (A06).
    """

    model_config = ConfigDict(from_attributes=True)

    booking_id: int
    booking_reference: str | None = None
    eco_experience_id: int
    eco_experience_title: str
    provider_profile_id: int
    provider_name: str
    user_id: int
    traveler_name: str
    amount: Decimal
    currency: str = "LKR"
    payment_method: str
    payment_status: str
    booking_status: str
    estimated_platform_commission: Decimal = Decimal("0.00")
    platform_commission: Decimal = Decimal("0.00")
    provider_share: Decimal = Decimal("0.00")
    gateway_name: str | None = None
    gateway_transaction_id: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class EcoAdminPaymentAuditSummary(BaseModel):
    """
    Aggregated payment audit metrics across all eco-tourism transactions (A06).
    """

    model_config = ConfigDict(from_attributes=True)

    total_bookings: int = 0
    paid_bookings_count: int = 0
    total_gross_volume: Decimal = Decimal("0.00")
    total_paid_volume: Decimal = Decimal("0.00")
    total_pending_cash_volume: Decimal = Decimal("0.00")
    total_refunded_volume: Decimal = Decimal("0.00")
    estimated_platform_commission: Decimal = Decimal("0.00")
    total_platform_commission: Decimal = Decimal("0.00")
    recorded_platform_fees: Decimal = Decimal("0.00")
    total_disbursed_settlements: Decimal = Decimal("0.00")
    total_approved_settlements: Decimal = Decimal("0.00")
    total_provider_payable: Decimal = Decimal("0.00")


class EcoAdminPaymentAuditResponse(BaseModel):
    """
    Paginated payment audit response with high-level summary (A06).
    """

    model_config = ConfigDict(from_attributes=True)

    summary: EcoAdminPaymentAuditSummary
    items: list[EcoAdminPaymentItem]
    total: int
    page: int
    page_size: int
    total_pages: int
    native_financials: list["EcoNativeFinanceTotals"] = Field(default_factory=list)


class EcoNativeFinanceAmounts(BaseModel):
    captured_online: Decimal = Decimal("0.00")
    unapplied_captures: Decimal = Decimal("0.00")
    pending_refunds: Decimal = Decimal("0.00")
    verified_refunds: Decimal = Decimal("0.00")
    verified_applied_refunds: Decimal = Decimal("0.00")
    cash_receipts: Decimal = Decimal("0.00")
    recorded_online_commission: Decimal = Decimal("0.00")


class EcoNativeFinanceTotals(EcoNativeFinanceAmounts):
    currency: str
    bookings: int
    paid_bookings: int


class EcoNativeRefundReportItem(BaseModel):
    id: int
    amount: Decimal
    state: str


class EcoNativeAdminBookingItem(BaseModel):
    id: int
    booking_reference: str
    experience_id: int
    provider_id: int
    traveler_id: int
    status: str
    payment_status: str
    currency: str
    payment_method: str
    created_at: datetime
    cancelled_at: datetime | None
    total_price: Decimal
    finance: EcoNativeFinanceAmounts
    refunds: list[EcoNativeRefundReportItem]


class EcoNativeAdminReport(BaseModel):
    items: list[EcoNativeAdminBookingItem]
    total: int
    page: int
    page_size: int
    totals_by_currency: list[EcoNativeFinanceTotals]
    provider_settlements_by_currency: list["EcoSettlementTotals"]


class EcoSettlementTotals(BaseModel):
    currency: str
    disbursed: Decimal
    pending: Decimal
    on_hold: Decimal
    recorded_platform_fees: Decimal


class EcoProviderEarningsSummaryResponse(BaseModel):
    """
    Comprehensive financial summary of online earnings and settlements for an eco provider (P14).
    """

    model_config = ConfigDict(from_attributes=True)

    provider_profile_id: int
    total_bookings: int = 0
    paid_bookings_count: int = 0
    gross_earnings: Decimal = Decimal("0.00")
    estimated_platform_commission: Decimal = Decimal("0.00")
    platform_commission: Decimal = Decimal("0.00")
    net_earnings: Decimal = Decimal("0.00")
    recorded_platform_fees: Decimal = Decimal("0.00")
    total_disbursed_payouts: Decimal = Decimal("0.00")
    approved_pending_payouts: Decimal = Decimal("0.00")
    on_hold_payouts: Decimal = Decimal("0.00")
    total_settled: Decimal = Decimal("0.00")
    pending_settlement: Decimal = Decimal("0.00")
    pending_cash_to_collect: Decimal = Decimal("0.00")
    refunded_amount: Decimal = Decimal("0.00")
    native_financials: list[EcoNativeFinanceTotals] = Field(default_factory=list)
    provider_settlements_by_currency: list[EcoSettlementTotals] = Field(default_factory=list)
    currency: str = "LKR"


class EcoProviderSettlementItem(BaseModel):
    """
    Single provider settlement payout record (P14).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    provider_profile_id: int
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


class EcoProviderSettlementPagination(BaseModel):
    """
    Paginated list of provider settlement payout records (P14).
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[EcoProviderSettlementItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# ============================================================
# Phase 5B: Ratings & Reviews Schemas (T13, P17)
# ============================================================


class EcoReviewCreateRequest(BaseModel):
    """
    Traveler review submission request for an eligible completed eco booking (T13).
    """

    model_config = ConfigDict(extra="forbid")

    booking_id: int = Field(..., description="Eligible completed eco-tourism booking ID")
    booking_source: Literal["activity", "eco_experience"] = "activity"
    rating: Decimal = Field(
        ...,
        ge=Decimal("1.00"),
        le=Decimal("5.00"),
        decimal_places=2,
        description="Rating from 1.00 to 5.00",
    )
    title: str | None = Field(default=None, max_length=150, description="Optional review title")
    comment: str | None = Field(default=None, max_length=2000, description="Optional review comment")

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class EcoReviewResponse(BaseModel):
    """
    Public representation of a verified eco experience review (T13).
    SECURITY RULE: Exposes only reviewer's display name, never private contact information.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    booking_id: int | None = None
    booking_source: str | None = None
    user_id: int
    reviewer_name: str
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool = True
    is_published: bool = True
    created_at: datetime


class EcoReviewListResponse(BaseModel):
    """
    Paginated public reviews list with aggregated rating statistics (T13).
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[EcoReviewResponse]
    total: int
    average_rating: float
    page: int
    page_size: int
    total_pages: int


class EcoProviderReviewItem(BaseModel):
    """
    Detailed review feedback item for provider dashboard (P17).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    eco_experience_id: int
    eco_experience_title: str
    booking_id: int | None = None
    booking_source: str | None = None
    booking_reference: str | None = None
    user_id: int
    reviewer_name: str
    rating: Decimal
    title: str | None = None
    comment: str | None = None
    is_verified_booking: bool
    is_published: bool
    created_at: datetime


class EcoProviderReviewListResponse(BaseModel):
    """
    Paginated review feedback response for provider dashboard with aggregated metrics (P17).
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[EcoProviderReviewItem]
    total: int
    average_rating: float
    page: int
    page_size: int
    total_pages: int


# ============================================================
# Weather & Environmental Emergency Cancellation (E06)
# ============================================================


class EcoWeatherEmergencyRequest(BaseModel):
    """
    Payload for authorized provider to declare a weather emergency or hazard (E06).
    """

    model_config = ConfigDict(extra="forbid")

    hazard_type: str = Field(
        default="weather_alert",
        description="Hazard type: 'weather_alert', 'monsoon_season', 'wildlife_breeding', or 'park_closure'",
    )
    reason: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="Detailed explanation of the weather hazard or environmental safety risk",
    )
    slot_id: int | None = Field(
        default=None,
        description="Optional single slot ID to cancel specifically",
    )
    start_date: date | None = Field(
        default=None,
        description="Start date of the weather restriction window (required if slot_id is omitted)",
    )
    end_date: date | None = Field(
        default=None,
        description="End date of the weather restriction window (required if slot_id is omitted)",
    )

    @field_validator("hazard_type")
    @classmethod
    def validate_hazard_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_SEASON_RESTRICTION_TYPES:
            raise ValueError(
                f"Invalid hazard_type '{v}'. Allowed: {', '.join(VALID_SEASON_RESTRICTION_TYPES)}"
            )
        return cleaned

    @model_validator(mode="after")
    def validate_targets(self) -> "EcoWeatherEmergencyRequest":
        if self.slot_id is None and (self.start_date is None or self.end_date is None):
            raise ValueError(
                "Either slot_id or both start_date and end_date must be provided."
            )
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be greater than or equal to start_date.")
        return self


class EcoWeatherEmergencyResponse(BaseModel):
    """
    Response returned after declaring a weather emergency and cancelling affected slots/bookings.
    """

    model_config = ConfigDict(from_attributes=True)

    eco_experience_id: int
    hazard_type: str
    reason: str
    restriction_id: int | None = None
    affected_slots_count: int
    cancelled_bookings_count: int
    released_capacity_total: int
    refunds_initiated_count: int
    total_refund_amount: Decimal
    cancelled_booking_ids: list[int]
    status: str = "success"
    message: str

