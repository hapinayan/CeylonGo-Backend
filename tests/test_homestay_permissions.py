
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.core.homestay_permissions import require_homestay_manager


@pytest.mark.parametrize(
    "role",
    ["owner", "admin", "provider"],
)
def test_owner_and_admin_are_allowed(role):
    user = Mock()
    user.role = role
    user.is_active = True
    user.id = 1

    db = Mock()
    profile = Mock()
    profile.approval_status = "approved"
    profile.provider_type = "homestay"
    profile.is_active = True
    db.query.return_value.filter.return_value.first.return_value = profile

    result = require_homestay_manager(user, db=db)

    assert result is user


def test_legacy_owner_without_profile_is_denied():
    user = Mock()
    user.role = "owner"
    user.is_active = True
    user.id = 1

    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None

    with pytest.raises(HTTPException) as exc_info:
        require_homestay_manager(user, db=db)

    assert exc_info.value.status_code == 403
    assert "approved homestay provider profile required" in exc_info.value.detail.lower()


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
