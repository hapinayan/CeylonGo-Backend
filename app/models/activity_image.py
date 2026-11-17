
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Unicode,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.user import Base


class ActivityImage(Base):
    __tablename__ = "activity_images"

    __table_args__ = (
        CheckConstraint(
            "display_order >= 0",
            name="CK_activity_images_order",
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

    image_url: Mapped[str] = mapped_column(
        Unicode(1000),
        nullable=False,
    )

    alt_text: Mapped[str | None] = mapped_column(
        Unicode(255),
        nullable=True,
    )

    display_order: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    is_cover: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    activity = relationship(
        "Activity",
        backref="images",
    )
