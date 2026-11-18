from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.provider_profile import ProviderProfile
from app.models.user import Base


class ArtisanListing(Base):
    __tablename__ = "artisan_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider_profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("provider_profiles.id"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    craft_category: Mapped[str] = mapped_column(
        Unicode(50), nullable=False, index=True
    )

    description: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)

    district: Mapped[str] = mapped_column(Unicode(100), nullable=False, index=True)

    city: Mapped[str] = mapped_column(Unicode(100), nullable=False)

    address: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)

    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)

    phone: Mapped[str | None] = mapped_column(Unicode(25), nullable=True)

    email: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)

    website_url: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)

    opening_hours: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)

    cover_image_url: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    approval_status: Mapped[str] = mapped_column(
        Unicode(20), default="approved", nullable=False, index=True
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    verification_status: Mapped[str] = mapped_column(
        Unicode(20), default="unverified", nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    # Relationships
    provider_profile: Mapped["ProviderProfile"] = relationship(
        "ProviderProfile",
        foreign_keys=[provider_profile_id],
        backref=backref("artisan_listings", lazy="select"),
    )

    @property
    def provider_business_name(self) -> str | None:
        if self.provider_profile:
            return self.provider_profile.business_name
        return None
