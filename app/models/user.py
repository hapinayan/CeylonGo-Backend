from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Unicode, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    full_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    email: Mapped[str] = mapped_column(Unicode(255), unique=True, nullable=False)

    password_hash: Mapped[str] = mapped_column(Unicode(255), nullable=False)

    role: Mapped[str] = mapped_column(Unicode(30), default="traveler", nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
