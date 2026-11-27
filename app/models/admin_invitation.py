from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Unicode, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class AdminInvitation(Base):
    __tablename__ = "admin_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Unicode(255), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    role: Mapped[str] = mapped_column(Unicode(50), nullable=False)
    department: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    token_hash: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Unicode(20), default="pending", nullable=False)
    invitation_note: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    invited_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invited_by_email: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
