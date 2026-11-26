"""Academic MVP Travel Community & Social Feed Service Layer."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.mvp import (
    MVPAdminAuditLog,
    MVPCommunityComment,
    MVPCommunityPost,
    MVPCommunityPostLike,
    MVPCommunityPostMedia,
    MVPCommunityReport,
    MVPCommunitySavedPost,
    MVPProviderProfile,
    MVPUser,
)
from app.schemas.mvp_community import (
    AdminCommunityCommentModerationPatch,
    AdminCommunityPostModerationPatch,
    AdminCommunityReportDecision,
    CommunityAuthorProfile,
    CommunityCommentCreateRequest,
    CommunityCommentDetails,
    CommunityCommentUpdateRequest,
    CommunityLikeStatus,
    CommunityPage,
    CommunityPostCreateRequest,
    CommunityPostDetails,
    CommunityPostMediaItem,
    CommunityPostUpdateRequest,
    CommunityReportCreateRequest,
    CommunityReportDetails,
    CommunitySaveStatus,
)
from app.services.community_posts import (
    _validate_and_strip_exif_image,
    get_community_upload_dir,
    get_max_media_bytes,
    resolve_safe_community_media_path,
    sanitize_community_text,
)

MAX_PHOTOS_PER_POST = 6
COMMUNITY_MEDIA_PREFIX = "community/"


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _table_exists(db: Session, table_name: str) -> bool:
    if db.get_bind().dialect.name != "mssql":
        return True
    return db.scalar(text("SELECT OBJECT_ID(:name, N'U')"), {"name": f"dbo.{table_name}"}) is not None


def media_installed(db: Session) -> bool:
    return _table_exists(db, "community_post_media")


def saved_posts_installed(db: Session) -> bool:
    return _table_exists(db, "community_saved_posts")


def reports_installed(db: Session) -> bool:
    return _table_exists(db, "community_reports")


def require_media_schema(db: Session) -> None:
    if not media_installed(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Community post photo uploads require MVP schema 1.0.5 (pending approval).",
        )


def require_saved_schema(db: Session) -> None:
    if not saved_posts_installed(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Community saved posts require MVP schema 1.0.5 (pending approval).",
        )


def require_reports_schema(db: Session) -> None:
    if not reports_installed(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Community moderation reports require MVP schema 1.0.5 (pending approval).",
        )


def _author_type(role: str) -> str:
    if role == "provider":
        return "provider"
    if role == "admin":
        return "admin"
    return "traveler"


def _build_author_map(db: Session, users: list[MVPUser]) -> dict[int, CommunityAuthorProfile]:
    from app.models.mvp_account import TravelerSettings
    from app.services import mvp_account

    user_ids = list({u.id for u in users if u is not None})
    if not user_ids:
        return {}
    profiles = {
        p.user_id: p
        for p in db.scalars(
            select(MVPProviderProfile).where(MVPProviderProfile.user_id.in_(user_ids))
        ).all()
    }
    avatars = {}
    if mvp_account.available(db):
        avatars = dict(db.execute(select(TravelerSettings.user_id, TravelerSettings.avatar)
                                  .where(TravelerSettings.user_id.in_(user_ids))).all())
    result: dict[int, CommunityAuthorProfile] = {}
    for u in users:
        if u is None or u.id in result:
            continue
        prof = profiles.get(u.id)
        role_type = _author_type(u.role)
        result[u.id] = CommunityAuthorProfile(
            id=u.id,
            full_name=u.full_name,
            author_type=role_type,
            provider_profile_id=prof.id if prof else None,
            provider_business_name=prof.business_name if prof else None,
            provider_district=prof.district if prof else None,
            is_verified_provider=bool(prof and prof.verification_status == "approved"),
            member_since=u.created_at,
            avatar_url=mvp_account.avatar_url(u.id, avatars.get(u.id)),
        )
    return result


def serialize_posts_batch(
    db: Session,
    posts: list[MVPCommunityPost],
    *,
    viewer: MVPUser | None = None,
) -> list[CommunityPostDetails]:
    if not posts:
        return []
    post_ids = [p.id for p in posts]
    authors = _build_author_map(db, [p.author for p in posts if p.author is not None])

    like_counts = {
        row[0]: int(row[1])
        for row in db.execute(
            select(MVPCommunityPostLike.post_id, func.count(MVPCommunityPostLike.user_id))
            .where(MVPCommunityPostLike.post_id.in_(post_ids))
            .group_by(MVPCommunityPostLike.post_id)
        ).all()
    }
    comment_counts = {
        row[0]: int(row[1])
        for row in db.execute(
            select(MVPCommunityComment.post_id, func.count(MVPCommunityComment.id))
            .where(
                MVPCommunityComment.post_id.in_(post_ids),
                MVPCommunityComment.visibility == "visible",
            )
            .group_by(MVPCommunityComment.post_id)
        ).all()
    }

    viewer_liked: set[int] = set()
    viewer_saved: set[int] = set()
    if viewer is not None:
        viewer_liked = set(
            db.scalars(
                select(MVPCommunityPostLike.post_id).where(
                    MVPCommunityPostLike.post_id.in_(post_ids),
                    MVPCommunityPostLike.user_id == viewer.id,
                )
            ).all()
        )
        if saved_posts_installed(db):
            viewer_saved = set(
                db.scalars(
                    select(MVPCommunitySavedPost.post_id).where(
                        MVPCommunitySavedPost.post_id.in_(post_ids),
                        MVPCommunitySavedPost.user_id == viewer.id,
                    )
                ).all()
            )

    media_by_post: dict[int, list[CommunityPostMediaItem]] = {pid: [] for pid in post_ids}
    if media_installed(db):
        media_rows = db.scalars(
            select(MVPCommunityPostMedia)
            .where(
                MVPCommunityPostMedia.post_id.in_(post_ids),
                MVPCommunityPostMedia.is_visible == True,  # noqa: E712
            )
            .order_by(MVPCommunityPostMedia.display_order.asc(), MVPCommunityPostMedia.id.asc())
        ).all()
        for m in media_rows:
            media_by_post.setdefault(m.post_id, []).append(
                CommunityPostMediaItem(
                    id=m.id,
                    post_id=m.post_id,
                    media_type=m.media_type,
                    image_url=f"/api/v1/community/posts/{m.post_id}/media/{m.id}",
                    alt_text=m.alt_text,
                    display_order=m.display_order,
                    created_at=m.created_at,
                )
            )

    items: list[CommunityPostDetails] = []
    for p in posts:
        author = authors.get(p.author_id)
        if author is None:
            author = CommunityAuthorProfile(
                id=p.author_id,
                full_name="Community Member",
                author_type="traveler",
                member_since=p.created_at,
            )
        items.append(
            CommunityPostDetails(
                id=p.id,
                author_id=p.author_id,
                author_name=author.full_name,
                author_type=author.author_type,
                author=author,
                body=p.body,
                visibility=p.visibility,
                created_at=p.created_at,
                updated_at=p.updated_at,
                likes_count=like_counts.get(p.id, 0),
                comments_count=comment_counts.get(p.id, 0),
                viewer_has_liked=p.id in viewer_liked,
                viewer_has_saved=p.id in viewer_saved,
                media=media_by_post.get(p.id, []),
            )
        )
    return items


def serialize_post(
    db: Session,
    post: MVPCommunityPost,
    *,
    viewer: MVPUser | None = None,
) -> CommunityPostDetails:
    return serialize_posts_batch(db, [post], viewer=viewer)[0]


def serialize_comments_batch(db: Session, comments: list[MVPCommunityComment]) -> list[CommunityCommentDetails]:
    if not comments:
        return []
    authors = _build_author_map(db, [c.author for c in comments if c.author is not None])
    items: list[CommunityCommentDetails] = []
    for c in comments:
        author = authors.get(c.author_id)
        if author is None:
            author = CommunityAuthorProfile(
                id=c.author_id,
                full_name="Community Member",
                author_type="traveler",
                member_since=c.created_at,
            )
        items.append(
            CommunityCommentDetails(
                id=c.id,
                post_id=c.post_id,
                author_id=c.author_id,
                author_name=author.full_name,
                author_type=author.author_type,
                author=author,
                body=c.body,
                visibility=c.visibility,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    return items


def _paginate_query(db: Session, query, page: int, page_size: int):
    total = int(db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0)
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    total_pages = max(1, (total + page_size - 1) // page_size)
    return rows, total, total_pages


def _visible_post_or_404(db: Session, post_id: int) -> MVPCommunityPost:
    post = db.scalar(
        select(MVPCommunityPost)
        .options(selectinload(MVPCommunityPost.author))
        .where(
            MVPCommunityPost.id == post_id,
            MVPCommunityPost.visibility == "visible",
            MVPCommunityPost.author.has(MVPUser.is_active == True),  # noqa: E712
        )
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found or unavailable.")
    return post


def _post_for_viewer_or_404(db: Session, post_id: int, viewer: MVPUser | None) -> MVPCommunityPost:
    post = db.scalar(
        select(MVPCommunityPost)
        .options(selectinload(MVPCommunityPost.author))
        .where(MVPCommunityPost.id == post_id)
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    is_admin = viewer is not None and viewer.role == "admin"
    is_owner = viewer is not None and viewer.id == post.author_id
    if post.visibility == "visible" and post.author and post.author.is_active:
        return post
    if is_admin or (is_owner and post.visibility != "deleted"):
        return post
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")


def list_public_posts(
    db: Session,
    *,
    viewer: MVPUser | None = None,
    search: str | None = None,
    author_id: int | None = None,
    author_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityPage[CommunityPostDetails]:
    query = (
        select(MVPCommunityPost)
        .options(selectinload(MVPCommunityPost.author))
        .where(
            MVPCommunityPost.visibility == "visible",
            MVPCommunityPost.author.has(MVPUser.is_active == True),  # noqa: E712
        )
    )
    if search and (term := search.strip().lower()):
        query = query.where(func.lower(MVPCommunityPost.body).contains(term, autoescape=True))
    if author_id is not None:
        query = query.where(MVPCommunityPost.author_id == author_id)
    if author_type in {"traveler", "provider"}:
        query = query.where(MVPCommunityPost.author.has(MVPUser.role == author_type))

    query = query.order_by(MVPCommunityPost.created_at.desc(), MVPCommunityPost.id.desc())
    rows, total, total_pages = _paginate_query(db, query, page, page_size)
    return CommunityPage[CommunityPostDetails](
        items=serialize_posts_batch(db, rows, viewer=viewer),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def list_own_posts(
    db: Session,
    *,
    user: MVPUser,
    page: int = 1,
    page_size: int = 20,
) -> CommunityPage[CommunityPostDetails]:
    query = (
        select(MVPCommunityPost)
        .options(selectinload(MVPCommunityPost.author))
        .where(
            MVPCommunityPost.author_id == user.id,
            MVPCommunityPost.visibility != "deleted",
        )
        .order_by(MVPCommunityPost.created_at.desc(), MVPCommunityPost.id.desc())
    )
    rows, total, total_pages = _paginate_query(db, query, page, page_size)
    return CommunityPage[CommunityPostDetails](
        items=serialize_posts_batch(db, rows, viewer=user),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def create_post(
    db: Session,
    *,
    author: MVPUser,
    req: CommunityPostCreateRequest,
) -> CommunityPostDetails:
    cleaned = sanitize_community_text(
        req.body, field_name="body", max_length=2000, allow_newlines=True, required=True
    )
    assert cleaned is not None
    now = _now_utc()
    post = MVPCommunityPost(
        author_id=author.id,
        body=cleaned,
        visibility="visible",
        created_at=now,
        updated_at=now,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return serialize_post(db, post, viewer=author)


def get_post_details(
    db: Session,
    *,
    post_id: int,
    viewer: MVPUser | None = None,
) -> CommunityPostDetails:
    post = _post_for_viewer_or_404(db, post_id, viewer)
    return serialize_post(db, post, viewer=viewer)


def update_own_post(
    db: Session,
    *,
    post_id: int,
    user: MVPUser,
    req: CommunityPostUpdateRequest,
) -> CommunityPostDetails:
    post = db.get(MVPCommunityPost, post_id)
    if post is None or post.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    if post.author_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own community posts.")
    if post.visibility == "hidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hidden posts moderated by an administrator cannot be edited.",
        )

    cleaned = sanitize_community_text(
        req.body, field_name="body", max_length=2000, allow_newlines=True, required=True
    )
    assert cleaned is not None
    post.body = cleaned
    post.updated_at = _now_utc()
    db.commit()
    db.refresh(post)
    return serialize_post(db, post, viewer=user)


def delete_post(
    db: Session,
    *,
    post_id: int,
    user: MVPUser,
) -> dict[str, object]:
    post = db.get(MVPCommunityPost, post_id)
    if post is None or post.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    is_admin = user.role == "admin"
    if post.author_id != user.id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this post.")
    if not is_admin and post.visibility == "hidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This post was hidden by an administrator and cannot be deleted by the author.",
        )

    now = _now_utc()
    post.visibility = "deleted"
    post.updated_at = now
    if saved_posts_installed(db):
        db.query(MVPCommunitySavedPost).filter(MVPCommunitySavedPost.post_id == post.id).delete(
            synchronize_session=False
        )
    if is_admin and post.author_id != user.id:
        db.add(
            MVPAdminAuditLog(
                actor_id=user.id,
                action="post_moderation",
                subject_post_id=post.id,
                reason="Deleted by administrator",
                occurred_at=now,
            )
        )
    db.commit()
    return {"deleted": True, "post_id": post_id, "visibility": "deleted"}


# =============================================================================
# POST PHOTOGRAPHS (MVP 1.0.5)
# =============================================================================

async def upload_post_photo(
    db: Session,
    *,
    post_id: int,
    user: MVPUser,
    file: UploadFile,
    alt_text: str | None = None,
) -> CommunityPostMediaItem:
    require_media_schema(db)
    post = db.get(MVPCommunityPost, post_id)
    if post is None or post.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    if post.author_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the post author can upload photos.")
    if post.visibility != "visible":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Photos cannot be added to hidden posts.")

    active_count = int(
        db.scalar(
            select(func.count(MVPCommunityPostMedia.id)).where(
                MVPCommunityPostMedia.post_id == post_id,
                MVPCommunityPostMedia.is_visible == True,  # noqa: E712
            )
        )
        or 0
    )
    if active_count >= MAX_PHOTOS_PER_POST:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A community post may contain at most {MAX_PHOTOS_PER_POST} photos.",
        )

    max_bytes = get_max_media_bytes()
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Photo exceeds maximum allowed size of {max_bytes // (1024 * 1024)} MB.",
        )

    sanitized_bytes, canonical_ext, canonical_mime = _validate_and_strip_exif_image(
        raw, filename=file.filename, content_type=file.content_type
    )
    cleaned_alt = sanitize_community_text(alt_text, field_name="alt_text", max_length=160)

    upload_dir = get_community_upload_dir()
    safe_filename = f"post_{post_id}_{uuid4().hex}{canonical_ext}"
    target_path = resolve_safe_community_media_path(safe_filename, upload_dir)
    target_path.write_bytes(sanitized_bytes)

    next_order = int(
        db.scalar(
            select(func.coalesce(func.max(MVPCommunityPostMedia.display_order), -1)).where(
                MVPCommunityPostMedia.post_id == post_id
            )
        )
        or -1
    ) + 1

    now = _now_utc()
    media = MVPCommunityPostMedia(
        post_id=post_id,
        storage_key=f"{COMMUNITY_MEDIA_PREFIX}{safe_filename}",
        media_type="image",
        content_type=canonical_mime,
        alt_text=cleaned_alt,
        display_order=next_order,
        is_visible=True,
        created_at=now,
    )
    db.add(media)
    try:
        db.commit()
        db.refresh(media)
    except Exception:
        db.rollback()
        if target_path.is_file():
            target_path.unlink(missing_ok=True)
        raise

    return CommunityPostMediaItem(
        id=media.id,
        post_id=media.post_id,
        media_type=media.media_type,
        image_url=f"/api/v1/community/posts/{media.post_id}/media/{media.id}",
        alt_text=media.alt_text,
        display_order=media.display_order,
        created_at=media.created_at,
    )


def get_post_photo_file(
    db: Session,
    *,
    post_id: int,
    media_id: int,
    viewer: MVPUser | None = None,
) -> tuple[Path, str]:
    require_media_schema(db)
    post = _post_for_viewer_or_404(db, post_id, viewer)
    media = db.scalar(
        select(MVPCommunityPostMedia).where(
            MVPCommunityPostMedia.id == media_id,
            MVPCommunityPostMedia.post_id == post.id,
            MVPCommunityPostMedia.is_visible == True,  # noqa: E712
        )
    )
    if media is None or not media.storage_key.startswith(COMMUNITY_MEDIA_PREFIX):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community photo not found.")

    filename = media.storage_key[len(COMMUNITY_MEDIA_PREFIX) :]
    file_path = resolve_safe_community_media_path(filename)
    if not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community photo file not found.")
    return file_path, media.content_type


def delete_post_photo(
    db: Session,
    *,
    post_id: int,
    media_id: int,
    user: MVPUser,
) -> dict[str, object]:
    require_media_schema(db)
    post = db.get(MVPCommunityPost, post_id)
    if post is None or post.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    if post.author_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this photo.")

    media = db.scalar(
        select(MVPCommunityPostMedia).where(
            MVPCommunityPostMedia.id == media_id,
            MVPCommunityPostMedia.post_id == post_id,
            MVPCommunityPostMedia.is_visible == True,  # noqa: E712
        )
    )
    if media is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community photo not found.")
    media.is_visible = False
    db.commit()
    return {"deleted": True, "media_id": media_id, "post_id": post_id}


# =============================================================================
# LIKES, COMMENTS & SAVED POSTS
# =============================================================================

def _likes_count(db: Session, post_id: int) -> int:
    return int(
        db.scalar(select(func.count(MVPCommunityPostLike.user_id)).where(MVPCommunityPostLike.post_id == post_id))
        or 0
    )


def like_post(db: Session, *, post_id: int, user: MVPUser) -> CommunityLikeStatus:
    from app.services.mvp_account import notify

    post = _visible_post_or_404(db, post_id)
    existing = db.get(MVPCommunityPostLike, (post_id, user.id))
    if existing is None:
        db.add(MVPCommunityPostLike(post_id=post_id, user_id=user.id, created_at=_now_utc()))
        try:
            if post.author_id != user.id:
                notify(db, post.author_id, "community", "post_liked", post.id,
                       f"post-like:{post.id}:{user.id}")
            db.commit()
        except IntegrityError:
            db.rollback()
    return CommunityLikeStatus(post_id=post_id, liked=True, likes_count=_likes_count(db, post_id))


def unlike_post(db: Session, *, post_id: int, user: MVPUser) -> CommunityLikeStatus:
    _visible_post_or_404(db, post_id)
    existing = db.get(MVPCommunityPostLike, (post_id, user.id))
    if existing is not None:
        db.delete(existing)
        db.commit()
    return CommunityLikeStatus(post_id=post_id, liked=False, likes_count=_likes_count(db, post_id))


def toggle_like(db: Session, *, post_id: int, user: MVPUser) -> CommunityLikeStatus:
    _visible_post_or_404(db, post_id)
    existing = db.get(MVPCommunityPostLike, (post_id, user.id))
    if existing is not None:
        db.delete(existing)
        db.commit()
        return CommunityLikeStatus(post_id=post_id, liked=False, likes_count=_likes_count(db, post_id))
    return like_post(db, post_id=post_id, user=user)


def list_comments(
    db: Session,
    *,
    post_id: int,
    viewer: MVPUser | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityPage[CommunityCommentDetails]:
    _post_for_viewer_or_404(db, post_id, viewer)
    query = (
        select(MVPCommunityComment)
        .options(selectinload(MVPCommunityComment.author))
        .where(
            MVPCommunityComment.post_id == post_id,
            MVPCommunityComment.visibility == "visible",
            MVPCommunityComment.author.has(MVPUser.is_active == True),  # noqa: E712
        )
        .order_by(MVPCommunityComment.created_at.asc(), MVPCommunityComment.id.asc())
    )
    rows, total, total_pages = _paginate_query(db, query, page, page_size)
    return CommunityPage[CommunityCommentDetails](
        items=serialize_comments_batch(db, rows),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def create_comment(
    db: Session,
    *,
    post_id: int,
    author: MVPUser,
    req: CommunityCommentCreateRequest,
) -> CommunityCommentDetails:
    from app.services.mvp_account import available, notify

    post = _visible_post_or_404(db, post_id)
    cleaned = sanitize_community_text(
        req.body, field_name="body", max_length=1000, allow_newlines=True, required=True
    )
    assert cleaned is not None
    now = _now_utc()
    comment = MVPCommunityComment(
        post_id=post_id,
        author_id=author.id,
        body=cleaned,
        visibility="visible",
        created_at=now,
        updated_at=now,
    )
    db.add(comment)
    if post.author_id != author.id and available(db):
        db.flush()
        notify(db, post.author_id, "community", "post_commented", comment.id,
               f"post-comment:{comment.id}", post_id=post.id)
    db.commit()
    db.refresh(comment)
    return serialize_comments_batch(db, [comment])[0]


def update_comment(
    db: Session,
    *,
    comment_id: int,
    user: MVPUser,
    req: CommunityCommentUpdateRequest,
) -> CommunityCommentDetails:
    comment = db.scalar(
        select(MVPCommunityComment)
        .options(selectinload(MVPCommunityComment.author))
        .where(MVPCommunityComment.id == comment_id)
    )
    if comment is None or comment.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
    if comment.author_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own comments.")
    if comment.visibility == "hidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hidden comments moderated by an administrator cannot be edited.",
        )

    cleaned = sanitize_community_text(
        req.body, field_name="body", max_length=1000, allow_newlines=True, required=True
    )
    assert cleaned is not None
    comment.body = cleaned
    comment.updated_at = _now_utc()
    db.commit()
    db.refresh(comment)
    return serialize_comments_batch(db, [comment])[0]


def delete_comment(
    db: Session,
    *,
    comment_id: int,
    user: MVPUser,
) -> dict[str, object]:
    comment = db.get(MVPCommunityComment, comment_id)
    if comment is None or comment.visibility == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")
    is_admin = user.role == "admin"
    if comment.author_id != user.id and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission to delete this comment.")

    now = _now_utc()
    comment.visibility = "deleted"
    comment.updated_at = now
    if is_admin and comment.author_id != user.id:
        db.add(
            MVPAdminAuditLog(
                actor_id=user.id,
                action="post_moderation",
                subject_post_id=comment.post_id,
                reason=f"Comment #{comment.id} removed by administrator",
                occurred_at=now,
            )
        )
    db.commit()
    return {"deleted": True, "comment_id": comment_id, "visibility": "deleted"}


def save_post(db: Session, *, post_id: int, user: MVPUser) -> CommunitySaveStatus:
    require_saved_schema(db)
    _visible_post_or_404(db, post_id)
    existing = db.get(MVPCommunitySavedPost, (user.id, post_id))
    if existing is None:
        db.add(MVPCommunitySavedPost(user_id=user.id, post_id=post_id, created_at=_now_utc()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return CommunitySaveStatus(post_id=post_id, saved=True)


def unsave_post(db: Session, *, post_id: int, user: MVPUser) -> CommunitySaveStatus:
    require_saved_schema(db)
    existing = db.get(MVPCommunitySavedPost, (user.id, post_id))
    if existing is not None:
        db.delete(existing)
        db.commit()
    elif db.get(MVPCommunityPost, post_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    return CommunitySaveStatus(post_id=post_id, saved=False)


def list_saved_posts(
    db: Session,
    *,
    user: MVPUser,
    page: int = 1,
    page_size: int = 20,
) -> CommunityPage[CommunityPostDetails]:
    require_saved_schema(db)
    query = (
        select(MVPCommunityPost)
        .join(MVPCommunitySavedPost, MVPCommunitySavedPost.post_id == MVPCommunityPost.id)
        .options(selectinload(MVPCommunityPost.author))
        .where(
            MVPCommunitySavedPost.user_id == user.id,
            MVPCommunityPost.visibility == "visible",
            MVPCommunityPost.author.has(MVPUser.is_active == True),  # noqa: E712
        )
        .order_by(MVPCommunitySavedPost.created_at.desc(), MVPCommunityPost.id.desc())
    )
    rows, total, total_pages = _paginate_query(db, query, page, page_size)
    return CommunityPage[CommunityPostDetails](
        items=serialize_posts_batch(db, rows, viewer=user),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# =============================================================================
# REPORTS & ADMIN MODERATION
# =============================================================================

def submit_report(
    db: Session,
    *,
    reporter: MVPUser,
    req: CommunityReportCreateRequest,
) -> CommunityReportDetails:
    require_reports_schema(db)
    if req.entity_type == "post":
        target = db.get(MVPCommunityPost, req.entity_id)
        if target is None or target.visibility == "deleted":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reported post not found.")
        owner_id = target.author_id
    else:
        target = db.get(MVPCommunityComment, req.entity_id)
        if target is None or target.visibility == "deleted":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reported comment not found.")
        owner_id = target.author_id

    if owner_id == reporter.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="You cannot report your own content.")

    dup = db.scalar(
        select(MVPCommunityReport).where(
            MVPCommunityReport.reporter_id == reporter.id,
            MVPCommunityReport.entity_type == req.entity_type,
            MVPCommunityReport.entity_id == req.entity_id,
            MVPCommunityReport.status == "pending",
        )
    )
    if dup is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="You have already reported this content.")

    clean_reason = sanitize_community_text(req.reason, field_name="reason", max_length=100, required=True)
    clean_desc = sanitize_community_text(req.description, field_name="description", max_length=1000, allow_newlines=True)
    assert clean_reason is not None

    report = MVPCommunityReport(
        reporter_id=reporter.id,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        reason=clean_reason,
        description=clean_desc,
        status="pending",
        created_at=_now_utc(),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return CommunityReportDetails(
        id=report.id,
        reporter_id=report.reporter_id,
        reporter_name=reporter.full_name,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        reviewed_by=report.reviewed_by,
        review_notes=report.review_notes,
        reviewed_at=report.reviewed_at,
        created_at=report.created_at,
    )


def admin_moderate_post(
    db: Session,
    *,
    admin: MVPUser,
    post_id: int,
    visibility: str,
    reason: str,
) -> MVPCommunityPost:
    post = db.get(MVPCommunityPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community post not found.")
    clean_reason = sanitize_community_text(reason, field_name="reason", max_length=500, required=True)
    assert clean_reason is not None

    now = _now_utc()
    post.visibility = visibility
    post.updated_at = now
    if visibility != "visible" and saved_posts_installed(db):
        db.query(MVPCommunitySavedPost).filter(MVPCommunitySavedPost.post_id == post.id).delete(
            synchronize_session=False
        )

    db.add(
        MVPAdminAuditLog(
            actor_id=admin.id,
            action="post_moderation",
            subject_post_id=post.id,
            reason=f"Post set to {visibility}: {clean_reason}"[:500],
            occurred_at=now,
        )
    )
    db.commit()
    db.refresh(post)
    return post


def admin_moderate_comment(
    db: Session,
    *,
    admin: MVPUser,
    comment_id: int,
    payload: AdminCommunityCommentModerationPatch,
) -> MVPCommunityComment:
    comment = db.get(MVPCommunityComment, comment_id)
    if comment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community comment not found.")
    clean_reason = sanitize_community_text(payload.reason, field_name="reason", max_length=400, required=True)
    assert clean_reason is not None

    now = _now_utc()
    comment.visibility = payload.visibility
    comment.updated_at = now
    db.add(
        MVPAdminAuditLog(
            actor_id=admin.id,
            action="post_moderation",
            subject_post_id=comment.post_id,
            reason=f"Comment #{comment.id} set to {payload.visibility}: {clean_reason}"[:500],
            occurred_at=now,
        )
    )
    db.commit()
    db.refresh(comment)
    return comment


def admin_list_reports(
    db: Session,
    *,
    report_status: str | None = None,
    entity_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityPage[CommunityReportDetails]:
    require_reports_schema(db)
    query = select(MVPCommunityReport).options(selectinload(MVPCommunityReport.reporter))
    if report_status:
        query = query.where(MVPCommunityReport.status == report_status)
    if entity_type:
        query = query.where(MVPCommunityReport.entity_type == entity_type)
    query = query.order_by(MVPCommunityReport.created_at.desc(), MVPCommunityReport.id.desc())
    rows, total, total_pages = _paginate_query(db, query, page, page_size)
    return CommunityPage[CommunityReportDetails](
        items=[
            CommunityReportDetails(
                id=r.id,
                reporter_id=r.reporter_id,
                reporter_name=r.reporter.full_name if r.reporter else None,
                entity_type=r.entity_type,
                entity_id=r.entity_id,
                reason=r.reason,
                description=r.description,
                status=r.status,
                reviewed_by=r.reviewed_by,
                review_notes=r.review_notes,
                reviewed_at=r.reviewed_at,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def admin_review_report(
    db: Session,
    *,
    admin: MVPUser,
    report_id: int,
    decision: AdminCommunityReportDecision,
) -> CommunityReportDetails:
    require_reports_schema(db)
    report = db.scalar(
        select(MVPCommunityReport)
        .options(selectinload(MVPCommunityReport.reporter))
        .where(MVPCommunityReport.id == report_id)
    )
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Community report not found.")

    clean_notes = sanitize_community_text(decision.review_notes, field_name="review_notes", max_length=500)
    now = _now_utc()
    report.status = decision.status
    report.reviewed_by = admin.id
    report.review_notes = clean_notes
    report.reviewed_at = now

    if decision.action_visibility is not None:
        mod_reason = clean_notes or f"Report #{report.id} ({report.reason})"
        if report.entity_type == "post":
            post = db.get(MVPCommunityPost, report.entity_id)
            if post is not None:
                post.visibility = decision.action_visibility
                post.updated_at = now
                if decision.action_visibility != "visible" and saved_posts_installed(db):
                    db.query(MVPCommunitySavedPost).filter(MVPCommunitySavedPost.post_id == post.id).delete(
                        synchronize_session=False
                    )
                db.add(
                    MVPAdminAuditLog(
                        actor_id=admin.id,
                        action="post_moderation",
                        subject_post_id=post.id,
                        reason=f"Report #{report.id}: {mod_reason}"[:500],
                        occurred_at=now,
                    )
                )
        else:
            comment = db.get(MVPCommunityComment, report.entity_id)
            if comment is not None:
                comment.visibility = decision.action_visibility
                comment.updated_at = now
                db.add(
                    MVPAdminAuditLog(
                        actor_id=admin.id,
                        action="post_moderation",
                        subject_post_id=comment.post_id,
                        reason=f"Report #{report.id} comment #{comment.id}: {mod_reason}"[:500],
                        occurred_at=now,
                    )
                )

    db.commit()
    db.refresh(report)
    return CommunityReportDetails(
        id=report.id,
        reporter_id=report.reporter_id,
        reporter_name=report.reporter.full_name if report.reporter else None,
        entity_type=report.entity_type,
        entity_id=report.entity_id,
        reason=report.reason,
        description=report.description,
        status=report.status,
        reviewed_by=report.reviewed_by,
        review_notes=report.review_notes,
        reviewed_at=report.reviewed_at,
        created_at=report.created_at,
    )
