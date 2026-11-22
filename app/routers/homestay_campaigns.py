from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import record_admin_audit_log
from app.core.dependencies import (
    require_admin,
    require_approved_provider,
    require_finance_admin,
    require_operations_or_finance_admin,
)
from app.core.listing_sync import sync_homestay_listing_reference
from app.core.attribution import generate_attribution_token, verify_attribution_token
from app.core.publication import public_homestay
from app.core.payhere import PayHereService, payhere_service
from app.database.connection import get_db
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    ListingReference,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.promotion_campaign import (
    AdvertisingPaymentResponse,
    AdvertisingRevenueSummaryResponse,
    CampaignAttributionTokenResponse,
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
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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

    listing_ref = sync_homestay_listing_reference(db, homestay)
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to establish listing reference for homestay",
        )

    campaign = PromotionCampaign(
        provider_profile_id=current_provider.id,
        listing_reference_id=listing_ref.id,
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
    Initiate PayHere advertising checkout for an approved, owned sponsored campaign.
    Requires administrator approval, valid homestay listing ownership, unexpired schedule,
    and server-side amount determination.
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

    # 1. Validate campaign ownership
    if campaign.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only initiate payment for your own campaigns",
        )

    # 2. Validate associated Homestay listing
    if not campaign.listing_reference_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is not linked to a specific homestay listing",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.id == campaign.listing_reference_id,
            ListingReference.module_type == "homestay",
        )
        .first()
    )
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Associated homestay listing reference not found",
        )

    homestay = db.query(Homestay).filter(Homestay.id == listing_ref.target_id).first()
    if not homestay or not homestay.is_active or homestay.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Associated homestay listing must be approved and active",
        )

    # Authoritative provider ownership check
    if homestay.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only sponsor homestays owned by your provider profile",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 3. Schedule expiration check
    if campaign.ends_at <= now_utc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign schedule has already expired. Cannot initiate payment for expired campaigns.",
        )

    # 4. Require administrator approval before payment
    if campaign.status in ("cancelled", "rejected"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot initiate payment for {campaign.status} campaign",
        )

    if campaign.status == "pending_approval":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign requires administrator approval before payment can be initiated",
        )

    if campaign.status not in ("approved", "active"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign status '{campaign.status}' is not eligible for payment",
        )

    # 5. Check if already paid
    existing_completed = (
        db.query(AdvertisingPayment)
        .filter(
            AdvertisingPayment.campaign_id == campaign.id,
            AdvertisingPayment.payment_status == "completed",
        )
        .first()
    )
    if existing_completed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Advertising payment for this campaign has already been completed",
        )

    # 6. Determine payable advertising fees from trusted server-side pricing
    # Real advertising checkout charges are disabled until an authoritative rate policy is approved by management.
    # In sandbox/test mode, campaign budget is permitted strictly for test verification with an explicit notice.
    if payhere_service.mode != "sandbox":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Real advertising checkout charges are disabled until an authoritative advertising pricing policy is officially approved by platform management.",
        )

    amount = Decimal(str(campaign.budget)).quantize(Decimal("0.01"))
    if amount <= Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authoritative advertising fee is not defined for this campaign. Positive budget required.",
        )

    # 7. Prevent duplicate checkout attempts
    pending_payment = (
        db.query(AdvertisingPayment)
        .filter(
            AdvertisingPayment.campaign_id == campaign.id,
            AdvertisingPayment.payment_status == "pending",
        )
        .order_by(AdvertisingPayment.id.desc())
        .first()
    )

    if pending_payment:
        order_ref = pending_payment.order_reference
        pending_payment.amount = amount
        pending_payment.updated_at = now_utc
    else:
        order_ref = f"AD-CAMP-{campaign.id}-{int(datetime.now(timezone.utc).timestamp())}"
        pending_payment = AdvertisingPayment(
            campaign_id=campaign.id,
            provider_profile_id=current_provider.id,
            order_reference=order_ref,
            amount=amount,
            currency="LKR",
            gateway_name="payhere",
            payment_status="pending",
        )
        db.add(pending_payment)

    db.commit()
    db.refresh(pending_payment)

    # 8. Generate PayHere checkout hash
    hash_value = payhere_service.generate_checkout_hash(
        order_id=order_ref,
        amount=amount,
        currency="LKR",
    )

    return CampaignPaymentInitiateResponse(
        campaign_id=campaign.id,
        order_id=order_ref,
        amount=amount,
        currency="LKR",
        merchant_id=payhere_service.merchant_id,
        hash_value=hash_value,
        sandbox=(payhere_service.mode == "sandbox"),
        status="pending",
        note=f"Payment for sponsored campaign: {campaign.campaign_name}",
    )


@router.get(
    "/api/v1/homestays/campaigns/{campaign_id}/payments",
    response_model=list[AdvertisingPaymentResponse],
    summary="Provider view campaign payments",
)
def get_campaign_payments(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """Retrieve all payment records for an owned campaign."""
    campaign = db.query(PromotionCampaign).filter(PromotionCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Campaign {campaign_id} not found",
        )

    if campaign.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view payments for your own campaigns",
        )

    payments = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.campaign_id == campaign_id)
        .order_by(AdvertisingPayment.created_at.desc())
        .all()
    )
    return payments


@router.get(
    "/api/v1/homestays/campaigns/payments/mine",
    response_model=list[AdvertisingPaymentResponse],
    summary="Provider view all own advertising payments",
)
def get_my_advertising_payments(
    db: Annotated[Session, Depends(get_db)],
    current_provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
):
    """Retrieve all advertising payments made by the authenticated provider."""
    payments = (
        db.query(AdvertisingPayment)
        .filter(AdvertisingPayment.provider_profile_id == current_provider.id)
        .order_by(AdvertisingPayment.created_at.desc())
        .all()
    )
    return payments


@router.get(
    "/api/v1/admin/advertising/payments",
    response_model=list[AdvertisingPaymentResponse],
    summary="Admin list advertising payments",
)
def admin_list_advertising_payments(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_finance_admin)],
    payment_status: Annotated[str | None, Query(description="Filter by payment status")] = None,
    campaign_id: Annotated[int | None, Query(description="Filter by campaign ID")] = None,
    provider_profile_id: Annotated[int | None, Query(description="Filter by provider ID")] = None,
):
    """Admin retrieves advertising transactions with optional filters."""
    query = db.query(AdvertisingPayment)
    if payment_status:
        query = query.filter(AdvertisingPayment.payment_status == payment_status)
    if campaign_id:
        query = query.filter(AdvertisingPayment.campaign_id == campaign_id)
    if provider_profile_id:
        query = query.filter(AdvertisingPayment.provider_profile_id == provider_profile_id)

    return query.order_by(AdvertisingPayment.created_at.desc()).all()


@router.get(
    "/api/v1/admin/advertising/revenue-summary",
    response_model=AdvertisingRevenueSummaryResponse,
    summary="Admin advertising revenue summary",
)
def admin_get_advertising_revenue_summary(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_finance_admin)],
):
    """Admin summary of total verified advertising revenue, separate from booking fees."""
    total_rev = (
        db.query(func.coalesce(func.sum(AdvertisingPayment.amount), Decimal("0.00")))
        .filter(AdvertisingPayment.payment_status == "completed")
        .scalar()
    ) or Decimal("0.00")

    total_count = db.query(func.count(AdvertisingPayment.id)).scalar() or 0
    completed_count = (
        db.query(func.count(AdvertisingPayment.id))
        .filter(AdvertisingPayment.payment_status == "completed")
        .scalar()
        or 0
    )
    failed_count = (
        db.query(func.count(AdvertisingPayment.id))
        .filter(AdvertisingPayment.payment_status.in_(["failed", "cancelled"]))
        .scalar()
        or 0
    )
    pending_count = (
        db.query(func.count(AdvertisingPayment.id))
        .filter(AdvertisingPayment.payment_status == "pending")
        .scalar()
        or 0
    )

    return AdvertisingRevenueSummaryResponse(
        total_verified_revenue=Decimal(str(total_rev)),
        total_transactions_count=total_count,
        completed_transactions_count=completed_count,
        failed_transactions_count=failed_count,
        pending_transactions_count=pending_count,
        currency="LKR",
    )


@router.get(
    "/api/v1/admin/homestays/campaigns",
    response_model=list[PromotionCampaignResponse],
    summary="Admin list all sponsored campaigns",
)
def admin_list_campaigns(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
        campaign.status = "approved"

    record_admin_audit_log(
        db,
        admin_user_id=current_admin.id,
        action="homestay_campaign_approved",
        entity_name="promotion_campaigns",
        entity_id=campaign.id,
        details={"campaign_name": campaign.campaign_name, "new_status": campaign.status},
        request=request,
    )
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
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
    record_admin_audit_log(
        db,
        admin_user_id=current_admin.id,
        action="homestay_campaign_rejected",
        entity_name="promotion_campaigns",
        entity_id=campaign.id,
        details={"campaign_name": campaign.campaign_name, "new_status": "cancelled"},
        request=request,
    )
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

    return PromotionAnalyticsSummary(
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


@router.get(
    "/api/v1/admin/homestays/campaigns/analytics",
    response_model=list[PromotionAnalyticsSummary],
    summary="Admin homestay promotion analytics summary",
)
def admin_get_promotions_analytics(
    db: Annotated[Session, Depends(get_db)],
    current_admin: Annotated[User, Depends(require_operations_or_finance_admin)],
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
            PromotionAnalyticsSummary(
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


# ==============================================================================
# 5. ADVERTISING ATTRIBUTION TOKENS (PHASE 12)
# ==============================================================================

@router.post(
    "/api/v1/homestays/campaigns/{campaign_id}/attribution-token",
    response_model=CampaignAttributionTokenResponse,
    summary="Issue signed advertising attribution tracking token for an active campaign",
)
def issue_campaign_attribution_token(
    campaign_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Issue a server-signed tracking token for an eligible active campaign.
    Ensures attribution can only be linked to authentic campaigns.
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

    if campaign.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot issue attribution token for campaign with status '{campaign.status}'. Campaign must be active.",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.id == campaign.listing_reference_id,
            ListingReference.module_type == "homestay",
        )
        .first()
    )
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign is not linked to a valid Homestay listing reference",
        )

    homestay_id = listing_ref.target_id
    if not db.query(Homestay.id).filter(Homestay.id == homestay_id, public_homestay()).first():
        raise HTTPException(404, detail="Homestay not found")
    token = generate_attribution_token(campaign_id=campaign.id, homestay_id=homestay_id, valid_hours=48)

    return CampaignAttributionTokenResponse(
        campaign_id=campaign.id,
        homestay_id=homestay_id,
        attribution_token=token,
        expires_in_hours=48,
    )


@router.get(
    "/api/v1/homestays/{homestay_id}/attribution-token",
    response_model=CampaignAttributionTokenResponse,
    summary="Get signed advertising attribution token for a sponsored homestay listing",
)
def get_homestay_active_attribution_token(
    homestay_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Retrieve an attribution token for the currently active sponsored campaign of a homestay.
    """
    homestay = db.query(Homestay).filter(Homestay.id == homestay_id, public_homestay()).first()
    if not homestay:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Homestay {homestay_id} not found",
        )

    listing_ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay_id,
        )
        .first()
    )
    if not listing_ref:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No listing reference found for homestay {homestay_id}",
        )

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    campaign = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.listing_reference_id == listing_ref.id,
            PromotionCampaign.status == "active",
            PromotionCampaign.starts_at <= now_utc,
            PromotionCampaign.ends_at >= now_utc,
        )
        .order_by(PromotionCampaign.id.desc())
        .first()
    )
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active sponsored campaign found for homestay {homestay_id}",
        )

    token = generate_attribution_token(campaign_id=campaign.id, homestay_id=homestay_id, valid_hours=48)
    return CampaignAttributionTokenResponse(
        campaign_id=campaign.id,
        homestay_id=homestay_id,
        attribution_token=token,
        expires_in_hours=48,
    )

