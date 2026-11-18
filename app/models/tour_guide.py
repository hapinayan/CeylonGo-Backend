from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Time,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile


class TourGuide(Base):
    """
    Core local tour guide entity representing a verified service provider.
    Identity and ownership derive strictly through provider_profile_id.
    """
    __tablename__ = "tour_guides"

    __table_args__ = (
        CheckConstraint("years_of_experience >= 0", name="CK_tour_guides_experience"),
        CheckConstraint("hourly_rate IS NULL OR hourly_rate >= 0", name="CK_tour_guides_hourly_rate"),
        CheckConstraint("daily_rate >= 0", name="CK_tour_guides_daily_rate"),
        CheckConstraint(
            "(base_latitude IS NULL AND base_longitude IS NULL) "
            "OR (base_latitude BETWEEN -90.0 AND 90.0 AND base_longitude BETWEEN -180.0 AND 180.0)",
            name="CK_tour_guides_coords",
        ),
        CheckConstraint(
            "approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended')",
            name="CK_tour_guides_approval_status",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'rejected', 'suspended')",
            name="CK_tour_guides_verification_status",
        ),
        CheckConstraint(
            "license_type IS NULL OR license_type IN ('national', 'chauffeur', 'site', 'area')",
            name="CK_tour_guides_license_type",
        ),
        CheckConstraint("free_cancellation_hours >= 0", name="CK_tour_guides_cancellation_hours"),
        CheckConstraint("max_group_size > 0", name="CK_tour_guides_group_size"),
        CheckConstraint("rating_average BETWEEN 0.00 AND 5.00", name="CK_tour_guides_rating"),
        CheckConstraint("review_count >= 0", name="CK_tour_guides_review_count"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Professional Details
    bio: Mapped[str] = mapped_column(Unicode(2000), nullable=False)
    years_of_experience: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    specialties: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)

    # Licensing (SLTDA)
    license_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    license_type: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)
    license_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Pricing & Currency
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)

    # Base Location for Interactive Map
    base_city: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    base_district: Mapped[str] = mapped_column(Unicode(100), nullable=False, index=True)
    base_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    base_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    # Availability & Discovery Visibility
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Separate Listing Approval & Review Workflow
    approval_status: Mapped[str] = mapped_column(
        Unicode(20), default="draft", nullable=False, index=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    # Guide Identity & Credential Verification State
    verification_status: Mapped[str] = mapped_column(
        Unicode(20), default="unverified", nullable=False, index=True
    )

    # Ratings & Reviews
    rating_average: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal("0.00"), nullable=False
    )
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Cancellation & Group Size
    cancellation_policy: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    free_cancellation_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    max_group_size: Mapped[int] = mapped_column(Integer, default=10, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    # Relationships
    provider_profile: Mapped[ProviderProfile] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
        backref=backref("tour_guide", uselist=False),
    )
    reviewer: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )
    languages: Mapped[list["TourGuideLanguage"]] = relationship(
        "TourGuideLanguage",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    service_areas: Mapped[list["TourGuideServiceArea"]] = relationship(
        "TourGuideServiceArea",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    credentials: Mapped[list["TourGuideCredential"]] = relationship(
        "TourGuideCredential",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    images: Mapped[list["TourGuideImage"]] = relationship(
        "TourGuideImage",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    packages: Mapped[list["TourGuidePackage"]] = relationship(
        "TourGuidePackage",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    availabilities: Mapped[list["TourGuideAvailability"]] = relationship(
        "TourGuideAvailability",
        back_populates="tour_guide",
        cascade="all, delete-orphan",
    )
    bookings: Mapped[list["TourGuideBooking"]] = relationship(
        "TourGuideBooking",
        back_populates="tour_guide",
        foreign_keys="TourGuideBooking.tour_guide_id",
    )
    verifications: Mapped[list["TourGuideVerification"]] = relationship(
        "TourGuideVerification",
        back_populates="tour_guide",
        foreign_keys="TourGuideVerification.tour_guide_id",
    )

    @property
    def user(self) -> User | None:
        """Derived user account through provider_profile."""
        return self.provider_profile.user if self.provider_profile else None


class TourGuideLanguage(Base):
    __tablename__ = "tour_guide_languages"

    __table_args__ = (
        UniqueConstraint("tour_guide_id", "language_code", name="UQ_tour_guide_languages_code"),
        CheckConstraint(
            "proficiency_level IN ('basic', 'conversational', 'fluent', 'native')",
            name="CK_tour_guide_languages_proficiency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    language_code: Mapped[str] = mapped_column(Unicode(10), nullable=False)
    language_name: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    proficiency_level: Mapped[str] = mapped_column(
        Unicode(20), default="fluent", nullable=False
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="languages")


class TourGuideServiceArea(Base):
    __tablename__ = "tour_guide_service_areas"

    __table_args__ = (
        UniqueConstraint("tour_guide_id", "district", "city", name="UQ_tour_guide_service_areas"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    province: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    district: Mapped[str] = mapped_column(Unicode(100), nullable=False, index=True)
    city: Mapped[str] = mapped_column(Unicode(100), default="", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="service_areas")


class TourGuideCredential(Base):
    """
    Official certificates, SLTDA licenses, and qualifications.
    file_reference is an internal protected storage identifier, never exposed in public API schemas.
    """
    __tablename__ = "tour_guide_credentials"

    __table_args__ = (
        UniqueConstraint("id", "tour_guide_id", name="UQ_tour_guide_credentials_guide"),
        CheckConstraint(
            "credential_type IN ('sltda_license', 'first_aid', 'degree', 'wildlife_permit', 'driving_license', 'other')",
            name="CK_tour_guide_credentials_type",
        ),
        CheckConstraint(
            "verification_status IN ('pending', 'approved', 'rejected')",
            name="CK_tour_guide_credentials_status",
        ),
        CheckConstraint(
            "expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date",
            name="CK_tour_guide_credentials_dates",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    credential_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    issuing_authority: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    certificate_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Protected internal storage reference
    file_reference: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    verification_status: Mapped[str] = mapped_column(
        Unicode(20), default="pending", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="credentials")
    verifications: Mapped[list["TourGuideVerification"]] = relationship(
        "TourGuideVerification",
        back_populates="credential",
        overlaps="verifications,tour_guide",
    )


class TourGuideImage(Base):
    __tablename__ = "tour_guide_images"

    __table_args__ = (
        CheckConstraint("display_order >= 0", name="CK_tour_guide_images_order"),
        Index(
            "UQ_tour_guide_images_profile",
            "tour_guide_id",
            unique=True,
            sqlite_where=text("is_profile = 1"),
            mssql_where=text("is_profile = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_profile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="images")


class TourGuidePackage(Base):
    __tablename__ = "tour_guide_packages"

    __table_args__ = (
        UniqueConstraint("id", "tour_guide_id", name="UQ_tour_guide_packages_guide"),
        CheckConstraint("duration_hours > 0", name="CK_tour_guide_packages_duration"),
        CheckConstraint("price >= 0", name="CK_tour_guide_packages_price"),
        CheckConstraint(
            "pricing_type IN ('per_person', 'per_group')",
            name="CK_tour_guide_packages_pricing_type",
        ),
        CheckConstraint("max_people > 0", name="CK_tour_guide_packages_max_people"),
        CheckConstraint(
            "(meeting_latitude IS NULL AND meeting_longitude IS NULL) "
            "OR (meeting_latitude BETWEEN -90.0 AND 90.0 AND meeting_longitude BETWEEN -180.0 AND 180.0)",
            name="CK_tour_guide_packages_coords",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    description: Mapped[str] = mapped_column(Unicode(4000), nullable=False)
    itinerary: Mapped[str | None] = mapped_column(
        UnicodeText().with_variant(mssql.NVARCHAR(length="max"), "mssql"),
        nullable=True,
    )
    duration_hours: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    pricing_type: Mapped[str] = mapped_column(
        Unicode(20), default="per_person", nullable=False
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    max_people: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    included_services: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    excluded_services: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    meeting_point: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    meeting_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    meeting_longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="packages")
    bookings: Mapped[list["TourGuideBooking"]] = relationship(
        "TourGuideBooking",
        back_populates="package",
        overlaps="bookings,tour_guide",
    )


class TourGuideAvailability(Base):
    __tablename__ = "tour_guide_availability"

    __table_args__ = (
        UniqueConstraint(
            "tour_guide_id",
            "available_date",
            "start_time",
            "end_time",
            name="UQ_tour_guide_availability_slot",
        ),
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) "
            "OR (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)",
            name="CK_tour_guide_availability_times",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    available_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship("TourGuide", back_populates="availabilities")


class TourGuideBooking(Base):
    """
    Booking record linking a traveler to a tour guide.
    Uses unambiguous start_datetime and end_datetime for same-day and multi-day bookings.
    Cross-guide package assignment is strictly forbidden by composite foreign key.
    """
    __tablename__ = "tour_guide_bookings"

    __table_args__ = (
        ForeignKeyConstraint(
            ["package_id", "tour_guide_id"],
            ["tour_guide_packages.id", "tour_guide_packages.tour_guide_id"],
            name="FK_tour_guide_bookings_package_guide",
            ondelete="NO ACTION",
        ),
        CheckConstraint("end_datetime > start_datetime", name="CK_tour_guide_bookings_datetimes"),
        CheckConstraint("number_of_people > 0", name="CK_tour_guide_bookings_people"),
        CheckConstraint("total_price >= 0", name="CK_tour_guide_bookings_price"),
        CheckConstraint("free_cancellation_hours_snapshot >= 0", name="CK_tour_guide_bookings_cancellation_hours_snapshot"),
        CheckConstraint(
            "booking_status IN ('pending', 'confirmed', 'in_progress', 'completed', 'cancelled', 'rejected', 'expired')",
            name="CK_tour_guide_bookings_status",
        ),
        CheckConstraint(
            "payment_method IN ('online', 'pay_at_location')",
            name="CK_tour_guide_bookings_payment_method",
        ),
        CheckConstraint(
            "payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')",
            name="CK_tour_guide_bookings_payment_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id"),
        nullable=False,
        index=True,
    )
    traveler_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    package_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Unambiguous start and end timestamps (supports same-day & multi-day)
    booking_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_datetime: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"), nullable=False, index=True
    )
    end_datetime: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"), nullable=False
    )

    number_of_people: Mapped[int] = mapped_column(Integer, nullable=False)
    pickup_location: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    special_requests: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    total_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)

    booking_status: Mapped[str] = mapped_column(
        Unicode(30), default="pending", nullable=False, index=True
    )
    payment_method: Mapped[str] = mapped_column(
        Unicode(20), default="pay_at_location", nullable=False
    )
    payment_status: Mapped[str] = mapped_column(
        Unicode(20), default="unpaid", nullable=False
    )

    # Expiration for pending bookings
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"), nullable=True, index=True
    )

    # Immutable policy snapshots at booking time
    cancellation_policy_snapshot: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    free_cancellation_hours_snapshot: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    # Cancellation details
    cancellation_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    cancelled_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship(
        "TourGuide",
        back_populates="bookings",
        foreign_keys=[tour_guide_id],
        overlaps="bookings,package",
    )
    traveler: Mapped["User"] = relationship("User", foreign_keys=[traveler_id])
    cancelled_by_user: Mapped["User | None"] = relationship(
        "User", foreign_keys=[cancelled_by]
    )
    package: Mapped["TourGuidePackage | None"] = relationship(
        "TourGuidePackage",
        back_populates="bookings",
        foreign_keys=[package_id],
    )


class TourGuideVerification(Base):
    """
    Verification review and audit record for a tour guide credential.
    Cross-guide credential assignment is strictly forbidden by composite foreign key.
    """
    __tablename__ = "tour_guide_verifications"

    __table_args__ = (
        ForeignKeyConstraint(
            ["credential_id", "tour_guide_id"],
            ["tour_guide_credentials.id", "tour_guide_credentials.tour_guide_id"],
            name="FK_tour_guide_verifications_credential_guide",
            ondelete="NO ACTION",
        ),
        CheckConstraint(
            "status IN ('approved', 'rejected', 'requires_info')",
            name="CK_tour_guide_verifications_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tour_guide_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tour_guides.id"),
        nullable=False,
        index=True,
    )
    credential_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    reviewed_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    verification_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    notes: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    verified_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    tour_guide: Mapped["TourGuide"] = relationship(
        "TourGuide",
        back_populates="verifications",
        foreign_keys=[tour_guide_id],
        overlaps="verifications,credential",
    )
    credential: Mapped["TourGuideCredential"] = relationship(
        "TourGuideCredential",
        back_populates="verifications",
        foreign_keys=[credential_id],
    )
    reviewer: Mapped["User"] = relationship("User", foreign_keys=[reviewed_by])
