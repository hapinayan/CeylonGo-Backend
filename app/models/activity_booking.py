
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy import DateTime
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class ActivityBooking(Base):
    __tablename__ = "activity_bookings"

    __table_args__ = (
        CheckConstraint(
            "participants > 0",
            name="CK_activity_bookings_participants",
        ),
        CheckConstraint(
            "total_price >= 0",
            name="CK_activity_bookings_price",
        ),
        CheckConstraint(
            "payment_method IN ('online', 'pay_at_location')",
            name="CK_activity_bookings_method",
        ),
        CheckConstraint(
            "payment_status IN "
            "('unpaid', 'pending', 'paid', "
            "'refund_pending', 'refunded', 'failed')",
            name="CK_activity_bookings_payment",
        ),
        CheckConstraint(
            "booking_status IN "
            "('pending', 'confirmed', 'rejected', "
            "'cancelled', 'completed')",
            name="CK_activity_bookings_status",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    activity_slot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("activity_slots.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    participants: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    total_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        Unicode(3),
        default="LKR",
        nullable=False,
    )

    payment_method: Mapped[str] = mapped_column(
        Unicode(20),
        nullable=False,
    )

    payment_status: Mapped[str] = mapped_column(
        Unicode(20),
        default="unpaid",
        nullable=False,
    )

    booking_status: Mapped[str] = mapped_column(
        Unicode(30),
        default="pending",
        nullable=False,
    )

    cancellation_reason: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    activity_slot = relationship(
        "ActivitySlot",
        backref="bookings",
    )

    traveler = relationship(
        "User",
        foreign_keys=[user_id],
    )
