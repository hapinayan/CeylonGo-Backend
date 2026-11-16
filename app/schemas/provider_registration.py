import re
from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

VALID_PROVIDER_TYPES = {
    "homestay",
    "tour_guide",
    "activity",
    "eco_tourism",
    "artisan",
}

PHONE_PATTERN = re.compile(r"^(?:\+94|0094|94)?(0?[1-9]\d{8})$")
NIC_OLD_PATTERN = re.compile(r"^[0-9]{9}[VX]$")
NIC_NEW_PATTERN = re.compile(r"^[0-9]{12}$")
PASSPORT_PATTERN = re.compile(r"^[A-Z0-9]{6,12}$")


def validate_sri_lankan_phone(phone_str: str) -> str:
    cleaned = re.sub(r"[\s\-]", "", phone_str.strip())
    match = PHONE_PATTERN.match(cleaned)
    if not match:
        raise ValueError(
            "Invalid Sri Lankan phone number format. Must be 10 digits starting with 0 (e.g. 0771234567) or with +94 country code."
        )
    digits = match.group(1)
    if not digits.startswith("0"):
        digits = "0" + digits
    return digits


def validate_sri_lankan_nic(nic_str: str) -> str:
    cleaned = nic_str.strip().upper()
    if not (NIC_OLD_PATTERN.match(cleaned) or NIC_NEW_PATTERN.match(cleaned)):
        raise ValueError(
            "Invalid Sri Lankan NIC format. Must be 9 digits followed by 'V'/'X' (e.g. 123456789V) or 12 digits (e.g. 200012345678)."
        )
    return cleaned


def validate_passport(passport_str: str) -> str:
    cleaned = passport_str.strip().upper()
    if not PASSPORT_PATTERN.match(cleaned):
        raise ValueError(
            "Invalid passport format. Must be 6 to 12 alphanumeric characters without spaces or symbols."
        )
    return cleaned


class PersonalDetailsRequest(BaseModel):
    full_name: Annotated[str, Field(min_length=2, max_length=150)]
    phone: str
    date_of_birth: date
    gender: Annotated[str, Field(min_length=2, max_length=20)]
    nationality: Annotated[str, Field(min_length=2, max_length=50)] = "Sri Lankan"
    identity_document_type: Literal["nic", "passport"] = "nic"
    nic_number: Annotated[
        str,
        Field(
            description="Identity document number (Sri Lankan NIC or Passport number depending on identity_document_type)"
        ),
    ]
    permanent_province: Annotated[str, Field(min_length=2, max_length=100)]
    permanent_district: Annotated[str, Field(min_length=2, max_length=100)]
    permanent_city: Annotated[str, Field(min_length=2, max_length=100)]
    permanent_landmark: Annotated[str, Field(min_length=5, max_length=255)]
    permanent_postal_code: Annotated[str, Field(min_length=3, max_length=20)]

    @field_validator("full_name")
    @classmethod
    def clean_full_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Full name must be at least 2 characters.")
        return cleaned

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return validate_sri_lankan_phone(v)

    @field_validator("date_of_birth")
    @classmethod
    def validate_dob(cls, v: date) -> date:
        today = datetime.now(UTC).date()
        if v >= today:
            raise ValueError("Date of birth must be in the past.")
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 18:
            raise ValueError("Service provider primary contact must be at least 18 years old.")
        return v

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str) -> str:
        cleaned = v.strip().title()
        if cleaned not in {"Male", "Female", "Other"}:
            raise ValueError("Gender must be one of 'Male', 'Female', or 'Other'.")
        return cleaned

    @field_validator("nationality")
    @classmethod
    def clean_demographic_strings(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or blank.")
        return cleaned

    @field_validator("nic_number")
    @classmethod
    def validate_identity_number(cls, v: str, info: ValidationInfo) -> str:
        doc_type = info.data.get("identity_document_type", "nic") if info.data else "nic"
        if doc_type == "passport":
            return validate_passport(v)
        return validate_sri_lankan_nic(v)

    @field_validator(
        "permanent_province",
        "permanent_district",
        "permanent_city",
        "permanent_landmark",
        "permanent_postal_code",
    )
    @classmethod
    def clean_required_strings(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or blank.")
        return cleaned


class BusinessDetailsRequest(BaseModel):
    business_name: Annotated[str, Field(min_length=2, max_length=150)]
    provider_type: str
    secondary_phone: str | None = None
    business_province: Annotated[str | None, Field(default=None, max_length=100)] = None
    business_district: Annotated[str | None, Field(default=None, max_length=100)] = None
    business_city: Annotated[str | None, Field(default=None, max_length=100)] = None
    business_landmark: Annotated[str | None, Field(default=None, max_length=255)] = None
    business_postal_code: Annotated[str | None, Field(default=None, max_length=20)] = None
    business_registration_number: Annotated[str | None, Field(default=None, max_length=50)] = None
    description: Annotated[str | None, Field(default=None, max_length=1000)] = None
    same_as_permanent: bool = False

    @field_validator("business_name")
    @classmethod
    def clean_business_name(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Business name must be at least 2 characters.")
        return cleaned

    @field_validator("provider_type")
    @classmethod
    def validate_provider_type(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if cleaned not in VALID_PROVIDER_TYPES:
            raise ValueError(
                f"Invalid provider_type '{v}'. Allowed types are: {', '.join(sorted(VALID_PROVIDER_TYPES))}."
            )
        return cleaned

    @field_validator("secondary_phone")
    @classmethod
    def validate_secondary_phone(cls, v: str | None) -> str | None:
        if v:
            return validate_sri_lankan_phone(v)
        return None

    @field_validator(
        "business_province",
        "business_district",
        "business_city",
        "business_landmark",
        "business_postal_code",
        "business_registration_number",
        "description",
    )
    @classmethod
    def clean_optional_fields(cls, v: str | None) -> str | None:
        if v is not None:
            cleaned = v.strip()
            return cleaned if cleaned else None
        return None


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    full_name: str | None = None
    phone: str | None = None
    identity_document_type: str = "nic"
    nic_number: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    nationality: str | None = None
    permanent_province: str | None = None
    permanent_district: str | None = None
    permanent_city: str | None = None
    permanent_landmark: str | None = None
    permanent_postal_code: str | None = None

    business_name: str | None = None
    provider_type: str | None = None
    secondary_phone: str | None = None
    business_province: str | None = None
    business_district: str | None = None
    business_city: str | None = None
    business_landmark: str | None = None
    business_postal_code: str | None = None
    business_registration_number: str | None = None
    description: str | None = None

    # Safe indicators (NEVER raw filesystem paths)
    selfie_uploaded: bool = False
    nic_front_uploaded: bool = False
    nic_back_uploaded: bool = False

    personal_completed: bool = False
    business_completed: bool = False
    identity_completed: bool = False
    current_step: str = "personal"
    updated_at: datetime | None = None


class RegistrationSubmitResponse(BaseModel):
    submitted: bool = True
    approval_status: str = "pending"
    next_action: str = "await_admin_review"
    message: str = "Your application has been submitted for review."
