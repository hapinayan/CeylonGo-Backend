
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

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )
