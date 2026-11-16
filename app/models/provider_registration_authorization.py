from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Unicode, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ProviderRegistrationAuthorization(Base):
    """
    Short-lived, single-use authorization record to gate multi-step registration
    after successful email OTP verification.

    Security Rules:
    1. Cryptographically secure random tokens are issued to the client; only the
       SHA-256 token_hash is stored in the database.
    2. Strictly bound to the verified email and authorized purpose.
    3. Enforces an expiration window (expires_at).
    4. One-time consumption via is_consumed and consumed_at.
    5. Supports immediate revocation via is_revoked and revoked_at if superseded.
    """

    __tablename__ = "provider_registration_authorizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email: Mapped[str] = mapped_column(Unicode(255), nullable=False, index=True)

    token_hash: Mapped[str] = mapped_column(
        Unicode(64), unique=True, nullable=False, index=True
    )

    purpose: Mapped[str] = mapped_column(Unicode(50), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    is_consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
