
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class HomestayPromotion(Base):
    __tablename__ = "homestay_promotions"

    __table_args__ = (
        CheckConstraint(
            "discount_percent > 0 AND discount_percent <= 100",
            name="CK_homestay_promotions_discount",
        ),
        CheckConstraint(
            "end_date >= start_date",
            name="CK_homestay_promotions_dates",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        Unicode(150),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Unicode(1000),
        nullable=True,
    )

    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2),
        nullable=False,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    end_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    homestay = relationship(
        "Homestay",
        foreign_keys=[homestay_id],
    )
