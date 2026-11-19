from datetime import date, datetime
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
    Unicode,
    UnicodeText,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User


class ListingReference(Base):
    __tablename__ = "listing_references"

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference: Mapped[str] = mapped_column(Unicode(50), unique=True, nullable=False)
    module_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    target_booking_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    booking_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="unpaid", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_references.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    gateway_name: Mapped[str] = mapped_column(Unicode(50), default="payhere", nullable=False)
    gateway_transaction_id: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    payment_method: Mapped[str] = mapped_column(Unicode(50), default="cash", nullable=False)
    gateway_response_payload: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    gross_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    commission_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    provider_payable_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payment_transactions.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    refund_reason: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    refund_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    processed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class ProviderSettlement(Base):
    __tablename__ = "provider_settlements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    net_payable: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    settlement_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    payout_reference: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    message: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    notification_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    link_url: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listing_references.id"), nullable=False, index=True
    )
    booking_reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("booking_references.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    title: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    comment: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    is_verified_booking: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Wishlist(Base):
    __tablename__ = "wishlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    listing_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("listing_references.id"), nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PromotionPackage(Base):
    __tablename__ = "promotion_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    description: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class PromotionCampaign(Base):
    __tablename__ = "promotion_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    listing_reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("listing_references.id", ondelete="SET NULL"), nullable=True, index=True
    )
    campaign_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    target_module: Mapped[str] = mapped_column(Unicode(30), default="homestay", nullable=False)
    budget: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    spent_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    status: Mapped[str] = mapped_column(Unicode(30), default="draft", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    listing_reference: Mapped["ListingReference | None"] = relationship(
        "ListingReference",
        foreign_keys=[listing_reference_id],
    )


class AdvertisingPayment(Base):
    __tablename__ = "advertising_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("promotion_campaigns.id"), nullable=False, index=True
    )
    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    order_reference: Mapped[str] = mapped_column(Unicode(100), unique=True, nullable=False)
    gateway_transaction_id: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    gateway_name: Mapped[str] = mapped_column(Unicode(50), default="payhere", nullable=False)
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False, index=True)
    initiated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    campaign: Mapped["PromotionCampaign"] = relationship(
        "PromotionCampaign",
        foreign_keys=[campaign_id],
        backref=backref("advertising_payments", lazy="select"),
    )
    provider_profile: Mapped["ProviderProfile"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
    )


class PromotionEvent(Base):
    __tablename__ = "promotion_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("promotion_campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    booking_reference_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("booking_references.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Unicode(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    booking_reference: Mapped["BookingReference | None"] = relationship(
        "BookingReference",
        foreign_keys=[booking_reference_id],
    )


class BookingStatusHistory(Base):
    __tablename__ = "booking_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_reference_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("booking_references.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_status: Mapped[str | None] = mapped_column(Unicode(30), nullable=True)
    to_status: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    changed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    notes: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column("action_type", Unicode(50), nullable=False)
    entity_name: Mapped[str] = mapped_column("entity_type", Unicode(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(Unicode(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
