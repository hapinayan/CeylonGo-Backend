"""
FastAPI Router for Module 03 — Provider Activity Discounts & Sponsored Campaigns (B15, B16).

Prefix: /api/v1/provider/activities
- /discounts: Provider Discount Offers (B15)
- /promotions/campaigns: Provider Sponsored Campaign Applications (B16)

Guarded strictly by require_activity_provider.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.routers.provider_activities import require_activity_provider
from app.schemas.activity_promotion import (
    ActivityCampaignAnalyticsSummary,
    ActivityCampaignCreate,
    ActivityCampaignPagination,
    ActivityCampaignResponse,
    ActivityCampaignUpdate,
    ActivityDiscountCreate,
    ActivityDiscountPagination,
    ActivityDiscountResponse,
    ActivityDiscountUpdate,
    ActivityProviderPromotionsAnalytics,
)
from app.services.activity_promotions import (
    cancel_provider_campaign,
    create_provider_campaign,
    create_provider_discount,
    deactivate_provider_discount,
    get_activity_campaign_analytics,
    get_provider_activities_promotions_analytics,
    get_provider_campaign,
    get_provider_discount,
    list_provider_campaigns,
    list_provider_discounts,
    update_provider_campaign,
    update_provider_discount,
)

router = APIRouter(
    prefix="/api/v1/provider/activities",
    tags=["Provider Activity Promotions"],
)


# ==============================================================================
# B15: Provider Discount Offers Endpoints (dbo.promotion_packages)
# ==============================================================================


@router.post(
    "/discounts",
    response_model=ActivityDiscountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create provider discount package (B15)",
)
def create_discount_offer(
    payload: ActivityDiscountCreate,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Create a new promotional discount package for the authenticated provider (B15).
    Persists to dbo.promotion_packages at provider scope.
    """
    return create_provider_discount(db, profile, payload)


@router.get(
    "/discounts",
    response_model=ActivityDiscountPagination,
    summary="List provider discount packages (B15)",
)
def list_discount_offers(
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
    is_active: bool | None = Query(default=None, description="Filter by active status"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    List discount packages belonging to the authenticated provider (B15).
    """
    return list_provider_discounts(
        db,
        profile,
        is_active=is_active,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/discounts/{discount_id}",
    response_model=ActivityDiscountResponse,
    summary="Get provider discount details (B15)",
)
def get_discount_offer(
    discount_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Get details of an owned discount package by ID (B15).
    """
    return get_provider_discount(db, profile, discount_id)


@router.patch(
    "/discounts/{discount_id}",
    response_model=ActivityDiscountResponse,
    summary="Update provider discount package (B15)",
)
def update_discount_offer(
    discount_id: int,
    payload: ActivityDiscountUpdate,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Update an owned discount package (B15).
    """
    return update_provider_discount(db, profile, discount_id, payload)


@router.post(
    "/discounts/{discount_id}/deactivate",
    response_model=ActivityDiscountResponse,
    summary="Deactivate provider discount package (B15)",
)
def deactivate_discount_offer(
    discount_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Deactivate an owned discount package (B15).
    """
    return deactivate_provider_discount(db, profile, discount_id)


# ==============================================================================
# B16: Provider Sponsored Campaign Applications Endpoints (dbo.promotion_campaigns)
# ==============================================================================


@router.post(
    "/promotions/campaigns",
    response_model=ActivityCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit activity sponsored campaign application (B16)",
)
def create_sponsored_campaign(
    payload: ActivityCampaignCreate,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Submit a sponsored campaign application for activities (B16).
    Enforces target_module = 'activity', physical column safety, and no self-approval.
    """
    return create_provider_campaign(db, profile, payload)


@router.get(
    "/promotions/campaigns",
    response_model=ActivityCampaignPagination,
    summary="List provider activity sponsored campaigns (B16)",
)
def list_sponsored_campaigns(
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by campaign status (draft, pending_approval, active, completed, cancelled)",
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    """
    List activity sponsored campaigns belonging to the authenticated provider (B16).
    """
    return list_provider_campaigns(
        db,
        profile,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/promotions/campaigns/{campaign_id}",
    response_model=ActivityCampaignResponse,
    summary="Get activity sponsored campaign details (B16)",
)
def get_sponsored_campaign(
    campaign_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Get details of an owned activity sponsored campaign by ID (B16).
    """
    return get_provider_campaign(db, profile, campaign_id)


@router.patch(
    "/promotions/campaigns/{campaign_id}",
    response_model=ActivityCampaignResponse,
    summary="Update draft activity sponsored campaign (B16)",
)
def update_sponsored_campaign(
    campaign_id: int,
    payload: ActivityCampaignUpdate,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Update a draft activity sponsored campaign (B16).
    """
    return update_provider_campaign(db, profile, campaign_id, payload)


@router.post(
    "/promotions/campaigns/{campaign_id}/cancel",
    response_model=ActivityCampaignResponse,
    summary="Cancel activity sponsored campaign (B16)",
)
def cancel_sponsored_campaign(
    campaign_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Cancel an owned activity sponsored campaign (B16).
    """
    return cancel_provider_campaign(db, profile, campaign_id)


# ==============================================================================
# B18: Provider Promotion Analytics Endpoints (dbo.promotion_events)
# ==============================================================================


@router.get(
    "/promotions/analytics",
    response_model=ActivityProviderPromotionsAnalytics,
    summary="Provider activity promotions aggregate analytics (B18)",
)
def get_provider_promotions_analytics(
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Retrieve provider-wide aggregate performance analytics across all activity campaigns (B18).
    Includes impressions, clicks, inquiries, bookings, CTR, conversion rate, and spend totals.
    """
    return get_provider_activities_promotions_analytics(db, profile)


@router.get(
    "/promotions/campaigns/{campaign_id}/analytics",
    response_model=ActivityCampaignAnalyticsSummary,
    summary="Get single activity campaign performance analytics (B18)",
)
def get_campaign_analytics(
    campaign_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Retrieve performance analytics for a single activity sponsored campaign (B18).
    Enforces tenant isolation and target_module = 'activity'.
    """
    return get_activity_campaign_analytics(db, profile, campaign_id)


@router.get(
    "/promotions/campaigns/{campaign_id}/stats",
    response_model=ActivityCampaignAnalyticsSummary,
    summary="Get single activity campaign stats (B18 - alias)",
)
def get_campaign_stats(
    campaign_id: int,
    profile: Annotated[ProviderProfile, Depends(require_activity_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Alias endpoint for single campaign performance metrics (B18 /stats).
    """
    return get_activity_campaign_analytics(db, profile, campaign_id)
