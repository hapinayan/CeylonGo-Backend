from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Integer
from sqlalchemy import Unicode, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "credential_version >= 0", name="CK_users_credential_version_nonnegative"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    full_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    email: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False)

    password_hash: Mapped[str | None] = mapped_column(Unicode(255), nullable=True)

    credential_version: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), nullable=False
    )

    role: Mapped[str] = mapped_column(Unicode(30), default="traveler", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auth_version: Mapped[int] = mapped_column(
        BigInteger, server_default=text("0"), nullable=False
    )

    google_sub: Mapped[str | None] = mapped_column(
        Unicode(255), unique=True, nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
