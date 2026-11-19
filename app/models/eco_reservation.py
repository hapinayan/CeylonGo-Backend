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
            "payment_status = 'unpaid' OR (payment_status = 'paid' AND status IN ('confirmed', 'completed'))",
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
