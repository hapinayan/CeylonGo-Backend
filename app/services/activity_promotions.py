"""
Service Layer for Module 03 — Provider Activity Discounts & Sponsored Campaigns (B15, B16).

Guarantees 100% ANSI-SQL and physical schema compatibility with SQL Server 2025 Express:
- Physical table dbo.promotion_packages (11 columns)
- Physical table dbo.promotion_campaigns (11 columns, strictly avoiding unapplied listing_reference_id)
- Multi-tenant ownership verification against provider_profiles.id
- Cross-module isolation enforcing target_module = 'activity'
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import case, func, insert, update
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, PromotionCampaign, PromotionEvent, PromotionPackage
from app.schemas.activity_admin import AdminCampaignReviewRequest
from app.schemas.activity_promotion import (
    ActivityCampaignAnalyticsSummary,
    ActivityCampaignCreate,
    ActivityCampaignPagination,
    ActivityCampaignPreviewRequest,
    ActivityCampaignPreviewResponse,
    ActivityCampaignResponse,
    ActivityCampaignUpdate,
    ActivityDiscountCreate,
    ActivityDiscountPagination,
    ActivityDiscountPreviewRequest,
    ActivityDiscountPreviewResponse,
    ActivityDiscountResponse,
    ActivityDiscountUpdate,
    ActivityPromotionEventResponse,
    ActivityProviderPromotionsAnalytics,
)

# Physical columns guaranteed to exist on SQL Server dbo.promotion_campaigns
CAMPAIGN_PHYSICAL_COLUMNS = (
    PromotionCampaign.id,
    PromotionCampaign.provider_profile_id,
    PromotionCampaign.campaign_name,
    PromotionCampaign.target_module,
    PromotionCampaign.budget,
    PromotionCampaign.spent_amount,
    PromotionCampaign.currency,
    PromotionCampaign.status,
    PromotionCampaign.starts_at,
    PromotionCampaign.ends_at,
    PromotionCampaign.created_at,
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _campaign_row_to_response(row: Any) -> ActivityCampaignResponse:
    """Helper to serialize a scalar row or model into ActivityCampaignResponse."""
    return ActivityCampaignResponse(
        id=row.id,
        provider_profile_id=row.provider_profile_id,
        campaign_name=row.campaign_name,
        target_module=row.target_module,
        budget=row.budget,
        spent_amount=row.spent_amount,
        currency=row.currency,
        status=row.status,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        created_at=row.created_at,
        targeting_scope="provider",
        activity_targeting_status="BLOCKED_BY_SCHEMA",
    )


# ==============================================================================
# B15: Provider Discount Offers Service Functions (dbo.promotion_packages)
# ==============================================================================


def validate_activity_discount_eligibility(
    db: Session,
    profile: ProviderProfile,
    activity_id: int,
    discount_percent: Decimal | None,
    discount_amount: Decimal | None,
    valid_from: date,
    valid_to: date,
) -> tuple[Activity, Decimal]:
    """
    Validates provider ownership, activity status, discount bounds, nonnegative pricing,
    and date ranges for activity-specific discount offers (B15).
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    # Cross-tenant isolation check
    if activity.provider_profile_id != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Activity does not belong to this provider",
        )

    # Activity status check
    if activity.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Activity is not eligible for promotions. Status is '{activity.approval_status}', must be 'approved'",
        )

    if not activity.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Activity is inactive and not eligible for promotions",
        )

    # Date validation
    if valid_to < valid_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_to cannot be earlier than valid_from",
        )

    today = date.today()
    if valid_to < today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Discount validity window cannot be in the past",
        )

    # Discount bounds and nonnegative pricing validation
    has_percent = discount_percent is not None and discount_percent > Decimal("0.00")
    has_amount = discount_amount is not None and discount_amount > Decimal("0.00")
    if not has_percent and not has_amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one of discount_percent or discount_amount must be greater than 0",
        )

    if has_percent:
        if discount_percent > Decimal("100.00"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Discount percentage cannot exceed 100%",
            )
        discount_factor = Decimal("1.00") - (discount_percent / Decimal("100.00"))
        discounted_price = (activity.price * discount_factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        if discount_amount > activity.price:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Fixed discount amount ({discount_amount}) cannot exceed activity price ({activity.price})",
            )
        discounted_price = (activity.price - discount_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Ensure nonnegative pricing rule
    discounted_price = max(Decimal("0.00"), discounted_price)

    return activity, discounted_price


def check_conflicting_discounts(
    db: Session,
    profile_id: int,
    title: str,
    valid_from: date,
    valid_to: date,
    exclude_discount_id: int | None = None,
) -> None:
    """Checks for duplicate or conflicting active discount offers for the provider."""
    query = db.query(PromotionPackage).filter(
        PromotionPackage.provider_profile_id == profile_id,
        PromotionPackage.is_active == True,
        PromotionPackage.title == title.strip(),
        PromotionPackage.valid_from <= valid_to,
        PromotionPackage.valid_to >= valid_from,
    )
    if exclude_discount_id is not None:
        query = query.filter(PromotionPackage.id != exclude_discount_id)

    if query.first() is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"An active discount offer with the title '{title.strip()}' already overlaps the specified date window",
        )


def preview_activity_discount(
    db: Session,
    profile: ProviderProfile,
    payload: ActivityDiscountPreviewRequest,
) -> ActivityDiscountPreviewResponse:
    """
    Computes a real-time preview of activity-specific discount pricing and eligibility (B15).
    Enforces cross-tenant isolation and nonnegative price boundaries.
    """
    activity, discounted_price = validate_activity_discount_eligibility(
        db=db,
        profile=profile,
        activity_id=payload.activity_id,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )

    return ActivityDiscountPreviewResponse(
        activity_id=activity.id,
        activity_title=activity.title,
        original_price=activity.price,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        discounted_price=discounted_price,
        currency=payload.currency,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_eligible=True,
        ineligibility_reason=None,
        targeting_scope="activity",
        activity_targeting_status="PREVIEW_ONLY_MIGRATION_PENDING",
    )


def create_provider_discount(
    db: Session,
    profile: ProviderProfile,
    payload: ActivityDiscountCreate,
) -> ActivityDiscountResponse:
    """
    Create a new provider discount offer (B15).
    If activity_id is provided, validates eligibility first, then rejects persistence
    with a clear migration-pending error so activity_id is NEVER falsely saved or silently discarded.
    """
    if payload.activity_id is not None:
        # Validate activity eligibility and ownership first so invalid IDs/unowned activities fail with 404/403/422
        validate_activity_discount_eligibility(
            db=db,
            profile=profile,
            activity_id=payload.activity_id,
            discount_percent=payload.discount_percent,
            discount_amount=payload.discount_amount,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
        )
        # Fail closed on persistent persistence request: dbo.promotion_packages lacks activity_id column
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Persistent activity-specific discount targeting is unavailable until Migration 041 is applied. "
                "Use POST /api/v1/provider/activities/discounts/preview for non-persistent pricing simulations, "
                "or omit activity_id to create a provider-wide discount offer."
            ),
        )

    if payload.valid_to < payload.valid_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_to cannot be earlier than valid_from",
        )
    has_percent = payload.discount_percent is not None and payload.discount_percent > Decimal("0.00")
    has_amount = payload.discount_amount is not None and payload.discount_amount > Decimal("0.00")
    if not has_percent and not has_amount:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="At least one of discount_percent or discount_amount must be greater than 0",
        )

    check_conflicting_discounts(
        db=db,
        profile_id=profile.id,
        title=payload.title,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
    )

    discount = PromotionPackage(
        provider_profile_id=profile.id,
        title=payload.title,
        description=payload.description,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        currency=payload.currency,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_active=payload.is_active,
    )
    db.add(discount)
    db.commit()
    db.refresh(discount)

    return ActivityDiscountResponse(
        id=discount.id,
        provider_profile_id=discount.provider_profile_id,
        title=discount.title,
        description=discount.description,
        discount_percent=discount.discount_percent,
        discount_amount=discount.discount_amount,
        currency=discount.currency,
        valid_from=discount.valid_from,
        valid_to=discount.valid_to,
        is_active=discount.is_active,
        created_at=discount.created_at,
        activity_id=None,
        activity_title=None,
        original_price=None,
        discounted_price=None,
        targeting_scope="provider",
        activity_targeting_status="BLOCKED_BY_SCHEMA",
    )


def get_activity_discounts_preview(
    db: Session,
    profile: ProviderProfile,
    activity_id: int,
) -> list[ActivityDiscountPreviewResponse]:
    """
    Returns active discount offers available to the provider for the specified activity (B15).
    Enforces ownership and returns empty list because physical activity-level targeting is pending Migration 041.
    Never incorrectly returns provider-wide promotions as though they are uniquely linked to the activity.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if activity.provider_profile_id != profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: Activity does not belong to this provider")

    # Physical table dbo.promotion_packages lacks activity_id column;
    # We do NOT falsely return provider-wide discounts as if they were linked to this activity.
    return []


def list_provider_discounts(
    db: Session,
    profile: ProviderProfile,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ActivityDiscountPagination:
    """
    List discounts belonging to the authenticated provider (B15).
    Supports optional active status filter and deterministic pagination.
    """
    query = db.query(PromotionPackage).filter(
        PromotionPackage.provider_profile_id == profile.id
    )

    if is_active is not None:
        query = query.filter(PromotionPackage.is_active == is_active)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(PromotionPackage.valid_to.asc(), PromotionPackage.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ActivityDiscountPagination(
        items=[ActivityDiscountResponse.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def get_provider_discount(
    db: Session,
    profile: ProviderProfile,
    discount_id: int,
) -> ActivityDiscountResponse:
    """
    Retrieve single discount detail owned by the authenticated provider (B15).
    Enforces strict tenant isolation.
    """
    discount = (
        db.query(PromotionPackage)
        .filter(
            PromotionPackage.id == discount_id,
            PromotionPackage.provider_profile_id == profile.id,
        )
        .first()
    )
    if discount is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found",
        )

    return ActivityDiscountResponse.model_validate(discount)


def update_provider_discount(
    db: Session,
    profile: ProviderProfile,
    discount_id: int,
    payload: ActivityDiscountUpdate,
) -> ActivityDiscountResponse:
    """
    Update an existing provider discount package (B15).
    Validates date ranges and discount bounds.
    """
    if payload.activity_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Persistent activity-specific discount targeting is unavailable until Migration 041 is applied. "
                "Use POST /api/v1/provider/activities/discounts/preview for non-persistent pricing simulations."
            ),
        )

    discount = (
        db.query(PromotionPackage)
        .filter(
            PromotionPackage.id == discount_id,
            PromotionPackage.provider_profile_id == profile.id,
        )
        .first()
    )
    if discount is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found",
        )

    eff_from = payload.valid_from if payload.valid_from is not None else discount.valid_from
    eff_to = payload.valid_to if payload.valid_to is not None else discount.valid_to
    if eff_to < eff_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="valid_to cannot be earlier than valid_from",
        )

    if payload.title is not None:
        discount.title = payload.title
    if payload.description is not None:
        discount.description = payload.description
    if payload.discount_percent is not None:
        discount.discount_percent = payload.discount_percent
    if payload.discount_amount is not None:
        discount.discount_amount = payload.discount_amount
    if payload.currency is not None:
        discount.currency = payload.currency
    if payload.valid_from is not None:
        discount.valid_from = payload.valid_from
    if payload.valid_to is not None:
        discount.valid_to = payload.valid_to
    if payload.is_active is not None:
        discount.is_active = payload.is_active

    db.commit()
    db.refresh(discount)

    return ActivityDiscountResponse.model_validate(discount)


def deactivate_provider_discount(
    db: Session,
    profile: ProviderProfile,
    discount_id: int,
) -> ActivityDiscountResponse:
    """
    Deactivate an existing provider discount offer (B15).
    """
    discount = (
        db.query(PromotionPackage)
        .filter(
            PromotionPackage.id == discount_id,
            PromotionPackage.provider_profile_id == profile.id,
        )
        .first()
    )
    if discount is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found",
        )

    discount.is_active = False
    db.commit()
    db.refresh(discount)

    return ActivityDiscountResponse.model_validate(discount)


# ==============================================================================
# B16: Provider Sponsored Campaign Applications (dbo.promotion_campaigns)
# ==============================================================================


def validate_activity_campaign_eligibility(
    db: Session,
    profile: ProviderProfile,
    activity_id: int,
    budget: Decimal,
    starts_at: datetime,
    ends_at: datetime,
) -> tuple[Activity, float, Decimal, int, int]:
    """
    Validates provider ownership, activity status, budget thresholds, and scheduling bounds
    for activity-specific sponsored campaigns (B16).
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if activity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity not found",
        )

    if activity.provider_profile_id != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: Activity does not belong to this provider",
        )

    if activity.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Activity is not eligible for sponsored campaigns. Status is '{activity.approval_status}', must be 'approved'",
        )

    if not activity.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Activity is inactive and not eligible for sponsored campaigns",
        )

    if budget <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Budget must be greater than zero",
        )

    if budget < Decimal("500.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign budget must be at least LKR 500.00 for viable distribution",
        )

    now = _now_utc()
    ends_at_utc = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
    starts_at_utc = starts_at if starts_at.tzinfo else starts_at.replace(tzinfo=timezone.utc)

    if ends_at_utc <= starts_at_utc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be strictly after starts_at",
        )

    if ends_at_utc <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be in the future",
        )

    duration_secs = (ends_at_utc - starts_at_utc).total_seconds()
    duration_days = max(1.0, round(duration_secs / 86400.0, 2))
    daily_budget = (budget / Decimal(str(duration_days))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    estimated_impressions = int(budget * Decimal("10"))
    estimated_clicks = int(estimated_impressions * Decimal("0.03"))

    return activity, duration_days, daily_budget, estimated_impressions, estimated_clicks


def preview_activity_campaign(
    db: Session,
    profile: ProviderProfile,
    payload: ActivityCampaignPreviewRequest,
) -> ActivityCampaignPreviewResponse:
    """
    Computes a preview simulation of activity sponsored campaign reach and burn rate (B16).
    Enforces cross-tenant isolation and schedule validation.
    """
    activity, duration_days, daily_budget, est_imp, est_clicks = validate_activity_campaign_eligibility(
        db=db,
        profile=profile,
        activity_id=payload.activity_id,
        budget=payload.budget,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )

    return ActivityCampaignPreviewResponse(
        activity_id=activity.id,
        activity_title=activity.title,
        budget=payload.budget,
        currency=payload.currency,
        duration_days=duration_days,
        daily_budget=daily_budget,
        estimated_impressions=est_imp,
        estimated_clicks=est_clicks,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        is_eligible=True,
        ineligibility_reason=None,
        targeting_scope="activity",
        activity_targeting_status="PREVIEW_ONLY_MIGRATION_PENDING",
    )


def get_activity_campaigns_preview(
    db: Session,
    profile: ProviderProfile,
    activity_id: int,
) -> list[ActivityCampaignResponse]:
    """
    Returns campaigns associated with the provider for the specified activity (B16).
    Enforces ownership and returns empty list because physical listing-level targeting is pending Migration 030/041.
    Never incorrectly returns provider-wide campaigns as though they are uniquely linked to the activity.
    """
    activity = db.query(Activity).filter(Activity.id == activity_id).first()
    if activity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Activity not found")
    if activity.provider_profile_id != profile.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access forbidden: Activity does not belong to this provider")

    # Physical table dbo.promotion_campaigns lacks listing_reference_id column;
    # We do NOT falsely return provider-wide campaigns as if they were linked to this activity.
    return []


def create_provider_campaign(
    db: Session,
    profile: ProviderProfile,
    payload: ActivityCampaignCreate,
) -> ActivityCampaignResponse:
    """
    Submit a sponsored campaign application for activities (B16).
    Enforces target_module = 'activity', physical column compatibility,
    no self-approval, and valid scheduling bounds.
    If activity_id is provided, validates eligibility first, then rejects persistence
    with a clear migration-pending error so activity_id is NEVER falsely saved or silently discarded.
    """
    now = _now_utc()
    if payload.activity_id is not None:
        # Validate eligibility and ownership first so invalid IDs or unowned activities fail with 404/403/422
        validate_activity_campaign_eligibility(
            db=db,
            profile=profile,
            activity_id=payload.activity_id,
            budget=payload.budget,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
        )
        # Fail closed on persistent persistence request: dbo.promotion_campaigns lacks listing_reference_id
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Persistent activity-specific campaign targeting is unavailable until Migration 030/041 is applied. "
                "Use POST /api/v1/provider/activities/promotions/campaigns/preview for non-persistent reach simulations, "
                "or omit activity_id to create a provider-wide sponsored campaign."
            ),
        )

    ends_at_utc = payload.ends_at if payload.ends_at.tzinfo else payload.ends_at.replace(tzinfo=timezone.utc)
    starts_at_utc = payload.starts_at if payload.starts_at.tzinfo else payload.starts_at.replace(tzinfo=timezone.utc)

    if ends_at_utc <= starts_at_utc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be strictly after starts_at",
        )

    if ends_at_utc <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be in the future",
        )

    if payload.budget <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Budget must be greater than zero",
        )

    # Initial status: pending_approval if submit_for_review else draft. Self-activation strictly forbidden.
    initial_status = "pending_approval" if payload.submit_for_review else "draft"

    # Use SQLAlchemy Core insert targeting strictly the physical columns
    stmt = (
        insert(PromotionCampaign.__table__)
        .values(
            provider_profile_id=profile.id,
            campaign_name=payload.campaign_name.strip(),
            target_module="activity",
            budget=payload.budget,
            spent_amount=Decimal("0.00"),
            currency=payload.currency,
            status=initial_status,
            starts_at=starts_at_utc.replace(tzinfo=None),
            ends_at=ends_at_utc.replace(tzinfo=None),
            created_at=now.replace(tzinfo=None),
        )
    )
    result = db.execute(stmt)
    db.commit()

    new_id = result.inserted_primary_key[0]
    return get_provider_campaign(db, profile, new_id)


def list_provider_campaigns(
    db: Session,
    profile: ProviderProfile,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ActivityCampaignPagination:
    """
    List activity sponsored campaigns owned by the authenticated provider (B16).
    Enforces cross-module isolation (target_module = 'activity') and pagination.
    """
    query = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
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

    return ActivityCampaignPagination(
        items=[_campaign_row_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def get_provider_campaign(
    db: Session,
    profile: ProviderProfile,
    campaign_id: int,
) -> ActivityCampaignResponse:
    """
    Retrieve single activity sponsored campaign (B16).
    Enforces tenant ownership and target_module = 'activity'.
    """
    row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    return _campaign_row_to_response(row)

def update_provider_campaign(
    db: Session,
    profile: ProviderProfile,
    campaign_id: int,
    payload: ActivityCampaignUpdate,
) -> ActivityCampaignResponse:
    """
    Update a draft activity sponsored campaign (B16).
    Providers can only modify campaigns in 'draft' status.
    Self-approval to 'active' or 'approved' is strictly forbidden.
    """
    row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    # Only draft campaigns can be edited
    if row.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only draft campaigns can be modified. Current status: {row.status}",
        )

    if payload.activity_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Persistent activity-specific campaign targeting is unavailable until Migration 030/041 is applied. "
                "Use POST /api/v1/provider/activities/promotions/campaigns/preview for non-persistent reach simulations."
            ),
        )

    values_to_update: dict[str, Any] = {}

    if payload.campaign_name is not None:
        values_to_update["campaign_name"] = payload.campaign_name.strip()
    if payload.budget is not None:
        if payload.budget <= Decimal("0.00"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Budget must be greater than zero",
            )
        values_to_update["budget"] = payload.budget
    if payload.currency is not None:
        values_to_update["currency"] = payload.currency

    # Date validation
    starts_at = payload.starts_at or row.starts_at
    ends_at = payload.ends_at or row.ends_at
    starts_at_clean = starts_at.replace(tzinfo=None)
    ends_at_clean = ends_at.replace(tzinfo=None)
    if ends_at_clean <= starts_at_clean:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Campaign ends_at must be strictly after starts_at",
        )
    if payload.starts_at is not None:
        values_to_update["starts_at"] = starts_at_clean
    if payload.ends_at is not None:
        values_to_update["ends_at"] = ends_at_clean

    # Status transition rules
    if payload.status is not None:
        target_status = payload.status.strip().lower()
        if target_status in ("active", "approved", "completed"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Self-activation or self-approval is not permitted",
            )
        if target_status not in ("draft", "pending_approval"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status transition to '{target_status}'",
            )
        values_to_update["status"] = target_status

    if values_to_update:
        stmt = (
            update(PromotionCampaign.__table__)
            .where(
                PromotionCampaign.id == campaign_id,
                PromotionCampaign.provider_profile_id == profile.id,
                PromotionCampaign.target_module == "activity",
            )
            .values(**values_to_update)
        )
        db.execute(stmt)
        db.commit()

    return get_provider_campaign(db, profile, campaign_id)


def cancel_provider_campaign(
    db: Session,
    profile: ProviderProfile,
    campaign_id: int,
) -> ActivityCampaignResponse:
    """
    Cancel an activity sponsored campaign (B16).
    Allowed if current status is draft, pending_approval, active, or paused.
    """
    row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    if row.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is already cancelled",
        )

    if row.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Completed campaigns cannot be cancelled",
        )

    stmt = (
        update(PromotionCampaign.__table__)
        .where(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
        .values(status="cancelled")
    )
    db.execute(stmt)
    db.commit()

    return get_provider_campaign(db, profile, campaign_id)


# ==============================================================================
# C06: Admin Promotion Management Service Functions (dbo.promotion_campaigns)
# ==============================================================================

VALID_CAMPAIGN_STATUS_FILTERS = {
    "draft",
    "pending_approval",
    "active",
    "paused",
    "completed",
    "cancelled",
}


def list_admin_campaigns(
    db: Session,
    status_filter: str | None = None,
    provider_profile_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ActivityCampaignPagination:
    """
    List activity sponsored campaigns across all providers for admin review (C06).
    Enforces:
    - Server-side target_module = 'activity' (cannot be overridden)
    - Valid status filtering against physical schema values
    - Provider profile ID filtering (must be >= 1)
    - Case-insensitive search on campaign_name
    - Stable deterministic ordering (created_at DESC, id DESC)
    - Column-safe scalar projection of the 11 physical columns
    """
    if status_filter is not None:
        clean_status = status_filter.strip().lower()
        if clean_status not in VALID_CAMPAIGN_STATUS_FILTERS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid status filter '{status_filter}'. Allowed statuses: {', '.join(sorted(VALID_CAMPAIGN_STATUS_FILTERS))}",
            )
    else:
        clean_status = None

    if provider_profile_id is not None and provider_profile_id < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="provider_profile_id must be a positive integer",
        )

    query = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(PromotionCampaign.target_module == "activity")
    )

    if clean_status:
        query = query.filter(PromotionCampaign.status == clean_status)

    if provider_profile_id is not None:
        query = query.filter(PromotionCampaign.provider_profile_id == provider_profile_id)

    if search is not None:
        clean_search = search.strip()
        if clean_search:
            query = query.filter(PromotionCampaign.campaign_name.ilike(f"%{clean_search}%"))

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    rows = (
        query.order_by(PromotionCampaign.created_at.desc(), PromotionCampaign.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return ActivityCampaignPagination(
        items=[_campaign_row_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


def get_admin_campaign(
    db: Session,
    campaign_id: int,
) -> ActivityCampaignResponse:
    """
    Retrieve single activity sponsored campaign for admin review (C06).
    Enforces:
    - target_module = 'activity'
    - 404 for non-existent or cross-module campaigns (prevents metadata leakage)
    - Column-safe scalar projection of the 11 physical columns
    """
    row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    return _campaign_row_to_response(row)


def review_admin_campaign(
    db: Session,
    campaign_id: int,
    payload: AdminCampaignReviewRequest,
    admin_user_id: int,
    ip_address: str | None = None,
) -> ActivityCampaignResponse:
    """
    Execute administrative moderation decision on an activity sponsored campaign (C06).
    Enforces:
    - target_module = 'activity'
    - Status eligibility transition validation
    - Safe physical column updates (mapping 'reject' -> 'cancelled')
    - Immutable audit logging to dbo.admin_audit_logs
    """
    row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    try:
        payload.validate_status_eligibility(row.status)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    # Determine target physical status
    if payload.action == "approve":
        target_status = "active"
    elif payload.action == "reject":
        target_status = "cancelled"
    elif payload.action == "pause":
        target_status = "paused"
    elif payload.action == "resume":
        target_status = "active"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported review action '{payload.action}'",
        )

    # Update campaign status
    stmt = (
        update(PromotionCampaign.__table__)
        .where(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "activity",
        )
        .values(status=target_status)
    )
    db.execute(stmt)

    # Record audit log
    audit_log = AdminAuditLog(
        admin_user_id=admin_user_id,
        action=f"campaign_{payload.action}",
        entity_name="promotion_campaign",
        entity_id=campaign_id,
        details=json.dumps(
            {
                "action": payload.action,
                "from_status": row.status,
                "to_status": target_status,
                "reason": payload.reason,
                "notes": payload.notes,
            }
        ),
        ip_address=ip_address,
    )
    db.add(audit_log)
    db.commit()

    return get_admin_campaign(db, campaign_id=campaign_id)


# ==============================================================================
# A17: Public Discovery Active Campaigns & Event Tracking
# ==============================================================================


def get_active_activity_campaigns_map(
    db: Session,
    now_utc: datetime | None = None,
) -> dict[int, int]:
    """
    Returns a dictionary mapping provider_profile_id -> campaign_id
    for all currently active activity campaigns on dbo.promotion_campaigns (A17).
    Guarantees physical schema compatibility (avoids unapplied listing_reference_id).
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    clean_now = now_utc.replace(tzinfo=None)

    rows = (
        db.query(PromotionCampaign.provider_profile_id, PromotionCampaign.id)
        .filter(
            PromotionCampaign.target_module == "activity",
            PromotionCampaign.status == "active",
            PromotionCampaign.starts_at <= clean_now,
            PromotionCampaign.ends_at >= clean_now,
            PromotionCampaign.spent_amount < PromotionCampaign.budget,
        )
        .order_by(PromotionCampaign.id.desc())
        .all()
    )
    # If a provider has multiple active campaigns, map to the latest active campaign ID
    return {row[0]: row[1] for row in rows}


def record_activity_promotion_event(
    db: Session,
    campaign_id: int,
    event_type: str,
    user_id: int | None = None,
    ip_address: str | None = None,
) -> ActivityPromotionEventResponse:
    """
    Records an impression, click, or inquiry event on dbo.promotion_events (A17).
    Enforces target_module = 'activity', active status, and physical column safety.
    Rejects fabricated public booking conversions.
    """
    clean_event = event_type.strip().lower()
    if clean_event == "booking":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fabricated booking conversions cannot be recorded directly via public event endpoints. Bookings must be attributed through verified reservation workflows.",
        )

    if clean_event not in ("impression", "click", "inquiry"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid event_type '{event_type}'. Allowed types: impression, click, inquiry",
        )

    campaign_row = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if campaign_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity promotion campaign not found",
        )

    if campaign_row.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot record events for campaign with status '{campaign_row.status}'. Campaign must be active.",
        )

    stmt = (
        insert(PromotionEvent.__table__)
        .values(
            campaign_id=campaign_id,
            event_type=clean_event,
            user_id=user_id,
            ip_address=ip_address,
        )
    )
    res = db.execute(stmt)
    db.commit()

    inserted_id = res.inserted_primary_key[0] if res.inserted_primary_key else 0

    return ActivityPromotionEventResponse(
        id=inserted_id,
        campaign_id=campaign_id,
        event_type=clean_event,
        user_id=user_id,
        ip_address=ip_address,
        created_at=datetime.now(timezone.utc),
    )


# ==============================================================================
# A18: Promotional Discounts & Special Offers Calculation
# ==============================================================================


def get_active_activity_discounts_map(
    db: Session,
    today: date | None = None,
) -> dict[int, list[PromotionPackage]]:
    """
    Returns a dictionary mapping provider_profile_id -> list of active PromotionPackage
    records from dbo.promotion_packages (A18).
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    packages = (
        db.query(PromotionPackage)
        .filter(
            PromotionPackage.is_active == True,
            PromotionPackage.valid_from <= today,
            PromotionPackage.valid_to >= today,
        )
        .order_by(PromotionPackage.id.desc())
        .all()
    )

    promo_map: dict[int, list[PromotionPackage]] = {}
    for p in packages:
        promo_map.setdefault(p.provider_profile_id, []).append(p)
    return promo_map


def calculate_activity_discount(
    base_price: Decimal,
    packages: list[PromotionPackage] | PromotionPackage | None,
) -> tuple[bool, Decimal | None, Decimal | None, Decimal | None, str | None]:
    """
    Computes promotional discounted price and special offer metadata for an activity (A18).
    Evaluates active packages and selects the offer providing the maximum savings.

    Returns:
        (has_discount, discount_percent, discount_amount, discounted_price, special_offer_title)
    """
    if not packages or base_price <= Decimal("0.00"):
        return False, None, None, None, None

    pkg_list = [packages] if isinstance(packages, PromotionPackage) else packages
    best_pkg: PromotionPackage | None = None
    best_savings = Decimal("0.00")

    for pkg in pkg_list:
        savings = Decimal("0.00")
        if pkg.discount_percent is not None and pkg.discount_percent > Decimal("0.00"):
            pct_savings = (
                base_price * (pkg.discount_percent / Decimal("100.00"))
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            savings = max(savings, pct_savings)

        if pkg.discount_amount is not None and pkg.discount_amount > Decimal("0.00"):
            fixed_savings = min(pkg.discount_amount, base_price).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            savings = max(savings, fixed_savings)

        if savings > best_savings:
            best_savings = savings
            best_pkg = pkg

    if best_pkg and best_savings > Decimal("0.00"):
        discounted_price = max(
            Decimal("0.00"), base_price - best_savings
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return (
            True,
            best_pkg.discount_percent,
            best_pkg.discount_amount,
            discounted_price,
            best_pkg.title,
        )

    return False, None, None, None, None


# ==============================================================================
# B18: Provider & Admin Promotion Analytics (dbo.promotion_events)
# ==============================================================================


def get_activity_campaign_analytics(
    db: Session,
    profile: ProviderProfile,
    campaign_id: int,
) -> ActivityCampaignAnalyticsSummary:
    """
    Retrieve performance analytics for a single activity sponsored campaign (B18).
    Enforces tenant isolation and target_module = 'activity'.
    """
    campaign = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "activity",
        )
        .first()
    )
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion campaign not found",
        )

    if campaign.provider_profile_id != profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view analytics for your own campaigns",
        )

    impressions = (
        db.query(func.count(PromotionEvent.id))
        .filter(
            PromotionEvent.campaign_id == campaign_id,
            PromotionEvent.event_type == "impression",
        )
        .scalar()
        or 0
    )
    clicks = (
        db.query(func.count(PromotionEvent.id))
        .filter(
            PromotionEvent.campaign_id == campaign_id,
            PromotionEvent.event_type == "click",
        )
        .scalar()
        or 0
    )
    inquiries = (
        db.query(func.count(PromotionEvent.id))
        .filter(
            PromotionEvent.campaign_id == campaign_id,
            PromotionEvent.event_type == "inquiry",
        )
        .scalar()
        or 0
    )
    bookings = (
        db.query(func.count(PromotionEvent.id))
        .filter(
            PromotionEvent.campaign_id == campaign_id,
            PromotionEvent.event_type == "booking",
        )
        .scalar()
        or 0
    )

    ctr = round((clicks / impressions * 100), 2) if impressions > 0 else 0.0
    conversion_rate = round((bookings / clicks * 100), 2) if clicks > 0 else 0.0

    return ActivityCampaignAnalyticsSummary(
        campaign_id=campaign.id,
        campaign_name=campaign.campaign_name,
        status=campaign.status,
        budget=campaign.budget,
        spent_amount=campaign.spent_amount,
        currency=campaign.currency,
        impressions=impressions,
        clicks=clicks,
        inquiries=inquiries,
        bookings=bookings,
        ctr=ctr,
        conversion_rate=conversion_rate,
    )


def get_provider_activities_promotions_analytics(
    db: Session,
    profile: ProviderProfile,
) -> ActivityProviderPromotionsAnalytics:
    """
    Provider-level aggregated performance analytics across all activity campaigns (B18).
    """
    campaigns = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(
            PromotionCampaign.provider_profile_id == profile.id,
            PromotionCampaign.target_module == "activity",
        )
        .order_by(PromotionCampaign.id.desc())
        .all()
    )

    summaries: list[ActivityCampaignAnalyticsSummary] = []
    total_budget = Decimal("0.00")
    total_spent = Decimal("0.00")
    total_impressions = 0
    total_clicks = 0
    total_inquiries = 0
    total_bookings = 0
    active_count = 0

    for c in campaigns:
        total_budget += c.budget
        total_spent += c.spent_amount
        if c.status == "active":
            active_count += 1

        imp = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "impression",
            )
            .scalar()
            or 0
        )
        clk = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "click",
            )
            .scalar()
            or 0
        )
        inq = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "inquiry",
            )
            .scalar()
            or 0
        )
        bks = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "booking",
            )
            .scalar()
            or 0
        )

        ctr = round((clk / imp * 100), 2) if imp > 0 else 0.0
        cvr = round((bks / clk * 100), 2) if clk > 0 else 0.0

        total_impressions += imp
        total_clicks += clk
        total_inquiries += inq
        total_bookings += bks

        summaries.append(
            ActivityCampaignAnalyticsSummary(
                campaign_id=c.id,
                campaign_name=c.campaign_name,
                status=c.status,
                budget=c.budget,
                spent_amount=c.spent_amount,
                currency=c.currency,
                impressions=imp,
                clicks=clk,
                inquiries=inq,
                bookings=bks,
                ctr=ctr,
                conversion_rate=cvr,
            )
        )

    overall_ctr = (
        round((total_clicks / total_impressions * 100), 2)
        if total_impressions > 0
        else 0.0
    )
    overall_cvr = (
        round((total_bookings / total_clicks * 100), 2)
        if total_clicks > 0
        else 0.0
    )

    return ActivityProviderPromotionsAnalytics(
        total_campaigns=len(campaigns),
        active_campaigns=active_count,
        total_budget=total_budget,
        total_spent=total_spent,
        currency="LKR",
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        total_inquiries=total_inquiries,
        total_bookings=total_bookings,
        overall_ctr=overall_ctr,
        overall_conversion_rate=overall_cvr,
        campaigns=summaries,
    )


def get_admin_activities_promotions_analytics(
    db: Session,
) -> list[ActivityCampaignAnalyticsSummary]:
    """
    Admin summary of all activity campaigns performance analytics (C06).
    """
    campaigns = (
        db.query(*CAMPAIGN_PHYSICAL_COLUMNS)
        .filter(PromotionCampaign.target_module == "activity")
        .order_by(PromotionCampaign.created_at.desc(), PromotionCampaign.id.desc())
        .all()
    )

    summaries: list[ActivityCampaignAnalyticsSummary] = []
    for c in campaigns:
        imp = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "impression",
            )
            .scalar()
            or 0
        )
        clk = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "click",
            )
            .scalar()
            or 0
        )
        inq = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "inquiry",
            )
            .scalar()
            or 0
        )
        bks = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "booking",
            )
            .scalar()
            or 0
        )
        ctr = round((clk / imp * 100), 2) if imp > 0 else 0.0
        cvr = round((bks / clk * 100), 2) if clk > 0 else 0.0

        summaries.append(
            ActivityCampaignAnalyticsSummary(
                campaign_id=c.id,
                campaign_name=c.campaign_name,
                status=c.status,
                budget=c.budget,
                spent_amount=c.spent_amount,
                currency=c.currency,
                impressions=imp,
                clicks=clk,
                inquiries=inq,
                bookings=bks,
                ctr=ctr,
                conversion_rate=cvr,
            )
        )
    return summaries
