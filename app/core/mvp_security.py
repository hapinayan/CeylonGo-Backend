"""
CeylonGo Academic MVP — Security and Authentication Helpers
Handles password hashing, JWT creation/verification, and version-based token invalidation.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
import hashlib
import os
import secrets
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.admin_roles import is_admin_role
from app.core.security import hash_password, verify_password, get_jwt_secret_key, get_jwt_algorithm
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPProviderProfile, MVPUser

http_bearer = HTTPBearer(auto_error=False)


def require_approved_provider(db: Session, user: MVPUser) -> MVPProviderProfile:
    profile = db.query(MVPProviderProfile).filter_by(user_id=user.id).first()
    if not profile or profile.verification_status != "approved":
        state = profile.verification_status if profile else "pending"
        raise HTTPException(403, f"Provider application status: {state}.")
    return profile

def create_mvp_access_token(
    user_id: int,
    email: str,
    role: str,
    auth_version: int = 0,
    credential_version: int = 0,
    expires_delta: timedelta | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=8))
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "auth_version": auth_version,
        "credential_version": credential_version,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    secret = get_jwt_secret_key()
    algo = get_jwt_algorithm()
    return jwt.encode(payload, secret, algorithm=algo)

def decode_mvp_token(token: str) -> dict[str, Any]:
    secret = get_jwt_secret_key()
    algo = get_jwt_algorithm()
    try:
        return jwt.decode(token, secret, algorithms=[algo])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def get_current_mvp_user(
    auth: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    db: Session = Depends(get_mvp_db),
) -> MVPUser:
    if not auth or not auth.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = _resolve_user_from_token(auth.credentials, db)
    if user.role == "provider":
        require_approved_provider(db, user)
    return user


def get_optional_mvp_user(
    auth: HTTPAuthorizationCredentials | None = Depends(http_bearer),
    db: Session = Depends(get_mvp_db),
) -> MVPUser | None:
    if not auth or not auth.credentials:
        return None
    return _resolve_user_from_token(auth.credentials, db)


def _resolve_user_from_token(token: str, db: Session) -> Any:
    payload = decode_mvp_token(token)
    user_id = int(payload.get("sub", 0))
    user = None
    try:
        user = db.query(MVPUser).filter(MVPUser.id == user_id).first()
    except Exception:
        db.rollback()
    if not user:
        try:
            from app.models.user import User
            user = db.query(User).filter(User.id == user_id).first()
        except Exception:
            db.rollback()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Check auth_version and credential_version (token invalidation upon logout / reset)
    if payload.get("auth_version", 0) != getattr(user, "auth_version", 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been invalidated. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("credential_version", 0) != getattr(user, "credential_version", 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credentials have been updated. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

def require_mvp_role(*allowed_roles: str):
    def role_checker(user: Any = Depends(get_current_mvp_user)) -> Any:
        if "admin" in allowed_roles and is_admin_role(user.role):
            return user
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access forbidden: requires one of {allowed_roles}",
            )
        return user
    return role_checker

def hash_binary_token(token: str) -> bytes:
    """Returns 32-byte binary SHA-256 hash of a token string."""
    return hashlib.sha256(token.encode("utf-8")).digest()
