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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class RentalBooking(Base):
    __tablename__ = "rental_bookings"

    __table_args__ = (
        CheckConstraint(
            "end_date >= start_date",
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

    start_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    end_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    total_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    status: Mapped[str] = mapped_column(
        Unicode(30), default="pending", nullable=False
    )

    pickup_location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("rental_locations.id"), nullable=True
    )
    return_location_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("rental_locations.id"), nullable=True
    )
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    payment_method: Mapped[str] = mapped_column(Unicode(30), default="pay_at_location", nullable=False)
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="unpaid", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    cancelled_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    daily_rate_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    cancellation_policy_snapshot: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    free_cancellation_hours_snapshot: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    vehicle = relationship("RentalVehicle", backref="bookings")
    user = relationship("User", foreign_keys=[user_id])

    @property
    def pickup_at(self) -> datetime | None:
        if self.start_date:
            return datetime.combine(self.start_date, datetime.min.time())
        return None

    @pickup_at.setter
    def pickup_at(self, value):
        if isinstance(value, datetime):
            self.start_date = value.date()
        elif isinstance(value, date):
            self.start_date = value

    @property
    def return_at(self) -> datetime | None:
        if self.end_date:
            return datetime.combine(self.end_date, datetime.min.time())
        return None

    @return_at.setter
    def return_at(self, value):
        if isinstance(value, datetime):
            self.end_date = value.date()
        elif isinstance(value, date):
            self.end_date = value
