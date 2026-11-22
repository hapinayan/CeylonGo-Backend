from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class AdminLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, description="Admin account password")

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower() if isinstance(value, str) else value


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    role: str
    admin_role: str
    is_active: bool
    created_at: datetime


class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AdminUserResponse
