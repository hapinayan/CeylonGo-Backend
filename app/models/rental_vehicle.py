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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class RentalVehicle(Base):
    __tablename__ = "rental_vehicles"

    __table_args__ = (
        CheckConstraint(
            "vehicle_type IN ('bike', 'bicycle', 'tuk_tuk')",
            name="CK_rental_vehicle_type",
        ),
        CheckConstraint(
            "price_per_day >= 0",
            name="CK_rental_vehicle_price",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    provider_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=False,
        index=True,
    )

    vehicle_type: Mapped[str] = mapped_column(
        Unicode(30), nullable=False
    )

    vehicle_name: Mapped[str] = mapped_column(
        Unicode(150), nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    price_per_day: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    status: Mapped[str] = mapped_column(
        Unicode(30), default="available", nullable=False
    )

    model_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    license_plate: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)
    seating_capacity: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    transmission: Mapped[str] = mapped_column(Unicode(20), default="manual", nullable=False)
    fuel_type: Mapped[str] = mapped_column(Unicode(20), default="petrol", nullable=False)
    district: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    city: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    location_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("rental_locations.id"), nullable=True)

    approval_status: Mapped[str] = mapped_column(Unicode(20), default="approved", nullable=False)
    verification_status: Mapped[str] = mapped_column(Unicode(20), default="unverified", nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    free_cancellation_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)

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
