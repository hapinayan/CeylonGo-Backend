import math
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
from math import ceil
from typing import Annotated, Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, text, true
from sqlalchemy.orm import Session

from app.core.booking_transactions import CAPTURED, RESERVED_REFUNDS
from app.core.dependencies import require_traveler
from app.core.publication import public_rental
from app.core.rental_capacity import TUK_TUK_MIN_PASSENGERS, tuk_tuk_passenger_limit
from app.database.connection import get_db
from app.core.notification_service import create_notification
from app.models.user import User
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalConditionEvidence,
    RentalDeposit,
    RentalEligibilityCheck,
    RentalIncident,
    RentalInspection,
    RentalLocation,
)
from app.models.shared import BookingReference, BookingStatusHistory, PaymentRefund, PaymentTransaction
from app.routers.auth import get_current_user
from app.schemas.rental_vehicle import VehicleHelmetInfo
from app.schemas.rental_deposit import (
    RentalDepositDetailResponse,
    RentalDepositResponse,
)
from app.schemas.rental_incident import (
    RentalIncidentCreateRequest,
    RentalIncidentListResponse,
    RentalIncidentResponse,
)
from app.schemas.rental_booking import (
    RentalBookingProviderItem,
    RentalBookingProviderListResponse,
    RentalCustomerSummary,
    RentalVehicleSummary,
    CashCollectionTerms,
    InspectionEvidenceItem,
    RentalBookingCancelResponse,
    RentalBookingCreate,
    RentalBookingHistoryItem,
    RentalBookingHistoryPagination,
    RentalBookingResponse,
    RentalBookingStatusUpdate,
    RentalDateValidationRequest,
    RentalDateValidationResponse,
    RentalInspectionResponse,
    TravelerInspectionAcknowledgementRequest,
    TravelerInspectionAcknowledgementResponse,
)


from app.services.provider_reservations import record_native_event
from app.services.rental_eligibility import (
    FOREIGN_TUK_TUK_PERMIT_MESSAGE,
    has_foreign_tuk_tuk_permit,
    rental_minimum_age,
    verify_rental_handover_driver,
)

router = APIRouter(
    prefix="/api/v1/rental-bookings",
    tags=["Rental Bookings"],
)


# ==========================================
# HELPER: GET CURRENT UTC DATETIME
# ==========================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==========================================
# HELPER: NORMALIZE DATETIME TO UTC
# ==========================================

def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    # Naive datetime values are interpreted as UTC.
    return value


# ==========================================
# HELPER: VEHICLE CONCURRENCY LOCK
# ==========================================

def lock_rental_vehicle(
    db: Session,
    vehicle_id: int,
):
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind else ""
    if dialect_name == "mssql":
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
            {
                "resource": f"rental_vehicle_booking_{vehicle_id}"
            },
        ).scalar()

        if lock_result is None or lock_result < 0:
            raise HTTPException(
                status_code=503,
                detail="Booking service is busy. Please try again.",
            )


# ==========================================
# TR-09: PRE-FLIGHT DATE VALIDATION
# ==========================================

@router.post(
    "/validate-dates",
    response_model=RentalDateValidationResponse,
    summary="Pre-validate rental pickup and return dates without creating a booking (TR-09)",
)
def validate_rental_dates(
    request_data: RentalDateValidationRequest,
    db: Annotated[Session, Depends(get_db)],
):
    pickup_at = normalize_utc(request_data.pickup_at)
    return_at = normalize_utc(request_data.return_at)
    current_time = utc_now()

    if return_at <= pickup_at:
        raise HTTPException(
            status_code=400,
            detail="Return date must be strictly after pickup date",
        )

    duration_seconds = (return_at - pickup_at).total_seconds()
    duration_hours = round(duration_seconds / 3600.0, 2)
    duration_days = max(1, int(ceil(duration_seconds / 86400.0)))

    if pickup_at <= current_time:
        return RentalDateValidationResponse(
            vehicle_id=request_data.vehicle_id,
            is_valid=False,
            pickup_at=pickup_at,
            return_at=return_at,
            duration_hours=duration_hours,
            duration_days=duration_days,
            collision_type="none",
            error_message="Pickup date must be in the future",
        )

    vehicle = db.get(RentalVehicle, request_data.vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        return RentalDateValidationResponse(
            vehicle_id=request_data.vehicle_id,
            is_valid=False,
            pickup_at=pickup_at,
            return_at=return_at,
            duration_hours=duration_hours,
            duration_days=duration_days,
            collision_type="none",
            error_message="Vehicle listing is not approved or not found",
        )

    if vehicle.status != "available":
        return RentalDateValidationResponse(
            vehicle_id=request_data.vehicle_id,
            is_valid=False,
            pickup_at=pickup_at,
            return_at=return_at,
            duration_hours=duration_hours,
            duration_days=duration_days,
            collision_type="none",
            error_message="Vehicle is currently unavailable",
        )

    # Check maintenance blackout block collision
    maintenance_block = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.vehicle_id == vehicle.id,
            RentalAvailabilityBlock.is_active == True,
            RentalAvailabilityBlock.start_datetime < return_at,
            RentalAvailabilityBlock.end_datetime > pickup_at,
        )
        .first()
    )
    if maintenance_block:
        return RentalDateValidationResponse(
            vehicle_id=request_data.vehicle_id,
            is_valid=False,
            pickup_at=pickup_at,
            return_at=return_at,
            duration_hours=duration_hours,
            duration_days=duration_days,
            collision_type="maintenance",
            error_message=f"Vehicle is unavailable due to scheduled {maintenance_block.block_type}: {maintenance_block.reason or 'Maintenance block'}",
        )

    # Check active booking collision
    overlapping_booking = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.vehicle_id == vehicle.id,
            RentalBooking.status.in_(["pending", "confirmed", "active", "in_progress"]),
            or_(
                RentalBooking.status != "pending",
                RentalBooking.expires_at > current_time,
                RentalBooking.expires_at.is_(None),
            ),
            RentalBooking.pickup_at < return_at,
            RentalBooking.return_at > pickup_at,
        )
        .first()
    )
    if overlapping_booking:
        return RentalDateValidationResponse(
            vehicle_id=request_data.vehicle_id,
            is_valid=False,
            pickup_at=pickup_at,
            return_at=return_at,
            duration_hours=duration_hours,
            duration_days=duration_days,
            collision_type="booked",
            error_message="Vehicle is already booked for these dates",
        )

    return RentalDateValidationResponse(
        vehicle_id=request_data.vehicle_id,
        is_valid=True,
        pickup_at=pickup_at,
        return_at=return_at,
        duration_hours=duration_hours,
        duration_days=duration_days,
        collision_type="none",
        error_message=None,
    )


# ==========================================
# TR-13: GET MY RENTAL BOOKINGS (ENRICHED & PAGINATED)
# ==========================================

@router.get(
    "/me",
    response_model=RentalBookingHistoryPagination,
    summary="Get traveler rental booking history with filters and pagination (TR-13)",
)
def get_my_rental_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status_filter: Annotated[
        Literal["upcoming", "active", "past", "cancelled", "all"],
        Query(description="Filter bookings by lifecycle stage"),
    ] = "all",
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
):
    query = (
        db.query(RentalBooking)
        .filter(RentalBooking.user_id == current_user.id)
    )

    current_time = utc_now()

    if status_filter == "upcoming":
        query = query.filter(
            RentalBooking.status.in_(["pending", "confirmed"]),
            RentalBooking.pickup_at > current_time,
        )
    elif status_filter == "active":
        query = query.filter(
            or_(
                RentalBooking.status.in_(("active", "in_progress")),
                and_(
                    RentalBooking.status == "confirmed",
                    RentalBooking.pickup_at <= current_time,
                    RentalBooking.return_at >= current_time,
                ),
            )
        )
    elif status_filter == "past":
        query = query.filter(
            or_(
                RentalBooking.status == "completed",
                and_(
                    RentalBooking.return_at < current_time,
                    RentalBooking.status.notin_(["cancelled", "rejected", "expired"]),
                ),
            )
        )
    elif status_filter == "cancelled":
        query = query.filter(
            RentalBooking.status.in_(["cancelled", "rejected", "expired"])
        )

    query = query.order_by(RentalBooking.created_at.desc())

    total = query.count()
    pages = max(1, ceil(total / limit)) if total > 0 else 0
    bookings = query.offset((page - 1) * limit).limit(limit).all()

    # Look up booking references for these bookings
    booking_ids = [b.id for b in bookings]
    refs = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id.in_(booking_ids),
        )
        .all()
    )
    ref_map = {r.target_booking_id: r.booking_reference for r in refs}

    items = []
    for b in bookings:
        v = b.vehicle
        cover_url = None
        if v and v.images:
            for img in v.images:
                if img.is_primary:
                    cover_url = img.image_url
                    break
            if not cover_url and len(v.images) > 0:
                cover_url = sorted(v.images, key=lambda x: x.display_order)[0].image_url

        p_loc = b.pickup_location or (v.location if v else None)
        r_loc = b.return_location or (v.location if v else None)

        items.append(
            RentalBookingHistoryItem(
                id=b.id,
                booking_reference=ref_map.get(b.id),
                vehicle_id=b.vehicle_id,
                vehicle_name=v.vehicle_name if v else "Rental Vehicle",
                vehicle_type=v.vehicle_type if v else "bike",
                cover_image_url=cover_url,
                pickup_location_name=p_loc.location_name if p_loc else None,
                return_location_name=r_loc.location_name if r_loc else None,
                provider_business_name=v.provider.business_name if (v and v.provider) else None,
                provider_phone=v.provider.phone if (v and v.provider) else None,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_price=b.total_price,
                currency=b.currency or "LKR",
                status=b.status,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                cancellation_policy=b.cancellation_policy_snapshot,
                free_cancellation_hours=b.free_cancellation_hours_snapshot if b.free_cancellation_hours_snapshot is not None else 24,
                cancellation_reason=b.cancellation_reason,
                cancelled_at=b.cancelled_at,
                self_drive_agreement_accepted=bool(b.self_drive_agreement_accepted),
                passenger_count=b.passenger_count,
                created_at=b.created_at,
            )
        )

    return RentalBookingHistoryPagination(
        total=total,
        page=page,
        limit=limit,
        pages=pages,
        items=items,
    )


# ==========================================
# GET RENTAL BOOKING BY ID
# ==========================================

@router.get(
    "/provider",
    response_model=RentalBookingProviderListResponse,
    summary="Provider view of fleet rental bookings",
)
def get_provider_rental_bookings(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Annotated[str | None, Query()] = None,
    vehicle_id: Annotated[int | None, Query()] = None,
):
    """
    Provider-scoped rental bookings list.
    Enforces that rental providers view only bookings for vehicles they own.
    """
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    query = (
        db.query(RentalBooking)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(RentalVehicle.provider_id == provider.id)
    )

    if vehicle_id is not None:
        target_vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )
        if not target_vehicle:
            raise HTTPException(
                status_code=404,
                detail="Rental vehicle not found or not owned by you",
            )
        query = query.filter(RentalBooking.vehicle_id == vehicle_id)

    if status and status.strip():
        clean_status = status.strip().lower()
        query = query.filter(RentalBooking.status == clean_status)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size))

    bookings = (
        query.order_by(RentalBooking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    items = []
    for b in bookings:
        v = b.vehicle
        u = b.user

        vehicle_summary = None
        if v:
            vehicle_summary = RentalVehicleSummary(
                id=v.id,
                vehicle_name=v.vehicle_name,
                vehicle_type=v.vehicle_type,
                price_per_day=v.price_per_day,
                license_plate=v.license_plate,
            )

        customer_summary = None
        if u:
            customer_summary = RentalCustomerSummary(
                id=u.id,
                full_name=u.full_name,
                email=u.email,
            )

        items.append(
            RentalBookingProviderItem(
                id=b.id,
                vehicle_id=b.vehicle_id,
                user_id=b.user_id,
                pickup_at=b.pickup_at,
                return_at=b.return_at,
                total_price=b.total_price,
                status=b.status,
                created_at=b.created_at,
                vehicle=vehicle_summary,
                customer=customer_summary,
            )
        )

    return RentalBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/provider/{booking_id}",
    response_model=RentalBookingProviderItem,
    summary="Provider view of single rental booking",
)
def get_provider_rental_booking_detail(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Get single booking details with rental provider ownership verification."""
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    booking = (
        db.query(RentalBooking)
        .join(RentalVehicle, RentalBooking.vehicle_id == RentalVehicle.id)
        .filter(
            RentalBooking.id == booking_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Rental booking not found or not owned by you",
        )

    v = booking.vehicle
    u = booking.user

    vehicle_summary = None
    if v:
        vehicle_summary = RentalVehicleSummary(
            id=v.id,
            vehicle_name=v.vehicle_name,
            vehicle_type=v.vehicle_type,
            price_per_day=v.price_per_day,
            license_plate=v.license_plate,
        )

    customer_summary = None
    if u:
        customer_summary = RentalCustomerSummary(
            id=u.id,
            full_name=u.full_name,
            email=u.email,
        )

    return RentalBookingProviderItem(
        id=booking.id,
        vehicle_id=booking.vehicle_id,
        user_id=booking.user_id,
        pickup_at=booking.pickup_at,
        return_at=booking.return_at,
        total_price=booking.total_price,
        status=booking.status,
        created_at=booking.created_at,
        vehicle=vehicle_summary,
        customer=customer_summary,
    )


# ==========================================
# GET RENTAL BOOKING BY ID
# ==========================================


@router.get(
    "/{booking_id}",
    response_model=RentalBookingResponse,
    summary="Get rental booking details by ID",
)
def get_rental_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.id == booking_id,
            RentalBooking.user_id == current_user.id,
        )
        .first()
    )

    if not booking:
        raise HTTPException(
            status_code=404,
            detail="Rental booking not found",
        )

    # Look up booking reference
    ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    legacy_party = {}
    if ref:
        booking.booking_reference = ref.booking_reference

        if booking.self_drive_agreement_accepted is None:
            history_entry = (
                db.query(BookingStatusHistory)
                .filter(
                    BookingStatusHistory.booking_reference_id == ref.id,
                    BookingStatusHistory.from_status.is_(None),
                    BookingStatusHistory.to_status == "pending",
                )
                .order_by(BookingStatusHistory.created_at, BookingStatusHistory.id)
                .first()
            )
            if history_entry and history_entry.notes:
                legacy_party["self_drive_agreement_accepted"] = (
                    "Self-drive agreement accepted: True" in history_entry.notes
                )
                count_match = re.search(r"Passenger count:\s*([1-9]\d*)\.", history_entry.notes)
                if count_match and booking.passenger_count is None:
                    legacy_party["passenger_count"] = int(count_match.group(1))

    v = booking.vehicle
    if v:
        booking.fuel_policy = "not_applicable" if v.vehicle_type == "bicycle" or (v.fuel_type and v.fuel_type.lower() == "electric") else "same_to_same"

    # AR-04: Security deposit status
    dep = (
        db.query(RentalDeposit)
        .filter(RentalDeposit.booking_id == booking.id)
        .order_by(RentalDeposit.held_at.desc())
        .first()
    )
    if dep:
        booking.security_deposit = dep.deposit_amount
        booking.deposit_status = dep.deposit_status
    else:
        deposit_map = {
            "bike": Decimal("10000.00"),
            "tuk_tuk": Decimal("15000.00"),
            "bicycle": Decimal("3000.00"),
        }
        booking.security_deposit = deposit_map.get(v.vehicle_type, Decimal("10000.00")) if v else Decimal("10000.00")
        booking.deposit_status = "not_collected"

    return RentalBookingResponse.model_validate(booking).model_copy(update=legacy_party)


# ==========================================
# TR-10 & TR-12: CREATE RENTAL BOOKING
# ==========================================

@router.post(
    "",
    response_model=RentalBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new rental booking (TR-10, TR-12)",
)
def create_rental_booking(
    booking_data: RentalBookingCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(require_traveler)],
):
    pickup_at = normalize_utc(booking_data.pickup_at)
    return_at = normalize_utc(booking_data.return_at)

    current_time = utc_now()
    if pickup_at <= current_time:
        raise HTTPException(
            status_code=400,
            detail="Pickup date must be in the future",
        )

    if return_at <= pickup_at:
        raise HTTPException(
            status_code=400,
            detail="Return date must be after pickup date",
        )

    try:
        # Acquire exclusive lock for vehicle
        lock_rental_vehicle(
            db,
            booking_data.vehicle_id,
        )

        vehicle = (
            db.query(RentalVehicle)
            .with_hint(RentalVehicle, "WITH (HOLDLOCK)", "mssql")
            .filter(RentalVehicle.id == booking_data.vehicle_id, public_rental(hold=True))
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=404,
                detail="Rental vehicle not found",
            )

        if vehicle.approval_status != "approved":
            raise HTTPException(
                status_code=400,
                detail="Vehicle listing is not approved by administrator",
            )

        if vehicle.status != "available":
            raise HTTPException(
                status_code=400,
                detail="Vehicle is currently unavailable",
            )

        # AR-01 & AR-11: Motorized driving license & permit verification (Conditions 6, 8, 15, 16)
        eligibility = None
        if vehicle.vehicle_type != "bicycle":
            eligibility = (
                db.query(RentalEligibilityCheck)
                .filter(RentalEligibilityCheck.user_id == current_user.id)
                .first()
            )
            if not eligibility or eligibility.verification_status != "approved":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Motorized rental bookings require an approved driving license. Please submit your driving credentials under /api/v1/rental-eligibility/license.",
                )
            if eligibility.license_expiry_date < return_at.date():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Driving license expires on {eligibility.license_expiry_date}, which is before the rental return date ({return_at.date()}).",
                )
            if vehicle.vehicle_type == "tuk_tuk" and eligibility.license_country.strip().lower() != "sri lanka":
                has_permit = has_foreign_tuk_tuk_permit(eligibility)
                if not has_permit:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=FOREIGN_TUK_TUK_PERMIT_MESSAGE,
                    )

        # AR-02: Minimum Age Requirements & Trusted Evidence Integrity (Conditions 11, 12, 13, 14)
        # 11: Do NOT accept client-supplied driver_dob in booking creation as authoritative proof of age.
        # 12: Check if an existing approved identity verification record provides a trustworthy date of birth (e.g. verified DOB on license or ProviderProfile.date_of_birth from KYC).
        min_age, requires_dob = rental_minimum_age(db, vehicle.vehicle_type)
        if eligibility is None and requires_dob:
            eligibility = db.query(RentalEligibilityCheck).filter_by(
                user_id=current_user.id, verification_status="approved",
            ).first()
        verified_dob = None
        if eligibility and getattr(eligibility, "date_of_birth", None):
            verified_dob = eligibility.date_of_birth
        else:
            user_kyc_profile = (
                db.query(ProviderProfile)
                .filter(ProviderProfile.user_id == current_user.id)
                .first()
            )
            if user_kyc_profile and user_kyc_profile.approval_status == "approved" and user_kyc_profile.date_of_birth:
                verified_dob = user_kyc_profile.date_of_birth

        if verified_dob:
            pickup_date = pickup_at.date()
            age_at_pickup = (
                pickup_date.year
                - verified_dob.year
                - ((pickup_date.month, pickup_date.day) < (verified_dob.month, verified_dob.day))
            )
            if age_at_pickup < min_age:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Renter must be at least {min_age} years old at pickup for {vehicle.vehicle_type} rentals. Current verified age is {age_at_pickup}.",
                )
        elif requires_dob:
            # 14: If verified DOB is unavailable, fail closed for age-restricted bookings and report the missing trusted-data dependency.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Age verification requires a trusted date of birth from verified identity KYC. No verified date of birth was found for this user account. (AR-02 dependency: Traveler identity verification not implemented).",
            )

        # AR-09: Mandatory Tuk-Tuk Self-Drive Rental Agreement Acceptance
        if vehicle.vehicle_type == "tuk_tuk":
            if not booking_data.self_drive_agreement_accepted:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tuk-tuk bookings require mandatory acceptance of the self-drive rental agreement terms (AR-09).",
                )

        # AR-10: Tuk-Tuk Passenger Count & Seating Capacity Validation
        if vehicle.vehicle_type == "tuk_tuk":
            if booking_data.passenger_count is None or booking_data.passenger_count < TUK_TUK_MIN_PASSENGERS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Tuk-tuk bookings require a valid passenger count of at least 1 passenger (AR-10).",
                )
            max_passengers = tuk_tuk_passenger_limit(vehicle.seating_capacity)
            if max_passengers is None:
                raise HTTPException(400, "Tuk-tuk seating capacity must be verified before booking")
            if booking_data.passenger_count > max_passengers:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Passenger count ({booking_data.passenger_count}) exceeds vehicle passenger capacity of {max_passengers} (total seating capacity: {vehicle.seating_capacity} including driver).",
                )
        elif vehicle.vehicle_type in ["bike", "bicycle"]:
            # Bike & bicycle backward compatibility
            if booking_data.passenger_count is not None:
                max_passengers = max(1, vehicle.seating_capacity - 1)
                if booking_data.passenger_count > max_passengers:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Passenger count ({booking_data.passenger_count}) exceeds vehicle passenger capacity of {max_passengers} for {vehicle.vehicle_type}.",
                    )

        for field, location_id in (
            ("pickup_location_id", booking_data.pickup_location_id),
            ("return_location_id", booking_data.return_location_id),
        ):
            if location_id is None:
                continue
            location = (
                db.query(RentalLocation.id)
                .with_hint(RentalLocation, "WITH (HOLDLOCK)", "mssql")
                .filter(
                    RentalLocation.id == location_id,
                    RentalLocation.provider_id == vehicle.provider_id,
                    RentalLocation.is_active == true(),
                )
                .first()
            )
            if location is None:
                raise HTTPException(400, f"{field} must identify an active hub for this vehicle's provider")

        # Mark expired pending bookings for this vehicle as 'expired'
        expired_bookings = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status == "pending",
                RentalBooking.expires_at <= current_time,
            )
            .all()
        )
        for exp in expired_bookings:
            exp.status = "expired"
        if expired_bookings:
            db.flush()

        # Prevent collision with active maintenance blackout blocks
        maintenance_block = (
            db.query(RentalAvailabilityBlock)
            .filter(
                RentalAvailabilityBlock.vehicle_id == vehicle.id,
                RentalAvailabilityBlock.is_active == True,
                RentalAvailabilityBlock.start_datetime < return_at,
                RentalAvailabilityBlock.end_datetime > pickup_at,
            )
            .first()
        )

        if maintenance_block:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Vehicle is unavailable due to scheduled {maintenance_block.block_type}: {maintenance_block.reason or 'Maintenance block'}",
            )

        # Prevent overlapping pending, confirmed, or active bookings
        overlapping_booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.vehicle_id == vehicle.id,
                RentalBooking.status.in_(["pending", "confirmed", "active", "in_progress"]),
                RentalBooking.pickup_at < return_at,
                RentalBooking.return_at > pickup_at,
            )
            .first()
        )

        if overlapping_booking:
            raise HTTPException(
                status_code=409,
                detail="Vehicle is already booked for these dates",
            )

        # Calculate rental duration in days
        duration_seconds = (return_at - pickup_at).total_seconds()
        rental_days = (
            Decimal(str(duration_seconds)) / Decimal("86400")
        ).to_integral_value(rounding=ROUND_CEILING)

        if rental_days < Decimal("1"):
            rental_days = Decimal("1")

        # Calculate total price on the server
        total_price = (vehicle.price_per_day * rental_days).quantize(
            Decimal("0.01")
        )

        # 30-minute hold window for payment/confirmation
        hold_expires_at = current_time + timedelta(minutes=30)

        new_booking = RentalBooking(
            vehicle_id=vehicle.id,
            user_id=current_user.id,
            pickup_at=pickup_at,
            return_at=return_at,
            total_price=total_price,
            status="pending",
            pickup_location_id=(
                vehicle.location_id if booking_data.pickup_location_id is None else booking_data.pickup_location_id
            ),
            return_location_id=(
                vehicle.location_id if booking_data.return_location_id is None else booking_data.return_location_id
            ),
            currency="LKR",
            payment_method=booking_data.payment_method or "pay_at_location",
            payment_status="unpaid",
            expires_at=hold_expires_at,
            passenger_count=booking_data.passenger_count,
            self_drive_agreement_accepted=booking_data.self_drive_agreement_accepted,
            daily_rate_snapshot=vehicle.price_per_day,
            cancellation_policy_snapshot=vehicle.cancellation_policy,
            free_cancellation_hours_snapshot=vehicle.free_cancellation_hours,
        )

        db.add(new_booking)
        db.flush()

        # Synchronize with shared platform booking_references (TR-10)
        ref_code = f"RB-{new_booking.id}-{uuid.uuid4().hex[:8].upper()}"
        booking_ref = BookingReference(
            booking_reference=ref_code,
            module_type="rental_vehicle",
            target_booking_id=new_booking.id,
            user_id=current_user.id,
            provider_profile_id=vehicle.provider_id,
            total_price=new_booking.total_price,
            currency=new_booking.currency or "LKR",
            booking_status=new_booking.status,
            payment_status=new_booking.payment_status or "unpaid",
        )
        db.add(booking_ref)
        db.flush()

        # AR-09 & AR-10: Persistent auditable agreement acceptance and passenger count in approved BookingStatusHistory
        agreement_note = (
            f"Initial booking created. Self-drive agreement accepted: {booking_data.self_drive_agreement_accepted}. Passenger count: {booking_data.passenger_count}."
            if vehicle.vehicle_type == "tuk_tuk"
            else f"Initial booking created. Vehicle category: {vehicle.vehicle_type}. Passenger count: {booking_data.passenger_count or 1}."
        )
        status_history = BookingStatusHistory(
            booking_reference_id=booking_ref.id,
            from_status=None,
            to_status="pending",
            changed_by=current_user.id,
            notes=agreement_note,
        )
        db.add(status_history)
        # TR-18: Dispatch in-app notification to rental provider
        provider_profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.id == vehicle.provider_id)
            .first()
        )
        if provider_profile and provider_profile.user_id:
            create_notification(
                db=db,
                user_id=provider_profile.user_id,
                title=f"New Rental Booking #{new_booking.id}",
                message=f"New booking request #{new_booking.id} received for {vehicle.vehicle_name} ({new_booking.pickup_at.strftime('%Y-%m-%d')} to {new_booking.return_at.strftime('%Y-%m-%d')}).",
                notification_type="rental_booking_created",
                link_url=f"/provider/rentals/bookings/{new_booking.id}",
            )
        db.commit()
        db.refresh(new_booking)

        # Attach booking reference to response
        new_booking.booking_reference = ref_code

        # Attach cash terms for pay_at_location (TR-12)
        if new_booking.payment_method == "pay_at_location":
            deposit_map = {
                "bike": Decimal("10000.00"),
                "tuk_tuk": Decimal("15000.00"),
                "bicycle": Decimal("3000.00"),
            }
            security_deposit = deposit_map.get(vehicle.vehicle_type, Decimal("10000.00"))
            new_booking.cash_terms = CashCollectionTerms(
                is_cash_on_pickup=True,
                payable_amount=new_booking.total_price,
                currency="LKR",
                security_deposit=security_deposit,
                instructions="Please pay cash (LKR) directly to the rental provider upon vehicle handover. A refundable security deposit is also collected at pickup.",
            )

        new_booking.fuel_policy = "not_applicable" if vehicle.vehicle_type == "bicycle" or (vehicle.fuel_type and vehicle.fuel_type.lower() == "electric") else "same_to_same"
        deposit_map = {
            "bike": Decimal("10000.00"),
            "tuk_tuk": Decimal("15000.00"),
            "bicycle": Decimal("3000.00"),
        }
        new_booking.security_deposit = deposit_map.get(vehicle.vehicle_type, Decimal("10000.00"))
        new_booking.deposit_status = "not_collected"

        return new_booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# TR-14: CANCEL RENTAL BOOKING (POLICY-AWARE)
# ==========================================

@router.patch(
    "/{booking_id}/cancel",
    response_model=RentalBookingResponse,
    summary="Cancel rental booking with policy evaluation and refund triggers (TR-14)",
)
def cancel_rental_booking(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.user_id == current_user.id,
            )
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(
            db,
            booking_vehicle_id,
        )

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.user_id == current_user.id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        if booking.status not in ("pending", "confirmed"):
            raise HTTPException(
                status_code=400,
                detail="This booking cannot be cancelled",
            )

        current_time = utc_now()
        if booking.pickup_at <= current_time:
            raise HTTPException(
                status_code=400,
                detail="Cannot cancel after pickup time",
            )

        vehicle = db.get(RentalVehicle, booking.vehicle_id)
        provider = db.get(ProviderProfile, vehicle.provider_id)
        booking_ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental_vehicle",
                BookingReference.target_booking_id == booking.id,
            )
            .first()
        )
        if booking_ref and (booking_ref.user_id != booking.user_id or booking_ref.provider_profile_id != provider.id):
            raise HTTPException(409, "Rental booking reference requires reconciliation before cancellation")

        previous = booking.status
        # Policy-aware cancellation evaluation (TR-14)
        free_hours = booking.free_cancellation_hours_snapshot
        if free_hours is None:
            free_hours = 24

        if booking.pickup_at - current_time >= timedelta(hours=free_hours):
            is_within_free_cancellation = True
            cancellation_fee = Decimal("0.00")
            msg = f"Cancelled within the free cancellation window ({free_hours} hours)."
        else:
            is_within_free_cancellation = False
            penalty = booking.daily_rate_snapshot
            if penalty is None:
                penalty = booking.total_price / Decimal("2")
            cancellation_fee = min(penalty, booking.total_price).quantize(Decimal("0.01"))
            msg = f"Cancelled past the free cancellation window ({free_hours} hours). Policy fee: {cancellation_fee} {booking.currency or 'LKR'}."

        booking.status = "cancelled"
        booking.cancelled_by = current_user.id
        booking.cancelled_at = current_time
        booking.cancellation_reason = f"Cancelled by traveler: {msg}"

        if booking_ref:
            booking_ref.booking_status = "cancelled"

        refund_amount = Decimal("0.00")
        refund_record_created = False
        if booking.payment_method == "online" and booking.payment_status == "paid" and booking_ref:
            tx = (
                db.query(PaymentTransaction)
                .filter(
                    PaymentTransaction.booking_reference_id == booking_ref.id,
                    PaymentTransaction.user_id == booking.user_id,
                    PaymentTransaction.gateway_name == "payhere",
                    PaymentTransaction.payment_method == "online",
                    PaymentTransaction.payment_status.in_(CAPTURED),
                    PaymentTransaction.gateway_transaction_id.is_not(None),
                    PaymentTransaction.paid_at.is_not(None),
                    PaymentTransaction.currency == (booking.currency or "LKR"),
                    PaymentTransaction.amount == booking.total_price,
                )
                .order_by(PaymentTransaction.paid_at, PaymentTransaction.id)
                .first()
            )
            if tx:
                reserved = db.query(PaymentRefund.amount, PaymentRefund.currency).filter(
                    PaymentRefund.payment_transaction_id == tx.id,
                    PaymentRefund.refund_status.in_(RESERVED_REFUNDS),
                ).all()
                if any(currency != tx.currency or amount < 0 for amount, currency in reserved):
                    raise HTTPException(409, "Existing refund reservations require reconciliation before cancellation")
                reserved_amount = sum((amount for amount, _ in reserved), Decimal("0.00"))
                refund_amount = max(
                    Decimal("0.00"),
                    (tx.amount - cancellation_fee - reserved_amount).quantize(Decimal("0.01")),
                )
                if refund_amount > 0:
                    db.add(PaymentRefund(
                        payment_transaction_id=tx.id,
                        amount=refund_amount,
                        currency=tx.currency,
                        refund_reason=f"Traveler cancellation refund audit: {msg}",
                        refund_status="pending",
                    ))
                    booking.payment_status = booking_ref.payment_status = "refund_pending"
                    refund_record_created = True
        record_native_event(
            db, booking, "rental_vehicle", provider.id, previous, "cancelled",
            current_user.id, provider.user_id, notify=False,
        )

        if provider.user_id:
            create_notification(
                db=db,
                user_id=provider.user_id,
                title=f"Rental Booking #{booking.id} Cancelled",
                message=f"Booking #{booking.id} for {vehicle.vehicle_name} was cancelled by traveler.",
                notification_type="rental_booking_cancelled",
                link_url=f"/provider/rentals/bookings/{booking.id}",
            )
        db.commit()
        db.refresh(booking)

        booking.is_within_free_cancellation = is_within_free_cancellation
        booking.refund_eligible = refund_amount > 0
        booking.cancellation_fee = cancellation_fee
        booking.refund_amount = refund_amount
        booking.refund_record_created = refund_record_created
        if booking_ref:
            booking.booking_reference = booking_ref.booking_reference

        return booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# PROVIDER STATUS TRANSITIONS
# ==========================================

@router.patch(
    "/{booking_id}/status",
    response_model=RentalBookingResponse,
    summary="Update rental booking lifecycle status (Provider only)",
)
def update_rental_booking_status(
    booking_id: int,
    status_data: RentalBookingStatusUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    if status_data.status not in ("confirmed", "active", "completed", "rejected"):
        raise HTTPException(
            status_code=422,
            detail="Provider can only confirm, activate, complete, or reject bookings",
        )

    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Only rental providers can update bookings",
        )

    if provider.approval_status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Rental provider is not approved",
        )

    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(
                RentalBooking.id == booking_id,
            )
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(
            db,
            booking_vehicle_id,
        )

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        vehicle = db.get(
            RentalVehicle,
            booking.vehicle_id,
        )

        if not vehicle:
            raise HTTPException(
                status_code=404,
                detail="Rental vehicle not found",
            )

        if vehicle.provider_id != provider.id:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        current_time = utc_now()

        # Handle Rejection
        if status_data.status == "rejected":
            if booking.status != "pending":
                raise HTTPException(
                    status_code=400,
                    detail="Only pending bookings can be rejected",
                )
            booking.status = "rejected"
            booking.cancellation_reason = status_data.rejection_reason or "Rejected by provider"
            booking.cancelled_by = current_user.id
            booking.cancelled_at = current_time

            # Sync BookingReference
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

            # TR-18: Dispatch rejection notification to traveler
            create_notification(
                db=db,
                user_id=booking.user_id,
                title=f"Rental Booking #{booking.id} Declined",
                message=f"Your booking #{booking.id} for {vehicle.vehicle_name} was declined: {booking.cancellation_reason}",
                notification_type="rental_booking_rejected",
                link_url=f"/traveler/rental-bookings/{booking.id}",
            )

            db.commit()
            db.refresh(booking)
            return booking

        # Allowed forward booking status transitions
        allowed_transitions = {
            "pending": ["confirmed"],
            "confirmed": ["active", "completed"],
            "active": ["completed"],
        }

        valid_targets = allowed_transitions.get(booking.status, [])

        if status_data.status not in valid_targets:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid booking status transition from '{booking.status}' to '{status_data.status}'",
            )

        # Safe expiry check for pending bookings being confirmed
        if status_data.status == "confirmed" and (
            booking.pickup_at <= current_time
            or (booking.expires_at is not None and booking.expires_at <= current_time)
        ):
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
                status_code=400,
                detail="Booking hold period has expired",
            )

        # Booking can only be completed after return time
        if (
            status_data.status == "completed"
            and booking.return_at > current_time
        ):
            raise HTTPException(
                status_code=400,
                detail="Booking cannot be completed before return time",
            )

        previous = booking.status
        if status_data.status == "active":
            verify_rental_handover_driver(db, booking, vehicle)
        booking.status = status_data.status
        record_native_event(
            db, booking, "rental_vehicle", provider.id, previous, booking.status,
            current_user.id, booking.user_id, notify=False,
        )

        # Sync BookingReference
        booking_ref = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "rental_vehicle",
                BookingReference.target_booking_id == booking.id,
            )
            .first()
        )
        if booking_ref:
            booking_ref.booking_status = booking.status

        # TR-18: Dispatch status transition notification to traveler
        if booking.status == "confirmed":
            create_notification(
                db=db,
                user_id=booking.user_id,
                title=f"Rental Booking #{booking.id} Confirmed",
                message=f"Your booking #{booking.id} for {vehicle.vehicle_name} has been confirmed by the provider!",
                notification_type="rental_booking_confirmed",
                link_url=f"/traveler/rental-bookings/{booking.id}",
            )
        elif booking.status == "active":
            notify_rental_pickup(db, booking, vehicle)
        elif booking.status == "completed":
            create_notification(
                db=db,
                user_id=booking.user_id,
                title=f"Rental Booking #{booking.id} Completed",
                message=f"Your rental for {vehicle.vehicle_name} is complete! Please rate and review your experience.",
                notification_type="rental_booking_completed",
                link_url=f"/traveler/rental-bookings/{booking.id}/reviews",
            )

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise


# ==============================================================================
# AR-06: TRAVELER INSPECTION CHECKLIST & DIGITAL ACKNOWLEDGEMENT
# ==============================================================================

def parse_inspection_helmet_info(notes: str | None, vehicle: RentalVehicle) -> VehicleHelmetInfo | None:
    """Extracts structured helmet details recorded in inspection notes/evidence for two-wheelers (AR-03)."""
    if vehicle.vehicle_type == "tuk_tuk":
        return VehicleHelmetInfo(
            helmet_provided=False,
            helmet_count=None,
            helmet_sizes=None,
            helmet_condition=None,
            requires_helmet=False,
            notes="Helmets are not required for tuk-tuks in Sri Lanka.",
        )
    if not notes:
        return VehicleHelmetInfo(
            helmet_provided=False,
            helmet_count=None,
            helmet_sizes=None,
            helmet_condition=None,
            requires_helmet=True,
            notes="No helmet data recorded during inspection.",
        )
    import re
    tag_match = re.search(r"\[Helmets:\s*([^\]]+)\]", notes, re.IGNORECASE)
    if tag_match:
        content = tag_match.group(1)
        count_m = re.search(r"count=(\d+)", content, re.IGNORECASE)
        count = int(count_m.group(1)) if count_m else 0
        sizes_m = re.search(r"sizes=([^,\]]+(?:,\s*[^,\]]+)*?)(?:,\s*condition|$)", content, re.IGNORECASE)
        sizes = [s.strip() for s in sizes_m.group(1).split(",") if s.strip()] if sizes_m else None
        cond_m = re.search(r"condition=([^,\]]+)", content, re.IGNORECASE)
        cond = cond_m.group(1).strip() if cond_m else None
        return VehicleHelmetInfo(
            helmet_provided=count > 0,
            helmet_count=count,
            helmet_sizes=sizes,
            helmet_condition=cond,
            requires_helmet=True,
            notes=f"Inspection recorded {count} helmet(s)",
        )
    return VehicleHelmetInfo(
        helmet_provided=False,
        helmet_count=None,
        helmet_sizes=None,
        helmet_condition=None,
        requires_helmet=True,
        notes="No helmet data recorded during inspection.",
    )


def return_acknowledgement_timestamp(
    inspection_type: str, value: datetime | None,
) -> datetime | None:
    if inspection_type == "return" and value is not None:
        return normalize_utc(value).replace(tzinfo=timezone.utc)
    return value


@router.get(
    "/{booking_id}/inspections",
    response_model=list[RentalInspectionResponse],
    summary="Get inspection checklists and condition photos for a booking (AR-06)",
)
def get_booking_inspections(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Returns digital handover and return inspection checklists with condition photos for a booking.
    Authorization: Accessible only by the booking traveler, vehicle provider, or admin.
    """
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found",
        )

    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    is_traveler = (booking.user_id == current_user.id)
    is_provider = bool(vehicle and vehicle.provider and vehicle.provider.user_id == current_user.id)
    is_admin = bool(getattr(current_user, "role", "") == "admin")

    if not (is_traveler or is_provider or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to booking inspection checklists",
        )

    inspections = (
        db.query(RentalInspection)
        .filter(RentalInspection.booking_id == booking.id)
        .order_by(RentalInspection.created_at.asc())
        .all()
    )

    fuel_policy = "not_applicable" if vehicle and (vehicle.vehicle_type == "bicycle" or (vehicle.fuel_type and vehicle.fuel_type.lower() == "electric")) else "same_to_same"

    results = []
    for insp in inspections:
        ev_items = [
            InspectionEvidenceItem(
                id=ev.id,
                inspection_id=ev.inspection_id,
                evidence_type=ev.evidence_type,
                file_url=ev.file_url,
                area_of_vehicle=ev.area_of_vehicle,
                description=ev.description,
                created_at=ev.created_at,
            )
            for ev in (getattr(insp, "condition_evidence", None) or [])
        ]
        helmets = parse_inspection_helmet_info(insp.notes, vehicle) if vehicle else None
        is_ack = insp.acknowledged_at is not None

        results.append(
            RentalInspectionResponse(
                id=insp.id,
                booking_id=insp.booking_id,
                inspection_type=insp.inspection_type,
                odometer_reading=insp.odometer_reading,
                fuel_level_percent=insp.fuel_level_percent,
                cleanliness_status=insp.cleanliness_status,
                inspected_by=insp.inspected_by,
                customer_signature=insp.customer_signature,
                is_acknowledged=is_ack,
                acknowledged_at=return_acknowledgement_timestamp(insp.inspection_type, insp.acknowledged_at),
                notes=insp.notes,
                helmets=helmets,
                fuel_policy=fuel_policy,
                evidence=ev_items,
                created_at=insp.created_at,
            )
        )

    return results


@router.get(
    "/{booking_id}/inspections/{inspection_id}",
    response_model=RentalInspectionResponse,
    summary="Get single inspection checklist and condition photos (AR-06)",
)
def get_booking_inspection_detail(
    booking_id: int,
    inspection_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Returns single inspection checklist with verified condition photos and acknowledgement state.
    """
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found",
        )

    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    is_traveler = (booking.user_id == current_user.id)
    is_provider = bool(vehicle and vehicle.provider and vehicle.provider.user_id == current_user.id)
    is_admin = bool(getattr(current_user, "role", "") == "admin")

    if not (is_traveler or is_provider or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to booking inspection checklist",
        )

    insp = (
        db.query(RentalInspection)
        .filter(
            RentalInspection.id == inspection_id,
            RentalInspection.booking_id == booking.id,
        )
        .first()
    )
    if not insp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inspection checklist not found for this booking",
        )

    ev_items = [
        InspectionEvidenceItem(
            id=ev.id,
            inspection_id=ev.inspection_id,
            evidence_type=ev.evidence_type,
            file_url=ev.file_url,
            area_of_vehicle=ev.area_of_vehicle,
            description=ev.description,
            created_at=ev.created_at,
        )
        for ev in (getattr(insp, "condition_evidence", None) or [])
    ]
    helmets = parse_inspection_helmet_info(insp.notes, vehicle) if vehicle else None
    is_ack = insp.acknowledged_at is not None
    fuel_policy = "not_applicable" if vehicle and (vehicle.vehicle_type == "bicycle" or (vehicle.fuel_type and vehicle.fuel_type.lower() == "electric")) else "same_to_same"

    return RentalInspectionResponse(
        id=insp.id,
        booking_id=insp.booking_id,
        inspection_type=insp.inspection_type,
        odometer_reading=insp.odometer_reading,
        fuel_level_percent=insp.fuel_level_percent,
        cleanliness_status=insp.cleanliness_status,
        inspected_by=insp.inspected_by,
        customer_signature=insp.customer_signature,
        is_acknowledged=is_ack,
        acknowledged_at=return_acknowledgement_timestamp(insp.inspection_type, insp.acknowledged_at),
        notes=insp.notes,
        helmets=helmets,
        fuel_policy=fuel_policy,
        evidence=ev_items,
        created_at=insp.created_at,
    )


@router.post(
    "/{booking_id}/inspections/{inspection_id}/acknowledge",
    response_model=TravelerInspectionAcknowledgementResponse,
    summary="Traveler digital acknowledgement of vehicle condition at handover (AR-06)",
)
def acknowledge_handover_inspection(
    booking_id: int,
    inspection_id: int,
    payload: TravelerInspectionAcknowledgementRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request = None,
):
    return acknowledge_rental_inspection(booking_id, inspection_id, payload, db, current_user, request, "pickup")


@router.post(
    "/{booking_id}/inspections/{inspection_id}/acknowledge-return",
    response_model=TravelerInspectionAcknowledgementResponse,
    summary="Traveler digital acknowledgement of vehicle condition at return",
)
def acknowledge_return_inspection(
    booking_id: int,
    inspection_id: int,
    payload: TravelerInspectionAcknowledgementRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    request: Request,
):
    return acknowledge_rental_inspection(booking_id, inspection_id, payload, db, current_user, request, "return")


def acknowledge_rental_inspection(booking_id, inspection_id, payload, db, current_user, request, inspection_type):
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found",
        )

    # Rule 15: Ensure only the traveler associated with the booking can acknowledge the inspection
    if booking.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the traveler associated with this booking can acknowledge the inspection checklist.",
        )

    lock_rental_vehicle(db, booking.vehicle_id)
    insp = (
        db.query(RentalInspection)
        .filter(
            RentalInspection.id == inspection_id,
            RentalInspection.booking_id == booking.id,
        )
        .first()
    )
    if not insp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inspection checklist not found for this booking",
        )

    label = "handover" if inspection_type == "pickup" else "return"
    if insp.inspection_type != inspection_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {'pickup handover' if inspection_type == 'pickup' else 'return'} inspection checklists can be acknowledged by traveler.",
        )

    # Rule 16: Prevent duplicate acknowledgements
    if insp.acknowledged_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Vehicle {label} inspection has already been acknowledged.",
        )

    if not payload.agreed_to_condition:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Traveler must agree to the inspected vehicle condition to acknowledge.",
        )

    now = utc_now()
    raw_payload = f"BOOKING:{booking.id}|INSP:{insp.id}|ODO:{insp.odometer_reading}|FUEL:{insp.fuel_level_percent}|USER:{current_user.id}|NAME:{payload.signature_name.strip()}|TIME:{now.isoformat()}"
    digest = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
    sig_token = f"SIG:SHA256:{digest[:32]}|BY:{payload.signature_name.strip()}|AT:{now.isoformat()}"

    recorded_signature = insp.customer_signature
    if not recorded_signature:
        insp.customer_signature = sig_token
    insp.acknowledged_at = now
    if request and hasattr(request, "client") and request.client:
        insp.acknowledgement_ip = request.client.host

    # Persist in BookingStatusHistory for multi-layered audit trail
    booking_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "rental_vehicle",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if recorded_signature and booking_ref is None:
        booking_ref = BookingReference(
            module_type="rental_vehicle", target_booking_id=booking.id,
            provider_profile_id=booking.vehicle.provider_id, user_id=booking.user_id,
            booking_reference=f"RB-{booking.id}-{uuid.uuid4().hex[:8].upper()}",
            total_price=booking.total_price, currency=booking.currency,
            booking_status=booking.status, payment_status=booking.payment_status,
        )
        db.add(booking_ref)
        db.flush()
    if booking_ref:
        status_history = BookingStatusHistory(
            booking_reference_id=booking_ref.id,
            from_status=booking.status,
            to_status=booking.status,
            changed_by=current_user.id,
            notes=f"Traveler digital {label} acknowledgement: {sig_token}",
        )
        db.add(status_history)

    db.commit()
    db.refresh(insp)

    return TravelerInspectionAcknowledgementResponse(
        booking_id=booking.id,
        inspection_id=insp.id,
        inspection_type=insp.inspection_type,
        is_acknowledged=True,
        customer_signature=sig_token,
        acknowledged_at=return_acknowledgement_timestamp(inspection_type, now),
        message=f"{label.capitalize()} condition successfully acknowledged by {payload.signature_name.strip()}.",
    )


# ==============================================================================
# AUTHORIZATION HELPER FOR BOOKING ACCESS
# ==============================================================================

def check_booking_access(
    booking: RentalBooking,
    current_user: User,
    db: Session,
) -> bool:
    """
    Check if current_user is:
    1. The traveler who placed the booking (booking.user_id == current_user.id)
    2. The provider who owns the booked vehicle
    3. An administrator
    """
    if getattr(current_user, "role", None) == "admin":
        return True
    if booking.user_id == current_user.id:
        return True
    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    if vehicle:
        provider = (
            db.query(ProviderProfile)
            .filter(
                ProviderProfile.id == vehicle.provider_id,
                ProviderProfile.user_id == current_user.id,
            )
            .first()
        )
        if provider:
            return True
    return False


# ==============================================================================
# AR-04: SECURITY DEPOSIT RETRIEVAL (TRAVELER & PROVIDER ACCESS)
# ==============================================================================

@router.get(
    "/{booking_id}/deposit",
    response_model=RentalDepositDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get security deposit status and history for a rental booking (AR-04)",
)
def get_booking_deposit(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found.",
        )

    if not check_booking_access(booking, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only the booking traveler, vehicle provider, or administrator may view deposit details.",
        )

    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    deposit_map = {
        "bike": Decimal("10000.00"),
        "tuk_tuk": Decimal("15000.00"),
        "bicycle": Decimal("3000.00"),
    }
    required_amount = deposit_map.get(vehicle.vehicle_type, Decimal("10000.00")) if vehicle else Decimal("10000.00")

    deposits = (
        db.query(RentalDeposit)
        .filter(RentalDeposit.booking_id == booking.id)
        .order_by(RentalDeposit.held_at.desc())
        .all()
    )

    active_dep = next((d for d in deposits if d.deposit_status == "held"), None)
    if not active_dep and deposits:
        active_dep = deposits[0]

    deposit_status = active_dep.deposit_status if active_dep else "not_collected"
    msg = (
        f"Security deposit status: {deposit_status}."
        if active_dep
        else f"Security deposit of {required_amount} LKR is required at pickup, currently not collected."
    )

    return RentalDepositDetailResponse(
        booking_id=booking.id,
        required_deposit_amount=required_amount,
        currency="LKR",
        deposit_status=deposit_status,
        active_deposit=RentalDepositResponse.model_validate(active_dep) if active_dep else None,
        history=[RentalDepositResponse.model_validate(d) for d in deposits],
        message=msg,
    )


# ==============================================================================
# AR-08: DAMAGE & ACCIDENT REPORTING (TRAVELER & PROVIDER ACCESS)
# ==============================================================================

@router.post(
    "/{booking_id}/incidents",
    response_model=RentalIncidentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Report vehicle damage, accident, or incident for a rental booking (AR-08)",
)
def report_rental_incident(
    booking_id: int,
    payload: RentalIncidentCreateRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found.",
        )

    if not check_booking_access(booking, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only the booking traveler, vehicle provider, or administrator may report an incident.",
        )

    if booking.status in ("cancelled", "rejected", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot report an incident for a booking in '{booking.status}' status.",
        )

    # Validate evidence URLs to prevent path traversal & SSRF injection
    for url in payload.evidence_urls:
        if ".." in url or "\\" in url or url.startswith("file://"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid evidence URL '{url}'. Path traversal and insecure schemes are prohibited.",
            )

    desc = payload.description.strip()
    if payload.evidence_urls:
        evidence_note = " [Evidence: " + ", ".join(payload.evidence_urls) + "]"
        if len(desc) + len(evidence_note) <= 2000:
            desc += evidence_note

    now = utc_now()
    inc_time = normalize_utc(payload.incident_datetime) if payload.incident_datetime else now

    incident = RentalIncident(
        booking_id=booking.id,
        incident_type=payload.incident_type,
        incident_datetime=inc_time,
        description=desc,
        estimated_damage_cost=payload.estimated_damage_cost,
        actual_repair_cost=None,
        police_report_reference=payload.police_report_reference.strip() if payload.police_report_reference else None,
        resolution_status="reported",
    )
    db.add(incident)
    db.flush()

    # Multi-layered audit trail in BookingStatusHistory
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
            changed_by=current_user.id,
            notes=f"Incident #{incident.id} reported: type='{incident.incident_type}', estimated_cost={incident.estimated_damage_cost} LKR.",
        )
        db.add(status_history)

    # Notify relevant counterparty
    vehicle = db.get(RentalVehicle, booking.vehicle_id)
    if vehicle:
        prov = db.get(ProviderProfile, vehicle.provider_id)
        target_notify_uid = prov.user_id if current_user.id == booking.user_id and prov else booking.user_id
        create_notification(
            db=db,
            user_id=target_notify_uid,
            title=f"Incident Reported: Booking #{booking.id}",
            message=f"A '{incident.incident_type}' incident was reported for booking #{booking.id} ({vehicle.vehicle_name}). Status: reported.",
            notification_type="rental_incident_reported",
            link_url=f"/traveler/rental-bookings/{booking.id}",
        )

    db.commit()
    db.refresh(incident)
    return incident


@router.get(
    "/{booking_id}/incidents",
    response_model=RentalIncidentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List all reported incidents for a rental booking (AR-08)",
)
def list_booking_incidents(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found.",
        )

    if not check_booking_access(booking, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only the booking traveler, vehicle provider, or administrator may view incidents.",
        )

    incidents = (
        db.query(RentalIncident)
        .filter(RentalIncident.booking_id == booking.id)
        .order_by(RentalIncident.created_at.desc())
        .all()
    )

    return RentalIncidentListResponse(
        booking_id=booking.id,
        total=len(incidents),
        items=[RentalIncidentResponse.model_validate(inc) for inc in incidents],
    )


@router.get(
    "/{booking_id}/incidents/{incident_id}",
    response_model=RentalIncidentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get details of a specific rental incident (AR-08)",
)
def get_booking_incident(
    booking_id: int,
    incident_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    booking = db.get(RentalBooking, booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rental booking not found.",
        )

    if not check_booking_access(booking, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Only the booking traveler, vehicle provider, or administrator may view this incident.",
        )

    incident = db.get(RentalIncident, incident_id)
    if not incident or incident.booking_id != booking.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rental incident #{incident_id} not found for booking #{booking.id}.",
        )

    return incident


# ==========================================
# CONFIRM VEHICLE HANDOVER
# ==========================================

def notify_rental_pickup(db, booking, vehicle):
    create_notification(
        db, booking.user_id, f"Rental Booking #{booking.id} Picked Up",
        f"Handover for {vehicle.vehicle_name} is complete.",
        "rental_booking_active", f"/traveler/rental-bookings/{booking.id}",
    )


@router.post(
    "/{booking_id}/handover",
    response_model=RentalBookingResponse,
    summary="Confirm vehicle handover to customer",
)
def confirm_rental_handover(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(RentalBooking.id == booking_id)
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(db, booking_vehicle_id)

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == booking.vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        if booking.status == "in_progress":
            raise HTTPException(
                status_code=400,
                detail="Vehicle has already been handed over",
            )

        if booking.status != "confirmed":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot handover booking with status '{booking.status}'. Booking must be 'confirmed'",
            )

        today = date.today()
        if booking.start_date and today < booking.start_date:
            raise HTTPException(
                status_code=400,
                detail="Cannot handover vehicle before booking start date",
            )

        verify_rental_handover_driver(db, booking, vehicle)
        previous = booking.status
        booking.status = "in_progress"
        record_native_event(db, booking, "rental_vehicle", provider.id, previous,
                            booking.status, current_user.id, booking.user_id, notify=False)
        notify_rental_pickup(db, booking, vehicle)

        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise


# ==========================================
# CONFIRM VEHICLE RETURN
# ==========================================

@router.post(
    "/{booking_id}/return",
    response_model=RentalBookingResponse,
    summary="Confirm vehicle return from customer",
)
def confirm_rental_return(
    booking_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        .first()
    )

    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Approved rental provider required",
        )

    try:
        booking_vehicle_id = (
            db.query(RentalBooking.vehicle_id)
            .filter(RentalBooking.id == booking_id)
            .scalar()
        )

        if booking_vehicle_id is None:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        lock_rental_vehicle(db, booking_vehicle_id)

        booking = (
            db.query(RentalBooking)
            .filter(
                RentalBooking.id == booking_id,
                RentalBooking.vehicle_id == booking_vehicle_id,
            )
            .populate_existing()
            .first()
        )

        if not booking:
            raise HTTPException(
                status_code=404,
                detail="Rental booking not found",
            )

        vehicle = (
            db.query(RentalVehicle)
            .filter(
                RentalVehicle.id == booking.vehicle_id,
                RentalVehicle.provider_id == provider.id,
            )
            .first()
        )

        if not vehicle:
            raise HTTPException(
                status_code=403,
                detail="You do not own this rental vehicle",
            )

        if booking.status == "completed":
            raise HTTPException(
                status_code=400,
                detail="Vehicle has already been returned",
            )

        if booking.status != "in_progress":
            raise HTTPException(
                status_code=400,
                detail=f"Cannot return booking with status '{booking.status}'. Booking must be 'in_progress'",
            )

        previous = booking.status
        booking.status = "completed"
        record_native_event(db, booking, "rental_vehicle", provider.id, previous,
                            booking.status, current_user.id, booking.user_id, notify=False)
        create_notification(
            db, booking.user_id, f"Rental Booking #{booking.id} Completed",
            f"Return of {vehicle.vehicle_name} is complete.",
            "rental_booking_completed", f"/traveler/rental-bookings/{booking.id}",
        )
        db.commit()
        db.refresh(booking)

        return booking

    except Exception:
        db.rollback()
        raise
