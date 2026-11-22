import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from app.core.audit import contains_unsafe_admin_note, record_admin_audit_log
from app.core.dependencies import require_operations_or_support_read, require_super_admin
from app.core.sensitive_route import SensitiveInputRoute
from app.database.connection import get_db
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.schemas.admin_travelers import (
    TravelerAccount, TravelerDirectory, TravelerStatusDetails, TravelerStatusEvent, TravelerStatusUpdate,
)

router = APIRouter(prefix="/api/v1/admin/travelers", tags=["Admin Travelers"], route_class=SensitiveInputRoute)
logger = logging.getLogger(__name__)
Database = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require_operations_or_support_read)]
STATUS_ACTIONS = {False: "traveler_deactivated", True: "traveler_reactivated"}


def _accounts(db: Session):
    return db.query(User.id, User.full_name, User.email, User.is_active, User.created_at).filter(
        User.role == "traveler"
    )


def _account(db: Session, traveler_id: int) -> TravelerAccount:
    row = _accounts(db).filter(User.id == traveler_id).one_or_none()
    if row is None:
        raise HTTPException(404, "Traveler not found")
    return TravelerAccount.model_validate(row)


@router.get("", response_model=TravelerDirectory)
def list_travelers(
    _admin: Reader,
    db: Database,
    is_active: bool | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> TravelerDirectory:
    query = _accounts(db)
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    if search and (term := search.strip().lower()):
        # Escape SQL Server's bracket pattern as well as standard LIKE wildcards.
        term = term.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")
        pattern = f"%{term}%"
        query = query.filter(or_(func.lower(User.full_name).like(pattern, escape="!"),
                                 func.lower(User.email).like(pattern, escape="!")))
    total = query.count()
    items = query.order_by(User.created_at.desc(), User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    return TravelerDirectory(items=[TravelerAccount.model_validate(row) for row in items],
                             total=total, page=page, page_size=page_size,
                             total_pages=(total + page_size - 1) // page_size)


@router.get("/{traveler_id}", response_model=TravelerAccount)
def get_traveler(traveler_id: int, _admin: Reader, db: Database) -> TravelerAccount:
    return _account(db, traveler_id)


@router.get("/{traveler_id}/history", response_model=list[TravelerStatusEvent])
def get_traveler_history(
    traveler_id: int, _admin: Reader, db: Database,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TravelerStatusEvent]:
    _account(db, traveler_id)
    rows = (db.query(AdminAuditLog).filter(
        AdminAuditLog.entity_name == "traveler_account", AdminAuditLog.entity_id == traveler_id,
        AdminAuditLog.action.in_(tuple(STATUS_ACTIONS.values())),
    ).order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size))
    events = []
    for row in rows:
        try:
            details = TravelerStatusDetails.model_validate_json(row.details or "null")
            if row.action != STATUS_ACTIONS[details.is_active]:
                raise ValueError("Status history action does not match transition")
            if contains_unsafe_admin_note(details.note):
                details.note = "[REDACTED]"
            events.append(TravelerStatusEvent(
                admin_user_id=row.admin_user_id, created_at=row.created_at,
                **details.model_dump(),
            ))
        except ValueError:
            logger.error("Invalid traveler status history event id=%s", row.id)
            raise HTTPException(500, "Traveler status history is unavailable") from None
    return events


@router.patch("/{traveler_id}/status", response_model=TravelerAccount)
def update_traveler_status(
    traveler_id: int, payload: TravelerStatusUpdate, request: Request,
    admin: Annotated[User, Depends(require_super_admin)], db: Database,
) -> TravelerAccount:
    if contains_unsafe_admin_note(payload.note):
        raise HTTPException(422, "Administrative note contains credentials or personal information")
    current = _accounts(db).add_columns(User.auth_version).filter(User.id == traveler_id).one_or_none()
    if current is None:
        raise HTTPException(404, "Traveler not found")
    account = TravelerAccount.model_validate(current)
    if account.is_active == payload.is_active:
        return account

    details = TravelerStatusDetails(previous_is_active=current.is_active, **payload.model_dump())
    try:
        result = db.execute(update(User).where(
            User.id == traveler_id, User.role == "traveler",
            User.is_active == current.is_active, User.auth_version == current.auth_version,
        ).values(is_active=payload.is_active, auth_version=User.auth_version + 1)
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, "Traveler account changed. Reload and retry.")
        record_admin_audit_log(
            db, admin_user_id=admin.id, action=STATUS_ACTIONS[payload.is_active],
            entity_name="traveler_account", entity_id=traveler_id, request=request,
            details=details.model_dump(),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    account.is_active = payload.is_active
    return account
