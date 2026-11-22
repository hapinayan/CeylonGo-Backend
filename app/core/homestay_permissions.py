
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import is_admin_role, resolve_canonical_admin_role
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.auth import get_current_user


def require_homestay_manager(
    current_user: Annotated[
        User,
        Depends(get_current_user),
    ],
    db: Annotated[
        Session | None,
        Depends(get_db),
    ] = None,
) -> User:
    """
    Ensure the caller is authorized to manage homestays.

    Authorized callers:
    1. Administrators (role == 'admin')
    2. Approved, active Service Providers with provider_type == 'homestay'
    3. Legacy owner role (role == 'owner') for backward compatibility

    Denied callers:
    - Unauthenticated callers (handled by get_current_user -> 401)
    - Inactive accounts (is_active == False -> 403)
    - Travelers (role == 'traveler' -> 403)
    - Providers with pending approval status -> 403
    - Providers with rejected approval status -> 403
    - Providers with incomplete registration (missing ProviderProfile) -> 404
    - Providers with mismatched category (e.g. artisan, tour_guide, driver) -> 403
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account.",
        )

    # 1. Administrator access (Super Admin, legacy 'admin', or Operations Admin)
    if is_admin_role(current_user.role) and resolve_canonical_admin_role(current_user.role) in (
        "super_admin",
        "operations_admin",
    ):
        return current_user

    # 2. Reject non-manager roles immediately
    if current_user.role not in ("provider", "owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner or admin access required",
        )

    # 3. Database session required to verify non-admin providers
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: database session required to verify provider profile",
        )

    # 3. Every non-admin manager must have an approved homestay provider profile
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if profile is None:
        if current_user.role == "provider":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: approved homestay provider profile required",
        )

    # Verify provider profile activation if supported by model/mock
    if getattr(profile, "is_active", True) is False:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: provider profile is inactive",
        )

    # Verify provider approval status
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

    # Verify provider category
    if profile.provider_type != "homestay":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only homestay service providers can manage homestays",
        )

    return current_user


def check_homestay_ownership(
    homestay: object,
    current_user: User,
    profile: ProviderProfile | None = None,
) -> bool:
    """
    Verify whether the current caller is authorized to manage the specified homestay.

    Authorization hierarchy:
    1. Administrators have universal override privileges.
    2. If the homestay has an authoritative provider_profile_id:
       - If caller has a profile, provider_profile_id MUST match.
       - Prevent mismatched owner_id and provider_profile_id relationships from granting access:
         If homestay.owner_id and profile.user_id are both present and do not match, access is denied.
       - If caller has no profile, access is denied.
    3. If provider_profile_id is None (legacy listing or admin-created):
       - homestay.owner_id must match current_user.id.
    """
    if is_admin_role(current_user.role) and resolve_canonical_admin_role(current_user.role) in (
        "super_admin",
        "operations_admin",
    ):
        return True

    homestay_profile_id = getattr(homestay, "provider_profile_id", None)
    if homestay_profile_id is not None:
        if profile is not None:
            homestay_owner_id = getattr(homestay, "owner_id", None)
            profile_user_id = getattr(profile, "user_id", None)
            if (
                homestay_owner_id is not None
                and profile_user_id is not None
                and homestay_owner_id != profile_user_id
            ):
                return False
            return homestay_profile_id == profile.id
        return False

    return getattr(homestay, "owner_id", None) == current_user.id
