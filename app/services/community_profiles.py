"""
Travel Community & Social Feed — Phase 4 Services
(Public Profiles, Follow/Unfollow, Follower/Following Lists & Approved Provider Promotions).

Strictly compatible with existing Migration 028 schema (no Migration 037 columns assumed).
- Public profiles reuse `dbo.users`, `dbo.provider_profiles`, `dbo.community_posts`, and `dbo.community_follows`.
- Provider eligible listings enforce each module's authoritative ownership & publication rules
  via `app/core/publication.py` and Tour Guide approval rules (`approval_status == 'approved'` & `is_active == True`).
- Persistent post-to-listing links (`community_posts.listing_reference_id` / `is_promotional`)
  remain deferred until Migration 037 is formally approved.
"""

from __future__ import annotations

import math
import os
import re

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, true
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.notification_service import create_notification
from app.core.publication import (
    public_activity,
    public_artisan,
    public_eco,
    public_homestay,
    public_rental,
)
from app.core.rate_limiter import SlidingWindowRateLimiter
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.community import (
    CommunityFollow,
    CommunityPost,
    CommunitySavedPost,
)
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import ListingReference
from app.models.tour_guide import TourGuide
from app.models.user import User
from app.schemas.community import (
    CommunityFeedResponse,
    CommunityFollowListResponse,
    CommunityFollowResponse,
    CommunityProfileResponse,
    ProviderCommunityEngagementResponse,
    ProviderEligibleListingItem,
    ProviderEligibleListingsResponse,
    ProviderPostEngagementItem,
)
from app.services.community_feed import _serialize_people_batch
from app.services.community_posts import serialize_posts_batch

# =============================================================================
# RATE LIMITER
# =============================================================================

community_follow_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("COMMUNITY_FOLLOW_RATE_LIMIT", "60")),
    window_seconds=int(os.getenv("COMMUNITY_FOLLOW_RATE_WINDOW_SECONDS", "60")),
    error_message="Too many follow actions. Please try again shortly.",
)


def reset_community_follow_limiter() -> None:
    """Clear in-memory follow rate limiter window (for tests)."""
    community_follow_limiter.reset()


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _get_active_user_or_404(db: Session, user_id: int) -> User:
    user = (
        db.query(User)
        .filter(User.id == user_id, User.is_active == true())
        .first()
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community user profile not found.",
        )
    return user


def _get_approved_provider_profile(db: Session, user: User) -> ProviderProfile | None:
    if user.role != "provider":
        return None
    return (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == user.id,
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )


def _count_active_followers(db: Session, user_id: int) -> int:
    return int(
        db.query(func.count(CommunityFollow.id))
        .join(User, User.id == CommunityFollow.follower_id)
        .filter(
            CommunityFollow.following_id == user_id,
            User.is_active == true(),
        )
        .scalar()
        or 0
    )


def _count_active_following(db: Session, user_id: int) -> int:
    return int(
        db.query(func.count(CommunityFollow.id))
        .join(User, User.id == CommunityFollow.following_id)
        .filter(
            CommunityFollow.follower_id == user_id,
            User.is_active == true(),
        )
        .scalar()
        or 0
    )


# =============================================================================
# 1. PUBLIC COMMUNITY PROFILES
# =============================================================================

def get_community_profile(
    db: Session,
    *,
    user_id: int,
    viewer: User | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityProfileResponse:
    """
    Return public-safe community profile and paginated published posts for an active user.
    Strictly excludes email, phone, NIC, password hash, unapproved business details,
    and unpublished posts.
    """
    target = _get_active_user_or_404(db, user_id)
    approved_profile = _get_approved_provider_profile(db, target)

    posts_query = db.query(CommunityPost).filter(
        CommunityPost.author_id == target.id,
        CommunityPost.is_published == true(),
    )
    posts_total = posts_query.count()
    total_pages = math.ceil(posts_total / page_size) if posts_total > 0 else 0
    offset = (page - 1) * page_size

    post_rows = (
        posts_query.order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    serialized_posts = serialize_posts_batch(db, post_rows, viewer=viewer)

    followers_count = _count_active_followers(db, target.id)
    following_count = _count_active_following(db, target.id)

    is_following = False
    if viewer is not None and viewer.id != target.id:
        is_following = (
            db.query(CommunityFollow.id)
            .filter(
                CommunityFollow.follower_id == viewer.id,
                CommunityFollow.following_id == target.id,
            )
            .first()
            is not None
        )

    return CommunityProfileResponse(
        id=target.id,
        full_name=target.full_name,
        role=target.role,
        is_verified_provider=approved_profile is not None,
        business_name=approved_profile.business_name if approved_profile else None,
        provider_type=approved_profile.provider_type if approved_profile else None,
        district=approved_profile.district if approved_profile else None,
        posts_count=posts_total,
        followers_count=followers_count,
        following_count=following_count,
        is_following=is_following,
        created_at=target.created_at,
        posts=CommunityFeedResponse(
            items=serialized_posts,
            total=posts_total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            has_next=(page < total_pages),
            has_previous=(page > 1 and posts_total > 0),
        ),
    )


# =============================================================================
# 2. FOLLOW & UNFOLLOW (IDEMPOTENT + TRANSACTIONAL NOTIFICATION)
# =============================================================================

def follow_community_user(
    db: Session,
    *,
    target_user_id: int,
    current_user: User,
) -> CommunityFollowResponse:
    community_follow_limiter.check_identifier(f"community_follow:user:{current_user.id}")

    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot follow yourself.",
        )

    target = _get_active_user_or_404(db, target_user_id)

    # Canonical pair lock prevents deadlocks on SQL Server
    lo, hi = sorted((current_user.id, target.id))
    lock_booking_resource(db, f"community_follow_{lo}_{hi}")

    existing = (
        db.query(CommunityFollow)
        .filter(
            CommunityFollow.follower_id == current_user.id,
            CommunityFollow.following_id == target.id,
        )
        .first()
    )
    if existing is not None:
        return CommunityFollowResponse(
            target_user_id=target.id,
            following=True,
            followers_count=_count_active_followers(db, target.id),
            following_count=_count_active_following(db, current_user.id),
        )

    created_new = False
    try:
        nested = db.begin_nested()
        try:
            db.add(
                CommunityFollow(
                    follower_id=current_user.id,
                    following_id=target.id,
                )
            )
            db.flush()
            nested.commit()
            created_new = True
        except IntegrityError:
            nested.rollback()
            created_new = False

        if created_new:
            create_notification(
                db=db,
                user_id=target.id,
                title=f"{current_user.full_name} started following you",
                message=f"{current_user.full_name} is now following your CeylonGo Travel Community profile.",
                notification_type="community_new_follower",
                link_url=f"/community/profiles/{current_user.id}",
            )

        _commit(db)
        return CommunityFollowResponse(
            target_user_id=target.id,
            following=True,
            followers_count=_count_active_followers(db, target.id),
            following_count=_count_active_following(db, current_user.id),
        )
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        raise


def unfollow_community_user(
    db: Session,
    *,
    target_user_id: int,
    current_user: User,
) -> CommunityFollowResponse:
    community_follow_limiter.check_identifier(f"community_follow:user:{current_user.id}")

    if target_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot unfollow yourself.",
        )

    target = _get_active_user_or_404(db, target_user_id)

    lo, hi = sorted((current_user.id, target.id))
    lock_booking_resource(db, f"community_follow_{lo}_{hi}")

    try:
        db.query(CommunityFollow).filter(
            CommunityFollow.follower_id == current_user.id,
            CommunityFollow.following_id == target.id,
        ).delete(synchronize_session=False)
        _commit(db)
        return CommunityFollowResponse(
            target_user_id=target.id,
            following=False,
            followers_count=_count_active_followers(db, target.id),
            following_count=_count_active_following(db, current_user.id),
        )
    except SQLAlchemyError:
        db.rollback()
        raise


# =============================================================================
# 3. FOLLOWERS & FOLLOWING PAGINATED LISTS (N+1 FREE)
# =============================================================================

def list_profile_followers(
    db: Session,
    *,
    user_id: int,
    viewer: User | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityFollowListResponse:
    target = _get_active_user_or_404(db, user_id)

    base_query = (
        db.query(User, ProviderProfile)
        .join(CommunityFollow, CommunityFollow.follower_id == User.id)
        .outerjoin(
            ProviderProfile,
            and_(
                ProviderProfile.user_id == User.id,
                ProviderProfile.approval_status == "approved",
            ),
        )
        .filter(
            CommunityFollow.following_id == target.id,
            User.is_active == true(),
        )
    )

    total = base_query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    rows = (
        base_query.order_by(CommunityFollow.created_at.desc(), CommunityFollow.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = _serialize_people_batch(db, rows, viewer=viewer)

    return CommunityFollowListResponse(
        user_id=target.id,
        list_type="followers",
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


def list_profile_following(
    db: Session,
    *,
    user_id: int,
    viewer: User | None = None,
    page: int = 1,
    page_size: int = 20,
) -> CommunityFollowListResponse:
    target = _get_active_user_or_404(db, user_id)

    base_query = (
        db.query(User, ProviderProfile)
        .join(CommunityFollow, CommunityFollow.following_id == User.id)
        .outerjoin(
            ProviderProfile,
            and_(
                ProviderProfile.user_id == User.id,
                ProviderProfile.approval_status == "approved",
            ),
        )
        .filter(
            CommunityFollow.follower_id == target.id,
            User.is_active == true(),
        )
    )

    total = base_query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    offset = (page - 1) * page_size

    rows = (
        base_query.order_by(CommunityFollow.created_at.desc(), CommunityFollow.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    items = _serialize_people_batch(db, rows, viewer=viewer)

    return CommunityFollowListResponse(
        user_id=target.id,
        list_type="following",
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total > 0),
    )


# =============================================================================
# 4. APPROVED PROVIDER ELIGIBLE LISTINGS & ENGAGEMENT METRICS
# =============================================================================

def list_provider_eligible_listings(
    db: Session,
    *,
    provider_profile: ProviderProfile,
) -> ProviderEligibleListingsResponse:
    """
    Return all publicly eligible, approved listings owned by the authenticated approved provider
    across all 6 CeylonGo provider modules, enforcing each module's own publication rules:
      - homestay:       public_homestay() + provider_profile_id / legacy owner_id ownership
      - tour_guide:     approval_status == 'approved' & is_active == True + provider_profile_id
      - activity:       public_activity() + provider_profile_id
      - eco_experience: public_eco() + provider_profile_id
      - artisan:        public_artisan() (includes is_verified == True) + provider_profile_id
      - rental_vehicle: public_rental() (status == 'available' & active pickup location) + provider_id
    """
    refs = (
        db.query(ListingReference)
        .filter(ListingReference.provider_profile_id == provider_profile.id)
        .all()
    )
    ref_id_by_key: dict[tuple[str, int], int] = {
        (r.module_type, r.target_id): r.id for r in refs
    }

    items: list[ProviderEligibleListingItem] = []

    # 1. Homestays
    homestays = (
        db.query(Homestay)
        .filter(
            public_homestay(),
            or_(
                Homestay.provider_profile_id == provider_profile.id,
                and_(
                    Homestay.provider_profile_id.is_(None),
                    Homestay.owner_id == provider_profile.user_id,
                ),
            ),
        )
        .order_by(Homestay.id.asc())
        .all()
    )
    for h in homestays:
        items.append(
            ProviderEligibleListingItem(
                module_type="homestay",
                target_id=h.id,
                listing_reference_id=ref_id_by_key.get(("homestay", h.id)),
                title=h.name,
                district=h.district,
                city=h.city,
                public_url=f"/homestays/{h.id}",
            )
        )

    # 2. Local Tour Guides
    guides = (
        db.query(TourGuide)
        .filter(
            TourGuide.provider_profile_id == provider_profile.id,
            TourGuide.approval_status == "approved",
            TourGuide.is_active == true(),
        )
        .order_by(TourGuide.id.asc())
        .all()
    )
    for g in guides:
        items.append(
            ProviderEligibleListingItem(
                module_type="tour_guide",
                target_id=g.id,
                listing_reference_id=ref_id_by_key.get(("tour_guide", g.id)),
                title=provider_profile.business_name,
                district=g.base_district,
                city=g.base_city,
                public_url=f"/tour-guides/{g.id}",
            )
        )

    # 3. Activities
    activities = (
        db.query(Activity)
        .filter(
            Activity.provider_profile_id == provider_profile.id,
            public_activity(),
        )
        .order_by(Activity.id.asc())
        .all()
    )
    for a in activities:
        items.append(
            ProviderEligibleListingItem(
                module_type="activity",
                target_id=a.id,
                listing_reference_id=ref_id_by_key.get(("activity", a.id)),
                title=a.title,
                district=a.district,
                city=a.city,
                public_url=f"/activities/{a.id}",
            )
        )

    # 4. Eco Experiences
    eco_rows = (
        db.query(EcoExperience)
        .filter(
            EcoExperience.provider_profile_id == provider_profile.id,
            public_eco(),
        )
        .order_by(EcoExperience.id.asc())
        .all()
    )
    for e in eco_rows:
        items.append(
            ProviderEligibleListingItem(
                module_type="eco_experience",
                target_id=e.id,
                listing_reference_id=ref_id_by_key.get(("eco_experience", e.id)),
                title=e.title,
                district=e.district,
                city=e.city,
                public_url=f"/eco-experiences/{e.id}",
            )
        )

    # 5. Artisan Listings
    artisans = (
        db.query(ArtisanListing)
        .filter(
            ArtisanListing.provider_profile_id == provider_profile.id,
            public_artisan(),
        )
        .order_by(ArtisanListing.id.asc())
        .all()
    )
    for ar in artisans:
        items.append(
            ProviderEligibleListingItem(
                module_type="artisan",
                target_id=ar.id,
                listing_reference_id=ref_id_by_key.get(("artisan", ar.id)),
                title=ar.title,
                district=ar.district,
                city=ar.city,
                public_url=f"/artisans/{ar.id}",
            )
        )

    # 6. Rental Vehicles
    vehicles = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.provider_id == provider_profile.id,
            public_rental(),
        )
        .order_by(RentalVehicle.id.asc())
        .all()
    )
    for v in vehicles:
        items.append(
            ProviderEligibleListingItem(
                module_type="rental_vehicle",
                target_id=v.id,
                listing_reference_id=ref_id_by_key.get(("rental_vehicle", v.id)),
                title=v.vehicle_name,
                district=v.district,
                city=v.city,
                public_url=f"/rentals/vehicles/{v.id}",
            )
        )

    return ProviderEligibleListingsResponse(
        provider_profile_id=provider_profile.id,
        business_name=provider_profile.business_name,
        provider_type=provider_profile.provider_type,
        items=items,
        total=len(items),
        promotional_linking_note=(
            "Approved owned listings are verified and ready for promotional linking on POST/PATCH /api/v1/community/posts "
            "(via listing_reference_id or listing_module_type + listing_target_id)."
        ),
    )


def get_provider_community_engagement(
    db: Session,
    *,
    provider_profile: ProviderProfile,
    page: int = 1,
    page_size: int = 20,
) -> ProviderCommunityEngagementResponse:
    """
    Calculate community engagement metrics strictly for the authenticated approved provider's own posts.
    Aggregates bookmark counts without disclosing individual users who saved posts.
    """
    uid = provider_profile.user_id

    base_posts = db.query(CommunityPost).filter(CommunityPost.author_id == uid)
    total_posts = base_posts.count()
    published_posts = base_posts.filter(CommunityPost.is_published == true()).count()
    unpublished_posts = total_posts - published_posts

    agg_likes, agg_comments = db.query(
        func.coalesce(func.sum(CommunityPost.likes_count), 0),
        func.coalesce(func.sum(CommunityPost.comments_count), 0),
    ).filter(CommunityPost.author_id == uid).one()

    total_saves = int(
        db.query(func.count(CommunitySavedPost.id))
        .join(CommunityPost, CommunityPost.id == CommunitySavedPost.post_id)
        .filter(CommunityPost.author_id == uid)
        .scalar()
        or 0
    )

    followers_count = _count_active_followers(db, uid)

    total_pages = math.ceil(total_posts / page_size) if total_posts > 0 else 0
    offset = (page - 1) * page_size
    page_posts = (
        base_posts.order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    saves_by_post: dict[int, int] = {}
    if page_posts:
        pids = [p.id for p in page_posts]
        saves_by_post = {
            pid: int(cnt)
            for pid, cnt in db.query(
                CommunitySavedPost.post_id, func.count(CommunitySavedPost.id)
            )
            .filter(CommunitySavedPost.post_id.in_(pids))
            .group_by(CommunitySavedPost.post_id)
            .all()
        }

    post_items: list[ProviderPostEngagementItem] = []
    for p in page_posts:
        collapsed = re.sub(r"\s+", " ", p.content or "").strip()
        preview = (collapsed[:117] + "...") if len(collapsed) > 120 else collapsed
        likes = max(0, int(p.likes_count))
        comments = max(0, int(p.comments_count))
        saves = saves_by_post.get(p.id, 0)
        post_items.append(
            ProviderPostEngagementItem(
                post_id=p.id,
                content_preview=preview,
                is_published=p.is_published,
                likes_count=likes,
                comments_count=comments,
                saves_count=saves,
                total_engagements=likes + comments + saves,
                created_at=p.created_at,
            )
        )

    return ProviderCommunityEngagementResponse(
        provider_profile_id=provider_profile.id,
        business_name=provider_profile.business_name,
        provider_type=provider_profile.provider_type,
        total_posts=total_posts,
        published_posts=published_posts,
        unpublished_posts=unpublished_posts,
        total_likes=int(agg_likes),
        total_comments=int(agg_comments),
        total_saves=total_saves,
        followers_count=followers_count,
        posts=post_items,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=(page < total_pages),
        has_previous=(page > 1 and total_posts > 0),
    )
