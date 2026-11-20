"""
FastAPI Routers for Local Tour Guide Module 05 — Phase 5:
- Traveler Reviews (TR-16) & Wishlists (TR-17) & Public Promotions/Events
- Provider Onboarding Dashboard (PR-01), Reviews (PR-16), Sponsored Campaigns (PR-19) & Analytics (PR-20)
- Admin Review Moderation, Promotion Management (AD-07) & Reports Overview (AD-08)
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_admin, require_traveler
from app.database.connection import get_db
from app.models.user import User
from app.routers.tour_guide import GuideProvider, OwnedGuide
from app.routers.tour_guide_public import get_optional_current_user
from app.schemas.tour_guide import (
    AdminAdPaymentListResponse,
    AdminCampaignDecisionRequest,
    AdminTourGuideReportsOverviewResponse,
    ProviderTourGuideAnalyticsResponse,
    ProviderTourGuideDashboardResponse,
    TourGuideCampaignAnalyticsResponse,
    TourGuideCampaignCreate,
    TourGuideCampaignListResponse,
    TourGuideCampaignPaymentPrepareResponse,
    TourGuideCampaignResponse,
    TourGuideCampaignStatusUpdate,
    TourGuidePromotionEventCreate,
    TourGuidePromotionEventResponse,
    TourGuidePromotionPackageCreate,
    TourGuidePromotionPackageResponse,
    TourGuidePromotionPackageUpdate,
    TourGuideReviewCreate,
    TourGuideReviewListResponse,
    TourGuideReviewModerationRequest,
    TourGuideReviewResponse,
    TourGuideWishlistActionResponse,
    TourGuideWishlistAddRequest,
    TourGuideWishlistListResponse,
    TourGuideWishlistStatusResponse,
)
from app.services.tour_guide_phase5 import (
    add_tour_guide_to_wishlist,
    admin_create_tour_guide_promotion_package,
    admin_decide_tour_guide_campaign,
    admin_get_tour_guide_reports_overview,
    admin_list_tour_guide_ad_payments,
    admin_list_tour_guide_campaigns,
    admin_list_tour_guide_promotion_packages,
    admin_moderate_tour_guide_review,
    admin_update_tour_guide_promotion_package,
    create_tour_guide_campaign,
    get_provider_tour_guide_analytics,
    get_provider_tour_guide_campaign_analytics,
    get_provider_tour_guide_dashboard,
    get_tour_guide_wishlist_status,
    list_active_promotion_packages,
    list_provider_tour_guide_campaigns,
    list_provider_tour_guide_reviews,
    list_public_tour_guide_reviews,
    list_traveler_tour_guide_wishlist,
    prepare_tour_guide_campaign_payment,
    record_tour_guide_promotion_event,
    remove_tour_guide_from_wishlist,
    submit_tour_guide_campaign,
    submit_tour_guide_review,
    update_provider_tour_guide_campaign_status,
)

DatabaseSession = Annotated[Session, Depends(get_db)]

traveler_router = APIRouter(
    tags=["Tour Guide Reviews, Wishlists & Promotions"],
)

provider_router = APIRouter(
    prefix="/api/v1/provider/tour-guides",
    tags=["Tour Guide Provider Dashboard, Promotions & Analytics"],
)

admin_router = APIRouter(
    prefix="/api/v1/admin/tour-guides",
    tags=["Admin Tour Guide Promotions & Reports"],
)


# =============================================================================
# 1. TRAVELER & PUBLIC ENDPOINTS (TR-16, TR-17, PR-19/PR-20 EVENTS)
# =============================================================================

@traveler_router.post(
    "/api/v1/tour-guides/{guide_id}/reviews",
    response_model=TourGuideReviewResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a verified review for a completed tour guide booking (TR-16)",
)
def traveler_submit_tour_guide_review(
    guide_id: Annotated[int, Path(gt=0)],
    payload: TourGuideReviewCreate,
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
):
    return submit_tour_guide_review(
        db,
        guide_id=guide_id,
        traveler=traveler,
        payload=payload,
    )


@traveler_router.get(
    "/api/v1/tour-guides/{guide_id}/reviews",
    response_model=TourGuideReviewListResponse,
    summary="List public verified reviews and rating breakdown for a tour guide (TR-16)",
)
def public_list_tour_guide_reviews(
    guide_id: Annotated[int, Path(gt=0)],
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
):
    return list_public_tour_guide_reviews(
        db,
        guide_id=guide_id,
        page=page,
        page_size=page_size,
    )


@traveler_router.get(
    "/api/v1/tour-guide-wishlist/me",
    response_model=TourGuideWishlistListResponse,
    summary="List authenticated traveler's saved favorite tour guides (TR-17)",
)
def traveler_list_tour_guide_wishlist(
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_traveler_tour_guide_wishlist(
        db,
        traveler=traveler,
        page=page,
        page_size=page_size,
    )


@traveler_router.post(
    "/api/v1/tour-guide-wishlist/{guide_id}",
    response_model=TourGuideWishlistActionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save an approved tour guide to traveler wishlist (TR-17)",
)
def traveler_add_tour_guide_wishlist(
    guide_id: Annotated[int, Path(gt=0)],
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
    payload: TourGuideWishlistAddRequest | None = None,
):
    return add_tour_guide_to_wishlist(
        db,
        guide_id=guide_id,
        traveler=traveler,
        notes=payload.notes if payload else None,
    )


@traveler_router.delete(
    "/api/v1/tour-guide-wishlist/{guide_id}",
    response_model=TourGuideWishlistActionResponse,
    summary="Remove a tour guide from traveler wishlist (TR-17)",
)
def traveler_remove_tour_guide_wishlist(
    guide_id: Annotated[int, Path(gt=0)],
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
):
    return remove_tour_guide_from_wishlist(
        db,
        guide_id=guide_id,
        traveler=traveler,
    )


@traveler_router.get(
    "/api/v1/tour-guide-wishlist/{guide_id}/status",
    response_model=TourGuideWishlistStatusResponse,
    summary="Check whether a tour guide is saved in traveler wishlist (TR-17)",
)
def traveler_tour_guide_wishlist_status(
    guide_id: Annotated[int, Path(gt=0)],
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
):
    return get_tour_guide_wishlist_status(
        db,
        guide_id=guide_id,
        traveler=traveler,
    )


@traveler_router.get(
    "/api/v1/tour-guide-promotions/packages",
    response_model=list[TourGuidePromotionPackageResponse],
    summary="List active sponsored promotion packages available for tour guides (PR-19)",
)
def public_list_tour_guide_promotion_packages(db: DatabaseSession):
    return list_active_promotion_packages(db)


@traveler_router.post(
    "/api/v1/tour-guide-promotions/events",
    response_model=TourGuidePromotionEventResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an impression, click, inquiry, or booking event for an active guide campaign (PR-20)",
)
def public_record_tour_guide_promotion_event(
    payload: TourGuidePromotionEventCreate,
    request: Request,
    db: DatabaseSession,
    current_user: Annotated[User | None, Depends(get_optional_current_user)] = None,
):
    client_ip = request.client.host if request.client else None
    return record_tour_guide_promotion_event(
        db,
        payload=payload,
        current_user_id=current_user.id if current_user else None,
        ip_address=client_ip,
    )


# =============================================================================
# 2. PROVIDER DASHBOARD, REVIEWS, PROMOTIONS & ANALYTICS (PR-01, 16, 19, 20)
# =============================================================================

@provider_router.get(
    "/dashboard",
    response_model=ProviderTourGuideDashboardResponse,
    summary="Tour guide onboarding checklist and operational dashboard (PR-01)",
)
def provider_get_tour_guide_dashboard(
    provider: GuideProvider,
    db: DatabaseSession,
):
    return get_provider_tour_guide_dashboard(db, provider=provider)


@provider_router.get(
    "/reviews",
    response_model=TourGuideReviewListResponse,
    summary="Read verified traveler reviews and rating distribution for your guide profile (PR-16)",
)
def provider_list_my_tour_guide_reviews(
    guide: OwnedGuide,
    db: DatabaseSession,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    return list_provider_tour_guide_reviews(
        db,
        guide=guide,
        page=page,
        page_size=page_size,
    )


@provider_router.get(
    "/analytics",
    response_model=ProviderTourGuideAnalyticsResponse,
    summary="Provider booking, revenue, wishlist, rating, and promotion performance analytics (PR-20)",
)
def provider_get_tour_guide_analytics(
    guide: OwnedGuide,
    db: DatabaseSession,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    return get_provider_tour_guide_analytics(
        db,
        guide=guide,
        date_from=date_from,
        date_to=date_to,
    )


@provider_router.post(
    "/promotions/campaigns",
    response_model=TourGuideCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sponsored promotion campaign for your tour guide profile (PR-19)",
)
def provider_create_tour_guide_campaign(
    payload: TourGuideCampaignCreate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return create_tour_guide_campaign(db, guide=guide, payload=payload)


@provider_router.get(
    "/promotions/campaigns",
    response_model=TourGuideCampaignListResponse,
    summary="List your tour guide sponsored promotion campaigns (PR-19)",
)
def provider_list_tour_guide_campaigns(
    guide: OwnedGuide,
    db: DatabaseSession,
    campaign_status: str | None = Query(default=None, alias="status"),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_provider_tour_guide_campaigns(
        db,
        guide=guide,
        status_filter=campaign_status,
        page=page,
        page_size=page_size,
    )


@provider_router.post(
    "/promotions/campaigns/{campaign_id}/submit",
    response_model=TourGuideCampaignResponse,
    summary="Submit a draft tour guide campaign for admin approval (PR-19)",
)
def provider_submit_tour_guide_campaign(
    campaign_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return submit_tour_guide_campaign(db, guide=guide, campaign_id=campaign_id)


@provider_router.patch(
    "/promotions/campaigns/{campaign_id}/status",
    response_model=TourGuideCampaignResponse,
    summary="Pause, resume, or cancel your tour guide promotion campaign (PR-19)",
)
def provider_update_tour_guide_campaign_status(
    campaign_id: Annotated[int, Path(gt=0)],
    payload: TourGuideCampaignStatusUpdate,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return update_provider_tour_guide_campaign_status(
        db,
        guide=guide,
        campaign_id=campaign_id,
        new_status=payload.status,
    )


@provider_router.post(
    "/promotions/campaigns/{campaign_id}/payment/prepare",
    response_model=TourGuideCampaignPaymentPrepareResponse,
    summary="Prepare signed PayHere advertising checkout parameters for a guide campaign (PR-19)",
)
def provider_prepare_tour_guide_campaign_payment(
    campaign_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return prepare_tour_guide_campaign_payment(db, guide=guide, campaign_id=campaign_id)


@provider_router.get(
    "/promotions/campaigns/{campaign_id}/analytics",
    response_model=TourGuideCampaignAnalyticsResponse,
    summary="View impression, click, inquiry, and booking conversion stats for a campaign (PR-20)",
)
def provider_get_tour_guide_campaign_analytics(
    campaign_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return get_provider_tour_guide_campaign_analytics(
        db,
        guide=guide,
        campaign_id=campaign_id,
    )


# =============================================================================
# 3. ADMIN REVIEW MODERATION, PROMOTION MANAGEMENT & REPORTS (AD-07, AD-08)
# =============================================================================

@admin_router.patch(
    "/reviews/{review_id}/moderate",
    response_model=TourGuideReviewResponse,
    summary="Admin: publish or unpublish a tour guide review and recalculate ratings",
)
def admin_moderate_guide_review_endpoint(
    review_id: Annotated[int, Path(gt=0)],
    payload: TourGuideReviewModerationRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
):
    return admin_moderate_tour_guide_review(
        db,
        review_id=review_id,
        is_published=payload.is_published,
        admin=admin,
        reason=payload.reason,
    )


@admin_router.post(
    "/promotions/packages",
    response_model=TourGuidePromotionPackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: create a sponsored promotion package for tour guides (AD-07)",
)
def admin_create_guide_promo_package(
    payload: TourGuidePromotionPackageCreate,
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
    provider_profile_id: int | None = Query(default=None, gt=0),
):
    return admin_create_tour_guide_promotion_package(
        db,
        payload=payload,
        admin=admin,
        provider_profile_id=provider_profile_id,
    )


@admin_router.get(
    "/promotions/packages",
    response_model=list[TourGuidePromotionPackageResponse],
    summary="Admin: list all tour guide promotion packages (AD-07)",
)
def admin_list_guide_promo_packages(
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
):
    return admin_list_tour_guide_promotion_packages(db)


@admin_router.patch(
    "/promotions/packages/{package_id}",
    response_model=TourGuidePromotionPackageResponse,
    summary="Admin: update or toggle a tour guide promotion package (AD-07)",
)
def admin_update_guide_promo_package(
    package_id: Annotated[int, Path(gt=0)],
    payload: TourGuidePromotionPackageUpdate,
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
):
    return admin_update_tour_guide_promotion_package(
        db,
        package_id=package_id,
        payload=payload,
        admin=admin,
    )


@admin_router.get(
    "/promotions/campaigns",
    response_model=TourGuideCampaignListResponse,
    summary="Admin: list all tour guide sponsored campaigns (AD-07)",
)
def admin_list_guide_campaigns(
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
    campaign_status: str | None = Query(default=None, alias="status"),
    provider_profile_id: int | None = Query(default=None, gt=0),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return admin_list_tour_guide_campaigns(
        db,
        status_filter=campaign_status,
        provider_profile_id=provider_profile_id,
        page=page,
        page_size=page_size,
    )


@admin_router.post(
    "/promotions/campaigns/{campaign_id}/decision",
    response_model=TourGuideCampaignResponse,
    summary="Admin: approve, reject, pause, or cancel a tour guide sponsored campaign (AD-07)",
)
def admin_decide_guide_campaign(
    campaign_id: Annotated[int, Path(gt=0)],
    payload: AdminCampaignDecisionRequest,
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
):
    return admin_decide_tour_guide_campaign(
        db,
        campaign_id=campaign_id,
        payload=payload,
        admin=admin,
    )


@admin_router.get(
    "/promotions/payments",
    response_model=AdminAdPaymentListResponse,
    summary="Admin: oversee tour guide advertising payments and revenue (AD-07)",
)
def admin_list_guide_ad_payments(
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
    payment_status: str | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return admin_list_tour_guide_ad_payments(
        db,
        payment_status=payment_status,
        page=page,
        page_size=page_size,
    )


@admin_router.get(
    "/reports/overview",
    response_model=AdminTourGuideReportsOverviewResponse,
    summary="Admin: comprehensive tour guide, booking, revenue, and engagement report (AD-08)",
)
def admin_get_guide_reports_overview(
    admin: Annotated[User, Depends(require_admin)],
    db: DatabaseSession,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    return admin_get_tour_guide_reports_overview(
        db,
        date_from=date_from,
        date_to=date_to,
    )
