from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Time,
    Unicode,
    UniqueConstraint,
    func,
)
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
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        backref=backref("business_images", cascade="all, delete-orphan", lazy="select"),
    )

    # Backward-compatible alias for existing callers
    @property
    def is_cover(self) -> bool:
        return self.is_primary

    @is_cover.setter
    def is_cover(self, value: bool) -> None:
        self.is_primary = value


class ArtisanProductImage(Base):
    __tablename__ = "artisan_product_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    showcase_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("showcase_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    image_url: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    caption: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    showcase_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("showcase_items.id"), nullable=False, index=True
    )
    artisan_listing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("artisan_listings.id"), nullable=False, index=True
    )
    traveler_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Unicode(3), default="LKR", nullable=False)
    reservation_status: Mapped[str] = mapped_column(
        Unicode(30), default="pending", nullable=False, index=True
    )
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    special_notes: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    artisan_listing: Mapped["ArtisanListing"] = relationship(
        "ArtisanListing",
        foreign_keys=[artisan_listing_id],
        backref=backref("reservations", lazy="select"),
    )
    showcase_item: Mapped["ShowcaseItem"] = relationship(
        "ShowcaseItem",
        foreign_keys=[showcase_item_id],
        backref=backref("reservations", lazy="select"),
    )
    traveler: Mapped["User"] = relationship(
        "User",
        foreign_keys=[traveler_id],
        backref=backref("artisan_reservations", lazy="select"),
    )
    canceller: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[cancelled_by],
    )

    # Backward-compatible property aliases
    @property
    def user(self) -> User:
        return self.traveler

    @property
    def user_id(self) -> int:
        return self.traveler_id

    @property
    def status(self) -> str:
        return self.reservation_status

    @status.setter
    def status(self, value: str) -> None:
        self.reservation_status = value

    @property
    def total_amount(self) -> Decimal:
        return self.total_price

    @total_amount.setter
    def total_amount(self, value: Decimal) -> None:
        self.total_price = value

    @property
    def reservation_date(self) -> date | None:
        return self.pickup_date

    @reservation_date.setter
    def reservation_date(self, value: date | None) -> None:
        self.pickup_date = value

    @property
    def item_title(self) -> str | None:
        return self.showcase_item.title if self.showcase_item else None

    @property
    def item_image_url(self) -> str | None:
        return self.showcase_item.image_url if self.showcase_item else None

    @property
    def listing_title(self) -> str | None:
        return self.artisan_listing.title if self.artisan_listing else None

    @property
    def provider_business_name(self) -> str | None:
        if self.artisan_listing and self.artisan_listing.provider_profile:
            return self.artisan_listing.provider_profile.business_name
        return None

    @property
    def traveler_name(self) -> str | None:
        return self.traveler.full_name if self.traveler else None

    @property
    def traveler_email(self) -> str | None:
        return self.traveler.email if self.traveler else None


class ArtisanBusinessHours(Base):
    __tablename__ = "artisan_business_hours"
    __table_args__ = (
        UniqueConstraint(
            "artisan_listing_id", "day_of_week", name="UQ_artisan_business_hours_day"
        ),
    )

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

    @property
    def day_name(self) -> str:
        DAY_NAMES = {
            0: "Sunday",
            1: "Monday",
            2: "Tuesday",
            3: "Wednesday",
            4: "Thursday",
            5: "Friday",
            6: "Saturday",
        }
        return DAY_NAMES.get(self.day_of_week, f"Day {self.day_of_week}")
