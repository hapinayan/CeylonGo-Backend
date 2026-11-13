import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.email import send_password_reset_email
from app.core.rate_limiter import check_email_limiter, forgot_password_limiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_password_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.database.connection import get_db
from app.models.password_reset import PasswordReset
from app.models.user import User
from app.schemas.auth import (
    CheckEmailRequest,
    CheckEmailResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UserResponse,
    VerifyResetTokenResponse,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(
    scheme_name="HTTPBearer",
    description="JWT Bearer token. Enter your token in format: <token>",
    auto_error=True,
)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    token = credentials.credentials
    try:
        payload = decode_access_token(token)
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

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive. Please contact support.",
        )

    return user


def rate_limit_check_email(request: Request) -> None:
    check_email_limiter.check(request)


def rate_limit_forgot_password(request: Request) -> None:
    forgot_password_limiter.check(request)


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
        is_registered=user is not None,
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
)
def login(
    request: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
):
    normalized_email = request.email.strip().lower()

    # Query user by normalized email
    user = db.query(User).filter(func.lower(User.email) == normalized_email).first()

    # Verify user exists and password matches
    if not user or not verify_password(request.password, user.password_hash):
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
    token = create_access_token(data={"sub": str(user.id)})

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
        logger.exception("Database error occurred during password reset request.")

    return ForgotPasswordResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


@router.get(
    "/verify-reset-token",
    response_model=VerifyResetTokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify if a password reset token is valid and unexpired",
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

    if reset_record.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset token has already been used.",
        )

    now_utc = datetime.now(UTC).replace(tzinfo=None)
    record_expires = reset_record.expires_at
    if record_expires.tzinfo is not None:
        record_expires = record_expires.replace(tzinfo=None)

    if now_utc > record_expires:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset token has expired.",
        )

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
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
)
def reset_password(
    request: ResetPasswordRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Atomically update user password using Argon2 and consume the single-use reset token.
    """
    token_hash = hash_reset_token(request.token)
    reset_record = (
        db.query(PasswordReset).filter(PasswordReset.token_hash == token_hash).first()
    )

    if not reset_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    if reset_record.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset token has already been used.",
        )

    now_utc = datetime.now(UTC).replace(tzinfo=None)
    record_expires = reset_record.expires_at
    if record_expires.tzinfo is not None:
        record_expires = record_expires.replace(tzinfo=None)

    if now_utc > record_expires:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset token has expired.",
        )

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token.",
        )

    hashed_password = hash_password(request.new_password)

    try:
        user.password_hash = hashed_password
        reset_record.is_used = True
        reset_record.used_at = now_utc

        db.query(PasswordReset).filter(
            PasswordReset.user_id == user.id,
            PasswordReset.is_used == False,
        ).update({"is_used": True}, synchronize_session=False)

        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database error occurred during password reset execution.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while resetting the password. Please try again later.",
        )

    return ResetPasswordResponse(
        message="Password has been reset successfully. You can now sign in with your new password."
    )
