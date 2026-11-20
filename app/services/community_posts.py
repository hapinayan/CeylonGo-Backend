"""
Travel Community & Social Feed — Phase 1 Services (Post Management, Hashtags & Secure Photo Uploads).

Implemented strictly against Migration 028 (dbo.community_posts, dbo.community_post_media,
dbo.community_post_likes, dbo.community_comments, dbo.community_saved_posts,
dbo.community_reports, dbo.community_hashtags, dbo.community_post_hashtags).

Deletion Semantics (SQL Server FK-Safe & Moderation-Preserving):
----------------------------------------------------------------
1. Moderation-Preserving Soft Unpublish (`soft_unpublished_moderation_preserved`):
   - Triggered when ANY `dbo.community_reports` record exists for the post
     (`entity_type == 'post', entity_id == post_id`) OR for any comment belonging
     to the post (`entity_type == 'comment', entity_id IN post_comment_ids`).
   - Preserves the `community_posts` row, `community_comments`, `community_post_media`,
     and physical media files as immutable moderation evidence while immediately hiding
     the post from public views (`is_published = False`), removing `community_saved_posts`
     bookmarks, and decrementing/unlinking `community_post_hashtags` so it disappears
     from hashtag discovery.
2. Clean FK-Safe Hard Delete (`hard_deleted`):
   - Triggered when NO moderation reports exist for the post or its comments.
   - Explicitly removes dependent rows inside a single DB transaction in SQL Server
     FK-safe order (preventing Error 547 on `ON DELETE NO ACTION` constraints):
       a) Decrement `community_hashtags.usage_count` (clamped >= 0) and delete
          `community_post_hashtags`.
       b) Delete `community_saved_posts` for `post_id` (FK is `ON DELETE NO ACTION`).
       c) Delete `community_post_likes` for `post_id`.
       d) Nullify `community_comments.parent_comment_id` for `post_id`, flush, then
          delete `community_comments` for `post_id` (self-FK is `ON DELETE NO ACTION`).
       e) Delete `community_post_media` and `community_posts`.
       f) Commit transaction; delete physical media files from disk only after commit
          succeeds so a DB failure never leaves surviving rows pointing to missing files.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import re
import uuid

from fastapi import HTTPException, UploadFile, status
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import false, func, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.location_validation import validate_coordinate_pair
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.community import (
    CommunityComment,
    CommunityHashtag,
    CommunityPost,
    CommunityPostHashtag,
    CommunityPostLike,
    CommunityPostMedia,
    CommunityReport,
    CommunitySavedPost,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.community import (
    CommunityAuthorSummary,
    CommunityHashtagResponse,
    CommunityMediaDeleteResponse,
    CommunityPostCreate,
    CommunityPostDeleteResponse,
    CommunityPostLocation,
    CommunityPostMediaResponse,
    CommunityPostResponse,
    CommunityPostShareMetadata,
    CommunityPostUpdate,
)
from app.services.community_promotions import (
    hydrate_eligible_linked_listings_batch,
    require_approved_provider_for_promotion,
    resolve_and_validate_provider_listing_link,
)

# =============================================================================
# CONFIGURATION & CONSTANTS
# =============================================================================

DEFAULT_MAX_MEDIA_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_MAX_PHOTOS_PER_POST = 10
MAX_IMAGE_PIXELS = 25_000_000  # Anti-decompression-bomb pixel cap (~5000x5000)
MAX_HASHTAGS_PER_POST = 20
MAX_HASHTAG_LENGTH = 100

ALLOWED_IMAGE_EXTENSIONS: dict[str, tuple[str, str, str]] = {
    # ext -> (canonical_ext, mime_type, pillow_format)
    ".jpg": (".jpg", "image/jpeg", "JPEG"),
    ".jpeg": (".jpg", "image/jpeg", "JPEG"),
    ".png": (".png", "image/png", "PNG"),
    ".webp": (".webp", "image/webp", "WEBP"),
}
ALLOWED_IMAGE_MIMES: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

COMMUNITY_MEDIA_URL_PREFIX = "/api/v1/uploads/community/"
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_SCRIPT_OR_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CAPTION_HASHTAG_RE = re.compile(r"(?:^|(?<=\s))#([^\s#]+)")
_VALID_HASHTAG_RE = re.compile(r"^[a-z0-9_]{1,100}$")


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def get_community_upload_dir() -> Path:
    configured = os.getenv("COMMUNITY_UPLOAD_DIR")
    if configured:
        base = Path(configured)
    else:
        base = Path(__file__).resolve().parents[2] / "uploads" / "community"
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_max_media_bytes() -> int:
    raw = os.getenv("COMMUNITY_MAX_MEDIA_BYTES")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_MEDIA_BYTES


def _parse_positive_int_env(var_name: str, default: int) -> int:
    raw = os.getenv(var_name)
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return default


def get_default_community_post_create_limiter() -> SlidingWindowRateLimiter:
    """Build user-scoped post creation rate limiter from environment settings."""
    return SlidingWindowRateLimiter(
        max_requests=_parse_positive_int_env("COMMUNITY_POST_CREATE_RATE_LIMIT", 20),
        window_seconds=_parse_positive_int_env(
            "COMMUNITY_POST_CREATE_RATE_WINDOW_SECONDS", 3600
        ),
        error_message="Too many community posts created. Please try again later.",
    )


def get_default_community_media_upload_limiter() -> SlidingWindowRateLimiter:
    """Build user-scoped media upload rate limiter from environment settings."""
    return SlidingWindowRateLimiter(
        max_requests=_parse_positive_int_env("COMMUNITY_MEDIA_UPLOAD_RATE_LIMIT", 40),
        window_seconds=_parse_positive_int_env(
            "COMMUNITY_MEDIA_UPLOAD_RATE_WINDOW_SECONDS", 3600
        ),
        error_message="Too many community photo uploads. Please try again later.",
    )


community_post_create_limiter = get_default_community_post_create_limiter()
community_media_upload_limiter = get_default_community_media_upload_limiter()


def reset_community_post_limiters() -> None:
    """Reset in-memory post creation & media upload rate-limit windows (for tests)."""
    community_post_create_limiter.reset()
    community_media_upload_limiter.reset()


def get_max_photos_per_post() -> int:
    raw = os.getenv("COMMUNITY_MAX_PHOTOS_PER_POST")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_MAX_PHOTOS_PER_POST


# =============================================================================
# SANITIZATION, COORDINATES & HASHTAG UTILITIES
# =============================================================================

def sanitize_community_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    allow_newlines: bool = False,
    required: bool = False,
) -> str | None:
    """Strip unsafe HTML/scripts and control characters from user-authored text."""
    if value is None:
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{field_name} is required.",
            )
        return None

    cleaned = _CONTROL_CHARS_RE.sub("", value)
    cleaned = _SCRIPT_OR_STYLE_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    if allow_newlines:
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
        cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n")).strip()
    else:
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if not cleaned:
        if required:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{field_name} cannot be empty after sanitization.",
            )
        return None

    if len(cleaned) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field_name} cannot exceed {max_length} characters.",
        )
    return cleaned


def validate_community_coordinates(
    latitude: Decimal | None,
    longitude: Decimal | None,
) -> None:
    """Enforce paired WGS-84 coordinates using the shared coordinate validation utility."""
    validate_coordinate_pair(latitude, longitude)
    if latitude is not None and (latitude < Decimal("-90") or latitude > Decimal("90")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Latitude must be between -90 and 90.",
        )
    if longitude is not None and (longitude < Decimal("-180") or longitude > Decimal("180")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Longitude must be between -180 and 180.",
        )


def normalize_and_validate_hashtags(
    content: str,
    explicit_hashtags: list[str] | None = None,
) -> list[str]:
    """
    Extract inline #hashtags from caption and merge any explicit hashtags list.
    Returns deduplicated lowercase tags validated against length & character rules.
    """
    ordered: dict[str, None] = {}

    for match in _CAPTION_HASHTAG_RE.finditer(content):
        raw = match.group(1)
        # Trim trailing sentence punctuation on inline hashtags (e.g. "#ella!" or "#kandy,")
        trimmed = raw.rstrip(".,!?;:)'\"")
        if not trimmed:
            continue
        norm = trimmed.lower()
        if not _VALID_HASHTAG_RE.fullmatch(norm):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Invalid hashtag '#{trimmed}'. Hashtags must be 1-{MAX_HASHTAG_LENGTH} "
                    "characters using letters, numbers, and underscores only."
                ),
            )
        ordered[norm] = None

    if explicit_hashtags is not None:
        for raw in explicit_hashtags:
            cleaned = _CONTROL_CHARS_RE.sub("", raw or "").strip()
            if cleaned.startswith("#"):
                cleaned = cleaned[1:].strip()
            norm = cleaned.lower()
            if not norm or not _VALID_HASHTAG_RE.fullmatch(norm):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f"Invalid hashtag '{raw}'. Hashtags must be 1-{MAX_HASHTAG_LENGTH} "
                        "characters using letters, numbers, and underscores only."
                    ),
                )
            ordered[norm] = None

    if len(ordered) > MAX_HASHTAGS_PER_POST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"A post may have at most {MAX_HASHTAGS_PER_POST} unique hashtags.",
        )

    return list(ordered.keys())


def ensure_no_blocked_hashtags(db: Session, tags: list[str]) -> None:
    """Reject creating or editing a post with any admin-blocked hashtag."""
    if not tags:
        return
    blocked = (
        db.query(CommunityHashtag.tag)
        .filter(
            CommunityHashtag.tag.in_(tags),
            CommunityHashtag.is_blocked == true(),
        )
        .order_by(CommunityHashtag.tag.asc())
        .all()
    )
    if blocked:
        blocked_labels = ", ".join(f"#{row[0]}" for row in blocked)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Blocked hashtag(s) cannot be used in community posts: {blocked_labels}.",
        )


def _get_or_create_hashtag(db: Session, tag: str) -> CommunityHashtag:
    existing = (
        db.query(CommunityHashtag)
        .filter(CommunityHashtag.tag == tag)
        .first()
    )
    if existing is not None:
        existing.usage_count = max(0, int(existing.usage_count)) + 1
        return existing

    nested = db.begin_nested()
    try:
        created = CommunityHashtag(tag=tag, usage_count=1)
        db.add(created)
        db.flush()
        nested.commit()
        return created
    except IntegrityError:
        nested.rollback()
        existing = (
            db.query(CommunityHashtag)
            .filter(CommunityHashtag.tag == tag)
            .first()
        )
        if existing is None:
            raise
        existing.usage_count = max(0, int(existing.usage_count)) + 1
        return existing


def sync_post_hashtags(db: Session, post: CommunityPost, desired_tags: list[str]) -> None:
    """
    Synchronize dbo.community_post_hashtags & dbo.community_hashtags.usage_count
    atomically for a post without creating duplicate links or negative counters.
    """
    existing_links = (
        db.query(CommunityPostHashtag, CommunityHashtag)
        .join(CommunityHashtag, CommunityHashtag.id == CommunityPostHashtag.hashtag_id)
        .filter(CommunityPostHashtag.post_id == post.id)
        .all()
    )
    existing_by_tag: dict[str, tuple[CommunityPostHashtag, CommunityHashtag]] = {
        hashtag.tag: (link, hashtag) for link, hashtag in existing_links
    }

    desired_set = set(desired_tags)

    # Remove hashtags no longer associated with this post
    for tag, (link, hashtag) in existing_by_tag.items():
        if tag not in desired_set:
            db.delete(link)
            hashtag.usage_count = max(0, int(hashtag.usage_count) - 1)

    # Add newly introduced hashtags
    for tag in desired_tags:
        if tag not in existing_by_tag:
            hashtag = _get_or_create_hashtag(db, tag)
            db.add(CommunityPostHashtag(post_id=post.id, hashtag_id=hashtag.id))

    db.flush()


def unlink_all_post_hashtags(db: Session, post_id: int) -> None:
    """Remove all hashtag links for a post and safely decrement usage_count (>= 0)."""
    links = (
        db.query(CommunityPostHashtag, CommunityHashtag)
        .join(CommunityHashtag, CommunityHashtag.id == CommunityPostHashtag.hashtag_id)
        .filter(CommunityPostHashtag.post_id == post_id)
        .all()
    )
    for link, hashtag in links:
        db.delete(link)
        hashtag.usage_count = max(0, int(hashtag.usage_count) - 1)
    db.flush()


# =============================================================================
# SERIALIZATION & AUTHORIZATION HELPERS
# =============================================================================

def _build_author_summary(db: Session, user: User) -> CommunityAuthorSummary:
    provider_profile = None
    if user.role == "provider":
        provider_profile = (
            db.query(ProviderProfile)
            .filter(
                ProviderProfile.user_id == user.id,
                ProviderProfile.approval_status == "approved",
            )
            .first()
        )
    return CommunityAuthorSummary(
        id=user.id,
        full_name=user.full_name,
        role=user.role,
        is_verified_provider=provider_profile is not None,
        business_name=provider_profile.business_name if provider_profile else None,
        provider_type=provider_profile.provider_type if provider_profile else None,
    )


def _build_share_metadata(
    post: CommunityPost,
    author: CommunityAuthorSummary,
    media_items: list[CommunityPostMediaResponse],
) -> CommunityPostShareMetadata:
    """Build canonical public sharing metadata; never expose public share links for unpublished posts."""
    place = post.location_tag or post.district
    if place:
        raw_title = f"{author.full_name} in {place} — CeylonGo Community"
    else:
        raw_title = f"{author.full_name} on CeylonGo Community"
    preview_title = raw_title[:160]

    collapsed = re.sub(r"\s+", " ", post.content or "").strip()
    preview_text = (collapsed[:157] + "...") if len(collapsed) > 160 else collapsed

    if not post.is_published:
        return CommunityPostShareMetadata(
            is_shareable=False,
            share_url=None,
            preview_title=preview_title,
            preview_text=preview_text,
            preview_image_url=None,
        )

    first_image = media_items[0].media_url if media_items else None
    return CommunityPostShareMetadata(
        is_shareable=True,
        share_url=f"/community/posts/{post.id}",
        preview_title=preview_title,
        preview_text=preview_text,
        preview_image_url=first_image,
    )


def serialize_posts_batch(
    db: Session,
    posts: list[CommunityPost],
    *,
    viewer: User | None = None,
) -> list[CommunityPostResponse]:
    """
    Serialize a list of CommunityPost rows using O(1) batched queries (preventing N+1):
      1. Batch users (authors)
      2. Batch approved provider profiles
      3. Batch media ordered by display_order ASC, id ASC
      4. Batch hashtags ordered by tag ASC
      5. Batch viewer likes (if authenticated)
      6. Batch viewer saved bookmarks (if authenticated; strictly private to viewer.id)
    """
    if not posts:
        return []

    post_ids = [p.id for p in posts]
    author_ids = list({p.author_id for p in posts})

    users = db.query(User).filter(User.id.in_(author_ids)).all()
    users_by_id = {u.id: u for u in users}

    provider_user_ids = [u.id for u in users if u.role == "provider"]
    approved_providers_by_uid: dict[int, ProviderProfile] = {}
    if provider_user_ids:
        profiles = (
            db.query(ProviderProfile)
            .filter(
                ProviderProfile.user_id.in_(provider_user_ids),
                ProviderProfile.approval_status == "approved",
            )
            .all()
        )
        approved_providers_by_uid = {p.user_id: p for p in profiles}

    media_by_post: dict[int, list[CommunityPostMediaResponse]] = {pid: [] for pid in post_ids}
    media_rows = (
        db.query(CommunityPostMedia)
        .filter(CommunityPostMedia.post_id.in_(post_ids))
        .order_by(
            CommunityPostMedia.post_id.asc(),
            CommunityPostMedia.display_order.asc(),
            CommunityPostMedia.id.asc(),
        )
        .all()
    )
    for m in media_rows:
        media_by_post[m.post_id].append(CommunityPostMediaResponse.model_validate(m))

    hashtags_by_post: dict[int, list[CommunityHashtagResponse]] = {pid: [] for pid in post_ids}
    hashtag_rows = (
        db.query(CommunityPostHashtag.post_id, CommunityHashtag)
        .join(CommunityHashtag, CommunityHashtag.id == CommunityPostHashtag.hashtag_id)
        .filter(
            CommunityPostHashtag.post_id.in_(post_ids),
            CommunityHashtag.is_blocked == false(),
        )
        .order_by(CommunityHashtag.tag.asc())
        .all()
    )
    for pid, h in hashtag_rows:
        hashtags_by_post[pid].append(CommunityHashtagResponse.model_validate(h))

    liked_post_ids: set[int] = set()
    saved_post_ids: set[int] = set()
    if viewer is not None:
        liked_post_ids = {
            row[0]
            for row in db.query(CommunityPostLike.post_id)
            .filter(
                CommunityPostLike.user_id == viewer.id,
                CommunityPostLike.post_id.in_(post_ids),
            )
            .all()
        }
        saved_post_ids = {
            row[0]
            for row in db.query(CommunitySavedPost.post_id)
            .filter(
                CommunitySavedPost.user_id == viewer.id,
                CommunitySavedPost.post_id.in_(post_ids),
            )
            .all()
        }

    linked_listings_by_ref = hydrate_eligible_linked_listings_batch(db, posts)

    results: list[CommunityPostResponse] = []
    for post in posts:
        author_user = users_by_id.get(post.author_id)
        if author_user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Post author not found.",
            )
        prof = approved_providers_by_uid.get(author_user.id)
        author_summary = CommunityAuthorSummary(
            id=author_user.id,
            full_name=author_user.full_name,
            role=author_user.role,
            is_verified_provider=prof is not None,
            business_name=prof.business_name if prof else None,
            provider_type=prof.provider_type if prof else None,
        )
        post_media = media_by_post.get(post.id, [])
        linked_summary = (
            linked_listings_by_ref.get(post.listing_reference_id)
            if post.listing_reference_id is not None
            else None
        )
        results.append(
            CommunityPostResponse(
                id=post.id,
                author_id=post.author_id,
                author=author_summary,
                content=post.content,
                location_tag=post.location_tag,
                district=post.district,
                latitude=post.latitude,
                longitude=post.longitude,
                location=CommunityPostLocation(
                    location_tag=post.location_tag,
                    district=post.district,
                    latitude=post.latitude,
                    longitude=post.longitude,
                ),
                likes_count=post.likes_count,
                comments_count=post.comments_count,
                is_published=post.is_published,
                is_promotional=bool(post.is_promotional),
                is_featured=bool(post.is_featured),
                listing_reference_id=(
                    linked_summary.listing_reference_id if linked_summary else None
                ),
                linked_listing=linked_summary,
                is_liked=(post.id in liked_post_ids),
                is_saved=(post.id in saved_post_ids),
                media=post_media,
                hashtags=hashtags_by_post.get(post.id, []),
                share_url=f"/community/posts/{post.id}",
                share_metadata=_build_share_metadata(post, author_summary, post_media),
                created_at=post.created_at,
                updated_at=post.updated_at,
            )
        )
    return results


def serialize_post(
    db: Session,
    post: CommunityPost,
    *,
    viewer: User | None = None,
) -> CommunityPostResponse:
    return serialize_posts_batch(db, [post], viewer=viewer)[0]


def _get_post_or_404(db: Session, post_id: int) -> CommunityPost:
    post = db.get(CommunityPost, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )
    return post


def _can_view_post(post: CommunityPost, viewer: User | None) -> bool:
    if post.is_published:
        return True
    if viewer is None:
        return False
    return viewer.id == post.author_id or viewer.role == "admin"


def _require_post_owner_or_admin(post: CommunityPost, current_user: User) -> None:
    if post.author_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this post.",
        )


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# POST CRUD OPERATIONS
# =============================================================================

def create_community_post(
    db: Session,
    *,
    current_user: User,
    payload: CommunityPostCreate,
) -> CommunityPostResponse:
    community_post_create_limiter.check_identifier(
        f"community_post_create:user:{current_user.id}"
    )

    content = sanitize_community_text(
        payload.content,
        field_name="content",
        max_length=5000,
        allow_newlines=True,
        required=True,
    )
    assert content is not None
    location_tag = sanitize_community_text(
        payload.location_tag, field_name="location_tag", max_length=150
    )
    district = sanitize_community_text(
        payload.district, field_name="district", max_length=100
    )
    validate_community_coordinates(payload.latitude, payload.longitude)
    tags = normalize_and_validate_hashtags(content, payload.hashtags)
    ensure_no_blocked_hashtags(db, tags)

    has_listing_link = (
        payload.listing_reference_id is not None
        or payload.listing_module_type is not None
    )
    is_promotional = bool(payload.is_promotional) or has_listing_link

    try:
        resolved_listing_ref_id: int | None = None
        if has_listing_link:
            ref = resolve_and_validate_provider_listing_link(
                db,
                current_user=current_user,
                listing_reference_id=payload.listing_reference_id,
                listing_module_type=payload.listing_module_type,
                listing_target_id=payload.listing_target_id,
            )
            resolved_listing_ref_id = ref.id
        elif is_promotional:
            require_approved_provider_for_promotion(db, current_user)

        post = CommunityPost(
            author_id=current_user.id,
            content=content,
            location_tag=location_tag,
            district=district,
            latitude=payload.latitude,
            longitude=payload.longitude,
            likes_count=0,
            comments_count=0,
            is_published=payload.is_published,
            is_promotional=is_promotional,
            listing_reference_id=resolved_listing_ref_id,
        )
        db.add(post)
        db.flush()

        if tags:
            sync_post_hashtags(db, post, tags)

        _commit(db)
        db.refresh(post)
        return serialize_post(db, post, viewer=current_user)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def get_community_post(
    db: Session,
    *,
    post_id: int,
    viewer: User | None,
) -> CommunityPostResponse:
    post = _get_post_or_404(db, post_id)
    if not _can_view_post(post, viewer):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post not found.",
        )
    return serialize_post(db, post, viewer=viewer)


def update_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
    payload: CommunityPostUpdate,
) -> CommunityPostResponse:
    post = _get_post_or_404(db, post_id)
    fields = payload.model_fields_set

    is_owner = post.author_id == current_user.id
    is_admin = current_user.role == "admin"

    if not is_owner:
        if not is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only edit your own community posts.",
            )
        # Existing admin moderation policy permits admin visibility toggling (`is_published`)
        # without rewriting another user's authored caption/location.
        non_moderation_fields = fields - {"is_published"}
        if non_moderation_fields:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Administrators may moderate post visibility (is_published) but cannot rewrite another user's post content.",
            )

    # Critical moderation guard (HR-01):
    # Authors must NEVER edit or republish a post hidden by an admin or marked removed_by_author.
    if not is_admin and post.moderation_status == "hidden_by_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This post was hidden by an administrator and cannot be edited or republished.",
        )
    if not is_admin and post.moderation_status == "removed_by_author":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This post was removed and preserved for moderation review; it cannot be edited or restored.",
        )

    new_content = post.content
    if "content" in fields:
        sanitized = sanitize_community_text(
            payload.content,
            field_name="content",
            max_length=5000,
            allow_newlines=True,
            required=True,
        )
        assert sanitized is not None
        new_content = sanitized

    new_location_tag = (
        sanitize_community_text(
            payload.location_tag, field_name="location_tag", max_length=150
        )
        if "location_tag" in fields
        else post.location_tag
    )
    new_district = (
        sanitize_community_text(
            payload.district, field_name="district", max_length=100
        )
        if "district" in fields
        else post.district
    )
    new_lat = payload.latitude if "latitude" in fields else post.latitude
    new_lon = payload.longitude if "longitude" in fields else post.longitude
    validate_community_coordinates(new_lat, new_lon)

    should_resync_hashtags = "content" in fields or "hashtags" in fields
    desired_tags: list[str] | None = None
    if should_resync_hashtags:
        explicit = payload.hashtags if "hashtags" in fields else None
        desired_tags = normalize_and_validate_hashtags(new_content, explicit)
        ensure_no_blocked_hashtags(db, desired_tags)

    listing_fields_touched = bool(
        fields & {"listing_reference_id", "listing_module_type", "listing_target_id"}
    )
    promo_touched = "is_promotional" in fields

    try:
        new_listing_ref_id = post.listing_reference_id
        new_is_promotional = bool(post.is_promotional)

        if listing_fields_touched:
            if (
                payload.listing_reference_id is None
                and payload.listing_module_type is None
                and payload.listing_target_id is None
            ):
                # Explicit unlink
                new_listing_ref_id = None
            else:
                ref = resolve_and_validate_provider_listing_link(
                    db,
                    current_user=current_user,
                    listing_reference_id=payload.listing_reference_id,
                    listing_module_type=payload.listing_module_type,
                    listing_target_id=payload.listing_target_id,
                )
                new_listing_ref_id = ref.id
                new_is_promotional = True

        if promo_touched and payload.is_promotional is not None:
            if payload.is_promotional:
                require_approved_provider_for_promotion(db, current_user)
                new_is_promotional = True
            else:
                if new_listing_ref_id is not None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Unlink the attached listing before disabling promotional status.",
                    )
                new_is_promotional = False

        post.content = new_content
        post.location_tag = new_location_tag
        post.district = new_district
        post.latitude = new_lat
        post.longitude = new_lon
        post.listing_reference_id = new_listing_ref_id
        post.is_promotional = new_is_promotional
        if "is_published" in fields and payload.is_published is not None:
            post.is_published = payload.is_published
            if is_admin and not is_owner:
                post.moderation_status = (
                    "active" if payload.is_published else "hidden_by_admin"
                )
        post.updated_at = _now_utc()

        if desired_tags is not None:
            sync_post_hashtags(db, post, desired_tags)

        _commit(db)
        db.refresh(post)
        return serialize_post(db, post, viewer=current_user)
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def _has_moderation_reports(db: Session, post_id: int) -> bool:
    """Return True if any community_reports row references this post or any of its comments."""
    post_report = (
        db.query(CommunityReport.id)
        .filter(
            CommunityReport.entity_type == "post",
            CommunityReport.entity_id == post_id,
        )
        .first()
    )
    if post_report is not None:
        return True

    comment_ids = [
        row[0]
        for row in db.query(CommunityComment.id)
        .filter(CommunityComment.post_id == post_id)
        .all()
    ]
    if not comment_ids:
        return False

    comment_report = (
        db.query(CommunityReport.id)
        .filter(
            CommunityReport.entity_type == "comment",
            CommunityReport.entity_id.in_(comment_ids),
        )
        .first()
    )
    return comment_report is not None


def _extract_community_filename(media_url: str) -> str | None:
    if not media_url.startswith(COMMUNITY_MEDIA_URL_PREFIX):
        return None
    candidate = media_url[len(COMMUNITY_MEDIA_URL_PREFIX) :]
    if not candidate or not _SAFE_FILENAME_RE.fullmatch(candidate):
        return None
    if "/" in candidate or "\\" in candidate or ".." in candidate:
        return None
    return candidate


def _remove_file_quietly(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def delete_community_post(
    db: Session,
    *,
    post_id: int,
    current_user: User,
) -> CommunityPostDeleteResponse:
    """
    Delete a community post safely under SQL Server foreign-key constraints while
    preserving moderation evidence whenever reports reference the post or its comments.
    """
    post = _get_post_or_404(db, post_id)
    _require_post_owner_or_admin(post, current_user)

    if current_user.role != "admin" and post.moderation_status == "hidden_by_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This post was hidden by an administrator and cannot be deleted by the author.",
        )

    if _has_moderation_reports(db, post.id):
        try:
            post.is_published = False
            post.moderation_status = (
                "hidden_by_admin"
                if (current_user.role == "admin" and post.author_id != current_user.id)
                else "removed_by_author"
            )
            post.updated_at = _now_utc()
            # Remove private bookmarks & hashtag links so the hidden post disappears
            # from saved lists and hashtag discovery while preserving the post,
            # comments, and media evidence for admin investigation.
            db.query(CommunitySavedPost).filter(
                CommunitySavedPost.post_id == post.id
            ).delete(synchronize_session=False)
            unlink_all_post_hashtags(db, post.id)
            _commit(db)
        except SQLAlchemyError:
            db.rollback()
            raise
        return CommunityPostDeleteResponse(
            deleted=True,
            post_id=post.id,
            deletion_mode="soft_unpublished_moderation_preserved",
            detail=(
                "Post has been unpublished and hidden from the community feed; "
                "underlying records and media were preserved because moderation reports reference this content."
            ),
        )

    # Clean hard delete in strict SQL Server FK-safe order (handles ON DELETE NO ACTION
    # on community_saved_posts.post_id and community_comments.parent_comment_id).
    media_rows = (
        db.query(CommunityPostMedia)
        .filter(CommunityPostMedia.post_id == post.id)
        .all()
    )
    files_to_delete: list[Path] = []
    upload_dir = get_community_upload_dir()
    for m in media_rows:
        fname = _extract_community_filename(m.media_url)
        if fname:
            try:
                files_to_delete.append(resolve_safe_community_media_path(fname, upload_dir))
            except HTTPException:
                pass

    try:
        unlink_all_post_hashtags(db, post.id)

        db.query(CommunitySavedPost).filter(
            CommunitySavedPost.post_id == post.id
        ).delete(synchronize_session=False)

        db.query(CommunityPostLike).filter(
            CommunityPostLike.post_id == post.id
        ).delete(synchronize_session=False)

        # Break self-referencing parent_comment_id links before deleting comments
        # to guarantee zero SQL Server Error 547 regardless of thread depth.
        db.query(CommunityComment).filter(
            CommunityComment.post_id == post.id,
            CommunityComment.parent_comment_id.is_not(None),
        ).update({CommunityComment.parent_comment_id: None}, synchronize_session=False)
        db.flush()

        db.query(CommunityComment).filter(
            CommunityComment.post_id == post.id
        ).delete(synchronize_session=False)

        db.query(CommunityPostMedia).filter(
            CommunityPostMedia.post_id == post.id
        ).delete(synchronize_session=False)

        db.delete(post)
        _commit(db)
    except SQLAlchemyError:
        db.rollback()
        raise

    for file_path in files_to_delete:
        _remove_file_quietly(file_path)

    return CommunityPostDeleteResponse(
        deleted=True,
        post_id=post_id,
        deletion_mode="hard_deleted",
        detail="Community post and associated media/engagement records permanently deleted.",
    )


# =============================================================================
# SECURE PHOTO VALIDATION, EXIF STRIPPING & STORAGE
# =============================================================================

def resolve_safe_community_media_path(
    filename: str,
    base_dir: Path | None = None,
) -> Path:
    """Strictly validate filename and ensure resolved path stays inside community upload directory."""
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or not _SAFE_FILENAME_RE.fullmatch(filename)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid media filename.",
        )

    root = (base_dir or get_community_upload_dir()).resolve()
    target = (root / filename).resolve()
    if not target.is_relative_to(root) or target == root:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid media path.",
        )
    return target


def _has_expected_magic_bytes(data: bytes, expected_format: str) -> bool:
    if expected_format == "JPEG":
        return data.startswith(b"\xff\xd8\xff")
    if expected_format == "PNG":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if expected_format == "WEBP":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _validate_and_strip_exif_image(
    raw_bytes: bytes,
    *,
    filename: str | None,
    content_type: str | None,
) -> tuple[bytes, str, str]:
    """
    Validate extension, MIME type, magic bytes, image integrity, and pixel bounds,
    then re-encode via Pillow with ALL EXIF / GPS / camera metadata stripped.
    Returns: (sanitized_bytes, canonical_extension, canonical_mime).
    """
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image file is empty.",
        )

    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image format. Allowed formats: JPEG (.jpg, .jpeg), PNG (.png), WebP (.webp).",
        )
    canonical_ext, canonical_mime, expected_pillow_fmt = ALLOWED_IMAGE_EXTENSIONS[ext]

    declared_mime = (content_type or "").split(";")[0].strip().lower()
    if declared_mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image MIME type. Allowed types: image/jpeg, image/png, image/webp.",
        )
    if ALLOWED_IMAGE_MIMES[declared_mime] != expected_pillow_fmt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension does not match declared Content-Type.",
        )

    if not _has_expected_magic_bytes(raw_bytes, expected_pillow_fmt):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Image content does not match the declared image format.",
        )

    # Verify structural validity & decode cleanly with Pillow
    try:
        with Image.open(BytesIO(raw_bytes)) as probe:
            actual_fmt = (probe.format or "").upper()
            if actual_fmt != expected_pillow_fmt:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Decoded image format does not match the declared format.",
                )
            width, height = probe.size
            if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Image dimensions exceed maximum allowed resolution.",
                )
            probe.verify()

        with Image.open(BytesIO(raw_bytes)) as img:
            # Apply EXIF orientation visually before discarding all EXIF tags
            oriented = ImageOps.exif_transpose(img)
            if expected_pillow_fmt == "JPEG":
                clean_mode = "RGB"
                converted = oriented.convert("RGB")
            else:
                clean_mode = "RGBA" if "A" in oriented.getbands() else "RGB"
                converted = oriented.convert(clean_mode)

            # Copy raw pixels into a fresh Image instance with zero metadata/EXIF/text info
            stripped = Image.new(clean_mode, converted.size)
            stripped.paste(converted)

            out = BytesIO()
            if expected_pillow_fmt == "JPEG":
                stripped.save(out, format="JPEG", quality=88, optimize=True)
            elif expected_pillow_fmt == "PNG":
                stripped.save(out, format="PNG", optimize=True)
            else:
                stripped.save(out, format="WEBP", quality=88)
            sanitized_bytes = out.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid or supported image.",
        ) from exc

    return sanitized_bytes, canonical_ext, canonical_mime


async def upload_community_post_photo(
    db: Session,
    *,
    post_id: int,
    current_user: User,
    file: UploadFile,
    display_order: int | None = None,
) -> CommunityPostMediaResponse:
    """Validate, EXIF-strip, persist to disk, and record a photo for a community post."""
    community_media_upload_limiter.check_identifier(
        f"community_media_upload:user:{current_user.id}"
    )

    post = _get_post_or_404(db, post_id)
    _require_post_owner_or_admin(post, current_user)

    if current_user.role != "admin" and post.moderation_status in (
        "hidden_by_admin",
        "removed_by_author",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media cannot be uploaded to a moderated or removed post.",
        )

    if display_order is not None and display_order < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="display_order must be greater than or equal to 0.",
        )

    max_photos = get_max_photos_per_post()
    current_count = (
        db.query(func.count(CommunityPostMedia.id))
        .filter(CommunityPostMedia.post_id == post.id)
        .scalar()
        or 0
    )
    if current_count >= max_photos:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum of {max_photos} photos allowed per community post.",
        )

    max_bytes = get_max_media_bytes()
    raw_bytes = await file.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Image exceeds maximum allowed size of {max_bytes} bytes.",
        )

    sanitized_bytes, canonical_ext, _ = _validate_and_strip_exif_image(
        raw_bytes,
        filename=file.filename,
        content_type=file.content_type,
    )

    generated_name = f"post_{post.id}_{uuid.uuid4().hex}{canonical_ext}"
    upload_dir = get_community_upload_dir()
    dest_path = resolve_safe_community_media_path(generated_name, upload_dir)

    if display_order is None:
        max_order = (
            db.query(func.max(CommunityPostMedia.display_order))
            .filter(CommunityPostMedia.post_id == post.id)
            .scalar()
        )
        next_order = 0 if max_order is None else int(max_order) + 1
    else:
        next_order = display_order

    try:
        dest_path.write_bytes(sanitized_bytes)
    except OSError as exc:
        _remove_file_quietly(dest_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store uploaded image.",
        ) from exc

    try:
        media = CommunityPostMedia(
            post_id=post.id,
            media_type="image",
            media_url=f"{COMMUNITY_MEDIA_URL_PREFIX}{generated_name}",
            display_order=next_order,
        )
        db.add(media)
        post.updated_at = _now_utc()
        _commit(db)
        db.refresh(media)
        return CommunityPostMediaResponse.model_validate(media)
    except SQLAlchemyError:
        db.rollback()
        _remove_file_quietly(dest_path)
        raise


def delete_community_post_media(
    db: Session,
    *,
    post_id: int,
    media_id: int,
    current_user: User,
) -> CommunityMediaDeleteResponse:
    """Delete a media item belonging to a post (owner or admin only)."""
    post = _get_post_or_404(db, post_id)
    _require_post_owner_or_admin(post, current_user)

    if current_user.role != "admin" and post.moderation_status in (
        "hidden_by_admin",
        "removed_by_author",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media cannot be modified on a moderated or removed post.",
        )

    media = (
        db.query(CommunityPostMedia)
        .filter(
            CommunityPostMedia.id == media_id,
            CommunityPostMedia.post_id == post.id,
        )
        .first()
    )
    if media is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community post media item not found.",
        )

    # Preserve physical evidence file if moderation reports exist on this post
    preserve_file = _has_moderation_reports(db, post.id)
    if preserve_file and current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Media cannot be deleted while moderation reports reference this post.",
        )

    fname = _extract_community_filename(media.media_url)
    file_path = None
    if fname and not preserve_file:
        try:
            file_path = resolve_safe_community_media_path(fname)
        except HTTPException:
            file_path = None

    try:
        db.delete(media)
        post.updated_at = _now_utc()
        _commit(db)
    except SQLAlchemyError:
        db.rollback()
        raise

    if file_path is not None:
        _remove_file_quietly(file_path)

    return CommunityMediaDeleteResponse(
        deleted=True,
        post_id=post.id,
        media_id=media_id,
        detail="Community post media deleted successfully.",
    )


def get_authorized_community_media_file(
    db: Session,
    *,
    filename: str,
    viewer: User | None,
) -> tuple[Path, str]:
    """
    Resolve a stored community photo only if:
    1. Filename passes strict traversal checks and resides inside uploads/community.
    2. A registered CommunityPostMedia row exists for this exact URL (blocks arbitrary file reads).
    3. The parent post is published, OR viewer is the post author or an admin.
    """
    file_path = resolve_safe_community_media_path(filename)
    media_url = f"{COMMUNITY_MEDIA_URL_PREFIX}{filename}"

    row = (
        db.query(CommunityPostMedia, CommunityPost)
        .join(CommunityPost, CommunityPost.id == CommunityPostMedia.post_id)
        .filter(CommunityPostMedia.media_url == media_url)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community image not found.",
        )
    _, post = row
    if not _can_view_post(post, viewer):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community image not found.",
        )

    if not file_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community image file not found on storage server.",
        )

    ext = file_path.suffix.lower()
    mime_type = (
        ALLOWED_IMAGE_EXTENSIONS[ext][1]
        if ext in ALLOWED_IMAGE_EXTENSIONS
        else "application/octet-stream"
    )
    return file_path, mime_type
