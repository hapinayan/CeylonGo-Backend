from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Time,
    Unicode,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

if TYPE_CHECKING:
    from app.models.booking import Booking


class Homestay(Base):
    __tablename__ = "homestays"

    __table_args__ = (
        CheckConstraint(
            "price_per_night >= 0",
            name="CK_homestays_price",
        ),
        CheckConstraint(
            "max_guests > 0",
            name="CK_homestays_guests",
        ),
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) "
            "OR (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0)",
            name="CK_homestays_coords",
        ),
        CheckConstraint(
            "approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended')",
            name="CK_homestays_approval_status",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'pending', 'verified', 'rejected', 'suspended')",
            name="CK_homestays_verification_status",
        ),
        CheckConstraint(
            "free_cancellation_hours >= 0",
            name="CK_homestays_cancellation_hours",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    owner_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    provider_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=True,
        index=True,
    )

    owner: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[owner_id],
    )

    provider_profile: Mapped["ProviderProfile | None"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
        backref=backref("homestays", lazy="select"),
    )

    @property
    def provider_business_name(self) -> str | None:
        if self.provider_profile:
            return self.provider_profile.business_name
        return None

    name: Mapped[str] = mapped_column(
        Unicode(150),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Unicode(None),
        nullable=True,
    )

    location: Mapped[str] = mapped_column(
        Unicode(255),
        nullable=False,
    )

    # Location Hierarchy & Coordinates
    province: Mapped[str | None] = mapped_column(
        Unicode(100),
        nullable=True,
    )

    district: Mapped[str | None] = mapped_column(
        Unicode(100),
        nullable=True,
        index=True,
    )

    city: Mapped[str | None] = mapped_column(
        Unicode(100),
        nullable=True,
    )

    address: Mapped[str | None] = mapped_column(
        Unicode(500),
        nullable=True,
    )

    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )

    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )

    price_per_night: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    max_guests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Check-in and Check-out Times
    check_in_time: Mapped[time] = mapped_column(
        Time,
        default=time(14, 0),
        nullable=False,
    )

    check_out_time: Mapped[time] = mapped_column(
        Time,
        default=time(11, 0),
        nullable=False,
    )

    # Listing Approval & Verification Workflow
    approval_status: Mapped[str] = mapped_column(
        Unicode(20),
        default="approved",
        nullable=False,
    )

    verification_status: Mapped[str] = mapped_column(
        Unicode(20),
        default="unverified",
        nullable=False,
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    # Policies & Timestamps
    cancellation_policy: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    free_cancellation_hours: Mapped[int] = mapped_column(
        Integer,
        default=24,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.sysutcdatetime(),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    # Relationships
    reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )

    images: Mapped[list["HomestayImage"]] = relationship(
        "HomestayImage",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    amenities: Mapped[list["HomestayAmenity"]] = relationship(
        "HomestayAmenity",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    availability_blocks: Mapped[list["HomestayAvailabilityBlock"]] = relationship(
        "HomestayAvailabilityBlock",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    bookings: Mapped[list["Booking"]] = relationship(
        "Booking",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HomestayImage(Base):
    __tablename__ = "homestay_images"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    image_url: Mapped[str] = mapped_column(
        Unicode(1000),
        nullable=False,
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="images",
    )


class HomestayAmenity(Base):
    __tablename__ = "homestay_amenities"

    __table_args__ = (
        UniqueConstraint(
            "homestay_id",
            "name",
            name="UQ_homestay_amenities_name",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        Unicode(100),
        nullable=False,
    )

    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="amenities",
    )


class HomestayAvailabilityBlock(Base):
    __tablename__ = "homestay_availability_blocks"

    __table_args__ = (
        CheckConstraint(
            "end_date > start_date",
            name="CK_homestay_availability_blocks_dates",
        ),
        CheckConstraint(
            "block_type IN ('maintenance', 'owner_unavailable', 'admin_block', 'renovation', 'seasonal_closure', 'other')",
            name="CK_homestay_availability_blocks_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    block_type: Mapped[str] = mapped_column(
        Unicode(50),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        Unicode(500),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        index=True,
    )

    created_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        server_default=func.sysutcdatetime(),
        onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        nullable=False,
    )

    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="availability_blocks",
    )

    creator: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by],
    )
