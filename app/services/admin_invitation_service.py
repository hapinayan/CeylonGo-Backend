import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, true
from sqlalchemy.exc import IntegrityError, ProgrammingError, OperationalError
from sqlalchemy.orm import Session

from app.core.admin_roles import MVP_ADMIN_ROLES
from app.core.email import is_smtp_configured, send_admin_invitation_email
from app.core.security import hash_password
from app.models.admin_invitation import AdminInvitation
from app.models.mvp import MVPUser
from app.schemas.admin_management import (
    AdminAccountItem,
    AdminAccountsListResponse,
    AdminInvitationCreate,
    AdminInvitationResponse,
    AdminInvitationVerifyResponse,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _active_super_admins(db: Session) -> set[int]:
    # Serialize admin-account and invitation mutations on SQL Server.
    rows = (
        db.query(MVPUser.id)
        .with_hint(MVPUser, "WITH (TABLOCKX, HOLDLOCK)", dialect_name="mssql")
        .filter(MVPUser.role == "super_admin", MVPUser.is_active == true())
        .all()
    )
    return {row.id for row in rows}


def _require_active_super_admin(db: Session, actor_id: int) -> set[int]:
    admins = _active_super_admins(db)
    if actor_id not in admins:
        raise HTTPException(403, "An active Super Admin is required.")
    return admins


def _invitation_query(db: Session):
    return db.query(AdminInvitation).populate_existing()


def _check_storage_error(exc: Exception) -> None:
    message = str(exc).lower()
    if "admin_invitations" in message and (
        "invalid object name" in message or "no such table" in message
    ):
        raise HTTPException(503, "Admin invitation storage requires schema V1.0.10.") from exc


def _find_invitation(db: Session, **filters) -> AdminInvitation:
    try:
        invitation = _invitation_query(db).filter_by(**filters).first()
    except (ProgrammingError, OperationalError) as exc:
        _check_storage_error(exc)
        raise
    if invitation is None:
        raise HTTPException(404, "Invitation not found.")
    return invitation


def _validate_pending(invitation: AdminInvitation, now: datetime) -> None:
    if invitation.role not in MVP_ADMIN_ROLES:
        raise HTTPException(400, "Invitation has an unsupported administrator role.")
    if invitation.status == "revoked":
        raise HTTPException(400, "This invitation has been revoked.")
    if invitation.status == "accepted":
        raise HTTPException(400, "This invitation has already been accepted.")
    if invitation.status == "expired" or invitation.expires_at <= now:
        raise HTTPException(410, "This invitation link has expired.")
    if invitation.status != "pending":
        raise HTTPException(400, "This invitation is no longer pending.")


def _invitation_response(invitation: AdminInvitation) -> AdminInvitationResponse:
    return AdminInvitationResponse(
        **{name: getattr(invitation, name) for name in (
            "id", "email", "full_name", "role", "department", "status",
            "invitation_note", "invited_by_email", "expires_at", "created_at",
        )}
    )


def _deliver_invitation(invitation: AdminInvitation, token: str) -> AdminInvitationResponse:
    response = _invitation_response(invitation)
    activation_url = os.getenv("ADMIN_INVITATION_ACTIVATION_URL", "").strip()
    parsed_url = urlsplit(activation_url)
    is_local_http = parsed_url.scheme == "http" and parsed_url.hostname in ("localhost", "127.0.0.1")
    if (
        not is_smtp_configured()
        or (parsed_url.scheme != "https" and not is_local_http)
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.query
        or parsed_url.fragment
    ):
        return response
    url = f"{activation_url}?{urlencode({'token': token})}"
    response.email_delivered = send_admin_invitation_email(invitation.email, url)
    response.delivery_status = "sent" if response.email_delivered else "failed"
    return response


def _account_item(user: MVPUser) -> AdminAccountItem:
    return AdminAccountItem(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        admin_role=user.role,
        is_active=user.is_active,
        status="active" if user.is_active else "inactive",
        created_at=user.created_at,
    )


class AdminInvitationService:
    @staticmethod
    def create_invitation(
        db: Session, current_admin: MVPUser, payload: AdminInvitationCreate,
    ) -> AdminInvitationResponse:
        _require_active_super_admin(db, current_admin.id)
        email = payload.email.strip().lower()
        if db.query(MVPUser.id).filter(func.lower(MVPUser.email) == email).first():
            raise HTTPException(400, "An account with this email address already exists.")
        now = _utc_now()
        try:
            existing = _invitation_query(db).filter(
                AdminInvitation.email == email,
                AdminInvitation.status == "pending",
                AdminInvitation.expires_at > now,
            ).first()
        except (ProgrammingError, OperationalError) as exc:
            _check_storage_error(exc)
            raise
        if existing:
            raise HTTPException(409, "An active invitation already exists for this email.")

        token = secrets.token_urlsafe(32)
        invitation = AdminInvitation(
            email=email,
            full_name=payload.full_name.strip(),
            role=payload.role,
            department=payload.department,
            invitation_note=payload.invitation_note,
            token_hash=_hash_token(token),
            status="pending",
            invited_by_id=current_admin.id,
            invited_by_email=current_admin.email,
            expires_at=now + timedelta(hours=48),
            created_at=now,
        )
        db.add(invitation)
        db.commit()
        return _deliver_invitation(invitation, token)

    @staticmethod
    def list_invitations(
        db: Session, status_filter: str | None = None,
    ) -> list[AdminInvitationResponse]:
        query = _invitation_query(db)
        now = _utc_now()
        expired = and_(
            AdminInvitation.status == "pending",
            AdminInvitation.expires_at <= now,
        )
        if status_filter == "pending":
            query = query.filter_by(status="pending").filter(AdminInvitation.expires_at > now)
        elif status_filter == "expired":
            query = query.filter(or_(AdminInvitation.status == "expired", expired))
        elif status_filter and status_filter != "all":
            query = query.filter_by(status=status_filter)
        try:
            invitations = query.order_by(AdminInvitation.id.desc()).all()
            results = []
            for invitation in invitations:
                response = _invitation_response(invitation)
                if invitation.status == "pending" and invitation.expires_at <= now:
                    response.status = "expired"
                results.append(response)
            return results
        except (ProgrammingError, OperationalError) as exc:
            _check_storage_error(exc)
            raise

    @staticmethod
    def resend_invitation(
        db: Session, invitation_id: int, current_admin: MVPUser,
    ) -> AdminInvitationResponse:
        _require_active_super_admin(db, current_admin.id)
        invitation = _find_invitation(db, id=invitation_id)
        if invitation.status == "accepted":
            raise HTTPException(400, "Cannot resend an accepted invitation.")
        token = secrets.token_urlsafe(32)
        invitation.token_hash = _hash_token(token)
        invitation.expires_at = _utc_now() + timedelta(hours=48)
        invitation.status = "pending"
        invitation.revoked_at = None
        invitation.invited_by_id = current_admin.id
        invitation.invited_by_email = current_admin.email
        db.commit()
        return _deliver_invitation(invitation, token)

    @staticmethod
    def revoke_invitation(
        db: Session, invitation_id: int, current_admin: MVPUser,
    ) -> dict:
        _require_active_super_admin(db, current_admin.id)
        invitation = _find_invitation(db, id=invitation_id)
        if invitation.status == "accepted":
            raise HTTPException(400, "Cannot revoke an accepted invitation.")
        invitation.status = "revoked"
        invitation.revoked_at = _utc_now()
        db.commit()
        return {"success": True, "message": f"Invitation #{invitation_id} has been revoked."}

    @staticmethod
    def verify_token(db: Session, raw_token: str) -> AdminInvitationVerifyResponse:
        invitation = _find_invitation(db, token_hash=_hash_token(raw_token.strip()))
        _validate_pending(invitation, _utc_now())
        if not db.query(MVPUser.id).filter(
            MVPUser.id == invitation.invited_by_id,
            MVPUser.role == "super_admin",
            MVPUser.is_active == true(),
        ).first():
            raise HTTPException(403, "An active Super Admin must authorize this invitation.")
        return AdminInvitationVerifyResponse(
            valid=True,
            email=invitation.email,
            full_name=invitation.full_name,
            role=invitation.role,
            department=invitation.department,
            expires_at=invitation.expires_at,
        )

    @staticmethod
    def activate_account(db: Session, raw_token: str, password: str) -> dict:
        token_hash = _hash_token(raw_token.strip())
        invitation = _find_invitation(db, token_hash=token_hash)
        _validate_pending(invitation, _utc_now())

        admins = _active_super_admins(db)
        invitation = _find_invitation(db, token_hash=token_hash)
        now = _utc_now()
        _validate_pending(invitation, now)
        if invitation.invited_by_id not in admins:
            raise HTTPException(403, "An active Super Admin must authorize this invitation.")

        email = invitation.email.lower()
        if db.query(MVPUser.id).filter(func.lower(MVPUser.email) == email).first():
            raise HTTPException(409, "An account with this email address already exists.")
        db.add(MVPUser(
            email=email,
            full_name=invitation.full_name,
            hashed_password=hash_password(password),
            role=invitation.role,
            is_active=True,
        ))
        invitation.status = "accepted"
        invitation.accepted_at = now
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "The account could not be activated.") from exc
        return {
            "success": True,
            "message": "Administrator account successfully activated. You may now log in.",
            "email": email,
        }

    @staticmethod
    def list_admin_accounts(
        db: Session,
        current_admin: MVPUser,
        role: str | None = None,
        status_filter: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> AdminAccountsListResponse:
        users = db.query(MVPUser).filter(MVPUser.role.in_(MVP_ADMIN_ROLES)).order_by(MVPUser.id)
        accounts_map = {user.email.lower(): _account_item(user) for user in users}
        now = _utc_now()
        for inv in AdminInvitationService.list_invitations(db, status_filter="pending"):
            if inv.expires_at > now and inv.email.lower() not in accounts_map:
                accounts_map[inv.email.lower()] = AdminAccountItem(
                    id=inv.id + 100000,
                    full_name=inv.full_name,
                    email=inv.email,
                    role=inv.role,
                    admin_role=inv.role,
                    is_active=True,
                    status="invited",
                    department=inv.department,
                    created_by=inv.invited_by_email,
                    last_active="Never",
                    created_at=inv.created_at,
                )

        all_items = list(accounts_map.values())

        # 3. Apply filters
        if role and role != "all":
            all_items = [a for a in all_items if a.role == role or a.admin_role == role]

        if status_filter and status_filter != "all":
            all_items = [a for a in all_items if a.status == status_filter]

        if search:
            q = search.lower().strip()
            all_items = [
                a for a in all_items
                if q in a.full_name.lower() or q in a.email.lower()
            ]

        total = len(all_items)
        page = max(1, page)
        page_size = max(1, page_size)
        total_pages = max(1, (total + page_size - 1) // page_size)
        start = (page - 1) * page_size
        paginated_items = all_items[start : start + page_size]

        return AdminAccountsListResponse(
            items=paginated_items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    @staticmethod
    def toggle_status(
        db: Session, current_admin: MVPUser, admin_id: int, is_active: bool,
    ) -> AdminAccountItem:
        admins = _require_active_super_admin(db, current_admin.id)
        user = db.get(MVPUser, admin_id, populate_existing=True)
        if user is None or user.role not in MVP_ADMIN_ROLES:
            raise HTTPException(404, "Administrator not found.")
        if not is_active and admins == {admin_id}:
            raise HTTPException(409, "Cannot deactivate the last active Super Admin.")
        if not is_active and admin_id == current_admin.id:
            raise HTTPException(400, "Administrators cannot deactivate their own account.")
        if user.is_active != is_active:
            user.is_active = is_active
            user.auth_version += 1
            user.credential_version += 1
        db.commit()
        return _account_item(user)
