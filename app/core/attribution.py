"""
Advertising Campaign Attribution Token Service (Phase 12)
========================================================
Cryptographically signed tracking tokens for homestay advertising campaigns.
Ensures conversions are attributed only to authentic, unexpired campaigns
associated with the exact advertised listing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from app.core.security import get_jwt_secret_key


def _get_signing_key() -> bytes:
    try:
        key = get_jwt_secret_key()
    except Exception:
        key = "ceylongo-secure-attribution-key"
    return key.encode("utf-8")


def generate_attribution_token(
    campaign_id: int,
    homestay_id: int,
    valid_hours: int = 48,
) -> str:
    """
    Generate an HMAC-SHA256 signed tracking token for an eligible campaign and homestay listing.
    """
    now = int(time.time())
    payload = {
        "cid": campaign_id,
        "hid": homestay_id,
        "iat": now,
        "exp": now + (valid_hours * 3600),
    }
    raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    payload_b64 = base64.urlsafe_b64encode(raw_json.encode("utf-8")).decode("utf-8").rstrip("=")

    sig = hmac.new(_get_signing_key(), payload_b64.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def verify_attribution_token(token: str | None, homestay_id: int) -> int | None:
    """
    Validate token signature, expiration timestamp, and target listing match.
    Returns the verified campaign_id if valid, or None if invalid/expired/mismatched.
    """
    if not token or not isinstance(token, str) or "." not in token:
        return None

    try:
        payload_b64, sig = token.strip().split(".", 1)
        padding = 4 - (len(payload_b64) % 4)
        if padding != 4:
            payload_b64_padded = payload_b64 + ("=" * padding)
        else:
            payload_b64_padded = payload_b64

        expected_sig = hmac.new(
            _get_signing_key(), payload_b64.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(sig, expected_sig):
            return None

        data: dict[str, Any] = json.loads(
            base64.urlsafe_b64decode(payload_b64_padded.encode("utf-8")).decode("utf-8")
        )

        # Context validation: Homestay listing must match exactly
        if data.get("hid") != homestay_id:
            return None

        # Expiration validation
        now = int(time.time())
        if data.get("exp", 0) < now:
            return None

        campaign_id = data.get("cid")
        if not campaign_id or not isinstance(campaign_id, int):
            return None

        return campaign_id
    except Exception:
        return None
