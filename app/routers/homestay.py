from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, and_, or_, text
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_approved_provider
from app.core.homestay_permissions import (
    check_homestay_ownership,
    require_homestay_manager,
)
from app.core.listing_sync import sync_homestay_listing_reference
from app.core.location_validation import (
    HOMESTAY_LOCATION_FIELDS,
    HOMESTAY_REVIEW_STATE,
    write_review_snapshot,
)
from app.core.promotion_pricing import (
    calculate_booking_price,
    calculate_detailed_booking_price,
)
from app.database.connection import get_db
from app.models.booking import Booking
from app.models.homestay import (
    Homestay,
    HomestayAmenity,
    HomestayAvailabilityBlock,
    HomestayImage,
)
from app.models.promotion import HomestayPromotion
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    ListingReference,
    PromotionCampaign,
    ProviderSettlement,
    Review,
)
from app.models.user import User
from app.schemas.homestay import (
    HomestayAvailabilityBlockCreate,
    HomestayAvailabilityBlockResponse,
    HomestayCreateRequest,
    HomestayImageAddRequest,
    HomestayImageReorderRequest,
    HomestayImageResponse,
    HomestayListResponse,
    HomestayProviderContactResponse,
    HomestayResponse,
    HomestayUpdateRequest,
)
from app.schemas.homestay_dashboard import (
    HomestayProviderBookingSummary,
    HomestayProviderDashboardResponse,
    HomestayProviderFinancialSummary,
    HomestayProviderListingSummary,
    HomestayProviderMarketingSummary,
    HomestayProviderRatingSummary,
)
from app.services.map_location_management import record_location_correction

router = APIRouter(
    prefix="/api/v1/homestays",
    tags=["Homestays"],
)


def _get_homestay_for_management(
    homestay_id: int,
    current_user: User,
    db: Session,
) -> Homestay:
    homestay = (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay_id)
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found",
        )

    profile = None
    if current_user.role != "admin" and (
        homestay.provider_profile_id is not None
        or current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
        except Exception:
            profile = None

    if not check_homestay_ownership(homestay, current_user, profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot manage this homestay",
        )

    return homestay


def populate_homestay_promotions_and_sponsorship(
    db: Session,
    homestays: list[Homestay],
    now: datetime | None = None,
) -> list[HomestayResponse]:
    """
    Populate is_sponsored, sponsored_campaign_id, and active promotion discount details
    for a list of homestays based on verified advertising payments and active promotion records.
    """
    if not homestays:
        return []

    if now is None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()

    homestay_ids = [h.id for h in homestays]

    # 1. Listing references
    listing_refs = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id.in_(homestay_ids),
        )
        .all()
    )
    listing_ref_id_to_homestay_id = {lr.id: lr.target_id for lr in listing_refs}

    # 2. Paid active campaigns
    # Only campaigns that:
    # - target_module == 'homestay'
    # - status == 'active'
    # - starts_at <= now <= ends_at
    # - verified server-side payment completed in advertising_payments
    paid_campaign_ids = {
        row[0]
        for row in db.query(AdvertisingPayment.campaign_id)
        .filter(AdvertisingPayment.payment_status == "completed")
        .all()
    }

    active_campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.target_module == "homestay",
            PromotionCampaign.status == "active",
            PromotionCampaign.starts_at <= now,
            PromotionCampaign.ends_at >= now,
            PromotionCampaign.id.in_(paid_campaign_ids),
        )
        .all()
    ) if paid_campaign_ids else []

    listing_campaign_map: dict[int, int] = {}

    for camp in active_campaigns:
        # Phase 09: Strict listing-specific attribution only.
        # Historical campaigns without listing_reference_id must NOT sponsor provider homestays.
        if camp.listing_reference_id is not None:
            target_hid = listing_ref_id_to_homestay_id.get(camp.listing_reference_id)
            if target_hid is not None:
                # Authoritative provider ownership check:
                # If homestay has a provider_profile_id, it must match the campaign's provider_profile_id.
                h_obj = next((x for x in homestays if x.id == target_hid), None)
                if h_obj and h_obj.provider_profile_id is not None:
                    if h_obj.provider_profile_id == camp.provider_profile_id:
                        listing_campaign_map[target_hid] = camp.id
                elif h_obj:
                    listing_campaign_map[target_hid] = camp.id

    # 3. Active promotions
    promotions = (
        db.query(HomestayPromotion)
        .filter(
            HomestayPromotion.homestay_id.in_(homestay_ids),
            HomestayPromotion.is_active == True,
            HomestayPromotion.start_date <= today,
            HomestayPromotion.end_date >= today,
        )
        .all()
    )

    promo_map: dict[int, list[HomestayPromotion]] = {}
    for promo in promotions:
        promo_map.setdefault(promo.homestay_id, []).append(promo)

    results: list[HomestayResponse] = []
    for h in homestays:
        item = HomestayResponse.model_validate(h)

        # Sponsorship attribution strictly via listing_campaign_map
        if h.id in listing_campaign_map:
            item.is_sponsored = True
            item.sponsored_campaign_id = listing_campaign_map[h.id]
        else:
            item.is_sponsored = False
            item.sponsored_campaign_id = None

        # Discount attribution
        h_promos = promo_map.get(h.id, [])
        best_promo = None
        best_savings = Decimal("0.00")
        base_price = Decimal(str(h.price_per_night))

        for promo in h_promos:
            savings = Decimal("0.00")
            if promo.discount_type == "percentage" and promo.discount_percent is not None:
                savings = (
                    base_price * Decimal(str(promo.discount_percent)) / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            elif promo.discount_type == "fixed" and promo.discount_amount is not None:
                savings = min(Decimal(str(promo.discount_amount)), base_price).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            if savings > best_savings:
                best_savings = savings
                best_promo = promo

        if best_promo:
            item.discount_percent = best_promo.discount_percent
            item.discount_amount = best_promo.discount_amount
            item.discounted_price_per_night = max(
                Decimal("0.00"), base_price - best_savings
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            item.discount_percent = None
            item.discount_amount = None
            item.discounted_price_per_night = None

        results.append(item)

    return results


# PUBLIC: List and search approved, active homestays
@router.get("", response_model=HomestayListResponse)
def list_homestays(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
    location: Annotated[str | None, Query(max_length=255)] = None,
    province: Annotated[str | None, Query(max_length=100)] = None,
    district: Annotated[str | None, Query(max_length=100)] = None,
    city: Annotated[str | None, Query(max_length=100)] = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    guests: Annotated[int | None, Query(ge=1)] = None,
    name: Annotated[str | None, Query(max_length=150)] = None,
    amenity: Annotated[list[str] | None, Query()] = None,
    sort_by: Annotated[
        str | None,
        Query(
            description="Sort order: price_asc, price_desc, or newest",
            pattern="^(price_asc|price_desc|newest)$",
        ),
    ] = None,
):
    query = db.query(Homestay).filter(
        Homestay.is_active == True,
        Homestay.approval_status == "approved",
    )

    if location and location.strip():
        query = query.filter(
            Homestay.location.ilike(f"%{location.strip()}%")
        )

    if province and province.strip():
        query = query.filter(
            Homestay.province.ilike(f"%{province.strip()}%")
        )

    if district and district.strip():
        query = query.filter(
            Homestay.district.ilike(f"%{district.strip()}%")
        )

    if city and city.strip():
        query = query.filter(
            Homestay.city.ilike(f"%{city.strip()}%")
        )

    if min_price is not None:
        query = query.filter(
            Homestay.price_per_night >= min_price
        )

    if max_price is not None:
        query = query.filter(
            Homestay.price_per_night <= max_price
        )

    if min_price is not None and max_price is not None:
        if min_price > max_price:
            raise HTTPException(
                status_code=422,
                detail="Minimum price cannot exceed maximum price",
            )

    if guests is not None:
        query = query.filter(
            Homestay.max_guests >= guests
        )

    if name and name.strip():
        query = query.filter(
            Homestay.name.ilike(f"%{name.strip()}%")
        )

    if amenity:
        for item in amenity:
            cleaned = item.strip()

            if not cleaned or len(cleaned) > 100:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid amenity",
                )

            query = query.filter(
                Homestay.amenities.any(
                    func.lower(HomestayAmenity.name)
                    == cleaned.lower()
                )
            )

    total = query.count()

    if sort_by == "price_asc":
        order_clauses = [Homestay.price_per_night.asc(), Homestay.id.asc()]
    elif sort_by == "price_desc":
        order_clauses = [Homestay.price_per_night.desc(), Homestay.id.desc()]
    elif sort_by == "newest":
        order_clauses = [Homestay.created_at.desc(), Homestay.id.desc()]
    else:
        order_clauses = [Homestay.id.asc()]

    homestays = (
        query.options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .order_by(*order_clauses)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items = populate_homestay_promotions_and_sponsorship(db, homestays, now=now)

    return HomestayListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# OWNER / PROVIDER: List own homestays across all statuses
# IMPORTANT: This route must appear before /{homestay_id}
@router.get(
    "/mine",
    response_model=HomestayListResponse,
)
def list_my_homestays(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
):
    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if profile is not None:
        query = db.query(Homestay).filter(
            (Homestay.provider_profile_id == profile.id)
            | (
                (Homestay.provider_profile_id == None)
                & (Homestay.owner_id == current_user.id)
            )
        )
    else:
        query = db.query(Homestay).filter(
            Homestay.owner_id == current_user.id
        )

    total = query.count()

    homestays = (
        query.options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .order_by(Homestay.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = populate_homestay_promotions_and_sponsorship(db, homestays)

    return HomestayListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# PROVIDER: Homestay provider dashboard metrics
@router.get(
    "/provider/dashboard",
    response_model=HomestayProviderDashboardResponse,
    summary="Get homestay provider dashboard metrics",
)
def get_provider_dashboard(
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[
        ProviderProfile,
        Depends(require_approved_provider),
    ],
):
    """Aggregate dashboard metrics for the authenticated homestay provider."""
    # 1. Owned homestays
    homestays = (
        db.query(Homestay)
        .filter(
            (Homestay.provider_profile_id == current_provider.id)
            | (
                (Homestay.provider_profile_id == None)
                & (Homestay.owner_id == current_provider.user_id)
            )
        )
        .all()
    )

    total_listings = len(homestays)
    approved_count = sum(1 for h in homestays if h.approval_status == "approved")
    pending_count = sum(1 for h in homestays if h.approval_status == "pending")
    draft_count = sum(1 for h in homestays if h.approval_status == "draft")
    rejected_count = sum(1 for h in homestays if h.approval_status == "rejected")
    suspended_count = sum(1 for h in homestays if h.approval_status == "suspended")

    listings_summary = HomestayProviderListingSummary(
        total=total_listings,
        approved=approved_count,
        pending_approval=pending_count,
        draft=draft_count,
        rejected=rejected_count,
        suspended=suspended_count,
    )

    homestay_ids = [h.id for h in homestays]

    # 2. Bookings
    bookings = []
    if homestay_ids:
        bookings = (
            db.query(Booking)
            .filter(Booking.homestay_id.in_(homestay_ids))
            .all()
        )

    bookings_summary = HomestayProviderBookingSummary(
        total=len(bookings),
        pending=sum(1 for b in bookings if b.status == "pending"),
        confirmed=sum(1 for b in bookings if b.status == "confirmed"),
        completed=sum(1 for b in bookings if b.status == "completed"),
        cancelled=sum(1 for b in bookings if b.status == "cancelled"),
        rejected=sum(1 for b in bookings if b.status == "rejected"),
    )

    # 3. Finances
    online_earnings = Decimal("0.00")
    cash_received = Decimal("0.00")

    for b in bookings:
        if b.payment_method == "cash" and b.cash_received_amount:
            cash_received += Decimal(str(b.cash_received_amount))
        elif b.status in ("confirmed", "completed"):
            online_earnings += Decimal(str(b.total_price))

    total_earnings = online_earnings + cash_received

    # Pending settlements
    pending_settlements = (
        db.query(ProviderSettlement)
        .filter(
            ProviderSettlement.provider_profile_id == current_provider.id,
            ProviderSettlement.settlement_status == "pending",
        )
        .all()
    )
    pending_settlements_amount = sum(
        (Decimal(str(s.net_amount)) for s in pending_settlements),
        Decimal("0.00"),
    )

    financial_summary = HomestayProviderFinancialSummary(
        online_earnings=online_earnings,
        cash_payments_received=cash_received,
        total_earnings=total_earnings,
        pending_settlements_amount=pending_settlements_amount,
        pending_settlements_count=len(pending_settlements),
        currency="LKR",
    )

    # 4. Marketing (active discount offers & campaigns)
    today = date.today()
    active_discounts = 0
    if homestay_ids:
        active_discounts = (
            db.query(HomestayPromotion)
            .filter(
                HomestayPromotion.homestay_id.in_(homestay_ids),
                HomestayPromotion.is_active == True,
                HomestayPromotion.start_date <= today,
                HomestayPromotion.end_date >= today,
            )
            .count()
        )

    active_campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.provider_profile_id == current_provider.id,
            PromotionCampaign.target_module == "homestay",
            PromotionCampaign.status == "active",
        )
        .count()
    )

    marketing_summary = HomestayProviderMarketingSummary(
        active_discount_offers=active_discounts,
        active_sponsored_campaigns=active_campaigns,
    )

    # 5. Reviews & Ratings
    total_reviews = 0
    avg_rating = 0.0

    if homestay_ids:
        listing_ref_ids = [
            row[0]
            for row in db.query(ListingReference.id)
            .filter(
                ListingReference.module_type == "homestay",
                ListingReference.target_id.in_(homestay_ids),
            )
            .all()
        ]
        if listing_ref_ids:
            reviews = (
                db.query(Review)
                .filter(
                    Review.listing_reference_id.in_(listing_ref_ids),
                    Review.moderation_status == "approved",
                )
                .all()
            )
            total_reviews = len(reviews)
            if total_reviews > 0:
                avg_rating = round(sum(r.rating for r in reviews) / total_reviews, 2)

    ratings_summary = HomestayProviderRatingSummary(
        average_rating=avg_rating,
        total_reviews=total_reviews,
    )

    return HomestayProviderDashboardResponse(
        provider_id=current_provider.id,
        business_name=current_provider.business_name,
        listings=listings_summary,
        bookings=bookings_summary,
        finances=financial_summary,
        marketing=marketing_summary,
        ratings=ratings_summary,
    )


# PUBLIC: Get active homestay details
@router.get("/{homestay_id}", response_model=HomestayResponse)
def get_homestay(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    homestay = (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    items = populate_homestay_promotions_and_sponsorship(db, [homestay])
    return items[0]


# PUBLIC: Secure provider contact info for approved homestay
@router.get(
    "/{homestay_id}/contact",
    response_model=HomestayProviderContactResponse,
    summary="Get authorized business contact details for an approved homestay",
)
def get_homestay_provider_contact(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
            Homestay.approval_status == "approved",
        )
        .first()
    )
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Homestay not found or not approved",
        )

    profile = None
    if homestay.provider_profile_id is not None:
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.id == homestay.provider_profile_id)
            .first()
        )
    elif homestay.owner_id is not None:
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == homestay.owner_id)
            .first()
        )

    if not profile or not profile.phone:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Authorized provider contact details are not available for this homestay",
        )

    return HomestayProviderContactResponse(
        homestay_id=homestay.id,
        business_name=profile.business_name or homestay.name,
        phone=profile.phone,
        secondary_phone=profile.secondary_phone,
        business_district=profile.business_district or profile.district,
        business_city=profile.business_city,
        description=profile.description,
    )


# PUBLIC: Check homestay availability (including blackout dates)
@router.get("/{homestay_id}/availability")
def check_homestay_availability(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    check_in: date = Query(...),
    check_out: date = Query(...),
):
    if check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    if check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="Check-out date must be after check-in date",
        )

    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if not homestay:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    # Check for active inventory reservations:
    # Confirmed bookings, in-progress bookings, and unexpired awaiting_payment bookings.
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == homestay_id,
            Booking.check_in < check_out,
            Booking.check_out > check_in,
            or_(
                Booking.status.in_(["confirmed", "in_progress"]),
                and_(
                    Booking.status == "awaiting_payment",
                    or_(
                        Booking.payment_expires_at.is_(None),
                        Booking.payment_expires_at > now_utc,
                    ),
                ),
            ),
        )
        .first()
    )

    # Check for overlapping active blackout blocks
    overlapping_block = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.homestay_id == homestay_id,
            HomestayAvailabilityBlock.is_active == True,
            HomestayAvailabilityBlock.start_date < check_out,
            HomestayAvailabilityBlock.end_date > check_in,
        )
        .first()
    )

    is_available = (overlapping_booking is None) and (overlapping_block is None)

    return {
        "homestay_id": homestay_id,
        "check_in": check_in,
        "check_out": check_out,
        "available": is_available,
    }


# PUBLIC: Preview the final booking price
@router.get("/{homestay_id}/price-preview")
def preview_homestay_price(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    check_in: date = Query(...),
    check_out: date = Query(...),
):
    if check_in < date.today():
        raise HTTPException(
            status_code=422,
            detail="Check-in date cannot be in the past",
        )

    if check_out <= check_in:
        raise HTTPException(
            status_code=422,
            detail="Check-out date must be after check-in date",
        )

    homestay = (
        db.query(Homestay)
        .filter(
            Homestay.id == homestay_id,
            Homestay.is_active == True,
        )
        .first()
    )

    if homestay is None:
        raise HTTPException(
            status_code=404,
            detail="Homestay not found",
        )

    breakdown = calculate_detailed_booking_price(
        db=db,
        homestay_id=homestay.id,
        check_in=check_in,
        check_out=check_out,
        price_per_night=homestay.price_per_night,
    )

    return {
        "homestay_id": homestay.id,
        "check_in": check_in,
        "check_out": check_out,
        "nights": breakdown["nights"],
        "price_per_night": breakdown["price_per_night"],
        "original_price": breakdown["original_price"],
        "discount_amount": breakdown["discount_amount"],
        "final_price": breakdown["final_price"],
        "savings": breakdown["savings"],
        "discount_type": breakdown["discount_type"],
        "discount_percent": breakdown["discount_percent"],
        "discount_fixed_amount": breakdown["discount_amount"],
    }


# OWNER / PROVIDER / ADMIN: Create homestay (draft by default)
@router.post(
    "",
    response_model=HomestayResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_homestay(
    request: HomestayCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    provider_profile_id = None
    if current_user.role == "provider":
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
            if profile is not None:
                provider_profile_id = profile.id
        except Exception:
            provider_profile_id = None

    # New listings created by providers start as draft; admins create approved
    approval_status = "approved" if current_user.role == "admin" else "draft"

    homestay = Homestay(
        owner_id=current_user.id,
        provider_profile_id=provider_profile_id,
        name=request.name,
        description=request.description,
        location=request.location,
        province=request.province,
        district=request.district,
        city=request.city,
        address=request.address,
        latitude=request.latitude,
        longitude=request.longitude,
        price_per_night=request.price_per_night,
        max_guests=request.max_guests,
        check_in_time=request.check_in_time or time(14, 0),
        check_out_time=request.check_out_time or time(11, 0),
        cancellation_policy=request.cancellation_policy,
        free_cancellation_hours=(
            request.free_cancellation_hours
            if request.free_cancellation_hours is not None
            else 24
        ),
        approval_status=approval_status,
        verification_status="unverified",
        is_active=True,
    )

    homestay.images = [
        HomestayImage(
            image_url=image.image_url,
            is_primary=image.is_primary,
        )
        for image in request.images
    ]

    homestay.amenities = [
        HomestayAmenity(name=amenity)
        for amenity in request.amenities
    ]

    try:
        db.add(homestay)
        db.flush()
        if homestay.approval_status == "approved":
            sync_homestay_listing_reference(db, homestay)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(homestay)

    return (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay.id)
        .first()
    )


# OWNER / PROVIDER: Submit homestay for admin approval
@router.post(
    "/{homestay_id}/submit-approval",
    response_model=HomestayResponse,
    summary="Submit a draft or rejected homestay listing for admin review",
)
def submit_homestay_for_approval(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = _get_homestay_for_management(homestay_id, current_user, db)

    if homestay.approval_status not in ("draft", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit homestay with status '{homestay.approval_status}'",
        )

    homestay.approval_status = "pending"
    homestay.rejection_reason = None

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(homestay)
    return homestay


# OWNER / ADMIN: Update homestay listing
@router.put(
    "/{homestay_id}",
    response_model=HomestayResponse,
)
def update_homestay(
    homestay_id: int,
    request: HomestayUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = _get_homestay_for_management(homestay_id, current_user, db)

    # Automatically associate legacy homestay with provider profile upon update
    if (
        homestay.provider_profile_id is None
        and current_user.role == "provider"
    ):
        try:
            profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
            if profile is not None:
                homestay.provider_profile_id = profile.id
        except Exception:
            pass

    # Prevent deactivation if active or upcoming confirmed bookings exist
    if not request.is_active and homestay.is_active:
        active_bookings = (
            db.query(Booking)
            .filter(
                Booking.homestay_id == homestay_id,
                Booking.status == "confirmed",
                Booking.check_out >= date.today(),
            )
            .count()
        )
        if active_bookings > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot deactivate homestay with active or upcoming confirmed bookings",
            )

    before = (homestay.latitude, homestay.longitude)
    after = (request.latitude, request.longitude)
    was_approved = homestay.approval_status == "approved"
    location_changes = {
        field: getattr(request, field) for field in HOMESTAY_LOCATION_FIELDS
        if getattr(homestay, field) != getattr(request, field)
    }
    location_changed = bool(location_changes)
    if location_changed and current_user.role != "admin":
        if homestay.approval_status == "pending":
            raise HTTPException(409, detail="Location is currently under review")
        if was_approved:
            location_changes.update(approval_status="draft", reviewed_by=None, reviewed_at=None)
    if location_changed:
        write_review_snapshot(db, homestay, HOMESTAY_REVIEW_STATE, location_changes)
    if before != after and current_user.role == "admin":
        record_location_correction(
            db, current_user.id, "homestays", homestay.id, before, after,
            "Coordinates changed through the administrative homestay update",
        )

    homestay.name = request.name
    homestay.description = request.description
    homestay.price_per_night = request.price_per_night
    homestay.max_guests = request.max_guests
    homestay.is_active = request.is_active
    if request.check_in_time is not None:
        homestay.check_in_time = request.check_in_time
    if request.check_out_time is not None:
        homestay.check_out_time = request.check_out_time
    homestay.cancellation_policy = request.cancellation_policy
    if request.free_cancellation_hours is not None:
        homestay.free_cancellation_hours = request.free_cancellation_hours

    # Safe synchronization of images (preserves existing when omitted)
    if request.images is not None:
        existing_images_by_url = {
            img.image_url.strip(): img for img in homestay.images
        }
        req_images_by_url = {
            img.image_url.strip(): img for img in request.images
        }

        # Remove images no longer requested
        for img_url, img_obj in list(existing_images_by_url.items()):
            if img_url not in req_images_by_url:
                homestay.images.remove(img_obj)
                db.delete(img_obj)

        # Update or add requested images
        for img_input in request.images:
            clean_url = img_input.image_url.strip()
            if clean_url in existing_images_by_url:
                existing_images_by_url[clean_url].is_primary = (
                    img_input.is_primary
                )
            else:
                homestay.images.append(
                    HomestayImage(
                        image_url=clean_url,
                        is_primary=img_input.is_primary,
                    )
                )

    # Safe synchronization of amenities (avoids unique constraint collision)
    if request.amenities is not None:
        existing_amenities_by_lower = {
            a.name.strip().lower(): a for a in homestay.amenities
        }

        unique_req_amenities = {}
        for a_name in request.amenities:
            clean_name = a_name.strip()
            if clean_name:
                key = clean_name.lower()
                if key not in unique_req_amenities:
                    unique_req_amenities[key] = clean_name

        req_keys = set(unique_req_amenities.keys())

        # Remove amenities no longer requested
        for key, a_obj in list(existing_amenities_by_lower.items()):
            if key not in req_keys:
                homestay.amenities.remove(a_obj)
                db.delete(a_obj)

        # Insert only genuinely new amenities; preserve existing ones in-place
        for key, orig_name in unique_req_amenities.items():
            if key not in existing_amenities_by_lower:
                homestay.amenities.append(HomestayAmenity(name=orig_name))

    # Republish current data or deactivate the shared reference on re-review.
    if was_approved:
        sync_homestay_listing_reference(db, homestay)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Homestay)
        .options(
            selectinload(Homestay.images),
            selectinload(Homestay.amenities),
        )
        .filter(Homestay.id == homestay_id)
        .first()
    )


# OWNER / ADMIN: Soft delete (deactivate) homestay
@router.delete(
    "/{homestay_id}",
    summary="Soft delete (deactivate) a homestay listing",
)
def delete_homestay(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(require_homestay_manager),
    ],
):
    homestay = _get_homestay_for_management(homestay_id, current_user, db)

    # Prevent deactivation if active or upcoming confirmed bookings exist
    active_bookings = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == homestay_id,
            Booking.status == "confirmed",
            Booking.check_out >= date.today(),
        )
        .count()
    )
    if active_bookings > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate homestay with active or upcoming confirmed bookings",
        )

    homestay.is_active = False
    sync_homestay_listing_reference(db, homestay)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Homestay deactivated successfully",
        "id": homestay_id,
        "is_active": False,
    }


# ==========================================
# PHOTO MANAGEMENT ENDPOINTS
# ==========================================

@router.post(
    "/{homestay_id}/images",
    response_model=HomestayImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an image to a homestay listing",
)
def add_homestay_image(
    homestay_id: int,
    request: HomestayImageAddRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    existing_images = (
        db.query(HomestayImage)
        .filter(HomestayImage.homestay_id == homestay_id)
        .all()
    )

    clean_url = request.image_url.strip()
    if any(img.image_url == clean_url for img in existing_images):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An image with this URL already exists for this homestay",
        )

    is_primary = request.is_primary
    if not existing_images:
        is_primary = True

    if is_primary:
        for img in existing_images:
            img.is_primary = False

    new_img = HomestayImage(
        homestay_id=homestay_id,
        image_url=clean_url,
        is_primary=is_primary,
    )
    db.add(new_img)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(new_img)
    return new_img


@router.delete(
    "/{homestay_id}/images/{image_id}",
    summary="Delete an image from a homestay listing",
)
def delete_homestay_image(
    homestay_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    image = (
        db.query(HomestayImage)
        .filter(
            HomestayImage.id == image_id,
            HomestayImage.homestay_id == homestay_id,
        )
        .first()
    )
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    was_primary = image.is_primary
    db.delete(image)

    if was_primary:
        remaining_first = (
            db.query(HomestayImage)
            .filter(
                HomestayImage.homestay_id == homestay_id,
                HomestayImage.id != image_id,
            )
            .order_by(HomestayImage.id.asc())
            .first()
        )
        if remaining_first:
            remaining_first.is_primary = True

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Image deleted successfully",
        "id": image_id,
    }


@router.patch(
    "/{homestay_id}/images/{image_id}/set-primary",
    response_model=HomestayImageResponse,
    summary="Designate an image as the primary listing photo",
)
def set_primary_homestay_image(
    homestay_id: int,
    image_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    target_image = (
        db.query(HomestayImage)
        .filter(
            HomestayImage.id == image_id,
            HomestayImage.homestay_id == homestay_id,
        )
        .first()
    )
    if not target_image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    all_images = (
        db.query(HomestayImage)
        .filter(HomestayImage.homestay_id == homestay_id)
        .all()
    )
    for img in all_images:
        img.is_primary = (img.id == image_id)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(target_image)
    return target_image


@router.patch(
    "/{homestay_id}/images/reorder",
    summary="Reorder homestay images",
)
def reorder_homestay_images(
    homestay_id: int,
    request: HomestayImageReorderRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    existing_ids = {
        img.id
        for img in db.query(HomestayImage.id)
        .filter(HomestayImage.homestay_id == homestay_id)
        .all()
    }

    req_ids = [item.image_id for item in request.items]
    for img_id in req_ids:
        if img_id not in existing_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Image with id {img_id} does not belong to this homestay",
            )

    return {
        "message": "Images reordered successfully",
        "reordered_ids": req_ids,
    }


def _acquire_homestay_lock(db: Session, homestay_id: int):
    """Acquire exclusive transaction app lock on SQL Server to prevent concurrent race conditions."""
    if db.bind and db.bind.dialect.name == "mssql":
        lock_result = db.execute(
            text("""
                DECLARE @result INT;
                EXEC @result = sp_getapplock
                    @Resource = :resource,
                    @LockMode = 'Exclusive',
                    @LockOwner = 'Transaction',
                    @LockTimeout = 10000;
                SELECT @result;
            """),
            {"resource": f"homestay_booking_{homestay_id}"},
        ).scalar()
        if lock_result is None or lock_result < 0:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Booking service is busy. Please try again.",
            )


# ==========================================
# AVAILABILITY & BLACKOUT DATES ENDPOINTS
# ==========================================

@router.post(
    "/{homestay_id}/availability-blocks",
    response_model=HomestayAvailabilityBlockResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an availability blackout date block for a homestay",
)
def create_availability_block(
    homestay_id: int,
    request: HomestayAvailabilityBlockCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)
    _acquire_homestay_lock(db, homestay_id)

    # 1. Check for overlapping active blocks
    overlapping_block = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.homestay_id == homestay_id,
            HomestayAvailabilityBlock.is_active == True,
            HomestayAvailabilityBlock.start_date < request.end_date,
            HomestayAvailabilityBlock.end_date > request.start_date,
        )
        .first()
    )
    if overlapping_block:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Blackout range overlaps with an existing availability block",
        )

    # 2. Check for active inventory reservations (confirmed, in-progress, unexpired awaiting_payment)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    overlapping_booking = (
        db.query(Booking)
        .filter(
            Booking.homestay_id == homestay_id,
            Booking.check_in < request.end_date,
            Booking.check_out > request.start_date,
            or_(
                Booking.status.in_(["confirmed", "in_progress"]),
                and_(
                    Booking.status == "awaiting_payment",
                    or_(
                        Booking.payment_expires_at.is_(None),
                        Booking.payment_expires_at > now_utc,
                    ),
                ),
            ),
        )
        .first()
    )
    if overlapping_booking:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot block dates that overlap with an existing active booking reservation",
        )

    block = HomestayAvailabilityBlock(
        homestay_id=homestay_id,
        start_date=request.start_date,
        end_date=request.end_date,
        block_type=request.block_type,
        reason=request.reason,
        is_active=True,
        created_by=current_user.id,
    )
    db.add(block)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(block)
    return block


@router.get(
    "/{homestay_id}/availability-blocks",
    response_model=list[HomestayAvailabilityBlockResponse],
    summary="List active availability blackout date blocks for a homestay",
)
def list_availability_blocks(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    blocks = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.homestay_id == homestay_id,
            HomestayAvailabilityBlock.is_active == True,
        )
        .order_by(HomestayAvailabilityBlock.start_date.asc())
        .all()
    )
    return blocks


@router.delete(
    "/{homestay_id}/availability-blocks/{block_id}",
    summary="Remove an availability blackout block",
)
def delete_availability_block(
    homestay_id: int,
    block_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_homestay_manager)],
):
    _get_homestay_for_management(homestay_id, current_user, db)

    block = (
        db.query(HomestayAvailabilityBlock)
        .filter(
            HomestayAvailabilityBlock.id == block_id,
            HomestayAvailabilityBlock.homestay_id == homestay_id,
        )
        .first()
    )
    if not block:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability block not found",
        )

    block.is_active = False
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Availability block removed successfully",
        "id": block_id,
    }
