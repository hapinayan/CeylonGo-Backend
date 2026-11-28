from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field


AdminRoleType = Literal["admin", "super_admin"]

InvitationStatusType = Literal["pending", "accepted", "revoked", "expired"]


class AdminInvitationCreate(BaseModel):
    email: EmailStr = Field(..., max_length=254)
    full_name: str = Field(..., min_length=2, max_length=120)
    role: AdminRoleType
    department: Optional[str] = Field(None, max_length=100)
    invitation_note: Optional[str] = Field(None, max_length=500)


class AdminInvitationResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: AdminRoleType
    department: Optional[str] = None
    status: InvitationStatusType
    invitation_note: Optional[str] = None
    invited_by_email: Optional[str] = None
    expires_at: datetime
    created_at: datetime
    invitation_url: Optional[str] = None
    raw_token: Optional[str] = None
    email_delivered: bool = False
    delivery_status: str = "unconfigured"


class AdminInvitationVerifyResponse(BaseModel):
    valid: bool
    email: str
    full_name: str
    role: AdminRoleType
    department: Optional[str] = None
    expires_at: datetime


class AdminActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=16)
    password: str = Field(..., min_length=8, max_length=128)


class AdminStatusToggleRequest(BaseModel):
    is_active: bool


class AdminAccountItem(BaseModel):
    id: int
    full_name: str
    email: str
    role: AdminRoleType
    admin_role: AdminRoleType
    is_active: bool
    status: Literal["active", "inactive", "invited"]
    department: Optional[str] = None
    created_by: Optional[str] = None
    last_active: Optional[str] = None
    created_at: Optional[datetime] = None


class AdminAccountsListResponse(BaseModel):
    items: list[AdminAccountItem]
    total: int
    page: int
    page_size: int
    total_pages: int
