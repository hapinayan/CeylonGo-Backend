"""
Role-Based Access Control (RBAC) and security dependencies for CeylonGo.

Dependencies:
- require_provider: Ensures caller is an authenticated, active User with role 'provider'.
- require_approved_provider: Ensures caller is a Provider whose profile has approval_status == 'approved'.
- require_admin: Ensures caller is an authenticated, active User with role 'admin'.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.auth import get_current_user


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

