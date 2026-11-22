"""
Pydantic response schemas for P1 Stage 3 — Admin Audit Logs API.

Exposes only safe audit log and administrator identity fields.
Never exposes password hashes, tokens, or secrets.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditActorSummary(BaseModel):
    """Safe summary of the administrator who performed the audited action."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: str
    role: str
    admin_role: str


class AdminAuditLogItemResponse(BaseModel):
    """Single audit log record returned by GET /api/v1/admin/audit-logs."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    admin_user_id: int
    admin: AuditActorSummary | None = None
    action_type: str
    entity_type: str
    entity_id: int
    details: dict[str, Any] | str | None = None
    ip_address: str | None = None
    created_at: datetime


class AdminAuditLogListResponse(BaseModel):
    """Paginated response for GET /api/v1/admin/audit-logs."""

    items: list[AdminAuditLogItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
