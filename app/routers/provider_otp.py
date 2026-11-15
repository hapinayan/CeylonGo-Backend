import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.email import send_provider_otp_email
from app.core.otp import (
    generate_authorization_token,
    generate_six_digit_otp,
    hash_authorization_token,
    hash_provider_otp,
    verify_provider_otp,
)
from app.core.rate_limiter import (
    provider_otp_send_limiter,
    provider_otp_verify_limiter,
)
from app.database.connection import get_db
from app.models.provider_email_otp import ProviderEmailOtp
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.user import User
from app.schemas.provider_otp import (
    SendOtpRequest,
    SendOtpResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/providers/otp", tags=["Provider OTP"])


def rate_limit_send_otp(request: Request) -> None:
    provider_otp_send_limiter.check(request)


def rate_limit_verify_otp(request: Request) -> None:
    provider_otp_verify_limiter.check(request)


def get_otp_expire_minutes() -> int:
    try:
        return int(os.getenv("PROVIDER_OTP_EXPIRE_MINUTES", "5"))
    except ValueError:
        return 5


def get_otp_max_attempts() -> int:
    try:
        return int(os.getenv("PROVIDER_OTP_MAX_ATTEMPTS", "5"))
    except ValueError:
        return 5


def get_otp_resend_cooldown_seconds() -> int:
    try:
        return int(os.getenv("PROVIDER_OTP_RESEND_COOLDOWN_SECONDS", "60"))
    except ValueError:
        return 60


def get_auth_token_expire_minutes() -> int:
    try:
        return int(os.getenv("PROVIDER_AUTH_TOKEN_EXPIRE_MINUTES", "30"))
    except ValueError:
        return 30


@router.post(
    "/send",
    response_model=SendOtpResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_send_otp)],
    summary="Send an email verification code for provider onboarding",
)
def send_provider_otp(
    request: SendOtpRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Generate and dispatch a cryptographically secure 6-digit OTP for Service Provider onboarding.

    Workflow & Security Rules:
    1. Normalizes email to lowercase.
    2. Inspects existing account state to determine purpose:
       - Unregistered email -> 'registration'
       - Rejected provider -> 'resubmission'
       - Approved provider without password -> 'password_setup'
       - Traveler account -> rejected with 409 Conflict (isolated account types)
       - Pending provider -> rejected with 400 Bad Request (under review)
       - Active provider with password -> rejected with 400 Bad Request (redirect to login)
    3. Enforces a 60-second resend cooldown per email.
    4. Invalidates any existing unconsumed OTPs for the same email and purpose.
    5. Stores only a keyed cryptographic HMAC-SHA256 digest of the OTP (never plaintext).
    6. Sends branded email via SMTP; aborts transaction with rollback if delivery fails.
    """
    email = request.email.strip().lower()

    # 1. Determine account state and OTP purpose
    user = (
        db.query(User).filter(func.lower(User.email) == email).first()
    )

    if user:
        if user.role == "traveler":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This email is already registered as a traveler account. A single email cannot be registered for both account types.",
            )
        if user.role == "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin accounts cannot register as service providers.",
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
                if profile.approval_status == "approved":
                    if user.password_hash is not None:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Your provider account is already active. Please log in.",
                        )
                    purpose = "password_setup"
                elif profile.approval_status == "rejected":
                    purpose = "resubmission"
                else:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid provider application state.",
                    )
            else:
                purpose = "registration"
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid account role for service provider onboarding.",
            )
    else:
        purpose = "registration"

    now = datetime.now(UTC)

    # 2. Check Resend Cooldown
    cooldown_seconds = get_otp_resend_cooldown_seconds()
    recent_otp = (
        db.query(ProviderEmailOtp)
        .filter(
            ProviderEmailOtp.email == email,
            ProviderEmailOtp.is_used == False,
        )
        .order_by(ProviderEmailOtp.created_at.desc())
        .first()
    )

    if recent_otp and recent_otp.created_at:
        created_at = recent_otp.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        elapsed = (now - created_at).total_seconds()
        if elapsed < cooldown_seconds:
            retry_after = max(1, int(cooldown_seconds - elapsed))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Please wait {retry_after} second(s) before requesting another verification code.",
                headers={"Retry-After": str(retry_after)},
            )

    # 3. Invalidate older active OTPs for the same email and purpose
    db.query(ProviderEmailOtp).filter(
        ProviderEmailOtp.email == email,
        ProviderEmailOtp.purpose == purpose,
        ProviderEmailOtp.is_used == False,
    ).update({"is_used": True})

    # 4. Generate OTP and keyed digest
    otp = generate_six_digit_otp()
    otp_hash = hash_provider_otp(email, otp)
    expire_minutes = get_otp_expire_minutes()
    expires_at = now + timedelta(minutes=expire_minutes)
    max_attempts = get_otp_max_attempts()

    new_otp = ProviderEmailOtp(
        email=email,
        otp_hash=otp_hash,
        purpose=purpose,
        expires_at=expires_at,
        attempts_count=0,
        max_attempts=max_attempts,
        is_used=False,
    )
    db.add(new_otp)
    db.flush()

    # 5. Dispatch email via SMTP with transactional rollback on failure
    email_sent = send_provider_otp_email(
        to_email=email,
        otp=otp,
        purpose=purpose,
        user_name=user.full_name if user else None,
    )

    if not email_sent:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to deliver verification email. Please try again later.",
        )

    db.commit()

    return SendOtpResponse(
        message="Verification code sent successfully.",
        email=email,
    )


@router.post(
    "/verify",
    response_model=VerifyOtpResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_verify_otp)],
    summary="Verify an email OTP and return appropriate next action and authorization token",
)
def verify_provider_otp_endpoint(
    request: VerifyOtpRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Verify submitted 6-digit OTP and issue a short-lived, single-use authorization token.

    Workflow & Security Rules:
    1. Normalizes email and verifies OTP against unexpired, unconsumed records.
    2. Protects against race conditions with row locking.
    3. Increments failed attempts counter atomically; invalidates code once max attempts (5) are reached.
    4. Marks successfully verified OTP as used.
    5. Rechecks live account status:
       - Traveler: 403 Forbidden (no authorization granted).
       - Pending Provider: Returns application_pending status (no authorization token).
       - Active Provider with Password: Directs to login (no authorization token).
       - New Email: Next action 'continue_registration', returns temporary authorization token.
       - Rejected Provider: Next action 'resubmit_application', returns temporary authorization token.
       - Approved Provider without Password: Next action 'create_password', returns temporary authorization token.
    6. Generates a 32-byte secure authorization token, stores only its SHA-256 hash, and revokes older tokens.
    """
    email = request.email.strip().lower()
    submitted_otp = request.otp.strip()
    now = datetime.now(UTC)

    # 1. Query current valid (unused, unexpired) OTP for this email
    otp_query = (
        db.query(ProviderEmailOtp)
        .filter(
            ProviderEmailOtp.email == email,
            ProviderEmailOtp.is_used == False,
            ProviderEmailOtp.expires_at > now,
        )
        .order_by(ProviderEmailOtp.created_at.desc())
    )

    try:
        otp_record = otp_query.with_for_update().first()
    except SQLAlchemyError:
        otp_record = otp_query.first()

    if not otp_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code.",
        )

    # 2. Check maximum attempts
    if otp_record.attempts_count >= otp_record.max_attempts:
        otp_record.is_used = True
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum verification attempts exceeded. Please request a new code.",
        )

    # 3. Verify OTP cryptographic digest
    is_valid = verify_provider_otp(email, submitted_otp, otp_record.otp_hash)
    if not is_valid:
        otp_record.attempts_count += 1
        if otp_record.attempts_count >= otp_record.max_attempts:
            otp_record.is_used = True
        db.commit()

        remaining = max(0, otp_record.max_attempts - otp_record.attempts_count)
        if remaining == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum verification attempts exceeded. Please request a new code.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid verification code. {remaining} attempt(s) remaining.",
        )

    # 4. Mark successful OTP as consumed
    otp_record.is_used = True
    otp_record.used_at = now

    # 5. Recheck live account status before issuing authorization
    user = (
        db.query(User).filter(func.lower(User.email) == email).first()
    )

    if user:
        if user.role == "traveler":
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Traveler accounts cannot receive provider registration authorization.",
            )
        if user.role == "admin":
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin accounts cannot receive provider registration authorization.",
            )

        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == user.id)
            .first()
        )

        if profile:
            if profile.approval_status == "pending":
                db.commit()
                return VerifyOtpResponse(
                    verified=True,
                    next_action="application_pending",
                    authorization_token=None,
                    expires_in=None,
                    message="Your provider application is currently pending admin review.",
                )
            if profile.approval_status == "approved":
                if user.password_hash is not None:
                    db.commit()
                    return VerifyOtpResponse(
                        verified=True,
                        next_action="login_required",
                        authorization_token=None,
                        expires_in=None,
                        message="Your provider account is active. Please proceed to login.",
                    )
                next_action = "create_password"
                purpose = "password_setup"
            elif profile.approval_status == "rejected":
                next_action = "resubmit_application"
                purpose = "resubmission"
            else:
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid application status.",
                )
        else:
            next_action = "continue_registration"
            purpose = "registration"
    else:
        next_action = "continue_registration"
        purpose = "registration"

    # 6. Issue temporary authorization token
    raw_auth_token = generate_authorization_token()
    token_hash = hash_authorization_token(raw_auth_token)
    auth_expire_minutes = get_auth_token_expire_minutes()
    auth_expires_at = now + timedelta(minutes=auth_expire_minutes)

    # Invalidate older active authorizations for this email and purpose
    db.query(ProviderRegistrationAuthorization).filter(
        ProviderRegistrationAuthorization.email == email,
        ProviderRegistrationAuthorization.purpose == purpose,
        ProviderRegistrationAuthorization.is_consumed == False,
        ProviderRegistrationAuthorization.is_revoked == False,
    ).update({"is_revoked": True, "revoked_at": now})

    new_auth = ProviderRegistrationAuthorization(
        email=email,
        token_hash=token_hash,
        purpose=purpose,
        expires_at=auth_expires_at,
        is_consumed=False,
        is_revoked=False,
    )
    db.add(new_auth)
    db.commit()

    return VerifyOtpResponse(
        verified=True,
        next_action=next_action,
        authorization_token=raw_auth_token,
        expires_in=auth_expire_minutes * 60,
        message="Email verified successfully.",
    )
