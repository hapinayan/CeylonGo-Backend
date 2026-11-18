from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models.shared import Notification


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    notification_type: str,
    link_url: str | None = None,
) -> Notification | None:
    """
    Safely creates an in-app notification for a user.
    Prevents duplicate notifications from repeated/retried requests within 30 seconds.
    """
    recent_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=30)
    duplicate = (
        db.query(Notification)
        .filter(
            Notification.user_id == user_id,
            Notification.notification_type == notification_type,
            Notification.title == title,
            Notification.created_at >= recent_cutoff,
        )
        .first()
    )
    if duplicate:
        return duplicate

    notif = Notification(
        user_id=user_id,
        title=title[:150],
        message=message[:1000],
        notification_type=notification_type[:50],
        link_url=link_url[:500] if link_url else None,
        is_read=False,
    )
    db.add(notif)
    return notif
