from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index
from sqlalchemy import LargeBinary, String, Unicode, UnicodeText
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase, PK_TYPE


class TravelerActivityEvent(MVPBase):
    __tablename__ = "traveler_activity_events"
    __table_args__ = (
        CheckConstraint(
            "(event_type = 'view' AND listing_id IS NOT NULL "
            "AND category IS NOT NULL AND search_query IS NULL) OR "
            "(event_type = 'search' AND listing_id IS NULL)",
            name="ck_traveler_activity_event_shape",
        ),
        Index("ix_traveler_activity_recent", "traveler_id", "created_at", "id"),
        Index(
            "ix_traveler_activity_dedupe", "traveler_id", "fingerprint", "created_at"
        ),
        Index("ix_traveler_activity_expiry", "created_at"),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True)
    traveler_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(6))
    listing_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("listings.id")
    )
    category: Mapped[str | None] = mapped_column(String(16))
    search_query: Mapped[str | None] = mapped_column(Unicode(120))
    district: Mapped[str | None] = mapped_column(Unicode(80))
    destination: Mapped[str | None] = mapped_column(Unicode(80))
    filters: Mapped[str | None] = mapped_column(UnicodeText)
    fingerprint: Mapped[bytes] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(DateTime)
