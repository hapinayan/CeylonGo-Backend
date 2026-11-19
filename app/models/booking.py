from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base, User

if TYPE_CHECKING:
    from app.models.homestay import Homestay


class Booking(Base):
    __tablename__ = "homestay_bookings"

    __table_args__ = (
        CheckConstraint(
            "check_out > check_in",
            name="CK_bookings_dates",
        ),
        CheckConstraint(
            "guests > 0",
            name="CK_bookings_guests",
        ),
        CheckConstraint(
            "total_price >= 0",
            name="CK_bookings_total_price",
        ),
        CheckConstraint(
            "status IN ('pending', 'awaiting_payment', 'confirmed', 'in_progress', 'completed', 'cancelled', 'rejected', 'expired')",
            name="CK_bookings_status",
        ),
        CheckConstraint(
            "payment_method IN ('online', 'pay_at_location')",
            name="CK_bookings_payment_method",
        ),
        CheckConstraint(
            "payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')",
            name="CK_bookings_payment_status",
        ),
        CheckConstraint(
            "free_cancellation_hours_snapshot >= 0",
            name="CK_bookings_cancellation_hours_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    check_in: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    check_out: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    guests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    total_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # Booking Lifecycle Status
    status: Mapped[str] = mapped_column(
        Unicode(30),
        nullable=False,
        default="confirmed",
    )

    # Financial & Payment Details
    currency: Mapped[str] = mapped_column(
        Unicode(3),
        default="LKR",
        nullable=False,
    )

    payment_method: Mapped[str] = mapped_column(
        Unicode(30),
        default="pay_at_location",
        nullable=False,
    )

    payment_status: Mapped[str] = mapped_column(
        Unicode(30),
        default="unpaid",
        nullable=False,
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
    )

    # Provider Approval & Payment Expiration
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
    )

    payment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
        index=True,
    )

    expired_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
    )

    # Commission & Financial Snapshots (10% online Homestay booking commission)
    commission_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    commission_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    provider_payable_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    # Cancellation Tracking
    cancellation_reason: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    cancelled_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(mssql.DATETIME2(), "mssql"),
        nullable=True,
    )

    # Snapshots at Time of Booking
    nightly_rate_snapshot: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    cancellation_policy_snapshot: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    free_cancellation_hours_snapshot: Mapped[int] = mapped_column(
        Integer,
        default=24,
        nullable=False,
    )

    # Timestamps
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
    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="bookings",
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )

    canceller: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[cancelled_by],
    )
