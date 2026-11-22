"""
Centralized, reusable Admin Audit Logging helper for CeylonGo Admin Management (P1 Stage 3).

Reuses the existing dbo.admin_audit_logs table and AdminAuditLog SQLAlchemy model:
- id (INT IDENTITY PRIMARY KEY)
- admin_user_id (INT NOT NULL FK -> dbo.users.id)
- action / action_type (NVARCHAR(50) NOT NULL)
- entity_name / entity_type (NVARCHAR(50) NOT NULL)
- entity_id (INT NOT NULL)
- details (NVARCHAR(MAX) NULL)
- ip_address (NVARCHAR(45) NULL)
- created_at (DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME())

Security Rules:
- Never stores passwords, password hashes, JWT tokens, reset tokens, OTPs, payment secrets,
  or raw identity document contents.
- Enforces valid integer admin_user_id and entity_id (> 0) to respect NOT NULL and FK constraints.
- Adds the AdminAuditLog entry to the caller's active SQLAlchemy Session without committing
  independently, ensuring atomic commit/rollback with the underlying business mutation.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence, Set
from datetime import date, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.shared import AdminAuditLog

SENSITIVE_AUDIT_KEYS: frozenset[str] = frozenset({
    "password",
    "password_hash",
    "new_password",
    "old_password",
    "token",
    "access_token",
    "refresh_token",
    "reset_token",
    "token_hash",
    "authorization",
    "otp",
    "otp_code",
    "otp_hash",
    "secret",
    "client_secret",
    "merchant_secret",
    "google_sub",
    "driver_license_number",
    "license_number",
    "nic_number",
    "passport_number",
})

_CREDENTIAL_TEXT = re.compile(
    r"\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]{8,}"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
    r"|\$argon2(?:id|i|d)\$|\$2[aby]\$"
    r"|\b(?:password(?:[ _-]hash)?|passphrase|(?:access[ _-]|refresh[ _-]|reset[ _-])?token|"
    r"authorization|api[ _-]?key|(?:client[ _-]|merchant[ _-])?secret|otp(?:[ _-]code)?)"
    r"[\"']?\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_PERSONAL_NOTE_TEXT = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
    r"|\b(?:passport|(?:driver[’']?s?\s+)?licen[cs]e|nic|phone|national\s+id)"
    r"(?:\s+(?:number|no\.?))?\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def contains_unsafe_admin_note(text: str) -> bool:
    return bool(_CREDENTIAL_TEXT.search(text) or _PERSONAL_NOTE_TEXT.search(text))


def extract_client_ip(request: Request | None) -> str | None:
    """Safely extract and bound the client IP address (max 45 chars for IPv6/IPv4) from a FastAPI Request."""
    if request is None or request.client is None or not request.client.host:
        return None
    host = str(request.client.host).strip()
    return host[:45] if host else None


def sanitize_audit_details(details: Any) -> str | None:
    """Sanitize audit metadata and serialize it for storage."""
    safe = redact_audit_details(details)
    if safe is None or isinstance(safe, str):
        return safe
    return json.dumps(safe, allow_nan=False)


def redact_audit_details(details: Any) -> dict[str, Any] | str | None:
    """Return the audit API's object, string, or null representation."""
    safe = _redact_value(details, depth=0)
    if safe is None or isinstance(safe, (dict, str)):
        return safe
    return json.dumps(safe, allow_nan=False)


def _redact_value(value: Any, *, depth: int) -> Any:
    # Bound recursion for cyclic objects and deeply nested JSON.
    if depth >= 64:
        return None
    if isinstance(value, Mapping):
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            normalized = key.strip().lower()
            if normalized in SENSITIVE_AUDIT_KEYS or any(
                marker in normalized for marker in ("password", "secret", "token_hash", "otp_hash")
            ):
                continue
            cleaned[key] = _redact_value(item, depth=depth + 1)
        return cleaned
    if isinstance(value, str):
        if depth > 0:
            return "[REDACTED]" if _CREDENTIAL_TEXT.search(value) else value
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.startswith(("{", "[")):
            try:
                parsed = json.loads(candidate)
            except (ValueError, RecursionError):
                return None
            return _redact_value(parsed, depth=depth)
        return "[REDACTED]" if _CREDENTIAL_TEXT.search(candidate) else candidate
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if isinstance(value, (Sequence, Set)):
        return [_redact_value(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, time, Decimal, UUID)):
        return str(value)
    return None


def record_admin_audit_log(
    db: Session,
    *,
    admin_user_id: int,
    action: str,
    entity_name: str,
    entity_id: int,
    details: Any = None,
    ip_address: str | None = None,
    request: Request | None = None,
) -> AdminAuditLog:
    """
    Stage an AdminAuditLog row inside the caller's active database transaction.

    Does NOT call db.commit(), so the audit log commits or rolls back atomically
    with the caller's business state mutation.
    """
    if not isinstance(admin_user_id, int) or admin_user_id <= 0:
        raise ValueError("admin_user_id must be a positive integer")
    if not isinstance(entity_id, int) or entity_id <= 0:
        raise ValueError("entity_id must be a positive integer")

    clean_action = (action or "").strip()[:50]
    clean_entity = (entity_name or "").strip()[:50]
    if not clean_action:
        raise ValueError("action must be a non-empty string")
    if not clean_entity:
        raise ValueError("entity_name must be a non-empty string")

    resolved_ip = ip_address
    if resolved_ip is None and request is not None:
        resolved_ip = extract_client_ip(request)
    if resolved_ip is not None:
        resolved_ip = str(resolved_ip).strip()[:45] or None

    entry = AdminAuditLog(
        admin_user_id=admin_user_id,
        action=clean_action,
        entity_name=clean_entity,
        entity_id=entity_id,
        details=sanitize_audit_details(details),
        ip_address=resolved_ip,
    )
    db.add(entry)
    return entry
