from datetime import timedelta
from typing import Any

from app.core.security import ADMIN_AUTH_METHOD, create_access_token


def create_fixture_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Issue a token for a freshly seeded account at the schema's default version."""
    return create_access_token({"auth_version": 0, "auth_method": ADMIN_AUTH_METHOD, **data}, expires_delta)
