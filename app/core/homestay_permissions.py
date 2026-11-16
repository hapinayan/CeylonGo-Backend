
from typing import Annotated

from fastapi import Depends, HTTPException, status

from app.models.user import User
from app.routers.auth import get_current_user


def require_homestay_manager(
    current_user: Annotated[
        User,
        Depends(get_current_user),
    ],
) -> User:
    if current_user.role not in ("owner", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner or admin access required",
        )

    return current_user
