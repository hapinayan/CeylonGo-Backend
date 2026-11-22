import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import false, func, select, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.admin_roles import is_admin_role
from app.core.email import send_password_reset_email
from app.core.google_auth import verify_google_id_token
from app.core.rate_limiter import admin_login_limiter, check_email_limiter, forgot_password_limiter, shared_auth_limiter
from app.core.security import (
    ADMIN_AUTH_METHOD,
    create_access_token,
    decode_access_token,
    generate_password_reset_token,
    get_access_token_credential_version,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.database.connection import get_db
from app.core.sensitive_route import SensitiveInputRoute
from app.models.password_reset import PasswordReset
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.auth import (
    CheckEmailRequest,
    CheckEmailResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    GoogleAuthRequest,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UserResponse,
    VerifyResetTokenResponse,
)
from app.schemas.provider_auth import validate_provider_password_strength

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"], route_class=SensitiveInputRoute)
logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(
    scheme_name="HTTPBearer",
    description="JWT Bearer token. Enter your token in format: <token>",
    auto_error=True,
)
optional_security_scheme = HTTPBearer(scheme_name="OptionalProviderPreviewBearer", auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
        credential_version = get_access_token_credential_version(payload)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id_raw = payload.get("sub")
    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(user_id_raw)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.credential_version != credential_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    if user.role == "traveler":
        version = payload.get("auth_version")
        if type(version) is not int or version != user.auth_version:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    if is_admin_role(user.role) and payload.get("auth_method") != ADMIN_AUTH_METHOD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_optional_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(optional_security_scheme)
    ],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    return get_current_user(credentials, db) if credentials is not None else None


def _issue_user_access_token(
    user: User, *, credential_version: int | None = None
) -> str:
    claims: dict[str, str | int] = {"sub": str(user.id)}
    if user.role == "traveler":
        claims["auth_version"] = user.auth_version
    version = (
        user.credential_version if credential_version is None else credential_version
    )
    return create_access_token(data=claims, credential_version=version)


def rate_limit_check_email(request: Request) -> None:
    check_email_limiter.check(request)


def rate_limit_forgot_password(request: Request) -> None:
    forgot_password_limiter.check_peer(request, "recovery-request")


def rate_limit_reset_verification(request: Request) -> None:
    admin_login_limiter.check_peer(request, "recovery-verify")


def rate_limit_reset_execution(request: Request) -> None:
    admin_login_limiter.check_peer(request, "recovery-reset")


def rate_limit_shared_login(request: Request) -> None:
    shared_auth_limiter.check_peer(request, "password-login")


def rate_limit_google_login(request: Request) -> None:
    shared_auth_limiter.check_peer(request, "google-login")


@router.post(
    "/check-email",
    response_model=CheckEmailResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_check_email)],
    summary="Check if an email address is already registered",
)
def check_email(
    request: CheckEmailRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Check whether a traveler account with the specified email address exists.

    Security Note (Account Enumeration):
    This endpoint directly returns `is_registered: true` or `false` to support
    the email-first authentication flow. Returning this boolean inherently
    reveals account existence to the caller. While rate limiting is enforced
    to hinder automated bulk enumeration, returning existence status cannot
    completely prevent enumeration of targeted email addresses.
    """
    normalized_email = request.email.strip().lower()

    try:
        user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while verifying the email address. Please try again later.",
        )

    return CheckEmailResponse(
        email=normalized_email,
        is_registered=user is not None and not is_admin_role(user.role),
    )


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new traveler account",
)
def register(
    request: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
):
    trimmed_full_name = request.full_name.strip()
    normalized_email = request.email.strip().lower()

    # Check for existing user by normalized email
    existing_user = (
        db.query(User).filter(func.lower(User.email) == normalized_email).first()
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    # Hash the password with Argon2
    hashed_pwd = hash_password(request.password)

    # Always enforce the traveler role on the backend
    new_user = User(
        full_name=trimmed_full_name,
        email=normalized_email,
        password_hash=hashed_pwd,
        role="traveler",
        is_active=True,
    )

    db.add(new_user)
    try:
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    return RegisterResponse(
        message="Account registered successfully",
        user=UserResponse.model_validate(new_user),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and return JWT access token",
    dependencies=[Depends(rate_limit_shared_login)],
)
def login(
    request: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    normalized_email = request.email.strip().lower()

    # Query user by normalized email
    user = db.query(User).filter(func.lower(User.email) == normalized_email).first()

    # Enforce Provider onboarding and application status rules
    if user and user.role == "provider":
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == user.id)
            .first()
        )
        if not profile or profile.approval_status == "pending":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Provider application is pending admin review.",
            )
        if profile.approval_status == "rejected":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Provider application was rejected.",
            )
        if profile.approval_status == "approved" and user.password_hash is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Provider account setup incomplete. Password setup required.",
            )

    # Verify user exists and password matches
    password_hash, credential_version = (
        (user.password_hash, user.credential_version) if user else (None, 0)
    )
    if not user or is_admin_role(user.role) or not verify_password(
        request.password, password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    # Check active status
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    # Generate signed JWT token with sub claim for user id
    token = _issue_user_access_token(user, credential_version=credential_version)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/google",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate traveler with Google ID token",
    dependencies=[Depends(rate_limit_google_login)],
)
def google_auth(
    request: GoogleAuthRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Authenticate or register a traveler using a Google Identity Services ID token.

    Security & Verification Rules:
    1. Verifies token cryptographically via google-auth library with configured GOOGLE_CLIENT_ID.
    2. Enforces Google issuer, audience, unexpired timestamp, subject (sub), and verified email.
    3. Finds existing user by Google sub identifier.
    4. If not found by sub, matches by normalized email:
       - If email is already linked to a different Google sub, rejects with 409 Conflict.
       - Otherwise, safely links the Google sub to the existing account.
    5. If no account exists, creates a new traveler user with password_hash=None.
    6. Ensures account is active (403 Forbidden if inactive).
    7. Returns standard CeylonGo JWT access token and user profile.
    """
    token_data = verify_google_id_token(request.id_token)
    google_sub = token_data["google_sub"]
    email = token_data["email"]
    full_name = token_data["full_name"]

    # 1. Lookup user by Google subject ID
    user = db.query(User).filter(User.google_sub == google_sub).first()

    if user:
        if is_admin_role(user.role):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials")
        if user.role == "provider":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Google authentication is not supported for provider accounts.",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive. Please contact support.",
            )
    else:
        # 2. Lookup existing user by verified email
        existing_user = db.query(User).filter(func.lower(User.email) == email).first()

        if existing_user:
            if is_admin_role(existing_user.role):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Could not validate credentials")
            if existing_user.role == "provider":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Google authentication is not supported for provider accounts.",
                )
            if not existing_user.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Account is inactive. Please contact support.",
                )
            if existing_user.google_sub and existing_user.google_sub != google_sub:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This email is already associated with another Google account.",
                )

            # Safely link Google account
            existing_user.google_sub = google_sub
            try:
                db.commit()
                db.refresh(existing_user)
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account conflict occurred during Google account linking.",
                )
            user = existing_user
        else:
            # 3. Create new traveler user (no local password)
            new_user = User(
                full_name=full_name,
                email=email,
                password_hash=None,
                role="traveler",
                is_active=True,
                google_sub=google_sub,
            )
            db.add(new_user)
            try:
                db.commit()
                db.refresh(new_user)
            except IntegrityError:
                db.rollback()
                # Handle concurrent registration race condition
                user = db.query(User).filter(User.google_sub == google_sub).first()
                if not user:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="An account conflict occurred during Google registration.",
                    )
            else:
                user = new_user

    # Linking and registration can refresh the row or resolve a concurrent insert.
    if is_admin_role(user.role):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    if user.role == "provider" or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Google authentication is not supported for provider accounts."
                if user.role == "provider"
                else "Account is inactive. Please contact support."
            ),
        )

    token = _issue_user_access_token(user)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get profile of the currently authenticated traveler",
)
def get_me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_forgot_password)],
    summary="Request a password reset link",
)
def forgot_password(
    request: ForgotPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Request a password reset link for the provided email address.

    Security Note (Anti-Enumeration):
    This endpoint returns an identical generic response whether the email exists
    in the database or not. If registered and active, a single-use token expiring
    in 30 minutes is created, its SHA-256 hash is saved to the database, and an email
    is safely dispatched via SMTP.
    """
    normalized_email = request.email.strip().lower()

    try:
        user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
        if user and user.is_active and user.role == "provider":
            # Enforce that incomplete provider onboarding cannot use password reset
            profile = db.query(ProviderProfile).filter_by(user_id=user.id).first()
            if not profile or profile.approval_status != "approved" or user.password_hash is None:
                user = None

        if user and user.is_active:
            # Invalidate any previously active unused tokens for this user
            db.query(PasswordReset).filter(
                PasswordReset.user_id == user.id,
                PasswordReset.is_used == False,
            ).update({"is_used": True}, synchronize_session=False)

            raw_token = generate_password_reset_token()
            token_hash = hash_reset_token(raw_token)
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30)

            reset_record = PasswordReset(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
                is_used=False,
            )
            db.add(reset_record)

            # Safely dispatch email via configured SMTP
            email_sent = send_password_reset_email(
                to_email=user.email,
                token=raw_token,
                user_name=user.full_name,
            )

            if email_sent:
                db.commit()
            else:
                # Do not commit unusable reset records when email delivery fails!
                db.rollback()
                logger.error(
                    "Password reset email delivery failed for user id %s. Rolling back reset token.",
                    user.id,
                )
    except SQLAlchemyError:
        db.rollback()
        logger.error("Database error occurred during password reset request.")

    return ForgotPasswordResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


@router.get(
    "/verify-reset-token",
    response_model=VerifyResetTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify if a password reset token is valid and unexpired",
    dependencies=[Depends(rate_limit_reset_verification)],
)
def verify_reset_token(
    token: Annotated[str, Query(min_length=1, description="Password reset token")],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Verify the validity, usage status, and expiration of a password reset token.
    """
    token_hash = hash_reset_token(token)
    reset_record = (
        db.query(PasswordReset).filter(PasswordReset.token_hash == token_hash).first()
    )

    if not reset_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    admin_recovery = user is not None and is_admin_role(user.role)
    if reset_record.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token." if admin_recovery
            else "This password reset token has already been used.",
        )

    now_utc = datetime.now(UTC).replace(tzinfo=None)
    record_expires = reset_record.expires_at
    if record_expires.tzinfo is not None:
        record_expires = record_expires.replace(tzinfo=None)

    if now_utc >= record_expires:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token." if admin_recovery
            else "This password reset token has expired.",
        )

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    if user.role == "provider":
        profile = db.query(ProviderProfile).filter_by(user_id=user.id).first()
        if not profile or profile.approval_status != "approved" or user.password_hash is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password reset is not available for this provider account.",
            )

    return VerifyResetTokenResponse(
        valid=True,
        email=user.email,
        message="Password reset token is valid.",
    )


@router.post(
    "/reset-password",
    response_model=ResetPasswordResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset password using a valid reset token",
    dependencies=[Depends(rate_limit_reset_execution)],
)
def reset_password(
    request: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Atomically update user password using Argon2 and consume the single-use reset token.
    """
    try:
        token_hash = hash_reset_token(request.token)
        reset_record = (
            db.query(PasswordReset).filter(PasswordReset.token_hash == token_hash).first()
        )

        if not reset_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired password reset token.",
            )

        user = db.query(User).filter(User.id == reset_record.user_id).first()
        admin_recovery = user is not None and is_admin_role(user.role)

        if reset_record.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid or expired password reset token."
                    if admin_recovery
                    else "This password reset token has already been used."
                ),
            )

        now_utc = datetime.now(UTC).replace(tzinfo=None)
        record_expires = reset_record.expires_at
        if record_expires.tzinfo is not None:
            record_expires = record_expires.replace(tzinfo=None)

        if now_utc >= record_expires:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid or expired password reset token."
                    if admin_recovery
                    else "This password reset token has expired."
                ),
            )

        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired password reset token.",
            )

        if user.role == "provider":
            profile = db.query(ProviderProfile).filter_by(user_id=user.id).first()
            if (
                not profile
                or profile.approval_status != "approved"
                or user.password_hash is None
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Password reset is not available for this provider account.",
                )

            try:
                validate_provider_password_strength(request.new_password)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=[{
                        "type": "value_error",
                        "loc": ["body", "new_password"],
                        "msg": str(exc),
                    }],
                ) from exc

        hashed_password = hash_password(request.new_password)

        redeemed_at = datetime.now(UTC).replace(tzinfo=None)
        claimed = db.query(PasswordReset).filter(
            PasswordReset.id == reset_record.id,
            PasswordReset.is_used == false(),
            PasswordReset.expires_at > redeemed_at,
        ).update(
            {"is_used": True, "used_at": redeemed_at}, synchronize_session=False
        )
        if claimed != 1:
            raise HTTPException(400, detail="Invalid or expired password reset token.")

        eligible_user = db.query(User).filter(
            User.id == user.id, User.is_active == true(), User.role == user.role
        )
        if user.role == "provider":
            approved_profile = select(ProviderProfile.id).where(
                ProviderProfile.user_id == User.id,
                ProviderProfile.approval_status == "approved",
            ).exists()
            eligible_user = eligible_user.filter(
                User.password_hash.is_not(None), approved_profile
            )
        updated = eligible_user.update(
            {
                "password_hash": hashed_password,
                "credential_version": User.credential_version + 1,
            },
            synchronize_session=False,
        )
        if updated != 1:
            raise HTTPException(400, detail="Invalid or expired password reset token.")

        db.query(PasswordReset).filter(
            PasswordReset.user_id == user.id,
            PasswordReset.is_used == false(),
        ).update({"is_used": True}, synchronize_session=False)

        db.commit()
        db.expire(user)
        db.expire(reset_record)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        logger.error("Database error occurred during password reset execution.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while resetting the password. Please try again later.",
        )

    return ResetPasswordResponse(
        message="Password has been reset successfully. You can now sign in with your new password."
    )
