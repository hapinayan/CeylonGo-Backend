from io import BytesIO
from pathlib import Path
from typing import Annotated
import os
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Path as ApiPath, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.routing import APIRoute
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import false, func, select, update
from sqlalchemy.orm import Session

from app.core.mvp_security import (
    get_optional_mvp_user, hash_password, require_mvp_role, verify_password,
)
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPCommunityPost, MVPPasswordReset, MVPUser
from app.models.mvp_account import Notification
from app.schemas.mvp_account import (
    Category, NotificationItem, NotificationPage, NotificationSettings, PasswordChange,
    PasswordChanged, Preferences, Profile, ProfilePatch, ReadAll, UnreadCount,
)
from app.services import mvp_account as account


class SafeValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                details = [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": details})

        return validated


router = APIRouter(tags=["MVP Traveler Account"], route_class=SafeValidationRoute)
DB = Annotated[Session, Depends(get_mvp_db)]
Traveler = Annotated[MVPUser, Depends(require_mvp_role("traveler"))]
Viewer = Annotated[MVPUser | None, Depends(get_optional_mvp_user)]
PositiveID = Annotated[int, ApiPath(gt=0)]
password_limit = SlidingWindowRateLimiter(max_requests=5, window_seconds=300)
MAX_IMAGE = 5 * 1024 * 1024


def media_root():
    return Path(os.getenv("TRAVELER_AVATAR_DIR", "uploads/traveler_avatars")).resolve()


def avatar_path(name):
    if name and re.fullmatch(r"[0-9a-f]{32}\.jpg", name):
        return media_root() / name
    return None


def remove_file(name):
    path = avatar_path(name)
    if path:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@router.patch("/auth/me", response_model=Profile)
def update_profile(payload: ProfilePatch, db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id, write=True)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row if key == "country" else user, key, value)
    user.updated_at = account.now()
    db.commit()
    return account.profile(db, user)


def avatar_bytes(file: UploadFile):
    payload = file.file.read(MAX_IMAGE + 1)
    if len(payload) > MAX_IMAGE:
        raise HTTPException(413, "Avatar exceeds 5 MB")
    formats = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    try:
        with Image.open(BytesIO(payload), formats=list(formats)) as image:
            if max(image.size) > 4096 or image.width * image.height > 12_000_000:
                raise HTTPException(422, "Avatar dimensions exceed the limit")
            if getattr(image, "is_animated", False):
                raise HTTPException(415, "Animated images are unsupported")
            image.verify()
        with Image.open(BytesIO(payload), formats=list(formats)) as image:
            clean = ImageOps.exif_transpose(image).convert("RGB")
            clean.thumbnail((1024, 1024))
            clean.info.clear()
            output = BytesIO()
            clean.save(output, format="JPEG", quality=88)
            return output.getvalue()
    except UnidentifiedImageError:
        raise HTTPException(415, "Upload a valid JPEG, PNG or WebP image") from None
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise HTTPException(422, "Avatar image is malformed") from None


@router.post("/auth/me/avatar", response_model=Profile)
def upload_avatar(db: DB, user: Traveler, file: Annotated[UploadFile, File()]):
    account.require_schema(db)
    content = avatar_bytes(file)
    name = uuid4().hex + ".jpg"
    path = avatar_path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(content)
    except OSError:
        remove_file(name)
        raise HTTPException(503, "Avatar storage is unavailable") from None
    try:
        row = account.settings(db, user.id, write=True)
        previous = row.avatar
        row.avatar = name
        user.updated_at = account.now()
        db.commit()
    except BaseException:
        db.rollback()
        remove_file(name)
        raise
    remove_file(previous)
    return account.profile(db, user)


@router.delete("/auth/me/avatar", response_model=Profile)
def delete_avatar(db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id, write=True)
    previous = row.avatar
    row.avatar = None
    user.updated_at = account.now()
    db.commit()
    remove_file(previous)
    return account.profile(db, user)


@router.get("/users/{user_id}/avatar", response_class=FileResponse, responses={
    200: {"content": {"image/jpeg": {}}},
})
def retrieve_avatar(user_id: PositiveID, db: DB, viewer: Viewer):
    account.require_schema(db)
    owner = db.get(MVPUser, user_id)
    if not owner or not owner.is_active or owner.role != "traveler":
        raise HTTPException(404, "Avatar not found")
    if viewer is None or viewer.id != user_id:
        visible = db.scalar(select(MVPCommunityPost.id).where(
            MVPCommunityPost.author_id == user_id, MVPCommunityPost.visibility == "visible",
        ).limit(1))
        if visible is None:
            raise HTTPException(404, "Avatar not found")
    row = account.settings(db, user_id)
    path = avatar_path(row.avatar) if row else None
    if not path or not path.is_file():
        raise HTTPException(404, "Avatar not found")
    return FileResponse(path, media_type="image/jpeg", headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
    })


@router.post("/auth/change-password", response_model=PasswordChanged)
def change_password(payload: PasswordChange, db: DB, user: Traveler):
    account.require_schema(db)
    password_limit.check_identifier(str(user.id))
    version = user.auth_version
    row = account.settings(db, user.id, write=True)
    if not user.is_active or user.auth_version != version:
        raise HTTPException(401, "Sign in again")
    if row.google_only:
        raise HTTPException(400, "Use Forgot Password to set a password for this Google account")
    current = payload.current_password.get_secret_value()
    new = payload.new_password.get_secret_value()
    if new != payload.confirm_password.get_secret_value():
        raise HTTPException(422, "New password confirmation does not match")
    if not verify_password(current, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    if new == current:
        raise HTTPException(400, "Choose a different password")
    user.hashed_password = hash_password(new)
    user.auth_version += 1
    user.credential_version += 1
    user.updated_at = account.now()
    db.execute(update(MVPPasswordReset).where(
        MVPPasswordReset.user_id == user.id, MVPPasswordReset.used_at.is_(None),
    ).values(used_at=user.updated_at))
    account.notify(db, user.id, "account", "password_changed", user.id,
                   f"password:{user.id}:{user.credential_version}")
    db.commit()
    return PasswordChanged(message="Password changed. Please log in again.", login_required=True)


@router.get("/traveler/preferences", response_model=Preferences)
def get_preferences(db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id)
    return Preferences.model_validate_json(row.preferences) if row else Preferences()


@router.put("/traveler/preferences", response_model=Preferences)
def set_preferences(payload: Preferences, db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id, write=True)
    row.preferences = payload.model_dump_json()
    db.commit()
    return payload


@router.get("/traveler/settings/notifications", response_model=NotificationSettings)
def get_notification_settings(db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id)
    return NotificationSettings.model_validate(row) if row else NotificationSettings()


@router.patch("/traveler/settings/notifications", response_model=NotificationSettings)
def set_notification_settings(payload: NotificationSettings, db: DB, user: Traveler):
    account.require_schema(db)
    row = account.settings(db, user.id, write=True)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    return row


def count(db, query):
    return db.scalar(select(func.count()).select_from(query.subquery()))


@router.get("/notifications", response_model=NotificationPage)
def notifications(db: DB, user: Traveler, page: int = Query(1, ge=1, le=100000),
                  page_size: int = Query(20, ge=1, le=100),
                  unread_only: bool = False, category: Category | None = None):
    account.require_schema(db)
    query = account.feed(user.id)
    unread = count(db, query.where(Notification.is_read == false()))
    if unread_only:
        query = query.where(Notification.is_read == false())
    if category:
        query = query.where(Notification.category == category)
    items = db.scalars(query.order_by(Notification.created_at.desc(), Notification.id.desc())
                       .offset((page - 1) * page_size).limit(page_size)).all()
    return dict(items=items, total=count(db, query), page=page, page_size=page_size,
                unread_count=unread)


@router.get("/notifications/unread-count", response_model=UnreadCount)
def unread_count(db: DB, user: Traveler):
    account.require_schema(db)
    return UnreadCount(unread_count=count(db, account.feed(user.id).where(
        Notification.is_read == false(),
    )))


@router.patch("/notifications/{notification_id}/read", response_model=NotificationItem)
def read_notification(notification_id: PositiveID, db: DB, user: Traveler):
    account.require_schema(db)
    account.lock_user(db, user.id)
    row = db.scalar(account.feed(user.id).where(Notification.id == notification_id))
    if row is None:
        raise HTTPException(404, "Notification not found")
    if not row.is_read:
        row.is_read, row.read_at = True, account.now()
    db.commit()
    return row


@router.post("/notifications/read-all", response_model=ReadAll)
def read_all(db: DB, user: Traveler):
    account.require_schema(db)
    account.lock_user(db, user.id)
    ids = account.feed(user.id).with_only_columns(Notification.id).where(
        Notification.is_read == false(),
    )
    result = db.execute(update(Notification).where(Notification.id.in_(ids)).values(
        is_read=True, read_at=account.now(),
    ).execution_options(synchronize_session=False))
    remaining = count(db, account.feed(user.id).where(Notification.is_read == false()))
    db.commit()
    return ReadAll(updated_count=result.rowcount, unread_count=remaining)

