
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.core.homestay_permissions import require_homestay_manager


@pytest.mark.parametrize(
    "role",
    ["owner", "admin"],
)
def test_owner_and_admin_are_allowed(role):
    user = Mock()
    user.role = role

    result = require_homestay_manager(user)

    assert result is user


@pytest.mark.parametrize(
    "role",
    ["traveler", "guest", ""],
)
def test_other_roles_are_forbidden(role):
    user = Mock()
    user.role = role

    with pytest.raises(HTTPException) as exc_info:
        require_homestay_manager(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == (
        "Owner or admin access required"
    )
