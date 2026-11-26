from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import inspect, or_, select, text, true
from sqlalchemy.orm import Session

from app.models.mvp import MVPCommunityComment, MVPCommunityPost, MVPUser
from app.models.mvp_account import Notification, TravelerSettings
from app.models.traveler_email_challenge import TravelerEmailChallenge
from app.schemas.mvp_account import Profile


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def available(db: Session) -> bool:
    if db.get_bind().dialect.name == "mssql":
        return bool(db.scalar(text(
            "SELECT CASE WHEN OBJECT_ID(N'dbo.traveler_settings', N'U') IS NOT NULL "
            "AND OBJECT_ID(N'dbo.traveler_notifications', N'U') IS NOT NULL THEN 1 ELSE 0 END"
        )))
    return all(inspect(db.connection()).has_table(name) for name in (
        "traveler_settings", "traveler_notifications",
    ))


def require_schema(db):
    if not available(db):
        raise HTTPException(503, "Traveler account features require MVP schema 1.0.7")


def lock_user(db, user_id):
    query = select(MVPUser).where(MVPUser.id == user_id)
    if db.get_bind().dialect.name == "mssql":
        query = query.with_hint(MVPUser, "WITH (UPDLOCK, HOLDLOCK)", "mssql")
    return db.scalar(query.execution_options(populate_existing=True))


def settings(db, user_id, *, write=False):
    if write:
        lock_user(db, user_id)
    row = db.get(TravelerSettings, user_id)
    if row is None and write:
        row = TravelerSettings(user_id=user_id)
        db.add(row)
    return row


def profile(db, user):
    traveler = user.role == "traveler"
    row = settings(db, user.id) if traveler and available(db) else None
    verified = bool(row and row.email_verified)
    password_evidence = False
    if traveler and inspect(db.connection()).has_table(
        TravelerEmailChallenge.__tablename__,
    ):
        challenge = TravelerEmailChallenge
        password_evidence = db.scalar(select(challenge.email).where(
            challenge.email == user.email, challenge.registration_hash.is_(None),
            challenge.otp_hash.is_(None), challenge.password_hash.is_(None),
            challenge.sent_at <= user.created_at,
        )) is not None
    return Profile(
        id=user.id, email=user.email, full_name=user.full_name, phone=user.phone,
        role=user.role, is_active=user.is_active, auth_version=user.auth_version,
        created_at=user.created_at, updated_at=user.updated_at,
        country=row.country if row else None,
        avatar_url=avatar_url(user.id, row.avatar) if row else None,
        email_verified=verified or password_evidence,
        authentication_method=(
            "google" if row and row.google_only else
            "password" if traveler and (password_evidence or user.credential_version > 0)
            else None
        ),
    )


def avatar_url(user_id, name):
    return f"/api/v1/users/{user_id}/avatar?v={name[:-4]}" if name else None


EVENTS = {
    "booking_pending": ("Booking request submitted", "Your request is awaiting provider review."),
    "booking_confirmed": ("Booking confirmed", "Your booking status is confirmed."),
    "booking_rejected": ("Booking rejected", "The provider rejected your booking request."),
    "booking_cancelled": ("Booking cancelled", "Your booking is cancelled."),
    "booking_cancelled_by_traveler": ("Booking cancelled", "You cancelled your booking."),
    "booking_cancelled_by_provider": ("Booking cancelled", "The provider cancelled your booking."),
    "booking_completed": ("Booking completed", "Your booking is complete."),
    "demo_succeeded": ("Demo payment successful", "Your Academic MVP demo payment succeeded."),
    "demo_failed": ("Demo payment failed", "Your Academic MVP demo payment did not succeed."),
    "unpaid": ("Pay at Location selected", "Payment remains unpaid until made at the location."),
    "post_liked": ("Post liked", "Someone liked your Community post."),
    "post_commented": ("New comment", "Someone commented on your Community post."),
    "password_changed": ("Password changed", "The password for your account was changed."),
    "password_reset": ("Password reset", "The password for your account was reset."),
}
PREFERENCE = {
    "booking": "booking_updates",
    "payment": "payment_updates",
    "community": "community_activity",
}


def notify(db, recipient, category, event, entity, key, *, post_id=None):
    if event not in EVENTS or not available(db):
        return
    db.flush()
    user = lock_user(db, recipient)
    if not user or not user.is_active or user.role != "traveler":
        return
    row = settings(db, recipient)
    if row and category in PREFERENCE and not getattr(row, PREFERENCE[category]):
        return
    if db.scalar(select(Notification.id).where(Notification.event_key == key)) is not None:
        return
    title, message = EVENTS[event]
    db.add(Notification(
        recipient_user_id=recipient, category=category, notification_type=event,
        title=title, message=message, event_key=key, created_at=now(),
        related_entity_type={"payment": "booking", "community": "community_post"}.get(
            category, category
        ),
        related_entity_id=post_id if post_id is not None else entity,
        source_comment_id=entity if event == "post_commented" else None,
    ))


def feed(user_id):
    visible_post = select(MVPCommunityPost.id).join(
        MVPUser, MVPCommunityPost.author_id == MVPUser.id,
    ).where(
        MVPCommunityPost.id == Notification.related_entity_id,
        MVPCommunityPost.visibility == "visible", MVPUser.is_active == true(),
    ).correlate(Notification).exists()
    visible_comment = select(MVPCommunityComment.id).join(
        MVPUser, MVPCommunityComment.author_id == MVPUser.id,
    ).where(
        MVPCommunityComment.id == Notification.source_comment_id,
        MVPCommunityComment.visibility == "visible", MVPUser.is_active == true(),
    ).correlate(Notification).exists()
    return select(Notification).where(
        Notification.recipient_user_id == user_id,
        or_(Notification.category != "community", visible_post),
        or_(Notification.source_comment_id.is_(None), visible_comment),
    )


def booking_event(db, booking, actor=None):
    if not available(db):
        return
    db.flush()
    event = f"booking_{booking.status}"
    if booking.status == "cancelled" and actor and actor.role in {"traveler", "provider"}:
        event += f"_by_{actor.role}"
    notify(db, booking.traveler_id, "booking", event,
           booking.id, f"booking:{booking.id}:{booking.status}")


def payment_event(db, booking, payment):
    if available(db):
        db.flush()
        notify(db, booking.traveler_id, "payment", payment.status, booking.id,
               f"payment:{payment.id}:{payment.status}")


