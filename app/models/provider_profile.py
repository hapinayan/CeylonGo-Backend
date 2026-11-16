from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Unicode, func
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.models.user import Base, User


class ProviderProfile(Base):
    __tablename__ = "provider_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True
    )

    # Business Details
    business_name: Mapped[str] = mapped_column(Unicode(150), nullable=False)

    provider_type: Mapped[str] = mapped_column(
        Unicode(30), nullable=False, index=True
    )

    phone: Mapped[str] = mapped_column(Unicode(25), nullable=False)

    secondary_phone: Mapped[str | None] = mapped_column(
        Unicode(25), nullable=True
    )

    # Legacy district column retained for backward compatibility
    district: Mapped[str] = mapped_column(Unicode(100), nullable=False)

    description: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)

    business_registration_number: Mapped[str | None] = mapped_column(
        Unicode(50), nullable=True
    )

    # Personal Details
    identity_document_type: Mapped[str] = mapped_column(
        Unicode(20), nullable=False, default="nic"
    )

    nic_number: Mapped[str | None] = mapped_column(
        Unicode(20), nullable=True, index=True
    )

    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)

    gender: Mapped[str | None] = mapped_column(Unicode(20), nullable=True)

    nationality: Mapped[str | None] = mapped_column(Unicode(50), nullable=True)

    # Permanent Address
    permanent_province: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    permanent_district: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    permanent_city: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    permanent_landmark: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    permanent_postal_code: Mapped[str | None] = mapped_column(
        Unicode(20), nullable=True
    )

    # Business Address
    business_province: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    business_district: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    business_city: Mapped[str | None] = mapped_column(
        Unicode(100), nullable=True
    )
    business_landmark: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    business_postal_code: Mapped[str | None] = mapped_column(
        Unicode(20), nullable=True
    )

    # Identity Verification File References (Stored as secure file paths/references)
    selfie_image_path: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    nic_front_image_path: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    nic_back_image_path: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )

    # Approval Workflow
    approval_status: Mapped[str] = mapped_column(
        Unicode(20), default="pending", nullable=False, index=True
    )

    reviewed_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    rejection_reason: Mapped[str | None] = mapped_column(Unicode(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
        backref=backref("provider_profile", uselist=False),
    )

    reviewer: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reviewed_by],
    )
