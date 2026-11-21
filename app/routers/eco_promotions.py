"""
FastAPI Router for Module 04 — Eco-Tourism Sponsored Promotions & Packages.

Endpoints (Phases P1.1B & P1.1C):
1. Admin Package Management (require_admin):
   - GET   /api/v1/admin/eco-promotions/packages              (List packages with pagination and active filter)
   - GET   /api/v1/admin/eco-promotions/packages/{package_id} (Get package detail)
   - POST  /api/v1/admin/eco-promotions/packages              (Create new package)
   - PUT   /api/v1/admin/eco-promotions/packages/{package_id} (Update package fields)
   - PATCH /api/v1/admin/eco-promotions/packages/{package_id}/toggle (Enable/disable package)

2. Public Package Catalog:
   - GET   /api/v1/eco-promotions/packages                    (List active packages, ordered deterministically)

3. Provider Campaign Management (require_approved_eco_provider):
   - POST  /api/v1/eco-promotions/campaigns                   (Request sponsored promotion for owned listing)
   - GET   /api/v1/eco-promotions/me/campaigns                (List provider's own campaigns)
   - GET   /api/v1/eco-promotions/me/campaigns/{campaign_id}  (Get campaign detail)
   - POST  /api/v1/eco-promotions/me/campaigns/{campaign_id}/cancel (Cancel pending or approved campaign)

4. Admin Campaign Moderation (require_admin):
   - GET   /api/v1/admin/eco-promotions/campaigns             (List all campaigns with filters & pagination)
   - GET   /api/v1/admin/eco-promotions/campaigns/{campaign_id} (Get campaign detail with audit metadata)
   - POST  /api/v1/admin/eco-promotions/campaigns/{campaign_id}/approve (Editorial approval only; no activation)
   - POST  /api/v1/admin/eco-promotions/campaigns/{campaign_id}/reject  (Reject campaign with mandatory reason)

Payment and safety rules:
- Editorial approval leaves the campaign unpaid until a verified capture is applied.
- Campaign pricing and duration use the immutable package snapshots.
- Historical package pricing snapshots are captured at campaign creation and never altered.
- Forged client-supplied statuses, snapshots, or admin review fields are forbidden (`extra='forbid'`).
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import math
import threading
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.dependencies import require_admin, require_approved_eco_provider
from app.core.security import verify_sponsored_tracking_token
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import (
    EcoPromotionAnalytics,
    EcoPromotionAttribution,
    EcoPromotionEventReceipt,
    EcoPromotionPackage,
    EcoSponsoredPromotion,
    EcoSponsorshipCapture,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.schemas.payment import PaymentInitiateResponse
from app.services import eco_sponsorship
from app.schemas.eco_promotion import (
    EcoCampaignApproveRequest,
    EcoCampaignRejectRequest,
    EcoPromotionAdminAnalyticsItem,
    EcoPromotionAdminAnalyticsResponse,
    EcoPromotionCampaignAnalyticsResponse,
    EcoPromotionCampaignSummaryAnalytics,
    EcoPromotionDailyMetric,
    EcoPromotionPackageCreate,
    EcoPromotionPackagePagination,
    EcoPromotionPackageResponse,
    EcoPromotionPackageToggleRequest,
    EcoPromotionPackageUpdate,
    EcoPromotionProviderAnalyticsResponse,
    EcoPromotionTrackRequest,
    EcoPromotionTrackResponse,
    EcoSponsoredPromotionAdminPagination,
    EcoSponsoredPromotionAdminResponse,
    EcoSponsoredPromotionCreate,
    EcoSponsoredPromotionPagination,
    EcoSponsoredPromotionResponse,
)

# Admin router with strict RBAC dependency
admin_eco_promotions_router = APIRouter(
    prefix="/api/v1/admin/eco-promotions",
    tags=["Admin Eco Promotions"],
    dependencies=[Depends(require_admin)],
)

# Provider & Public router
eco_promotions_router = APIRouter(
    prefix="/api/v1/eco-promotions",
    tags=["Eco Promotions"],
)


# ==============================================================================
# Helper Serializers
# ==============================================================================


def _serialize_campaign_response(
    campaign: EcoSponsoredPromotion,
) -> EcoSponsoredPromotionResponse:
    return EcoSponsoredPromotionResponse(
        id=campaign.id,
        eco_experience_id=campaign.eco_experience_id,
        provider_profile_id=campaign.provider_profile_id,
        package_id=campaign.package_id,
        package_price_snapshot=campaign.package_price_snapshot,
        currency_snapshot=campaign.currency_snapshot,
        duration_days_snapshot=campaign.duration_days_snapshot,
        campaign_status=campaign.campaign_status,
        payment_status=campaign.payment_status,
        payment_reference=campaign.payment_reference,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        requested_at=campaign.requested_at,
        reviewed_at=campaign.reviewed_at,
        review_reason=campaign.review_reason,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        experience_title=(
            campaign.eco_experience.title if campaign.eco_experience else None
        ),
        package_name=campaign.package.name if campaign.package else None,
        placement_tier=(
            campaign.package.placement_tier if campaign.package else None
        ),
        is_currently_active=campaign.is_currently_active,
    )


def _serialize_campaign_admin_response(
    campaign: EcoSponsoredPromotion,
) -> EcoSponsoredPromotionAdminResponse:
    return EcoSponsoredPromotionAdminResponse(
        id=campaign.id,
        eco_experience_id=campaign.eco_experience_id,
        provider_profile_id=campaign.provider_profile_id,
        package_id=campaign.package_id,
        package_price_snapshot=campaign.package_price_snapshot,
        currency_snapshot=campaign.currency_snapshot,
        duration_days_snapshot=campaign.duration_days_snapshot,
        campaign_status=campaign.campaign_status,
        payment_status=campaign.payment_status,
        payment_reference=campaign.payment_reference,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        requested_at=campaign.requested_at,
        reviewed_at=campaign.reviewed_at,
        review_reason=campaign.review_reason,
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
        experience_title=(
            campaign.eco_experience.title if campaign.eco_experience else None
        ),
        package_name=campaign.package.name if campaign.package else None,
        placement_tier=(
            campaign.package.placement_tier if campaign.package else None
        ),
        is_currently_active=campaign.is_currently_active,
        reviewed_by=campaign.reviewed_by,
        reviewer_email=campaign.reviewer.email if campaign.reviewer else None,
        provider_business_name=(
            campaign.provider_profile.business_name
            if campaign.provider_profile
            else None
        ),
    )


# ==============================================================================
# Admin Promotion Package Endpoints (P1.1B)
# ==============================================================================


@admin_eco_promotions_router.get(
    "/packages",
    response_model=EcoPromotionPackagePagination,
    summary="List all promotion packages for admin management",
)
def admin_list_promotion_packages(
    db: Annotated[Session, Depends(get_db)],
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Items per page"
    ),
    is_active: bool | None = Query(
        default=None, description="Optional active status filter"
    ),
):
    query = db.query(EcoPromotionPackage)

    if is_active is not None:
        query = query.filter(EcoPromotionPackage.is_active == is_active)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(EcoPromotionPackage.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return EcoPromotionPackagePagination(
        items=[EcoPromotionPackageResponse.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@admin_eco_promotions_router.get(
    "/packages/{package_id}",
    response_model=EcoPromotionPackageResponse,
    summary="Get promotion package details by ID",
)
def admin_get_promotion_package(
    package_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    package = (
        db.query(EcoPromotionPackage)
        .filter(EcoPromotionPackage.id == package_id)
        .first()
    )
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Promotion package with ID {package_id} not found",
        )

    return package


@admin_eco_promotions_router.post(
    "/packages",
    response_model=EcoPromotionPackageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new advertising promotion package",
)
def admin_create_promotion_package(
    payload: EcoPromotionPackageCreate,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    clean_name = payload.name.strip()
    existing = (
        db.query(EcoPromotionPackage)
        .filter(func.lower(EcoPromotionPackage.name) == func.lower(clean_name))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Promotion package with name '{clean_name}' already exists",
        )

    package = EcoPromotionPackage(
        name=clean_name,
        description=payload.description.strip() if payload.description else None,
        duration_days=payload.duration_days,
        price=payload.price,
        currency=payload.currency,
        placement_tier=payload.placement_tier,
        is_active=payload.is_active,
    )

    db.add(package)
    db.commit()
    db.refresh(package)

    return package


@admin_eco_promotions_router.put(
    "/packages/{package_id}",
    response_model=EcoPromotionPackageResponse,
    summary="Update an existing promotion package",
)
def admin_update_promotion_package(
    package_id: int,
    payload: EcoPromotionPackageUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    package = (
        db.query(EcoPromotionPackage)
        .filter(EcoPromotionPackage.id == package_id)
        .first()
    )
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Promotion package with ID {package_id} not found",
        )

    if payload.name is not None:
        clean_name = payload.name.strip()
        duplicate = (
            db.query(EcoPromotionPackage)
            .filter(
                func.lower(EcoPromotionPackage.name) == func.lower(clean_name),
                EcoPromotionPackage.id != package_id,
            )
            .first()
        )
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Promotion package with name '{clean_name}' already exists",
            )
        package.name = clean_name

    if payload.description is not None:
        package.description = (
            payload.description.strip() if payload.description.strip() else None
        )

    if payload.duration_days is not None:
        package.duration_days = payload.duration_days

    if payload.price is not None:
        package.price = payload.price

    if payload.currency is not None:
        package.currency = payload.currency

    if payload.placement_tier is not None:
        package.placement_tier = payload.placement_tier

    if payload.is_active is not None:
        package.is_active = payload.is_active

    package.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(package)

    return package


@admin_eco_promotions_router.patch(
    "/packages/{package_id}/toggle",
    response_model=EcoPromotionPackageResponse,
    summary="Toggle active state of a promotion package",
)
def admin_toggle_promotion_package(
    package_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
    payload: EcoPromotionPackageToggleRequest | None = None,
):
    package = (
        db.query(EcoPromotionPackage)
        .filter(EcoPromotionPackage.id == package_id)
        .first()
    )
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Promotion package with ID {package_id} not found",
        )

    if payload and payload.is_active is not None:
        package.is_active = payload.is_active
    else:
        package.is_active = not package.is_active

    package.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(package)

    return package


# ==============================================================================
# Public / Provider Package Catalog Endpoint (P1.1B)
# ==============================================================================


@eco_promotions_router.get(
    "/packages",
    response_model=list[EcoPromotionPackageResponse],
    summary="Browse active visibility packages available for purchase",
)
def list_public_promotion_packages(
    db: Annotated[Session, Depends(get_db)],
):
    packages = (
        db.query(EcoPromotionPackage)
        .filter(EcoPromotionPackage.is_active == True)
        .order_by(
            EcoPromotionPackage.price.asc(),
            EcoPromotionPackage.duration_days.asc(),
            EcoPromotionPackage.id.asc(),
        )
        .all()
    )

    return packages


# ==============================================================================
# Provider Campaign Endpoints (P1.1C)
# ==============================================================================


@eco_promotions_router.post(
    "/campaigns",
    response_model=EcoSponsoredPromotionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a sponsored promotion campaign for an approved eco experience",
)
def create_campaign_request(
    payload: EcoSponsoredPromotionCreate,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider endpoint: Request a sponsored visibility campaign for an owned, approved listing.
    Initial state: campaign_status='pending_approval', payment_status='unpaid'.
    Captures immutable package pricing and duration snapshots.
    """
    # 1. Experience existence and ownership check
    exp = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == payload.eco_experience_id)
        .first()
    )
    if not exp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found",
        )
    if exp.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this eco experience",
        )

    # 2. Experience publication status check
    if exp.listing_status != "approved" or not exp.is_published:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Eco experience must be approved and published before requesting sponsored promotion",
        )

    # 3. Package existence and active state check
    pkg = (
        db.query(EcoPromotionPackage)
        .filter(EcoPromotionPackage.id == payload.package_id)
        .first()
    )
    if not pkg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Promotion package not found",
        )
    if not pkg.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot request promotion for an inactive package",
        )

    eco_sponsorship.lock_experience(db, exp.id)
    eco_sponsorship.expire_campaigns(db, exp.id)
    # 4. Duplicate / conflict check for concurrent pending or active campaigns
    conflict = (
        db.query(EcoSponsoredPromotion)
        .filter(
            EcoSponsoredPromotion.eco_experience_id == exp.id,
            EcoSponsoredPromotion.campaign_status.in_(
                ["pending_approval", "approved", "active"]
            ),
        )
        .first()
    )
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An active or pending campaign already exists for this eco experience",
        )

    # 5. Create new campaign with immutable snapshots and default unpaid status
    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=provider_profile.id,
        package_id=pkg.id,
        package_price_snapshot=pkg.price,
        currency_snapshot=pkg.currency,
        duration_days_snapshot=pkg.duration_days,
        campaign_status="pending_approval",
        payment_status="unpaid",
        payment_reference=None,
        starts_at=None,
        ends_at=None,
        requested_at=datetime.now(UTC),
        reviewed_at=None,
        reviewed_by=None,
        review_reason=None,
    )

    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return _serialize_campaign_response(campaign)


@eco_promotions_router.post(
    "/me/campaigns/{campaign_id}/payments/initiate",
    response_model=PaymentInitiateResponse,
)
def initiate_eco_sponsorship(
    campaign_id: int,
    provider: Annotated[ProviderProfile, Depends(require_approved_eco_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    return eco_sponsorship.checkout(db, provider, campaign_id)


@admin_eco_promotions_router.post("/campaigns/{campaign_id}/expire")
def expire_eco_campaign(
    campaign_id: int, db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    campaign = eco_sponsorship.locked_campaign(
        db, select(EcoSponsoredPromotion).where(EcoSponsoredPromotion.id == campaign_id)
    )
    eco_sponsorship.expire_campaigns(db, campaign.eco_experience_id)
    db.refresh(campaign)
    result = {"campaign_status": campaign.campaign_status}
    db.commit()
    return result


@admin_eco_promotions_router.get("/campaigns/{campaign_id}/payments")
def report_eco_sponsorship_payments(
    campaign_id: int, db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict]:
    if not eco_sponsorship.ledger_enabled():
        raise HTTPException(503, "Eco sponsorship payment ledger is unavailable")
    if db.get(EcoSponsoredPromotion, campaign_id) is None:
        raise HTTPException(404, "Eco campaign not found")
    capture = EcoSponsorshipCapture
    rows = db.scalars(select(capture).where(capture.promotion_id == campaign_id)
                     .order_by(capture.captured_at.desc(), capture.gateway_id)
                     .offset((page - 1) * page_size).limit(page_size))
    return [
        {"gateway_id": row.gateway_id, "amount": str(row.amount), "currency": row.currency,
         "status": "applied" if row.applied else "reconciliation_required",
         "captured_at": row.captured_at}
        for row in rows
    ]



@eco_promotions_router.get(
    "/me/campaigns",
    response_model=EcoSponsoredPromotionPagination,
    summary="List authenticated provider's sponsored promotion campaigns",
)
def list_my_campaigns(
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
    campaign_status: str | None = Query(
        default=None, description="Optional campaign status filter"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Items per page"
    ),
):
    query = (
        db.query(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
        )
        .filter(
            EcoSponsoredPromotion.provider_profile_id == provider_profile.id
        )
    )

    if campaign_status and campaign_status.strip():
        query = query.filter(
            func.lower(EcoSponsoredPromotion.campaign_status)
            == campaign_status.strip().lower()
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(
            EcoSponsoredPromotion.created_at.desc(),
            EcoSponsoredPromotion.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return EcoSponsoredPromotionPagination(
        items=[_serialize_campaign_response(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@eco_promotions_router.get(
    "/me/campaigns/{campaign_id}",
    response_model=EcoSponsoredPromotionResponse,
    summary="Get details of a specific campaign owned by the provider",
)
def get_my_campaign(
    campaign_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    campaign = (
        db.query(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )
    if campaign.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this campaign",
        )

    return _serialize_campaign_response(campaign)



@eco_promotions_router.post(
    "/me/campaigns/{campaign_id}/cancel",
    response_model=EcoSponsoredPromotionResponse,
    summary="Cancel a pending or approved campaign request",
)
def cancel_my_campaign(
    campaign_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider endpoint: Cancels an owned campaign that is currently pending approval or approved.
    Active, expired, or already-cancelled campaigns cannot be cancelled through this endpoint.
    """
    campaign = eco_sponsorship.locked_campaign(
        db, select(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )
    if campaign.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this campaign",
        )

    if campaign.campaign_status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is already cancelled",
        )

    if campaign.campaign_status not in ("pending_approval", "approved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel campaign with status '{campaign.campaign_status}'",
        )

    campaign.campaign_status = "cancelled"
    campaign.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(campaign)

    return _serialize_campaign_response(campaign)


# ==============================================================================
# Phase P1.1E: Tracking Infrastructure & Anti-Abuse Safeguards
# ==============================================================================

_REPLAY_CACHE_LOCK = threading.Lock()
_USED_EVENT_NONCES: dict[str, set[str]] = {
    "impression": set(),
    "click": set(),
}
_NONCE_EXPIRY_QUEUE: list[tuple[float, str, str]] = []
_MAX_NONCE_QUEUE_SIZE = 25000
_NONCE_TTL_SECONDS = 7200.0  # 2 hours

_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_MAX_REQUESTS_PER_WINDOW = 60
_CLIENT_REQUEST_TIMESTAMPS: dict[str, list[float]] = defaultdict(list)


def _reset_tracking_caches():
    """Helper used to reset tracking state between isolated test suites."""
    with _REPLAY_CACHE_LOCK:
        _USED_EVENT_NONCES["impression"].clear()
        _USED_EVENT_NONCES["click"].clear()
        _NONCE_EXPIRY_QUEUE.clear()
    with _RATE_LIMIT_LOCK:
        _CLIENT_REQUEST_TIMESTAMPS.clear()


def _check_and_record_replay(event_type: str, nonce: str) -> None:
    """Enforces replay/duplicate prevention. Rejects already-consumed nonces."""
    now_ts = time.time()
    with _REPLAY_CACHE_LOCK:
        cutoff = now_ts - _NONCE_TTL_SECONDS
        while _NONCE_EXPIRY_QUEUE and (
            _NONCE_EXPIRY_QUEUE[0][0] < cutoff
            or len(_NONCE_EXPIRY_QUEUE) > _MAX_NONCE_QUEUE_SIZE
        ):
            _, old_nonce, old_type = _NONCE_EXPIRY_QUEUE.pop(0)
            _USED_EVENT_NONCES[old_type].discard(old_nonce)

        if nonce in _USED_EVENT_NONCES[event_type]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Duplicate tracking event detected: this token has already been recorded for a sponsored {event_type}",
            )

        _USED_EVENT_NONCES[event_type].add(nonce)
        _NONCE_EXPIRY_QUEUE.append((now_ts, nonce, event_type))


def _check_rate_limit(client_identifier: str) -> None:
    """Sliding-window bounded rate limiting to prevent metric flooding."""
    now_ts = time.time()
    cutoff = now_ts - _RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LIMIT_LOCK:
        timestamps = _CLIENT_REQUEST_TIMESTAMPS[client_identifier]
        _CLIENT_REQUEST_TIMESTAMPS[client_identifier] = [
            ts for ts in timestamps if ts > cutoff
        ]
        if (
            len(_CLIENT_REQUEST_TIMESTAMPS[client_identifier])
            >= _MAX_REQUESTS_PER_WINDOW
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded for sponsored promotion tracking. Please throttle requests.",
            )
        _CLIENT_REQUEST_TIMESTAMPS[client_identifier].append(now_ts)


def _record_sponsored_event(
    event_type: str,
    token: str,
    campaign_id_expected: int | None,
    db: Session,
    client_identifier: str,
) -> EcoPromotionTrackResponse:
    _check_rate_limit(client_identifier)

    try:
        payload = verify_sponsored_tracking_token(token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tracking token: {str(e)}",
        )

    token_campaign_id = payload.get("campaign_id")
    token_experience_id = payload.get("eco_experience_id")
    nonce = payload.get("nonce")

    if not token_campaign_id or not token_experience_id or not nonce:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed tracking token claims",
        )

    if (
        campaign_id_expected is not None
        and campaign_id_expected != token_campaign_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tracking token campaign ID {token_campaign_id} does not match endpoint campaign ID {campaign_id_expected}",
        )

    durable = eco_sponsorship.ledger_enabled()
    if not durable:
        _check_and_record_replay(event_type, nonce)

    campaign = eco_sponsorship.locked_campaign(
        db, select(EcoSponsoredPromotion).where(EcoSponsoredPromotion.id == token_campaign_id)
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Promotion campaign {token_campaign_id} not found",
        )
    if campaign.eco_experience_id != token_experience_id:
        raise HTTPException(400, "Tracking token experience does not match campaign")

    if campaign.payment_status != "paid":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot record ad metrics for an unpaid campaign",
        )

    if campaign.campaign_status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot record ad metrics for campaign with status '{campaign.campaign_status}'",
        )

    now = datetime.now(UTC)
    if campaign.starts_at is None or campaign.ends_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign validity schedule is undefined",
        )

    starts_at = (
        campaign.starts_at.replace(tzinfo=UTC)
        if campaign.starts_at.tzinfo is None
        else campaign.starts_at
    )
    ends_at = (
        campaign.ends_at.replace(tzinfo=UTC)
        if campaign.ends_at.tzinfo is None
        else campaign.ends_at
    )

    if ends_at <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign validity window has expired",
        )

    if starts_at > now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign validity window has not started yet",
        )

    if durable and db.scalar(select(EcoSponsoredPromotion.id).where(
        EcoSponsoredPromotion.id == campaign.id, *eco_sponsorship.active_campaign(now)
    )) is None:
        raise HTTPException(400, "Campaign has no applied verified payment")

    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == campaign.eco_experience_id)
        .first()
    )
    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated eco experience not found",
        )

    if not eco_sponsorship.listing_is_eligible(db, campaign):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot record ad metrics for an unapproved or unpublished experience",
        )

    if durable:
        if db.get(EcoPromotionEventReceipt, (nonce, event_type)) is not None:
            raise HTTPException(409, "Duplicate sponsored tracking event")
        db.add(EcoPromotionEventReceipt(
            nonce=nonce, event_type=event_type, promotion_id=campaign.id
        ))
    today = now.date()
    row = (
        db.query(EcoPromotionAnalytics)
        .filter(
            EcoPromotionAnalytics.promotion_id == campaign.id,
            EcoPromotionAnalytics.metric_date == today,
        )
        .first()
    )

    if row:
        if event_type == "impression":
            row.impressions_count = EcoPromotionAnalytics.impressions_count + 1
        elif event_type == "click":
            row.clicks_count = EcoPromotionAnalytics.clicks_count + 1
        row.updated_at = now
    else:
        row = EcoPromotionAnalytics(
            promotion_id=campaign.id,
            eco_experience_id=campaign.eco_experience_id,
            metric_date=today,
            impressions_count=1 if event_type == "impression" else 0,
            clicks_count=1 if event_type == "click" else 0,
        )
        db.add(row)

    db.commit()
    db.refresh(row)

    return EcoPromotionTrackResponse(
        status="recorded",
        event_type=event_type,
        campaign_id=campaign.id,
        eco_experience_id=campaign.eco_experience_id,
        metric_date=today,
        impressions_count=row.impressions_count,
        clicks_count=row.clicks_count,
    )


# ==============================================================================
# Public / Client Event Tracking APIs
# ==============================================================================


@eco_promotions_router.post(
    "/track/impression",
    response_model=EcoPromotionTrackResponse,
    summary="Record a client-reported visible sponsored placement impression",
)
def track_impression(
    payload: EcoPromotionTrackRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return _record_sponsored_event(
        event_type="impression",
        token=payload.token,
        campaign_id_expected=None,
        db=db,
        client_identifier="global_impression",
    )


@eco_promotions_router.post(
    "/track/click",
    response_model=EcoPromotionTrackResponse,
    summary="Record a client-reported sponsored placement click",
)
def track_click(
    payload: EcoPromotionTrackRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return _record_sponsored_event(
        event_type="click",
        token=payload.token,
        campaign_id_expected=None,
        db=db,
        client_identifier="global_click",
    )


@eco_promotions_router.post(
    "/campaigns/{campaign_id}/track/impression",
    response_model=EcoPromotionTrackResponse,
    summary="Record a visible sponsored placement impression for a specific campaign",
)
def track_campaign_impression(
    campaign_id: int,
    payload: EcoPromotionTrackRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return _record_sponsored_event(
        event_type="impression",
        token=payload.token,
        campaign_id_expected=campaign_id,
        db=db,
        client_identifier=f"camp_{campaign_id}",
    )


@eco_promotions_router.post(
    "/campaigns/{campaign_id}/track/click",
    response_model=EcoPromotionTrackResponse,
    summary="Record a sponsored placement click for a specific campaign",
)
def track_campaign_click(
    campaign_id: int,
    payload: EcoPromotionTrackRequest,
    db: Annotated[Session, Depends(get_db)],
):
    return _record_sponsored_event(
        event_type="click",
        token=payload.token,
        campaign_id_expected=campaign_id,
        db=db,
        client_identifier=f"camp_{campaign_id}",
    )


# ==============================================================================
# Provider Promotion Analytics Dashboard APIs
# ==============================================================================


@eco_promotions_router.get(
    "/me/analytics",
    response_model=EcoPromotionProviderAnalyticsResponse,
    summary="Get aggregated sponsored promotion performance dashboard for provider",
)
def get_provider_promotions_analytics(
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date cannot be after end_date",
        )

    campaigns = (
        db.query(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
        )
        .filter(
            EcoSponsoredPromotion.provider_profile_id == provider_profile.id
        )
        .order_by(EcoSponsoredPromotion.id.desc())
        .all()
    )

    campaign_ids = [c.id for c in campaigns]
    all_analytics_rows = []
    if campaign_ids:
        daily_query = db.query(EcoPromotionAnalytics).filter(
            EcoPromotionAnalytics.promotion_id.in_(campaign_ids)
        )
        if start_date:
            daily_query = daily_query.filter(
                EcoPromotionAnalytics.metric_date >= start_date
            )
        if end_date:
            daily_query = daily_query.filter(
                EcoPromotionAnalytics.metric_date <= end_date
            )
        all_analytics_rows = daily_query.order_by(
            EcoPromotionAnalytics.metric_date.asc()
        ).all()

    rows_by_campaign = defaultdict(list)
    rows_by_date = defaultdict(lambda: {"impressions": 0, "clicks": 0})
    for r in all_analytics_rows:
        rows_by_campaign[r.promotion_id].append(r)
        rows_by_date[r.metric_date]["impressions"] += r.impressions_count
        rows_by_date[r.metric_date]["clicks"] += r.clicks_count

    campaign_summaries = []
    total_impressions = 0
    total_clicks = 0
    active_count = 0

    for c in campaigns:
        if c.is_currently_active:
            active_count += 1
        c_rows = rows_by_campaign.get(c.id, [])
        c_impr = sum(r.impressions_count for r in c_rows)
        c_clicks = sum(r.clicks_count for r in c_rows)
        c_ctr = (
            Decimal(round(c_clicks / c_impr * 100, 2))
            if c_impr > 0
            else Decimal("0.00")
        )
        total_impressions += c_impr
        total_clicks += c_clicks

        campaign_summaries.append(
            EcoPromotionCampaignSummaryAnalytics(
                campaign_id=c.id,
                eco_experience_id=c.eco_experience_id,
                experience_title=(
                    c.eco_experience.title if c.eco_experience else None
                ),
                package_name=c.package.name if c.package else None,
                campaign_status=c.campaign_status,
                payment_status=c.payment_status,
                is_currently_active=c.is_currently_active,
                total_impressions=c_impr,
                total_clicks=c_clicks,
                ctr_percent=c_ctr,
            )
        )

    avg_ctr = (
        Decimal(round(total_clicks / total_impressions * 100, 2))
        if total_impressions > 0
        else Decimal("0.00")
    )

    daily_metrics = []
    for d in sorted(rows_by_date.keys()):
        d_impr = rows_by_date[d]["impressions"]
        d_clicks = rows_by_date[d]["clicks"]
        d_ctr = (
            Decimal(round(d_clicks / d_impr * 100, 2))
            if d_impr > 0
            else Decimal("0.00")
        )
        daily_metrics.append(
            EcoPromotionDailyMetric(
                metric_date=d,
                impressions_count=d_impr,
                clicks_count=d_clicks,
                ctr_percent=d_ctr,
            )
        )

    return EcoPromotionProviderAnalyticsResponse(
        provider_profile_id=provider_profile.id,
        total_campaigns=len(campaigns),
        active_campaigns_count=active_count,
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        average_ctr_percent=avg_ctr,
        start_date=start_date,
        end_date=end_date,
        campaigns=campaign_summaries,
        daily_metrics=daily_metrics,
    )


@eco_promotions_router.get(
    "/me/campaigns/{campaign_id}/analytics",
    response_model=EcoPromotionCampaignAnalyticsResponse,
    summary="Get performance analytics for a specific owned promotion campaign",
)
def get_single_campaign_analytics(
    campaign_id: int,
    provider_profile: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date cannot be after end_date",
        )

    campaign = (
        db.query(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with ID {campaign_id} not found",
        )

    if campaign.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own this campaign",
        )

    q = db.query(EcoPromotionAnalytics).filter(
        EcoPromotionAnalytics.promotion_id == campaign.id
    )
    if start_date:
        q = q.filter(EcoPromotionAnalytics.metric_date >= start_date)
    if end_date:
        q = q.filter(EcoPromotionAnalytics.metric_date <= end_date)

    records = q.order_by(EcoPromotionAnalytics.metric_date.asc()).all()

    total_impressions = sum(r.impressions_count for r in records)
    total_clicks = sum(r.clicks_count for r in records)
    ctr = (
        Decimal(round(total_clicks / total_impressions * 100, 2))
        if total_impressions > 0
        else Decimal("0.00")
    )

    daily_metrics = [
        EcoPromotionDailyMetric(
            metric_date=r.metric_date,
            impressions_count=r.impressions_count,
            clicks_count=r.clicks_count,
            ctr_percent=(
                Decimal(round(r.clicks_count / r.impressions_count * 100, 2))
                if r.impressions_count > 0
                else Decimal("0.00")
            ),
        )
        for r in records
    ]

    attributed_bookings = 0
    if eco_sponsorship.ledger_enabled():
        attribution = EcoPromotionAttribution
        count = select(func.count()).select_from(attribution).where(
            attribution.promotion_id == campaign.id
        )
        if start_date:
            count = count.where(attribution.recorded_on >= start_date)
        if end_date:
            count = count.where(attribution.recorded_on <= end_date)
        attributed_bookings = db.scalar(count)

    return EcoPromotionCampaignAnalyticsResponse(
        campaign_id=campaign.id,
        eco_experience_id=campaign.eco_experience_id,
        experience_title=(
            campaign.eco_experience.title if campaign.eco_experience else None
        ),
        package_name=campaign.package.name if campaign.package else None,
        campaign_status=campaign.campaign_status,
        payment_status=campaign.payment_status,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        is_currently_active=campaign.is_currently_active,
        total_impressions=total_impressions,
        total_clicks=total_clicks,
        ctr_percent=ctr,
        start_date=start_date,
        end_date=end_date,
        daily_metrics=daily_metrics,
        attributed_bookings=attributed_bookings,
    )


# ==============================================================================
# Admin Campaign Moderation Endpoints (P1.1C)
# ==============================================================================


@admin_eco_promotions_router.get(
    "/campaigns",
    response_model=EcoSponsoredPromotionAdminPagination,
    summary="List all sponsored promotion campaigns for admin moderation",
)
def admin_list_campaigns(
    db: Annotated[Session, Depends(get_db)],
    campaign_status: str | None = Query(
        default=None, description="Filter by campaign status"
    ),
    payment_status: str | None = Query(
        default=None, description="Filter by payment status"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="Items per page"
    ),
):
    query = db.query(EcoSponsoredPromotion).options(
        selectinload(EcoSponsoredPromotion.eco_experience),
        selectinload(EcoSponsoredPromotion.package),
        selectinload(EcoSponsoredPromotion.provider_profile),
        selectinload(EcoSponsoredPromotion.reviewer),
    )

    if campaign_status and campaign_status.strip():
        query = query.filter(
            func.lower(EcoSponsoredPromotion.campaign_status)
            == campaign_status.strip().lower()
        )

    if payment_status and payment_status.strip():
        query = query.filter(
            func.lower(EcoSponsoredPromotion.payment_status)
            == payment_status.strip().lower()
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    items = (
        query.order_by(
            EcoSponsoredPromotion.created_at.desc(),
            EcoSponsoredPromotion.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return EcoSponsoredPromotionAdminPagination(
        items=[_serialize_campaign_admin_response(c) for c in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@admin_eco_promotions_router.get(
    "/campaigns/{campaign_id}",
    response_model=EcoSponsoredPromotionAdminResponse,
    summary="Get detailed campaign management record for admin review",
)
def admin_get_campaign(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    campaign = (
        db.query(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
            selectinload(EcoSponsoredPromotion.provider_profile),
            selectinload(EcoSponsoredPromotion.reviewer),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with ID {campaign_id} not found",
        )

    return _serialize_campaign_admin_response(campaign)


@admin_eco_promotions_router.post(
    "/campaigns/{campaign_id}/approve",
    response_model=EcoSponsoredPromotionAdminResponse,
    summary="Admin editorial approval of a promotion campaign",
)
def admin_approve_campaign(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
    payload: EcoCampaignApproveRequest | None = None,
):
    """
    Admin moderation endpoint: Grants editorial approval to a campaign.
    PAYMENT DEFERRAL GUARANTEE:
    - Sets campaign_status = 'approved'.
    - Payment remains 'unpaid'.
    - Does NOT activate the campaign.
    - Approval represents editorial compliance only, awaiting centralized payment verification.
    """
    campaign = eco_sponsorship.locked_campaign(
        db, select(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
            selectinload(EcoSponsoredPromotion.provider_profile),
            selectinload(EcoSponsoredPromotion.reviewer),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with ID {campaign_id} not found",
        )

    if campaign.campaign_status == "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is already approved",
        )

    if campaign.campaign_status != "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve campaign with status '{campaign.campaign_status}'. Only pending campaigns may be approved.",
        )

    campaign.campaign_status = "approved"
    campaign.reviewed_by = current_admin.id
    campaign.reviewed_at = datetime.now(UTC)
    if payload and payload.notes:
        campaign.review_reason = payload.notes.strip()
    campaign.updated_at = datetime.now(UTC)

    # Explicit payment deferral safeguards:
    campaign.payment_status = "unpaid"
    campaign.starts_at = None
    campaign.ends_at = None

    db.commit()
    db.refresh(campaign)

    return _serialize_campaign_admin_response(campaign)


@admin_eco_promotions_router.post(
    "/campaigns/{campaign_id}/reject",
    response_model=EcoSponsoredPromotionAdminResponse,
    summary="Admin rejection of a promotion campaign with mandatory reason",
)
def admin_reject_campaign(
    campaign_id: int,
    payload: EcoCampaignRejectRequest,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_admin)],
):
    """
    Admin moderation endpoint: Rejects a campaign with a mandatory reason.
    Can reject from 'pending_approval' or revoke an un-activated 'approved' campaign.
    """
    campaign = eco_sponsorship.locked_campaign(
        db, select(EcoSponsoredPromotion)
        .options(
            selectinload(EcoSponsoredPromotion.eco_experience),
            selectinload(EcoSponsoredPromotion.package),
            selectinload(EcoSponsoredPromotion.provider_profile),
            selectinload(EcoSponsoredPromotion.reviewer),
        )
        .filter(EcoSponsoredPromotion.id == campaign_id)
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign with ID {campaign_id} not found",
        )

    if campaign.campaign_status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is already rejected",
        )

    if campaign.campaign_status not in ("pending_approval", "approved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot reject campaign with status '{campaign.campaign_status}'.",
        )

    campaign.campaign_status = "rejected"
    campaign.reviewed_by = current_admin.id
    campaign.reviewed_at = datetime.now(UTC)
    campaign.review_reason = payload.reason.strip()
    campaign.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(campaign)

    return _serialize_campaign_admin_response(campaign)


# ==============================================================================
# Admin Platform Promotion Analytics API (P1.1E)
# ==============================================================================


@admin_eco_promotions_router.get(
    "/analytics",
    response_model=EcoPromotionAdminAnalyticsResponse,
    summary="Get platform-wide sponsored promotion analytics",
)
def get_admin_promotions_analytics(
    current_admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    campaign_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    """
    Admin-only platform analytics: Aggregates real impressions and clicks.
    Provides campaign-level breakdown with pagination.
    Does NOT fabricate booking revenue or inquiry metrics.
    """
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date cannot be after end_date",
        )

    base_q = db.query(EcoSponsoredPromotion).options(
        selectinload(EcoSponsoredPromotion.eco_experience),
        selectinload(EcoSponsoredPromotion.package),
        selectinload(EcoSponsoredPromotion.provider_profile),
    )
    if campaign_status:
        base_q = base_q.filter(
            EcoSponsoredPromotion.campaign_status == campaign_status
        )

    total_campaigns = base_q.count()
    total_pages = (
        math.ceil(total_campaigns / page_size) if total_campaigns > 0 else 0
    )

    all_campaigns = base_q.all()
    all_c_ids = [c.id for c in all_campaigns]

    tot_impr = 0
    tot_clicks = 0
    rows_by_campaign = defaultdict(list)

    if all_c_ids:
        an_q = db.query(EcoPromotionAnalytics).filter(
            EcoPromotionAnalytics.promotion_id.in_(all_c_ids)
        )
        if start_date:
            an_q = an_q.filter(EcoPromotionAnalytics.metric_date >= start_date)
        if end_date:
            an_q = an_q.filter(EcoPromotionAnalytics.metric_date <= end_date)
        all_analytics = an_q.all()

        for a in all_analytics:
            tot_impr += a.impressions_count
            tot_clicks += a.clicks_count
            rows_by_campaign[a.promotion_id].append(a)

    overall_ctr = (
        Decimal(round(tot_clicks / tot_impr * 100, 2))
        if tot_impr > 0
        else Decimal("0.00")
    )

    paginated_campaigns = (
        base_q.order_by(EcoSponsoredPromotion.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for c in paginated_campaigns:
        c_rows = rows_by_campaign.get(c.id, [])
        c_impr = sum(r.impressions_count for r in c_rows)
        c_clicks = sum(r.clicks_count for r in c_rows)
        c_ctr = (
            Decimal(round(c_clicks / c_impr * 100, 2))
            if c_impr > 0
            else Decimal("0.00")
        )

        items.append(
            EcoPromotionAdminAnalyticsItem(
                campaign_id=c.id,
                provider_profile_id=c.provider_profile_id,
                provider_business_name=(
                    c.provider_profile.business_name
                    if c.provider_profile
                    else None
                ),
                eco_experience_id=c.eco_experience_id,
                experience_title=(
                    c.eco_experience.title if c.eco_experience else None
                ),
                package_name=c.package.name if c.package else None,
                campaign_status=c.campaign_status,
                payment_status=c.payment_status,
                starts_at=c.starts_at,
                ends_at=c.ends_at,
                is_currently_active=c.is_currently_active,
                total_impressions=c_impr,
                total_clicks=c_clicks,
                ctr_percent=c_ctr,
            )
        )

    return EcoPromotionAdminAnalyticsResponse(
        total_campaigns=total_campaigns,
        total_impressions=tot_impr,
        total_clicks=tot_clicks,
        overall_ctr_percent=overall_ctr,
        start_date=start_date,
        end_date=end_date,
        items=items,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
