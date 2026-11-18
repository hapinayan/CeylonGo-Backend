from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_approved_provider
from app.core.payhere import PayHereService
from app.database.connection import get_db
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    ListingReference,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.promotion_campaign import (
    CampaignPaymentInitiateResponse,
    PromotionAnalyticsSummary,
    PromotionCampaignCreate,
    PromotionCampaignResponse,
    PromotionEventCreate,
    PromotionEventResponse,
    PromotionPackageCreate,
    PromotionPackageResponse,
    PromotionPackageUpdate,
)

router = APIRouter(tags=["Homestay Sponsored Campaigns"])


# ==============================================================================
# 1. PROMOTION PACKAGES
# ==============================================================================

@router.get(
    "/api/v1/homestays/promotions/packages",
    response_model=list[PromotionPackageResponse],
    summary="List active promotion packages",
)
def list_active_packages(
    db: Annotated[Session, Depends(get_db)],
):
    """Retrieve active promotion packages available for homestays."""
    packages = (
        db.query(PromotionPackage)
        .filter(PromotionPackage.is_active == True)
        .order_by(PromotionPackage.created_at.desc())
        .all()
    )
    return packages


@router.post(
    "/api/v1/admin/homestays/promotions/packages",
    response_model=PromotionPackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin create promotion package",
)
def admin_create_package(
    payload: PromotionPackageCreate,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
    provider_profile_id: Annotated[int | None, Query(description="Optional provider profile ID")] = None,
):
    """Admin creates a promotion package."""
    if payload.valid_to <= payload.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_to must be after valid_from",
        )

    # Resolve a valid provider_profile_id (required by DB FK constraint)
    target_provider_id = provider_profile_id
    if target_provider_id is None:
        first_provider = db.query(ProviderProfile).first()
        if not first_provider:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A valid provider profile is required to attach the promotion package",
            )
        target_provider_id = first_provider.id

    package = PromotionPackage(
        provider_profile_id=target_provider_id,
        title=payload.title,
        description=payload.description,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        currency=payload.currency,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_active=payload.is_active,
    )
    db.add(package)
    db.commit()
    db.refresh(package)
    return package


@router.get(
    "/api/v1/admin/homestays/promotions/packages",
    response_model=list[PromotionPackageResponse],
    summary="Admin list all promotion packages",
)
def admin_list_all_packages(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """Admin retrieves all promotion packages."""
    return db.query(PromotionPackage).order_by(PromotionPackage.id.desc()).all()


@router.patch(
    "/api/v1/admin/homestays/promotions/packages/{package_id}",
    response_model=PromotionPackageResponse,
    summary="Admin update or toggle promotion package",
)
def admin_update_package(
    package_id: int,
    payload: PromotionPackageUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """Admin updates promotion package fields or activation status."""
    package = db.query(PromotionPackage).filter(PromotionPackage.id == package_id).first()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Promotion package {package_id} not found",
        )

    if payload.title is not None:
        package.title = payload.title
    if payload.description is not None:
        package.description = payload.description
    if payload.discount_percent is not None:
        package.discount_percent = payload.discount_percent
    if payload.discount_amount is not None:
        package.discount_amount = payload.discount_amount
    if payload.currency is not None:
        package.currency = payload.currency
    if payload.valid_from is not None:
        package.valid_from = payload.valid_from
    if payload.valid_to is not None:
        package.valid_to = payload.valid_to
    if payload.is_active is not None:
        package.is_active = payload.is_active

    if package.valid_to <= package.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_to must be after valid_from",
        )

    db.commit()
    db.refresh(package)
    return package


# ==============================================================================
# 2. SPONSORED PROMOTION CAMPAIGNS
# ==============================================================================

@router.post(
    "/api/v1/homestays/{homestay_id}/campaigns",
    response_model=PromotionCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provider create sponsored campaign",
)
def create_homestay_campaign(
    homestay_id: int,
    payload: PromotionCampaignCreate,
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """Create a sponsored promotion campaign for an approved, owned homestay."""
    homestay = db.query(Homestay).filter(Homestay.id == homestay_id).first()
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Homestay {homestay_id} not found",
        )

    # Ownership check
    if (
        homestay.provider_profile_id != current_provider.id
        and homestay.owner_id != current_provider.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only create sponsored campaigns for your own homestays",
        )

    # Approval check
    if homestay.approval_status != "approved" or not homestay.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only approved and active homestays are eligible for sponsored promotions",
        )

    if payload.ends_at <= payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ends_at must be strictly after starts_at",
        )

    campaign = PromotionCampaign(
        provider_profile_id=current_provider.id,
        campaign_name=payload.campaign_name,
        target_module="homestay",
        budget=payload.budget,
        spent_amount=Decimal("0.00"),
        currency=payload.currency,
        status="pending_approval",
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return campaign


@router.get(
    "/api/v1/homestays/campaigns/mine",
    response_model=list[PromotionCampaignResponse],
    summary="Provider list own sponsored campaigns",
)
def list_my_campaigns(
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """List all campaigns owned by current provider for homestays."""
    campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.provider_profile_id == current_provider.id,
            PromotionCampaign.target_module == "homestay",
        )
        .order_by(PromotionCampaign.created_at.desc())
        .all()
    )

    results = []
    for c in campaigns:
        # Count impressions & clicks
        imp_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "impression",
            )
            .scalar()
            or 0
        )
        clk_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "click",
            )
            .scalar()
            or 0
        )
        bk_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "booking",
            )
            .scalar()
            or 0
        )

        resp = PromotionCampaignResponse.model_validate(c)
        resp.impressions_count = imp_count
        resp.clicks_count = clk_count
        resp.bookings_count = bk_count
        results.append(resp)

    return results


@router.post(
    "/api/v1/homestays/campaigns/{campaign_id}/initiate-payment",
    response_model=CampaignPaymentInitiateResponse,
    summary="Initiate PayHere payment for sponsored campaign",
)
def initiate_campaign_payment(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """
    Advertising payment initiation is disabled until reliable campaign payment persistence is available.
    dbo.payment_transactions strictly enforces a foreign key to dbo.booking_references, which cannot
    be safely created for advertising transactions without database schema modification.
    """
    campaign = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    if campaign.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate payment for your own campaigns",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Advertising payment initiation is disabled until reliable advertising transaction persistence is available in the database schema.",
    )


@router.post(
    "/api/v1/homestays/campaigns/{campaign_id}/verify-payment",
    response_model=PromotionCampaignResponse,
    summary="Verify payment and update campaign status",
)
def verify_campaign_payment(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """
    Campaign payment verification is currently deferred.
    Direct campaign payment activation is disabled because dbo.payment_transactions
    strictly requires a booking_reference_id foreign key, which cannot be safely
    created for advertising payments without database schema alterations.
    """
    campaign = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    if campaign.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only verify payment for your own campaigns",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Direct client-side campaign payment activation is disabled. Paid campaign activation is deferred until reliable advertising transaction persistence and IPN gateway verification are supported.",
    )


@router.get(
    "/api/v1/admin/homestays/campaigns",
    response_model=list[PromotionCampaignResponse],
    summary="Admin list all sponsored campaigns",
)
def admin_list_campaigns(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
    status_filter: Annotated[str | None, Query(description="Filter by status")] = None,
):
    """Admin retrieves all sponsored campaigns across homestays."""
    query = db.query(PromotionCampaign).filter(PromotionCampaign.target_module == "homestay")
    if status_filter:
        query = query.filter(PromotionCampaign.status == status_filter)
    campaigns = query.order_by(PromotionCampaign.created_at.desc()).all()
    return campaigns


@router.post(
    "/api/v1/admin/homestays/campaigns/{campaign_id}/approve",
    response_model=PromotionCampaignResponse,
    summary="Admin approve sponsored campaign",
)
def admin_approve_campaign(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """Admin approves a sponsored campaign."""
    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "homestay",
        )
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if campaign.ends_at < now:
        campaign.status = "completed"
    else:
        campaign.status = "active"

    db.commit()
    db.refresh(campaign)
    return campaign


@router.post(
    "/api/v1/admin/homestays/campaigns/{campaign_id}/reject",
    response_model=PromotionCampaignResponse,
    summary="Admin reject sponsored campaign",
)
def admin_reject_campaign(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """Admin rejects a sponsored campaign."""
    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.id == campaign_id,
            PromotionCampaign.target_module == "homestay",
        )
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    campaign.status = "cancelled"
    db.commit()
    db.refresh(campaign)
    return campaign


# ==============================================================================
# 3. PROMOTION ANALYTICS & EVENTS
# ==============================================================================

@router.post(
    "/api/v1/homestays/campaigns/{campaign_id}/events",
    response_model=PromotionEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log promotion event (impression, click, booking, inquiry)",
)
def log_promotion_event(
    campaign_id: int,
    payload: PromotionEventCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User | None, Depends(get_current_user)] = None,
):
    """Log an interaction event for a campaign."""
    campaign = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    if payload.event_type == "booking":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fabricated booking conversions cannot be recorded directly via public event endpoints. Bookings must be attributed through verified reservation workflows.",
        )

    client_ip = request.client.host if request.client else None
    user_id = current_user.id if current_user else None

    event = PromotionEvent(
        campaign_id=campaign.id,
        event_type=payload.event_type,
        user_id=user_id,
        ip_address=client_ip,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@router.get(
    "/api/v1/homestays/campaigns/{campaign_id}/analytics",
    response_model=PromotionAnalyticsSummary,
    summary="Provider campaign performance analytics",
)
def get_campaign_analytics(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """Retrieve campaign performance analytics."""
    campaign = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    if campaign.provider_profile_id != current_provider.id:
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

    return PromotionAnalyticsSummary(
        campaign_id=campaign.id,
        campaign_name=campaign.campaign_name,
        status=campaign.status,
        budget=campaign.budget,
        spent_amount=campaign.spent_amount,
        currency=campaign.currency,
        impressions=impressions,
        clicks=clicks,
        bookings=bookings,
        ctr=ctr,
        conversion_rate=conversion_rate,
    )


@router.get(
    "/api/v1/admin/homestays/campaigns/analytics",
    response_model=list[PromotionAnalyticsSummary],
    summary="Admin homestay promotion analytics summary",
)
def admin_get_promotions_analytics(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """Admin summary of all homestay campaigns analytics."""
    campaigns = (
        db.query(PromotionCampaign)
        .filter(PromotionCampaign.target_module == "homestay")
        .all()
    )

    summaries = []
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
            PromotionAnalyticsSummary(
                campaign_id=c.id,
                campaign_name=c.campaign_name,
                status=c.status,
                budget=c.budget,
                spent_amount=c.spent_amount,
                currency=c.currency,
                impressions=imp,
                clicks=clk,
                bookings=bks,
                ctr=ctr,
                conversion_rate=cvr,
            )
        )

    return summaries
