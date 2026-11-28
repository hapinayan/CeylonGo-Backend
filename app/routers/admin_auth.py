from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import (
    is_admin_role,
    require_admin,
    resolve_canonical_admin_role,
)
from app.core.rate_limiter import admin_login_limiter
from app.core.security import ADMIN_AUTH_METHOD, create_access_token, verify_admin_password
from app.core.sensitive_route import SensitiveInputRoute
from app.database.connection import get_db
from app.models.user import User
from app.schemas.admin_auth import (
    AdminLoginRequest,
    AdminTokenResponse,
    AdminUserResponse,
)

router = APIRouter(
    route_class=SensitiveInputRoute,
    prefix="/api/v1/admin/auth",
    tags=["Admin Authentication"],
)


def rate_limit_admin_login(request: Request) -> None:
    admin_login_limiter.check_peer(request, "admin-login")


def _serialize_admin_user(user: Any) -> AdminUserResponse:
    canonical = resolve_canonical_admin_role(user.role) if is_admin_role(user.role) else user.role
    return AdminUserResponse(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        admin_role=canonical,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post(
    "/login",
    response_model=AdminTokenResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_admin_login)],
    summary="Authenticate administrator and issue JWT access token",
)
def admin_login(
    payload: AdminLoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> AdminTokenResponse:
    """
    Authenticate an administrator using email and password.

    Recognizes the five approved admin role identifiers (`super_admin`,
    `operations_admin`, `finance_admin`, `content_moderator`, `support_admin`)
    as well as the legacy `admin` role (`admin_role` resolved to `super_admin`).
    Rejects non-admin roles (`traveler`, `provider`, etc.) and inactive accounts.
    """
    normalized_email = payload.email.strip().lower()

    user = None
    try:
        from app.models.mvp import MVPUser
        user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
    except Exception:
        db.rollback()
    if not user:
        try:
            user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
        except Exception:
            db.rollback()

    stored_hash = getattr(user, "hashed_password", None) or getattr(user, "password_hash", None)
    password_matches = verify_admin_password(payload.password, stored_hash)
    if not user or not password_matches or not user.is_active or not is_admin_role(user.role):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(data={"sub": str(user.id), "auth_method": ADMIN_AUTH_METHOD, "role": user.role})

    return AdminTokenResponse(
        access_token=token,
        token_type="bearer",
        user=_serialize_admin_user(user),
    )


@router.get(
    "/me",
    response_model=AdminUserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get profile of the currently authenticated administrator",
)
def get_admin_me(
    current_admin: Annotated[User, Depends(require_admin)],
) -> AdminUserResponse:
    """
    Retrieve the authenticated administrator's safe identity profile.
    Protected by `require_admin`.
    """
    return _serialize_admin_user(current_admin)
