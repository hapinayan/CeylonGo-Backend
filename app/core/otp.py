import hashlib
import hmac
import secrets

from app.core.security import get_jwt_secret_key


def generate_six_digit_otp() -> str:
    """Generate a cryptographically secure 6-digit numeric OTP (000000-999999)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_provider_otp(email: str, otp: str) -> str:
    """
    Compute a keyed HMAC-SHA256 digest of the OTP bound to the recipient's normalized email.
    
    Protects against precomputed dictionary/rainbow attacks and prevents raw OTP storage.
    """
    secret = get_jwt_secret_key().encode()
    message = f"{email.strip().lower()}:{otp.strip()}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_provider_otp(email: str, otp: str, stored_hash: str) -> bool:
    """Verify submitted OTP against stored hash using constant-time comparison."""
    if not otp or not stored_hash:
        return False
    computed_hash = hash_provider_otp(email, otp)
    return hmac.compare_digest(computed_hash, stored_hash)


def generate_authorization_token() -> str:
    """Generate a cryptographically secure URL-safe token (32 bytes)."""
    return secrets.token_urlsafe(32)


def hash_authorization_token(token: str) -> str:
    """Compute SHA-256 hex digest of the raw authorization token for safe storage."""
    return hashlib.sha256(token.strip().encode()).hexdigest()

