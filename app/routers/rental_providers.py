"""
FastAPI router for CeylonGo Module 09 — Rental Service Provider Backend (Phase 3).
Handles:
- SP-02: Add Rental Vehicle (with technical specifications & pending approval)
- SP-03: Manage Rental Vehicles (fleet listing, pagination, update, availability toggle, soft-retirement)
- SP-04: Vehicle Photo Management (upload, caption, display order, primary photo designation, delete)
- SP-05: Category Management (bike, bicycle, tuk_tuk validation)
- SP-06: Vehicle Specifications (model year, capacity, transmission, fuel type, license plate)
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from math import ceil
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.core.notification_service import create_notification
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalConditionEvidence,
    RentalDeposit,
    RentalEligibilityCheck,
    RentalIncident,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    PaymentTransaction,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
    ProviderSettlement,
    Review,
)
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.rental_provider import (
    BlockType,
    ProviderAvailabilityBlockCreate,
    ProviderAvailabilityBlockResponse,
    ProviderAvailabilityBlockUpdate,
    ProviderBookingActionResponse,
    ProviderBookingHistoryItem,
    ProviderBookingHistoryPagination,
    ProviderBookingRejectRequest,
    ProviderBookingRequestItem,
    ProviderBookingRequestPagination,
    ProviderCashPaymentResponse,
    ProviderEvidenceItem,
    ProviderEvidenceResponse,
    ProviderHandoverInspectionRequest,
    ProviderHandoverResponse,
    ProviderInspectionResponse,
    ProviderLocationCreate,
    ProviderLocationResponse,
    ProviderLocationUpdate,
    ProviderRatePlanCreate,
    ProviderRatePlanResponse,
    ProviderRatePlanUpdate,
    ProviderRecordCashPaymentRequest,
    ProviderRentalCampaignCreateRequest,
    ProviderRentalCampaignListResponse,
    ProviderRentalCampaignResponse,
    ProviderRentalDiscountCreateRequest,
    ProviderRentalDiscountListResponse,
    ProviderRentalDiscountResponse,
    ProviderRentalDiscountUpdateRequest,
    ProviderReturnInspectionRequest,
    ProviderReturnResponse,
    ProviderReviewItem,
    ProviderReviewsDashboardResponse,
    ProviderSettlementItem,
    ProviderSettlementsSummaryResponse,
    ProviderVehicleCreate,
    ProviderVehicleDetailResponse,
    ProviderVehicleImageCreate,
    ProviderVehicleImageResponse,
    ProviderVehicleImageUpdate,
    ProviderVehicleListItem,
    ProviderVehiclePagination,
    ProviderVehicleUpdate,
    ProviderVerificationStatusResponse,
    RatePlanDuration,
    RentalBookingMetrics,
    RentalFleetMetrics,
    RentalPromotionPackageOption,
    RentalProviderDashboardResponse,
    RentalRevenueMetrics,
    RentalUtilizationMetrics,
    VehicleType,
    VehicleVerificationSummary,
)
from app.schemas.rental_deposit import (
    RentalDepositCollectRequest,
    RentalDepositResolveRequest,
    RentalDepositResponse,
)

router = APIRouter(
    prefix="/api/v1/provider/rentals",
    tags=["Rental Provider Fleet"],
)


# ==============================================================================
# HELPER FUNCTIONS & DEPENDENCIES
# ==============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def get_current_approved_rental_provider(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderProfile:
    """
    Ensure the authenticated caller has provider role, has a registered
    ProviderProfile for provider_type='rental', and is approved by admin.
    """
    if current_user.role not in ("provider", "service_provider", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: provider role required",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider profile not found",
        )

    if profile.provider_type != "rental":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only rental service providers can access rental fleet operations",
        )

    if profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access forbidden: rental provider application is {profile.approval_status}",
        )

    return profile


def get_current_rental_provider(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderProfile:
    """
    Ensure the authenticated caller has provider role and a registered
    rental ProviderProfile (permits non-approved callers for SP-20 status tracking).
    """
    if current_user.role not in ("provider", "service_provider", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: provider role required",
        )

    profile = (
        db.query(ProviderProfile)
        .filter(ProviderProfile.user_id == current_user.id)
        .first()
    )

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Provider profile not found",
        )

    if profile.provider_type != "rental":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only rental service providers can access rental operations",
        )

    return profile


def get_provider_vehicle_or_404(
    vehicle_id: int,
    provider_id: int,
    db: Session,
) -> RentalVehicle:
    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider_id,
        )
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental vehicle not found or access denied",
        )
    return vehicle


def get_provider_booking_or_404(
    booking_id: int,
    provider_id: int,
    db: Session,
) -> tuple[RentalBooking, RentalVehicle]:
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found or access denied",
        )
    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    if not vehicle or vehicle.provider_id != provider_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found or access denied",
        )
    return booking, vehicle


def get_primary_image_url(images: list[RentalVehicleImage]) -> str | None:
    if not images:
        return None
    for img in images:
        if img.is_primary:
            return img.image_url
    return images[0].image_url


def find_active_booking_conflicts(
    vehicle_id: int,
    db: Session,
) -> list[RentalBooking]:
    now = utc_now()
    bookings = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.vehicle_id == vehicle_id,
            RentalBooking.status.in_(["pending", "confirmed", "active"]),
        )
        .all()
    )
    active_conflicts = []
    for b in bookings:
        if b.status == "pending" and b.expires_at and b.expires_at < now:
            continue
        active_conflicts.append(b)
    return active_conflicts


def build_vehicle_detail_response(vehicle: RentalVehicle) -> ProviderVehicleDetailResponse:
    sorted_images = sorted(vehicle.images or [], key=lambda img: (img.display_order, img.id))
    primary_url = get_primary_image_url(sorted_images)

    image_responses = [
        ProviderVehicleImageResponse(
            id=img.id,
            vehicle_id=img.vehicle_id,
            image_url=img.image_url,
            caption=img.caption,
            display_order=img.display_order,
            is_primary=img.is_primary,
            created_at=img.created_at,
        )
        for img in sorted_images
    ]

    return ProviderVehicleDetailResponse(
        id=vehicle.id,
        provider_id=vehicle.provider_id,
        vehicle_type=vehicle.vehicle_type,
        vehicle_name=vehicle.vehicle_name,
        description=vehicle.description,
        price_per_day=vehicle.price_per_day,
        currency="LKR",
        status=vehicle.status,
        approval_status=vehicle.approval_status,
        verification_status=vehicle.verification_status,
        model_year=vehicle.model_year,
        license_plate=vehicle.license_plate,
        seating_capacity=vehicle.seating_capacity,
        transmission=vehicle.transmission,
        fuel_type=vehicle.fuel_type,
        district=vehicle.district,
        city=vehicle.city,
        location_id=vehicle.location_id,
        cancellation_policy=vehicle.cancellation_policy,
        free_cancellation_hours=vehicle.free_cancellation_hours,
        helmet_count=getattr(vehicle, "helmet_count", None),
        helmets_included=getattr(vehicle, "helmets_included", None),
        helmet_sizes=getattr(vehicle, "helmet_sizes", None),
        security_deposit=getattr(vehicle, "security_deposit", None),
        fuel_policy=getattr(vehicle, "fuel_policy", None),
        primary_image_url=primary_url,
        images=image_responses,
        created_at=vehicle.created_at,
        updated_at=vehicle.updated_at,
    )


# ==============================================================================
# SP-02: ADD RENTAL VEHICLE
# ==============================================================================

@router.post(
    "/vehicles",
    response_model=ProviderVehicleDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new rental vehicle (SP-02, SP-05, SP-06)",
)
def add_rental_vehicle(
    payload: ProviderVehicleCreate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Registers a new rental vehicle under the authenticated provider's account.
    All new provider vehicles start with approval_status='pending' and
    verification_status='unverified', requiring admin review before public discovery.
    """
    new_vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type=payload.vehicle_type,
        vehicle_name=payload.vehicle_name,
        description=payload.description,
        price_per_day=payload.price_per_day,
        status="available",
        approval_status="pending",  # Strict: Provider listings start as pending
        verification_status="unverified",
        model_year=payload.model_year,
        license_plate=payload.license_plate,
        seating_capacity=payload.seating_capacity,
        transmission=payload.transmission,
        fuel_type=payload.fuel_type,
        district=payload.district,
        city=payload.city,
        location_id=payload.location_id,
        cancellation_policy=payload.cancellation_policy,
        free_cancellation_hours=payload.free_cancellation_hours,
        helmet_count=payload.helmet_count,
        helmets_included=payload.helmets_included,
        helmet_sizes=payload.helmet_sizes,
        security_deposit=payload.security_deposit,
        fuel_policy=payload.fuel_policy,
    )

    try:
        db.add(new_vehicle)
        db.commit()
        db.refresh(new_vehicle)
    except Exception:
        db.rollback()
        raise

    return build_vehicle_detail_response(new_vehicle)


# ==============================================================================
# SP-03: MANAGE RENTAL VEHICLES (FLEET LIST & DETAIL)
# ==============================================================================

@router.get(
    "/vehicles",
    response_model=ProviderVehiclePagination,
    summary="List provider's rental fleet with pagination and filters (SP-03)",
)
def list_provider_vehicles(
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(None, description="Filter by status: available, unavailable, retired"),
    approval_status: str | None = Query(None, description="Filter by approval status: pending, approved, rejected"),
    vehicle_type: VehicleType | None = Query(None, description="Filter by vehicle type: bike, bicycle, tuk_tuk"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
):
    query = db.query(RentalVehicle).filter(RentalVehicle.provider_id == provider.id)

    if status:
        query = query.filter(RentalVehicle.status == status)
    if approval_status:
        query = query.filter(RentalVehicle.approval_status == approval_status)
    if vehicle_type:
        query = query.filter(RentalVehicle.vehicle_type == vehicle_type)

    total = query.count()
    offset = (page - 1) * limit
    vehicles = query.order_by(RentalVehicle.id.desc()).offset(offset).limit(limit).all()

    now = utc_now()
    items = []
    for v in vehicles:
        sorted_images = sorted(v.images or [], key=lambda img: (img.display_order, img.id))
        primary_url = get_primary_image_url(sorted_images)

        # Count active / upcoming bookings for this vehicle
        active_count = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == v.id,
                RentalBooking.status.in_(["pending", "confirmed", "active"]),
                or_(
                    RentalBooking.status != "pending",
                    RentalBooking.expires_at.is_(None),
                    RentalBooking.expires_at > now,
                ),
            )
            .count()
        )

        items.append(
            ProviderVehicleListItem(
                id=v.id,
                provider_id=v.provider_id,
                vehicle_type=v.vehicle_type,
                vehicle_name=v.vehicle_name,
                description=v.description,
                price_per_day=v.price_per_day,
                currency="LKR",
                status=v.status,
                approval_status=v.approval_status,
                verification_status=v.verification_status,
                model_year=v.model_year,
                license_plate=v.license_plate,
                seating_capacity=v.seating_capacity,
                transmission=v.transmission,
                fuel_type=v.fuel_type,
                district=v.district,
                city=v.city,
                location_id=v.location_id,
                primary_image_url=primary_url,
                image_count=len(sorted_images),
                active_booking_count=active_count,
                cancellation_policy=v.cancellation_policy,
                free_cancellation_hours=v.free_cancellation_hours,
                helmet_count=getattr(v, "helmet_count", None),
                helmets_included=getattr(v, "helmets_included", None),
                helmet_sizes=getattr(v, "helmet_sizes", None),
                security_deposit=getattr(v, "security_deposit", None),
                fuel_policy=getattr(v, "fuel_policy", None),
                created_at=v.created_at,
                updated_at=v.updated_at,
            )
        )

    pages = ceil(total / limit) if limit else 1

    return ProviderVehiclePagination(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


@router.get(
    "/vehicles/{vehicle_id}",
    response_model=ProviderVehicleDetailResponse,
    summary="Get single vehicle details for provider (SP-03)",
)
def get_provider_vehicle_detail(
    vehicle_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)
    return build_vehicle_detail_response(vehicle)


@router.patch(
    "/vehicles/{vehicle_id}",
    response_model=ProviderVehicleDetailResponse,
    summary="Update vehicle specifications, pricing, and availability status (SP-03, SP-05, SP-06)",
)
def update_provider_vehicle(
    vehicle_id: int,
    payload: ProviderVehicleUpdate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)
    update_data = payload.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fields provided for update",
        )

    # Check retirement guard if setting status to 'retired'
    if update_data.get("status") == "retired":
        conflicts = find_active_booking_conflicts(vehicle.id, db)
        if conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot retire vehicle: {len(conflicts)} active or upcoming booking(s) exist.",
            )

    # Validate combined category specs after update
    target_vtype = update_data.get("vehicle_type", vehicle.vehicle_type)
    target_seats = update_data.get("seating_capacity", vehicle.seating_capacity)
    target_fuel = (update_data.get("fuel_type") or vehicle.fuel_type or "").lower().strip()

    if target_vtype == "bicycle":
        if target_seats > 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Bicycles cannot have a seating capacity greater than 2",
            )
        if target_fuel not in ("none", "electric", "pedal", "n/a"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Bicycles must have fuel_type as 'none', 'pedal', or 'electric'",
            )
    elif target_vtype == "tuk_tuk":
        if target_seats < 3 or target_seats > 4:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tuk-tuks must have a legal passenger seating capacity between 3 and 4",
            )
    elif target_vtype == "bike":
        if target_seats > 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Motorbikes / scooters cannot exceed a seating capacity of 2",
            )

    # Apply updates
    for field, value in update_data.items():
        if field in {"approval_status", "verification_status", "provider_id", "id"}:
            continue  # Protected fields
        setattr(vehicle, field, value)

    try:
        db.commit()
        db.refresh(vehicle)
    except Exception:
        db.rollback()
        raise

    return build_vehicle_detail_response(vehicle)


@router.delete(
    "/vehicles/{vehicle_id}",
    summary="Soft-retire a vehicle (SP-03)",
)
def retire_provider_vehicle(
    vehicle_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Safely retires a vehicle (status='retired').
    Strict safety check: Blocks retirement if any active, confirmed, or unexpired pending bookings exist.
    """
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    # Active booking guard
    conflicts = find_active_booking_conflicts(vehicle.id, db)
    if conflicts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retire vehicle: {len(conflicts)} active or upcoming booking(s) exist.",
        )

    vehicle.status = "retired"

    try:
        db.commit()
        db.refresh(vehicle)
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Vehicle successfully retired",
        "vehicle_id": vehicle.id,
        "status": "retired",
    }


# ==============================================================================
# SP-04: VEHICLE PHOTO MANAGEMENT
# ==============================================================================

@router.get(
    "/vehicles/{vehicle_id}/images",
    response_model=list[ProviderVehicleImageResponse],
    summary="List all photos for a vehicle (SP-04)",
)
def list_vehicle_images(
    vehicle_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)
    images = (
        db.query(RentalVehicleImage)
        .filter(RentalVehicleImage.vehicle_id == vehicle.id)
        .order_by(RentalVehicleImage.display_order.asc(), RentalVehicleImage.id.asc())
        .all()
    )
    return images


@router.post(
    "/vehicles/{vehicle_id}/images",
    response_model=ProviderVehicleImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a photo to vehicle gallery (SP-04)",
)
def add_vehicle_image(
    vehicle_id: int,
    payload: ProviderVehicleImageCreate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    existing_images = (
        db.query(RentalVehicleImage)
        .filter(RentalVehicleImage.vehicle_id == vehicle.id)
        .all()
    )

    # If first image or payload requests primary, make this primary and unset others
    is_primary = payload.is_primary or (len(existing_images) == 0)

    if is_primary:
        for img in existing_images:
            img.is_primary = False

    new_image = RentalVehicleImage(
        vehicle_id=vehicle.id,
        image_url=payload.image_url,
        caption=payload.caption,
        display_order=payload.display_order,
        is_primary=is_primary,
    )

    try:
        db.add(new_image)
        db.commit()
        db.refresh(new_image)
    except Exception:
        db.rollback()
        raise

    return new_image


@router.patch(
    "/vehicles/{vehicle_id}/images/{image_id}",
    response_model=ProviderVehicleImageResponse,
    summary="Update photo caption, display order, or primary status (SP-04)",
)
def update_vehicle_image(
    vehicle_id: int,
    image_id: int,
    payload: ProviderVehicleImageUpdate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    image = (
        db.query(RentalVehicleImage)
        .filter(
            RentalVehicleImage.id == image_id,
            RentalVehicleImage.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle image not found",
        )

    if payload.is_primary is True:
        # Unset primary from all other images of this vehicle
        db.query(RentalVehicleImage).filter(
            RentalVehicleImage.vehicle_id == vehicle.id,
            RentalVehicleImage.id != image.id,
        ).update({"is_primary": False})
        image.is_primary = True

    if payload.caption is not None:
        image.caption = payload.caption

    if payload.display_order is not None:
        image.display_order = payload.display_order

    try:
        db.commit()
        db.refresh(image)
    except Exception:
        db.rollback()
        raise

    return image


@router.delete(
    "/vehicles/{vehicle_id}/images/{image_id}",
    summary="Delete a photo from vehicle gallery (SP-04)",
)
def delete_vehicle_image(
    vehicle_id: int,
    image_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    image = (
        db.query(RentalVehicleImage)
        .filter(
            RentalVehicleImage.id == image_id,
            RentalVehicleImage.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle image not found",
        )

    was_primary = image.is_primary

    try:
        db.delete(image)
        db.flush()

        # If deleted photo was primary, auto-promote the next available image
        if was_primary:
            remaining = (
                db.query(RentalVehicleImage)
                .filter(RentalVehicleImage.vehicle_id == vehicle.id)
                .order_by(RentalVehicleImage.display_order.asc(), RentalVehicleImage.id.asc())
                .first()
            )
            if remaining:
                remaining.is_primary = True

        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Vehicle image deleted successfully",
        "image_id": image_id,
    }


# ==============================================================================
# SP-07: RENTAL PRICING MANAGEMENT (RATE PLANS)
# ==============================================================================

@router.get(
    "/vehicles/{vehicle_id}/rate-plans",
    response_model=list[ProviderRatePlanResponse],
    summary="List all rate plans for a vehicle (SP-07)",
)
def list_vehicle_rate_plans(
    vehicle_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)
    plans = (
        db.query(RentalRatePlan)
        .filter(RentalRatePlan.vehicle_id == vehicle.id)
        .order_by(RentalRatePlan.duration_type.asc(), RentalRatePlan.rate.asc())
        .all()
    )
    return plans


@router.post(
    "/vehicles/{vehicle_id}/rate-plans",
    response_model=ProviderRatePlanResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new rate plan for a vehicle (SP-07)",
)
def add_vehicle_rate_plan(
    vehicle_id: int,
    payload: ProviderRatePlanCreate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    # Reject conflicting active rate plan of same duration_type
    if payload.is_active:
        existing_active = (
            db.query(RentalRatePlan)
            .filter(
                RentalRatePlan.vehicle_id == vehicle.id,
                RentalRatePlan.duration_type == payload.duration_type,
                RentalRatePlan.is_active == True,
            )
            .first()
        )
        if existing_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An active '{payload.duration_type}' rate plan already exists for this vehicle (Plan ID: {existing_active.id}). Deactivate or update it instead.",
            )

    new_plan = RentalRatePlan(
        vehicle_id=vehicle.id,
        plan_name=payload.plan_name,
        duration_type=payload.duration_type,
        rate=payload.rate,
        currency=payload.currency,
        included_km_per_day=payload.included_km_per_day,
        extra_km_charge=payload.extra_km_charge,
        is_active=payload.is_active,
    )

    try:
        db.add(new_plan)
        db.commit()
        db.refresh(new_plan)
    except Exception:
        db.rollback()
        raise

    return new_plan


@router.patch(
    "/vehicles/{vehicle_id}/rate-plans/{plan_id}",
    response_model=ProviderRatePlanResponse,
    summary="Update a vehicle rate plan (SP-07)",
)
def update_vehicle_rate_plan(
    vehicle_id: int,
    plan_id: int,
    payload: ProviderRatePlanUpdate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    plan = (
        db.query(RentalRatePlan)
        .filter(
            RentalRatePlan.id == plan_id,
            RentalRatePlan.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rate plan not found",
        )

    target_duration = payload.duration_type or plan.duration_type
    target_active = payload.is_active if payload.is_active is not None else plan.is_active

    if target_active:
        conflict = (
            db.query(RentalRatePlan)
            .filter(
                RentalRatePlan.vehicle_id == vehicle.id,
                RentalRatePlan.duration_type == target_duration,
                RentalRatePlan.is_active == True,
                RentalRatePlan.id != plan.id,
            )
            .first()
        )
        if conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Another active '{target_duration}' rate plan already exists for this vehicle (Plan ID: {conflict.id}).",
            )

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(plan, field, value)

    try:
        db.commit()
        db.refresh(plan)
    except Exception:
        db.rollback()
        raise

    return plan


@router.delete(
    "/vehicles/{vehicle_id}/rate-plans/{plan_id}",
    summary="Delete a vehicle rate plan (SP-07)",
)
def delete_vehicle_rate_plan(
    vehicle_id: int,
    plan_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    plan = (
        db.query(RentalRatePlan)
        .filter(
            RentalRatePlan.id == plan_id,
            RentalRatePlan.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rate plan not found",
        )

    try:
        db.delete(plan)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Rate plan deleted successfully",
        "plan_id": plan_id,
    }


# ==============================================================================
# SP-08: AVAILABILITY & BLACKOUT MANAGEMENT
# ==============================================================================

@router.get(
    "/vehicles/{vehicle_id}/blocks",
    response_model=list[ProviderAvailabilityBlockResponse],
    summary="List all availability/maintenance blocks for a vehicle (SP-08)",
)
def list_vehicle_availability_blocks(
    vehicle_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    is_active: bool | None = Query(None, description="Filter by active status"),
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    query = db.query(RentalAvailabilityBlock).filter(RentalAvailabilityBlock.vehicle_id == vehicle.id)
    if is_active is not None:
        query = query.filter(RentalAvailabilityBlock.is_active == is_active)

    blocks = query.order_by(RentalAvailabilityBlock.start_datetime.asc()).all()
    return blocks


@router.post(
    "/vehicles/{vehicle_id}/blocks",
    response_model=ProviderAvailabilityBlockResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a maintenance or blackout period for a vehicle (SP-08)",
)
def create_vehicle_availability_block(
    vehicle_id: int,
    payload: ProviderAvailabilityBlockCreate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    start_norm = normalize_utc(payload.start_datetime)
    end_norm = normalize_utc(payload.end_datetime)

    if end_norm <= start_norm:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_datetime must be strictly greater than start_datetime",
        )

    # Collision guard with confirmed/active bookings (Requirement 5)
    if payload.is_active:
        now = utc_now()
        conflicting_bookings = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status.in_(["pending", "confirmed", "active"]),
                RentalBooking.pickup_at < end_norm,
                RentalBooking.return_at > start_norm,
            )
            .all()
        )
        real_conflicts = [
            b for b in conflicting_bookings
            if not (b.status == "pending" and b.expires_at and b.expires_at < now)
        ]
        if real_conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot block availability: overlaps with {len(real_conflicts)} active or confirmed booking(s).",
            )

    new_block = RentalAvailabilityBlock(
        vehicle_id=vehicle.id,
        start_datetime=start_norm,
        end_datetime=end_norm,
        block_type=payload.block_type,
        reason=payload.reason,
        is_active=payload.is_active,
    )

    try:
        db.add(new_block)
        db.commit()
        db.refresh(new_block)
    except Exception:
        db.rollback()
        raise

    return new_block


@router.patch(
    "/vehicles/{vehicle_id}/blocks/{block_id}",
    response_model=ProviderAvailabilityBlockResponse,
    summary="Update a maintenance or blackout block (SP-08)",
)
def update_vehicle_availability_block(
    vehicle_id: int,
    block_id: int,
    payload: ProviderAvailabilityBlockUpdate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    block = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.id == block_id,
            RentalAvailabilityBlock.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not block:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability block not found",
        )

    target_start = normalize_utc(payload.start_datetime) if payload.start_datetime else block.start_datetime
    target_end = normalize_utc(payload.end_datetime) if payload.end_datetime else block.end_datetime
    target_active = payload.is_active if payload.is_active is not None else block.is_active

    if target_end <= target_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_datetime must be strictly greater than start_datetime",
        )

    if target_active:
        now = utc_now()
        conflicting_bookings = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status.in_(["pending", "confirmed", "active"]),
                RentalBooking.pickup_at < target_end,
                RentalBooking.return_at > target_start,
            )
            .all()
        )
        real_conflicts = [
            b for b in conflicting_bookings
            if not (b.status == "pending" and b.expires_at and b.expires_at < now)
        ]
        if real_conflicts:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot update block: overlaps with {len(real_conflicts)} active or confirmed booking(s).",
            )

    update_data = payload.model_dump(exclude_unset=True)
    if "start_datetime" in update_data and update_data["start_datetime"]:
        update_data["start_datetime"] = target_start
    if "end_datetime" in update_data and update_data["end_datetime"]:
        update_data["end_datetime"] = target_end

    for field, value in update_data.items():
        setattr(block, field, value)

    try:
        db.commit()
        db.refresh(block)
    except Exception:
        db.rollback()
        raise

    return block


@router.delete(
    "/vehicles/{vehicle_id}/blocks/{block_id}",
    summary="Delete an availability or blackout block (SP-08)",
)
def delete_vehicle_availability_block(
    vehicle_id: int,
    block_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    vehicle = get_provider_vehicle_or_404(vehicle_id, provider.id, db)

    block = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.id == block_id,
            RentalAvailabilityBlock.vehicle_id == vehicle.id,
        )
        .first()
    )

    if not block:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Availability block not found",
        )

    try:
        db.delete(block)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Availability block deleted successfully",
        "block_id": block_id,
    }


# ==============================================================================
# SP-09: PICKUP & RETURN LOCATION MANAGEMENT
# ==============================================================================

@router.get(
    "/locations",
    response_model=list[ProviderLocationResponse],
    summary="List all pickup/return stations for the provider (SP-09)",
)
def list_provider_locations(
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    is_active: bool | None = Query(None, description="Filter by active status"),
):
    query = db.query(RentalLocation).filter(RentalLocation.provider_id == provider.id)
    if is_active is not None:
        query = query.filter(RentalLocation.is_active == is_active)

    locations = query.order_by(
        RentalLocation.district.asc(),
        RentalLocation.city.asc(),
        RentalLocation.location_name.asc(),
    ).all()
    return locations


@router.get(
    "/locations/{location_id}",
    response_model=ProviderLocationResponse,
    summary="Get single pickup/return station details (SP-09)",
)
def get_provider_location(
    location_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    location = (
        db.query(RentalLocation)
        .filter(
            RentalLocation.id == location_id,
            RentalLocation.provider_id == provider.id,
        )
        .first()
    )
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental location not found or access denied",
        )
    return location


@router.post(
    "/locations",
    response_model=ProviderLocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a designated rental pickup/return location (SP-09)",
)
def create_provider_location(
    payload: ProviderLocationCreate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    new_location = RentalLocation(
        provider_id=provider.id,
        location_name=payload.location_name,
        district=payload.district,
        city=payload.city,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
        is_active=payload.is_active,
    )

    try:
        db.add(new_location)
        db.commit()
        db.refresh(new_location)
    except Exception:
        db.rollback()
        raise

    return new_location


@router.patch(
    "/locations/{location_id}",
    response_model=ProviderLocationResponse,
    summary="Update a rental pickup/return location (SP-09)",
)
def update_provider_location(
    location_id: int,
    payload: ProviderLocationUpdate,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    location = (
        db.query(RentalLocation)
        .filter(
            RentalLocation.id == location_id,
            RentalLocation.provider_id == provider.id,
        )
        .first()
    )
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental location not found or access denied",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # Check GPS coordinates pair validity
    target_lat = update_data.get("latitude", location.latitude)
    target_lng = update_data.get("longitude", location.longitude)
    if (target_lat is None and target_lng is not None) or (target_lat is not None and target_lng is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both latitude and longitude must be provided together, or both omitted",
        )

    for field, value in update_data.items():
        setattr(location, field, value)

    try:
        db.commit()
        db.refresh(location)
    except Exception:
        db.rollback()
        raise

    return location


@router.delete(
    "/locations/{location_id}",
    summary="Delete or deactivate a rental location (SP-09)",
)
def delete_provider_location(
    location_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    location = (
        db.query(RentalLocation)
        .filter(
            RentalLocation.id == location_id,
            RentalLocation.provider_id == provider.id,
        )
        .first()
    )
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental location not found or access denied",
        )

    # Check if vehicles currently reference this location
    referenced_vehicles = (
        db.query(RentalVehicle)
        .filter(RentalVehicle.location_id == location.id)
        .count()
    )

    if referenced_vehicles > 0:
        # Prevent broken foreign keys; deactivate location
        location.is_active = False
        db.commit()
        return {
            "message": f"Location cannot be permanently deleted because {referenced_vehicles} vehicle(s) are stationed there. It has been deactivated instead.",
            "location_id": location_id,
            "is_active": False,
        }

    try:
        db.delete(location)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "message": "Rental location deleted successfully",
        "location_id": location_id,
    }


# ==============================================================================
# SP-10: PROVIDER BOOKING REQUEST INBOX
# ==============================================================================

@router.get(
    "/bookings/requests",
    response_model=ProviderBookingRequestPagination,
    summary="Get pending rental booking requests with hold expiration and urgency indicators (SP-10)",
)
def get_provider_booking_requests(
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    urgency: str | None = Query(None, description="Filter by urgency: critical, urgent, normal, expired"),
    vehicle_id: int | None = Query(None, description="Filter by specific vehicle ID"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
):
    now = utc_now()
    query = (
        db.query(RentalBooking, RentalVehicle, User)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .join(User, RentalBooking.user_id == User.id)
        .filter(
            RentalVehicle.provider_id == provider.id,
            RentalBooking.status == "pending",
        )
    )

    if vehicle_id is not None:
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)

    raw_results = query.order_by(RentalBooking.created_at.desc()).all()

    processed_items = []
    for b, v, u in raw_results:
        time_rem = None
        if b.expires_at:
            delta = int((b.expires_at - now).total_seconds())
            if delta <= 0:
                urg = "expired"
                time_rem = 0
            elif delta <= 1800:
                urg = "critical"
                time_rem = delta
            elif delta <= 7200:
                urg = "urgent"
                time_rem = delta
            else:
                urg = "normal"
                time_rem = delta
        else:
            urg = "normal"

        if urgency and urg != urgency:
            continue

        ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental_vehicle",
                BookingReference.target_booking_id == b.id,
            )
            .first()
        )

        days = max(1, (b.return_at - b.pickup_at).days or 1)
        processed_items.append(
            ProviderBookingRequestItem(
                id=b.id,
                booking_reference=ref.booking_reference if ref else None,
                vehicle_id=v.id,
                vehicle_name=v.vehicle_name,
                vehicle_type=v.vehicle_type,
                license_plate=v.license_plate,
                traveler_id=u.id,
                traveler_name=u.full_name or "Traveler",
                traveler_email=u.email,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_days=days,
                daily_rate=b.daily_rate_snapshot or v.price_per_day,
                total_price=b.total_price,
                currency=b.currency,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                status=b.status,
                expires_at=b.expires_at,
                urgency=urg,
                time_remaining_seconds=time_rem,
                created_at=b.created_at,
            )
        )

    total = len(processed_items)
    offset = (page - 1) * limit
    paginated_items = processed_items[offset : offset + limit]
    pages = ceil(total / limit) if limit else 1

    return ProviderBookingRequestPagination(
        items=paginated_items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


# ==============================================================================
# SP-11: BOOKING APPROVAL & REJECTION
# ==============================================================================

@router.post(
    "/bookings/{booking_id}/accept",
    response_model=ProviderBookingActionResponse,
    summary="Accept a pending rental booking request (SP-11)",
)
def accept_provider_booking(
    booking_id: int,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot accept booking in '{booking.status}' status. Only pending bookings can be accepted.",
        )

    now = utc_now()
    if booking.expires_at and booking.expires_at <= now:
        booking.status = "expired"
        booking_ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental_vehicle",
                BookingReference.target_booking_id == booking.id,
            )
            .first()
        )
        if booking_ref:
            booking_ref.booking_status = "expired"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Booking request has expired and cannot be accepted.",
        )

    booking.status = "confirmed"

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        booking_ref.booking_status = "confirmed"

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Rental Booking #{booking.id} Confirmed",
        message=f"Your booking for {vehicle.vehicle_name} from {booking.pickup_at.strftime('%Y-%m-%d')} has been confirmed by the provider.",
        notification_type="rental_booking_confirmed",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(booking)

    return ProviderBookingActionResponse(
        booking_id=booking.id,
        status="confirmed",
        message=f"Booking #{booking.id} successfully confirmed.",
        action_at=now,
    )


@router.post(
    "/bookings/{booking_id}/reject",
    response_model=ProviderBookingActionResponse,
    summary="Decline a pending rental booking request (SP-11)",
)
def reject_provider_booking(
    booking_id: int,
    payload: ProviderBookingRejectRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot decline booking in '{booking.status}' status. Only pending bookings can be declined.",
        )

    now = utc_now()
    booking.status = "rejected"
    booking.cancellation_reason = payload.reason
    booking.cancelled_by = provider.user_id
    booking.cancelled_at = now

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        booking_ref.booking_status = "rejected"

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Rental Booking #{booking.id} Declined",
        message=f"Your booking for {vehicle.vehicle_name} was declined: {payload.reason}",
        notification_type="rental_booking_rejected",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(booking)

    return ProviderBookingActionResponse(
        booking_id=booking.id,
        status="rejected",
        message=f"Booking #{booking.id} was declined.",
        action_at=now,
        cancellation_reason=payload.reason,
    )


# ==============================================================================
# SP-12: PROVIDER BOOKING HISTORY
# ==============================================================================

@router.get(
    "/bookings",
    response_model=ProviderBookingHistoryPagination,
    summary="Provider booking history archive with filters & pagination (SP-12)",
)
def get_provider_booking_history(
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = Query(None, description="Filter by status (pending, confirmed, active, completed, rejected, cancelled, expired)"),
    vehicle_id: int | None = Query(None, description="Filter by vehicle ID"),
    payment_status: str | None = Query(None, description="Filter by payment status (unpaid, paid, partially_paid)"),
    start_date: datetime | None = Query(None, description="Filter pickup on or after date"),
    end_date: datetime | None = Query(None, description="Filter pickup on or before date"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
):
    query = (
        db.query(RentalBooking, RentalVehicle, User)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .join(User, RentalBooking.user_id == User.id)
        .filter(RentalVehicle.provider_id == provider.id)
    )

    if status:
        query = query.filter(RentalBooking.status == status)
    if vehicle_id is not None:
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)
    if payment_status:
        query = query.filter(RentalBooking.payment_status == payment_status)
    if start_date:
        query = query.filter(RentalBooking.pickup_at >= normalize_utc(start_date))
    if end_date:
        query = query.filter(RentalBooking.pickup_at <= normalize_utc(end_date))

    total = query.count()
    offset = (page - 1) * limit
    results = query.order_by(RentalBooking.id.desc()).offset(offset).limit(limit).all()

    items = []
    for b, v, u in results:
        ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental_vehicle",
                BookingReference.target_booking_id == b.id,
            )
            .first()
        )
        items.append(
            ProviderBookingHistoryItem(
                id=b.id,
                booking_reference=ref.booking_reference if ref else None,
                vehicle_id=v.id,
                vehicle_name=v.vehicle_name,
                vehicle_type=v.vehicle_type,
                traveler_id=u.id,
                traveler_name=u.full_name or "Traveler",
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_price=b.total_price,
                currency=b.currency,
                status=b.status,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                created_at=b.created_at,
            )
        )

    pages = ceil(total / limit) if limit else 1

    return ProviderBookingHistoryPagination(
        items=items,
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )


# ==============================================================================
# SP-13: DIGITAL VEHICLE HANDOVER
# ==============================================================================

@router.post(
    "/bookings/{booking_id}/handover",
    response_model=ProviderHandoverResponse,
    summary="Digital vehicle handover inspection and status transition to active (SP-13)",
)
def record_vehicle_handover(
    booking_id: int,
    payload: ProviderHandoverInspectionRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.status == "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vehicle handover has already been completed for this booking.",
        )
    if booking.status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Handover can only be completed for 'confirmed' bookings. Current status is '{booking.status}'.",
        )

    # AR-01, AR-02, AR-11: Recheck driving eligibility before vehicle handover (Condition 7)
    driver_verification_msg = "Exempt (Bicycle)"
    if vehicle.vehicle_type != "bicycle":
        eligibility = (
            db.query(RentalEligibilityCheck)
            .filter(RentalEligibilityCheck.user_id == booking.user_id)
            .first()
        )
        if not eligibility or eligibility.verification_status != "approved":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Handover cannot proceed: Renter driving license verification is missing or not approved.",
            )
        if eligibility.license_expiry_date < booking.return_at.date():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Handover cannot proceed: Renter driving license expired on {eligibility.license_expiry_date}, which is before the rental return date ({booking.return_at.date()}).",
            )
        if vehicle.vehicle_type == "tuk_tuk" and eligibility.license_country.strip().lower() != "sri lanka":
            if not eligibility.idp_number:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Handover cannot proceed: Foreign traveler renting a tuk-tuk requires an International Driving Permit (IDP) and Sri Lankan recognition permit endorsement.",
                )
        driver_verification_msg = f"Verified (License: {eligibility.license_country} - Approved)"

    existing_insp = (
        db.query(RentalInspection)
        .filter(
            RentalInspection.booking_id == booking.id,
            RentalInspection.inspection_type == "pickup",
        )
        .first()
    )
    if existing_insp:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pickup inspection has already been recorded for this booking.",
        )

    now = utc_now()
    insp_notes = payload.notes or ""
    if payload.helmet_count is not None:
        sizes_str = ",".join(payload.helmet_sizes) if payload.helmet_sizes else ""
        cond_str = payload.helmet_condition or "Good"
        helmet_tag = f"[Helmets: count={payload.helmet_count}, sizes={sizes_str}, condition={cond_str}]"
        insp_notes = f"{insp_notes} {helmet_tag}".strip()

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="pickup",
        odometer_reading=payload.odometer_reading,
        fuel_level_percent=payload.fuel_level_percent,
        cleanliness_status=payload.cleanliness_status,
        inspected_by=provider.user_id,
        customer_signature=payload.customer_signature,
        notes=insp_notes if insp_notes else None,
    )
    db.add(inspection)
    db.flush()

    evidence_resps = []
    for ev in payload.evidence:
        ev_obj = RentalConditionEvidence(
            inspection_id=inspection.id,
            evidence_type=ev.evidence_type,
            file_url=ev.file_url,
            area_of_vehicle=ev.area_of_vehicle,
            description=ev.description,
        )
        db.add(ev_obj)
        db.flush()
        evidence_resps.append(
            ProviderEvidenceResponse(
                id=ev_obj.id,
                inspection_id=ev_obj.inspection_id,
                evidence_type=ev_obj.evidence_type,
                file_url=ev_obj.file_url,
                area_of_vehicle=ev_obj.area_of_vehicle,
                description=ev_obj.description,
                created_at=ev_obj.created_at,
            )
        )

    booking.status = "active"

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        booking_ref.booking_status = "active"

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Vehicle Handover Complete — Rental #{booking.id} Active",
        message=f"Handover for {vehicle.vehicle_name} is complete. Your rental is now active. Safe travels!",
        notification_type="rental_booking_active",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(inspection)

    insp_resp = ProviderInspectionResponse(
        id=inspection.id,
        booking_id=inspection.booking_id,
        inspection_type=inspection.inspection_type,
        odometer_reading=inspection.odometer_reading,
        fuel_level_percent=inspection.fuel_level_percent,
        cleanliness_status=inspection.cleanliness_status,
        inspected_by=inspection.inspected_by,
        customer_signature=inspection.customer_signature,
        notes=inspection.notes,
        evidence=evidence_resps,
        created_at=inspection.created_at,
    )

    return ProviderHandoverResponse(
        booking_id=booking.id,
        status="active",
        handover_completed_at=now,
        message=f"Handover completed successfully for booking #{booking.id}.",
        inspection=insp_resp,
        driver_verification=driver_verification_msg,
    )


# ==============================================================================
# SP-14: DIGITAL VEHICLE RETURN
# ==============================================================================

@router.post(
    "/bookings/{booking_id}/return",
    response_model=ProviderReturnResponse,
    summary="Digital vehicle return inspection and status transition to completed (SP-14)",
)
def record_vehicle_return(
    booking_id: int,
    payload: ProviderReturnInspectionRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vehicle return has already been completed for this booking.",
        )
    if booking.status != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Vehicle return can only be performed on 'active' bookings (after valid handover). Current status is '{booking.status}'.",
        )

    pickup_insp = (
        db.query(RentalInspection)
        .filter(
            RentalInspection.booking_id == booking.id,
            RentalInspection.inspection_type == "pickup",
        )
        .first()
    )

    if pickup_insp and payload.odometer_reading < pickup_insp.odometer_reading:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Return odometer reading ({payload.odometer_reading}) cannot be less than pickup reading ({pickup_insp.odometer_reading}).",
        )

    pickup_odo = pickup_insp.odometer_reading if pickup_insp else payload.odometer_reading
    km_driven = payload.odometer_reading - pickup_odo

    excess_km = 0
    excess_km_charge = Decimal("0.00")
    rate_plan = (
        db.query(RentalRatePlan)
        .filter(
            RentalRatePlan.vehicle_id == vehicle.id,
            RentalRatePlan.is_active == True,
        )
        .first()
    )

    if rate_plan and rate_plan.included_km_per_day is not None and rate_plan.extra_km_charge is not None:
        duration_days = max(1, (booking.return_at - booking.pickup_at).days or 1)
        allowed_km = rate_plan.included_km_per_day * duration_days
        if km_driven > allowed_km:
            excess_km = km_driven - allowed_km
            excess_km_charge = Decimal(str(excess_km)) * rate_plan.extra_km_charge

    pickup_fuel = pickup_insp.fuel_level_percent if pickup_insp else 100
    fuel_diff = payload.fuel_level_percent - pickup_fuel

    # AR-05: Disclosed fuel policy evaluation
    if vehicle.vehicle_type == "bicycle" or (vehicle.fuel_type and vehicle.fuel_type.lower() == "electric"):
        fuel_policy = "not_applicable"
        fuel_diff = 0
        fuel_deficit = 0
    elif vehicle.description and "full_to_full" in vehicle.description.lower().replace("-", "_"):
        fuel_policy = "full_to_full"
        fuel_deficit = max(0, 100 - payload.fuel_level_percent)
    else:
        fuel_policy = "same_to_same"
        fuel_deficit = max(0, pickup_fuel - payload.fuel_level_percent)

    now = utc_now()
    scheduled_return = normalize_utc(booking.return_at)
    return_time = normalize_utc(payload.actual_return_time) if payload.actual_return_time else now

    is_late = False
    late_minutes = 0
    grace_period_applied = False
    billable_late_hours = 0
    late_hourly_rate = None
    late_return_fee = Decimal("0.00")
    late_fee_notes = "Vehicle returned on or before scheduled return time."

    if return_time > scheduled_return:
        lateness_seconds = (return_time - scheduled_return).total_seconds()
        late_minutes = int(lateness_seconds // 60)
        is_late = True

        # AR-07: 60-minute deducted grace period policy
        # Billable late hours = ceil(max(0, lateness_seconds - 3600) / 3600)
        excess_seconds = max(0.0, lateness_seconds - 3600.0)

        if excess_seconds == 0.0:
            grace_period_applied = True
            billable_late_hours = 0
            late_hourly_rate = None
            late_return_fee = Decimal("0.00")
            late_fee_notes = f"Returned {late_minutes} minutes late (within 1-hour grace period). No late fee assessed."
        else:
            grace_period_applied = False
            billable_late_hours = ceil(excess_seconds / 3600.0)
            hourly_plan = (
                db.query(RentalRatePlan)
                .filter(
                    RentalRatePlan.vehicle_id == vehicle.id,
                    RentalRatePlan.duration_type == "hourly",
                    RentalRatePlan.is_active == True,
                )
                .first()
            )
            if hourly_plan and hourly_plan.rate and hourly_plan.rate > Decimal("0.00"):
                late_hourly_rate = hourly_plan.rate
                late_return_fee = Decimal(str(billable_late_hours)) * hourly_plan.rate
                late_fee_notes = f"Returned {late_minutes} minutes late ({billable_late_hours} billable hour(s) beyond 60-minute grace period at {late_hourly_rate} LKR/hr). Total late fee: {late_return_fee} LKR."
            else:
                late_hourly_rate = None
                late_return_fee = Decimal("0.00")
                late_fee_notes = f"Returned {late_minutes} minutes late ({billable_late_hours} billable hour(s) beyond 60-minute grace period), but no approved hourly rate plan is configured for vehicle #{vehicle.id}. Lateness logged without monetary penalty."

    inspection = RentalInspection(
        booking_id=booking.id,
        inspection_type="return",
        odometer_reading=payload.odometer_reading,
        fuel_level_percent=payload.fuel_level_percent,
        cleanliness_status=payload.cleanliness_status,
        inspected_by=provider.user_id,
        customer_signature=payload.customer_signature,
        notes=payload.notes,
    )
    db.add(inspection)
    db.flush()

    evidence_resps = []
    for ev in payload.evidence:
        ev_obj = RentalConditionEvidence(
            inspection_id=inspection.id,
            evidence_type=ev.evidence_type,
            file_url=ev.file_url,
            area_of_vehicle=ev.area_of_vehicle,
            description=ev.description,
        )
        db.add(ev_obj)
        db.flush()
        evidence_resps.append(
            ProviderEvidenceResponse(
                id=ev_obj.id,
                inspection_id=ev_obj.inspection_id,
                evidence_type=ev_obj.evidence_type,
                file_url=ev_obj.file_url,
                area_of_vehicle=ev_obj.area_of_vehicle,
                description=ev_obj.description,
                created_at=ev_obj.created_at,
            )
        )

    booking.status = "completed"

    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        booking_ref.booking_status = "completed"
        return_audit_notes = f"Vehicle return completed. km_driven={km_driven}, excess_km={excess_km}, fuel_policy={fuel_policy}."
        if is_late:
            return_audit_notes += f" {late_fee_notes}"
        status_history = BookingStatusHistory(
            booking_reference_id=booking_ref.id,
            from_status="active",
            to_status="completed",
            changed_by=provider.user_id,
            notes=return_audit_notes,
        )
        db.add(status_history)

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Rental Booking #{booking.id} Completed",
        message=f"Return inspection for {vehicle.vehicle_name} has been processed. Thank you for renting with CeylonGo!",
        notification_type="rental_booking_completed",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(inspection)

    insp_resp = ProviderInspectionResponse(
        id=inspection.id,
        booking_id=inspection.booking_id,
        inspection_type=inspection.inspection_type,
        odometer_reading=inspection.odometer_reading,
        fuel_level_percent=inspection.fuel_level_percent,
        cleanliness_status=inspection.cleanliness_status,
        inspected_by=inspection.inspected_by,
        customer_signature=inspection.customer_signature,
        notes=inspection.notes,
        evidence=evidence_resps,
        created_at=inspection.created_at,
    )

    return ProviderReturnResponse(
        booking_id=booking.id,
        status="completed",
        return_completed_at=now,
        km_driven=km_driven,
        excess_km=excess_km,
        excess_km_charge=excess_km_charge,
        fuel_policy=fuel_policy,
        pickup_fuel_percent=pickup_fuel,
        return_fuel_percent=payload.fuel_level_percent,
        fuel_difference_percent=fuel_diff,
        fuel_deficit_percent=fuel_deficit,
        is_late=is_late,
        late_minutes=late_minutes,
        grace_period_applied=grace_period_applied,
        billable_late_hours=billable_late_hours,
        late_hourly_rate=late_hourly_rate,
        late_return_fee=late_return_fee,
        late_fee_notes=late_fee_notes,
        message=f"Vehicle return completed successfully for booking #{booking.id}.",
        inspection=insp_resp,
    )


# ==============================================================================
# SP-15: CASH PAYMENT RECORDING
# ==============================================================================

@router.post(
    "/bookings/{booking_id}/record-cash",
    response_model=ProviderCashPaymentResponse,
    summary="Record cash collected from traveler at vehicle pickup (SP-15)",
)
def record_cash_payment(
    booking_id: int,
    payload: ProviderRecordCashPaymentRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Booking is already marked as fully paid.",
        )

    outstanding = booking.total_price
    if payload.amount > outstanding:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Payment amount ({payload.amount} {booking.currency}) exceeds outstanding booking balance ({outstanding} {booking.currency}). Overpayments are not allowed.",
        )

    now = utc_now()
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )

    tx_code = f"CASH-{booking.id}-{int(now.timestamp())}"
    if booking_ref:
        tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=booking.user_id,
            gateway_name="cash_on_pickup",
            gateway_transaction_id=tx_code,
            amount=payload.amount,
            currency=booking.currency,
            payment_status="success",
            payment_method="cash",
            paid_at=now,
        )
        db.add(tx)

    if payload.amount == outstanding:
        booking.payment_status = "paid"
        if booking_ref:
            booking_ref.payment_status = "paid"
    else:
        booking.payment_status = "partially_paid"

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Payment Receipt: Rental #{booking.id}",
        message=f"Cash payment of {payload.amount} {booking.currency} recorded for rental {vehicle.vehicle_name}. Status: {booking.payment_status}.",
        notification_type="rental_payment_received",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(booking)

    return ProviderCashPaymentResponse(
        booking_id=booking.id,
        amount_received=payload.amount,
        currency=booking.currency,
        payment_status=booking.payment_status,
        gateway_transaction_id=tx_code,
        paid_at=now,
        message=f"Cash payment of {payload.amount} {booking.currency} recorded successfully.",
    )


# ==============================================================================
# AR-04: SECURITY DEPOSIT MANAGEMENT (COLLECT & RESOLVE)
# ==============================================================================

@router.post(
    "/bookings/{booking_id}/deposit/collect",
    response_model=RentalDepositResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record security deposit collected from traveler at vehicle pickup (AR-04)",
)
def collect_security_deposit(
    booking_id: int,
    payload: RentalDepositCollectRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    if booking.status in ("cancelled", "completed", "rejected", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot collect security deposit for a booking in '{booking.status}' status.",
        )

    # Check for active held deposit (prevent duplicate collection)
    existing_held = (
        db.query(RentalDeposit)
        .filter(
            RentalDeposit.booking_id == booking.id,
            RentalDeposit.deposit_status == "held",
        )
        .first()
    )
    if existing_held:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A security deposit of {existing_held.deposit_amount} {existing_held.currency} is already held for booking #{booking.id}. Duplicate collection is not permitted.",
        )

    # Determine deposit amount
    if payload.amount is not None and payload.amount > Decimal("0.00"):
        deposit_amount = payload.amount
    elif getattr(vehicle, "security_deposit", None) is not None:
        deposit_amount = vehicle.security_deposit
    else:
        deposit_map = {
            "bike": Decimal("10000.00"),
            "tuk_tuk": Decimal("15000.00"),
            "bicycle": Decimal("3000.00"),
        }
        deposit_amount = deposit_map.get(vehicle.vehicle_type, Decimal("10000.00"))

    now = utc_now()
    deposit = RentalDeposit(
        booking_id=booking.id,
        deposit_amount=deposit_amount,
        currency=payload.currency or "LKR",
        deposit_status="held",
        deducted_amount=Decimal("0.00"),
        refunded_amount=Decimal("0.00"),
        held_at=now,
    )
    db.add(deposit)
    db.flush()

    # Audit trail in BookingStatusHistory
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        audit_note = f"Security deposit collected: {deposit_amount} {deposit.currency} (status: held)."
        if payload.notes:
            audit_note += f" Notes: {payload.notes.strip()}"
        status_history = BookingStatusHistory(
            booking_reference_id=booking_ref.id,
            from_status=booking.status,
            to_status=booking.status,
            changed_by=provider.user_id,
            notes=audit_note,
        )
        db.add(status_history)

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Security Deposit Collected: Booking #{booking.id}",
        message=f"A refundable security deposit of {deposit_amount} {deposit.currency} has been recorded for your rental of {vehicle.vehicle_name}.",
        notification_type="rental_deposit_collected",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(deposit)
    return deposit


@router.post(
    "/bookings/{booking_id}/deposit/resolve",
    response_model=RentalDepositResponse,
    status_code=status.HTTP_200_OK,
    summary="Record security deposit release or damage deduction at return (AR-04)",
)
def resolve_security_deposit(
    booking_id: int,
    payload: RentalDepositResolveRequest,
    provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    booking, vehicle = get_provider_booking_or_404(booking_id, provider.id, db)

    held_deposit = (
        db.query(RentalDeposit)
        .filter(
            RentalDeposit.booking_id == booking.id,
            RentalDeposit.deposit_status == "held",
        )
        .first()
    )
    if not held_deposit:
        # Check if already resolved
        resolved_deposit = (
            db.query(RentalDeposit)
            .filter(RentalDeposit.booking_id == booking.id)
            .order_by(RentalDeposit.held_at.desc())
            .first()
        )
        if resolved_deposit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Security deposit for booking #{booking.id} is already resolved with status '{resolved_deposit.deposit_status}'. Duplicate resolution is not permitted.",
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No held security deposit found for booking #{booking.id} to resolve.",
        )

    now = utc_now()
    if payload.action == "release":
        held_deposit.deposit_status = "fully_refunded"
        held_deposit.refunded_amount = held_deposit.deposit_amount
        held_deposit.deducted_amount = Decimal("0.00")
        held_deposit.deduction_reason = payload.deduction_reason or "Deposit refunded in full following clean return inspection."
        held_deposit.released_at = now
    elif payload.action == "deduct":
        if payload.deducted_amount <= Decimal("0.00"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="deducted_amount must be greater than 0.00 when action is 'deduct'.",
            )
        if payload.deducted_amount > held_deposit.deposit_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Deduction amount ({payload.deducted_amount}) cannot exceed the recorded deposit amount ({held_deposit.deposit_amount}).",
            )
        if not payload.deduction_reason or not payload.deduction_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="deduction_reason is mandatory when withholding an amount from the security deposit.",
            )
        held_deposit.deducted_amount = payload.deducted_amount
        held_deposit.refunded_amount = held_deposit.deposit_amount - payload.deducted_amount
        held_deposit.deduction_reason = payload.deduction_reason.strip()
        held_deposit.released_at = now
        if held_deposit.refunded_amount > Decimal("0.00"):
            held_deposit.deposit_status = "partially_refunded"
        else:
            held_deposit.deposit_status = "deducted"

    # Audit trail
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if booking_ref:
        status_history = BookingStatusHistory(
            booking_reference_id=booking_ref.id,
            from_status=booking.status,
            to_status=booking.status,
            changed_by=provider.user_id,
            notes=f"Security deposit resolved: status='{held_deposit.deposit_status}', deducted={held_deposit.deducted_amount}, refunded={held_deposit.refunded_amount}. Reason: {held_deposit.deduction_reason}",
        )
        db.add(status_history)

    create_notification(
        db=db,
        user_id=booking.user_id,
        title=f"Security Deposit Resolved: Booking #{booking.id}",
        message=f"Your security deposit for {vehicle.vehicle_name} has been resolved ({held_deposit.deposit_status}). Refunded: {held_deposit.refunded_amount} {held_deposit.currency}, Deducted: {held_deposit.deducted_amount} {held_deposit.currency}.",
        notification_type="rental_deposit_resolved",
        link_url=f"/traveler/rental-bookings/{booking.id}",
    )

    db.commit()
    db.refresh(held_deposit)
    return held_deposit


# ==============================================================================
# SP-01: RENTAL PROVIDER DASHBOARD
# ==============================================================================

@router.get(
    "/dashboard",
    response_model=RentalProviderDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Rental provider fleet, booking, utilization, and revenue dashboard",
)
def get_provider_dashboard(
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    now = utc_now()
    provider_id = current_provider.id

    # Fleet metrics
    vehicles = db.query(RentalVehicle).filter(RentalVehicle.provider_id == provider_id).all()
    total_vehicles = len(vehicles)
    available_vehicles = sum(1 for v in vehicles if v.status == "available" and v.approval_status == "approved")
    pending_approval_vehicles = sum(1 for v in vehicles if v.approval_status == "pending")
    maintenance_vehicles = sum(1 for v in vehicles if v.status == "maintenance")
    retired_vehicles = sum(1 for v in vehicles if v.status == "retired")

    vehicle_ids = [v.id for v in vehicles]

    # Booking metrics
    if vehicle_ids:
        bookings = (
            db.query(RentalBooking)
            .filter(RentalBooking.vehicle_id.in_(vehicle_ids))
            .all()
        )
    else:
        bookings = []

    total_bookings = len(bookings)
    pending_requests = sum(1 for b in bookings if b.status == "pending")
    confirmed_bookings = sum(1 for b in bookings if b.status == "confirmed")
    active_rentals = sum(1 for b in bookings if b.status == "active")
    completed_rentals = sum(1 for b in bookings if b.status == "completed")
    cancelled_bookings = sum(1 for b in bookings if b.status in ("cancelled", "rejected", "expired"))

    # Utilization
    utilization_rate = round((active_rentals / total_vehicles) * 100, 2) if total_vehicles > 0 else 0.0

    # Revenue metrics
    gross_revenue = sum(
        (b.total_price for b in bookings if b.status in ("confirmed", "active", "completed")),
        Decimal("0.00"),
    )

    # Online vs Cash payments from PaymentTransaction via BookingReference
    transactions = (
        db.query(PaymentTransaction)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.provider_profile_id == provider_id,
            PaymentTransaction.payment_status.in_(["paid", "completed", "success"]),
        )
        .all()
    )

    online_paid = sum(
        (t.amount for t in transactions if t.gateway_name != "cash_on_pickup"),
        Decimal("0.00"),
    )
    cash_collected = sum(
        (t.amount for t in transactions if t.gateway_name == "cash_on_pickup"),
        Decimal("0.00"),
    )

    return RentalProviderDashboardResponse(
        provider_id=provider_id,
        business_name=current_provider.business_name,
        fleet=RentalFleetMetrics(
            total_vehicles=total_vehicles,
            available_vehicles=available_vehicles,
            pending_approval_vehicles=pending_approval_vehicles,
            maintenance_vehicles=maintenance_vehicles,
            retired_vehicles=retired_vehicles,
        ),
        bookings=RentalBookingMetrics(
            total_bookings=total_bookings,
            pending_requests=pending_requests,
            confirmed_bookings=confirmed_bookings,
            active_rentals=active_rentals,
            completed_rentals=completed_rentals,
            cancelled_bookings=cancelled_bookings,
        ),
        utilization=RentalUtilizationMetrics(
            active_rentals_count=active_rentals,
            total_fleet_capacity=total_vehicles,
            fleet_utilization_rate=utilization_rate,
        ),
        revenue=RentalRevenueMetrics(
            gross_booking_revenue=gross_revenue,
            online_paid_amount=online_paid,
            cash_collected_amount=cash_collected,
            total_revenue_collected=online_paid + cash_collected,
            currency="LKR",
        ),
        timestamp=now,
    )


# ==============================================================================
# SP-16: ONLINE EARNINGS & SETTLEMENTS
# ==============================================================================

@router.get(
    "/settlements",
    response_model=ProviderSettlementsSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Provider rental earnings breakdown and historical settlements",
)
def get_provider_rental_settlements_summary(
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    provider_id = current_provider.id

    # Gross booking revenue from confirmed, active, completed bookings
    vehicles = db.query(RentalVehicle.id).filter(RentalVehicle.provider_id == provider_id).all()
    vehicle_ids = [v[0] for v in vehicles]

    if vehicle_ids:
        gross_revenue = (
            db.query(func.coalesce(func.sum(RentalBooking.total_price), Decimal("0.00")))
            .filter(
                RentalBooking.vehicle_id.in_(vehicle_ids),
                RentalBooking.status.in_(["confirmed", "active", "completed"]),
            )
            .scalar()
        )
    else:
        gross_revenue = Decimal("0.00")

    # Confirmed transactions
    transactions = (
        db.query(PaymentTransaction)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.provider_profile_id == provider_id,
            PaymentTransaction.payment_status.in_(["paid", "completed", "success"]),
        )
        .all()
    )

    online_paid = sum(
        (t.amount for t in transactions if t.gateway_name != "cash_on_pickup"),
        Decimal("0.00"),
    )
    cash_paid = sum(
        (t.amount for t in transactions if t.gateway_name == "cash_on_pickup"),
        Decimal("0.00"),
    )
    confirmed_payments = online_paid + cash_paid

    # Provider settlements from dbo.provider_settlements
    settlements = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.provider_profile_id == provider_id)
        .order_by(ProviderSettlement.period_start.desc())
        .all()
    )

    # CRITICAL: Compute platform commission strictly from verified platform_fee records in dbo.provider_settlements
    # Do NOT apply an unverified hardcoded 10% commission.
    platform_commission = sum(
        (s.platform_fee for s in settlements),
        Decimal("0.00"),
    )

    completed_payouts = sum(
        (s.net_payable for s in settlements if s.settlement_status in ("paid", "completed")),
        Decimal("0.00"),
    )

    pending_from_records = sum(
        (s.net_payable for s in settlements if s.settlement_status == "pending"),
        Decimal("0.00"),
    )

    # Pending balance is the recorded pending settlements plus un-settled online revenue
    pending_settlements = pending_from_records if pending_from_records > Decimal("0.00") else max(
        Decimal("0.00"), online_paid - platform_commission - completed_payouts
    )

    settlement_items = [
        ProviderSettlementItem(
            id=s.id,
            period_start=s.period_start,
            period_end=s.period_end,
            gross_amount=s.gross_amount,
            platform_fee=s.platform_fee,
            net_payable=s.net_payable,
            currency=s.currency,
            settlement_status=s.settlement_status,
            payout_reference=s.payout_reference,
            paid_at=s.paid_at,
            created_at=s.created_at,
        )
        for s in settlements
    ]

    return ProviderSettlementsSummaryResponse(
        provider_id=provider_id,
        gross_booking_revenue=gross_revenue,
        confirmed_payments=confirmed_payments,
        online_paid_amount=online_paid,
        cash_paid_amount=cash_paid,
        platform_commission=platform_commission,
        completed_payouts=completed_payouts,
        pending_settlements=pending_settlements,
        currency="LKR",
        settlements=settlement_items,
    )


@router.get(
    "/settlements/{settlement_id}",
    response_model=ProviderSettlementItem,
    status_code=status.HTTP_200_OK,
    summary="Get single settlement record details",
)
def get_provider_rental_settlement_detail(
    settlement_id: int,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    settlement = (
        db.query(ProviderSettlement)
        .filter(ProviderSettlement.id == settlement_id)
        .first()
    )
    if not settlement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Settlement record not found.",
        )
    if settlement.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this settlement record.",
        )

    return settlement


# ==============================================================================
# SP-17: SPONSORED PROMOTIONS
# ==============================================================================

@router.get(
    "/promotions/packages",
    response_model=list[RentalPromotionPackageOption],
    status_code=status.HTTP_200_OK,
    summary="Discover available promotional placement packages for rentals",
)
def get_rental_promotion_packages():
    """Returns available platform promotional packages for rental listings."""
    return [
        RentalPromotionPackageOption(
            package_key="rental_boost_7d",
            title="7-Day Rental Spotlight",
            description="Boosts vehicle listings to the top of traveler search and categories for 7 days.",
            duration_days=7,
            price=Decimal("2500.00"),
            currency="LKR",
            is_active=True,
        ),
        RentalPromotionPackageOption(
            package_key="rental_boost_14d",
            title="14-Day Prime Visibility",
            description="Premium search placement and verified sponsor badge for 14 days.",
            duration_days=14,
            price=Decimal("4500.00"),
            currency="LKR",
            is_active=True,
        ),
        RentalPromotionPackageOption(
            package_key="rental_boost_30d",
            title="30-Day Elite Featured Fleet",
            description="Full-month top-tier sponsored boost across district, category, and keyword searches.",
            duration_days=30,
            price=Decimal("8000.00"),
            currency="LKR",
            is_active=True,
        ),
    ]


@router.get(
    "/promotions/campaigns",
    response_model=ProviderRentalCampaignListResponse,
    status_code=status.HTTP_200_OK,
    summary="List provider rental sponsored promotion campaigns",
)
def list_provider_rental_campaigns(
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    campaigns = (
        db.query(PromotionCampaign)
        .filter(
            PromotionCampaign.provider_profile_id == current_provider.id,
            PromotionCampaign.target_module.in_(["rental", "rental_vehicle"]),
        )
        .order_by(PromotionCampaign.created_at.desc())
        .all()
    )

    items = []
    for c in campaigns:
        # Event counts
        imp_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "impression",
            )
            .scalar() or 0
        )
        clk_count = (
            db.query(func.count(PromotionEvent.id))
            .filter(
                PromotionEvent.campaign_id == c.id,
                PromotionEvent.event_type == "click",
            )
            .scalar() or 0
        )
        items.append(
            ProviderRentalCampaignResponse(
                id=c.id,
                provider_profile_id=c.provider_profile_id,
                campaign_name=c.campaign_name,
                target_module=c.target_module,
                budget=c.budget,
                spent_amount=c.spent_amount,
                currency=c.currency,
                status=c.status,
                starts_at=c.starts_at,
                ends_at=c.ends_at,
                created_at=c.created_at,
                impressions_count=imp_count,
                clicks_count=clk_count,
            )
        )

    return ProviderRentalCampaignListResponse(items=items, total=len(items))


@router.post(
    "/promotions/campaigns",
    response_model=ProviderRentalCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new rental sponsored promotion campaign",
)
def create_provider_rental_campaign(
    payload: ProviderRentalCampaignCreateRequest,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    starts_at = normalize_utc(payload.starts_at)
    ends_at = normalize_utc(payload.ends_at)

    if ends_at <= starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campaign end date must be after start date.",
        )

    # Listing eligibility check: Provider must have at least one approved and available rental vehicle
    eligible_vehicles = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.provider_id == current_provider.id,
            RentalVehicle.approval_status == "approved",
            RentalVehicle.status == "available",
        )
        .count()
    )
    if eligible_vehicles == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider has no approved and available rental vehicles eligible for promotion.",
        )

    # Initial campaign status is pending_approval/draft until payment verification
    campaign = PromotionCampaign(
        provider_profile_id=current_provider.id,
        campaign_name=payload.campaign_name,
        target_module="rental",
        budget=payload.budget,
        spent_amount=Decimal("0.00"),
        currency=payload.currency,
        status="pending_approval",
        starts_at=starts_at,
        ends_at=ends_at,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)

    return ProviderRentalCampaignResponse(
        id=campaign.id,
        provider_profile_id=campaign.provider_profile_id,
        campaign_name=campaign.campaign_name,
        target_module=campaign.target_module,
        budget=campaign.budget,
        spent_amount=campaign.spent_amount,
        currency=campaign.currency,
        status=campaign.status,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        created_at=campaign.created_at,
        impressions_count=0,
        clicks_count=0,
    )


# ==============================================================================
# SP-18: RENTAL DISCOUNTS & OFFERS
# ==============================================================================

@router.get(
    "/discounts",
    response_model=ProviderRentalDiscountListResponse,
    status_code=status.HTTP_200_OK,
    summary="List promotional discount packages created by provider",
)
def list_provider_rental_discounts(
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    active_only: bool = Query(default=False),
):
    query = db.query(PromotionPackage).filter(PromotionPackage.provider_profile_id == current_provider.id)
    if active_only:
        today = date.today()
        query = query.filter(
            PromotionPackage.is_active == True,
            PromotionPackage.valid_from <= today,
            PromotionPackage.valid_to >= today,
        )

    discounts = query.order_by(PromotionPackage.created_at.desc()).all()
    items = [
        ProviderRentalDiscountResponse(
            id=d.id,
            provider_profile_id=d.provider_profile_id,
            title=d.title,
            description=d.description,
            discount_percent=d.discount_percent,
            discount_amount=d.discount_amount,
            currency=d.currency,
            valid_from=d.valid_from,
            valid_to=d.valid_to,
            is_active=d.is_active,
            created_at=d.created_at,
        )
        for d in discounts
    ]
    return ProviderRentalDiscountListResponse(items=items, total=len(items))


@router.post(
    "/discounts",
    response_model=ProviderRentalDiscountResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new promotional discount package",
)
def create_provider_rental_discount(
    payload: ProviderRentalDiscountCreateRequest,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    if payload.valid_to < payload.valid_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_to must be greater than or equal to valid_from.",
        )

    has_pct = payload.discount_percent is not None and payload.discount_percent > Decimal("0.00")
    has_amt = payload.discount_amount is not None and payload.discount_amount > Decimal("0.00")
    if not has_pct and not has_amt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of discount_percent or discount_amount must be specified and greater than zero.",
        )

    discount = PromotionPackage(
        provider_profile_id=current_provider.id,
        title=payload.title,
        description=payload.description,
        discount_percent=payload.discount_percent,
        discount_amount=payload.discount_amount,
        currency="LKR",
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        is_active=payload.is_active,
    )
    db.add(discount)
    db.commit()
    db.refresh(discount)

    return ProviderRentalDiscountResponse(
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
    )


@router.get(
    "/discounts/{discount_id}",
    response_model=ProviderRentalDiscountResponse,
    status_code=status.HTTP_200_OK,
    summary="Get single rental discount details",
)
def get_provider_rental_discount_detail(
    discount_id: int,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    discount = (
        db.query(PromotionPackage)
        .filter(PromotionPackage.id == discount_id)
        .first()
    )
    if not discount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found.",
        )
    if discount.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this discount package.",
        )

    return ProviderRentalDiscountResponse(
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
    )


@router.patch(
    "/discounts/{discount_id}",
    response_model=ProviderRentalDiscountResponse,
    status_code=status.HTTP_200_OK,
    summary="Update rental discount package",
)
def update_provider_rental_discount(
    discount_id: int,
    payload: ProviderRentalDiscountUpdateRequest,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    discount = (
        db.query(PromotionPackage)
        .filter(PromotionPackage.id == discount_id)
        .first()
    )
    if not discount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found.",
        )
    if discount.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to update this discount package.",
        )

    new_from = payload.valid_from if payload.valid_from is not None else discount.valid_from
    new_to = payload.valid_to if payload.valid_to is not None else discount.valid_to
    if new_to < new_from:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="valid_to must be greater than or equal to valid_from.",
        )

    if payload.title is not None:
        discount.title = payload.title
    if payload.description is not None:
        discount.description = payload.description
    if payload.discount_percent is not None:
        discount.discount_percent = payload.discount_percent
    if payload.discount_amount is not None:
        discount.discount_amount = payload.discount_amount
    if payload.valid_from is not None:
        discount.valid_from = payload.valid_from
    if payload.valid_to is not None:
        discount.valid_to = payload.valid_to
    if payload.is_active is not None:
        discount.is_active = payload.is_active

    db.commit()
    db.refresh(discount)

    return ProviderRentalDiscountResponse(
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
    )


@router.delete(
    "/discounts/{discount_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate or remove discount package",
)
def delete_provider_rental_discount(
    discount_id: int,
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    discount = (
        db.query(PromotionPackage)
        .filter(PromotionPackage.id == discount_id)
        .first()
    )
    if not discount:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Discount package not found.",
        )
    if discount.provider_profile_id != current_provider.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to delete this discount package.",
        )

    db.delete(discount)
    db.commit()
    return {"message": f"Discount package #{discount_id} deleted successfully."}


# ==============================================================================
# SP-19: CUSTOMER REVIEWS DASHBOARD
# ==============================================================================

@router.get(
    "/reviews",
    response_model=ProviderReviewsDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="Customer ratings & reviews dashboard across provider fleet",
)
def get_provider_rental_reviews_dashboard(
    current_provider: Annotated[ProviderProfile, Depends(get_current_approved_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
    vehicle_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    provider_id = current_provider.id

    # If vehicle_id is provided, verify it belongs to this provider
    if vehicle_id is not None:
        veh = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == vehicle_id,
                RentalVehicle.provider_id == provider_id,
            )
            .first()
        )
        if not veh:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vehicle not found or does not belong to your fleet.",
            )
        target_vehicle_ids = [vehicle_id]
        vehicle_map = {veh.id: veh.vehicle_name}
    else:
        vehs = db.query(RentalVehicle).filter(RentalVehicle.provider_id == provider_id).all()
        target_vehicle_ids = [v.id for v in vehs]
        vehicle_map = {v.id: v.vehicle_name for v in vehs}

    if not target_vehicle_ids:
        return ProviderReviewsDashboardResponse(
            total_reviews=0,
            average_rating=0.0,
            rating_distribution={"5_star": 0, "4_star": 0, "3_star": 0, "2_star": 0, "1_star": 0},
            reviews=[],
            page=page,
            page_size=page_size,
            total_pages=0,
        )

    # Find listing references for these vehicles
    listing_refs = (
        db.query(ListingReference.id, ListingReference.target_id)
        .filter(
            ListingReference.module_type == "rental_vehicle",
            ListingReference.target_id.in_(target_vehicle_ids),
        )
        .all()
    )

    ref_to_vehicle = {lr.id: lr.target_id for lr in listing_refs}
    ref_ids = list(ref_to_vehicle.keys())

    if not ref_ids:
        return ProviderReviewsDashboardResponse(
            total_reviews=0,
            average_rating=0.0,
            rating_distribution={"5_star": 0, "4_star": 0, "3_star": 0, "2_star": 0, "1_star": 0},
            reviews=[],
            page=page,
            page_size=page_size,
            total_pages=0,
        )

    query = (
        db.query(Review, User.full_name)
        .outerjoin(User, Review.user_id == User.id)
        .filter(
            Review.listing_reference_id.in_(ref_ids),
            Review.is_published == True,
        )
    )

    total_reviews = query.count()
    if total_reviews == 0:
        return ProviderReviewsDashboardResponse(
            total_reviews=0,
            average_rating=0.0,
            rating_distribution={"5_star": 0, "4_star": 0, "3_star": 0, "2_star": 0, "1_star": 0},
            reviews=[],
            page=page,
            page_size=page_size,
            total_pages=0,
        )

    # Average rating
    avg_rating_val = (
        db.query(func.avg(Review.rating))
        .filter(
            Review.listing_reference_id.in_(ref_ids),
            Review.is_published == True,
        )
        .scalar()
    )
    average_rating = round(float(avg_rating_val), 2) if avg_rating_val else 0.0

    # Rating distribution across 5 buckets
    all_ratings = (
        db.query(Review.rating)
        .filter(
            Review.listing_reference_id.in_(ref_ids),
            Review.is_published == True,
        )
        .all()
    )

    dist = {"5_star": 0, "4_star": 0, "3_star": 0, "2_star": 0, "1_star": 0}
    for (r_val,) in all_ratings:
        r_float = float(r_val)
        if r_float >= 4.5:
            dist["5_star"] += 1
        elif r_float >= 3.5:
            dist["4_star"] += 1
        elif r_float >= 2.5:
            dist["3_star"] += 1
        elif r_float >= 1.5:
            dist["2_star"] += 1
        else:
            dist["1_star"] += 1

    total_pages = ceil(total_reviews / page_size)
    results = (
        query.order_by(Review.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    reviews_list = []
    for rev, author_name in results:
        v_id = ref_to_vehicle.get(rev.listing_reference_id)
        v_name = vehicle_map.get(v_id) if v_id else None
        reviews_list.append(
            ProviderReviewItem(
                id=rev.id,
                vehicle_id=v_id,
                vehicle_name=v_name,
                reviewer_name=author_name or "Verified Traveler",
                rating=rev.rating,
                title=rev.title,
                comment=rev.comment,
                is_verified_booking=rev.is_verified_booking,
                created_at=rev.created_at,
            )
        )

    return ProviderReviewsDashboardResponse(
        total_reviews=total_reviews,
        average_rating=average_rating,
        rating_distribution=dist,
        reviews=reviews_list,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ==============================================================================
# SP-20: PROVIDER & VEHICLE VERIFICATION STATUS
# ==============================================================================

@router.get(
    "/verification-status",
    response_model=ProviderVerificationStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Track provider KYC verification and vehicle listing approval states",
)
def get_provider_verification_status(
    current_provider: Annotated[ProviderProfile, Depends(get_current_rental_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    provider_id = current_provider.id

    # KYC documents submitted indicators
    docs_submitted = {
        "selfie_submitted": bool(current_provider.selfie_image_path),
        "nic_front_submitted": bool(current_provider.nic_front_image_path),
        "nic_back_submitted": bool(current_provider.nic_back_image_path),
        "business_registration_submitted": bool(current_provider.business_registration_number),
    }

    # Fetch vehicles
    vehicles = (
        db.query(RentalVehicle)
        .filter(RentalVehicle.provider_id == provider_id)
        .order_by(RentalVehicle.created_at.desc())
        .all()
    )

    total_vehicles = len(vehicles)
    approved_count = sum(1 for v in vehicles if v.approval_status == "approved")
    pending_count = sum(1 for v in vehicles if v.approval_status == "pending")
    rejected_count = sum(1 for v in vehicles if v.approval_status == "rejected")

    vehicle_summaries = [
        VehicleVerificationSummary(
            vehicle_id=v.id,
            vehicle_name=v.vehicle_name,
            vehicle_type=v.vehicle_type,
            license_plate=v.license_plate,
            status=v.status,
            approval_status=v.approval_status,
            verification_status=v.verification_status,
            reviewed_at=v.reviewed_at,
            rejection_reason=v.rejection_reason,
        )
        for v in vehicles
    ]

    return ProviderVerificationStatusResponse(
        provider_id=provider_id,
        business_name=current_provider.business_name,
        kyc_approval_status=current_provider.approval_status,
        is_profile_verified=(current_provider.approval_status == "approved"),
        kyc_reviewed_at=current_provider.reviewed_at,
        kyc_rejection_reason=current_provider.rejection_reason,
        documents_submitted=docs_submitted,
        fleet_summary={
            "total_vehicles": total_vehicles,
            "approved_vehicles": approved_count,
            "pending_vehicles": pending_count,
            "rejected_vehicles": rejected_count,
        },
        vehicles=vehicle_summaries,
    )



