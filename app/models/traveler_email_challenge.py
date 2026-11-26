from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, String, Unicode
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase


class TravelerEmailChallenge(MVPBase):
    __tablename__ = "traveler_email_challenges"
    __table_args__ = (
        CheckConstraint(
            "attempts BETWEEN 0 AND 5", name="CK_traveler_email_challenges_attempts"
        ),
    )

    email: Mapped[str] = mapped_column(
        Unicode(254).with_variant(
            Unicode(254, collation="Latin1_General_100_BIN2"), "mssql"
        ),
        primary_key=True,
    )
    registration_hash: Mapped[str | None] = mapped_column(String(64))
    otp_hash: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str | None] = mapped_column(Unicode(150))
    phone: Mapped[str | None] = mapped_column(Unicode(32))
    password_hash: Mapped[str | None] = mapped_column(Unicode(512))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime().with_variant(DATETIME2(), "mssql"), nullable=False
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False)
