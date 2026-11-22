"""
Travel Community & Social Feed — Phase 3 Services
(Likes, Threaded Comments & Replies, Private Saved Posts, Notifications & Rate Limiting).

Strictly compatible with Migration 028 (no Migration 037 columns assumed).

Comment Deletion Semantics on Migration 028 (SQL Server ON DELETE NO ACTION Safe):
----------------------------------------------------------------------------------
Because `FK_community_comments_parent` on `dbo.community_comments(parent_comment_id)` is
`ON DELETE NO ACTION` and Migration 037 (comment `status` column) is not yet approved:
1. If ANY `dbo.community_reports` row references the comment (`entity_type == 'comment'`)
   and caller is NOT an admin: reject deletion with `409 Conflict` so moderation evidence
   is preserved intact.
2. If the comment has child replies (`EXISTS community_comments WHERE parent_comment_id = id`)
   OR an admin removes a reported comment: redact the comment content in-place to
   `[Comment deleted by author]` / `[Comment removed by moderator]` (`deletion_mode =
   'redacted_thread_preserved'`). This keeps all child replies intact, preserves thread
   continuity, and prevents SQL Server FK violation Error 547 without requiring new columns.
3. Otherwise (no child replies and no moderation reports): permanently hard-delete the
   comment (`deletion_mode = 'hard_deleted'`) and recompute `community_posts.comments_count`
   atomically under the per-post engagement lock.

Concurrency & Counter Integrity:
--------------------------------
On Microsoft SQL Server (`mssql`), every like, unlike, comment creation, and comment
deletion acquires a transaction-scoped exclusive `sp_getapplock` via
`lock_community_post(db, post_id)`,
then recomputes `likes_count` / `comments_count` from authoritative `COUNT(*)` inside the
same transaction. Notifications are added to the same SQLAlchemy session before commit so
any transaction failure rolls back both the engagement write and its notification atomically.
"""

from __future__ import annotations

from datetime import UTC, datetime
import math
import os

from fastapi import HTTPException, status
from sqlalchemy import func, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.dependencies import is_admin_role, resolve_canonical_admin_role
from app.core.notification_service import create_notification
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.community import (
    CommunityComment,
    CommunityPost,
    CommunityPostLike,
    CommunityReport,
    CommunitySavedPost,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.community import (
    CommunityAuthorSummary,
    CommunityCommentCreate,
    CommunityCommentDeleteResponse,
    CommunityCommentListResponse,
    CommunityCommentResponse,
    CommunityCommentUpdate,
    CommunityLikeResponse,
    CommunitySaveResponse,
    CommunitySavedPostsResponse,
)
from app.services.community_posts import (
    is_public_community_post,
    lock_community_post,
    public_community_post_filter,
    sanitize_community_text,
    serialize_posts_batch,
)

REDACTED_BY_AUTHOR_TEXT = "[Comment deleted by author]"
REDACTED_BY_ADMIN_TEXT = "[Comment removed by moderator]"
_REDACTED_PLACEHOLDERS = frozenset({REDACTED_BY_AUTHOR_TEXT, REDACTED_BY_ADMIN_TEXT})


# =============================================================================
# RATE LIMITERS (REUSING SHARED SlidingWindowRateLimiter)
# =============================================================================

community_like_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("COMMUNITY_LIKE_RATE_LIMIT", "60")),
    window_seconds=int(os.getenv("COMMUNITY_LIKE_RATE_WINDOW_SECONDS", "60")),
    error_message="Too many like actions. Please try again shortly.",
)
community_comment_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("COMMUNITY_COMMENT_RATE_LIMIT", "20")),
    window_seconds=int(os.getenv("COMMUNITY_COMMENT_RATE_WINDOW_SECONDS", "60")),
    error_message="Too many comments posted. Please try again shortly.",
)
community_save_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("COMMUNITY_SAVE_RATE_LIMIT", "60")),
    window_seconds=int(os.getenv("COMMUNITY_SAVE_RATE_WINDOW_SECONDS", "60")),
    error_message="Too many bookmark actions. Please try again shortly.",
)


def reset_community_engagement_limiters() -> None:
    """Clear in-memory community engagement rate limiter windows (for tests)."""
    community_like_limiter.reset()
    community_comment_limiter.reset()
    community_save_limiter.reset()


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _get_published_post_or_404(db: Session, post_id: int) -> CommunityPost:
    """Return a published post whose author is active, else 404 (prevents engagement on hidden posts)."""
    post = (
        db.query(CommunityPost)
        .join(User, User.id == CommunityPost.author_id)
        .filter(
            CommunityPost.id == post_id,
            public_community_post_filter(),
            User.is_active == true(),
        )
        .populate_existing()
        .first()
    )
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )
    return post


def _can_view_post(post: CommunityPost, viewer: User | None) -> bool:
    if is_public_community_post(post):
        return True
    if viewer is None:
        return False
    if viewer.id == post.author_id:
        return True
    return is_admin_role(viewer.role) and resolve_canonical_admin_role(viewer.role) in (
        "super_admin",
        "content_moderator",
    )


def _recompute_likes_count(db: Session, post: CommunityPost) -> int:
    count = int(
        db.query(func.count(CommunityPostLike.id))
        .filter(CommunityPostLike.post_id == post.id)
        .scalar()
        or 0
    )
    post.likes_count = max(0, count)
    return post.likes_count


def _recompute_comments_count(db: Session, post: CommunityPost) -> int:
    count = int(
        db.query(func.count(CommunityComment.id))
        .filter(CommunityComment.post_id == post.id)
        .scalar()
        or 0
    )
    post.comments_count = max(0, count)
    return post.comments_count


# =============================================================================
# 1. POST LIKES (IDEMPOTENT + COUNTER CONSISTENCY + NOTIFICATIONS)
# =============================================================================

def like_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
) -> CommunityLikeResponse:
    community_like_limiter.check_identifier(f"community_like:user:{current_user.id}")

    lock_community_post(db, post_id)
    post = _get_published_post_or_404(db, post_id)

    existing = (
        db.query(CommunityPostLike)
        .filter(
            CommunityPostLike.post_id == post.id,
            CommunityPostLike.user_id == current_user.id,
        )
        .first()
    )
    if existing is not None:
        return CommunityLikeResponse(
            post_id=post.id,
            liked=True,
            likes_count=max(0, int(post.likes_count)),
        )

    created_new = False
    try:
        nested = db.begin_nested()
        try:
            db.add(CommunityPostLike(post_id=post.id, user_id=current_user.id))
            db.flush()
            nested.commit()
            created_new = True
        except IntegrityError:
            nested.rollback()
            created_new = False

        likes_count = _recompute_likes_count(db, post)

        if created_new and post.author_id != current_user.id:
            create_notification(
                db=db,
                user_id=post.author_id,
                title=f"{current_user.full_name} liked your post #{post.id}",
                message=f"{current_user.full_name} liked your Travel Community post.",
                notification_type="community_post_like",
                link_url=f"/community/posts/{post.id}",
            )

        _commit(db)
        return CommunityLikeResponse(
            post_id=post.id,
            liked=True,
            likes_count=likes_count,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def unlike_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
) -> CommunityLikeResponse:
    community_like_limiter.check_identifier(f"community_like:user:{current_user.id}")

    lock_community_post(db, post_id)
    post = _get_published_post_or_404(db, post_id)

    try:
        deleted_rows = (
            db.query(CommunityPostLike)
            .filter(
                CommunityPostLike.post_id == post.id,
                CommunityPostLike.user_id == current_user.id,
            )
            .delete(synchronize_session=False)
        )
        db.flush()
        likes_count = _recompute_likes_count(db, post) if deleted_rows else max(0, int(post.likes_count))
        _commit(db)
        return CommunityLikeResponse(
            post_id=post.id,
            liked=False,
            likes_count=likes_count,
        )
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 2. COMMENTS & THREADED REPLIES
# =============================================================================

def _build_comment_authors_map(
    db: Session,
    author_ids: list[int],
) -> dict[int, CommunityAuthorSummary]:
    if not author_ids:
        return {}

    users = db.query(User).filter(User.id.in_(author_ids)).all()
    provider_uids = [u.id for u in users if u.role == "provider"]
    approved_profiles: dict[int, ProviderProfile] = {}
    if provider_uids:
        profs = (
            db.query(ProviderProfile)
            .filter(
                ProviderProfile.user_id.in_(provider_uids),
                ProviderProfile.approval_status == "approved",
            )
            .all()
        )
        approved_profiles = {p.user_id: p for p in profs}

    result: dict[int, CommunityAuthorSummary] = {}
    for u in users:
        prof = approved_profiles.get(u.id)
        result[u.id] = CommunityAuthorSummary(
            id=u.id,
            full_name=u.full_name,
            role=u.role,
            is_verified_provider=prof is not None,
            business_name=prof.business_name if prof else None,
            provider_type=prof.provider_type if prof else None,
        )
    return result


def _serialize_comment_node(
    comment: CommunityComment,
    authors_map: dict[int, CommunityAuthorSummary],
    children_by_parent: dict[int, list[CommunityComment]],
) -> CommunityCommentResponse:
    author = authors_map.get(comment.author_id) or CommunityAuthorSummary(
        id=comment.author_id,
        full_name="Community Member",
        role="traveler",
    )
    direct_children = children_by_parent.get(comment.id, [])
    serialized_replies = [
        _serialize_comment_node(child, authors_map, children_by_parent)
        for child in direct_children
    ]
    return CommunityCommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author_id=comment.author_id,
        author=author,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        is_redacted=(
            comment.status != "active" or comment.content in _REDACTED_PLACEHOLDERS
        ),
        reply_count=len(serialized_replies),
        replies=serialized_replies,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


def list_post_comments(
    db: Session,
    *,
    post_id: int,
    viewer: User | None = None,
    page: int = 1,
    page_size: int = 20,
    parent_comment_id: int | None = None,
) -> CommunityCommentListResponse:
    post = db.get(CommunityPost, post_id)
    if post is None or not _can_view_post(post, viewer):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )

    if parent_comment_id is not None:
        parent = db.get(CommunityComment, parent_comment_id)
        if parent is None or parent.post_id != post.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent comment not found for this post.",
            )
        roots_query = db.query(CommunityComment).filter(
            CommunityComment.post_id == post.id,
            CommunityComment.parent_comment_id == parent_comment_id,
        )
    else:
        roots_query = db.query(CommunityComment).filter(
            CommunityComment.post_id == post.id,
            CommunityComment.parent_comment_id.is_(None),
        )

    total = roots_query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    roots = (
        roots_query.order_by(CommunityComment.created_at.asc(), CommunityComment.id.asc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    if not roots:
        return CommunityCommentListResponse(
            post_id=post.id,
            items=[],
            total=total,
            total_comments_on_post=max(0, int(post.comments_count)),
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=False,
            has_previous=(page > 1 and total > 0),
        )

    # Load all replies for this post in one query to assemble nested threads without N+1
    all_replies = (
        db.query(CommunityComment)
        .filter(
            CommunityComment.post_id == post.id,
            CommunityComment.parent_comment_id.is_not(None),
        )
        .order_by(CommunityComment.created_at.asc(), CommunityComment.id.asc())
        .all()
    )
    children_by_parent: dict[int, list[CommunityComment]] = {}
    for reply in all_replies:
        assert reply.parent_comment_id is not None
        children_by_parent.setdefault(reply.parent_comment_id, []).append(reply)

    # Collect author IDs across roots and reachable descendant replies
    all_nodes: list[CommunityComment] = []
    stack = list(roots)
    while stack:
        node = stack.pop()
        all_nodes.append(node)
        stack.extend(children_by_parent.get(node.id, []))

    authors_map = _build_comment_authors_map(
        db, list({n.author_id for n in all_nodes})
    )
    items = [_serialize_comment_node(r, authors_map, children_by_parent) for r in roots]

    return CommunityCommentListResponse(
        post_id=post.id,
        items=items,
        total=total,
        total_comments_on_post=max(0, int(post.comments_count)),
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


def create_post_comment(
    db: Session,
    *,
    post_id: int,
    current_user: User,
    payload: CommunityCommentCreate,
) -> CommunityCommentResponse:
    community_comment_limiter.check_identifier(f"community_comment:user:{current_user.id}")

    content = sanitize_community_text(
        payload.content,
        field_name="content",
        max_length=1000,
        allow_newlines=True,
        required=True,
    )
    assert content is not None

    lock_community_post(db, post_id)
    post = _get_published_post_or_404(db, post_id)

    parent_comment: CommunityComment | None = None
    if payload.parent_comment_id is not None:
        parent_comment = db.get(CommunityComment, payload.parent_comment_id)
        if parent_comment is None or parent_comment.post_id != post.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid parent_comment_id: parent comment does not belong to this post.",
            )

    try:
        comment = CommunityComment(
            post_id=post.id,
            author_id=current_user.id,
            parent_comment_id=parent_comment.id if parent_comment else None,
            content=content,
        )
        db.add(comment)
        db.flush()
        _recompute_comments_count(db, post)

        snippet = (content[:117] + "...") if len(content) > 120 else content
        link = f"/community/posts/{post.id}#comment-{comment.id}"

        if parent_comment is not None:
            # Notify parent comment author on reply (never self-notify)
            if parent_comment.author_id != current_user.id:
                create_notification(
                    db=db,
                    user_id=parent_comment.author_id,
                    title=f"{current_user.full_name} replied to your comment on post #{post.id}",
                    message=snippet,
                    notification_type="community_comment_reply",
                    link_url=link,
                )
            # Also notify post author if distinct from commenter AND distinct from parent comment author
            if (
                post.author_id != current_user.id
                and post.author_id != parent_comment.author_id
            ):
                create_notification(
                    db=db,
                    user_id=post.author_id,
                    title=f"{current_user.full_name} commented on your post #{post.id}",
                    message=snippet,
                    notification_type="community_post_comment",
                    link_url=link,
                )
        else:
            # Top-level comment notifies post author (never self-notify)
            if post.author_id != current_user.id:
                create_notification(
                    db=db,
                    user_id=post.author_id,
                    title=f"{current_user.full_name} commented on your post #{post.id}",
                    message=snippet,
                    notification_type="community_post_comment",
                    link_url=link,
                )

        _commit(db)
        db.refresh(comment)
        authors_map = _build_comment_authors_map(db, [comment.author_id])
        return _serialize_comment_node(comment, authors_map, {})
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def update_community_comment(
    db: Session,
    *,
    comment_id: int,
    current_user: User,
    payload: CommunityCommentUpdate,
) -> CommunityCommentResponse:
    comment = db.get(CommunityComment, comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found.",
        )

    if comment.author_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own comments.",
        )

    post = db.get(CommunityPost, comment.post_id)
    if post is None or (not is_public_community_post(post) and post.author_id != current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )

    if comment.status != "active" or comment.content in _REDACTED_PLACEHOLDERS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A deleted or moderated comment cannot be edited.",
        )

    sanitized = sanitize_community_text(
        payload.content,
        field_name="content",
        max_length=1000,
        allow_newlines=True,
        required=True,
    )
    assert sanitized is not None

    try:
        comment.content = sanitized
        comment.updated_at = _now_utc()
        _commit(db)
        db.refresh(comment)
        authors_map = _build_comment_authors_map(db, [comment.author_id])
        return _serialize_comment_node(comment, authors_map, {})
    except SQLAlchemyError:
        db.rollback()
        raise


def execute_comment_deletion_uncommitted(
    db: Session,
    *,
    comment_id: int,
    current_user: User,
    force_admin_redaction_label: bool = False,
) -> tuple[CommunityCommentDeleteResponse, dict[str, object]]:
    """
    Execute FK-safe comment redaction or hard-delete within the caller's active transaction
    (without committing) so admin moderation can attach an atomic AdminAuditLog entry.
    """
    comment = db.get(CommunityComment, comment_id)
    if comment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found.",
        )

    is_owner = comment.author_id == current_user.id
    is_admin = is_admin_role(current_user.role) and resolve_canonical_admin_role(current_user.role) in (
        "super_admin",
        "content_moderator",
    )
    if not is_owner and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own comments.",
        )

    post = db.get(CommunityPost, comment.post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )

    lock_community_post(db, post.id)
    comment = db.get(CommunityComment, comment_id, populate_existing=True)
    post = db.get(CommunityPost, post.id, populate_existing=True)
    if comment is None or post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found.")

    has_reports = (
        db.query(CommunityReport.id)
        .filter(
            CommunityReport.entity_type == "comment",
            CommunityReport.entity_id == comment.id,
        )
        .first()
        is not None
    )
    if has_reports and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Comment cannot be deleted while moderation reports reference it.",
        )

    has_replies = (
        db.query(CommunityComment.id)
        .filter(CommunityComment.parent_comment_id == comment.id)
        .first()
        is not None
    )

    comment_author_id = comment.author_id
    post_id = post.id

    if has_replies or has_reports:
        use_admin_label = force_admin_redaction_label or (is_admin and not is_owner)
        comment.content = REDACTED_BY_ADMIN_TEXT if use_admin_label else REDACTED_BY_AUTHOR_TEXT
        comment.status = "removed_by_moderator" if use_admin_label else "deleted_by_author"
        comment.updated_at = _now_utc()
        db.flush()
        comments_count = _recompute_comments_count(db, post)
        response = CommunityCommentDeleteResponse(
            deleted=True,
            comment_id=comment.id,
            post_id=post_id,
            comments_count=comments_count,
            deletion_mode="redacted_thread_preserved",
            detail=(
                "Comment content was redacted in place to preserve reply-thread integrity "
                "and SQL Server foreign-key safety (ON DELETE NO ACTION)."
            ),
        )
    else:
        db.delete(comment)
        db.flush()
        comments_count = _recompute_comments_count(db, post)
        response = CommunityCommentDeleteResponse(
            deleted=True,
            comment_id=comment_id,
            post_id=post_id,
            comments_count=comments_count,
            deletion_mode="hard_deleted",
            detail="Comment permanently deleted.",
        )

    meta: dict[str, object] = {
        "comment_id": comment_id,
        "post_id": post_id,
        "comment_author_id": comment_author_id,
        "had_replies": has_replies,
        "had_reports": has_reports,
        "deletion_mode": response.deletion_mode,
        "comments_count": response.comments_count,
    }
    return response, meta


def delete_community_comment(
    db: Session,
    *,
    comment_id: int,
    current_user: User,
) -> CommunityCommentDeleteResponse:
    try:
        response, _meta = execute_comment_deletion_uncommitted(
            db,
            comment_id=comment_id,
            current_user=current_user,
        )
        _commit(db)
        return response
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 3. PRIVATE SAVED POSTS (BOOKMARKS)
# =============================================================================

def save_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
) -> CommunitySaveResponse:
    community_save_limiter.check_identifier(f"community_save:user:{current_user.id}")
    post = _get_published_post_or_404(db, post_id)

    existing = (
        db.query(CommunitySavedPost)
        .filter(
            CommunitySavedPost.user_id == current_user.id,
            CommunitySavedPost.post_id == post.id,
        )
        .first()
    )
    if existing is not None:
        return CommunitySaveResponse(post_id=post.id, saved=True)

    try:
        nested = db.begin_nested()
        try:
            db.add(CommunitySavedPost(user_id=current_user.id, post_id=post.id))
            db.flush()
            nested.commit()
        except IntegrityError:
            nested.rollback()
        _commit(db)
        return CommunitySaveResponse(post_id=post.id, saved=True)
    except SQLAlchemyError:
        db.rollback()
        raise


def unsave_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
) -> CommunitySaveResponse:
    community_save_limiter.check_identifier(f"community_save:user:{current_user.id}")

    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )

    try:
        db.query(CommunitySavedPost).filter(
            CommunitySavedPost.user_id == current_user.id,
            CommunitySavedPost.post_id == post.id,
        ).delete(synchronize_session=False)
        _commit(db)
        return CommunitySaveResponse(post_id=post.id, saved=False)
    except SQLAlchemyError:
        db.rollback()
        raise


def list_saved_community_posts(
    db: Session,
    *,
    current_user: User,
    page: int = 1,
    page_size: int = 20,
) -> CommunitySavedPostsResponse:
    """Return ONLY the authenticated caller's private saved posts, excluding unpublished/inactive posts."""
    base_query = (
        db.query(CommunityPost)
        .join(CommunitySavedPost, CommunitySavedPost.post_id == CommunityPost.id)
        .join(User, User.id == CommunityPost.author_id)
        .filter(
            CommunitySavedPost.user_id == current_user.id,
            public_community_post_filter(),
            User.is_active == true(),
        )
    )

    total = base_query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    posts = (
        base_query.order_by(
            CommunitySavedPost.created_at.desc(),
            CommunitySavedPost.id.desc(),
        )
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = serialize_posts_batch(db, posts, viewer=current_user)

    return CommunitySavedPostsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )
