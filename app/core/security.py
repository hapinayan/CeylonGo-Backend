import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash

load_dotenv()

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    if not hashed_password:
        return False
    return password_hash.verify(password, hashed_password)


def get_jwt_secret_key() -> str:
    secret_key = os.getenv("JWT_SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "Configuration error: JWT_SECRET_KEY environment variable is not set."
        )
    return secret_key


def get_jwt_algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def get_jwt_access_token_expire_minutes() -> int:
    expire_minutes_raw = os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    try:
        return int(expire_minutes_raw)
    except ValueError:
        raise RuntimeError(
            "Configuration error: JWT_ACCESS_TOKEN_EXPIRE_MINUTES must be an integer."
        )


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
    *,
    credential_version: int = 0,
) -> str:
    to_encode = data.copy()
    if expires_delta is not None:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=get_jwt_access_token_expire_minutes()
        )

    to_encode.update({"exp": expire, "credential_version": credential_version})
    secret_key = get_jwt_secret_key()
    algorithm = get_jwt_algorithm()

    return jwt.encode(to_encode, secret_key, algorithm=algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    secret_key = get_jwt_secret_key()
    algorithm = get_jwt_algorithm()
    return jwt.decode(token, secret_key, algorithms=[algorithm])


def get_access_token_credential_version(payload: dict[str, Any]) -> int:
    if "credential_version" in payload:
        version = payload["credential_version"]
        if type(version) is int and version >= 0:
            return version
        raise jwt.InvalidTokenError("Invalid credential version")

    # Legacy JWTs are accepted only until an explicitly configured UTC deadline.
    try:
        deadline = int(os.getenv("JWT_LEGACY_CREDENTIAL_VERSION_UNTIL", "0"))
    except ValueError:
        deadline = 0
    if datetime.now(UTC).timestamp() < deadline:
        return 0
    raise jwt.InvalidTokenError("Missing credential version")


def generate_password_reset_token() -> str:
    """Generate a cryptographically secure URL-safe token (32 bytes)."""
    return secrets.token_urlsafe(32)


def hash_reset_token(token: str) -> str:
    """Compute SHA-256 hex digest of the raw token for safe storage."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def create_sponsored_tracking_token(
    campaign_id: int,
    eco_experience_id: int,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Generate a tamper-evident, time-limited cryptographic token for a visible
    boosted sponsored placement. Forbids arbitrary provider-selected IDs or forgery.
    """
    if expires_delta is None:
        expires_delta = timedelta(hours=2)
    now = datetime.now(UTC)
    payload = {
        "sub": "sponsored_tracking",
        "campaign_id": campaign_id,
        "eco_experience_id": eco_experience_id,
        "placement_type": "boosted",
        "nonce": secrets.token_hex(16),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(
        payload, get_jwt_secret_key(), algorithm=get_jwt_algorithm()
    )


def verify_sponsored_tracking_token(token: str) -> dict[str, Any]:
    """
    Verify signature, expiration, and boosted placement claims of a tracking token.
    Raises ValueError on invalid token.
    """
    try:
        payload = jwt.decode(
            token,
            get_jwt_secret_key(),
            algorithms=[get_jwt_algorithm()],
        )
    except jwt.PyJWTError as exc:
        raise ValueError(f"Invalid tracking token: {str(exc)}") from exc

    if payload.get("sub") != "sponsored_tracking":
        raise ValueError("Invalid tracking token subject")
    if payload.get("placement_type") != "boosted":
        raise ValueError("Tracking only permitted for boosted sponsored placements")
    return payload
