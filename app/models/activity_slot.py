
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    func,
)
from sqlalchemy import DateTime
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class ActivitySlot(Base):
    __tablename__ = "activity_slots"

    __table_args__ = (
        CheckConstraint(
            "ends_at > starts_at",
            name="CK_activity_slots_time",
        ),
        CheckConstraint(
            "capacity > 0",
            name="CK_activity_slots_capacity",
        ),
        CheckConstraint(
            "reserved_count >= 0 AND reserved_count <= capacity",
            name="CK_activity_slots_reserved",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    activity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("activities.id"),
        nullable=False,
        index=True,
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        nullable=False,
    )

    ends_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        nullable=False,
    )

    capacity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    reserved_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(__import__('sqlalchemy').dialects.mssql.DATETIME2(), 'mssql'),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    activity = relationship(
        "Activity",
        backref="slots",
    )
