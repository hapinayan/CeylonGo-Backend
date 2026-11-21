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


def validate_provider_password_strength(password: str) -> str:
    if not password.strip():
        raise ValueError("Password cannot be blank or whitespace only.")
    if len(password) < 12:
        raise ValueError("Provider password must be at least 12 characters long.")
    if len(password) > 128:
        raise ValueError("Password cannot exceed 128 characters.")
    return password


class ProviderPasswordSetupRequest(BaseModel):
    password: str = Field(..., min_length=12, max_length=128, description="Strong password (at least 12 characters)")
    confirm_password: str = Field(..., min_length=12, max_length=128, description="Password confirmation")

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        return validate_provider_password_strength(v)

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
    created_at: datetime
