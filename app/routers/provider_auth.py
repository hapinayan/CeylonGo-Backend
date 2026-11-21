import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import (
    require_approved_provider,
    require_password_setup_authorization,
)
from app.core.provider_onboarding import lock_onboarding, require_live_authorization
from app.core.rate_limiter import (
    provider_login_limiter,
    provider_password_setup_limiter,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.user import User
from app.schemas.provider_auth import (
    ProviderLoginRequest,
    ProviderLoginResponse,
    ProviderMeResponse,
    ProviderPasswordSetupRequest,
    ProviderPasswordSetupResponse,
    ProviderUserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/providers",
    tags=["Provider Authentication"],
)


def rate_limit_password_setup(request: Request) -> None:
    provider_password_setup_limiter.check(request)


def rate_limit_provider_login(request: Request) -> None:
    provider_login_limiter.check(request)


@router.post(
    "/password/setup",
    response_model=ProviderPasswordSetupResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_password_setup)],
    summary="Set provider account password and activate account",
)
def setup_provider_password(
    payload: ProviderPasswordSetupRequest,
    auth_context: Annotated[
        tuple[ProviderRegistrationAuthorization, User, ProviderProfile],
        Depends(require_password_setup_authorization),
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Complete password creation for an approved service provider.
    Validates the temporary authorization token, stores the Argon2 password hash,
    activates the account (users.is_active = True), consumes the authorization token,
    and revokes any other active setup tokens for this email in a single atomic transaction.
    """
    auth_record, user, profile = auth_context

    lock_onboarding(db, auth_record.email)
    require_live_authorization(db, auth_record)
    db.refresh(profile)
    db.refresh(user)

    if profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider application status is '{profile.approval_status}'. Approval required.",
        )

    if user.password_hash is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password setup has already been completed for this account. Please sign in via provider login.",
        )

    now = datetime.now(UTC)

    try:
        require_live_authorization(db, auth_record, consume=True)
        updated = db.query(User).filter(
            User.id == user.id, User.role == "provider", User.password_hash.is_(None),
            User.is_active == False,
            db.query(ProviderProfile.id).filter(
                ProviderProfile.id == profile.id, ProviderProfile.user_id == user.id,
                ProviderProfile.approval_status == "approved",
            ).exists(),
        ).update({
            "password_hash": hash_password(payload.password), "is_active": True,
        }, synchronize_session=False)
        if updated != 1:
            db.rollback()
            raise HTTPException(409, detail="Provider approval or activation state changed.")

        # 4. Revoke any other unused password-setup authorization tokens for this provider
        db.query(ProviderRegistrationAuthorization).filter(
            ProviderRegistrationAuthorization.email == auth_record.email,
            ProviderRegistrationAuthorization.purpose == "password_setup",
            ProviderRegistrationAuthorization.id != auth_record.id,
            ProviderRegistrationAuthorization.is_consumed == False,
        ).update({"is_revoked": True, "revoked_at": now}, synchronize_session=False)

        # 5. Commit atomic transaction
        db.commit()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning("Database error occurred during provider password setup.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete password setup. Please try again.",
        ) from exc

    return ProviderPasswordSetupResponse(
        success=True,
        account_activated=True,
        next_action="provider_login",
        message="Your provider account is ready. Please sign in.",
    )


@router.post(
    "/login",
    response_model=ProviderLoginResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_provider_login)],
    summary="Authenticate approved service provider and issue JWT access token",
)
def provider_login(
    payload: ProviderLoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Authenticate an approved service provider with their email and password.
    Enforces that only approved, activated provider accounts can authenticate.
    Traveler, Admin, pending, rejected, or unactivated accounts are rejected with 403 Forbidden.
    """
    normalized_email = payload.email.strip().lower()

    user = (
        db.query(User)
        .filter(func.lower(User.email) == normalized_email)
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if user.role != "provider":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access restricted to provider accounts. Please use the appropriate login portal.",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == user.id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider profile record not found.",
        )

    if profile.approval_status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider application is pending admin review.",
        )

    if profile.approval_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider application was rejected.",
        )

    if profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider application is not approved.",
        )

    if user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider account setup incomplete. Password setup required.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Provider account is inactive. Please contact support.",
        )

    password_hash, credential_version = user.password_hash, user.credential_version
    if not verify_password(payload.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Issue signed JWT access token
    token = create_access_token(
        data={"sub": str(user.id)}, credential_version=credential_version
    )

    return ProviderLoginResponse(
        access_token=token,
        token_type="bearer",
        user=ProviderUserResponse(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            is_active=user.is_active,
            business_name=profile.business_name,
            provider_type=profile.provider_type,
            district=profile.district,
            approval_status=profile.approval_status,
        ),
    )


@router.get(
    "/me",
    response_model=ProviderMeResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current approved provider profile information",
)
def get_current_provider_profile(
    profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """
    Retrieve the authenticated, approved provider's profile information.
    Protected by require_approved_provider dependency.
    """
    user = profile.user
    return ProviderMeResponse(
        id=profile.id,
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        business_name=profile.business_name,
        provider_type=profile.provider_type,
        phone=profile.phone,
        secondary_phone=profile.secondary_phone,
        district=profile.district,
        description=profile.description,
        business_registration_number=profile.business_registration_number,
        approval_status=profile.approval_status,
        created_at=profile.created_at,
    )
