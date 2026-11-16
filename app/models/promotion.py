from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.artisan_listing import ArtisanListing
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base


class Promotion(Base):
    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id"), nullable=False, index=True
    )

    showcase_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("showcase_items.id"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    description: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)

    promo_code: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)

    discount_percentage: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    discount_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )

    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

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
    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        foreign_keys=[artisan_listing_id],
        backref=backref("promotions", lazy="select"),
    )

    showcase_item: Mapped["ShowcaseItem | None"] = relationship(
        "ShowcaseItem",
        foreign_keys=[showcase_item_id],
        backref=backref("promotions", lazy="select"),
    )

    @property
    def listing_title(self) -> str | None:
        if self.artisan_listing:
            return self.artisan_listing.title
        return None

    @property
    def showcase_item_title(self) -> str | None:
        if self.showcase_item:
            return self.showcase_item.title
        return None
