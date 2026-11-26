from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase


class TravelerSettings(MVPBase):
    __tablename__ = "traveler_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    country: Mapped[str | None] = mapped_column(Unicode(80))
    avatar: Mapped[str | None] = mapped_column(Unicode(40))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    google_only: Mapped[bool] = mapped_column(Boolean, default=False)
    preferences: Mapped[str] = mapped_column(UnicodeText, default="{}")
    booking_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    payment_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    community_activity: Mapped[bool] = mapped_column(Boolean, default=True)
    promotional_updates: Mapped[bool] = mapped_column(Boolean, default=False)


class Notification(MVPBase):
    __tablename__ = "traveler_notifications"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    notification_type: Mapped[str] = mapped_column(Unicode(50))
    category: Mapped[str] = mapped_column(Unicode(20))
    title: Mapped[str] = mapped_column(Unicode(120))
    message: Mapped[str] = mapped_column(Unicode(250))
    related_entity_type: Mapped[str] = mapped_column(Unicode(20))
    related_entity_id: Mapped[int] = mapped_column(BigInteger)
    source_comment_id: Mapped[int | None] = mapped_column(BigInteger)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    event_key: Mapped[str] = mapped_column(Unicode(120), unique=True)


