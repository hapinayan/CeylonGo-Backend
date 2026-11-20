"""
Local Tour Guide Module 05 — Public Discovery, Availability Calendar & Price Preview Service.

Enforces Module 05 Spec Section 11 security & business rules:
- Only approved, active guides owned by approved, active providers are publicly visible.
- Private credentials (file_reference, certificate_number, unapproved credentials) are never leaked.
- Verified Guide Badge (is_verified) is true strictly when verification_status == 'verified'.
- Booking availability windows expose anonymized busy slots only (zero traveler PII).
- Prices and group-size limits are calculated and enforced strictly server-side.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math

from fastapi import HTTPException, status
from sqlalchemy import and_, case, exists, func, or_, true
from sqlalchemy.orm import Session, selectinload

from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, PromotionCampaign
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuideCredential,
    TourGuideImage,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
)
from app.models.user import User
from app.schemas.tour_guide import (
    PriceFilterBasis,
    PublicAvailabilityDay,
    PublicGuideAvailabilityCalendarResponse,
    PublicGuideContactResponse,
    PublicGuideCredentialBadge,
    PublicGuideImageSummary,
    PublicGuideLanguageSummary,
    PublicGuideServiceAreaSummary,
    PublicGuideSortBy,
    PublicTimeWindow,
    PublicTourGuideDetailResponse,
    PublicTourGuideListResponse,
    PublicTourGuidePackageDetailResponse,
    PublicTourGuidePackageResponse,
    PublicTourGuideSummary,
    TourGuidePricePreviewRequest,
    TourGuidePricePreviewResponse,
)
from app.services.tour_guide_sync import (
    GUIDE_CATEGORY_BY_SLUG,
    classify_package_duration,
    extract_category_slugs,
    resolve_category_slug,
    validate_currency_code,
)

MAX_CALENDAR_SPAN_DAYS = 90
DEFAULT_CALENDAR_SPAN_DAYS = 30
ACTIVE_BOOKING_STATUSES = ("confirmed", "in_progress")


def _escape_like(term: str) -> str:
    return (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("[", "\\[")
    )


def _public_guide_base_query(db: Session):
    """
    Base query enforcing the strict 4-condition public visibility gate:
    1. TourGuide.approval_status == 'approved'
    2. TourGuide.is_active == True
    3. ProviderProfile.approval_status == 'approved'
    4. User.is_active == True
    """
    return (
        db.query(TourGuide)
        .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
        .join(User, ProviderProfile.user_id == User.id)
        .filter(
            TourGuide.approval_status == "approved",
            TourGuide.is_active == true(),
            ProviderProfile.approval_status == "approved",
            User.is_active == true(),
        )
    )


def get_public_guide_or_404(db: Session, guide_id: int) -> TourGuide:
    """Fetch a publicly visible TourGuide with eager-loaded public sub-resources, or 404."""
    guide = (
        _public_guide_base_query(db)
        .options(
            selectinload(TourGuide.provider_profile).selectinload(ProviderProfile.user),
            selectinload(TourGuide.languages),
            selectinload(TourGuide.service_areas),
            selectinload(TourGuide.images),
            selectinload(TourGuide.packages),
            selectinload(TourGuide.credentials),
        )
        .filter(TourGuide.id == guide_id)
        .first()
    )
    if guide is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide not found",
        )
    return guide


def _active_sponsored_guide_ids(db: Session, guide_ids: list[int] | None = None) -> set[int]:
    """
    Identify guide IDs with an active, unexpired sponsored PromotionCampaign
    linked via dbo.listing_references (target_module='tour_guide').
    """
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    query = (
        db.query(ListingReference.target_id)
        .join(
            PromotionCampaign,
            PromotionCampaign.listing_reference_id == ListingReference.id,
        )
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.is_active == true(),
            PromotionCampaign.target_module == "tour_guide",
            PromotionCampaign.status == "active",
            PromotionCampaign.starts_at <= now_utc,
            PromotionCampaign.ends_at > now_utc,
        )
    )
    if guide_ids is not None:
        if not guide_ids:
            return set()
        query = query.filter(ListingReference.target_id.in_(guide_ids))
    return {row[0] for row in query.all()}


def _is_experience_verified(guide: TourGuide) -> bool:
    if guide.verification_status != "verified":
        return False
    return any(c.verification_status == "approved" for c in guide.credentials)


def _ordered_images(guide: TourGuide) -> list[PublicGuideImageSummary]:
    ordered = sorted(
        guide.images,
        key=lambda img: (not img.is_profile, img.display_order, img.id),
    )
    return [PublicGuideImageSummary.model_validate(img) for img in ordered]


def _ordered_languages(guide: TourGuide) -> list[PublicGuideLanguageSummary]:
    ordered = sorted(
        guide.languages,
        key=lambda lang: (not lang.is_primary, lang.language_name.lower(), lang.id),
    )
    return [PublicGuideLanguageSummary.model_validate(lang) for lang in ordered]


def _ordered_service_areas(guide: TourGuide) -> list[PublicGuideServiceAreaSummary]:
    ordered = sorted(
        guide.service_areas,
        key=lambda area: (
            not area.is_primary,
            area.district.lower(),
            area.city.lower(),
            area.id,
        ),
    )
    return [PublicGuideServiceAreaSummary.model_validate(area) for area in ordered]


def _public_verified_credentials(guide: TourGuide) -> list[PublicGuideCredentialBadge]:
    """Return ONLY admin-approved credentials with strictly safe public fields."""
    approved = sorted(
        (c for c in guide.credentials if c.verification_status == "approved"),
        key=lambda c: c.id,
    )
    return [
        PublicGuideCredentialBadge(
            id=c.id,
            credential_type=c.credential_type,
            title=c.title,
            issuing_authority=c.issuing_authority,
            expiry_date=c.expiry_date,
            verified=True,
        )
        for c in approved
    ]


def _active_packages(guide: TourGuide) -> list[TourGuidePackage]:
    return sorted(
        (p for p in guide.packages if p.is_active),
        key=lambda p: (p.price, p.id),
    )


def _serialize_public_guide_summary(
    guide: TourGuide,
    *,
    is_sponsored: bool,
) -> PublicTourGuideSummary:
    images = _ordered_images(guide)
    primary_image_url = images[0].image_url if images else None
    active_pkgs = _active_packages(guide)
    min_pkg_price = min((p.price for p in active_pkgs), default=None)
    exp_verified = _is_experience_verified(guide)

    return PublicTourGuideSummary(
        id=guide.id,
        business_name=guide.provider_profile.business_name,
        display_name=guide.provider_profile.user.full_name,
        bio=guide.bio,
        years_of_experience=guide.years_of_experience,
        experience_is_verified=exp_verified,
        experience_label="Verified" if exp_verified else "Self-reported",
        specialties=guide.specialties,
        categories=extract_category_slugs(guide.specialties),
        hourly_rate=guide.hourly_rate,
        daily_rate=guide.daily_rate,
        currency=guide.currency,
        base_city=guide.base_city,
        base_district=guide.base_district,
        base_latitude=guide.base_latitude,
        base_longitude=guide.base_longitude,
        is_available=guide.is_available,
        is_verified=guide.verification_status == "verified",
        verification_status=guide.verification_status,
        is_sponsored=is_sponsored,
        rating_average=guide.rating_average,
        review_count=guide.review_count,
        max_group_size=guide.max_group_size,
        primary_image_url=primary_image_url,
        languages=_ordered_languages(guide),
        service_areas=_ordered_service_areas(guide),
        active_packages_count=len(active_pkgs),
        min_package_price=min_pkg_price,
    )


# =============================================================================
# 1. PUBLIC DISCOVERY, SEARCH, FILTERS, SORTING & PAGINATION (TR-01..03,06,07,09,20)
# =============================================================================

def list_public_tour_guides(
    db: Session,
    *,
    q: str | None = None,
    location: str | None = None,
    district: str | None = None,
    city: str | None = None,
    language: str | None = None,
    category: str | None = None,
    min_experience: int | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    price_basis: PriceFilterBasis = "daily",
    currency: str | None = None,
    verified_only: bool = False,
    min_rating: Decimal | None = None,
    available_only: bool = False,
    available_date: date | None = None,
    group_size: int | None = None,
    sort_by: PublicGuideSortBy = "recommended",
    page: int = 1,
    page_size: int = 20,
) -> PublicTourGuideListResponse:
    if min_price is not None and max_price is not None and max_price < min_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="max_price must be greater than or equal to min_price",
        )

    query = _public_guide_base_query(db).options(
        selectinload(TourGuide.provider_profile).selectinload(ProviderProfile.user),
        selectinload(TourGuide.languages),
        selectinload(TourGuide.service_areas),
        selectinload(TourGuide.images),
        selectinload(TourGuide.packages),
        selectinload(TourGuide.credentials),
    )

    if q and q.strip():
        pattern = f"%{_escape_like(q.strip().lower())}%"
        area_match = exists().where(
            TourGuideServiceArea.tour_guide_id == TourGuide.id,
            or_(
                func.lower(TourGuideServiceArea.district).like(pattern, escape="\\"),
                func.lower(TourGuideServiceArea.city).like(pattern, escape="\\"),
                func.lower(TourGuideServiceArea.province).like(pattern, escape="\\"),
            ),
        )
        query = query.filter(
            or_(
                func.lower(ProviderProfile.business_name).like(pattern, escape="\\"),
                func.lower(User.full_name).like(pattern, escape="\\"),
                func.lower(TourGuide.bio).like(pattern, escape="\\"),
                func.lower(func.coalesce(TourGuide.specialties, "")).like(pattern, escape="\\"),
                func.lower(TourGuide.base_city).like(pattern, escape="\\"),
                func.lower(TourGuide.base_district).like(pattern, escape="\\"),
                area_match,
            )
        )

    if location and location.strip():
        loc_pattern = f"%{_escape_like(location.strip().lower())}%"
        loc_area_match = exists().where(
            TourGuideServiceArea.tour_guide_id == TourGuide.id,
            or_(
                func.lower(TourGuideServiceArea.district).like(loc_pattern, escape="\\"),
                func.lower(TourGuideServiceArea.city).like(loc_pattern, escape="\\"),
                func.lower(TourGuideServiceArea.province).like(loc_pattern, escape="\\"),
            ),
        )
        query = query.filter(
            or_(
                func.lower(TourGuide.base_city).like(loc_pattern, escape="\\"),
                func.lower(TourGuide.base_district).like(loc_pattern, escape="\\"),
                loc_area_match,
            )
        )

    if district and district.strip():
        dist_clean = district.strip().lower()
        dist_area_match = exists().where(
            TourGuideServiceArea.tour_guide_id == TourGuide.id,
            func.lower(TourGuideServiceArea.district) == dist_clean,
        )
        query = query.filter(
            or_(
                func.lower(TourGuide.base_district) == dist_clean,
                dist_area_match,
            )
        )

    if city and city.strip():
        city_clean = city.strip().lower()
        city_area_match = exists().where(
            TourGuideServiceArea.tour_guide_id == TourGuide.id,
            func.lower(TourGuideServiceArea.city) == city_clean,
        )
        query = query.filter(
            or_(
                func.lower(TourGuide.base_city) == city_clean,
                city_area_match,
            )
        )

    if language and language.strip():
        lang_clean = language.strip().lower()
        lang_pattern = f"%{_escape_like(lang_clean)}%"
        lang_match = exists().where(
            TourGuideLanguage.tour_guide_id == TourGuide.id,
            or_(
                func.lower(TourGuideLanguage.language_code) == lang_clean,
                func.lower(TourGuideLanguage.language_name) == lang_clean,
                func.lower(TourGuideLanguage.language_name).like(lang_pattern, escape="\\"),
            ),
        )
        query = query.filter(lang_match)

    if category and category.strip():
        slug = resolve_category_slug(category)
        if slug is None:
            allowed = ", ".join(GUIDE_CATEGORY_BY_SLUG.keys())
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unsupported category '{category}'. Allowed categories: {allowed}",
            )
        canonical_name = GUIDE_CATEGORY_BY_SLUG[slug]["name"].lower()
        cat_pattern = f"%{_escape_like(canonical_name)}%"
        query = query.filter(
            func.lower(func.coalesce(TourGuide.specialties, "")).like(cat_pattern, escape="\\")
        )

    if min_experience is not None:
        query = query.filter(TourGuide.years_of_experience >= min_experience)

    if currency and currency.strip():
        try:
            clean_currency = validate_currency_code(currency)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        query = query.filter(TourGuide.currency == clean_currency)

    if min_price is not None or max_price is not None:
        if price_basis == "daily":
            if min_price is not None:
                query = query.filter(TourGuide.daily_rate >= min_price)
            if max_price is not None:
                query = query.filter(TourGuide.daily_rate <= max_price)
        elif price_basis == "hourly":
            query = query.filter(TourGuide.hourly_rate.is_not(None))
            if min_price is not None:
                query = query.filter(TourGuide.hourly_rate >= min_price)
            if max_price is not None:
                query = query.filter(TourGuide.hourly_rate <= max_price)
        elif price_basis == "package":
            pkg_conds = [
                TourGuidePackage.tour_guide_id == TourGuide.id,
                TourGuidePackage.is_active == true(),
            ]
            if min_price is not None:
                pkg_conds.append(TourGuidePackage.price >= min_price)
            if max_price is not None:
                pkg_conds.append(TourGuidePackage.price <= max_price)
            query = query.filter(exists().where(*pkg_conds))
        else:  # "any"
            daily_conds = []
            hourly_conds = [TourGuide.hourly_rate.is_not(None)]
            pkg_conds = [
                TourGuidePackage.tour_guide_id == TourGuide.id,
                TourGuidePackage.is_active == true(),
            ]
            if min_price is not None:
                daily_conds.append(TourGuide.daily_rate >= min_price)
                hourly_conds.append(TourGuide.hourly_rate >= min_price)
                pkg_conds.append(TourGuidePackage.price >= min_price)
            if max_price is not None:
                daily_conds.append(TourGuide.daily_rate <= max_price)
                hourly_conds.append(TourGuide.hourly_rate <= max_price)
                pkg_conds.append(TourGuidePackage.price <= max_price)
            query = query.filter(
                or_(
                    and_(*daily_conds),
                    and_(*hourly_conds),
                    exists().where(*pkg_conds),
                )
            )

    if verified_only:
        query = query.filter(TourGuide.verification_status == "verified")

    if min_rating is not None:
        query = query.filter(TourGuide.rating_average >= min_rating)

    if available_only or available_date is not None:
        query = query.filter(TourGuide.is_available == true())

    if available_date is not None:
        # Exclude guides with a full-day block on available_date
        full_day_blocked = exists().where(
            TourGuideAvailability.tour_guide_id == TourGuide.id,
            TourGuideAvailability.available_date == available_date,
            TourGuideAvailability.is_available == False,
            TourGuideAvailability.start_time.is_(None),
            TourGuideAvailability.end_time.is_(None),
        )
        # Exclude guides with an active confirmed/in_progress booking spanning the whole day
        day_start = datetime.combine(available_date, time.min)
        day_end = datetime.combine(available_date, time(23, 59, 59, 999999))
        full_day_booked = exists().where(
            TourGuideBooking.tour_guide_id == TourGuide.id,
            TourGuideBooking.booking_status.in_(ACTIVE_BOOKING_STATUSES),
            TourGuideBooking.start_datetime <= day_start,
            TourGuideBooking.end_datetime >= day_end,
        )
        query = query.filter(~full_day_blocked, ~full_day_booked)

    if group_size is not None:
        pkg_group_match = exists().where(
            TourGuidePackage.tour_guide_id == TourGuide.id,
            TourGuidePackage.is_active == true(),
            TourGuidePackage.max_people >= group_size,
        )
        query = query.filter(
            or_(
                TourGuide.max_group_size >= group_size,
                pkg_group_match,
            )
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    sponsored_ids = _active_sponsored_guide_ids(db) if total > 0 else set()

    if sort_by == "price_asc":
        query = query.order_by(TourGuide.daily_rate.asc(), TourGuide.id.asc())
    elif sort_by == "price_desc":
        query = query.order_by(TourGuide.daily_rate.desc(), TourGuide.id.asc())
    elif sort_by == "rating_desc":
        query = query.order_by(
            TourGuide.rating_average.desc(),
            TourGuide.review_count.desc(),
            TourGuide.id.asc(),
        )
    elif sort_by == "experience_desc":
        query = query.order_by(
            TourGuide.years_of_experience.desc(),
            TourGuide.rating_average.desc(),
            TourGuide.id.asc(),
        )
    elif sort_by == "newest":
        query = query.order_by(TourGuide.created_at.desc(), TourGuide.id.desc())
    else:  # "recommended"
        sponsored_rank = (
            case((TourGuide.id.in_(sponsored_ids), 1), else_=0)
            if sponsored_ids
            else case((TourGuide.id == -1, 1), else_=0)
        )
        verified_rank = case((TourGuide.verification_status == "verified", 1), else_=0)
        query = query.order_by(
            sponsored_rank.desc(),
            verified_rank.desc(),
            TourGuide.rating_average.desc(),
            TourGuide.review_count.desc(),
            TourGuide.id.asc(),
        )

    guides = query.offset((page - 1) * page_size).limit(page_size).all()
    items = [
        _serialize_public_guide_summary(g, is_sponsored=(g.id in sponsored_ids))
        for g in guides
    ]

    return PublicTourGuideListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


# =============================================================================
# 2. PUBLIC GUIDE DETAIL & PACKAGES (TR-04..09, TR-20, GS-01..05)
# =============================================================================

def _serialize_public_package(
    pkg: TourGuidePackage,
    m034_map: dict[int, tuple[str | None, object]] | None = None,
) -> PublicTourGuidePackageResponse:
    dto = PublicTourGuidePackageResponse.model_validate(pkg)
    if m034_map and pkg.id in m034_map:
        instructions, special_reqs = m034_map[pkg.id]
        dto.meeting_instructions = instructions
        dto.special_requirements = special_reqs
    return dto


def get_public_tour_guide_detail(
    db: Session,
    guide_id: int,
) -> PublicTourGuideDetailResponse:
    from app.services.tour_guide_m034_extensions import (
        get_guide_verified_experience_years,
        get_packages_m034_fields_map,
    )

    guide = get_public_guide_or_404(db, guide_id)
    sponsored_ids = _active_sponsored_guide_ids(db, [guide.id])
    is_verified = guide.verification_status == "verified"
    exp_verified = _is_experience_verified(guide)
    verified_years = get_guide_verified_experience_years(db, guide.id)
    active_pkgs = _active_packages(guide)
    pkg_m034_map = get_packages_m034_fields_map(db, [p.id for p in active_pkgs])

    return PublicTourGuideDetailResponse(
        id=guide.id,
        business_name=guide.provider_profile.business_name,
        display_name=guide.provider_profile.user.full_name,
        bio=guide.bio,
        years_of_experience=guide.years_of_experience,
        verified_years_of_experience=verified_years,
        experience_is_verified=exp_verified or (verified_years is not None),
        experience_label="Verified" if (exp_verified or verified_years is not None) else "Self-reported",
        specialties=guide.specialties,
        categories=extract_category_slugs(guide.specialties),
        # Only display official SLTDA license_type publicly when guide is verified
        license_type=guide.license_type if is_verified else None,
        hourly_rate=guide.hourly_rate,
        daily_rate=guide.daily_rate,
        currency=guide.currency,
        base_city=guide.base_city,
        base_district=guide.base_district,
        base_latitude=guide.base_latitude,
        base_longitude=guide.base_longitude,
        is_available=guide.is_available,
        is_verified=is_verified,
        verification_status=guide.verification_status,
        is_sponsored=(guide.id in sponsored_ids),
        rating_average=guide.rating_average,
        review_count=guide.review_count,
        cancellation_policy=guide.cancellation_policy,
        free_cancellation_hours=guide.free_cancellation_hours,
        max_group_size=guide.max_group_size,
        images=_ordered_images(guide),
        languages=_ordered_languages(guide),
        service_areas=_ordered_service_areas(guide),
        verified_credentials=_public_verified_credentials(guide),
        packages=[_serialize_public_package(p, pkg_m034_map) for p in active_pkgs],
    )


def list_public_guide_packages(
    db: Session,
    guide_id: int,
    *,
    duration_category: str | None = None,
    pricing_type: str | None = None,
    max_price: Decimal | None = None,
    min_group_size: int | None = None,
) -> list[PublicTourGuidePackageResponse]:
    from app.services.tour_guide_m034_extensions import get_packages_m034_fields_map

    guide = get_public_guide_or_404(db, guide_id)
    packages = _active_packages(guide)
    pkg_m034_map = get_packages_m034_fields_map(db, [p.id for p in packages])

    filtered: list[PublicTourGuidePackageResponse] = []
    for pkg in packages:
        if pricing_type and pkg.pricing_type != pricing_type:
            continue
        if max_price is not None and pkg.price > max_price:
            continue
        if min_group_size is not None and pkg.max_people < min_group_size:
            continue
        dto = _serialize_public_package(pkg, pkg_m034_map)
        if duration_category and dto.duration_category != duration_category:
            continue
        filtered.append(dto)
    return filtered


def get_public_package_detail(
    db: Session,
    package_id: int,
) -> PublicTourGuidePackageDetailResponse:
    from app.services.tour_guide_m034_extensions import get_packages_m034_fields_map

    pkg = (
        db.query(TourGuidePackage)
        .join(TourGuide, TourGuidePackage.tour_guide_id == TourGuide.id)
        .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
        .join(User, ProviderProfile.user_id == User.id)
        .options(
            selectinload(TourGuidePackage.tour_guide)
            .selectinload(TourGuide.provider_profile)
            .selectinload(ProviderProfile.user)
        )
        .filter(
            TourGuidePackage.id == package_id,
            TourGuidePackage.is_active == true(),
            TourGuide.approval_status == "approved",
            TourGuide.is_active == true(),
            ProviderProfile.approval_status == "approved",
            User.is_active == true(),
        )
        .first()
    )
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour package not found",
        )

    guide = pkg.tour_guide
    pkg_m034_map = get_packages_m034_fields_map(db, [pkg.id])
    return PublicTourGuidePackageDetailResponse(
        package=_serialize_public_package(pkg, pkg_m034_map),
        guide_id=guide.id,
        guide_business_name=guide.provider_profile.business_name,
        guide_display_name=guide.provider_profile.user.full_name,
        guide_base_city=guide.base_city,
        guide_base_district=guide.base_district,
        guide_is_verified=guide.verification_status == "verified",
        guide_rating_average=guide.rating_average,
        guide_review_count=guide.review_count,
        cancellation_policy=guide.cancellation_policy,
        free_cancellation_hours=guide.free_cancellation_hours,
    )


# =============================================================================
# 3. PUBLIC AVAILABILITY CALENDAR (TR-10)
# =============================================================================

def get_public_guide_availability_calendar(
    db: Session,
    guide_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> PublicGuideAvailabilityCalendarResponse:
    guide = get_public_guide_or_404(db, guide_id)

    today_utc = datetime.now(UTC).date()
    start_d = date_from or today_utc
    end_d = date_to or (start_d + timedelta(days=DEFAULT_CALENDAR_SPAN_DAYS))

    if end_d < start_d:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )
    if (end_d - start_d).days > MAX_CALENDAR_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Availability calendar range cannot exceed {MAX_CALENDAR_SPAN_DAYS} days",
        )

    slots = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.tour_guide_id == guide.id,
            TourGuideAvailability.available_date >= start_d,
            TourGuideAvailability.available_date <= end_d,
        )
        .order_by(
            TourGuideAvailability.available_date.asc(),
            TourGuideAvailability.start_time.asc(),
        )
        .all()
    )

    range_start_dt = datetime.combine(start_d, time.min)
    range_end_dt = datetime.combine(end_d, time(23, 59, 59, 999999))
    active_bookings = (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.booking_status.in_(ACTIVE_BOOKING_STATUSES),
            TourGuideBooking.start_datetime <= range_end_dt,
            TourGuideBooking.end_datetime >= range_start_dt,
        )
        .order_by(TourGuideBooking.start_datetime.asc())
        .all()
    )

    slots_by_date: dict[date, list[TourGuideAvailability]] = {}
    for slot in slots:
        slots_by_date.setdefault(slot.available_date, []).append(slot)

    days: list[PublicAvailabilityDay] = []
    total_days = (end_d - start_d).days + 1
    for offset in range(total_days):
        cur_date = start_d + timedelta(days=offset)
        cur_start_dt = datetime.combine(cur_date, time.min)
        cur_end_dt = datetime.combine(cur_date, time(23, 59, 59, 999999))

        open_windows: list[PublicTimeWindow] = []
        blocked_windows: list[PublicTimeWindow] = []
        booked_windows: list[PublicTimeWindow] = []

        for slot in slots_by_date.get(cur_date, []):
            is_full = slot.start_time is None and slot.end_time is None
            win = PublicTimeWindow(
                start_time=slot.start_time,
                end_time=slot.end_time,
                full_day=is_full,
                status="open" if slot.is_available else "blocked",
            )
            if slot.is_available:
                open_windows.append(win)
            else:
                blocked_windows.append(win)

        for booking in active_bookings:
            if booking.start_datetime < cur_end_dt and booking.end_datetime > cur_start_dt:
                covers_full = (
                    booking.start_datetime <= cur_start_dt
                    and booking.end_datetime >= cur_end_dt
                )
                clipped_start = max(booking.start_datetime, cur_start_dt).time()
                clipped_end = min(booking.end_datetime, cur_end_dt).time()
                booked_windows.append(
                    PublicTimeWindow(
                        start_time=None if covers_full else clipped_start,
                        end_time=None if covers_full else clipped_end,
                        full_day=covers_full,
                        status="booked",
                    )
                )

        has_full_block = any(w.full_day for w in blocked_windows)
        has_full_booking = any(w.full_day for w in booked_windows)

        if not guide.is_available or has_full_block:
            day_status = "unavailable"
            is_bookable = False
        elif has_full_booking:
            day_status = "booked"
            is_bookable = False
        elif blocked_windows or booked_windows:
            day_status = "partially_available"
            is_bookable = True
        else:
            day_status = "available"
            is_bookable = True

        days.append(
            PublicAvailabilityDay(
                date=cur_date,
                status=day_status,
                is_bookable=is_bookable,
                open_windows=open_windows,
                blocked_windows=blocked_windows,
                booked_windows=booked_windows,
            )
        )

    return PublicGuideAvailabilityCalendarResponse(
        tour_guide_id=guide.id,
        date_from=start_d,
        date_to=end_d,
        guide_is_available=guide.is_available,
        days=days,
    )


# =============================================================================
# 4. SERVER-SIDE PRICE PREVIEW (TR-09, GS-04, GS-05)
# =============================================================================

def _check_window_availability(
    db: Session,
    guide: TourGuide,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[bool, str | None]:
    if not guide.is_available:
        return False, "Guide is currently marked unavailable for new bookings."

    # Check overlapping confirmed/in_progress bookings
    booking_conflict = (
        db.query(TourGuideBooking.id)
        .filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.booking_status.in_(ACTIVE_BOOKING_STATUSES),
            TourGuideBooking.start_datetime < end_dt,
            TourGuideBooking.end_datetime > start_dt,
        )
        .first()
    )
    if booking_conflict is not None:
        return False, "Requested date/time conflicts with an existing confirmed booking."

    # Check blocked availability slots across each covered calendar day
    cur_date = start_dt.date()
    end_date = end_dt.date()
    blocked_slots = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.tour_guide_id == guide.id,
            TourGuideAvailability.is_available == False,
            TourGuideAvailability.available_date >= cur_date,
            TourGuideAvailability.available_date <= end_date,
        )
        .all()
    )
    for slot in blocked_slots:
        slot_start = datetime.combine(slot.available_date, slot.start_time or time.min)
        slot_end = (
            datetime.combine(slot.available_date, slot.end_time)
            if slot.end_time is not None
            else datetime.combine(slot.available_date, time(23, 59, 59, 999999))
        )
        if slot_start < end_dt and slot_end > start_dt:
            return False, f"Guide has blocked availability on {slot.available_date}."

    return True, "Guide appears available for the requested date/time."


def calculate_tour_guide_price_preview(
    db: Session,
    guide_id: int,
    payload: TourGuidePricePreviewRequest,
) -> TourGuidePricePreviewResponse:
    guide = get_public_guide_or_404(db, guide_id)

    package_id: int | None = None
    package_title: str | None = None

    if payload.rate_type == "package":
        pkg = (
            db.query(TourGuidePackage)
            .filter(
                TourGuidePackage.id == payload.package_id,
                TourGuidePackage.tour_guide_id == guide.id,
                TourGuidePackage.is_active == true(),
            )
            .first()
        )
        if pkg is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active tour package not found for this guide",
            )
        if payload.number_of_people > pkg.max_people:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"package maximum of {pkg.max_people} travelers."
                ),
            )
        package_id = pkg.id
        package_title = pkg.title
        pricing_type = pkg.pricing_type
        unit_price = pkg.price
        max_group_size = pkg.max_people
        duration_hours = pkg.duration_hours
        currency = pkg.currency
        if pricing_type == "per_person":
            subtotal = (unit_price * Decimal(payload.number_of_people)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            quantity_description = f"{payload.number_of_people} traveler(s) x {unit_price} {currency}"
        else:
            subtotal = unit_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            quantity_description = f"Flat group rate (up to {max_group_size} travelers)"

    elif payload.rate_type == "hourly":
        if guide.hourly_rate is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This tour guide does not offer hourly rate bookings.",
            )
        if payload.number_of_people > guide.max_group_size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"guide maximum of {guide.max_group_size} travelers."
                ),
            )
        pricing_type = "hourly"
        unit_price = guide.hourly_rate
        max_group_size = guide.max_group_size
        duration_hours = payload.duration_hours
        currency = guide.currency
        subtotal = (unit_price * duration_hours).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        quantity_description = f"{duration_hours} hour(s) x {unit_price} {currency}/hr"

    else:  # "daily"
        if payload.number_of_people > guide.max_group_size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"guide maximum of {guide.max_group_size} travelers."
                ),
            )
        pricing_type = "daily"
        unit_price = guide.daily_rate
        max_group_size = guide.max_group_size
        duration_hours = Decimal(payload.duration_days * 8).quantize(Decimal("0.01"))
        currency = guide.currency
        subtotal = (unit_price * Decimal(payload.duration_days)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        quantity_description = f"{payload.duration_days} day(s) x {unit_price} {currency}/day"

    estimated_start: datetime | None = None
    estimated_end: datetime | None = None
    is_available_window: bool | None = None
    availability_note: str | None = None

    if payload.booking_date is not None:
        effective_start_time = payload.start_time or time(8, 0)
        estimated_start = datetime.combine(payload.booking_date, effective_start_time)
        if payload.rate_type == "daily" and payload.duration_days is not None:
            # Multi-day daily booking runs through end of last day
            last_day = payload.booking_date + timedelta(days=payload.duration_days - 1)
            estimated_end = datetime.combine(last_day, time(18, 0))
            if estimated_end <= estimated_start:
                estimated_end = estimated_start + timedelta(hours=8)
        else:
            estimated_end = estimated_start + timedelta(hours=float(duration_hours))

        is_available_window, availability_note = _check_window_availability(
            db, guide, estimated_start, estimated_end
        )

    return TourGuidePricePreviewResponse(
        tour_guide_id=guide.id,
        package_id=package_id,
        package_title=package_title,
        rate_type=payload.rate_type,
        pricing_type=pricing_type,
        unit_price=unit_price,
        quantity_description=quantity_description,
        number_of_people=payload.number_of_people,
        max_group_size=max_group_size,
        duration_hours=duration_hours,
        duration_category=classify_package_duration(float(duration_hours)),
        subtotal=subtotal,
        total_price=subtotal,
        currency=currency,
        estimated_start_datetime=estimated_start,
        estimated_end_datetime=estimated_end,
        is_available_for_requested_window=is_available_window,
        availability_note=availability_note,
        cancellation_policy=guide.cancellation_policy,
        free_cancellation_hours=guide.free_cancellation_hours,
    )


# =============================================================================
# 5. PUBLIC CONTACT INFORMATION WITH PRIVACY & VERIFICATION RULES (TR-18)
# =============================================================================

def mask_phone_number(phone: str) -> str:
    cleaned = phone.strip()
    if len(cleaned) <= 5:
        return "*" * len(cleaned)
    return f"{cleaned[:3]}{'*' * (len(cleaned) - 6)}{cleaned[-3:]}"


def get_public_guide_contact(
    db: Session,
    guide_id: int,
    *,
    authenticated_user: User | None,
) -> PublicGuideContactResponse:
    from app.services.tour_guide_m034_extensions import (
        get_public_guide_whatsapp_contact,
    )

    guide = get_public_guide_or_404(db, guide_id)
    provider = guide.provider_profile
    is_authenticated = authenticated_user is not None
    is_verified = guide.verification_status == "verified"

    wa = get_public_guide_whatsapp_contact(
        db,
        guide=guide,
        is_authenticated=is_authenticated,
    )
    channels = ["phone"]
    if wa["whatsapp_available"]:
        channels.append("whatsapp")

    if is_authenticated:
        phone_display = provider.phone
        secondary_display = provider.secondary_phone
        privacy_note = (
            "Approved guide phone and opted-in WhatsApp contact shown for authenticated user."
            if wa["whatsapp_available"]
            else (
                "Approved guide phone contact shown for authenticated user. "
                "Dedicated WhatsApp opt-in requires schema extension (Migration 034)."
            )
        )
    else:
        phone_display = mask_phone_number(provider.phone)
        secondary_display = None
        privacy_note = (
            "Sign in to view direct guide phone contact details. "
            "Personal account email and identity documents are never exposed."
        )

    return PublicGuideContactResponse(
        tour_guide_id=guide.id,
        business_name=provider.business_name,
        display_name=provider.user.full_name,
        base_city=guide.base_city,
        base_district=guide.base_district,
        is_verified=is_verified,
        verification_status=guide.verification_status,
        is_authenticated_view=is_authenticated,
        requires_authentication_for_direct_contact=not is_authenticated,
        phone=phone_display,
        secondary_phone=secondary_display,
        whatsapp_available=wa["whatsapp_available"],
        whatsapp_number=wa["whatsapp_number"],
        whatsapp_link=wa["whatsapp_link"],
        available_channels=channels,
        privacy_note=privacy_note,
    )
