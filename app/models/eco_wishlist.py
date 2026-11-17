"""
SQLAlchemy model for Module 04 — Traveler Eco-Tourism Wishlist / Favorites.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.eco_experience import EcoExperience
from app.models.user import Base, User


class EcoWishlist(Base):
    __tablename__ = "eco_wishlists"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "eco_experience_id",
            name="UQ_eco_wishlists_user_experience",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    eco_experience_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("eco_experiences.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    eco_experience: Mapped["EcoExperience"] = relationship(
        "EcoExperience",
        foreign_keys=[eco_experience_id],
        lazy="selectin",
    )
