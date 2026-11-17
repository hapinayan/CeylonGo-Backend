"""
SQLAlchemy models for Module 04 — Eco-Tourism Sponsored Promotions & Performance Analytics.

Phase P1.1A Database Foundation:
- EcoPromotionPackage (dbo.eco_promotion_packages)
- EcoSponsoredPromotion (dbo.eco_sponsored_promotions)
- EcoPromotionAnalytics (dbo.eco_promotion_analytics)
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

# ==============================================================================
# Domain Constants
# ==============================================================================

VALID_CAMPAIGN_STATUSES = (
    "pending_approval",
    "approved",
    "active",
    "rejected",
    "expired",
    "cancelled",
)

VALID_PAYMENT_STATUSES = (
    "unpaid",
    "pending_verification",
    "paid",
    "failed",
    "refunded",
)

VALID_PLACEMENT_TIERS = (
    "standard",
    "featured",
    "spotlight",
)


# ==============================================================================
# 1. Eco Promotion Package Model
# ==============================================================================

class EcoPromotionPackage(Base):
    __tablename__ = "eco_promotion_packages"

    __table_args__ = (
        UniqueConstraint("name", name="UQ_eco_promotion_packages_name"),
        CheckConstraint("duration_days > 0", name="CK_eco_promo_pkg_days"),
        CheckConstraint("price >= 0.0", name="CK_eco_promo_pkg_price"),
        CheckConstraint(
            "placement_tier IN ('standard', 'featured', 'spotlight')",
            name="CK_eco_promo_pkg_tier",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    description: Mapped[str | None] = mapped_column(
        Unicode(500), nullable=True
    )
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        Unicode(10), default="LKR", nullable=False
    )
    placement_tier: Mapped[str] = mapped_column(
        Unicode(30), default="standard", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, index=True
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
    promotions: Mapped[list["EcoSponsoredPromotion"]] = relationship(
        "EcoSponsoredPromotion",
        back_populates="package",
        foreign_keys="[EcoSponsoredPromotion.package_id]",
    )


# ==============================================================================
# 2. Eco Sponsored Promotion Campaign Model
# ==============================================================================

class EcoSponsoredPromotion(Base):
    __tablename__ = "eco_sponsored_promotions"

    __table_args__ = (
        CheckConstraint(
            "package_price_snapshot >= 0.0", name="CK_eco_spon_price"
        ),
        CheckConstraint("duration_days_snapshot > 0", name="CK_eco_spon_days"),
        CheckConstraint(
            "campaign_status IN ('pending_approval', 'approved', 'active', 'rejected', 'expired', 'cancelled')",
            name="CK_eco_spon_status",
        ),
        CheckConstraint(
            "payment_status IN ('unpaid', 'pending_verification', 'paid', 'failed', 'refunded')",
            name="CK_eco_spon_payment",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="CK_eco_spon_dates",
        ),
        CheckConstraint(
            "campaign_status <> 'active' OR (starts_at IS NOT NULL AND ends_at IS NOT NULL AND payment_status = 'paid')",
            name="CK_eco_spon_active_consistent",
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
    provider_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id", ondelete="NO ACTION"),
        nullable=False,
        index=True,
    )
    package_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_promotion_packages.id", ondelete="NO ACTION"),
        nullable=False,
        index=True,
    )

    # Immutable Package Snapshot at time of booking
    package_price_snapshot: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency_snapshot: Mapped[str] = mapped_column(
        Unicode(10), default="LKR", nullable=False
    )
    duration_days_snapshot: Mapped[int] = mapped_column(
        Integer, nullable=False
    )

    # Status and Lifecycle
    campaign_status: Mapped[str] = mapped_column(
        Unicode(30), default="pending_approval", nullable=False, index=True
    )
    payment_status: Mapped[str] = mapped_column(
        Unicode(30), default="unpaid", nullable=False, index=True
    )
    payment_reference: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )

    # Scheduled Validity Window
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    # Review & Audit
    requested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    review_reason: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
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
    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        foreign_keys=[eco_experience_id],
    )
    provider_profile: Mapped["ProviderProfile"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
    )
    package: Mapped["EcoPromotionPackage"] = relationship(
        "EcoPromotionPackage",
        back_populates="promotions",
        foreign_keys=[package_id],
    )
    reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )
    analytics: Mapped[list["EcoPromotionAnalytics"]] = relationship(
        "EcoPromotionAnalytics",
        back_populates="promotion",
        cascade="all, delete-orphan",
        foreign_keys="[EcoPromotionAnalytics.promotion_id]",
    )

    @property
    def is_currently_active(self) -> bool:
        """Helper to determine whether the promotion is active right now."""
        from datetime import UTC, datetime
        now = datetime.now(UTC)
        if (
            self.campaign_status in ("approved", "active")
            and self.payment_status == "paid"
            and self.starts_at is not None
            and self.ends_at is not None
        ):
            starts = self.starts_at.replace(tzinfo=UTC) if self.starts_at.tzinfo is None else self.starts_at
            ends = self.ends_at.replace(tzinfo=UTC) if self.ends_at.tzinfo is None else self.ends_at
            return starts <= now <= ends
        return False


# ==============================================================================
# 3. Eco Promotion Analytics Model
# ==============================================================================

class EcoPromotionAnalytics(Base):
    __tablename__ = "eco_promotion_analytics"

    __table_args__ = (
        UniqueConstraint(
            "promotion_id",
            "metric_date",
            name="UQ_eco_promotion_analytics",
        ),
        CheckConstraint(
            "impressions_count >= 0 AND clicks_count >= 0",
            name="CK_eco_promo_counts",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    promotion_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_sponsored_promotions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="NO ACTION"),
        nullable=False,
        index=True,
    )
    metric_date: Mapped[date] = mapped_column(
        Date, nullable=False, index=True
    )
    impressions_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    clicks_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
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
    promotion: Mapped["EcoSponsoredPromotion"] = relationship(
        "EcoSponsoredPromotion",
        back_populates="analytics",
        foreign_keys=[promotion_id],
    )
    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        foreign_keys=[eco_experience_id],
    )
