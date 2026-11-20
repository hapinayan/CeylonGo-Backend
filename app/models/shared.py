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
    UnicodeText,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.models.user import Base, User


class ListingReference(Base):
    __tablename__ = "listing_references"
    __table_args__ = (
        UniqueConstraint("module_type", "target_id", name="UQ_listing_references"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Unicode(255), nullable=False)
    district: Mapped[str] = mapped_column(Unicode(100), nullable=False, index=True)
    city: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class BookingReference(Base):
    __tablename__ = "booking_references"
    __table_args__ = (
        UniqueConstraint("module_type", "target_booking_id", name="UQ_booking_references_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference: Mapped[str] = mapped_column(
        "booking_reference", Unicode(50), unique=True, nullable=False
    )
    module_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    target_booking_id: Mapped[int] = mapped_column(
        "target_booking_id", Integer, nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    total_price: Mapped[Decimal] = mapped_column(
        "total_price", Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    booking_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="unpaid", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    # Synonyms for backward compatibility with existing Python consumers:
    booking_reference_code = synonym("booking_reference")
    target_id = synonym("target_booking_id")
    total_amount = synonym("total_price")

    # In-memory properties for legacy callers expecting start_time, end_time, updated_at
    # (Note: These columns do NOT exist in SQL Server Migration 027 dbo.booking_references;
    # keeping them as properties ensures SQLAlchemy NEVER attempts to query or insert them.)
    @property
    def start_time(self) -> datetime | None:
        return getattr(self, "_start_time", None)

    @start_time.setter
    def start_time(self, value: datetime | None) -> None:
        self._start_time = value

    @property
    def end_time(self) -> datetime | None:
        return getattr(self, "_end_time", None)

    @end_time.setter
    def end_time(self, value: datetime | None) -> None:
        self._end_time = value

    @property
    def updated_at(self) -> datetime | None:
        return getattr(self, "_updated_at", None)

    @updated_at.setter
    def updated_at(self, value: datetime | None) -> None:
        self._updated_at = value


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_references.id"), nullable=False, index=True
    )
    transaction_reference: Mapped[str] = mapped_column(Unicode(100), unique=True, nullable=False)
    payment_gateway: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    gateway_response: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_transactions.id"), nullable=False, index=True
    )
    refund_reference: Mapped[str] = mapped_column(Unicode(100), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    processed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProviderSettlement(Base):
    __tablename__ = "provider_settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    gross_earnings: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    platform_commission: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    net_payable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    settlement_reference: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Unicode(200), nullable=False)
    message: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    notification_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    reference_module: Mapped[str | None] = mapped_column(Unicode(30), nullable=True)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating >= 1.00 AND rating <= 5.00", name="CK_reviews_rating"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listing_references.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("booking_references.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    title: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    comment: Mapped[str | None] = mapped_column("comment", Unicode(2000), nullable=True)
    is_verified_booking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), onupdate=func.sysutcdatetime(), nullable=False
    )

    # Synonyms for backward compatibility
    review_text = synonym("comment")

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    listing_reference: Mapped["ListingReference"] = relationship(
        "ListingReference", foreign_keys=[listing_reference_id]
    )
    booking_reference: Mapped["BookingReference | None"] = relationship(
        "BookingReference", foreign_keys=[booking_reference_id]
    )


class Wishlist(Base):
    __tablename__ = "wishlists"
    __table_args__ = (
        UniqueConstraint("user_id", "listing_reference_id", name="UQ_wishlists_user_listing"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listing_references.id", ondelete="CASCADE"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    listing_reference: Mapped["ListingReference"] = relationship(
        "ListingReference", foreign_keys=[listing_reference_id]
    )


class PromotionPackage(Base):
    __tablename__ = "promotion_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    module_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PromotionCampaign(Base):
    __tablename__ = "promotion_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("promotion_packages.id"), nullable=False, index=True
    )
    listing_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listing_references.id"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), default="active", nullable=False)
    impressions_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PromotionEvent(Base):
    __tablename__ = "promotion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("promotion_campaigns.id"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Unicode(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class BookingStatusHistory(Base):
    __tablename__ = "booking_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_references.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(
        "from_status", Unicode(30), nullable=True
    )
    to_status: Mapped[str] = mapped_column(
        "to_status", Unicode(30), nullable=False
    )
    changed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(
        "notes", Unicode(500), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    # Synonyms for backward compatibility:
    old_status = synonym("from_status")
    new_status = synonym("to_status")
    change_reason = synonym("notes")


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    entity_name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Unicode(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
