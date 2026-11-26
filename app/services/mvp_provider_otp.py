"""Email ownership challenges for the Academic MVP provider workflow."""

from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from math import ceil

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.email import get_provider_otp_expire_minutes, send_provider_otp_email
from app.core.otp import (
    generate_authorization_token,
    generate_six_digit_otp,
    hash_authorization_token,
    hash_otp_message,
)
from app.models.mvp import MVPProviderEmailChallenge, MVPProviderProfile, MVPUser
from app.schemas.provider_otp import SendOtpResponse, VerifyOtpResponse

C = MVPProviderEmailChallenge
PASSWORD_UNSET = "!provider-password-unset"
AUTH_LIFETIME = 1800


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(email, otp):
    return bytes.fromhex(hash_otp_message(f"mvp-provider-email:{email}:{otp}"))


def _account(db, email):
    row = db.execute(
        select(MVPUser, MVPProviderProfile)
        .outerjoin(MVPProviderProfile, MVPProviderProfile.user_id == MVPUser.id)
        .where(func.lower(MVPUser.email) == email)
    ).one_or_none()
    if row and row[0].role != "provider":
        raise HTTPException(409, "This email is already registered for a different account")
    return row if row else (None, None)


def _latest(db: Session, email: str, *, lock: bool = False):
    query = select(C).where(C.email == email).order_by(C.id.desc()).limit(1)
    if lock:
        query = query.with_hint(C, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
    return db.scalar(query)


def send(db, email):
    user, profile = _account(db, email)
    latest = _latest(db, email, lock=True)
    now = utc_now()
    if latest:
        remaining = ceil((latest.created_at + timedelta(seconds=60) - now).total_seconds())
        if remaining > 0:
            raise HTTPException(429, "Wait before requesting another code",
                                headers={"Retry-After": str(remaining)})
        latest.consumed_at = now
    code = generate_six_digit_otp()
    while latest and latest.verified_at is None and compare_digest(
        latest.challenge_hash, _hash(email, code)
    ):
        code = generate_six_digit_otp()
    db.add(C(email=email, challenge_hash=_hash(email, code), created_at=now,
             expires_at=now + timedelta(minutes=get_provider_otp_expire_minutes()), attempts=0))
    db.commit()
    purpose = "registration"
    if profile and profile.verification_status == "rejected":
        purpose = "resubmission"
    if profile and profile.verification_status == "approved" and user.hashed_password == PASSWORD_UNSET:
        purpose = "password_setup"
    if not send_provider_otp_email(email, code, purpose=purpose):
        raise HTTPException(503, "Verification email could not be sent",
                            headers={"Retry-After": "60"})
    return SendOtpResponse(email=email, message="Verification code sent successfully.")


def verify(db, email, code):
    latest = _latest(db, email, lock=True)
    now = utc_now()
    invalid = HTTPException(400, "Invalid or expired verification code")
    if (
        not latest or latest.email != email or latest.consumed_at
        or latest.verified_at or latest.expires_at <= now or latest.attempts >= 5
    ):
        raise invalid
    if not compare_digest(latest.challenge_hash, _hash(email, code)):
        latest.attempts += 1
        db.commit()
        raise invalid
    user, profile = _account(db, email)
    action = "continue_registration"
    if profile:
        action = {
            "pending": "application_pending",
            "rejected": "resubmit_application",
            "suspended": "login_required",
            "approved": (
                "create_password" if user.hashed_password == PASSWORD_UNSET else "login_required"
            ),
        }.get(profile.verification_status, action)
    token = None
    latest.verified_at = now
    if action in {"continue_registration", "resubmit_application", "create_password"}:
        token = generate_authorization_token()
        latest.challenge_hash = bytes.fromhex(hash_authorization_token(token))
        latest.expires_at = now + timedelta(seconds=AUTH_LIFETIME)
    else:
        latest.consumed_at = now
    db.commit()
    return VerifyOtpResponse(
        verified=True, next_action=action, authorization_token=token,
        expires_in=AUTH_LIFETIME if token else None,
        message={
            "application_pending": "Your application is pending review.",
            "login_required": "Use provider login if your account is active.",
        }.get(action, "Email verified successfully."),
    )


def authorize(db: Session, token: str, *, lock: bool = False):
    if len(token) != 43:
        raise HTTPException(401, "Invalid or expired provider registration authorization")
    digest = bytes.fromhex(hash_authorization_token(token))
    query = select(C).where(
        C.challenge_hash == digest, C.verified_at.is_not(None),
        C.consumed_at.is_(None), C.expires_at > utc_now(),
    )
    if lock:
        query = query.with_hint(C, "WITH (UPDLOCK)", dialect_name="mssql")
    challenge = db.scalar(query)
    if not challenge or not compare_digest(challenge.challenge_hash, digest):
        raise HTTPException(401, "Invalid or expired provider registration authorization")
    return challenge, *_account(db, challenge.email)


def consume(db, challenge):
    consumed = db.execute(update(C).where(
        C.id == challenge.id, C.consumed_at.is_(None), C.expires_at > utc_now(),
    ).values(consumed_at=utc_now()))
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(401, "Invalid or expired provider registration authorization")
