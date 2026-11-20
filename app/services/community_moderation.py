"""
Travel Community & Social Feed — Phase 5 & Step 5C Governance Services
(Community Reporting, Admin Report Queue & Review, Admin Post Visibility & Featuring,
Admin Comment Moderation, Community-Only User Suspension, Hashtag Governance,
Admin Activity Summary & Transactional AdminAuditLog Integration).

Strictly aligned with Migration 028 + Migration 039 + dbo.admin_audit_logs:
- Uses dbo.community_reports, dbo.community_posts (is_published, is_featured, moderation_status),
  dbo.community_comments (status), dbo.community_hashtags (is_blocked, is_featured),
  dbo.community_user_profiles, dbo.users (read-only), and dbo.admin_audit_logs.
- NEVER modifies dbo.users.is_active for community-only suspension.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import math
import os
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import false, func, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.community import (
    CommunityComment,
    CommunityHashtag,
    CommunityPost,
    CommunityReport,
    CommunityUserProfile,
)
from app.models.shared import AdminAuditLog
from app.models.user import User
from app.schemas.community import (
    AdminCommunityActivityResponse,
    AdminCommunityAuditLogItem,
    AdminCommunityCommentModerationResponse,
    AdminCommunityHashtagItem,
    AdminCommunityHashtagListResponse,
    AdminCommunityHashtagModerationResponse,
    AdminCommunityHashtagUpdate,
    AdminCommunityPostModerationRequest,
    AdminCommunityPostModerationResponse,
    AdminCommunityProfileSuspensionRequest,
    AdminCommunityProfileSuspensionResponse,
    AdminCommunityReportItem,
    AdminCommunityReportListResponse,
    AdminCommunityReportReviewResponse,
    AdminCommunityReportUpdate,
    AdminSafeUserRef,
    CommunityReportCreate,
    CommunityReportResponse,
    CommunityReportTargetPreview,
)
from app.services.community_engagement import execute_comment_deletion_uncommitted
from app.services.community_posts import sanitize_community_text

# =============================================================================
# CONSTANTS & RATE LIMITER
# =============================================================================

OPEN_REPORT_STATUSES: frozenset[str] = frozenset({"pending", "investigating"})

ALLOWED_REPORT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"investigating", "resolved", "dismissed"}),
    "investigating": frozenset({"resolved", "dismissed"}),
    "resolved": frozenset(),
    "dismissed": frozenset(),
}

COMMUNITY_AUDIT_ENTITY_NAMES: tuple[str, ...] = (
    "community_report",
    "community_post",
    "community_comment",
    "community_user_profile",
    "community_hashtag",
)

PENDING_MIGRATION_037_MODERATION_FEATURES: list[str] = []

community_report_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("COMMUNITY_REPORT_RATE_LIMIT", "15")),
    window_seconds=int(os.getenv("COMMUNITY_REPORT_RATE_WINDOW_SECONDS", "3600")),
    error_message="Too many report submissions. Please try again later.",
)


def reset_community_report_limiter() -> None:
    """Reset in-memory report rate limiter window (for tests)."""
    community_report_limiter.reset()


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def is_profile_currently_suspended(
    profile: CommunityUserProfile | None,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True iff the user has an active, non-expired community suspension."""
    if profile is None or not bool(profile.is_community_suspended):
        return False
    if profile.suspended_until is None:
        return True
    current_time = now if now is not None else _now_utc()
    until_naive = _normalize_utc_naive(profile.suspended_until)
    assert until_naive is not None
    return until_naive > current_time


def enforce_community_not_suspended(db: Session, current_user: User) -> User:
    """
    Centralized enforcement guard for all community user write endpoints.
    - Admin accounts retain moderation capabilities and are never blocked here.
    - Reads dbo.community_user_profiles only; NEVER reads or modifies dbo.users.is_active.
    - Expired suspensions (suspended_until <= now UTC) are automatically treated as unsuspended.
    """
    if current_user.role == "admin":
        return current_user

    profile = db.get(CommunityUserProfile, current_user.id)
    now = _now_utc()
    if not is_profile_currently_suspended(profile, now=now):
        return current_user

    assert profile is not None
    if profile.suspended_until is not None:
        until_naive = _normalize_utc_naive(profile.suspended_until)
        assert until_naive is not None
        detail = (
            "Your Travel Community posting and interaction privileges are suspended "
            f"until {until_naive.isoformat()}Z."
        )
    else:
        detail = (
            "Your Travel Community posting and interaction privileges are currently suspended."
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail,
    )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _collapse_preview(text: str | None, max_len: int = 160) -> str | None:
    if text is None:
        return None
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 3] + "..."


# =============================================================================
# TRANSACTIONAL ADMIN AUDIT LOGGING
# =============================================================================

def record_community_admin_audit(
    db: Session,
    *,
    admin_user_id: int,
    action: str,
    entity_name: str,
    entity_id: int,
    details: dict[str, Any] | str | None = None,
    ip_address: str | None = None,
) -> AdminAuditLog:
    """
    Create and flush an AdminAuditLog row inside the caller's active DB transaction.
    Never catches or suppresses errors so moderation actions and audit logs commit
    or roll back atomically together.
    """
    serialized: str | None
    if isinstance(details, dict):
        serialized = json.dumps(details, default=str)
    else:
        serialized = details

    entry = AdminAuditLog(
        admin_user_id=admin_user_id,
        action=action[:50],
        entity_name=entity_name[:50],
        entity_id=entity_id,
        details=serialized,
        ip_address=ip_address[:45] if ip_address else None,
    )
    db.add(entry)
    db.flush()
    return entry


# =============================================================================
# 1. COMMUNITY REPORTING (JWT)
# =============================================================================

def _validate_report_target(
    db: Session,
    *,
    entity_type: str,
    entity_id: int,
    reporter: User,
) -> None:
    """
    Verify that the polymorphic report target exists, is accessible to the reporter,
    and is not a self-report.
    """
    is_admin = reporter.role == "admin"

    if entity_type == "post":
        post = db.get(CommunityPost, entity_id)
        if post is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report target post not found.",
            )
        author = db.get(User, post.author_id)
        author_active = author is not None and bool(author.is_active)
        if not is_admin and (not post.is_published or not author_active):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report target post not found.",
            )
        if post.author_id == reporter.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot report your own post.",
            )
        return

    if entity_type == "comment":
        comment = db.get(CommunityComment, entity_id)
        if comment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report target comment not found.",
            )
        post = db.get(CommunityPost, comment.post_id)
        post_author = db.get(User, post.author_id) if post else None
        post_accessible = (
            post is not None
            and post.is_published
            and post_author is not None
            and bool(post_author.is_active)
        )
        if not is_admin and not post_accessible:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report target comment not found.",
            )
        if comment.author_id == reporter.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot report your own comment.",
            )
        return

    if entity_type == "user":
        if entity_id == reporter.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot report yourself.",
            )
        target_user = db.get(User, entity_id)
        if target_user is None or not target_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report target user not found.",
            )
        return

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid entity_type. Must be 'post', 'comment', or 'user'.",
    )


def submit_community_report(
    db: Session,
    *,
    current_user: User,
    payload: CommunityReportCreate,
) -> CommunityReportResponse:
    community_report_limiter.check_identifier(f"community_report:user:{current_user.id}")

    reason = sanitize_community_text(
        payload.report_reason,
        field_name="report_reason",
        max_length=100,
        allow_newlines=False,
        required=True,
    )
    assert reason is not None
    if len(reason) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="report_reason must be at least 3 characters after sanitization.",
        )

    description = sanitize_community_text(
        payload.description,
        field_name="description",
        max_length=1000,
        allow_newlines=True,
        required=False,
    )

    _validate_report_target(
        db,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        reporter=current_user,
    )

    # Prevent duplicate concurrent open reports from the same user for the same target
    lock_booking_resource(
        db,
        f"community_report_{current_user.id}_{payload.entity_type}_{payload.entity_id}",
    )

    existing_open = (
        db.query(CommunityReport.id)
        .filter(
            CommunityReport.reporter_id == current_user.id,
            CommunityReport.entity_type == payload.entity_type,
            CommunityReport.entity_id == payload.entity_id,
            CommunityReport.report_status.in_(tuple(OPEN_REPORT_STATUSES)),
        )
        .first()
    )
    if existing_open is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an open report for this target.",
        )

    try:
        report = CommunityReport(
            reporter_id=current_user.id,
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            report_reason=reason,
            description=description,
            report_status="pending",
        )
        db.add(report)
        _commit(db)
        db.refresh(report)
        return CommunityReportResponse(
            id=report.id,
            reporter_id=report.reporter_id,
            entity_type=report.entity_type,  # type: ignore[arg-type]
            entity_id=report.entity_id,
            report_reason=report.report_reason,
            description=report.description,
            report_status=report.report_status,  # type: ignore[arg-type]
            created_at=report.created_at,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# BATCH REPORT HYDRATION (N+1 FREE, ZERO PRIVATE PII)
# =============================================================================

def _hydrate_admin_reports(
    db: Session,
    reports: list[CommunityReport],
) -> list[AdminCommunityReportItem]:
    if not reports:
        return []

    post_ids = {r.entity_id for r in reports if r.entity_type == "post"}
    comment_ids = {r.entity_id for r in reports if r.entity_type == "comment"}
    user_target_ids = {r.entity_id for r in reports if r.entity_type == "user"}

    posts_by_id: dict[int, CommunityPost] = {}
    if post_ids:
        posts_by_id = {
            p.id: p
            for p in db.query(CommunityPost).filter(CommunityPost.id.in_(post_ids)).all()
        }

    comments_by_id: dict[int, CommunityComment] = {}
    if comment_ids:
        comments_by_id = {
            c.id: c
            for c in db.query(CommunityComment)
            .filter(CommunityComment.id.in_(comment_ids))
            .all()
        }

    # Collect every user ID needed across reporters, reviewers, target users, and content authors
    user_ids: set[int] = set(user_target_ids)
    for r in reports:
        user_ids.add(r.reporter_id)
        if r.reviewed_by is not None:
            user_ids.add(r.reviewed_by)
    for p in posts_by_id.values():
        user_ids.add(p.author_id)
    for c in comments_by_id.values():
        user_ids.add(c.author_id)

    users_by_id: dict[int, User] = {}
    if user_ids:
        users_by_id = {
            u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
        }

    def _safe_user_ref(uid: int | None) -> AdminSafeUserRef | None:
        if uid is None:
            return None
        u = users_by_id.get(uid)
        if u is None:
            return None
        return AdminSafeUserRef(id=u.id, full_name=u.full_name, role=u.role)

    items: list[AdminCommunityReportItem] = []
    for r in reports:
        if r.entity_type == "post":
            post = posts_by_id.get(r.entity_id)
            if post is not None:
                author = users_by_id.get(post.author_id)
                preview = CommunityReportTargetPreview(
                    entity_type="post",
                    entity_id=r.entity_id,
                    exists=True,
                    summary=_collapse_preview(post.content),
                    author_id=post.author_id,
                    author_name=author.full_name if author else None,
                    is_published=post.is_published,
                )
            else:
                preview = CommunityReportTargetPreview(
                    entity_type="post",
                    entity_id=r.entity_id,
                    exists=False,
                )
        elif r.entity_type == "comment":
            comment = comments_by_id.get(r.entity_id)
            if comment is not None:
                author = users_by_id.get(comment.author_id)
                preview = CommunityReportTargetPreview(
                    entity_type="comment",
                    entity_id=r.entity_id,
                    exists=True,
                    summary=_collapse_preview(comment.content),
                    author_id=comment.author_id,
                    author_name=author.full_name if author else None,
                    post_id=comment.post_id,
                )
            else:
                preview = CommunityReportTargetPreview(
                    entity_type="comment",
                    entity_id=r.entity_id,
                    exists=False,
                )
        else:
            target_u = users_by_id.get(r.entity_id)
            if target_u is not None:
                preview = CommunityReportTargetPreview(
                    entity_type="user",
                    entity_id=r.entity_id,
                    exists=True,
                    summary=f"{target_u.full_name} ({target_u.role})",
                    author_id=target_u.id,
                    author_name=target_u.full_name,
                    is_active=target_u.is_active,
                )
            else:
                preview = CommunityReportTargetPreview(
                    entity_type="user",
                    entity_id=r.entity_id,
                    exists=False,
                )

        items.append(
            AdminCommunityReportItem(
                id=r.id,
                reporter_id=r.reporter_id,
                reporter=_safe_user_ref(r.reporter_id),
                entity_type=r.entity_type,  # type: ignore[arg-type]
                entity_id=r.entity_id,
                report_reason=r.report_reason,
                description=r.description,
                report_status=r.report_status,  # type: ignore[arg-type]
                reviewed_by=r.reviewed_by,
                reviewer=_safe_user_ref(r.reviewed_by),
                reviewed_at=r.reviewed_at,
                review_notes=r.review_notes,
                created_at=r.created_at,
                target_preview=preview,
            )
        )

    return items


# =============================================================================
# 2. ADMIN REPORT MANAGEMENT
# =============================================================================

def list_admin_community_reports(
    db: Session,
    *,
    report_status: str | None = None,
    entity_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminCommunityReportListResponse:
    query = db.query(CommunityReport)
    if report_status is not None:
        query = query.filter(CommunityReport.report_status == report_status)
    if entity_type is not None:
        query = query.filter(CommunityReport.entity_type == entity_type)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    rows = (
        query.order_by(CommunityReport.created_at.desc(), CommunityReport.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = _hydrate_admin_reports(db, rows)

    return AdminCommunityReportListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


def review_admin_community_report(
    db: Session,
    *,
    report_id: int,
    admin_user: User,
    payload: AdminCommunityReportUpdate,
    ip_address: str | None = None,
) -> AdminCommunityReportReviewResponse:
    report = db.get(CommunityReport, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community report not found.",
        )

    lock_booking_resource(db, f"community_report_review_{report.id}")
    db.refresh(report)

    previous_status = report.report_status
    new_status = payload.report_status
    allowed_next = ALLOWED_REPORT_TRANSITIONS.get(previous_status, frozenset())
    if new_status not in allowed_next:
        allowed_label = ", ".join(sorted(allowed_next)) if allowed_next else "none (terminal state)"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid report status transition from '{previous_status}' to '{new_status}'. "
                f"Allowed transitions: {allowed_label}."
            ),
        )

    sanitized_notes = sanitize_community_text(
        payload.review_notes,
        field_name="review_notes",
        max_length=500,
        allow_newlines=True,
        required=False,
    )

    try:
        report.report_status = new_status
        report.reviewed_by = admin_user.id
        report.reviewed_at = _now_utc()
        if payload.review_notes is not None:
            report.review_notes = sanitized_notes
        db.flush()

        audit_entry = record_community_admin_audit(
            db,
            admin_user_id=admin_user.id,
            action="community_report_review",
            entity_name="community_report",
            entity_id=report.id,
            details={
                "previous_status": previous_status,
                "new_status": new_status,
                "target_entity_type": report.entity_type,
                "target_entity_id": report.entity_id,
                "review_notes": report.review_notes,
            },
            ip_address=ip_address,
        )

        _commit(db)
        db.refresh(report)
        hydrated = _hydrate_admin_reports(db, [report])[0]
        return AdminCommunityReportReviewResponse(
            report=hydrated,
            previous_status=previous_status,  # type: ignore[arg-type]
            audit_log_id=audit_entry.id,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 3. ADMIN POST MODERATION (HIDE / RESTORE / FEATURE / UNFEATURE)
# =============================================================================

def moderate_admin_community_post(
    db: Session,
    *,
    post_id: int,
    admin_user: User,
    payload: AdminCommunityPostModerationRequest,
    ip_address: str | None = None,
) -> AdminCommunityPostModerationResponse:
    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )

    lock_booking_resource(db, f"community_post_mod_{post.id}")
    db.refresh(post)

    sanitized_reason = sanitize_community_text(
        payload.reason,
        field_name="reason",
        max_length=500,
        allow_newlines=True,
        required=False,
    )

    previous_is_published = bool(post.is_published)
    previous_is_featured = bool(post.is_featured)
    previous_moderation_status = post.moderation_status

    target_is_published = (
        payload.is_published if payload.is_published is not None else previous_is_published
    )
    if payload.is_published is not None:
        target_moderation_status = "active" if payload.is_published else "hidden_by_admin"
    else:
        target_moderation_status = previous_moderation_status

    if payload.is_featured is True:
        if not target_is_published or target_moderation_status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only published, active community posts can be featured.",
            )
        target_is_featured = True
    elif payload.is_featured is False:
        target_is_featured = False
    else:
        # Auto-clear featured flag whenever an admin hides a post
        target_is_featured = False if payload.is_published is False else previous_is_featured

    if payload.is_published is False:
        action = "community_post_hide"
    elif payload.is_published is True:
        action = "community_post_restore"
    elif payload.is_featured is True:
        action = "community_post_feature"
    else:
        action = "community_post_unfeature"

    try:
        post.is_published = target_is_published
        post.moderation_status = target_moderation_status
        post.is_featured = target_is_featured
        post.updated_at = _now_utc()
        db.flush()

        audit_entry = record_community_admin_audit(
            db,
            admin_user_id=admin_user.id,
            action=action,
            entity_name="community_post",
            entity_id=post.id,
            details={
                "author_id": post.author_id,
                "previous_is_published": previous_is_published,
                "new_is_published": bool(post.is_published),
                "previous_is_featured": previous_is_featured,
                "new_is_featured": bool(post.is_featured),
                "previous_moderation_status": previous_moderation_status,
                "new_moderation_status": post.moderation_status,
                "reason": sanitized_reason,
            },
            ip_address=ip_address,
        )

        _commit(db)
        db.refresh(post)
        return AdminCommunityPostModerationResponse(
            post_id=post.id,
            author_id=post.author_id,
            is_published=bool(post.is_published),
            previous_is_published=previous_is_published,
            is_featured=bool(post.is_featured),
            previous_is_featured=previous_is_featured,
            moderation_status=post.moderation_status,
            action=action,  # type: ignore[arg-type]
            reason=sanitized_reason,
            updated_at=post.updated_at,
            audit_log_id=audit_entry.id,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 3B. ADMIN COMMUNITY-ONLY USER SUSPENSION (TC-AD-02 / MIGRATION 039)
# =============================================================================

def moderate_admin_community_profile_suspension(
    db: Session,
    *,
    user_id: int,
    admin_user: User,
    payload: AdminCommunityProfileSuspensionRequest,
    ip_address: str | None = None,
) -> AdminCommunityProfileSuspensionResponse:
    """
    Suspend or unsuspend a user's Travel Community write access ONLY via
    dbo.community_user_profiles. NEVER modifies dbo.users.is_active.
    """
    target_user = db.get(User, user_id)
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found.",
        )

    if target_user.role == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administrator accounts cannot be community-suspended.",
        )

    lock_booking_resource(db, f"community_profile_susp_{target_user.id}")

    profile = db.get(CommunityUserProfile, target_user.id)
    if profile is not None:
        db.refresh(profile)

    previous_is_suspended = bool(profile.is_community_suspended) if profile is not None else False
    previous_suspended_until = profile.suspended_until if profile is not None else None
    now = _now_utc()

    if payload.is_community_suspended:
        sanitized_reason = sanitize_community_text(
            payload.suspension_reason,
            field_name="suspension_reason",
            max_length=500,
            allow_newlines=True,
            required=True,
        )
        assert sanitized_reason is not None
        if len(sanitized_reason) < 3:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="suspension_reason must be at least 3 characters after sanitization.",
            )

        until_naive = _normalize_utc_naive(payload.suspended_until)
        if until_naive is not None and until_naive <= now:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="suspended_until must be strictly in the future (UTC).",
            )

        action = "community_user_suspend"
        audit_note = sanitized_reason
    else:
        audit_note = sanitize_community_text(
            payload.suspension_reason,
            field_name="suspension_reason",
            max_length=500,
            allow_newlines=True,
            required=False,
        )
        sanitized_reason = None
        until_naive = None
        action = "community_user_unsuspend"

    try:
        if profile is None:
            profile = CommunityUserProfile(
                user_id=target_user.id,
                is_community_suspended=payload.is_community_suspended,
                suspended_until=until_naive if payload.is_community_suspended else None,
                suspension_reason=sanitized_reason if payload.is_community_suspended else None,
                suspended_by=admin_user.id if payload.is_community_suspended else None,
                suspended_at=now if payload.is_community_suspended else None,
                updated_at=now,
            )
            db.add(profile)
        else:
            if payload.is_community_suspended:
                profile.is_community_suspended = True
                profile.suspended_until = until_naive
                profile.suspension_reason = sanitized_reason
                profile.suspended_by = admin_user.id
                profile.suspended_at = now
            else:
                profile.is_community_suspended = False
                profile.suspended_until = None
                profile.suspension_reason = None
                profile.suspended_by = None
                profile.suspended_at = None
            profile.updated_at = now

        db.flush()

        audit_entry = record_community_admin_audit(
            db,
            admin_user_id=admin_user.id,
            action=action,
            entity_name="community_user_profile",
            entity_id=target_user.id,
            details={
                "target_user_id": target_user.id,
                "target_role": target_user.role,
                "previous_is_community_suspended": previous_is_suspended,
                "new_is_community_suspended": bool(profile.is_community_suspended),
                "previous_suspended_until": (
                    previous_suspended_until.isoformat() if previous_suspended_until else None
                ),
                "new_suspended_until": (
                    profile.suspended_until.isoformat() if profile.suspended_until else None
                ),
                "reason": audit_note,
            },
            ip_address=ip_address,
        )

        _commit(db)
        db.refresh(profile)
        return AdminCommunityProfileSuspensionResponse(
            user_id=profile.user_id,
            is_community_suspended=bool(profile.is_community_suspended),
            is_currently_suspended=is_profile_currently_suspended(profile),
            suspended_until=profile.suspended_until,
            suspension_reason=profile.suspension_reason,
            suspended_by=profile.suspended_by,
            suspended_at=profile.suspended_at,
            updated_at=profile.updated_at,
            action=action,  # type: ignore[arg-type]
            audit_log_id=audit_entry.id,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 3C. ADMIN HASHTAG GOVERNANCE (BLOCKED & FEATURED HASHTAGS — TC-AD-02)
# =============================================================================

def list_admin_community_hashtags(
    db: Session,
    *,
    is_blocked: bool | None = None,
    is_featured: bool | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminCommunityHashtagListResponse:
    query = db.query(CommunityHashtag)
    if is_blocked is True:
        query = query.filter(CommunityHashtag.is_blocked == true())
    elif is_blocked is False:
        query = query.filter(CommunityHashtag.is_blocked == false())

    if is_featured is True:
        query = query.filter(CommunityHashtag.is_featured == true())
    elif is_featured is False:
        query = query.filter(CommunityHashtag.is_featured == false())

    if q is not None:
        cleaned = q.strip().lstrip("#").lower()
        if cleaned:
            pattern = f"%{_escape_like(cleaned)}%"
            query = query.filter(CommunityHashtag.tag.like(pattern, escape="\\"))

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    rows = (
        query.order_by(
            CommunityHashtag.is_blocked.desc(),
            CommunityHashtag.is_featured.desc(),
            CommunityHashtag.usage_count.desc(),
            CommunityHashtag.tag.asc(),
            CommunityHashtag.id.asc(),
        )
        .offset(offset)
        .limit(page_size)
        .all()
    )

    items = [AdminCommunityHashtagItem.model_validate(row) for row in rows]
    return AdminCommunityHashtagListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


def moderate_admin_community_hashtag(
    db: Session,
    *,
    hashtag_id: int,
    admin_user: User,
    payload: AdminCommunityHashtagUpdate,
    ip_address: str | None = None,
) -> AdminCommunityHashtagModerationResponse:
    hashtag = db.get(CommunityHashtag, hashtag_id)
    if hashtag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community hashtag not found.",
        )

    lock_booking_resource(db, f"community_hashtag_mod_{hashtag.id}")
    db.refresh(hashtag)

    sanitized_reason = sanitize_community_text(
        payload.reason,
        field_name="reason",
        max_length=500,
        allow_newlines=True,
        required=False,
    )

    previous_is_blocked = bool(hashtag.is_blocked)
    previous_is_featured = bool(hashtag.is_featured)

    target_is_blocked = (
        payload.is_blocked if payload.is_blocked is not None else previous_is_blocked
    )
    target_is_featured = (
        payload.is_featured if payload.is_featured is not None else previous_is_featured
    )

    if target_is_blocked and target_is_featured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A hashtag cannot be both blocked and featured simultaneously.",
        )

    if payload.is_blocked is not None and payload.is_featured is not None:
        if target_is_blocked:
            action = "community_hashtag_block"
        elif target_is_featured:
            action = "community_hashtag_feature"
        else:
            action = "community_hashtag_moderate"
    elif payload.is_blocked is True:
        action = "community_hashtag_block"
    elif payload.is_blocked is False:
        action = "community_hashtag_unblock"
    elif payload.is_featured is True:
        action = "community_hashtag_feature"
    else:
        action = "community_hashtag_unfeature"

    try:
        hashtag.is_blocked = target_is_blocked
        hashtag.is_featured = target_is_featured
        db.flush()

        audit_entry = record_community_admin_audit(
            db,
            admin_user_id=admin_user.id,
            action=action,
            entity_name="community_hashtag",
            entity_id=hashtag.id,
            details={
                "tag": hashtag.tag,
                "previous_is_blocked": previous_is_blocked,
                "new_is_blocked": bool(hashtag.is_blocked),
                "previous_is_featured": previous_is_featured,
                "new_is_featured": bool(hashtag.is_featured),
                "reason": sanitized_reason,
            },
            ip_address=ip_address,
        )

        _commit(db)
        db.refresh(hashtag)
        return AdminCommunityHashtagModerationResponse(
            hashtag=AdminCommunityHashtagItem.model_validate(hashtag),
            previous_is_blocked=previous_is_blocked,
            previous_is_featured=previous_is_featured,
            action=action,  # type: ignore[arg-type]
            reason=sanitized_reason,
            audit_log_id=audit_entry.id,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 4. ADMIN COMMENT MODERATION (FK-SAFE REDACTION / DELETION + AUDIT)
# =============================================================================

def moderate_admin_delete_comment(
    db: Session,
    *,
    comment_id: int,
    admin_user: User,
    reason: str | None = None,
    ip_address: str | None = None,
) -> AdminCommunityCommentModerationResponse:
    sanitized_reason = sanitize_community_text(
        reason,
        field_name="reason",
        max_length=500,
        allow_newlines=True,
        required=False,
    )

    try:
        delete_result, meta = execute_comment_deletion_uncommitted(
            db,
            comment_id=comment_id,
            current_user=admin_user,
            force_admin_redaction_label=True,
        )

        audit_entry = record_community_admin_audit(
            db,
            admin_user_id=admin_user.id,
            action="community_comment_moderate_delete",
            entity_name="community_comment",
            entity_id=comment_id,
            details={
                **meta,
                "reason": sanitized_reason,
            },
            ip_address=ip_address,
        )

        _commit(db)
        return AdminCommunityCommentModerationResponse(
            deleted=delete_result.deleted,
            comment_id=delete_result.comment_id,
            post_id=delete_result.post_id,
            comments_count=delete_result.comments_count,
            deletion_mode=delete_result.deletion_mode,
            detail=delete_result.detail,
            audit_log_id=audit_entry.id,
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 5. COMMUNITY ADMIN ACTIVITY SUMMARY
# =============================================================================

def get_admin_community_activity(
    db: Session,
    *,
    recent_limit: int = 10,
) -> AdminCommunityActivityResponse:
    total_posts = int(db.query(func.count(CommunityPost.id)).scalar() or 0)
    published_posts = int(
        db.query(func.count(CommunityPost.id))
        .filter(CommunityPost.is_published == true())
        .scalar()
        or 0
    )
    hidden_posts = total_posts - published_posts
    total_comments = int(db.query(func.count(CommunityComment.id)).scalar() or 0)

    status_counts = {
        st: int(cnt)
        for st, cnt in db.query(
            CommunityReport.report_status, func.count(CommunityReport.id)
        )
        .group_by(CommunityReport.report_status)
        .all()
    }
    pending_reports = status_counts.get("pending", 0)
    investigating_reports = status_counts.get("investigating", 0)
    resolved_reports = status_counts.get("resolved", 0)
    dismissed_reports = status_counts.get("dismissed", 0)
    total_reports = (
        pending_reports + investigating_reports + resolved_reports + dismissed_reports
    )

    recent_open_rows = (
        db.query(CommunityReport)
        .filter(CommunityReport.report_status.in_(tuple(OPEN_REPORT_STATUSES)))
        .order_by(CommunityReport.created_at.desc(), CommunityReport.id.desc())
        .limit(recent_limit)
        .all()
    )
    recent_flagged_reports = _hydrate_admin_reports(db, recent_open_rows)

    audit_rows = (
        db.query(AdminAuditLog)
        .filter(AdminAuditLog.entity_name.in_(COMMUNITY_AUDIT_ENTITY_NAMES))
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(recent_limit)
        .all()
    )
    admin_ids = {a.admin_user_id for a in audit_rows}
    admin_names: dict[int, str] = {}
    if admin_ids:
        admin_names = {
            u.id: u.full_name
            for u in db.query(User.id, User.full_name)
            .filter(User.id.in_(admin_ids))
            .all()
        }

    recent_moderation_actions: list[AdminCommunityAuditLogItem] = []
    for a in audit_rows:
        parsed_details: dict[str, Any] | str | None = a.details
        if isinstance(a.details, str):
            try:
                parsed_details = json.loads(a.details)
            except ValueError:
                parsed_details = a.details
        recent_moderation_actions.append(
            AdminCommunityAuditLogItem(
                id=a.id,
                admin_user_id=a.admin_user_id,
                admin_name=admin_names.get(a.admin_user_id),
                action=a.action,
                entity_name=a.entity_name,
                entity_id=a.entity_id,
                details=parsed_details,
                created_at=a.created_at,
            )
        )

    return AdminCommunityActivityResponse(
        total_posts=total_posts,
        published_posts=published_posts,
        hidden_posts=hidden_posts,
        total_comments=total_comments,
        total_reports=total_reports,
        pending_reports=pending_reports,
        investigating_reports=investigating_reports,
        resolved_reports=resolved_reports,
        dismissed_reports=dismissed_reports,
        recent_flagged_reports=recent_flagged_reports,
        recent_moderation_actions=recent_moderation_actions,
        pending_migration_037_features=list(PENDING_MIGRATION_037_MODERATION_FEATURES),
    )
