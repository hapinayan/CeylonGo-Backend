from datetime import datetime
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
        CheckConstraint(
            "helmet_count IS NULL OR helmet_count >= 0",
            name="CK_rental_vehicles_helmet_count",
        ),
        CheckConstraint(
            "security_deposit IS NULL OR security_deposit >= 0",
            name="CK_rental_vehicles_security_deposit",
        ),
        CheckConstraint(
            "fuel_policy IS NULL OR fuel_policy IN ('same_to_same', 'full_to_full', 'not_applicable')",
            name="CK_rental_vehicles_fuel_policy",
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

    # Phase 6B additions (AR-03, AR-04, AR-05)
    helmet_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    helmets_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    helmet_sizes: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    security_deposit: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    fuel_policy: Mapped[str | None] = mapped_column(Unicode(30), nullable=True)

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

    provider = relationship("ProviderProfile", foreign_keys=[provider_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    location = relationship("RentalLocation", back_populates="vehicles")
    rate_plans = relationship("RentalRatePlan", back_populates="vehicle", cascade="all, delete-orphan")
    images = relationship("RentalVehicleImage", back_populates="vehicle", cascade="all, delete-orphan")
    documents = relationship("RentalVehicleDocument", back_populates="vehicle", cascade="all, delete-orphan")
    availability_blocks = relationship("RentalAvailabilityBlock", back_populates="vehicle", cascade="all, delete-orphan")
