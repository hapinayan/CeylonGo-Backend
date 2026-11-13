import logging
import os
from typing import Any

from fastapi import HTTPException, status
from google.auth.exceptions import GoogleAuthError
from google.auth.transport import requests
from google.oauth2 import id_token

logger = logging.getLogger(__name__)


def get_google_client_id() -> str:
    """Retrieve Google OAuth Client ID from environment configuration."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    if not client_id or not client_id.strip():
        raise RuntimeError(
            "Configuration error: GOOGLE_CLIENT_ID environment variable is not set."
        )
    return client_id.strip()


def verify_google_id_token(
    token_str: str, client_id: str | None = None
) -> dict[str, Any]:
    """
    Verify and decode a Google ID token from Google Identity Services.
    Validates cryptographic signature, expiry, audience, issuer, and email verification.
    Returns verified payload dict with normalized user attributes.
    """
    target_audience = client_id or get_google_client_id()

    try:
        request = requests.Request()
        payload = id_token.verify_oauth2_token(
            token_str, request, audience=target_audience
        )
    except ValueError as exc:
        logger.warning("Google ID token validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google ID token: {exc}",
        ) from exc
    except GoogleAuthError as exc:
        logger.warning("Google authentication error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google authentication failed: {exc}",
        ) from exc

    # Validate Google token issuer
    issuer = payload.get("iss")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google ID token issuer.",
        )

    # Validate sub (Google stable unique user ID)
    google_sub = payload.get("sub")
    if not google_sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google ID token is missing subject (sub) identifier.",
        )

    # Validate email
    email = payload.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google ID token does not contain an email address.",
        )

    # Strict check: Never link or authenticate unverified emails
    email_verified = payload.get("email_verified")
    if email_verified is not True and str(email_verified).lower() != "true":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google account email is not verified.",
        )

    full_name = payload.get("name") or payload.get("given_name")
    if not full_name or not full_name.strip():
        full_name = email.split("@")[0].replace(".", " ").title()

    return {
        "google_sub": str(google_sub).strip(),
        "email": email.strip().lower(),
        "full_name": full_name.strip(),
        "picture": payload.get("picture"),
    }
