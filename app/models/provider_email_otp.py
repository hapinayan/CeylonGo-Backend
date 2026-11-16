from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Unicode, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ProviderEmailOtp(Base):
    """
    Dedicated database model for Service Provider Email OTP Verification.

    Security & Operational Rules:
    1. Raw OTPs are NEVER stored; only secure hashes (otp_hash) are persisted.
    2. Enforces expiration via expires_at.
    3. Tracks verification attempts (attempts_count) against max_attempts to prevent brute force.
    4. Enforces single-use verification via is_used and used_at.
    5. Categorizes verification via purpose ('registration', 'resubmission', 'password_setup').
    """

    __tablename__ = "provider_email_otps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email: Mapped[str] = mapped_column(Unicode(255), nullable=False, index=True)

    otp_hash: Mapped[str] = mapped_column(Unicode(255), nullable=False)

    purpose: Mapped[str] = mapped_column(Unicode(50), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    attempts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
