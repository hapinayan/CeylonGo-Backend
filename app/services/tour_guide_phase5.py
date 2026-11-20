"""
Local Tour Guide Module 05 — Phase 5 Services:
- TR-16: Traveler Ratings & Reviews (verified completed bookings + rating sync + moderation)
- TR-17: Traveler Wishlist / Favorite Guides
- PR-01: Provider Onboarding & Guide Dashboard
- PR-16: Provider Customer Reviews & Rating Breakdown
- PR-19: Sponsored Promotions & Advertising Payment Preparation/Verification
- PR-20: Promotion Events, Campaign Analytics & Provider Performance Dashboard
- AD-07: Admin Promotion Packages, Campaign Approvals & Ad Payment Oversight
- AD-08: Admin Guide, Booking, Revenue & Engagement Reports

Strictly complies with existing migrations 001–033 CHECK constraints
(including CK_promotion_campaigns_status and CK_promotion_events_type).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
import math
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.notification_service import create_notification
from app.core.payhere import payhere_service
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    BookingReference,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
    ProviderSettlement,
    Review,
    Wishlist,
)
from app.models.tour_guide import (
    TourGuide,
    TourGuideBooking,
    TourGuideCredential,
    TourGuideImage,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
)
from app.models.user import User
from app.schemas.tour_guide import (
    AdminAdPaymentItem,
    AdminAdPaymentListResponse,
    AdminCampaignDecisionRequest,
    AdminTourGuideReportsOverviewResponse,
    ProviderDashboardBookingSummary,
    ProviderDashboardPromotionSummary,
    ProviderOnboardingChecklist,
    ProviderTourGuideAnalyticsResponse,
    ProviderTourGuideDashboardResponse,
    SavedTourGuideItem,
    TourGuideCampaignAnalyticsResponse,
    TourGuideCampaignCreate,
    TourGuideCampaignListResponse,
    TourGuideCampaignPaymentPrepareResponse,
    TourGuideCampaignResponse,
    TourGuidePromotionEventCreate,
    TourGuidePromotionEventResponse,
    TourGuidePromotionPackageCreate,
    TourGuidePromotionPackageResponse,
    TourGuidePromotionPackageUpdate,
    TourGuideProviderEarningsResponse,
    TourGuideReviewCreate,
    TourGuideReviewListResponse,
    TourGuideReviewResponse,
    TourGuideWishlistActionResponse,
    TourGuideWishlistListResponse,
    TourGuideWishlistStatusResponse,
)
from app.services.tour_guide_booking import (
    _booking_eager_query,
    _get_booking_reference_map,
    _serialize_booking,
    expire_stale_pending_bookings,
    sync_tour_guide_booking_reference,
)
from app.services.tour_guide_payment import (
    PAID_TRANSACTION_STATUSES,
    calculate_tour_guide_commission,
    calculate_tour_guide_provider_earnings,
    verify_payhere_ipn_payload,
)
from app.services.tour_guide_public import get_public_guide_or_404
from app.services.tour_guide_sync import (
    record_tour_guide_admin_audit as log_admin_action,
    sync_tour_guide_listing_reference,
)


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _ensure_guide_listing_reference(db: Session, guide: TourGuide) -> ListingReference:
    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .first()
    )
    if ref is None:
        ref = sync_tour_guide_listing_reference(db, guide)
        db.flush()
    return ref


def _rating_distribution(db: Session, listing_ref_id: int) -> dict[str, int]:
    dist = {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}
    rows = (
        db.query(Review.rating)
        .filter(
            Review.listing_reference_id == listing_ref_id,
            Review.is_published == true(),
        )
        .all()
    )
    for (rating_val,) in rows:
        bucket = max(1, min(5, int(Decimal(str(rating_val)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))))
        dist[str(bucket)] += 1
    return dist


# =============================================================================
# 1. RATINGS & REVIEWS (TR-16, PR-16, ADMIN MODERATION)
# =============================================================================

def recalculate_tour_guide_ratings(db: Session, guide: TourGuide) -> tuple[Decimal, int]:
    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .first()
    )
    if ref is None:
        guide.rating_average = Decimal("0.00")
        guide.review_count = 0
        return guide.rating_average, guide.review_count

    count_val, avg_val = (
        db.query(func.count(Review.id), func.avg(Review.rating))
        .filter(
            Review.listing_reference_id == ref.id,
            Review.is_published == true(),
        )
        .first()
    )
    review_count = int(count_val or 0)
    rating_avg = (
        Decimal(str(avg_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if review_count > 0 and avg_val is not None
        else Decimal("0.00")
    )
    guide.rating_average = rating_avg
    guide.review_count = review_count
    return rating_avg, review_count


def submit_tour_guide_review(
    db: Session,
    *,
    guide_id: int,
    traveler: User,
    payload: TourGuideReviewCreate,
) -> TourGuideReviewResponse:
    guide = get_public_guide_or_404(db, guide_id)

    booking = (
        db.query(TourGuideBooking)
        .filter(
            TourGuideBooking.id == payload.booking_id,
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.traveler_id == traveler.id,
        )
        .first()
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eligible tour guide booking not found for this traveler.",
        )

    if booking.booking_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reviews can only be submitted after the tour guide booking is completed.",
        )

    listing_ref = _ensure_guide_listing_reference(db, guide)
    booking_ref = sync_tour_guide_booking_reference(db, booking, guide)

    existing = (
        db.query(Review)
        .filter(Review.booking_reference_id == booking_ref.id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You have already submitted a review for this completed booking.",
        )

    rating_clean = Decimal(str(payload.rating)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    review = Review(
        listing_reference_id=listing_ref.id,
        booking_reference_id=booking_ref.id,
        user_id=traveler.id,
        rating=rating_clean,
        title=payload.title,
        comment=payload.comment,
        is_verified_booking=True,
        is_published=True,
    )
    db.add(review)
    db.flush()

    recalculate_tour_guide_ratings(db, guide)

    create_notification(
        db=db,
        user_id=guide.provider_profile.user_id,
        title=f"New Review for {guide.provider_profile.business_name}",
        message=(
            f"{traveler.full_name} left a {rating_clean}-star verified review"
            + (f": \"{payload.title}\"" if payload.title else ".")
        ),
        notification_type="new_guide_review",
        link_url="/provider/tour-guides/reviews",
    )

    _commit(db)
    db.refresh(review)

    return TourGuideReviewResponse(
        id=review.id,
        tour_guide_id=guide.id,
        listing_reference_id=review.listing_reference_id,
        booking_reference_id=review.booking_reference_id,
        user_id=review.user_id,
        reviewer_name=traveler.full_name,
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        is_verified_booking=review.is_verified_booking,
        is_published=review.is_published,
        created_at=review.created_at,
    )


def _list_reviews_for_guide(
    db: Session,
    *,
    guide: TourGuide,
    published_only: bool,
    page: int,
    page_size: int,
) -> TourGuideReviewListResponse:
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .first()
    )
    if listing_ref is None:
        return TourGuideReviewListResponse(
            items=[],
            total=0,
            average_rating=Decimal("0.00"),
            rating_distribution={"5": 0, "4": 0, "3": 0, "2": 0, "1": 0},
            page=page,
            page_size=page_size,
            total_pages=0,
            has_more=False,
        )

    query = (
        db.query(Review, User.full_name)
        .outerjoin(User, Review.user_id == User.id)
        .filter(Review.listing_reference_id == listing_ref.id)
    )
    if published_only:
        query = query.filter(Review.is_published == true())

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(Review.created_at.desc(), Review.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    avg_val = (
        db.query(func.avg(Review.rating))
        .filter(
            Review.listing_reference_id == listing_ref.id,
            Review.is_published == true(),
        )
        .scalar()
    )
    average_rating = (
        Decimal(str(avg_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if avg_val is not None
        else Decimal("0.00")
    )

    items = [
        TourGuideReviewResponse(
            id=r.id,
            tour_guide_id=guide.id,
            listing_reference_id=r.listing_reference_id,
            booking_reference_id=r.booking_reference_id,
            user_id=r.user_id,
            reviewer_name=author_name or "Verified Traveler",
            rating=r.rating,
            title=r.title,
            comment=r.comment,
            is_verified_booking=r.is_verified_booking,
            is_published=r.is_published,
            created_at=r.created_at,
        )
        for r, author_name in rows
    ]

    return TourGuideReviewListResponse(
        items=items,
        total=total,
        average_rating=average_rating,
        rating_distribution=_rating_distribution(db, listing_ref.id),
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def list_public_tour_guide_reviews(
    db: Session,
    *,
    guide_id: int,
    page: int = 1,
    page_size: int = 10,
) -> TourGuideReviewListResponse:
    guide = get_public_guide_or_404(db, guide_id)
    return _list_reviews_for_guide(
        db, guide=guide, published_only=True, page=page, page_size=page_size
    )


def list_provider_tour_guide_reviews(
    db: Session,
    *,
    guide: TourGuide,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideReviewListResponse:
    return _list_reviews_for_guide(
        db, guide=guide, published_only=True, page=page, page_size=page_size
    )


def admin_moderate_tour_guide_review(
    db: Session,
    *,
    review_id: int,
    is_published: bool,
    admin: User,
    reason: str | None = None,
) -> TourGuideReviewResponse:
    row = (
        db.query(Review, ListingReference, User.full_name)
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .outerjoin(User, Review.user_id == User.id)
        .filter(
            Review.id == review_id,
            ListingReference.module_type == "tour_guide",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide review not found.",
        )

    review, listing_ref, author_name = row
    guide = db.query(TourGuide).filter(TourGuide.id == listing_ref.target_id).first()

    review.is_published = is_published
    review.updated_at = _now_utc()
    db.flush()

    if guide is not None:
        recalculate_tour_guide_ratings(db, guide)

    log_admin_action(
        db,
        admin_user_id=admin.id,
        action="moderate_tour_guide_review",
        entity_name="review",
        entity_id=review.id,
        details=f"is_published={is_published}; reason={reason or 'N/A'}",
    )

    _commit(db)
    db.refresh(review)

    return TourGuideReviewResponse(
        id=review.id,
        tour_guide_id=listing_ref.target_id,
        listing_reference_id=review.listing_reference_id,
        booking_reference_id=review.booking_reference_id,
        user_id=review.user_id,
        reviewer_name=author_name or "Verified Traveler",
        rating=review.rating,
        title=review.title,
        comment=review.comment,
        is_verified_booking=review.is_verified_booking,
        is_published=review.is_published,
        created_at=review.created_at,
    )


# =============================================================================
# 2. WISHLIST / FAVORITE GUIDES (TR-17)
# =============================================================================

def add_tour_guide_to_wishlist(
    db: Session,
    *,
    guide_id: int,
    traveler: User,
    notes: str | None = None,
) -> TourGuideWishlistActionResponse:
    guide = get_public_guide_or_404(db, guide_id)
    listing_ref = _ensure_guide_listing_reference(db, guide)

    existing = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == traveler.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This tour guide is already in your wishlist.",
        )

    entry = Wishlist(
        user_id=traveler.id,
        listing_reference_id=listing_ref.id,
        notes=notes.strip() if notes and notes.strip() else None,
    )
    db.add(entry)
    _commit(db)
    db.refresh(entry)

    return TourGuideWishlistActionResponse(
        message="Tour guide saved to your wishlist.",
        tour_guide_id=guide.id,
        saved=True,
        wishlist_id=entry.id,
    )


def remove_tour_guide_from_wishlist(
    db: Session,
    *,
    guide_id: int,
    traveler: User,
) -> TourGuideWishlistActionResponse:
    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide_id,
        )
        .first()
    )
    if listing_ref is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide is not in your wishlist.",
        )

    entry = (
        db.query(Wishlist)
        .filter(
            Wishlist.user_id == traveler.id,
            Wishlist.listing_reference_id == listing_ref.id,
        )
        .first()
    )
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide is not in your wishlist.",
        )

    db.delete(entry)
    _commit(db)

    return TourGuideWishlistActionResponse(
        message="Tour guide removed from your wishlist.",
        tour_guide_id=guide_id,
        saved=False,
        wishlist_id=None,
    )


def get_tour_guide_wishlist_status(
    db: Session,
    *,
    guide_id: int,
    traveler: User,
) -> TourGuideWishlistStatusResponse:
    row = (
        db.query(Wishlist.id)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .filter(
            Wishlist.user_id == traveler.id,
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide_id,
        )
        .first()
    )
    return TourGuideWishlistStatusResponse(
        tour_guide_id=guide_id,
        saved=row is not None,
        wishlist_id=row[0] if row is not None else None,
    )


def list_traveler_tour_guide_wishlist(
    db: Session,
    *,
    traveler: User,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideWishlistListResponse:
    query = (
        db.query(Wishlist, TourGuide)
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .join(TourGuide, ListingReference.target_id == TourGuide.id)
        .join(ProviderProfile, TourGuide.provider_profile_id == ProviderProfile.id)
        .join(User, ProviderProfile.user_id == User.id)
        .options(
            selectinload(TourGuide.provider_profile).selectinload(ProviderProfile.user),
            selectinload(TourGuide.images),
        )
        .filter(
            Wishlist.user_id == traveler.id,
            ListingReference.module_type == "tour_guide",
            TourGuide.approval_status == "approved",
            TourGuide.is_active == true(),
            ProviderProfile.approval_status == "approved",
            User.is_active == true(),
        )
    )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(Wishlist.created_at.desc(), Wishlist.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items: list[SavedTourGuideItem] = []
    for wl, guide in rows:
        ordered_imgs = sorted(
            guide.images,
            key=lambda img: (not img.is_profile, img.display_order, img.id),
        )
        profile_img_url = ordered_imgs[0].image_url if ordered_imgs else None
        items.append(
            SavedTourGuideItem(
                wishlist_id=wl.id,
                saved_at=wl.created_at,
                notes=wl.notes,
                tour_guide_id=guide.id,
                business_name=guide.provider_profile.business_name,
                display_name=guide.provider_profile.user.full_name,
                base_city=guide.base_city,
                base_district=guide.base_district,
                daily_rate=guide.daily_rate,
                hourly_rate=guide.hourly_rate,
                currency=guide.currency,
                rating_average=guide.rating_average,
                review_count=guide.review_count,
                is_verified=(guide.verification_status == "verified"),
                is_available=guide.is_available,
                profile_image_url=profile_img_url,
            )
        )

    return TourGuideWishlistListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


# =============================================================================
# 3. SPONSORED PROMOTIONS, EVENTS & ADVERTISING PAYMENT (PR-19, PR-20, AD-07)
# =============================================================================

def expire_finished_tour_guide_campaigns(
    db: Session,
    *,
    provider_profile_id: int | None = None,
) -> int:
    now_utc = _now_utc()
    q = db.query(PromotionCampaign).filter(
        PromotionCampaign.target_module == "tour_guide",
        PromotionCampaign.status == "active",
        PromotionCampaign.ends_at <= now_utc,
    )
    if provider_profile_id is not None:
        q = q.filter(PromotionCampaign.provider_profile_id == provider_profile_id)
    expired = q.all()
    if not expired:
        return 0
    for c in expired:
        c.status = "completed"
    _commit(db)
    return len(expired)


def _campaign_event_counts(db: Session, campaign_ids: list[int]) -> dict[int, dict[str, int]]:
    result = {
        cid: {"impression": 0, "click": 0, "inquiry": 0, "booking": 0}
        for cid in campaign_ids
    }
    if not campaign_ids:
        return result
    rows = (
        db.query(PromotionEvent.campaign_id, PromotionEvent.event_type, func.count(PromotionEvent.id))
        .filter(PromotionEvent.campaign_id.in_(campaign_ids))
        .group_by(PromotionEvent.campaign_id, PromotionEvent.event_type)
        .all()
    )
    for cid, ev_type, cnt in rows:
        if cid in result and ev_type in result[cid]:
            result[cid][ev_type] = int(cnt)
    return result


def _campaign_latest_payment_status(db: Session, campaign_ids: list[int]) -> dict[int, str | None]:
    if not campaign_ids:
        return {}
    rows = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.campaign_id.in_(campaign_ids))
        .order_by(AdvertisingPayment.id.desc())
        .all()
    )
    latest: dict[int, str | None] = {}
    for p in rows:
        if p.campaign_id not in latest:
            latest[p.campaign_id] = p.payment_status
    return latest


def _serialize_campaign(
    db: Session,
    campaign: PromotionCampaign,
    *,
    counts: dict[str, int] | None = None,
    latest_payment_status: str | None = None,
    guide_id: int | None = None,
    business_name: str | None = None,
) -> TourGuideCampaignResponse:
    c = counts or {"impression": 0, "click": 0, "inquiry": 0, "booking": 0}
    resolved_guide_id = guide_id
    if resolved_guide_id is None and campaign.listing_reference is not None:
        resolved_guide_id = campaign.listing_reference.target_id
    return TourGuideCampaignResponse(
        id=campaign.id,
        provider_profile_id=campaign.provider_profile_id,
        tour_guide_id=resolved_guide_id,
        guide_business_name=business_name,
        listing_reference_id=campaign.listing_reference_id,
        campaign_name=campaign.campaign_name,
        target_module=campaign.target_module,
        budget=campaign.budget,
        spent_amount=campaign.spent_amount,
        currency=campaign.currency,
        status=campaign.status,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        created_at=campaign.created_at,
        impressions=c["impression"],
        clicks=c["click"],
        inquiries=c["inquiry"],
        bookings=c["booking"],
        latest_payment_status=latest_payment_status,
    )


def list_active_promotion_packages(db: Session) -> list[TourGuidePromotionPackageResponse]:
    today = _now_utc().date()
    rows = (
        db.query(PromotionPackage)
        .filter(
            PromotionPackage.is_active == true(),
            PromotionPackage.valid_to >= today,
        )
        .order_by(PromotionPackage.created_at.desc(), PromotionPackage.id.desc())
        .all()
    )
    return [TourGuidePromotionPackageResponse.model_validate(r) for r in rows]


def create_tour_guide_campaign(
    db: Session,
    *,
    guide: TourGuide,
    payload: TourGuideCampaignCreate,
) -> TourGuideCampaignResponse:
    if guide.approval_status != "approved" or not guide.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active, approved tour guide profiles can create sponsored campaigns.",
        )

    now_utc = _now_utc()
    if payload.ends_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be in the future.",
        )

    listing_ref = _ensure_guide_listing_reference(db, guide)
    initial_status = "pending_approval" if payload.submit_immediately else "draft"

    campaign = PromotionCampaign(
        provider_profile_id=guide.provider_profile_id,
        listing_reference_id=listing_ref.id,
        campaign_name=payload.campaign_name.strip(),
        target_module="tour_guide",
        budget=payload.budget.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        spent_amount=Decimal("0.00"),
        currency=payload.currency,
        status=initial_status,
        starts_at=payload.starts_at.replace(tzinfo=None),
        ends_at=payload.ends_at.replace(tzinfo=None),
    )
    db.add(campaign)
    _commit(db)
    db.refresh(campaign)

    return _serialize_campaign(
        db,
        campaign,
        guide_id=guide.id,
        business_name=guide.provider_profile.business_name,
    )


def list_provider_tour_guide_campaigns(
    db: Session,
    *,
    guide: TourGuide,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideCampaignListResponse:
    expire_finished_tour_guide_campaigns(db, provider_profile_id=guide.provider_profile_id)
    query = db.query(PromotionCampaign).filter(
        PromotionCampaign.provider_profile_id == guide.provider_profile_id,
        PromotionCampaign.target_module == "tour_guide",
    )
    if status_filter:
        query = query.filter(PromotionCampaign.status == status_filter.strip().lower())

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(PromotionCampaign.created_at.desc(), PromotionCampaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    cids = [r.id for r in rows]
    ev_map = _campaign_event_counts(db, cids)
    pay_map = _campaign_latest_payment_status(db, cids)

    items = [
        _serialize_campaign(
            db,
            r,
            counts=ev_map.get(r.id),
            latest_payment_status=pay_map.get(r.id),
            guide_id=guide.id,
            business_name=guide.provider_profile.business_name,
        )
        for r in rows
    ]
    return TourGuideCampaignListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def _require_owned_campaign(
    db: Session, *, guide: TourGuide, campaign_id: int
) -> PromotionCampaign:
    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.provider_profile_id == guide.provider_profile_id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .first()
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide promotion campaign not found.",
        )
    return campaign


def submit_tour_guide_campaign(
    db: Session,
    *,
    guide: TourGuide,
    campaign_id: int,
) -> TourGuideCampaignResponse:
    if guide.approval_status != "approved" or not guide.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tour guide profile must be approved and active to submit a campaign.",
        )
    campaign = _require_owned_campaign(db, guide=guide, campaign_id=campaign_id)
    if campaign.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only draft campaigns can be submitted (current status: '{campaign.status}').",
        )
    if campaign.ends_at <= _now_utc():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot submit a campaign whose end date has already passed.",
        )

    campaign.status = "pending_approval"
    _commit(db)
    db.refresh(campaign)
    return _serialize_campaign(
        db,
        campaign,
        counts=_campaign_event_counts(db, [campaign.id]).get(campaign.id),
        latest_payment_status=_campaign_latest_payment_status(db, [campaign.id]).get(campaign.id),
        guide_id=guide.id,
        business_name=guide.provider_profile.business_name,
    )


def update_provider_tour_guide_campaign_status(
    db: Session,
    *,
    guide: TourGuide,
    campaign_id: int,
    new_status: str,
) -> TourGuideCampaignResponse:
    expire_finished_tour_guide_campaigns(db, provider_profile_id=guide.provider_profile_id)
    campaign = _require_owned_campaign(db, guide=guide, campaign_id=campaign_id)

    if new_status == "paused":
        if campaign.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only active campaigns can be paused.",
            )
    elif new_status == "active":
        if campaign.status != "paused":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only paused campaigns can be resumed by the provider.",
            )
        if campaign.ends_at <= _now_utc():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot resume a campaign whose end date has passed.",
            )
        if guide.approval_status != "approved" or not guide.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tour guide profile must be approved and active to resume a campaign.",
            )
    elif new_status == "cancelled":
        if campaign.status in ("completed", "cancelled"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel campaign with status '{campaign.status}'.",
            )

    campaign.status = new_status
    _commit(db)
    db.refresh(campaign)
    return _serialize_campaign(
        db,
        campaign,
        counts=_campaign_event_counts(db, [campaign.id]).get(campaign.id),
        latest_payment_status=_campaign_latest_payment_status(db, [campaign.id]).get(campaign.id),
        guide_id=guide.id,
        business_name=guide.provider_profile.business_name,
    )


def prepare_tour_guide_campaign_payment(
    db: Session,
    *,
    guide: TourGuide,
    campaign_id: int,
) -> TourGuideCampaignPaymentPrepareResponse:
    if guide.approval_status != "approved" or not guide.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only approved, active tour guides can pay for sponsored campaigns.",
        )

    campaign = _require_owned_campaign(db, guide=guide, campaign_id=campaign_id)
    if campaign.status in ("completed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot pay for a campaign with status '{campaign.status}'.",
        )
    if campaign.ends_at <= _now_utc():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot initiate advertising payment for an expired campaign.",
        )

    existing_completed = (
        db.query(AdvertisingPayment)
        .filter(
            AdvertisingPayment.campaign_id == campaign.id,
            AdvertisingPayment.payment_status == "completed",
        )
        .first()
    )
    if existing_completed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Advertising payment has already been completed for this campaign.",
        )

    ad_pay = (
        db.query(AdvertisingPayment)
        .filter(
            AdvertisingPayment.campaign_id == campaign.id,
            AdvertisingPayment.payment_status == "pending",
        )
        .order_by(AdvertisingPayment.id.desc())
        .first()
    )
    if ad_pay is None:
        order_ref = f"AD-TG-{campaign.id}-{uuid.uuid4().hex[:8].upper()}"
        ad_pay = AdvertisingPayment(
            campaign_id=campaign.id,
            provider_profile_id=guide.provider_profile_id,
            order_reference=order_ref,
            amount=campaign.budget,
            currency=campaign.currency or "LKR",
            gateway_name="payhere",
            payment_status="pending",
        )
        db.add(ad_pay)
        db.flush()
    else:
        ad_pay.amount = campaign.budget
        ad_pay.currency = campaign.currency or "LKR"

    _commit(db)
    db.refresh(ad_pay)

    checkout_hash = payhere_service.generate_checkout_hash(
        order_id=ad_pay.order_reference,
        amount=ad_pay.amount,
        currency=ad_pay.currency,
    )
    return TourGuideCampaignPaymentPrepareResponse(
        campaign_id=campaign.id,
        advertising_payment_id=ad_pay.id,
        order_reference=ad_pay.order_reference,
        payment_mode=payhere_service.mode,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        amount=ad_pay.amount,
        currency=ad_pay.currency,
        hash=checkout_hash,
        items=f"Sponsored Tour Guide Campaign: {campaign.campaign_name}",
        return_url=payhere_service.return_url,
        cancel_url=payhere_service.cancel_url,
        notify_url=payhere_service.notify_url,
    )


def handle_tour_guide_advertising_payment_callback(
    db: Session,
    *,
    form_data: dict,
) -> dict:
    """
    Isolated PayHere IPN handler for Tour Guide advertising payments (`AD-TG-...`).
    Verifies merchant_id + MD5 signature + exact amount/currency, enforces
    idempotency, and activates eligible tour_guide campaigns safely.
    """
    verified = verify_payhere_ipn_payload(form_data)
    order_id = verified["order_id"]
    payment_id = verified["payment_id"]
    received_amt = verified["amount"]
    received_currency = verified["currency"]
    status_code = verified["status_code"]

    ad_pay = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.order_reference == order_id)
        .first()
    )
    if ad_pay is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Advertising payment order '{order_id}' not found.",
        )

    expected_amt = Decimal(str(ad_pay.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    expected_curr = (ad_pay.currency or "LKR").upper()
    if received_amt != expected_amt or received_currency != expected_curr:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Advertising payment amount or currency mismatch.",
        )

    if ad_pay.payment_status == "completed":
        return {"status": "already_processed", "campaign_id": ad_pay.campaign_id}

    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.id == ad_pay.campaign_id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .first()
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated tour guide promotion campaign not found.",
        )

    now_utc = _now_utc()
    ad_pay.gateway_transaction_id = payment_id
    ad_pay.updated_at = now_utc

    if status_code == "2":
        ad_pay.payment_status = "completed"
        ad_pay.verified_at = now_utc

        guide = None
        if campaign.listing_reference_id is not None:
            lref = db.query(ListingReference).filter(ListingReference.id == campaign.listing_reference_id).first()
            if lref is not None and lref.module_type == "tour_guide":
                guide = db.query(TourGuide).filter(TourGuide.id == lref.target_id).first()

        is_eligible = (
            guide is not None
            and guide.is_active
            and guide.approval_status == "approved"
            and guide.provider_profile_id == campaign.provider_profile_id
            and campaign.ends_at > now_utc
            and campaign.status != "cancelled"
        )
        if is_eligible:
            campaign.status = "active"
            campaign.spent_amount = received_amt
            create_notification(
                db=db,
                user_id=guide.provider_profile.user_id,
                title="Sponsored Guide Campaign Activated",
                message=(
                    f"Payment for '{campaign.campaign_name}' ({received_amt} {received_currency}) "
                    "was verified and your sponsored placement is now active."
                ),
                notification_type="guide_campaign_activated",
                link_url="/provider/tour-guides/promotions",
            )
        elif campaign.ends_at <= now_utc:
            campaign.status = "completed"
    elif status_code in ("-1", "-2"):
        ad_pay.payment_status = "failed"

    _commit(db)
    return {
        "status": "ok",
        "campaign_id": campaign.id,
        "campaign_status": campaign.status,
        "payment_status": ad_pay.payment_status,
    }


def record_tour_guide_promotion_event(
    db: Session,
    *,
    payload: TourGuidePromotionEventCreate,
    current_user_id: int | None = None,
    ip_address: str | None = None,
) -> TourGuidePromotionEventResponse:
    expire_finished_tour_guide_campaigns(db)
    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.id == payload.campaign_id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .first()
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide promotion campaign not found.",
        )

    now_utc = _now_utc()
    if campaign.status != "active" or campaign.ends_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Promotion events can only be recorded for active, unexpired campaigns.",
        )

    booking_ref_id: int | None = None
    if payload.booking_id is not None:
        booking_ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "tour_guide",
                BookingReference.target_booking_id == payload.booking_id,
                BookingReference.provider_profile_id == campaign.provider_profile_id,
            )
            .first()
        )
        if booking_ref is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Referenced tour guide booking does not match this campaign's guide.",
            )
        booking_ref_id = booking_ref.id

    ev = PromotionEvent(
        campaign_id=campaign.id,
        booking_reference_id=booking_ref_id,
        event_type=payload.event_type,
        user_id=current_user_id,
        ip_address=ip_address[:45] if ip_address else None,
    )
    db.add(ev)
    _commit(db)
    db.refresh(ev)

    return TourGuidePromotionEventResponse(
        id=ev.id,
        campaign_id=ev.campaign_id,
        booking_reference_id=ev.booking_reference_id,
        event_type=ev.event_type,
        user_id=ev.user_id,
        created_at=ev.created_at,
    )


def get_provider_tour_guide_campaign_analytics(
    db: Session,
    *,
    guide: TourGuide,
    campaign_id: int,
) -> TourGuideCampaignAnalyticsResponse:
    expire_finished_tour_guide_campaigns(db, provider_profile_id=guide.provider_profile_id)
    campaign = _require_owned_campaign(db, guide=guide, campaign_id=campaign_id)
    counts = _campaign_event_counts(db, [campaign.id])[campaign.id]
    impressions = counts["impression"]
    clicks = counts["click"]
    inquiries = counts["inquiry"]
    bookings = counts["booking"]
    ctr = round((clicks / impressions) * 100.0, 2) if impressions > 0 else 0.0
    conv = round((bookings / clicks) * 100.0, 2) if clicks > 0 else 0.0
    remaining = max(Decimal("0.00"), campaign.budget - campaign.spent_amount)

    return TourGuideCampaignAnalyticsResponse(
        campaign_id=campaign.id,
        campaign_name=campaign.campaign_name,
        status=campaign.status,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        budget=campaign.budget,
        spent_amount=campaign.spent_amount,
        remaining_budget=remaining,
        currency=campaign.currency,
        impressions=impressions,
        clicks=clicks,
        inquiries=inquiries,
        bookings=bookings,
        ctr_percent=ctr,
        conversion_rate_percent=conv,
    )


# =============================================================================
# 4. PROVIDER ONBOARDING & DASHBOARD (PR-01) + PERFORMANCE ANALYTICS (PR-20)
# =============================================================================

def get_provider_tour_guide_dashboard(
    db: Session,
    *,
    provider: ProviderProfile,
) -> ProviderTourGuideDashboardResponse:
    guide = (
        db.query(TourGuide)
        .filter(TourGuide.provider_profile_id == provider.id)
        .first()
    )

    earnings = calculate_tour_guide_provider_earnings(
        db, provider_profile_id=provider.id
    )

    if guide is None:
        onboarding = ProviderOnboardingChecklist(
            has_profile=False,
            has_languages=False,
            has_service_areas=False,
            has_profile_image=False,
            has_credentials=False,
            has_active_package=False,
            submitted_for_approval=False,
            is_approved=False,
            is_verified=False,
            completion_percent=0,
            next_steps=[
                "Create your tour guide profile (bio, base city/district, rates, specialties).",
                "Add spoken languages and service areas.",
                "Upload a profile photo and SLTDA/guide credentials.",
                "Create at least one tour package and submit your profile for admin approval.",
            ],
        )
        return ProviderTourGuideDashboardResponse(
            provider_profile_id=provider.id,
            business_name=provider.business_name,
            provider_approval_status=provider.approval_status,
            tour_guide_id=None,
            guide_approval_status=None,
            guide_verification_status=None,
            is_active=False,
            is_available=False,
            is_verified_badge=False,
            rating_average=Decimal("0.00"),
            review_count=0,
            wishlist_saves_count=0,
            onboarding=onboarding,
            bookings=ProviderDashboardBookingSummary(
                total=0,
                pending=0,
                confirmed=0,
                in_progress=0,
                completed=0,
                cancelled=0,
                rejected=0,
                expired=0,
                upcoming_bookings=[],
            ),
            earnings=earnings,
            promotions=ProviderDashboardPromotionSummary(
                active_campaigns=0,
                pending_campaigns=0,
                total_impressions=0,
                total_clicks=0,
                total_attributed_bookings=0,
            ),
        )

    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    expire_finished_tour_guide_campaigns(db, provider_profile_id=provider.id)

    lang_count = db.query(func.count(TourGuideLanguage.id)).filter(TourGuideLanguage.tour_guide_id == guide.id).scalar() or 0
    area_count = db.query(func.count(TourGuideServiceArea.id)).filter(TourGuideServiceArea.tour_guide_id == guide.id).scalar() or 0
    img_count = db.query(func.count(TourGuideImage.id)).filter(TourGuideImage.tour_guide_id == guide.id).scalar() or 0
    cred_count = db.query(func.count(TourGuideCredential.id)).filter(TourGuideCredential.tour_guide_id == guide.id).scalar() or 0
    pkg_count = db.query(func.count(TourGuidePackage.id)).filter(TourGuidePackage.tour_guide_id == guide.id, TourGuidePackage.is_active == true()).scalar() or 0

    has_languages = lang_count > 0
    has_service_areas = area_count > 0
    has_profile_image = img_count > 0
    has_credentials = cred_count > 0
    has_active_package = pkg_count > 0
    submitted = guide.approval_status in ("pending", "approved")
    is_approved = guide.approval_status == "approved"
    is_verified = guide.verification_status == "verified"

    checklist_flags = [
        True,  # has_profile
        has_languages,
        has_service_areas,
        has_profile_image,
        has_credentials,
        has_active_package,
        submitted,
        is_approved,
    ]
    completion_percent = round((sum(1 for f in checklist_flags if f) / len(checklist_flags)) * 100)
    next_steps: list[str] = []
    if not has_languages:
        next_steps.append("Add at least one spoken language.")
    if not has_service_areas:
        next_steps.append("Add your service areas (province/district/city).")
    if not has_profile_image:
        next_steps.append("Upload a profile photo for travelers.")
    if not has_credentials:
        next_steps.append("Upload your SLTDA license or guide credential for verification.")
    if not has_active_package:
        next_steps.append("Create at least one active tour package.")
    if guide.approval_status == "draft":
        next_steps.append("Submit your tour guide profile for admin approval.")
    elif guide.approval_status == "pending":
        next_steps.append("Await admin approval of your tour guide profile.")
    elif guide.approval_status == "rejected":
        next_steps.append("Review admin rejection feedback and resubmit your profile.")
    elif not is_verified:
        next_steps.append("Complete credential verification to earn the Verified Guide badge.")

    # Booking counts & upcoming preview
    status_counts = {
        "pending": 0,
        "confirmed": 0,
        "in_progress": 0,
        "completed": 0,
        "cancelled": 0,
        "rejected": 0,
        "expired": 0,
    }
    b_rows = (
        db.query(TourGuideBooking.booking_status, func.count(TourGuideBooking.id))
        .filter(TourGuideBooking.tour_guide_id == guide.id)
        .group_by(TourGuideBooking.booking_status)
        .all()
    )
    total_bookings = 0
    for st, cnt in b_rows:
        c_int = int(cnt)
        total_bookings += c_int
        if st in status_counts:
            status_counts[st] = c_int

    now_utc = _now_utc()
    upcoming_rows = (
        _booking_eager_query(db)
        .filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.booking_status.in_(("pending", "confirmed", "in_progress")),
            TourGuideBooking.end_datetime >= now_utc,
        )
        .order_by(TourGuideBooking.start_datetime.asc())
        .limit(5)
        .all()
    )
    ref_map = _get_booking_reference_map(db, [b.id for b in upcoming_rows])
    upcoming_items = [_serialize_booking(b, ref_map.get(b.id)) for b in upcoming_rows]

    # Wishlist saves count
    wishlist_saves = (
        db.query(func.count(Wishlist.id))
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .scalar()
        or 0
    )

    # Promotions summary
    campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.provider_profile_id == provider.id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .all()
    )
    ev_map = _campaign_event_counts(db, [c.id for c in campaigns])
    promo_summary = ProviderDashboardPromotionSummary(
        active_campaigns=sum(1 for c in campaigns if c.status == "active"),
        pending_campaigns=sum(1 for c in campaigns if c.status == "pending_approval"),
        total_impressions=sum(ev_map[c.id]["impression"] for c in campaigns),
        total_clicks=sum(ev_map[c.id]["click"] for c in campaigns),
        total_attributed_bookings=sum(ev_map[c.id]["booking"] for c in campaigns),
    )

    return ProviderTourGuideDashboardResponse(
        provider_profile_id=provider.id,
        business_name=provider.business_name,
        provider_approval_status=provider.approval_status,
        tour_guide_id=guide.id,
        guide_approval_status=guide.approval_status,
        guide_verification_status=guide.verification_status,
        is_active=guide.is_active,
        is_available=guide.is_available,
        is_verified_badge=is_verified,
        rating_average=guide.rating_average,
        review_count=guide.review_count,
        wishlist_saves_count=int(wishlist_saves),
        onboarding=ProviderOnboardingChecklist(
            has_profile=True,
            has_languages=has_languages,
            has_service_areas=has_service_areas,
            has_profile_image=has_profile_image,
            has_credentials=has_credentials,
            has_active_package=has_active_package,
            submitted_for_approval=submitted,
            is_approved=is_approved,
            is_verified=is_verified,
            completion_percent=completion_percent,
            next_steps=next_steps,
        ),
        bookings=ProviderDashboardBookingSummary(
            total=total_bookings,
            pending=status_counts["pending"],
            confirmed=status_counts["confirmed"],
            in_progress=status_counts["in_progress"],
            completed=status_counts["completed"],
            cancelled=status_counts["cancelled"],
            rejected=status_counts["rejected"],
            expired=status_counts["expired"],
            upcoming_bookings=upcoming_items,
        ),
        earnings=earnings,
        promotions=promo_summary,
    )


def get_provider_tour_guide_analytics(
    db: Session,
    *,
    guide: TourGuide,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProviderTourGuideAnalyticsResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )

    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    expire_finished_tour_guide_campaigns(db, provider_profile_id=guide.provider_profile_id)

    bq = db.query(TourGuideBooking).filter(TourGuideBooking.tour_guide_id == guide.id)
    if date_from:
        bq = bq.filter(TourGuideBooking.booking_date >= date_from)
    if date_to:
        bq = bq.filter(TourGuideBooking.booking_date <= date_to)
    bookings = bq.all()

    total_b = len(bookings)
    confirmed_or_done = sum(
        1 for b in bookings if b.booking_status in ("confirmed", "in_progress", "completed")
    )
    completed = sum(1 for b in bookings if b.booking_status == "completed")
    cancelled = sum(1 for b in bookings if b.booking_status == "cancelled")
    rejected = sum(1 for b in bookings if b.booking_status == "rejected")
    decided = confirmed_or_done + rejected

    acceptance_rate = round((confirmed_or_done / decided) * 100.0, 2) if decided > 0 else 0.0
    completion_rate = round((completed / total_b) * 100.0, 2) if total_b > 0 else 0.0
    cancellation_rate = round((cancelled / total_b) * 100.0, 2) if total_b > 0 else 0.0

    non_cancelled_prices = [
        b.total_price for b in bookings if b.booking_status not in ("cancelled", "rejected", "expired")
    ]
    avg_val = (
        (sum(non_cancelled_prices, Decimal("0.00")) / Decimal(len(non_cancelled_prices))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if non_cancelled_prices
        else Decimal("0.00")
    )

    earnings = calculate_tour_guide_provider_earnings(
        db,
        provider_profile_id=guide.provider_profile_id,
        period_start=date_from,
        period_end=date_to,
    )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide.id,
        )
        .first()
    )
    dist = _rating_distribution(db, listing_ref.id) if listing_ref else {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}
    wishlist_saves = (
        db.query(func.count(Wishlist.id))
        .filter(Wishlist.listing_reference_id == listing_ref.id)
        .scalar()
        or 0
        if listing_ref
        else 0
    )

    campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.provider_profile_id == guide.provider_profile_id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .all()
    )
    ev_map = _campaign_event_counts(db, [c.id for c in campaigns])
    imp = sum(ev_map[c.id]["impression"] for c in campaigns)
    clk = sum(ev_map[c.id]["click"] for c in campaigns)
    inq = sum(ev_map[c.id]["inquiry"] for c in campaigns)
    pb = sum(ev_map[c.id]["booking"] for c in campaigns)

    return ProviderTourGuideAnalyticsResponse(
        tour_guide_id=guide.id,
        date_from=date_from,
        date_to=date_to,
        total_bookings=total_b,
        confirmed_or_completed_bookings=confirmed_or_done,
        completed_bookings=completed,
        cancelled_bookings=cancelled,
        rejected_bookings=rejected,
        acceptance_rate_percent=acceptance_rate,
        completion_rate_percent=completion_rate,
        cancellation_rate_percent=cancellation_rate,
        average_booking_value=avg_val,
        online_gross_revenue=earnings.total_online_gross_earnings,
        cash_collected_revenue=earnings.cash_collected_total,
        currency=guide.currency or "LKR",
        rating_average=guide.rating_average,
        review_count=guide.review_count,
        rating_distribution=dist,
        wishlist_saves_count=int(wishlist_saves),
        total_campaigns=len(campaigns),
        active_campaigns=sum(1 for c in campaigns if c.status == "active"),
        promotion_impressions=imp,
        promotion_clicks=clk,
        promotion_inquiries=inq,
        promotion_bookings=pb,
        promotion_ctr_percent=round((clk / imp) * 100.0, 2) if imp > 0 else 0.0,
        promotion_conversion_rate_percent=round((pb / clk) * 100.0, 2) if clk > 0 else 0.0,
    )


# =============================================================================
# 5. ADMIN PROMOTION MANAGEMENT (AD-07) & ADMIN REPORTS OVERVIEW (AD-08)
# =============================================================================

def admin_create_tour_guide_promotion_package(
    db: Session,
    *,
    payload: TourGuidePromotionPackageCreate,
    admin: User,
    provider_profile_id: int | None = None,
) -> TourGuidePromotionPackageResponse:
    target_provider_id = provider_profile_id
    if target_provider_id is None:
        first_provider = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.provider_type == "tour_guide")
            .first()
            or db.query(ProviderProfile).first()
        )
        if first_provider is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one provider profile is required to attach a promotion package.",
            )
        target_provider_id = first_provider.id
    else:
        exists = db.query(ProviderProfile.id).filter(ProviderProfile.id == target_provider_id).first()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider profile not found.",
            )

    pkg = PromotionPackage(
        provider_profile_id=target_provider_id,
        title=payload.title.strip(),
        description=payload.description.strip() if payload.description else None,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        currency=payload.currency,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_active=payload.is_active,
    )
    db.add(pkg)
    db.flush()

    log_admin_action(
        db,
        admin_user_id=admin.id,
        action="create_tour_guide_promotion_package",
        entity_name="promotion_package",
        entity_id=pkg.id,
        details=f"title={pkg.title}; active={pkg.is_active}",
    )
    _commit(db)
    db.refresh(pkg)
    return TourGuidePromotionPackageResponse.model_validate(pkg)


def admin_list_tour_guide_promotion_packages(db: Session) -> list[TourGuidePromotionPackageResponse]:
    rows = db.query(PromotionPackage).order_by(PromotionPackage.id.desc()).all()
    return [TourGuidePromotionPackageResponse.model_validate(r) for r in rows]


def admin_update_tour_guide_promotion_package(
    db: Session,
    *,
    package_id: int,
    payload: TourGuidePromotionPackageUpdate,
    admin: User,
) -> TourGuidePromotionPackageResponse:
    pkg = db.query(PromotionPackage).filter(PromotionPackage.id == package_id).first()
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion package not found.",
        )

    updates = payload.model_dump(exclude_unset=True)
    new_from = updates.get("valid_from", pkg.valid_from)
    new_to = updates.get("valid_to", pkg.valid_to)
    if new_to < new_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_to must be on or after valid_from",
        )

    for k, v in updates.items():
        setattr(pkg, k, v.strip() if isinstance(v, str) else v)

    log_admin_action(
        db,
        admin_user_id=admin.id,
        action="update_tour_guide_promotion_package",
        entity_name="promotion_package",
        entity_id=pkg.id,
        details=f"updated_fields={sorted(updates.keys())}",
    )
    _commit(db)
    db.refresh(pkg)
    return TourGuidePromotionPackageResponse.model_validate(pkg)


def admin_list_tour_guide_campaigns(
    db: Session,
    *,
    status_filter: str | None = None,
    provider_profile_id: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideCampaignListResponse:
    expire_finished_tour_guide_campaigns(db)
    query = (
        db.query(PromotionCampaign, ProviderProfile.business_name)
        .join(ProviderProfile, PromotionCampaign.provider_profile_id == ProviderProfile.id)
        .options(selectinload(PromotionCampaign.listing_reference))
        .filter(PromotionCampaign.target_module == "tour_guide")
    )
    if status_filter:
        query = query.filter(PromotionCampaign.status == status_filter.strip().lower())
    if provider_profile_id is not None:
        query = query.filter(PromotionCampaign.provider_profile_id == provider_profile_id)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(PromotionCampaign.created_at.desc(), PromotionCampaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    cids = [c.id for c, _ in rows]
    ev_map = _campaign_event_counts(db, cids)
    pay_map = _campaign_latest_payment_status(db, cids)

    items = [
        _serialize_campaign(
            db,
            c,
            counts=ev_map.get(c.id),
            latest_payment_status=pay_map.get(c.id),
            business_name=b_name,
        )
        for c, b_name in rows
    ]
    return TourGuideCampaignListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def admin_decide_tour_guide_campaign(
    db: Session,
    *,
    campaign_id: int,
    payload: AdminCampaignDecisionRequest,
    admin: User,
) -> TourGuideCampaignResponse:
    expire_finished_tour_guide_campaigns(db)
    campaign = (
        db.query(PromotionCampaign)
        .options(selectinload(PromotionCampaign.listing_reference))
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "tour_guide",
        )
        .first()
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide promotion campaign not found.",
        )

    profile = db.query(ProviderProfile).filter(ProviderProfile.id == campaign.provider_profile_id).first()
    guide = (
        db.query(TourGuide)
        .filter(TourGuide.provider_profile_id == campaign.provider_profile_id)
        .first()
    )

    action = payload.action
    if action == "approve":
        if campaign.status not in ("pending_approval", "draft", "paused"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot approve campaign in status '{campaign.status}'.",
            )
        if guide is None or guide.approval_status != "approved" or not guide.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve campaign for an inactive or unapproved tour guide.",
            )
        if campaign.ends_at <= _now_utc():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot approve a campaign whose end date has passed.",
            )
        campaign.status = "active"
    elif action == "reject":
        if not payload.reason or len(payload.reason.strip()) < 5:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Rejection reason of at least 5 characters is required.",
            )
        if campaign.status not in ("pending_approval", "draft"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot reject campaign in status '{campaign.status}'.",
            )
        campaign.status = "cancelled"
    elif action == "pause":
        if campaign.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only active campaigns can be paused.",
            )
        campaign.status = "paused"
    elif action == "cancel":
        if campaign.status in ("completed", "cancelled"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Campaign is already '{campaign.status}'.",
            )
        campaign.status = "cancelled"

    log_admin_action(
        db,
        admin_user_id=admin.id,
        action=f"tour_guide_campaign_{action}",
        entity_name="promotion_campaign",
        entity_id=campaign.id,
        details=f"new_status={campaign.status}; reason={payload.reason or 'N/A'}",
    )

    if profile is not None:
        create_notification(
            db=db,
            user_id=profile.user_id,
            title=f"Tour Guide Campaign {action.title()}d",
            message=(
                f"Your sponsored campaign '{campaign.campaign_name}' status is now '{campaign.status}'."
                + (f" Note: {payload.reason.strip()}" if payload.reason else "")
            ),
            notification_type=f"guide_campaign_{action}",
            link_url="/provider/tour-guides/promotions",
        )

    _commit(db)
    db.refresh(campaign)
    return _serialize_campaign(
        db,
        campaign,
        counts=_campaign_event_counts(db, [campaign.id]).get(campaign.id),
        latest_payment_status=_campaign_latest_payment_status(db, [campaign.id]).get(campaign.id),
        guide_id=guide.id if guide else None,
        business_name=profile.business_name if profile else None,
    )


def admin_list_tour_guide_ad_payments(
    db: Session,
    *,
    payment_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminAdPaymentListResponse:
    base_q = (
        db.query(AdvertisingPayment, PromotionCampaign.campaign_name)
        .join(PromotionCampaign, AdvertisingPayment.campaign_id == PromotionCampaign.id)
        .filter(PromotionCampaign.target_module == "tour_guide")
    )

    verified_total = (
        db.query(func.coalesce(func.sum(AdvertisingPayment.amount), Decimal("0.00")))
        .join(PromotionCampaign, AdvertisingPayment.campaign_id == PromotionCampaign.id)
        .filter(
            PromotionCampaign.target_module == "tour_guide",
            AdvertisingPayment.payment_status == "completed",
        )
        .scalar()
    )

    query = base_q
    if payment_status:
        query = query.filter(AdvertisingPayment.payment_status == payment_status.strip().lower())

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(AdvertisingPayment.initiated_at.desc(), AdvertisingPayment.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = [
        AdminAdPaymentItem(
            id=p.id,
            campaign_id=p.campaign_id,
            campaign_name=c_name,
            provider_profile_id=p.provider_profile_id,
            order_reference=p.order_reference,
            gateway_transaction_id=p.gateway_transaction_id,
            amount=p.amount,
            currency=p.currency,
            payment_status=p.payment_status,
            initiated_at=p.initiated_at,
            verified_at=p.verified_at,
        )
        for p, c_name in rows
    ]

    return AdminAdPaymentListResponse(
        items=items,
        total=total,
        total_verified_revenue=Decimal(str(verified_total)).quantize(Decimal("0.01")),
        currency="LKR",
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def admin_get_tour_guide_reports_overview(
    db: Session,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> AdminTourGuideReportsOverviewResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )

    expire_stale_pending_bookings(db)
    expire_finished_tour_guide_campaigns(db)

    guides = db.query(TourGuide).all()
    guides_by_approval = {"draft": 0, "pending": 0, "approved": 0, "rejected": 0, "suspended": 0}
    guides_by_verif = {"unverified": 0, "pending": 0, "verified": 0, "rejected": 0, "suspended": 0}
    guides_by_district: dict[str, int] = {}
    tg_provider_ids: set[int] = set()

    for g in guides:
        tg_provider_ids.add(g.provider_profile_id)
        if g.approval_status in guides_by_approval:
            guides_by_approval[g.approval_status] += 1
        if g.verification_status in guides_by_verif:
            guides_by_verif[g.verification_status] += 1
        dist_key = g.base_district or "Unknown"
        guides_by_district[dist_key] = guides_by_district.get(dist_key, 0) + 1

    bq = db.query(TourGuideBooking)
    if date_from:
        bq = bq.filter(TourGuideBooking.booking_date >= date_from)
    if date_to:
        bq = bq.filter(TourGuideBooking.booking_date <= date_to)
    bookings = bq.all()

    bookings_by_status = {
        "pending": 0,
        "confirmed": 0,
        "in_progress": 0,
        "completed": 0,
        "cancelled": 0,
        "rejected": 0,
        "expired": 0,
    }
    bookings_by_method = {"online": 0, "pay_at_location": 0}
    cash_total = Decimal("0.00")

    for b in bookings:
        if b.booking_status in bookings_by_status:
            bookings_by_status[b.booking_status] += 1
        if b.payment_method in bookings_by_method:
            bookings_by_method[b.payment_method] += 1
        if b.payment_method == "pay_at_location" and b.payment_status == "paid":
            cash_total += b.total_price

    total_b = len(bookings)
    comp_rate = round((bookings_by_status["completed"] / total_b) * 100.0, 2) if total_b > 0 else 0.0
    canc_rate = round((bookings_by_status["cancelled"] / total_b) * 100.0, 2) if total_b > 0 else 0.0

    # Online payment transactions & refunds strictly scoped to tour_guide
    tx_rows = (
        db.query(PaymentTransaction, TourGuideBooking)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(TourGuideBooking, BookingReference.target_booking_id == TourGuideBooking.id)
        .filter(
            BookingReference.module_type == "tour_guide",
            PaymentTransaction.payment_status.in_(PAID_TRANSACTION_STATUSES),
        )
        .all()
    )
    online_gross = Decimal("0.00")
    raw_commission = Decimal("0.00")
    tx_ids: list[int] = []
    for tx, b in tx_rows:
        if date_from and b.booking_date < date_from:
            continue
        if date_to and b.booking_date > date_to:
            continue
        tx_ids.append(tx.id)
        gross = tx.gross_amount or tx.amount or Decimal("0.00")
        online_gross += gross
        if tx.commission_amount is not None:
            raw_commission += tx.commission_amount
        else:
            raw_commission += calculate_tour_guide_commission(gross).commission_amount

    verified_refunds = Decimal("0.00")
    commission_reversal = Decimal("0.00")
    if tx_ids:
        r_rows = (
            db.query(PaymentRefund)
            .filter(
                PaymentRefund.payment_transaction_id.in_(tx_ids),
                PaymentRefund.refund_status.in_(("completed", "processed")),
            )
            .all()
        )
        for r in r_rows:
            verified_refunds += r.amount
            commission_reversal += calculate_tour_guide_commission(r.amount).commission_amount
    net_commission = max(Decimal("0.00"), raw_commission - commission_reversal)

    settled_total = Decimal("0.00")
    if tg_provider_ids:
        s_val = (
            db.query(func.coalesce(func.sum(ProviderSettlement.net_payable), Decimal("0.00")))
            .filter(
                ProviderSettlement.provider_profile_id.in_(tg_provider_ids),
                ProviderSettlement.settlement_status.in_(("completed", "paid")),
            )
            .scalar()
        )
        settled_total = Decimal(str(s_val)).quantize(Decimal("0.01"))

    ad_rev_val = (
        db.query(func.coalesce(func.sum(AdvertisingPayment.amount), Decimal("0.00")))
        .join(PromotionCampaign, AdvertisingPayment.campaign_id == PromotionCampaign.id)
        .filter(
            PromotionCampaign.target_module == "tour_guide",
            AdvertisingPayment.payment_status == "completed",
        )
        .scalar()
    )
    ad_rev = Decimal(str(ad_rev_val)).quantize(Decimal("0.01"))

    rev_count_val, rev_avg_val = (
        db.query(func.count(Review.id), func.avg(Review.rating))
        .join(ListingReference, Review.listing_reference_id == ListingReference.id)
        .filter(
            ListingReference.module_type == "tour_guide",
            Review.is_published == true(),
        )
        .first()
    )
    pub_reviews = int(rev_count_val or 0)
    plat_avg = (
        Decimal(str(rev_avg_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if pub_reviews > 0 and rev_avg_val is not None
        else Decimal("0.00")
    )

    wl_total = (
        db.query(func.count(Wishlist.id))
        .join(ListingReference, Wishlist.listing_reference_id == ListingReference.id)
        .filter(ListingReference.module_type == "tour_guide")
        .scalar()
        or 0
    )

    active_campaigns = (
        db.query(func.count(PromotionCampaign.id))
        .filter(
            PromotionCampaign.target_module == "tour_guide",
            PromotionCampaign.status == "active",
        )
        .scalar()
        or 0
    )

    return AdminTourGuideReportsOverviewResponse(
        date_from=date_from,
        date_to=date_to,
        total_guides=len(guides),
        active_guides=sum(1 for g in guides if g.is_active),
        available_guides=sum(1 for g in guides if g.is_active and g.is_available and g.approval_status == "approved"),
        guides_by_approval_status=guides_by_approval,
        guides_by_verification_status=guides_by_verif,
        guides_by_district=guides_by_district,
        total_bookings=total_b,
        bookings_by_status=bookings_by_status,
        bookings_by_payment_method=bookings_by_method,
        booking_completion_rate_percent=comp_rate,
        booking_cancellation_rate_percent=canc_rate,
        online_gross_revenue=online_gross.quantize(Decimal("0.01")),
        ceylongo_commission_revenue=net_commission.quantize(Decimal("0.01")),
        verified_refunds_total=verified_refunds.quantize(Decimal("0.01")),
        direct_cash_collected_total=cash_total.quantize(Decimal("0.01")),
        settled_payouts_total=settled_total,
        verified_advertising_revenue=ad_rev,
        currency="LKR",
        total_published_reviews=pub_reviews,
        platform_average_rating=plat_avg,
        total_wishlist_saves=int(wl_total),
        active_sponsored_campaigns=int(active_campaigns),
    )
