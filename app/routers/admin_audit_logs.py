"""
FastAPI Router for P1 Stage 3 — Admin Audit Logs Viewer.

Endpoint:
- GET /api/v1/admin/audit-logs

Security & Governance Guarantees:
- Strictly read-only: never creates additional audit records or mutates database state.
- Restricted to Super Admin (`super_admin` and legacy `admin`) via `Depends(require_super_admin)`.
- Supports bounded pagination (`page >= 1`, `1 <= page_size <= 100`) and safe filters
  (`admin_user_id`, `action_type`, `entity_type`, `entity_id`, `date_from`, `date_to`, `search`).
- Hydrates safe admin actor summary (`id`, `full_name`, `email`, `role`, `admin_role`) without N+1 queries.
- Redacts sensitive field names from structured `details` before returning them.
"""

from datetime import date, datetime, time
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.audit import redact_audit_details
from app.core.dependencies import (
    is_admin_role,
    require_admin,
    require_super_admin,
    resolve_canonical_admin_role,
)
from app.database.connection import get_db
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.schemas.admin_audit_logs import (
    AdminAuditLogItemResponse,
    AdminAuditLogListResponse,
    AuditActorSummary,
)

router = APIRouter(
    prefix="/api/v1/admin/audit-logs",
    tags=["Admin Audit Logs"],
    dependencies=[Depends(require_admin), Depends(require_super_admin)],
)


@router.get(
    "",
    response_model=AdminAuditLogListResponse,
    status_code=status.HTTP_200_OK,
    summary="List and filter immutable administrative audit logs (Super Admin only)",
)
def list_admin_audit_logs(
    _super_admin: Annotated[User, Depends(require_super_admin)],
    db: Annotated[Session, Depends(get_db)],
    admin_user_id: Annotated[
        int | None,
        Query(gt=0, description="Filter by acting administrator user ID"),
    ] = None,
    action_type: Annotated[
        str | None,
        Query(alias="action", max_length=50, description="Filter by action identifier"),
    ] = None,
    entity_type: Annotated[
        str | None,
        Query(alias="entity_name", max_length=50, description="Filter by target entity type"),
    ] = None,
    entity_id: Annotated[
        int | None,
        Query(gt=0, description="Filter by target entity ID"),
    ] = None,
    date_from: Annotated[
        date | None,
        Query(description="Filter logs created on or after this date (UTC)"),
    ] = None,
    date_to: Annotated[
        date | None,
        Query(description="Filter logs created on or before this date (UTC)"),
    ] = None,
    search: Annotated[
        str | None,
        Query(max_length=100, description="Search in action or entity type"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Items per page (max 100)")
    ] = 20,
) -> AdminAuditLogListResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to cannot be earlier than date_from",
        )

    query = db.query(AdminAuditLog)

    if admin_user_id is not None:
        query = query.filter(AdminAuditLog.admin_user_id == admin_user_id)

    if action_type and action_type.strip():
        query = query.filter(
            func.lower(AdminAuditLog.action) == action_type.strip().lower()
        )

    if entity_type and entity_type.strip():
        query = query.filter(
            func.lower(AdminAuditLog.entity_name) == entity_type.strip().lower()
        )

    if entity_id is not None:
        query = query.filter(AdminAuditLog.entity_id == entity_id)

    if date_from is not None:
        start_dt = datetime.combine(date_from, time.min)
        query = query.filter(AdminAuditLog.created_at >= start_dt)

    if date_to is not None:
        end_dt = datetime.combine(date_to, time.max)
        query = query.filter(AdminAuditLog.created_at <= end_dt)

    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(AdminAuditLog.action).like(term),
                func.lower(AdminAuditLog.entity_name).like(term),
            )
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    rows = (
        query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    admin_ids = {row.admin_user_id for row in rows}
    admins_by_id: dict[int, AuditActorSummary] = {}
    if admin_ids:
        users = db.query(User).filter(User.id.in_(admin_ids)).all()
        for u in users:
            canonical = (
                resolve_canonical_admin_role(u.role)
                if is_admin_role(u.role)
                else u.role
            )
            admins_by_id[u.id] = AuditActorSummary(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
                role=u.role,
                admin_role=canonical,
            )

    items = [
        AdminAuditLogItemResponse(
            id=row.id,
            admin_user_id=row.admin_user_id,
            admin=admins_by_id.get(row.admin_user_id),
            action_type=row.action,
            entity_type=row.entity_name,
            entity_id=row.entity_id,
            details=redact_audit_details(row.details),
            ip_address=row.ip_address,
            created_at=row.created_at,
        )
        for row in rows
    ]

    return AdminAuditLogListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
