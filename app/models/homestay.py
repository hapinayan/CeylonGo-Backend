
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Unicode,
    func,
)
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User


class Homestay(Base):
    __tablename__ = "homestays"

    __table_args__ = (
        CheckConstraint(
            "price_per_night >= 0",
            name="CK_homestays_price",
        ),
        CheckConstraint(
            "max_guests > 0",
            name="CK_homestays_guests",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    owner_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )

    provider_profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("provider_profiles.id"),
        nullable=True,
        index=True,
    )

    owner: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[owner_id],
    )

    provider_profile: Mapped["ProviderProfile | None"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
        backref=backref("homestays", lazy="select"),
    )

    @property
    def provider_business_name(self) -> str | None:
        if self.provider_profile:
            return self.provider_profile.business_name
        return None

    name: Mapped[str] = mapped_column(
        Unicode(150),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Unicode(None),
        nullable=True,
    )

    location: Mapped[str] = mapped_column(
        Unicode(255),
        nullable=False,
    )

    price_per_night: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    max_guests: Mapped[int] = mapped_column(
        Integer,
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

    images: Mapped[list["HomestayImage"]] = relationship(
        "HomestayImage",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    amenities: Mapped[list["HomestayAmenity"]] = relationship(
        "HomestayAmenity",
        back_populates="homestay",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class HomestayImage(Base):
    __tablename__ = "homestay_images"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    image_url: Mapped[str] = mapped_column(
        Unicode(1000),
        nullable=False,
    )

    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="images",
    )


class HomestayAmenity(Base):
    __tablename__ = "homestay_amenities"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    homestay_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("homestays.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        Unicode(100),
        nullable=False,
    )

    homestay: Mapped["Homestay"] = relationship(
        "Homestay",
        back_populates="amenities",
    )
