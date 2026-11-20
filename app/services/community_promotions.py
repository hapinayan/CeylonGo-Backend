"""
Travel Community & Social Feed — Step 5B Provider Promotional & Secure Listing Link Services
(TC-PR-01, TC-PR-02, TC-SEC-02).

Security & Cross-Module Invariants:
- Only active, approved service providers (User.role == 'provider', User.is_active == True,
  ProviderProfile.approval_status == 'approved') may create promotional posts or attach listing links.
- Every listing link is validated against its authoritative module table for BOTH ownership AND
  live public publication/approval rules across all 6 CeylonGo provider modules:
    1. homestay       -> public_homestay() + provider_profile_id / legacy owner_id
    2. tour_guide     -> approval_status == 'approved', is_active == True, approved_provider()
    3. activity       -> public_activity() + provider_profile_id
    4. eco_experience -> public_eco() + provider_profile_id
    5. artisan        -> public_artisan() (includes is_verified == True) + provider_profile_id
    6. rental_vehicle -> public_rental() (status == 'available' & active pickup location) + provider_id
- Provider listing tables and approval statuses are NEVER modified by community services.
- Missing dbo.listing_references rows are created idempotently under SQL Server sp_getapplock
  and a nested savepoint catching IntegrityError to prevent duplicate rows and race conditions.
- Public serialization rechecks live eligibility in O(1) batched queries (N+1 free) and suppresses
  revoked/unpublished/unapproved listing links while preserving historical community posts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.publication import (
    approved_provider,
    public_activity,
    public_artisan,
    public_eco,
    public_homestay,
    public_rental,
)
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.community import CommunityPost
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import ListingReference
from app.models.tour_guide import TourGuide
from app.models.user import User
from app.schemas.community import CommunityLinkedListingSummary, ListingModuleType

SUPPORTED_LISTING_MODULES: frozenset[str] = frozenset(
    {
        "homestay",
        "tour_guide",
        "activity",
        "eco_experience",
        "artisan",
        "rental_vehicle",
    }
)


def build_listing_public_url(module_type: str, target_id: int) -> str:
    if module_type == "homestay":
        return f"/homestays/{target_id}"
    if module_type == "tour_guide":
        return f"/tour-guides/{target_id}"
    if module_type == "activity":
        return f"/activities/{target_id}"
    if module_type == "eco_experience":
        return f"/eco-experiences/{target_id}"
    if module_type == "artisan":
        return f"/artisans/{target_id}"
    if module_type == "rental_vehicle":
        return f"/rentals/vehicles/{target_id}"
    return f"/listings/{module_type}/{target_id}"


def require_approved_provider_for_promotion(
    db: Session,
    current_user: User,
) -> ProviderProfile:
    """
    Verify the authenticated user is an active, approved service provider.
    Travelers, admins, and pending/rejected providers are rejected with 403 Forbidden.
    """
    if current_user.role != "provider" or not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only approved service providers can publish promotional posts or link CeylonGo listings.",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your service provider account must be approved before creating promotional posts or linking listings.",
        )
    return profile


def _validate_authoritative_listing_for_provider(
    db: Session,
    *,
    module_type: str,
    target_id: int,
    provider_profile: ProviderProfile,
) -> dict[str, Any]:
    """
    Validate existence, strict ownership, and live public eligibility against the listing's
    authoritative module table without mutating any provider listing table.
    Returns normalized metadata suitable for ListingReference lookup/creation.
    """
    if module_type not in SUPPORTED_LISTING_MODULES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported listing module type '{module_type}'.",
        )

    pid = provider_profile.id
    uid = provider_profile.user_id

    if module_type == "homestay":
        raw = db.get(Homestay, target_id)
        if raw is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Homestay listing not found.")
        owned = (raw.provider_profile_id == pid) or (
            raw.provider_profile_id is None and raw.owner_id == uid
        )
        if not owned:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        eligible = (
            db.query(Homestay.id)
            .filter(Homestay.id == target_id, public_homestay())
            .first()
        )
        if eligible is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved and active homestay listings can be linked to promotional posts.",
            )
        return {
            "title": (raw.name or f"Homestay #{raw.id}")[:255],
            "district": (raw.district or raw.location or provider_profile.district or "General")[:100],
            "city": (raw.city or raw.location)[:100] if (raw.city or raw.location) else None,
            "latitude": raw.latitude,
            "longitude": raw.longitude,
        }

    if module_type == "tour_guide":
        raw = db.get(TourGuide, target_id)
        if raw is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tour guide listing not found.")
        if raw.provider_profile_id != pid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        eligible = (
            db.query(TourGuide.id)
            .filter(
                TourGuide.id == target_id,
                TourGuide.approval_status == "approved",
                TourGuide.is_active == true(),
                approved_provider(TourGuide.provider_profile_id),
            )
            .first()
        )
        if eligible is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved and active tour guide profiles can be linked to promotional posts.",
            )
        return {
            "title": (provider_profile.business_name or f"Tour Guide #{raw.id}")[:255],
            "district": (raw.base_district or provider_profile.district or "General")[:100],
            "city": raw.base_city[:100] if raw.base_city else None,
            "latitude": raw.base_latitude,
            "longitude": raw.base_longitude,
        }

    if module_type == "activity":
        raw = db.get(Activity, target_id)
        if raw is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity listing not found.")
        if raw.provider_profile_id != pid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        eligible = (
            db.query(Activity.id)
            .filter(Activity.id == target_id, public_activity())
            .first()
        )
        if eligible is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved and active activity listings can be linked to promotional posts.",
            )
        return {
            "title": (raw.title or f"Activity #{raw.id}")[:255],
            "district": (raw.district or provider_profile.district or "General")[:100],
            "city": raw.city[:100] if raw.city else None,
            "latitude": getattr(raw, "latitude", None),
            "longitude": getattr(raw, "longitude", None),
        }

    if module_type == "eco_experience":
        raw = db.get(EcoExperience, target_id)
        if raw is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Eco experience listing not found.")
        if raw.provider_profile_id != pid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        eligible = (
            db.query(EcoExperience.id)
            .filter(EcoExperience.id == target_id, public_eco())
            .first()
        )
        if eligible is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved and published eco experience listings can be linked to promotional posts.",
            )
        return {
            "title": (raw.title or f"Eco Experience #{raw.id}")[:255],
            "district": (raw.district or provider_profile.district or "General")[:100],
            "city": raw.city[:100] if raw.city else None,
            "latitude": getattr(raw, "latitude", None),
            "longitude": getattr(raw, "longitude", None),
        }

    if module_type == "artisan":
        raw = db.get(ArtisanListing, target_id)
        if raw is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artisan listing not found.")
        if raw.provider_profile_id != pid:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        eligible = (
            db.query(ArtisanListing.id)
            .filter(ArtisanListing.id == target_id, public_artisan())
            .first()
        )
        if eligible is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only approved, active, and verified artisan listings can be linked to promotional posts.",
            )
        return {
            "title": (raw.title or f"Artisan Listing #{raw.id}")[:255],
            "district": (raw.district or provider_profile.district or "General")[:100],
            "city": raw.city[:100] if raw.city else None,
            "latitude": getattr(raw, "latitude", None),
            "longitude": getattr(raw, "longitude", None),
        }

    # rental_vehicle
    raw = db.get(RentalVehicle, target_id)
    if raw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rental vehicle listing not found.")
    if raw.provider_id != pid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only link listings that belong to your own approved provider account.",
        )
    eligible = (
        db.query(RentalVehicle.id)
        .filter(RentalVehicle.id == target_id, public_rental())
        .first()
    )
    if eligible is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only approved and available rental vehicle listings can be linked to promotional posts.",
        )
    return {
        "title": (raw.vehicle_name or f"Rental Vehicle #{raw.id}")[:255],
        "district": (raw.district or provider_profile.district or "General")[:100],
        "city": raw.city[:100] if raw.city else None,
        "latitude": getattr(raw, "latitude", None),
        "longitude": getattr(raw, "longitude", None),
    }


def _ensure_listing_reference_idempotent(
    db: Session,
    *,
    module_type: str,
    target_id: int,
    provider_profile_id: int,
    meta: dict[str, Any],
) -> ListingReference:
    """
    Lookup or idempotently create a centralized dbo.listing_references row for an already
    validated approved listing using SQL Server sp_getapplock and a nested savepoint
    catching IntegrityError (prevents duplicate UQ_listing_references violations under concurrency).
    """
    lock_booking_resource(db, f"listing_ref_{module_type}_{target_id}")

    existing = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == module_type,
            ListingReference.target_id == target_id,
        )
        .first()
    )
    if existing is not None:
        return existing

    lat: Decimal | None = meta.get("latitude")
    lon: Decimal | None = meta.get("longitude")
    nested = db.begin_nested()
    try:
        ref = ListingReference(
            module_type=module_type,
            target_id=target_id,
            provider_profile_id=provider_profile_id,
            title=meta["title"],
            district=meta["district"],
            city=meta.get("city"),
            latitude=lat,
            longitude=lon,
            is_active=True,
        )
        db.add(ref)
        db.flush()
        nested.commit()
        return ref
    except IntegrityError:
        nested.rollback()
        existing = (
            db.query(ListingReference)
            .filter(
                ListingReference.module_type == module_type,
                ListingReference.target_id == target_id,
            )
            .first()
        )
        if existing is None:
            raise
        return existing


def resolve_and_validate_provider_listing_link(
    db: Session,
    *,
    current_user: User,
    listing_reference_id: int | None = None,
    listing_module_type: str | None = None,
    listing_target_id: int | None = None,
) -> ListingReference:
    """
    Validate provider authorization, listing existence, strict ownership, and live module
    approval/publication rules, then return (or idempotently create) its shared ListingReference.
    """
    provider_profile = require_approved_provider_for_promotion(db, current_user)

    if listing_reference_id is not None:
        ref = db.get(ListingReference, listing_reference_id)
        if ref is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Referenced listing not found.",
            )
        if ref.provider_profile_id != provider_profile.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only link listings that belong to your own approved provider account.",
            )
        _validate_authoritative_listing_for_provider(
            db,
            module_type=ref.module_type,
            target_id=ref.target_id,
            provider_profile=provider_profile,
        )
        return ref

    if listing_module_type is None or listing_target_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Both listing_module_type and listing_target_id are required when linking by module and ID.",
        )

    meta = _validate_authoritative_listing_for_provider(
        db,
        module_type=listing_module_type,
        target_id=listing_target_id,
        provider_profile=provider_profile,
    )
    return _ensure_listing_reference_idempotent(
        db,
        module_type=listing_module_type,
        target_id=listing_target_id,
        provider_profile_id=provider_profile.id,
        meta=meta,
    )


# =============================================================================
# BATCHED LIVE-ELIGIBILITY HYDRATION FOR PUBLIC FEED / SEARCH / POST DETAIL
# =============================================================================

def hydrate_eligible_linked_listings_batch(
    db: Session,
    posts: list[CommunityPost],
) -> dict[int, CommunityLinkedListingSummary]:
    """
    Resolve linked listings for a batch of posts in O(1) queries (N+1 free),
    rechecking each module's live public publication & approval rules.
    Revoked, rejected, unpublished, unverified, or unavailable listings are suppressed.
    """
    ref_ids = list({p.listing_reference_id for p in posts if p.listing_reference_id is not None})
    if not ref_ids:
        return {}

    refs = (
        db.query(ListingReference)
        .filter(ListingReference.id.in_(ref_ids))
        .all()
    )
    if not refs:
        return {}

    by_module: dict[str, list[ListingReference]] = {}
    for ref in refs:
        by_module.setdefault(ref.module_type, []).append(ref)

    eligible_summaries: dict[int, CommunityLinkedListingSummary] = {}

    # 1. Homestays
    if "homestay" in by_module:
        m_refs = by_module["homestay"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(Homestay)
            .filter(Homestay.id.in_(tids), public_homestay())
            .all()
        )
        by_tid = {h.id: h for h in rows}
        for r in m_refs:
            h = by_tid.get(r.target_id)
            if h is None:
                continue
            if h.provider_profile_id is not None and h.provider_profile_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="homestay",
                target_id=h.id,
                title=h.name or r.title,
                district=h.district or r.district,
                city=h.city or r.city,
                public_url=build_listing_public_url("homestay", h.id),
            )

    # 2. Tour Guides
    if "tour_guide" in by_module:
        m_refs = by_module["tour_guide"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(TourGuide, ProviderProfile)
            .join(ProviderProfile, ProviderProfile.id == TourGuide.provider_profile_id)
            .filter(
                TourGuide.id.in_(tids),
                TourGuide.approval_status == "approved",
                TourGuide.is_active == true(),
                approved_provider(TourGuide.provider_profile_id),
            )
            .all()
        )
        by_tid = {g.id: (g, prof) for g, prof in rows}
        for r in m_refs:
            pair = by_tid.get(r.target_id)
            if pair is None:
                continue
            g, prof = pair
            if g.provider_profile_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="tour_guide",
                target_id=g.id,
                title=prof.business_name or r.title,
                district=g.base_district or r.district,
                city=g.base_city or r.city,
                public_url=build_listing_public_url("tour_guide", g.id),
            )

    # 3. Activities
    if "activity" in by_module:
        m_refs = by_module["activity"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(Activity)
            .filter(Activity.id.in_(tids), public_activity())
            .all()
        )
        by_tid = {a.id: a for a in rows}
        for r in m_refs:
            a = by_tid.get(r.target_id)
            if a is None or a.provider_profile_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="activity",
                target_id=a.id,
                title=a.title or r.title,
                district=a.district or r.district,
                city=a.city or r.city,
                public_url=build_listing_public_url("activity", a.id),
            )

    # 4. Eco Experiences
    if "eco_experience" in by_module:
        m_refs = by_module["eco_experience"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(EcoExperience)
            .filter(EcoExperience.id.in_(tids), public_eco())
            .all()
        )
        by_tid = {e.id: e for e in rows}
        for r in m_refs:
            e = by_tid.get(r.target_id)
            if e is None or e.provider_profile_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="eco_experience",
                target_id=e.id,
                title=e.title or r.title,
                district=e.district or r.district,
                city=e.city or r.city,
                public_url=build_listing_public_url("eco_experience", e.id),
            )

    # 5. Artisan Listings
    if "artisan" in by_module:
        m_refs = by_module["artisan"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(ArtisanListing)
            .filter(ArtisanListing.id.in_(tids), public_artisan())
            .all()
        )
        by_tid = {ar.id: ar for ar in rows}
        for r in m_refs:
            ar = by_tid.get(r.target_id)
            if ar is None or ar.provider_profile_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="artisan",
                target_id=ar.id,
                title=ar.title or r.title,
                district=ar.district or r.district,
                city=ar.city or r.city,
                public_url=build_listing_public_url("artisan", ar.id),
            )

    # 6. Rental Vehicles
    if "rental_vehicle" in by_module:
        m_refs = by_module["rental_vehicle"]
        tids = [r.target_id for r in m_refs]
        rows = (
            db.query(RentalVehicle)
            .filter(RentalVehicle.id.in_(tids), public_rental())
            .all()
        )
        by_tid = {v.id: v for v in rows}
        for r in m_refs:
            v = by_tid.get(r.target_id)
            if v is None or v.provider_id != r.provider_profile_id:
                continue
            eligible_summaries[r.id] = CommunityLinkedListingSummary(
                listing_reference_id=r.id,
                module_type="rental_vehicle",
                target_id=v.id,
                title=v.vehicle_name or r.title,
                district=v.district or r.district,
                city=v.city or r.city,
                public_url=build_listing_public_url("rental_vehicle", v.id),
            )

    return eligible_summaries
