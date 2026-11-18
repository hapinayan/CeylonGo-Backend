from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.shared import Notification
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.notification import (
    NotificationListResponse,
    NotificationResponse,
    UnreadCountResponse,
)

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["Notifications"],
)


@router.get("", response_model=NotificationListResponse)
def get_user_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
    unread_only: bool = False,
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    unread_count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
        .count()
    )

    if unread_only:
        query = query.filter(Notification.is_read == False)

    total = query.count()
    items = (
        query.order_by(Notification.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return NotificationListResponse(
        items=items,
        total=total,
        unread_count=unread_count,
        page=page,
        page_size=page_size,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
def get_unread_notification_count(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
        .count()
    )
    return UnreadCountResponse(unread_count=count)


@router.patch("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_as_read(
    notification_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    notif = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )
    if not notif:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    if not notif.is_read:
        notif.is_read = True
        notif.read_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        db.refresh(notif)

    return notif


@router.post("/read-all", summary="Mark all user notifications as read")
def mark_all_notifications_as_read(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    unread_notifs = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read == False,
        )
        .all()
    )
    for n in unread_notifs:
        n.is_read = True
        n.read_at = now_utc

    db.commit()
    return {"message": "All notifications marked as read", "updated_count": len(unread_notifs)}
