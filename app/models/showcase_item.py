from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.artisan_listing import ArtisanListing
from app.models.user import Base


class ShowcaseItem(Base):
    __tablename__ = "showcase_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    item_type: Mapped[str] = mapped_column(
        Unicode(30), default="product", nullable=False, index=True
    )

    description: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)

    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    currency: Mapped[str] = mapped_column(Unicode(10), default="LKR", nullable=False)

    image_url: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    materials: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)

    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    stock_quantity: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    sku: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)

    approval_status: Mapped[str] = mapped_column(
        Unicode(20), default="approved", nullable=False, index=True
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

    # Relationship back to ArtisanListing
    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        foreign_keys=[artisan_listing_id],
        backref=backref("showcase_items", lazy="select"),
    )

    @property
    def listing_title(self) -> str | None:
        if self.artisan_listing:
            return self.artisan_listing.title
        return None
