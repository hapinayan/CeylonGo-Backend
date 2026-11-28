from datetime import datetime
from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Unicode, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase


class AdminInvitation(MVPBase):
    __tablename__ = "admin_invitations"

    __table_args__ = (
        CheckConstraint("role IN ('admin', 'super_admin')", name="ck_admin_invitations_role"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked', 'expired')",
            name="ck_admin_invitations_status",
        ),
        Index("ix_admin_invitations_email_status", "email", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(Unicode(254), nullable=False)
    full_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    department: Mapped[str | None] = mapped_column(Unicode(100), nullable=True)
    token_hash: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    invitation_note: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)
    invited_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    invited_by_email: Mapped[str | None] = mapped_column(Unicode(254), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
