from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
            "return_at > pickup_at",
            name="CK_rental_booking_dates",
        ),
        CheckConstraint(
            "total_price >= 0",
            name="CK_rental_booking_price",
        ),
        CheckConstraint(
            "passenger_count IS NULL OR passenger_count > 0",
            name="CK_rental_bookings_passenger_count",
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
    passenger_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    self_drive_agreement_accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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
    cancelled_by_user = relationship("User", foreign_keys=[cancelled_by])
    pickup_location = relationship("RentalLocation", foreign_keys=[pickup_location_id])
    return_location = relationship("RentalLocation", foreign_keys=[return_location_id])
    inspections = relationship("RentalInspection", back_populates="booking", cascade="all, delete-orphan")
    incidents = relationship("RentalIncident", back_populates="booking", cascade="all, delete-orphan")
    deposits = relationship("RentalDeposit", back_populates="booking", cascade="all, delete-orphan")

    @property
    def start_date(self) -> date | None:
        if self.pickup_at:
            return self.pickup_at.date() if isinstance(self.pickup_at, datetime) else self.pickup_at
        return None

    @start_date.setter
    def start_date(self, value):
        if isinstance(value, datetime):
            self.pickup_at = value
        elif isinstance(value, date):
            self.pickup_at = datetime.combine(value, datetime.min.time())

    @property
    def end_date(self) -> date | None:
        if self.return_at:
            return self.return_at.date() if isinstance(self.return_at, datetime) else self.return_at
        return None

    @end_date.setter
    def end_date(self, value):
        if isinstance(value, datetime):
            self.return_at = value
        elif isinstance(value, date):
            self.return_at = datetime.combine(value, datetime.min.time())
