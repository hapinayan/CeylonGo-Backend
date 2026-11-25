import hmac
from datetime import UTC, datetime, timedelta
from math import ceil

from fastapi import HTTPException, Request
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.email import send_traveler_otp_email
from app.core.otp import (
    generate_authorization_token,
    generate_six_digit_otp,
    hash_authorization_token,
    hash_otp_message,
)
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.core.security import hash_password
from app.models.mvp import MVPUser
from app.models.traveler_email_challenge import TravelerEmailChallenge
from app.models.user import User
from app.schemas.auth import (
    TravelerChallengeRequest,
    TravelerChallengeResponse,
    TravelerVerifyEmailRequest,
    TravelerVerifiedResponse,
)

OTP_VALIDITY = timedelta(minutes=5)
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_ATTEMPTS = 5
send_limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
verify_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)


def rate_limit_send(request: Request) -> None:
    send_limiter.check_peer(request, "traveler-otp-send")


def rate_limit_verify(request: Request) -> None:
    verify_limiter.check_peer(request, "traveler-otp-verify")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _otp_digest(email: str, registration_hash: str, otp: str) -> str:
    return hash_otp_message(f"traveler-registration:{registration_hash}:{email}:{otp}")


def _require_available_email(
    db: Session, user_model: type[User] | type[MVPUser], email: str
) -> None:
    if db.query(user_model.id).filter(func.lower(user_model.email) == email).first():
        raise HTTPException(409, "An account with this email already exists")


def _check_cooldown(challenge: TravelerEmailChallenge, now: datetime) -> None:
    remaining = ceil((challenge.sent_at + RESEND_COOLDOWN - now).total_seconds())
    if remaining > 0:
        raise _send_rate_error(remaining)


def _send_rate_error(retry_after: int = 60) -> HTTPException:
    return HTTPException(
        429,
        "Wait before requesting another verification code",
        headers={"Retry-After": str(retry_after)},
    )


def _deliver(email: str, registration_id: str, otp: str) -> TravelerChallengeResponse:
    if not send_traveler_otp_email(email, otp):
        raise HTTPException(
            503, "Verification email could not be sent", headers={"Retry-After": "60"}
        )
    return TravelerChallengeResponse(
        message="Verification code sent",
        email=email,
        registration_id=registration_id,
        expires_in=int(OTP_VALIDITY.total_seconds()),
        resend_after=int(RESEND_COOLDOWN.total_seconds()),
    )


def start_registration(
    db: Session,
    user_model: type[User] | type[MVPUser],
    *,
    email: str,
    full_name: str,
    password: str,
    phone: str | None = None,
) -> TravelerChallengeResponse:
    _require_available_email(db, user_model, email)
    challenge = db.get(TravelerEmailChallenge, email)
    if challenge:
        # SQL Server collations can consider distinct mailbox names equal.
        if challenge.email != email:
            raise HTTPException(409, "This email conflicts with an existing registration")
        _check_cooldown(challenge, _utc_now())

    registration_id = generate_authorization_token()
    registration_hash = hash_authorization_token(registration_id)
    otp = generate_six_digit_otp()
    if challenge and challenge.otp_hash:
        while _otp_digest(email, challenge.registration_hash, otp) == challenge.otp_hash:
            otp = generate_six_digit_otp()
    password_hash = hash_password(password)
    now = _utc_now()
    values = {
        "registration_hash": registration_hash,
        "otp_hash": _otp_digest(email, registration_hash, otp),
        "full_name": full_name,
        "phone": phone,
        "password_hash": password_hash,
        "sent_at": now,
        "attempts": 0,
    }
    try:
        if challenge:
            result = db.execute(
                update(TravelerEmailChallenge)
                .where(
                    TravelerEmailChallenge.email == email,
                    TravelerEmailChallenge.registration_hash == challenge.registration_hash,
                    TravelerEmailChallenge.sent_at <= now - RESEND_COOLDOWN,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                raise _send_rate_error()
        else:
            db.add(TravelerEmailChallenge(email=email, **values))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _send_rate_error() from None
    return _deliver(email, registration_id, otp)


def _invalid_code() -> HTTPException:
    return HTTPException(400, "Invalid or expired verification challenge")


def _get_challenge(
    db: Session, request: TravelerChallengeRequest
) -> TravelerEmailChallenge:
    challenge = db.get(TravelerEmailChallenge, request.email)
    digest = hash_authorization_token(request.registration_id)
    if (
        not challenge
        or challenge.email != request.email
        or not challenge.registration_hash
        or not hmac.compare_digest(challenge.registration_hash, digest)
    ):
        raise _invalid_code()
    return challenge


def _current_challenge(challenge: TravelerEmailChallenge):
    return (
        update(TravelerEmailChallenge)
        .where(
            TravelerEmailChallenge.email == challenge.email,
            TravelerEmailChallenge.registration_hash == challenge.registration_hash,
            TravelerEmailChallenge.otp_hash == challenge.otp_hash,
            TravelerEmailChallenge.sent_at == challenge.sent_at,
        )
        .execution_options(synchronize_session=False)
    )


def resend_verification(
    db: Session,
    user_model: type[User] | type[MVPUser],
    request: TravelerChallengeRequest,
) -> TravelerChallengeResponse:
    challenge = _get_challenge(db, request)
    email = challenge.email
    _require_available_email(db, user_model, email)
    now = _utc_now()
    _check_cooldown(challenge, now)
    otp = generate_six_digit_otp()
    digest = _otp_digest(email, challenge.registration_hash, otp)
    while hmac.compare_digest(digest, challenge.otp_hash):
        otp = generate_six_digit_otp()
        digest = _otp_digest(email, challenge.registration_hash, otp)
    result = db.execute(
        _current_challenge(challenge).values(otp_hash=digest, sent_at=now, attempts=0)
    )
    if result.rowcount != 1:
        db.rollback()
        raise _send_rate_error()
    db.commit()
    return _deliver(email, request.registration_id, otp)


def verify_registration(
    db: Session,
    user_model: type[User] | type[MVPUser],
    request: TravelerVerifyEmailRequest,
) -> TravelerVerifiedResponse:
    challenge = _get_challenge(db, request)
    now = _utc_now()
    digest = _otp_digest(challenge.email, challenge.registration_hash, request.otp)
    current = _current_challenge(challenge).where(
        TravelerEmailChallenge.sent_at > now - OTP_VALIDITY,
        TravelerEmailChallenge.attempts < MAX_ATTEMPTS,
    )
    if not hmac.compare_digest(digest, challenge.otp_hash):
        db.execute(current.values(attempts=TravelerEmailChallenge.attempts + 1))
        db.commit()
        raise _invalid_code()

    consumed = db.execute(
        current.values(
            registration_hash=None,
            otp_hash=None,
            full_name=None,
            password_hash=None,
            phone=None,
        )
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise _invalid_code()

    details = {
        "email": challenge.email,
        "full_name": challenge.full_name,
        "role": "traveler",
        "is_active": True,
    }
    if user_model is MVPUser:
        user = MVPUser(
            **details, hashed_password=challenge.password_hash, phone=challenge.phone
        )
    else:
        user = User(**details, password_hash=challenge.password_hash)
    try:
        _require_available_email(db, user_model, challenge.email)
        db.add(user)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "An account with this email already exists") from None
    return TravelerVerifiedResponse(message="Email verified. You can now log in.")
