from datetime import date, datetime, timedelta
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import Base, User


class RentalAgeRule(Base):
    __tablename__ = "rental_age_rules"

    __table_args__ = (
        CheckConstraint("vehicle_type IN ('bike', 'bicycle', 'tuk_tuk')",
                        name="CK_rental_age_rules_vehicle_type"),
        CheckConstraint("minimum_age BETWEEN 0 AND 120", name="CK_rental_age_rules_minimum_age"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_type: Mapped[str] = mapped_column(Unicode(20), nullable=False, unique=True)
    minimum_age: Mapped[int] = mapped_column(Integer, nullable=False)


class RentalLocation(Base):
    __tablename__ = "rental_locations"

    __table_args__ = (
        CheckConstraint(
            "(latitude IS NULL AND longitude IS NULL) OR (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0)",
            name="CK_rental_locations_coords",
        ),
    )

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

    provider = relationship("ProviderProfile", foreign_keys=[provider_id])
    vehicles = relationship("RentalVehicle", back_populates="location")


class RentalRatePlan(Base):
    __tablename__ = "rental_rate_plans"

    __table_args__ = (
        CheckConstraint(
            "duration_type IN ('hourly', 'daily', 'weekly', 'monthly')",
            name="CK_rental_rate_plans_type",
        ),
        CheckConstraint(
            "rate >= 0",
            name="CK_rental_rate_plans_rate",
        ),
        CheckConstraint(
            "extra_km_charge IS NULL OR extra_km_charge >= 0",
            name="CK_rental_rate_plans_extra_km",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_name: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    duration_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    included_km_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra_km_charge: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    vehicle: Mapped["RentalVehicle"] = relationship("RentalVehicle", back_populates="rate_plans")


class RentalVehicleImage(Base):
    __tablename__ = "rental_vehicle_images"

    __table_args__ = (
        CheckConstraint(
            "display_order >= 0",
            name="CK_rental_vehicle_images_order",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    vehicle: Mapped["RentalVehicle"] = relationship("RentalVehicle", back_populates="images")


class RentalVehicleDocument(Base):
    __tablename__ = "rental_vehicle_documents"

    __table_args__ = (
        CheckConstraint(
            "document_type IN ('revenue_license', 'insurance_certificate', 'vcr', 'fitness_certificate', 'other')",
            name="CK_rental_vehicle_documents_type",
        ),
        CheckConstraint(
            "coverage_amount IS NULL OR coverage_amount >= 0",
            name="CK_rental_vehicle_docs_coverage",
        ),
        CheckConstraint(
            "deductible_amount IS NULL OR deductible_amount >= 0",
            name="CK_rental_vehicle_docs_deductible",
        ),
        CheckConstraint(
            "insurance_effective_date IS NULL OR "
            "(expiry_date IS NOT NULL AND insurance_effective_date <= expiry_date)",
            name="CK_rental_vehicle_docs_insurance_dates",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    document_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    file_reference: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 6B additions (AR-12: Insurance info)
    coverage_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    deductible_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    insurance_provider: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)
    insurance_policy_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    insurance_effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    insurance_exclusions: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    vehicle: Mapped["RentalVehicle"] = relationship("RentalVehicle", back_populates="documents")


class RentalAvailabilityBlock(Base):
    __tablename__ = "rental_availability_blocks"

    __table_args__ = (
        CheckConstraint(
            "end_datetime > start_datetime",
            name="CK_rental_availability_blocks_dates",
        ),
        CheckConstraint(
            "block_type IN ('maintenance', 'service', 'accident_repair', 'owner_unavailable', 'administrative')",
            name="CK_rental_availability_blocks_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_vehicles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    block_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    vehicle: Mapped["RentalVehicle"] = relationship("RentalVehicle", back_populates="availability_blocks")

    @property
    def start_date(self) -> date:
        return self.start_datetime.date()

    @property
    def end_date(self) -> date:
        return (self.end_datetime - timedelta(microseconds=1)).date()


class RentalEligibilityCheck(Base):
    __tablename__ = "rental_eligibility_checks"

    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('pending', 'approved', 'rejected', 'expired')",
            name="CK_rental_eligibility_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    driver_license_number: Mapped[str] = mapped_column(Unicode(100), nullable=False)
    license_country: Mapped[str] = mapped_column(Unicode(100), default="Sri Lanka", nullable=False)
    license_expiry_date: Mapped[date] = mapped_column(Date, nullable=False)
    license_front_image: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    license_back_image: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    idp_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)

    # Phase 6B additions (AR-02: DOB, AR-11: Foreign Permits)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    recognition_permit_number: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    recognition_permit_document_url: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    verification_status: Mapped[str] = mapped_column(Unicode(30), default="pending", nullable=False)
    reviewed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])


class RentalInspection(Base):
    __tablename__ = "rental_inspections"

    __table_args__ = (
        CheckConstraint(
            "inspection_type IN ('pickup', 'return')",
            name="CK_rental_inspections_type",
        ),
        CheckConstraint(
            "odometer_reading >= 0",
            name="CK_rental_inspections_odometer",
        ),
        CheckConstraint(
            "fuel_level_percent BETWEEN 0 AND 100",
            name="CK_rental_inspections_fuel",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    inspection_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    odometer_reading: Mapped[int] = mapped_column(Integer, nullable=False)
    fuel_level_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    cleanliness_status: Mapped[str] = mapped_column(Unicode(30), default="clean", nullable=False)
    inspected_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    customer_signature: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    notes: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    # Phase 6B additions (AR-06: Digital Acknowledgement Metadata)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledgement_ip: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    booking: Mapped["RentalBooking"] = relationship("RentalBooking", back_populates="inspections")
    inspector = relationship("User", foreign_keys=[inspected_by])
    condition_evidence = relationship("RentalConditionEvidence", back_populates="inspection", cascade="all, delete-orphan")


class RentalConditionEvidence(Base):
    __tablename__ = "rental_condition_evidence"

    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('photo', 'video', 'document')",
            name="CK_rental_evidence_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inspection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    file_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    area_of_vehicle: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    inspection: Mapped["RentalInspection"] = relationship("RentalInspection", back_populates="condition_evidence")


class RentalIncident(Base):
    __tablename__ = "rental_incidents"

    __table_args__ = (
        CheckConstraint(
            "incident_type IN ('scratch', 'dent', 'accident', 'mechanical_failure', 'theft', 'late_return', 'traffic_fine')",
            name="CK_rental_incidents_type",
        ),
        CheckConstraint(
            "estimated_damage_cost >= 0",
            name="CK_rental_incidents_est_cost",
        ),
        CheckConstraint(
            "actual_repair_cost IS NULL OR actual_repair_cost >= 0",
            name="CK_rental_incidents_act_cost",
        ),
        CheckConstraint(
            "resolution_status IN ('reported', 'under_investigation', 'insurance_claimed', 'customer_billed', 'resolved', 'dismissed')",
            name="CK_rental_incidents_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_type: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    incident_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description: Mapped[str] = mapped_column(Unicode(2000), nullable=False)
    estimated_damage_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    actual_repair_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    police_report_reference: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    resolution_status: Mapped[str] = mapped_column(Unicode(30), default="reported", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), onupdate=func.sysutcdatetime(), nullable=False
    )

    booking: Mapped["RentalBooking"] = relationship("RentalBooking", back_populates="incidents")


class RentalDeposit(Base):
    __tablename__ = "rental_deposits"

    __table_args__ = (
        CheckConstraint(
            "deposit_amount >= 0",
            name="CK_rental_deposits_amount",
        ),
        CheckConstraint(
            "deducted_amount >= 0",
            name="CK_rental_deposits_deducted",
        ),
        CheckConstraint(
            "refunded_amount >= 0",
            name="CK_rental_deposits_refunded",
        ),
        CheckConstraint(
            "deposit_status IN ('held', 'partially_refunded', 'fully_refunded', 'forfeited', 'deducted')",
            name="CK_rental_deposits_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("rental_bookings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    deposit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    deposit_status: Mapped[str] = mapped_column(Unicode(30), default="held", nullable=False)
    deducted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    deduction_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    held_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    booking: Mapped["RentalBooking"] = relationship("RentalBooking", back_populates="deposits")
