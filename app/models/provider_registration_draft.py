from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, Unicode, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.user import Base


class ProviderRegistrationDraft(Base):
    """
    Temporary, editable storage for in-progress Service Provider registration drafts.

    Security & Architecture Rules:
    1. Tied to a unique, verified email from Phase 2B OTP verification.
    2. Incomplete drafts allow nullable fields until final submission.
    3. Never stores raw OTPs or authorization tokens.
    4. Identity files are stored as secure, non-public storage references.
    5. Primary phone and secondary phone are validated for Sri Lankan numbering.
    """

    __tablename__ = "provider_registration_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    email: Mapped[str] = mapped_column(
        Unicode(255), unique=True, nullable=False, index=True
    )

    full_name: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)

    primary_phone: Mapped[str | None] = mapped_column(Unicode(25), nullable=True)

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

    # Business Details
    business_name: Mapped[str | None] = mapped_column(Unicode(150), nullable=True)

    provider_category: Mapped[str | None] = mapped_column(
        Unicode(30), nullable=True, index=True
    )

    secondary_phone: Mapped[str | None] = mapped_column(
        Unicode(25), nullable=True
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

    business_registration_number: Mapped[str | None] = mapped_column(
        Unicode(50), nullable=True
    )

    business_description: Mapped[str | None] = mapped_column(
        Unicode(1000), nullable=True
    )

    # Identity Documents References (never stores raw images or public paths)
    selfie_file_reference: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    nic_front_file_reference: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )
    nic_back_file_reference: Mapped[str | None] = mapped_column(
        Unicode(255), nullable=True
    )

    # Step progress: 'personal', 'business', 'identity', 'review'
    registration_step: Mapped[str] = mapped_column(
        Unicode(50), default="personal", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.sysutcdatetime(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.sysutcdatetime(),
        onupdate=func.sysutcdatetime(),
        nullable=False,
    )

    # Property aliases for seamless field access
    @property
    def phone(self) -> str | None:
        return self.primary_phone

    @phone.setter
    def phone(self, value: str | None) -> None:
        self.primary_phone = value

    @property
    def provider_type(self) -> str | None:
        return self.provider_category

    @provider_type.setter
    def provider_type(self, value: str | None) -> None:
        self.provider_category = value

    @property
    def description(self) -> str | None:
        return self.business_description

    @description.setter
    def description(self, value: str | None) -> None:
        self.business_description = value
