from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Time, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.artisan_listing import ArtisanListing
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User


class ArtisanBusinessImage(Base):
    __tablename__ = "artisan_business_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    is_cover: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        backref=backref("business_images", cascade="all, delete-orphan", lazy="select"),
    )


class ArtisanProductImage(Base):
    __tablename__ = "artisan_product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    showcase_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("showcase_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    showcase_item: Mapped["ShowcaseItem"] = relationship(
        "ShowcaseItem",
        backref=backref("product_images", cascade="all, delete-orphan", lazy="select"),
    )


class ArtisanReservation(Base):
    __tablename__ = "artisan_reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id"), nullable=False, index=True
    )
    showcase_item_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("showcase_items.id"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    reservation_code: Mapped[str] = mapped_column(
        Unicode(36), unique=True, nullable=False
    )
    reservation_type: Mapped[str] = mapped_column(Unicode(30), nullable=False)
    reservation_date: Mapped[date] = mapped_column(Date, nullable=False)
    time_slot: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(10), default="LKR", nullable=False)
    status: Mapped[str] = mapped_column(
        Unicode(20), default="pending", nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    artisan_listing: Mapped["ArtisanListing"] = relationship("ArtisanListing")
    showcase_item: Mapped["ShowcaseItem | None"] = relationship("ShowcaseItem")
    user: Mapped["User"] = relationship("User")


class ArtisanBusinessHours(Base):
    __tablename__ = "artisan_business_hours"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    open_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    close_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        backref=backref("business_hours", cascade="all, delete-orphan", lazy="select"),
    )
