from datetime import datetime
from typing import List

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Unicode, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


ROLE_NAME_MAP = {
    "tourist": "traveler",
    "vendor": "vendor",
    "admin": "admin",
}

API_ROLE_TO_ID = {
    "traveler": 1,
    "tourist": 1,
    "vendor": 2,
    "admin": 3,
}


class Role(Base):
    __tablename__ = "Roles"
    __table_args__ = {"schema": "dbo"}

    role_id: Mapped[int] = mapped_column("RoleId", Integer, primary_key=True)
    role_name: Mapped[str] = mapped_column("RoleName", Unicode(50), nullable=False)


class UserRole(Base):
    __tablename__ = "UserRoles"
    __table_args__ = {"schema": "dbo"}

    user_id: Mapped[int] = mapped_column(
        "UserId", Integer, ForeignKey("dbo.Users.UserId"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        "RoleId", Integer, ForeignKey("dbo.Roles.RoleId"), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="user_role_links")
    role: Mapped["Role"] = relationship("Role", lazy="joined")


class User(Base):
    __tablename__ = "Users"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(
        "UserId", Integer, primary_key=True, autoincrement=True
    )

    full_name: Mapped[str] = mapped_column(
        "DisplayName", Unicode(120), nullable=False
    )

    email: Mapped[str] = mapped_column(
        "Email", Unicode(320), unique=True, nullable=False
    )

    password_hash: Mapped[str] = mapped_column(
        "PasswordHash", Unicode(255), nullable=False
    )

    is_active: Mapped[bool] = mapped_column(
        "IsActive", Boolean, default=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        "CreatedAtUtc", DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    user_role_links: Mapped[List["UserRole"]] = relationship(
        "UserRole",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="joined",
    )

    password_reset_tokens: Mapped[List["PasswordResetToken"]] = relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def role(self) -> str:
        if self.user_role_links:
            for link in self.user_role_links:
                if link.role and link.role.role_name:
                    raw_name = link.role.role_name.strip().lower()
                    return ROLE_NAME_MAP.get(raw_name, raw_name)
        return "traveler"

    @role.setter
    def role(self, value: str) -> None:
        role_id = API_ROLE_TO_ID.get(value.lower() if value else "")
        if role_id is not None:
            if self.user_role_links:
                self.user_role_links[0].role_id = role_id
            else:
                self.user_role_links.append(UserRole(role_id=role_id))


class PasswordResetToken(Base):
    __tablename__ = "PasswordResetTokens"
    __table_args__ = {"schema": "dbo"}

    id: Mapped[int] = mapped_column(
        "TokenId", Integer, primary_key=True, autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        "UserId",
        Integer,
        ForeignKey("dbo.Users.UserId", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        "TokenHash", Unicode(64), nullable=False, unique=True, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        "ExpiresAtUtc", DateTime, nullable=False
    )
    is_used: Mapped[bool] = mapped_column(
        "IsUsed", Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        "CreatedAtUtc", DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        "UsedAtUtc", DateTime, nullable=True
    )

    user: Mapped["User"] = relationship("User", back_populates="password_reset_tokens")
