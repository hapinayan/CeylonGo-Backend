
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class RentalBooking(Base):
    __tablename__ = "rental_bookings"

    __table_args__ = (
        CheckConstraint(
            "return_at > pickup_at",
            name="CK_rental_booking_dates",
        ),
        CheckConstraint(
            "total_price >= 0",
            name="CK_rental_booking_price",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    vehicle_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("rental_vehicles.id"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    pickup_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )

    return_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False
    )

    total_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    status: Mapped[str] = mapped_column(
        Unicode(30),
        default="pending",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
