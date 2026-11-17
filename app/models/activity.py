
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class Activity(Base):
    __tablename__ = "activities"

    __table_args__ = (
        CheckConstraint(
            "category IN ('cultural', 'adventure', 'local', 'community')",
            name="CK_activities_category",
        ),
        CheckConstraint(
            "pricing_type IN ('per_person', 'per_group')",
            name="CK_activities_pricing",
        ),
        CheckConstraint(
            "price >= 0",
            name="CK_activities_price",
        ),
        CheckConstraint(
            "duration_minutes > 0",
            name="CK_activities_duration",
        ),
        CheckConstraint(
            "minimum_age BETWEEN 0 AND 120",
            name="CK_activities_age",
        ),
        CheckConstraint(
            "approval_status IN "
            "('draft', 'pending', 'approved', 'rejected', 'suspended')",
            name="CK_activities_status",
        ),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="CK_activities_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="CK_activities_longitude",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    provider_profile_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        Unicode(150),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Unicode(4000),
        nullable=False,
    )

    category: Mapped[str] = mapped_column(
        Unicode(30),
        nullable=False,
        index=True,
    )

    district: Mapped[str] = mapped_column(
        Unicode(100),
        nullable=False,
    )

    city: Mapped[str] = mapped_column(
        Unicode(100),
        nullable=False,
    )

    meeting_point: Mapped[str] = mapped_column(
        Unicode(500),
        nullable=False,
    )

    latitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )

    longitude: Mapped[Decimal | None] = mapped_column(
        Numeric(9, 6),
        nullable=True,
    )

    duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    itinerary: Mapped[str | None] = mapped_column(
        mssql.NVARCHAR(length="max"),
        nullable=True,
    )

    requirements: Mapped[str | None] = mapped_column(
        Unicode(2000),
        nullable=True,
    )

    safety_rules: Mapped[str | None] = mapped_column(
        Unicode(2000),
        nullable=True,
    )

    required_equipment: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    minimum_age: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    pricing_type: Mapped[str] = mapped_column(
        Unicode(20),
        nullable=False,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        Unicode(3),
        default="LKR",
        nullable=False,
    )

    approval_status: Mapped[str] = mapped_column(
        Unicode(20),
        default="draft",
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        mssql.DATETIME2(),
        nullable=True,
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        Unicode(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        mssql.DATETIME2(),
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        mssql.DATETIME2(),
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    provider_profile = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
    )

    reviewer = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )
