"""
SQLAlchemy models for Module 04 — Eco-Tourism Core Entities.

Entities:
- EcoExperience
- EcoExperienceImage
- EcoSafetyRule
- EcoSeasonRestriction
- EcoPractice
- EcoEvidence
- EcoVerification
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

VALID_ECO_CATEGORIES = (
    "Forest & Nature Trails",
    "Wildlife & Birdwatching",
    "Sustainable Village Experiences",
    "Conservation Activities",
    "Eco-Friendly Stays",
    "Community Eco-Tourism",
)

VALID_PRICING_TYPES = ("per_person", "per_group")

VALID_LISTING_STATUSES = (
    "draft",
    "pending_approval",
    "approved",
    "rejected",
    "deactivated",
)

VALID_ECO_BADGE_STATUSES = (
    "none",
    "pending",
    "granted",
    "rejected",
    "revoked",
)

VALID_SAFETY_RULE_TYPES = (
    "wildlife_no_feeding",
    "environmental_conduct",
    "operator_instruction",
    "equipment_requirement",
    "health_age_restriction",
)

VALID_SEASON_RESTRICTION_TYPES = (
    "monsoon_season",
    "wildlife_breeding",
    "weather_alert",
    "park_closure",
)

VALID_PRACTICE_CATEGORIES = (
    "waste_reduction",
    "renewable_energy",
    "community_employment",
    "wildlife_protection",
    "habitat_restoration",
    "sustainable_sourcing",
)

VALID_EVIDENCE_DOCUMENT_TYPES = (
    "certificate",
    "permit",
    "photo_proof",
    "audit_report",
)

VALID_VERIFICATION_DECISIONS = (
    "submitted",
    "granted",
    "rejected",
    "revoked",
)


class EcoExperience(Base):
    __tablename__ = "eco_experiences"

    __table_args__ = (
        CheckConstraint(
            "eco_category IN ("
            "'Forest & Nature Trails', "
            "'Wildlife & Birdwatching', "
            "'Sustainable Village Experiences', "
            "'Conservation Activities', "
            "'Eco-Friendly Stays', "
            "'Community Eco-Tourism'"
            ")",
            name="CK_eco_experiences_category",
        ),
        CheckConstraint(
            "pricing_type IN ('per_person', 'per_group')",
            name="CK_eco_experiences_pricing_type",
        ),
        CheckConstraint(
            "base_price >= 0",
            name="CK_eco_experiences_base_price",
        ),
        CheckConstraint(
            "duration_hours > 0",
            name="CK_eco_experiences_duration_hours",
        ),
        CheckConstraint(
            "max_group_size > 0 AND max_group_size <= 50",
            name="CK_eco_experiences_max_group_size",
        ),
        CheckConstraint(
            "min_participants > 0 AND min_participants <= max_group_size",
            name="CK_eco_experiences_min_participants",
        ),
        CheckConstraint(
            "min_age IS NULL OR (min_age >= 0 AND min_age <= 100)",
            name="CK_eco_experiences_min_age",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0)",
            name="CK_eco_experiences_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0)",
            name="CK_eco_experiences_longitude",
        ),
        CheckConstraint(
            "listing_status IN ('draft', 'pending_approval', 'approved', 'rejected', 'deactivated')",
            name="CK_eco_experiences_listing_status",
        ),
        CheckConstraint(
            "eco_badge_status IN ('none', 'pending', 'granted', 'rejected', 'revoked')",
            name="CK_eco_experiences_badge_status",
        ),
        CheckConstraint(
            "free_cancellation_hours >= 0",
            name="CK_eco_experiences_cancellation_hours",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    provider_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=False,
        index=True,
    )

    linked_homestay_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("homestays.id"),
        nullable=True,
        index=True,
    )

    # Core Listing Details
    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    eco_category: Mapped[str] = mapped_column(
        Unicode(60), nullable=False, index=True
    )

    description: Mapped[str | None] = mapped_column(
        Unicode(None), nullable=True
    )

    itinerary: Mapped[str | None] = mapped_column(
        Unicode(None), nullable=True
    )

    duration_hours: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False
    )

    # Location & Meeting Point
    district: Mapped[str] = mapped_column(
        Unicode(100), nullable=False, index=True
    )

    city: Mapped[str] = mapped_column(Unicode(100), nullable=False)

    address: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )

    meeting_point: Mapped[str] = mapped_column(
        Unicode(500), nullable=False
    )

    directions_info: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )

    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6), nullable=True
    )

    # Pricing & Environmental Group Size Limits
    pricing_type: Mapped[str] = mapped_column(
        Unicode(20), default="per_person", nullable=False
    )

    base_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    currency: Mapped[str] = mapped_column(
        Unicode(10), default="LKR", nullable=False
    )

    max_group_size: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    min_participants: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False
    )

    min_age: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    # Eco-Specific Guidelines, Wildlife Safety & Conservation Contribution
    environmental_guidelines: Mapped[str] = mapped_column(
        Unicode(2000), nullable=False
    )

    wildlife_safety_summary: Mapped[str] = mapped_column(
        Unicode(2000), nullable=False
    )

    conservation_factual_contribution: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    conservation_marketing_statement: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    protected_area_permit_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    certified_guide_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    permit_details: Mapped[str | None] = mapped_column(
        Unicode(500), nullable=True
    )

    cancellation_policy: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    free_cancellation_hours: Mapped[int] = mapped_column(
        Integer, default=24, nullable=False
    )

    # Listing Lifecycle & Admin Moderation State
    listing_status: Mapped[str] = mapped_column(
        Unicode(30), default="draft", nullable=False, index=True
    )

    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )

    approval_reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    approval_reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    approval_rejection_reason: Mapped[str | None] = mapped_column(
        Unicode(500), nullable=True
    )

    # Eco-Friendly Badge Status (Never automatically granted)
    eco_badge_status: Mapped[str] = mapped_column(
        Unicode(30), default="none", nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    # Relationships
    provider_profile: Mapped["ProviderProfile"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
        backref=backref("eco_experiences", lazy="select"),
    )

    linked_homestay: Mapped["Homestay | None"] = relationship(
        "Homestay",
        foreign_keys=[linked_homestay_id],
        backref=backref("linked_eco_experiences", lazy="select"),
    )

    approval_reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[approval_reviewed_by],
    )

    images: Mapped[list["EcoExperienceImage"]] = relationship(
        "EcoExperienceImage",
        back_populates="eco_experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoExperienceImage.sort_order.asc(), EcoExperienceImage.id.asc()",
    )

    safety_rules: Mapped[list["EcoSafetyRule"]] = relationship(
        "EcoSafetyRule",
        back_populates="eco_experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoSafetyRule.id.asc()",
    )

    season_restrictions: Mapped[list["EcoSeasonRestriction"]] = relationship(
        "EcoSeasonRestriction",
        back_populates="eco_experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoSeasonRestriction.start_date.asc()",
    )

    practices: Mapped[list["EcoPractice"]] = relationship(
        "EcoPractice",
        back_populates="eco_experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoPractice.id.asc()",
    )

    verifications: Mapped[list["EcoVerification"]] = relationship(
        "EcoVerification",
        back_populates="eco_experience",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoVerification.created_at.desc(), EcoVerification.id.desc()",
    )

    @property
    def is_publicly_visible(self) -> bool:
        """
        Public visibility strictly requires admin approval and publication.
        """
        return self.listing_status == "approved" and bool(self.is_published)

    @property
    def has_verified_eco_badge(self) -> bool:
        """
        Eco-Friendly badge is displayed ONLY when eco_badge_status == 'granted'
        and the latest EcoVerification decision is 'granted' and unexpired.
        Never automatically trusts provider claims.
        """
        if self.eco_badge_status != "granted":
            return False
        if not self.verifications:
            return False
        latest = self.verifications[0]
        if latest.decision != "granted" or latest.reviewed_by_admin_id is None:
            return False
        if latest.valid_until is not None:
            now = datetime.now(latest.valid_until.tzinfo)
            if latest.valid_until <= now:
                return False
        return True

    @property
    def conservation_information(self) -> dict[str, object]:
        return {
            "factual_contribution": self.conservation_factual_contribution,
            "marketing_statement": self.conservation_marketing_statement,
            "protected_area_permit_required": bool(
                self.protected_area_permit_required
            ),
            "certified_guide_required": bool(self.certified_guide_required),
            "permit_details": self.permit_details,
        }

    @property
    def provider_business_name(self) -> str | None:
        if self.provider_profile:
            return self.provider_profile.business_name
        return None

    @property
    def provider_phone(self) -> str | None:
        if self.provider_profile:
            return self.provider_profile.phone
        return None


class EcoExperienceImage(Base):
    __tablename__ = "eco_experience_images"

    __table_args__ = (
        CheckConstraint(
            "sort_order >= 0",
            name="CK_eco_experience_images_sort_order",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)

    caption: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        back_populates="images",
    )


class EcoSafetyRule(Base):
    __tablename__ = "eco_safety_rules"

    __table_args__ = (
        CheckConstraint(
            "rule_type IN ("
            "'wildlife_no_feeding', "
            "'environmental_conduct', "
            "'operator_instruction', "
            "'equipment_requirement', "
            "'health_age_restriction'"
            ")",
            name="CK_eco_safety_rules_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rule_type: Mapped[str] = mapped_column(
        Unicode(50), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    description: Mapped[str] = mapped_column(Unicode(1000), nullable=False)

    is_mandatory: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        back_populates="safety_rules",
    )


class EcoSeasonRestriction(Base):
    __tablename__ = "eco_season_restrictions"

    __table_args__ = (
        CheckConstraint(
            "end_date >= start_date",
            name="CK_eco_season_restrictions_dates",
        ),
        CheckConstraint(
            "restriction_type IN ("
            "'monsoon_season', "
            "'wildlife_breeding', "
            "'weather_alert', "
            "'park_closure'"
            ")",
            name="CK_eco_season_restrictions_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)

    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    restriction_type: Mapped[str] = mapped_column(
        Unicode(40), nullable=False, index=True
    )

    reason: Mapped[str] = mapped_column(Unicode(500), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
    )

    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        back_populates="season_restrictions",
    )

    creator: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by],
    )


class EcoPractice(Base):
    __tablename__ = "eco_practices"

    __table_args__ = (
        CheckConstraint(
            "practice_category IN ("
            "'waste_reduction', "
            "'renewable_energy', "
            "'community_employment', "
            "'wildlife_protection', "
            "'habitat_restoration', "
            "'sustainable_sourcing'"
            ")",
            name="CK_eco_practices_category",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    practice_category: Mapped[str] = mapped_column(
        Unicode(60), nullable=False, index=True
    )

    claim_description: Mapped[str] = mapped_column(
        Unicode(1000), nullable=False
    )

    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        back_populates="practices",
    )

    evidences: Mapped[list["EcoEvidence"]] = relationship(
        "EcoEvidence",
        back_populates="eco_practice",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EcoEvidence.id.asc()",
    )


class EcoEvidence(Base):
    """
    Private sustainability verification evidence linked to an EcoPractice.
    Security rule: file_reference is an internal server storage identifier
    and must NEVER be exposed in public API schemas.
    """

    __tablename__ = "eco_evidences"

    __table_args__ = (
        CheckConstraint(
            "document_type IN ('certificate', 'permit', 'photo_proof', 'audit_report')",
            name="CK_eco_evidences_document_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_practice_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_practices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    file_reference: Mapped[str] = mapped_column(
        Unicode(255), nullable=False
    )

    document_type: Mapped[str] = mapped_column(
        Unicode(50), nullable=False
    )

    notes: Mapped[str | None] = mapped_column(
        Unicode(500), nullable=True
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_practice: Mapped["EcoPractice"] = relationship(
        "EcoPractice",
        back_populates="evidences",
    )


class EcoVerification(Base):
    """
    Immutable decision history record for Eco-Friendly Badge verification.
    Preserves every submission, grant, rejection, and revocation decision with reason.
    """

    __tablename__ = "eco_verifications"

    __table_args__ = (
        CheckConstraint(
            "decision IN ('submitted', 'granted', 'rejected', 'revoked')",
            name="CK_eco_verifications_decision",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    submitted_by_provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=False,
        index=True,
    )

    reviewed_by_admin_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    decision: Mapped[str] = mapped_column(
        Unicode(30), default="submitted", nullable=False, index=True
    )

    reason: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        back_populates="verifications",
    )

    submitted_by_provider: Mapped["ProviderProfile"] = relationship(
        "ProviderProfile",
        foreign_keys=[submitted_by_provider_id],
    )

    reviewed_by_admin: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by_admin_id],
    )
