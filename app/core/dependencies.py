"""
Role-Based Access Control (RBAC) and security dependencies for CeylonGo.

Dependencies:
- require_provider: Ensures caller is an authenticated, active User with role 'provider'.
- require_approved_provider: Ensures caller is a Provider whose profile has approval_status == 'approved'.
- require_admin: Ensures caller is an authenticated, active User with role 'admin'.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.otp import hash_authorization_token
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.user import User
from app.routers.auth import get_current_user

registration_security_scheme = HTTPBearer(
    scheme_name="ProviderRegistrationBearer",
    description="Provider Registration Authorization Token (Bearer <token>)",
    auto_error=False,
)



def require_provider(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Ensure the authenticated caller has the 'provider' role and is active.

    Allows pending, approved, and rejected providers to access permitted onboarding endpoints.
    Rejects users with other roles (traveler, admin) with 403 Forbidden.
    """
    if current_user.role != "provider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: provider role required",
        )
    return current_user


def require_approved_provider(
    current_user: Annotated[User, Depends(require_provider)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderProfile:
    """
    Ensure the authenticated provider's application is 'approved'.

    Verifies that the ProviderProfile exists and its approval_status is 'approved'.
    Blocks pending and rejected providers from accessing protected dashboard / operational endpoints.
    """
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider profile not found",
        )

    if profile.approval_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: provider application is pending approval",
        )

    if profile.approval_status == "rejected":
        reason = profile.rejection_reason or "No rejection reason provided."
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access forbidden: provider application was rejected ({reason})",
        )

    if profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: approved provider status required",
        )

    return profile


def require_approved_eco_provider(
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_provider)
    ],
) -> ProviderProfile:
    """
    Ensure the authenticated caller is an approved service provider
    specifically registered under the 'eco_tourism' provider_type.
    """
    if provider_profile.provider_type != "eco_tourism":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only approved eco_tourism service providers can perform this action",
        )
    return provider_profile


def require_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Ensure the authenticated caller has the 'admin' role and is active.

    Rejects travelers and providers with 403 Forbidden.
    """
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: admin role required",
        )
    return current_user


def require_traveler(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """
    Ensure the authenticated caller has the 'traveler' role and is active.

    Rejects providers and admins with 403 Forbidden.
    """
    if current_user.role != "traveler":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: traveler role required",
        )
    return current_user


def require_registration_authorization(
    auth_credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(registration_security_scheme)
    ],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderRegistrationAuthorization:
    """
    Validate the temporary registration authorization token issued by Phase 2B.

    Security Checks:
    1. Authorization Bearer header must be present and well-formed.
    2. SHA-256 hash of token looked up in database.
    3. Token must exist, not be revoked, not be consumed, and not be expired.
    4. Token purpose must be 'registration' or 'resubmission'.
    5. Rechecks live account state:
       - Traveler: 403 Forbidden.
       - Admin: 403 Forbidden.
       - Existing Provider:
         - Pending: 400 Bad Request.
         - Approved with password: 400 Bad Request.
         - Rejected: purpose must be 'resubmission'.
       - Unregistered:
         - purpose must be 'registration'.
    """
    if not auth_credentials or not auth_credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = auth_credentials.credentials.strip()
    token_hash = hash_authorization_token(raw_token)

    auth_record = (
        db.query(ProviderRegistrationAuthorization)
        .filter(ProviderRegistrationAuthorization.token_hash == token_hash)
        .first()
    )

    if not auth_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid registration authorization token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth_record.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration authorization token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth_record.is_consumed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration authorization token has already been used.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(UTC)
    expires_at = auth_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Registration authorization token has expired. Please verify your email again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth_record.purpose not in ("registration", "resubmission"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Authorization token purpose not allowed for registration.",
        )

    # Recheck live account state
    user = db.query(User).filter(User.email == auth_record.email).first()
    if user:
        if user.role == "traveler":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This email is registered as a traveler account.",
            )
        if user.role == "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin accounts cannot register as providers.",
            )
        if user.role == "provider":
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == user.id)
                .first()
            )
            if profile:
                if profile.approval_status == "pending":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Your provider application is currently pending admin review.",
                    )
                if profile.approval_status == "approved" and user.password_hash:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Account is already active. Please log in directly.",
                    )
                if profile.approval_status == "rejected" and auth_record.purpose != "resubmission":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid authorization token purpose for resubmission.",
                    )
    else:
        if auth_record.purpose != "registration":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid authorization token purpose for new registration.",
            )

    return auth_record


def require_password_setup_authorization(
    auth_credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(registration_security_scheme)
    ],
    db: Annotated[Session, Depends(get_db)],
) -> tuple[ProviderRegistrationAuthorization, User, ProviderProfile]:
    """
    Validate the temporary password-setup authorization token issued after OTP verification.

    Security Checks:
    1. Authorization Bearer header must be present and well-formed.
    2. SHA-256 hash of token looked up in database.
    3. Purpose must be exactly 'password_setup'.
    4. Token must not be expired, not revoked, and not consumed.
    5. Verified email belongs to an existing User with role 'provider'.
    6. ProviderProfile exists and has approval_status == 'approved'.
    7. User currently has no password (password_hash is None).
    8. User is not already activated (is_active is False).
    """
    if not auth_credentials or not auth_credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password setup authorization token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = auth_credentials.credentials.strip()
    token_hash = hash_authorization_token(raw_token)

    auth_record = (
        db.query(ProviderRegistrationAuthorization)
        .filter(ProviderRegistrationAuthorization.token_hash == token_hash)
        .first()
    )

    if not auth_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid password setup authorization token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth_record.purpose != "password_setup":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Invalid authorization token purpose '{auth_record.purpose}'. Expected 'password_setup'.",
        )

    if auth_record.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password setup authorization token has been revoked.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if auth_record.is_consumed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password setup authorization token has already been used.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = datetime.now(UTC)
    expires_at = auth_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password setup authorization token has expired. Please verify your email again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Recheck live user and profile state
    user = (
        db.query(User)
        .filter(func.lower(User.email) == auth_record.email.lower())
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User account associated with this authorization token was not found.",
        )

    if user.role != "provider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account role '{user.role}' is not eligible for provider password setup.",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == user.id)
        .first()
    )
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider profile record was not found.",
        )

    if profile.approval_status != "approved":
        if profile.approval_status == "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider application is still pending review.",
            )
        if profile.approval_status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider application has been rejected.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider application status is '{profile.approval_status}'. Approval required.",
        )

    if user.password_hash is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password setup has already been completed for this account. Please sign in via provider login.",
        )

    if user.is_active is True:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider account is already activated. Please sign in via provider login.",
        )

    return auth_record, user, profile
