from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.rate_limiter import password_reset_limiter
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_password_reset_token,
    get_password_reset_token_expire_minutes,
    hash_password,
    hash_token,
    verify_password,
)
from app.database.connection import get_db
from app.models.user import PasswordResetToken, Role, User, UserRole
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    TokenResponse,
    UserResponse,
)
from app.services.email_service import email_service

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])

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

    # Validate that the Tourist role exists rather than silently assigning an unknown role
    tourist_role = (
        db.query(Role)
        .filter(func.lower(Role.role_name) == "tourist")
        .first()
    )
    if not tourist_role:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Default registration role 'Tourist' is not configured in the database",
        )

    # Hash the password with Argon2
    hashed_pwd = hash_password(request.password)

    # Always enforce the traveler (Tourist) role on the backend
    new_user = User(
        full_name=trimmed_full_name,
        email=normalized_email,
        password_hash=hashed_pwd,
        is_active=True,
    )
    new_user_role = UserRole(role_id=tourist_role.role_id)
    new_user.user_role_links.append(new_user_role)

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
    except Exception:
        db.rollback()
        raise

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
    summary="Request a password reset link",
)
def forgot_password(
    request: ForgotPasswordRequest,
    fastapi_request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    client_ip = (
        fastapi_request.headers.get("x-forwarded-for")
        or (fastapi_request.client.host if fastapi_request.client else "unknown")
    ).split(",")[0].strip()

    # Rate limiting protection per IP and per email
    if password_reset_limiter.is_rate_limited(
        f"ip:{client_ip}"
    ) or password_reset_limiter.is_rate_limited(f"email:{request.email}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many password reset requests. Please wait a few minutes before trying again.",
        )

    normalized_email = request.email.strip().lower()

    # Query user by normalized email
    user = (
        db.query(User).filter(func.lower(User.email) == normalized_email).first()
    )

    # Anti-enumeration: Return the same response whether user exists or not
    if user and user.is_active:
        # 1. Invalidate any older active tokens for this user
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.is_used == False,  # noqa: E712
        ).update({"is_used": True})

        # 2. Generate cryptographically secure token and its SHA-256 hash
        raw_token = generate_password_reset_token()
        token_hash = hash_token(raw_token)
        expire_minutes = get_password_reset_token_expire_minutes()
        now_naive = datetime.now(UTC).replace(tzinfo=None)
        expires_at = now_naive + timedelta(minutes=expire_minutes)

        # 3. Store record in database
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            is_used=False,
        )
        db.add(reset_token)
        db.commit()

        # 4. Enqueue email delivery asynchronously (BackgroundTasks)
        background_tasks.add_task(
            email_service.send_password_reset_email,
            user.email,
            raw_token,
            expire_minutes,
        )

    return ForgotPasswordResponse()


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
    token_hash = hash_token(request.token)

    record = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == token_hash)
        .first()
    )

    now_naive = datetime.now(UTC).replace(tzinfo=None)

    # Validate token existence, single-use, and expiration
    if not record or record.is_used or record.expires_at < now_naive:
        # If token exists but expired, mark used to clean up
        if record and not record.is_used and record.expires_at < now_naive:
            record.is_used = True
            db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    # Query associated user
    user = db.query(User).filter(User.id == record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )

    # Perform atomic transactional password update and token invalidation
    new_hash = hash_password(request.password)
    user.password_hash = new_hash
    record.is_used = True
    record.used_at = now_naive

    # Invalidate any other active tokens for this user
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.is_used == False,  # noqa: E712
    ).update({"is_used": True})

    db.commit()

    return ResetPasswordResponse()
