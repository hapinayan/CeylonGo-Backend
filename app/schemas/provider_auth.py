from datetime import datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class ProviderPasswordSetupRequest(BaseModel):
    password: str = Field(..., min_length=12, max_length=128, description="Strong password (at least 12 characters)")
    confirm_password: str = Field(..., min_length=12, max_length=128, description="Password confirmation")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Password cannot be blank or whitespace only.")
        if len(v) < 12:
            raise ValueError("Provider password must be at least 12 characters long.")
        if len(v) > 128:
            raise ValueError("Password cannot exceed 128 characters.")
        return v

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        if self.password != self.confirm_password:
            raise ValueError("Password and confirmation password do not match.")
        return self


class ProviderPasswordSetupResponse(BaseModel):
    success: bool = True
    account_activated: bool = True
    next_action: str = "provider_login"
    message: str = "Your provider account is ready. Please sign in."


class ProviderLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, description="Account password")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower() if isinstance(v, str) else v


class ProviderUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: str = "provider"
    is_active: bool
    business_name: str
    provider_type: str
    district: str
    approval_status: str


class ProviderLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: ProviderUserResponse


class ProviderMeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    business_name: str
    provider_type: str
    phone: str
    secondary_phone: str | None = None
    district: str
    description: str | None = None
    business_registration_number: str | None = None
    approval_status: str
    business_province: str | None = None
    business_district: str | None = None
    business_city: str | None = None
    business_landmark: str | None = None
    business_postal_code: str | None = None
    created_at: datetime


class ProviderProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str | None = Field(default=None, min_length=2, max_length=150)
    phone: str | None = Field(default=None, min_length=9, max_length=25)
    secondary_phone: str | None = Field(default=None, max_length=25)
    district: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    business_province: str | None = Field(default=None, max_length=100)
    business_district: str | None = Field(default=None, max_length=100)
    business_city: str | None = Field(default=None, max_length=100)
    business_landmark: str | None = Field(default=None, max_length=255)
    business_postal_code: str | None = Field(default=None, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_primary_phone(cls, v: str | None) -> str | None:
        if v:
            from app.schemas.provider_registration import validate_sri_lankan_phone
            return validate_sri_lankan_phone(v)
        return None

    @field_validator("secondary_phone")
    @classmethod
    def validate_sec_phone(cls, v: str | None) -> str | None:
        if v:
            from app.schemas.provider_registration import validate_sri_lankan_phone
            return validate_sri_lankan_phone(v)
        return None


class ProviderPasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(..., min_length=1, description="Current account password")
    new_password: str = Field(..., min_length=8, max_length=128, description="New password (at least 8 characters)")
    confirm_new_password: str = Field(..., min_length=8, max_length=128, description="Confirm new password")

    @model_validator(mode="after")
    def passwords_match(self) -> Self:
        if self.new_password != self.confirm_new_password:
            raise ValueError("New password and confirmation password do not match.")
        if self.new_password == self.current_password:
            raise ValueError("New password must be different from current password.")
        return self


class ProviderSecurityStatusResponse(BaseModel):
    user_id: int
    email: str
    is_active: bool
    has_password: bool
    role: str
    provider_type: str
    approval_status: str

