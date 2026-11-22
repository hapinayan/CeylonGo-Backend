"""
Travel Community & Social Feed — Phase 2 Services (Public Feed, Search & Trending Hashtags).

Strictly compatible with existing Migration 028 schema (no Migration 037 columns assumed).
Unsupported Migration-037-dependent filters (`featured_only`, `promotional_only`) return
explicit HTTP 501 Not Implemented rather than silently returning inaccurate results.
"""

from __future__ import annotations

import math
import re

from fastapi import HTTPException, status
from sqlalchemy import and_, false, func, or_, select, true
from sqlalchemy.orm import Session

from app.models.community import (
    CommunityFollow,
    CommunityHashtag,
    CommunityPost,
    CommunityPostHashtag,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.community import (
    CommunityFeedResponse,
    CommunityHashtagResponse,
    CommunityPersonSearchResult,
    CommunitySearchResponse,
    CommunityTrendingHashtagsResponse,
)
from app.services.community_posts import (
    public_community_post_filter,
    MAX_HASHTAG_LENGTH,
    sanitize_community_text,
    serialize_posts_batch,
)

_VALID_HASHTAG_FILTER_RE = re.compile(r"^[a-z0-9_]{1,100}$")
_VALID_SEARCH_TYPES = frozenset({"all", "posts", "people", "hashtags"})


def _published_hashtag_exists():
    """Subquery predicate ensuring hashtags only surface publicly if linked to >=1 published post by an active author."""
    return (
        select(CommunityPostHashtag.id)
        .join(CommunityPost, CommunityPost.id == CommunityPostHashtag.post_id)
        .join(User, User.id == CommunityPost.author_id)
        .where(
            CommunityPostHashtag.hashtag_id == CommunityHashtag.id,
            public_community_post_filter(),
            User.is_active == true(),
        )
        .exists()
    )


def _escape_like_term(raw: str) -> str:
    """Escape SQL LIKE wildcards (\\, %, _) cleanly for both SQL Server and SQLite."""
    escaped = raw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _normalize_hashtag_filter(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("#"):
        cleaned = cleaned[1:].strip()
    norm = cleaned.lower()
    if not norm or not _VALID_HASHTAG_FILTER_RE.fullmatch(norm):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Invalid hashtag filter '{raw}'. Hashtags must be 1-{MAX_HASHTAG_LENGTH} "
                "characters using letters, numbers, and underscores only."
            ),
        )
    return norm


# =============================================================================
# 1. PUBLIC COMMUNITY FEED
# =============================================================================

def get_community_feed(
    db: Session,
    *,
    viewer: User | None = None,
    page: int = 1,
    page_size: int = 20,
    district: str | None = None,
    author_id: int | None = None,
    hashtag: str | None = None,
    following_only: bool = False,
    featured_only: bool = False,
    promotional_only: bool = False,
) -> CommunityFeedResponse:
    """
    Return paginated published community posts ordered newest-first with deterministic
    tie-breaking (`created_at DESC, id DESC`) and O(1) batched N+1-free enrichment.
    """
    if following_only and viewer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required to filter the community feed by followed accounts.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    query = (
        db.query(CommunityPost)
        .join(User, User.id == CommunityPost.author_id)
        .filter(
            public_community_post_filter(),
            CommunityPost.moderation_status == "active",
            User.is_active == true(),
        )
    )

    if district is not None:
        clean_district = sanitize_community_text(
            district, field_name="district", max_length=100, required=True
        )
        assert clean_district is not None
        query = query.filter(func.lower(CommunityPost.district) == clean_district.lower())

    if author_id is not None:
        query = query.filter(CommunityPost.author_id == author_id)

    if hashtag is not None:
        norm_tag = _normalize_hashtag_filter(hashtag)
        hashtag_post_ids = (
            select(CommunityPostHashtag.post_id)
            .join(CommunityHashtag, CommunityHashtag.id == CommunityPostHashtag.hashtag_id)
            .where(
                CommunityHashtag.tag == norm_tag,
                CommunityHashtag.is_blocked == false(),
            )
        )
        query = query.filter(CommunityPost.id.in_(hashtag_post_ids))

    if following_only and viewer is not None:
        followed_ids = select(CommunityFollow.following_id).where(
            CommunityFollow.follower_id == viewer.id
        )
        query = query.filter(CommunityPost.author_id.in_(followed_ids))

    if featured_only:
        query = query.filter(CommunityPost.is_featured == true())

    if promotional_only:
        approved_provider_uids = (
            select(ProviderProfile.user_id)
            .where(ProviderProfile.approval_status == "approved")
        )
        query = query.filter(
            CommunityPost.is_promotional == true(),
            CommunityPost.author_id.in_(approved_provider_uids),
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    posts = (
        query.order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = serialize_posts_batch(db, posts, viewer=viewer)

    return CommunityFeedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


# =============================================================================
# 2. UNIFIED COMMUNITY SEARCH (POSTS, PEOPLE, HASHTAGS)
# =============================================================================

def _serialize_people_batch(
    db: Session,
    rows: list[tuple[User, ProviderProfile | None]],
    *,
    viewer: User | None = None,
) -> list[CommunityPersonSearchResult]:
    if not rows:
        return []

    user_ids = [u.id for u, _ in rows]

    published_posts_counts = {
        uid: int(cnt)
        for uid, cnt in db.query(CommunityPost.author_id, func.count(CommunityPost.id))
        .filter(
            CommunityPost.author_id.in_(user_ids),
            public_community_post_filter(),
        )
        .group_by(CommunityPost.author_id)
        .all()
    }

    followers_counts = {
        uid: int(cnt)
        for uid, cnt in db.query(CommunityFollow.following_id, func.count(CommunityFollow.id))
        .filter(CommunityFollow.following_id.in_(user_ids))
        .group_by(CommunityFollow.following_id)
        .all()
    }

    following_ids: set[int] = set()
    if viewer is not None:
        following_ids = {
            row[0]
            for row in db.query(CommunityFollow.following_id)
            .filter(
                CommunityFollow.follower_id == viewer.id,
                CommunityFollow.following_id.in_(user_ids),
            )
            .all()
        }

    results: list[CommunityPersonSearchResult] = []
    for user, profile in rows:
        results.append(
            CommunityPersonSearchResult(
                id=user.id,
                full_name=user.full_name,
                role=user.role,
                is_verified_provider=profile is not None,
                business_name=profile.business_name if profile else None,
                provider_type=profile.provider_type if profile else None,
                district=profile.district if profile else None,
                posts_count=published_posts_counts.get(user.id, 0),
                followers_count=followers_counts.get(user.id, 0),
                is_following=(user.id in following_ids),
            )
        )
    return results


def search_community(
    db: Session,
    *,
    q: str,
    search_type: str = "all",
    district: str | None = None,
    page: int = 1,
    page_size: int = 20,
    viewer: User | None = None,
) -> CommunitySearchResponse:
    """
    Search published community posts, active public users/providers, and active hashtags.
    Strictly excludes unpublished posts, inactive accounts, and private user fields
    (email, phone, NIC, password_hash) from both search matching and responses.
    """
    if search_type not in _VALID_SEARCH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid search type. Allowed values: all, posts, people, hashtags.",
        )

    clean_q = sanitize_community_text(q, field_name="q", max_length=100, required=True)
    assert clean_q is not None
    clean_district: str | None = None
    if district is not None:
        clean_district = sanitize_community_text(
            district, field_name="district", max_length=100, required=True
        )

    like_pattern = _escape_like_term(clean_q.lower())
    offset = (page - 1) * page_size

    posts_items = []
    posts_total = 0
    people_items: list[CommunityPersonSearchResult] = []
    people_total = 0
    hashtags_items: list[CommunityHashtagResponse] = []
    hashtags_total = 0

    # 1. Posts search (published posts from active users only)
    if search_type in ("all", "posts"):
        posts_query = (
            db.query(CommunityPost)
            .join(User, User.id == CommunityPost.author_id)
            .filter(
                public_community_post_filter(),
                User.is_active == true(),
                or_(
                    func.lower(CommunityPost.content).like(like_pattern, escape="\\"),
                    func.lower(func.coalesce(CommunityPost.location_tag, "")).like(
                        like_pattern, escape="\\"
                    ),
                    func.lower(func.coalesce(CommunityPost.district, "")).like(
                        like_pattern, escape="\\"
                    ),
                ),
            )
        )
        if clean_district is not None:
            posts_query = posts_query.filter(
                func.lower(CommunityPost.district) == clean_district.lower()
            )
        posts_total = posts_query.count()
        post_rows = (
            posts_query.order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        posts_items = serialize_posts_batch(db, post_rows, viewer=viewer)

    # 2. People search (active public travelers & providers; NEVER searches/leaks email or phone)
    if search_type in ("all", "people"):
        people_query = (
            db.query(User, ProviderProfile)
            .outerjoin(
                ProviderProfile,
                and_(
                    ProviderProfile.user_id == User.id,
                    ProviderProfile.approval_status == "approved",
                ),
            )
            .filter(
                User.is_active == true(),
                User.role.in_(("traveler", "provider")),
                or_(
                    func.lower(User.full_name).like(like_pattern, escape="\\"),
                    func.lower(func.coalesce(ProviderProfile.business_name, "")).like(
                        like_pattern, escape="\\"
                    ),
                ),
            )
        )
        if clean_district is not None:
            people_query = people_query.filter(
                func.lower(ProviderProfile.district) == clean_district.lower()
            )
        people_total = people_query.count()
        people_rows = (
            people_query.order_by(User.full_name.asc(), User.id.asc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        people_items = _serialize_people_batch(db, people_rows, viewer=viewer)

    # 3. Hashtags search (active tags with usage_count > 0 linked to >=1 published active-author post)
    if search_type in ("all", "hashtags"):
        tag_query_term = clean_q.lstrip("#").strip().lower()
        if tag_query_term:
            tag_pattern = _escape_like_term(tag_query_term)
            ht_query = db.query(CommunityHashtag).filter(
                CommunityHashtag.usage_count > 0,
                CommunityHashtag.is_blocked == false(),
                _published_hashtag_exists(),
                func.lower(CommunityHashtag.tag).like(tag_pattern, escape="\\"),
            )
            hashtags_total = ht_query.count()
            ht_rows = (
                ht_query.order_by(
                    CommunityHashtag.is_featured.desc(),
                    CommunityHashtag.usage_count.desc(),
                    CommunityHashtag.tag.asc(),
                    CommunityHashtag.id.asc(),
                )
                .offset(offset)
                .limit(page_size)
                .all()
            )
            hashtags_items = [CommunityHashtagResponse.model_validate(h) for h in ht_rows]

    return CommunitySearchResponse(
        query=clean_q,
        search_type=search_type,  # type: ignore[arg-type]
        page=page,
        page_size=page_size,
        posts=posts_items,
        posts_total=posts_total,
        people=people_items,
        people_total=people_total,
        hashtags=hashtags_items,
        hashtags_total=hashtags_total,
    )


# =============================================================================
# 3. TRENDING HASHTAGS
# =============================================================================

def get_trending_hashtags(
    db: Session,
    *,
    limit: int = 10,
) -> CommunityTrendingHashtagsResponse:
    """Return active non-blocked published hashtags ordered by featured & usage_count DESC with deterministic tie-breaking."""
    base_query = db.query(CommunityHashtag).filter(
        CommunityHashtag.usage_count > 0,
        CommunityHashtag.is_blocked == false(),
        _published_hashtag_exists(),
    )
    total = base_query.count()
    rows = (
        base_query.order_by(
            CommunityHashtag.is_featured.desc(),
            CommunityHashtag.usage_count.desc(),
            CommunityHashtag.tag.asc(),
            CommunityHashtag.id.asc(),
        )
        .limit(limit)
        .all()
    )
    return CommunityTrendingHashtagsResponse(
        items=[CommunityHashtagResponse.model_validate(h) for h in rows],
        limit=limit,
        total=total,
    )
