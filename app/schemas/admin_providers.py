from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProviderListItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    full_name: str
    email: str
    business_name: str
    provider_type: str
    district: str
    approval_status: str
    created_at: datetime


class ProviderListResponse(BaseModel):
    items: list[ProviderListItemResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class ProviderDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    full_name: str
    email: str
    role: str
    is_active: bool

    # Personal details
    phone: str
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

    # Business details
    business_name: str
    provider_type: str
    secondary_phone: str | None = None
    district: str
    business_province: str | None = None
    business_district: str | None = None
    business_city: str | None = None
    business_landmark: str | None = None
    business_postal_code: str | None = None
    business_registration_number: str | None = None
    description: str | None = None

    # Approval Workflow
    approval_status: str
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    reviewer_name: str | None = None
    rejection_reason: str | None = None

    # Safe Document Availability Indicators (never raw filesystem paths)
    selfie_uploaded: bool = False
    nic_front_uploaded: bool = False
    nic_back_uploaded: bool = False


class RejectProviderRequest(BaseModel):
    reason: Annotated[str, Field(min_length=5, max_length=500)]

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 5:
            raise ValueError("Rejection reason must be at least 5 characters.")
        return cleaned


class ProviderReviewActionResponse(BaseModel):
    success: bool = True
    provider_id: int
    approval_status: str
    rejection_reason: str | None = None
    message: str
    email_sent: bool
