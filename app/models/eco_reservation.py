from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Unicode,
    func,
    text,
)
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base

UTC_TIMESTAMP = DateTime().with_variant(DATETIME2(), "mssql")


class EcoAvailabilitySlot(Base):
    __tablename__ = "eco_availability_slots"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="CK_eco_slot_times"),
        CheckConstraint(
            "capacity BETWEEN 1 AND 50 AND reserved_count BETWEEN 0 AND capacity",
            name="CK_eco_slot_capacity",
        ),
        Index("IX_eco_slot_schedule", "eco_experience_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    eco_experience_id: Mapped[int] = mapped_column(ForeignKey("eco_experiences.id"))
    starts_at: Mapped[datetime] = mapped_column(UTC_TIMESTAMP)
    ends_at: Mapped[datetime] = mapped_column(UTC_TIMESTAMP)
    capacity: Mapped[int]
    reserved_count: Mapped[int] = mapped_column(default=0)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTC_TIMESTAMP, server_default=func.sysutcdatetime()
    )


class EcoReservation(Base):
    __tablename__ = "eco_reservations"
    __table_args__ = (
        CheckConstraint("total_price >= 0", name="CK_eco_reservation_amount"),
        CheckConstraint("free_cancellation_hours >= 0"),
        CheckConstraint(
            "participants BETWEEN 1 AND 50 AND capacity_held BETWEEN participants AND 50",
            name="CK_eco_reservation_participants",
        ),
        CheckConstraint(
            "payment_status = 'unpaid' "
            "OR (payment_status = 'paid' AND status IN ('confirmed', 'completed')) "
            "OR (payment_status IN ('refund_pending', 'refund_rejected', 'no_refund_due', "
            "'refund_failed', 'refunded', 'partially_refunded') "
            "AND status = 'cancelled')",
            name="CK_eco_reservation_payment",
        ),
        CheckConstraint(
            "(holds_capacity = 1 AND status IN ('pending', 'confirmed', 'completed')) "
            "OR (holds_capacity = 0 AND status IN ('rejected', 'cancelled'))",
            name="CK_eco_reservation_state",
        ),
        Index(
            "UX_eco_reservation_active_traveler",
            "slot_id",
            "traveler_id",
            unique=True,
            sqlite_where=text("holds_capacity = 1"),
            mssql_where=text("holds_capacity = 1"),
        ),
        Index("IX_eco_reservation_traveler", "traveler_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("eco_availability_slots.id"))
    traveler_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    participants: Mapped[int]
    capacity_held: Mapped[int]
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(Unicode(3))
    free_cancellation_hours: Mapped[int]
    status: Mapped[str] = mapped_column(Unicode(30), default="pending")
    payment_status: Mapped[str] = mapped_column(Unicode(30), default="unpaid")
    holds_capacity: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTC_TIMESTAMP, server_default=func.sysutcdatetime()
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTC_TIMESTAMP)
    cancelled_at: Mapped[datetime | None] = mapped_column(UTC_TIMESTAMP)
    completed_at: Mapped[datetime | None] = mapped_column(UTC_TIMESTAMP)


class EcoCancellationPolicy(Base):
    """Approved listing policy version copied onto new bookings."""

    __tablename__ = "eco_cancellation_policies"
    __table_args__ = (
        CheckConstraint("late_refund_percent BETWEEN 0 AND 100",
                        name="CK_eco_policy_percent"),
    )

    eco_experience_id: Mapped[int] = mapped_column(
        ForeignKey("eco_experiences.id"), primary_key=True
    )
    late_refund_percent: Mapped[int]


class EcoCancellationSnapshot(Base):
    __tablename__ = "eco_cancellation_snapshots"
    __table_args__ = (
        CheckConstraint("late_refund_percent BETWEEN 0 AND 100",
                        name="CK_eco_policy_snapshot_percent"),
    )

    reservation_id: Mapped[int] = mapped_column(
        ForeignKey("eco_reservations.id"), primary_key=True
    )
    late_refund_percent: Mapped[int]


class EcoRefundReconciliation(Base):
    __tablename__ = "eco_refund_reconciliations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('processing', 'succeeded', 'failed', 'reconciliation_required')",
            name="CK_eco_refund_reconciliation_state",
        ),
        Index(
            "UX_eco_refund_gateway_reference", "gateway_refund_reference", unique=True,
            mssql_where=text("gateway_refund_reference IS NOT NULL"),
            sqlite_where=text("gateway_refund_reference IS NOT NULL"),
        ),
    )

    refund_id: Mapped[int] = mapped_column(ForeignKey("payment_refunds.id"), primary_key=True)
    state: Mapped[str] = mapped_column(Unicode(30))
    gateway_refund_reference: Mapped[str | None] = mapped_column(Unicode(50))


class EcoAgeLimit(Base):
    __tablename__ = "eco_age_limits"
    __table_args__ = (
        CheckConstraint("maximum_age BETWEEN 0 AND 100", name="CK_eco_maximum_age"),
    )

    eco_experience_id: Mapped[int] = mapped_column(
        ForeignKey("eco_experiences.id"), primary_key=True
    )
    maximum_age: Mapped[int]


class EcoSlotClearance(Base):
    """Verified slot-wide coverage, provisioned only by authorized operators."""

    __tablename__ = "eco_slot_clearances"
    __table_args__ = (
        CheckConstraint("kind IN ('permit', 'guide')", name="CK_eco_clearance_kind"),
    )

    slot_id: Mapped[int] = mapped_column(
        ForeignKey("eco_availability_slots.id"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Unicode(10), primary_key=True)
    verified_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    evidence_reference: Mapped[str] = mapped_column(Unicode(100))
    valid_until: Mapped[datetime] = mapped_column(UTC_TIMESTAMP)
    revoked_at: Mapped[datetime | None] = mapped_column(UTC_TIMESTAMP)
