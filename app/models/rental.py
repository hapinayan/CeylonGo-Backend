from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import Base, User


class RentalLocation(Base):
    __tablename__ = "rental_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )
    location_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    district: Mapped[str] = mapped_column(Unicode(100), nullable=False, index=True)
    city: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    address: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalRatePlan(Base):
    __tablename__ = "rental_rate_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    min_days: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    security_deposit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalVehicleImage(Base):
    __tablename__ = "rental_vehicle_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    vehicle: Mapped["RentalVehicle"] = relationship(
        "RentalVehicle",
        backref=backref("images", cascade="all, delete-orphan", lazy="select"),
    )


class RentalVehicleDocument(Base):
    __tablename__ = "rental_vehicle_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    document_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalAvailabilityBlock(Base):
    __tablename__ = "rental_availability_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    block_type: Mapped[str] = mapped_column(Unicode(30), default="maintenance", nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalEligibilityCheck(Base):
    __tablename__ = "rental_eligibility_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    driver_license_number: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    license_country: Mapped[str] = mapped_column(Unicode(100), default="Sri Lanka", nullable=False)
    license_expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    license_image_front_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    license_image_back_url: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    verified_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalInspection(Base):
    __tablename__ = "rental_inspections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inspection_type: Mapped[str] = mapped_column(Unicode(20), nullable=False)
    inspected_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    odometer_reading: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    fuel_level: Mapped[str | None] = mapped_column(Unicode(20), nullable=True)
    condition_notes: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    signature_url: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    inspected_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalConditionEvidence(Base):
    __tablename__ = "rental_condition_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(Unicode(20), default="photo", nullable=False)
    media_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )


class RentalIncident(Base):
    __tablename__ = "rental_incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reported_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    incident_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    description: Mapped[str] = mapped_column(Unicode(2000), nullable=False)
    estimated_damage_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(Unicode(20), default="reported", nullable=False)
    resolution_notes: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RentalDeposit(Base):
    __tablename__ = "rental_deposits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    status: Mapped[str] = mapped_column(Unicode(20), default="held", nullable=False)
    deducted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
