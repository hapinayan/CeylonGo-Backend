from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.admin_roles import MVP_ADMIN_ROLES, is_admin_role, resolve_canonical_admin_role
from app.core.dependencies import require_admin
from app.core.mvp_security import require_mvp_role
from app.core.rate_limiter import admin_login_limiter
from app.core.security import ADMIN_AUTH_METHOD, create_access_token, verify_admin_password
from app.core.sensitive_route import SensitiveInputRoute
from app.database.connection import get_db
from app.models.mvp import MVPUser
from app.models.user import User
from app.schemas.admin_auth import (
    AdminLoginRequest,
    AdminTokenResponse,
    AdminUserResponse,
    MVPAdminTokenResponse,
    MVPAdminUserResponse,
)

mvp_router = APIRouter(
    route_class=SensitiveInputRoute,
    prefix="/api/v1/admin/auth",
    tags=["Admin Authentication"],
)

router = APIRouter(
    route_class=SensitiveInputRoute,
    prefix=mvp_router.prefix,
    tags=mvp_router.tags,
)


def rate_limit_admin_login(request: Request) -> None:
    admin_login_limiter.check_peer(request, "admin-login")


def _serialize_admin_user(user: Any, *, mvp: bool) -> AdminUserResponse:
    model = MVPAdminUserResponse if mvp else AdminUserResponse
    return model(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        admin_role=user.role if mvp else resolve_canonical_admin_role(user.role),
        is_active=user.is_active,
        created_at=user.created_at,
    )


@mvp_router.post(
    "/login",
    response_model=MVPAdminTokenResponse,
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

    Accepts the Academic MVP roles `admin` and `super_admin`.
    Rejects non-admin roles (`traveler`, `provider`, etc.) and inactive accounts.
    """
    normalized_email = payload.email.strip().lower()

    user = db.query(MVPUser).filter(func.lower(MVPUser.email) == normalized_email).first()
    return _authenticate(user, payload.password, mvp=True)


def _authenticate(user: MVPUser | User | None, password: str, *, mvp: bool) -> AdminTokenResponse:
    stored_hash = getattr(user, "hashed_password", None) or getattr(user, "password_hash", None)
    password_matches = verify_admin_password(password, stored_hash)
    allowed = user and (user.role in MVP_ADMIN_ROLES if mvp else is_admin_role(user.role))
    if not allowed or not password_matches or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(
        data={
            "sub": str(user.id),
            "auth_method": ADMIN_AUTH_METHOD,
            "role": user.role,
            "auth_version": getattr(user, "auth_version", 0),
        },
        credential_version=getattr(user, "credential_version", 0),
    )

    model = MVPAdminTokenResponse if mvp else AdminTokenResponse
    return model(
        access_token=token,
        token_type="bearer",
        user=_serialize_admin_user(user, mvp=mvp),
    )


@mvp_router.get(
    "/me",
    response_model=MVPAdminUserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get profile of the currently authenticated administrator",
)
def get_admin_me(
    current_admin: Annotated[MVPUser, Depends(require_mvp_role("admin"))],
) -> AdminUserResponse:
    """
    Retrieve the authenticated administrator's safe identity profile.
    Requires an active Academic MVP administrator.
    """
    return _serialize_admin_user(current_admin, mvp=True)

@router.post("/login", response_model=AdminTokenResponse, dependencies=[Depends(rate_limit_admin_login)])
def legacy_admin_login(
    payload: AdminLoginRequest, db: Annotated[Session, Depends(get_db)],
) -> AdminTokenResponse:
    email = payload.email.strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    return _authenticate(user, payload.password, mvp=False)


@router.get("/me", response_model=AdminUserResponse)
def legacy_admin_me(current_admin: Annotated[User, Depends(require_admin)]) -> AdminUserResponse:
    return _serialize_admin_user(current_admin, mvp=False)
