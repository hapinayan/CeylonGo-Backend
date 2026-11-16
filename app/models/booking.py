
from datetime import date, datetime
from decimal import Decimal

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
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


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

    status: Mapped[str] = mapped_column(
        Unicode(30),
        nullable=False,
        default="confirmed",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
