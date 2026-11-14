from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.user import Base, User


class ProviderProfile(Base):
    __tablename__ = "provider_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True
    )

    business_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    provider_type: Mapped[str] = mapped_column(
        Unicode(30), nullable=False, index=True
    )

    phone: Mapped[str] = mapped_column(Unicode(25), nullable=False)

    district: Mapped[str] = mapped_column(Unicode(100), nullable=False)

    description: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    approval_status: Mapped[str] = mapped_column(
        Unicode(20), default="pending", nullable=False, index=True
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        backref=backref("provider_profile", uselist=False),
    )

    reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )

