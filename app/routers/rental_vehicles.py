from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import ceil
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, or_, select, true
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider
from app.core.booking_transactions import lock_booking_resource
from app.core.publication import public_rental
from app.core.rental_capacity import TUK_TUK_MIN_PASSENGERS, tuk_tuk_passenger_limit, valid_tuk_tuk_seating_capacity
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import ListingReference, PromotionCampaign
from app.models.user import User
from app.routers.auth import get_current_user, get_optional_current_user
from app.services.rental_promotions import RENTAL_CAMPAIGN_MODULES, verified_rental_promotion_payment
from app.services.rental_availability import overlapping_rental_bookings
from app.schemas.rental_vehicle import (
    FuelPolicy,
    RentalAvailabilityBlockCreate,
    RentalAvailabilityBlockResponse,
    RentalCategoryItem,
    RentalLocationResponse,
    RentalPickupInput,
    RentalPickupResponse,
    RentalProviderContactResponse,
    RentalQuoteRequest,
    RentalQuoteResponse,
    RentalRatePlanResponse,
    RentalVehicleCreate,
    RentalVehicleDetailResponse,
    RentalVehicleDocumentCreate,
    RentalVehicleDocumentResponse,
    RentalVehicleImageResponse,
    RentalVehicleListItem,
    RentalVehiclePagination,
    RentalVehicleResponse,
    RentalVehicleUpdate,
    UnavailablePeriod,
    VehicleAvailabilityResponse,
    VehicleHelmetInfo,
    VehicleInsuranceSummary,
    VehicleType,
)


router = APIRouter(
    prefix="/api/v1/rental-vehicles",
    tags=["Rental Vehicles"],
)
pickup_router = APIRouter(prefix="/api/v1/provider/rental-locations", tags=["Rental"])


def require_rental_provider(
    provider: Annotated[ProviderProfile, Depends(require_approved_provider)],
) -> ProviderProfile:
    if provider.provider_type != "rental":
        raise HTTPException(403, detail="Rental provider access required")
    return provider


RentalProvider = Annotated[ProviderProfile, Depends(require_rental_provider)]
Db = Annotated[Session, Depends(get_db)]


def require_owned_pickup_location(db: Session, provider_id: int, location_id: int) -> None:
    location = db.query(RentalLocation).filter(
        RentalLocation.id == location_id, RentalLocation.provider_id == provider_id,
        RentalLocation.is_active == true(),
        RentalLocation.latitude.between(-90, 90),
        RentalLocation.longitude.between(-180, 180),
    ).first()
    if location is None:
        raise HTTPException(404, detail="Available rental pickup location not found")


@pickup_router.get("", response_model=list[RentalPickupResponse])
def list_rental_pickups(
    db: Db, provider: RentalProvider, page: Annotated[int, Query(ge=1, le=10000)] = 1,
):
    return (
        db.query(RentalLocation).filter(RentalLocation.provider_id == provider.id)
        .order_by(RentalLocation.id).offset((page - 1) * 50).limit(50).all()
    )


@pickup_router.post("", response_model=RentalPickupResponse, status_code=201)
def create_rental_pickup(payload: RentalPickupInput, db: Db, provider: RentalProvider):
    pickup = RentalLocation(**payload.model_dump(), provider_id=provider.id, is_active=False)
    db.add(pickup)
    db.commit()
    db.refresh(pickup)
    return pickup


@pickup_router.put("/{location_id}", response_model=RentalPickupResponse)
def update_rental_pickup(
    location_id: int, payload: RentalPickupInput, db: Db, provider: RentalProvider,
):
    owned = db.query(RentalLocation).filter(
        RentalLocation.id == location_id, RentalLocation.provider_id == provider.id,
    )
    pickup = owned.first()
    if pickup is None:
        raise HTTPException(404, detail="Rental pickup location not found")
    changes = payload.model_dump()
    if any(getattr(pickup, key) != value for key, value in changes.items()):
        snapshot = [
            getattr(RentalLocation, key) == getattr(pickup, key) for key in changes
        ]
        updated = owned.filter(*snapshot).update(
            {**changes, "is_active": False}, synchronize_session=False,
        )
        if updated != 1:
            db.rollback()
            raise HTTPException(409, detail="Rental pickup location changed. Retry the update.")
        db.commit()
        db.refresh(pickup)
    return pickup


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def tuk_tuk_capacity_metadata(vehicle: RentalVehicle) -> dict[str, int | None]:
    if vehicle.vehicle_type != "tuk_tuk":
        return {}
    limit = tuk_tuk_passenger_limit(vehicle.seating_capacity)
    return {
        "max_passengers": limit,
        "min_passengers": TUK_TUK_MIN_PASSENGERS if limit is not None else None,
    }


def get_active_rental_sponsorship(db: Session) -> tuple[set[int], set[int]]:
    current_time = utc_now()
    try:
        active_campaigns = (
            db.query(PromotionCampaign.provider_profile_id, PromotionCampaign.listing_reference_id,
                     RentalVehicle.id)
            .outerjoin(ListingReference, and_(
                ListingReference.id == PromotionCampaign.listing_reference_id,
                ListingReference.module_type.in_(RENTAL_CAMPAIGN_MODULES),
                ListingReference.provider_profile_id == PromotionCampaign.provider_profile_id,
            ))
            .outerjoin(RentalVehicle, and_(
                RentalVehicle.id == ListingReference.target_id,
                RentalVehicle.provider_id == PromotionCampaign.provider_profile_id,
            ))
            .filter(
                PromotionCampaign.target_module.in_(RENTAL_CAMPAIGN_MODULES),
                PromotionCampaign.status == "active",
                verified_rental_promotion_payment().exists(),
                PromotionCampaign.starts_at <= current_time,
                PromotionCampaign.ends_at > current_time,
            )
            .all()
        )
        return (
            {provider_id for provider_id, reference_id, _ in active_campaigns if reference_id is None},
            {vehicle_id for _, _, vehicle_id in active_campaigns if vehicle_id is not None},
        )
    except Exception:
        return set(), set()


def get_vehicle_insurance_summary(vehicle_id: int, db: Session) -> VehicleInsuranceSummary:
    """
    Evaluates vehicle insurance document status strictly against approved records (AR-12).
    - Returns verified ONLY when an approved insurance certificate is verified and not expired.
    - Masks policy/certificate numbers for public data privacy.
    - Never exposes private file storage paths or unverified claims.
    """
    today = date.today()
    active_policy = and_(
        RentalVehicleDocument.is_verified == true(),
        RentalVehicleDocument.expiry_date >= today,
        or_(RentalVehicleDocument.insurance_effective_date.is_(None),
            RentalVehicleDocument.insurance_effective_date <= today),
    )
    insurance_doc = (
        db.query(RentalVehicleDocument)
        .filter(
            RentalVehicleDocument.vehicle_id == vehicle_id,
            RentalVehicleDocument.document_type == "insurance_certificate",
        )
        .order_by(case((active_policy, 1), else_=0).desc(),
                  RentalVehicleDocument.created_at.desc(), RentalVehicleDocument.id.desc())
        .first()
    )

    if not insurance_doc:
        return VehicleInsuranceSummary(
            is_insured=False,
            insurance_status="not_provided",
            policy_number_masked=None,
            expiry_date=None,
        )

    is_expired = bool(insurance_doc.expiry_date and insurance_doc.expiry_date < today)
    is_future = bool(insurance_doc.insurance_effective_date and insurance_doc.insurance_effective_date > today)

    if insurance_doc.is_verified and insurance_doc.expiry_date is not None and not is_expired and not is_future:
        status_str = "verified"
        is_ins = True
    elif is_expired:
        status_str = "expired"
        is_ins = False
    elif insurance_doc.is_verified and is_future:
        status_str = "not_yet_effective"
        is_ins = False
    else:
        status_str = "unverified"
        is_ins = False

    masked_policy = None
    policy_number = insurance_doc.insurance_policy_number or insurance_doc.document_number
    if policy_number:
        clean_num = policy_number.strip()
        if len(clean_num) <= 4:
            masked_policy = "****"
        else:
            masked_policy = f"{clean_num[:2]}****{clean_num[-4:]}"

    return VehicleInsuranceSummary(
        is_insured=is_ins,
        insurance_status=status_str,
        policy_number_masked=masked_policy,
        expiry_date=insurance_doc.expiry_date,
        effective_date=insurance_doc.insurance_effective_date,
        exclusions=insurance_doc.insurance_exclusions if is_ins else None,
        evidence_verified=bool(is_ins and insurance_doc.file_reference.strip()),
        coverage_amount=insurance_doc.coverage_amount if is_ins else None,
        deductible_amount=insurance_doc.deductible_amount if is_ins else None,
        insurance_provider=insurance_doc.insurance_provider if is_ins else None,
    )


def get_vehicle_fuel_policy(vehicle: RentalVehicle) -> FuelPolicy:
    """
    AR-05: Determines disclosed fuel policy.
    - Bicycles and electric vehicles are 'not_applicable'.
    - Combustion vehicles (petrol, diesel, hybrid) default to structured fuel_policy
      or 'same_to_same' / 'full_to_full' if disclosed in vehicle description.
    """
    if vehicle.vehicle_type == "bicycle" or (vehicle.fuel_type and vehicle.fuel_type.lower() == "electric"):
        return "not_applicable"
    db_policy = getattr(vehicle, "fuel_policy", None)
    if db_policy in ("same_to_same", "full_to_full", "not_applicable"):
        return db_policy
    if vehicle.description and "full_to_full" in vehicle.description.lower().replace("-", "_"):
        return "full_to_full"
    return "same_to_same"


def get_vehicle_helmet_info(vehicle: RentalVehicle, db: Session) -> VehicleHelmetInfo:
    """
    AR-03: Resolves helmet availability, sizes, and condition for two-wheelers.
    - Tuk-Tuk: helmets are not required.
    - Bike / Bicycle: helmets are required.
    - First checks structured vehicle columns (helmet_count, helmets_included, helmet_sizes).
    - If not structured, inspects vehicle description for tags.
    - Otherwise returns unprovided without inventing false claims.
    """
    if vehicle.vehicle_type == "tuk_tuk":
        return VehicleHelmetInfo(
            helmet_provided=False,
            helmet_count=None,
            helmet_sizes=None,
            helmet_condition=None,
            requires_helmet=False,
            notes="Helmets are not required for tuk-tuks in Sri Lanka.",
        )

    # Check structured helmet columns first (AR-03)
    db_count = getattr(vehicle, "helmet_count", None)
    db_included = getattr(vehicle, "helmets_included", None)
    db_sizes = getattr(vehicle, "helmet_sizes", None)
    if db_count is not None and db_count > 0:
        sizes_list = [s.strip() for s in db_sizes.split(",") if s.strip()] if db_sizes else None
        return VehicleHelmetInfo(
            helmet_provided=True,
            helmet_count=db_count,
            helmet_sizes=sizes_list,
            helmet_condition=vehicle.helmet_condition,
            requires_helmet=True,
            notes=f"Provided with {db_count} helmet(s)",
        )
    elif db_included is not None and db_included:
        sizes_list = [s.strip() for s in db_sizes.split(",") if s.strip()] if db_sizes else None
        return VehicleHelmetInfo(
            helmet_provided=True,
            helmet_count=None,
            helmet_sizes=sizes_list,
            helmet_condition=vehicle.helmet_condition,
            requires_helmet=True,
            notes="Helmets included with vehicle rental",
        )

    # For two-wheelers (bike, bicycle): requires_helmet is True
    import re
    desc = vehicle.description or ""
    if "helmet" in desc.lower():
        # Tag format: [Helmets: count=2, sizes=M,L, condition=Good]
        tag_match = re.search(r"\[Helmets:\s*([^\]]+)\]", desc, re.IGNORECASE)
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
                notes=f"Provided with {count} helmet(s)",
            )
        # Free-text format: "2 helmets" or "includes 1 helmet"
        count_match = re.search(r"(\d+)\s*helmets?", desc, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else None
        sizes = []
        for s in ["XS", "S", "M", "L", "XL", "XXL"]:
            if re.search(r"\b" + s + r"\b", desc):
                sizes.append(s)
        return VehicleHelmetInfo(
            helmet_provided=count is not None and count > 0,
            helmet_count=count,
            helmet_sizes=sizes or None,
            requires_helmet=True,
            notes=f"Disclosed by provider: {count} helmet(s)" if count is not None else "Helmet quantity is unspecified.",
        )

    return VehicleHelmetInfo(
        helmet_provided=False,
        helmet_count=None,
        helmet_sizes=None,
        helmet_condition=None,
        requires_helmet=True,
        notes="No helmet availability data recorded by provider for this vehicle.",
    )


# ==============================================================================
# TR-03: VEHICLE CATEGORIES & ACTIVE FLEET COUNTS
# Defined before dynamic {vehicle_id} to prevent path parameter collision
# ==============================================================================

@router.get(
    "/categories",
    response_model=list[RentalCategoryItem],
    summary="Get supported rental vehicle categories and active fleet counts (TR-03)",
)
def get_rental_vehicle_categories(
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns supported vehicle categories with metadata, descriptions, active fleet counts,
    and driving license/helmet requirement flags.
    """
    counts = (
        db.query(
            RentalVehicle.vehicle_type,
            func.count(RentalVehicle.id),
        )
        .filter(
            RentalVehicle.approval_status == "approved",
            RentalVehicle.status == "available",
        )
        .group_by(RentalVehicle.vehicle_type)
        .all()
    )
    count_map = {c[0]: c[1] for c in counts}

    categories = [
        RentalCategoryItem(
            category="bike",
            name="Motorbikes & Scooters",
            description="Explore Sri Lanka on two wheels with high fuel efficiency, automatic scooters, and agile street motorbikes.",
            requires_license=True,
            requires_helmet=True,
            active_fleet_count=count_map.get("bike", 0),
            sample_image_url="https://images.ceylongo.com/categories/bike.jpg",
        ),
        RentalCategoryItem(
            category="bicycle",
            name="City & Mountain Bicycles",
            description="Eco-friendly, healthy, and perfect for exploring coastal roads, historic towns, and scenic trails.",
            requires_license=False,
            requires_helmet=False,
            active_fleet_count=count_map.get("bicycle", 0),
            sample_image_url="https://images.ceylongo.com/categories/bicycle.jpg",
        ),
        RentalCategoryItem(
            category="tuk_tuk",
            name="Tuk-Tuk (3-Wheeler Auto Rickshaws)",
            description="The iconic Sri Lankan experience for small groups, scenic country roads, and authentic local travel.",
            requires_license=True,
            requires_helmet=False,
            active_fleet_count=count_map.get("tuk_tuk", 0),
            sample_image_url="https://images.ceylongo.com/categories/tuktuk.jpg",
        ),
    ]

    return categories


# ==============================================================================
# TR-01 & TR-02: RENTAL VEHICLE DISCOVERY & MULTI-CRITERIA SEARCH
# ==============================================================================

@router.get(
    "",
    response_model=RentalVehiclePagination,
    summary="Discover and search available rental vehicles (TR-01, TR-02)",
)
def get_rental_vehicles(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
    district: Annotated[str | None, Query(description="Filter by district name")] = None,
    city: Annotated[str | None, Query(description="Filter by city name")] = None,
    vehicle_type: Annotated[VehicleType | None, Query(description="Vehicle category filter")] = None,
    min_price: Annotated[Decimal | None, Query(ge=0, description="Minimum daily rate")] = None,
    max_price: Annotated[Decimal | None, Query(ge=0, description="Maximum daily rate")] = None,
    pickup_at: Annotated[datetime | None, Query(description="Requested pickup datetime")] = None,
    return_at: Annotated[datetime | None, Query(description="Requested return datetime")] = None,
    verified_only: Annotated[bool, Query(description="Filter only verified providers (TR-20)")] = False,
    sort_by: Annotated[
        Literal["newest", "price_asc", "price_desc"],
        Query(description="Sorting order"),
    ] = "newest",
):
    """
    Search and browse approved, active rental vehicle listings with cover image, pricing,
    location summary, provider verification badge, and pagination.
    """
    query = (
        db.query(RentalVehicle)
        .outerjoin(RentalVehicle.location)
        .outerjoin(RentalVehicle.provider)
        .filter(
            public_rental(),
        )
    )

    # 1. Location text filters (district and city)
    if district and district.strip():
        dist_pat = f"%{district.strip()}%"
        query = query.filter(
            or_(
                RentalVehicle.district.ilike(dist_pat),
                RentalLocation.district.ilike(dist_pat),
            )
        )

    if city and city.strip():
        city_pat = f"%{city.strip()}%"
        query = query.filter(
            or_(
                RentalVehicle.city.ilike(city_pat),
                RentalLocation.city.ilike(city_pat),
            )
        )

    # 2. Vehicle category filter
    if vehicle_type:
        query = query.filter(RentalVehicle.vehicle_type == vehicle_type)

    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(400, "min_price must not exceed max_price")
    if (pickup_at is None) != (return_at is None):
        raise HTTPException(400, "pickup_at and return_at must be supplied together")

    # 3. Price range filters
    if min_price is not None:
        query = query.filter(RentalVehicle.price_per_day >= min_price)
    if max_price is not None:
        query = query.filter(RentalVehicle.price_per_day <= max_price)

    # 4. Verified provider filter (TR-20)
    if verified_only:
        query = query.filter(ProviderProfile.approval_status == "approved")

    # 5. Date availability filter (TR-02)
    if pickup_at and return_at:
        norm_pickup = normalize_utc(pickup_at)
        norm_return = normalize_utc(return_at)
        if norm_return <= norm_pickup:
            raise HTTPException(
                status_code=400,
                detail="return_at must be strictly after pickup_at",
            )

        current_time = utc_now()

        # Exclude vehicles with active overlapping bookings
        booking_overlap_subq = (
            db.query(RentalBooking.vehicle_id)
            .filter(
                RentalBooking.status.in_(["pending", "confirmed", "active", "in_progress"]),
                or_(
                    RentalBooking.status != "pending",
                    RentalBooking.expires_at > current_time,
                    RentalBooking.expires_at.is_(None),
                ),
                RentalBooking.pickup_at < norm_return,
                RentalBooking.return_at > norm_pickup,
            )
            .subquery()
        )

        # Exclude vehicles with active maintenance/blackout blocks
        block_overlap_subq = (
            db.query(RentalAvailabilityBlock.vehicle_id)
            .filter(
                RentalAvailabilityBlock.is_active == True,
                RentalAvailabilityBlock.start_datetime < norm_return,
                RentalAvailabilityBlock.end_datetime > norm_pickup,
            )
            .subquery()
        )

        query = query.filter(
            RentalVehicle.id.notin_(select(booking_overlap_subq.c.vehicle_id)),
            RentalVehicle.id.notin_(select(block_overlap_subq.c.vehicle_id)),
        )

    # 6. Sorting with sponsored promotion prioritization (TR-19)
    sponsored_provider_ids, sponsored_vehicle_ids = get_active_rental_sponsorship(db)
    sponsored_order = case(
        (or_(RentalVehicle.provider_id.in_(sponsored_provider_ids),
             RentalVehicle.id.in_(sponsored_vehicle_ids)), 0),
        else_=1,
    ).asc()

    if sort_by == "price_asc":
        query = query.order_by(sponsored_order, RentalVehicle.price_per_day.asc())
    elif sort_by == "price_desc":
        query = query.order_by(sponsored_order, RentalVehicle.price_per_day.desc())
    else:
        query = query.order_by(sponsored_order, RentalVehicle.created_at.desc())

    # 7. Pagination
    total = query.count()
    pages = max(1, ceil(total / limit)) if total > 0 else 0
    vehicles = query.offset((page - 1) * limit).limit(limit).all()

    items: list[RentalVehicleListItem] = []
    for v in vehicles:
        # Determine primary image URL
        primary_img = None
        if v.images:
            for img in v.images:
                if img.is_primary:
                    primary_img = img.image_url
                    break
            if not primary_img and len(v.images) > 0:
                primary_img = sorted(v.images, key=lambda x: x.display_order)[0].image_url

        loc_name = v.location.location_name if v.location else None
        loc_district = v.district or (v.location.district if v.location else None)
        loc_city = v.city or (v.location.city if v.location else None)

        is_verified = False
        prov_name = None
        if v.provider:
            is_verified = (v.provider.approval_status == "approved")
            prov_name = v.provider.business_name

        ins = get_vehicle_insurance_summary(v.id, db)
        fuel_pol = get_vehicle_fuel_policy(v)
        helmet_inf = get_vehicle_helmet_info(v, db)

        items.append(
            RentalVehicleListItem(
                id=v.id,
                provider_id=v.provider_id,
                provider_business_name=prov_name,
                is_provider_verified=is_verified,
                vehicle_type=v.vehicle_type,
                vehicle_name=v.vehicle_name,
                description=v.description,
                price_per_day=v.price_per_day,
                currency="LKR",
                status=v.status,
                model_year=v.model_year,
                seating_capacity=v.seating_capacity,
                **tuk_tuk_capacity_metadata(v),
                transmission=v.transmission,
                fuel_type=v.fuel_type,
                district=loc_district,
                city=loc_city,
                location_id=v.location_id,
                location_name=loc_name,
                primary_image_url=primary_img,
                is_sponsored=(v.provider_id in sponsored_provider_ids or v.id in sponsored_vehicle_ids),
                cancellation_policy=v.cancellation_policy,
                free_cancellation_hours=v.free_cancellation_hours,
                is_insured=ins.is_insured,
                insurance_status=ins.insurance_status,
                fuel_policy=fuel_pol,
                helmets=helmet_inf,
                created_at=v.created_at,
            )
        )

    return RentalVehiclePagination(
        total=total,
        page=page,
        limit=limit,
        pages=pages,
        items=items,
    )


# ==============================================================================
# TR-04: VEHICLE DETAILS & SPECIFICATIONS
# ==============================================================================

@router.get(
    "/{vehicle_id}",
    response_model=RentalVehicleDetailResponse,
    summary="Get vehicle full details and specifications (TR-04)",
)
def get_rental_vehicle(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_current_user)],
):
    """
    Returns full technical specifications, location details, gallery images, active
    rate plans, and provider verification status. Returns 404 for unapproved vehicles.
    """
    visibility = public_rental()
    if user is not None and user.role == "admin":
        visibility = true()
    elif user is not None and user.role == "provider":
        owned_profiles = db.query(ProviderProfile.id).filter(
            ProviderProfile.user_id == user.id,
            ProviderProfile.provider_type == "rental",
            ProviderProfile.approval_status == "approved",
        )
        visibility = or_(visibility, RentalVehicle.provider_id.in_(owned_profiles.statement))
    vehicle = db.query(RentalVehicle).filter(
        RentalVehicle.id == vehicle_id, visibility,
    ).first()
    if vehicle is None:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    # Primary image detection
    primary_img = None
    if vehicle.images:
        for img in vehicle.images:
            if img.is_primary:
                primary_img = img.image_url
                break
        if not primary_img and len(vehicle.images) > 0:
            primary_img = sorted(vehicle.images, key=lambda x: x.display_order)[0].image_url

    loc_district = vehicle.district or (vehicle.location.district if vehicle.location else None)
    loc_city = vehicle.city or (vehicle.location.city if vehicle.location else None)

    is_verified = False
    prov_name = None
    if vehicle.provider:
        is_verified = (vehicle.provider.approval_status == "approved")
        prov_name = vehicle.provider.business_name

    sponsored_provider_ids, sponsored_vehicle_ids = get_active_rental_sponsorship(db)

    # Sort images by display_order
    sorted_images = sorted(vehicle.images, key=lambda img: (img.display_order, img.id))
    active_plans = [p for p in vehicle.rate_plans if p.is_active]
    ins_summary = get_vehicle_insurance_summary(vehicle.id, db)
    fuel_pol = get_vehicle_fuel_policy(vehicle)
    helmet_inf = get_vehicle_helmet_info(vehicle, db)

    return RentalVehicleDetailResponse(
        id=vehicle.id,
        provider_id=vehicle.provider_id,
        provider_business_name=prov_name,
        is_provider_verified=is_verified,
        vehicle_type=vehicle.vehicle_type,
        vehicle_name=vehicle.vehicle_name,
        description=vehicle.description,
        price_per_day=vehicle.price_per_day,
        currency="LKR",
        status=vehicle.status,
        model_year=vehicle.model_year,
        seating_capacity=vehicle.seating_capacity,
        **tuk_tuk_capacity_metadata(vehicle),
        transmission=vehicle.transmission,
        fuel_type=vehicle.fuel_type,
        district=loc_district,
        city=loc_city,
        location_id=vehicle.location_id,
        location=vehicle.location,
        cancellation_policy=vehicle.cancellation_policy,
        free_cancellation_hours=vehicle.free_cancellation_hours,
        security_deposit=(
            vehicle.security_deposit
            if (getattr(vehicle, "security_deposit", None) is not None and vehicle.security_deposit >= Decimal("0.00"))
            else {
                "bike": Decimal("10000.00"),
                "tuk_tuk": Decimal("15000.00"),
                "bicycle": Decimal("3000.00"),
            }.get(vehicle.vehicle_type, Decimal("10000.00"))
        ),
        security_deposit_terms="Refundable security deposit collected at vehicle pickup and returned following clean vehicle return inspection.",
        insurance=ins_summary,
        fuel_policy=fuel_pol,
        helmets=helmet_inf,
        primary_image_url=primary_img,
        images=sorted_images,
        rate_plans=active_plans,
        approval_status=vehicle.approval_status,
        is_sponsored=(vehicle.provider_id in sponsored_provider_ids or vehicle.id in sponsored_vehicle_ids),
        created_at=vehicle.created_at,
        updated_at=vehicle.updated_at,
    )


# ==============================================================================
# TR-05: VEHICLE PHOTO GALLERY
# ==============================================================================

@router.get(
    "/{vehicle_id}/images",
    response_model=list[RentalVehicleImageResponse],
    summary="Get vehicle photo gallery ordered by display order (TR-05)",
)
def get_rental_vehicle_images(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns all approved photos for a vehicle ordered chronologically by display_order.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    images = (
        db.query(RentalVehicleImage)
        .filter(RentalVehicleImage.vehicle_id == vehicle.id)
        .order_by(
            RentalVehicleImage.display_order.asc(),
            RentalVehicleImage.id.asc(),
        )
        .all()
    )

    return images


# ==============================================================================
# TR-06: VEHICLE PICKUP & RETURN LOCATIONS
# ==============================================================================

@router.get(
    "/{vehicle_id}/locations",
    response_model=list[RentalLocationResponse],
    summary="Get designated pickup and drop-off hubs for vehicle (TR-06)",
)
def get_rental_vehicle_locations(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns pickup and drop-off hubs designated for a specific vehicle with GPS coordinates.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    locations = (
        db.query(RentalLocation)
        .filter(
            RentalLocation.provider_id == vehicle.provider_id,
            RentalLocation.is_active == true(),
        )
        .order_by(
            case((RentalLocation.id == vehicle.location_id, 0), else_=1).asc(),
            RentalLocation.location_name.asc(),
        )
        .all()
    )

    return locations


# ==============================================================================
# TR-17: CONTACT RENTAL PROVIDER
# ==============================================================================

@router.get(
    "/{vehicle_id}/provider-contact",
    response_model=RentalProviderContactResponse,
    summary="Get approved contact channels for rental provider (TR-17)",
)
def get_rental_provider_contact(
    vehicle_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns approved provider contact channels (business name, phone, address, etc.)
    for authenticated travelers. Returns 404 for unapproved vehicles or missing providers.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    provider = db.get(ProviderProfile, vehicle.provider_id)
    if not provider:
        raise HTTPException(
            status_code=404,
            detail="Provider profile not found",
        )

    address_parts = [
        p
        for p in [
            provider.business_landmark or provider.permanent_landmark,
            provider.business_city or provider.permanent_city,
            provider.business_district or provider.district,
            provider.business_province or provider.permanent_province,
        ]
        if p
    ]
    formatted_address = ", ".join(address_parts) if address_parts else f"{provider.district}, Sri Lanka"

    return RentalProviderContactResponse(
        provider_id=provider.id,
        business_name=provider.business_name,
        phone=provider.phone,
        secondary_phone=provider.secondary_phone,
        district=provider.district,
        city=provider.business_city or provider.permanent_city,
        province=provider.business_province or provider.permanent_province,
        business_address=formatted_address,
        approval_status=provider.approval_status,
        is_verified=(provider.approval_status == "approved"),
    )


# ==============================================================================
# TR-07: PRICING PLANS & QUOTE CALCULATOR
# ==============================================================================

@router.get(
    "/{vehicle_id}/rate-plans",
    response_model=list[RentalRatePlanResponse],
    summary="Get vehicle active rate plans (TR-07)",
)
def get_rental_vehicle_rate_plans(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Returns all active pricing tiers (hourly, daily, weekly, monthly) configured for a vehicle.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    plans = (
        db.query(RentalRatePlan)
        .filter(
            RentalRatePlan.vehicle_id == vehicle.id,
            RentalRatePlan.is_active == True,
        )
        .order_by(
            RentalRatePlan.duration_type.asc(),
            RentalRatePlan.rate.asc(),
        )
        .all()
    )

    return plans


@router.post(
    "/{vehicle_id}/calculate-quote",
    response_model=RentalQuoteResponse,
    summary="Calculate transparent rental quote including deposit and mileage terms (TR-07)",
)
def calculate_rental_quote(
    vehicle_id: int,
    quote_request: RentalQuoteRequest,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Evaluates duration and selects the most cost-effective active rate plan (hourly, daily,
    weekly, or monthly). Calculates base price, mileage quota, extra km charges, and security deposit.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    pickup_at = normalize_utc(quote_request.pickup_at)
    return_at = normalize_utc(quote_request.return_at)

    if return_at <= pickup_at:
        raise HTTPException(
            status_code=400,
            detail="Return date must be strictly after pickup date",
        )

    duration_seconds = (return_at - pickup_at).total_seconds()
    duration_hours = round(duration_seconds / 3600.0, 2)
    duration_days = max(1, int(ceil(duration_seconds / 86400.0)))

    # Fetch active rate plans
    rate_plans = (
        db.query(RentalRatePlan)
        .filter(
            RentalRatePlan.vehicle_id == vehicle.id,
            RentalRatePlan.is_active == True,
        )
        .all()
    )

    plans_by_type = {p.duration_type: p for p in rate_plans}

    # Best-fit pricing tier selection:
    if duration_hours < 24 and "hourly" in plans_by_type:
        plan = plans_by_type["hourly"]
        h_count = max(1, int(ceil(duration_hours)))
        base_price = (plan.rate * Decimal(str(h_count))).quantize(Decimal("0.01"))
        plan_name = plan.plan_name
        duration_type = "hourly"
        rate = plan.rate
        included_km = (plan.included_km_per_day or 50) * duration_days
        extra_km_charge = plan.extra_km_charge
    elif duration_days >= 30 and "monthly" in plans_by_type:
        plan = plans_by_type["monthly"]
        months = max(1, duration_days // 30)
        remaining_days = duration_days % 30
        daily_rate = plans_by_type.get("daily", None)
        d_rate = daily_rate.rate if daily_rate else vehicle.price_per_day
        base_price = ((plan.rate * months) + (d_rate * remaining_days)).quantize(Decimal("0.01"))
        plan_name = plan.plan_name
        duration_type = "monthly"
        rate = plan.rate
        included_km = (plan.included_km_per_day or 100) * duration_days
        extra_km_charge = plan.extra_km_charge
    elif duration_days >= 7 and "weekly" in plans_by_type:
        plan = plans_by_type["weekly"]
        weeks = max(1, duration_days // 7)
        remaining_days = duration_days % 7
        daily_rate = plans_by_type.get("daily", None)
        d_rate = daily_rate.rate if daily_rate else vehicle.price_per_day
        base_price = ((plan.rate * weeks) + (d_rate * remaining_days)).quantize(Decimal("0.01"))
        plan_name = plan.plan_name
        duration_type = "weekly"
        rate = plan.rate
        included_km = (plan.included_km_per_day or 100) * duration_days
        extra_km_charge = plan.extra_km_charge
    elif "daily" in plans_by_type:
        plan = plans_by_type["daily"]
        base_price = (plan.rate * Decimal(str(duration_days))).quantize(Decimal("0.01"))
        plan_name = plan.plan_name
        duration_type = "daily"
        rate = plan.rate
        included_km = (plan.included_km_per_day or 100) * duration_days
        extra_km_charge = plan.extra_km_charge
    else:
        # Standard daily rate fallback
        base_price = (vehicle.price_per_day * Decimal(str(duration_days))).quantize(Decimal("0.01"))
        plan_name = "Standard Daily Rate"
        duration_type = "daily"
        rate = vehicle.price_per_day
        included_km = 100 * duration_days
        extra_km_charge = Decimal("50.00") if vehicle.vehicle_type != "bicycle" else None

    # Refundable security deposit based on vehicle or category fallback
    v_deposit = getattr(vehicle, "security_deposit", None)
    if v_deposit is not None and v_deposit >= Decimal("0.00"):
        security_deposit = v_deposit
    else:
        deposit_map = {
            "bike": Decimal("10000.00"),
            "tuk_tuk": Decimal("15000.00"),
            "bicycle": Decimal("3000.00"),
        }
        security_deposit = deposit_map.get(vehicle.vehicle_type, Decimal("10000.00"))

    total_price = base_price
    ins_summary = get_vehicle_insurance_summary(vehicle.id, db)
    fuel_pol = get_vehicle_fuel_policy(vehicle)

    return RentalQuoteResponse(
        vehicle_id=vehicle.id,
        seating_capacity=vehicle.seating_capacity,
        **tuk_tuk_capacity_metadata(vehicle),
        pickup_at=pickup_at,
        return_at=return_at,
        duration_hours=duration_hours,
        duration_days=duration_days,
        selected_plan_name=plan_name,
        duration_type=duration_type,
        rate=rate,
        base_price=base_price,
        total_price=total_price,
        currency="LKR",
        included_km=included_km,
        extra_km_charge=extra_km_charge,
        security_deposit=security_deposit,
        cancellation_policy=vehicle.cancellation_policy,
        free_cancellation_hours=vehicle.free_cancellation_hours,
        is_insured=ins_summary.is_insured,
        insurance_status=ins_summary.insurance_status,
        fuel_policy=fuel_pol,
    )


# ==============================================================================
# TR-08: AVAILABILITY CALENDAR
# ==============================================================================

@router.get(
    "/{vehicle_id}/availability",
    response_model=VehicleAvailabilityResponse,
    summary="Get vehicle availability calendar and unavailable ranges (TR-08)",
)
def get_rental_vehicle_availability(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
    start_date: Annotated[datetime | None, Query(description="Start of inspection window")] = None,
    end_date: Annotated[datetime | None, Query(description="End of inspection window")] = None,
):
    """
    Exposes vehicle availability windows, booked blocks, and maintenance periods
    for interactive calendar views.
    """
    vehicle = db.get(RentalVehicle, vehicle_id)
    if not vehicle or vehicle.approval_status != "approved":
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    current_time = utc_now()
    norm_start = normalize_utc(start_date) if start_date else None
    norm_end = normalize_utc(end_date) if end_date else None

    if norm_start and norm_end and norm_end <= norm_start:
        raise HTTPException(
            status_code=400,
            detail="end_date must be strictly after start_date",
        )

    # 1. Query conflicting bookings
    booking_q = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.vehicle_id == vehicle.id,
            RentalBooking.status.in_(["pending", "confirmed", "active", "in_progress"]),
            or_(
                RentalBooking.status != "pending",
                RentalBooking.expires_at > current_time,
                RentalBooking.expires_at.is_(None),
            ),
        )
    )
    if norm_start:
        booking_q = booking_q.filter(RentalBooking.return_at > norm_start)
    if norm_end:
        booking_q = booking_q.filter(RentalBooking.pickup_at < norm_end)

    bookings = booking_q.all()

    # 2. Query conflicting availability blocks
    block_q = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.vehicle_id == vehicle.id,
            RentalAvailabilityBlock.is_active == True,
        )
    )
    if norm_start:
        block_q = block_q.filter(RentalAvailabilityBlock.end_datetime > norm_start)
    if norm_end:
        block_q = block_q.filter(RentalAvailabilityBlock.start_datetime < norm_end)

    blocks = block_q.all()

    periods: list[UnavailablePeriod] = []
    for b in bookings:
        periods.append(
            UnavailablePeriod(
                start_at=b.pickup_at,
                end_at=b.return_at,
                reason="booked",
            )
        )
    for blk in blocks:
        periods.append(
            UnavailablePeriod(
                start_at=blk.start_datetime,
                end_at=blk.end_datetime,
                reason="maintenance",
            )
        )

    periods.sort(key=lambda p: p.start_at)

    is_available = vehicle.status == "available"
    if norm_start and norm_end:
        is_available = is_available and len(periods) == 0

    return VehicleAvailabilityResponse(
        vehicle_id=vehicle.id,
        is_available=is_available,
        query_start=norm_start,
        query_end=norm_end,
        unavailable_periods=periods,
    )


# ==============================================================================
# PROVIDER FLEET MANAGEMENT (EXISTING ROUTES PRESERVED)
# ==============================================================================

@router.post(
    "",
    response_model=RentalVehicleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new rental vehicle (Approved providers only)",
)
def create_rental_vehicle(
    vehicle_data: RentalVehicleCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
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
            detail="Only rental providers can add vehicles",
        )

    if provider.approval_status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Rental provider is not approved",
        )

    if vehicle_data.location_id is not None:
        require_owned_pickup_location(db, provider.id, vehicle_data.location_id)

    if vehicle_data.vehicle_type == "tuk_tuk" and not valid_tuk_tuk_seating_capacity(vehicle_data.seating_capacity):
        raise HTTPException(422, "Tuk-tuk total seating capacity must be 3 or 4")

    new_vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type=vehicle_data.vehicle_type,
        vehicle_name=vehicle_data.vehicle_name,
        description=vehicle_data.description,
        price_per_day=vehicle_data.price_per_day,
        location_id=vehicle_data.location_id,
        status="available",
        model_year=vehicle_data.model_year,
        license_plate=vehicle_data.license_plate,
        seating_capacity=vehicle_data.seating_capacity,
        transmission=vehicle_data.transmission,
        fuel_type=vehicle_data.fuel_type,
        district=vehicle_data.district,
        city=vehicle_data.city,
        cancellation_policy=vehicle_data.cancellation_policy,
        free_cancellation_hours=vehicle_data.free_cancellation_hours,
        helmet_count=vehicle_data.helmet_count,
        helmets_included=vehicle_data.helmets_included,
        helmet_sizes=vehicle_data.helmet_sizes,
        helmet_condition=vehicle_data.helmet_condition,
        approval_status="approved" if current_user.role == "admin" else "pending",
    )

    try:
        db.add(new_vehicle)
        db.commit()
        db.refresh(new_vehicle)
    except Exception:
        db.rollback()
        raise

    return new_vehicle


@router.patch(
    "/{vehicle_id}",
    response_model=RentalVehicleResponse,
    summary="Update rental vehicle details (Owner provider only)",
)
def update_rental_vehicle(
    vehicle_id: int,
    vehicle_data: RentalVehicleUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
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
            detail="Only rental providers can update vehicles",
        )

    if provider.approval_status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Rental provider is not approved",
        )

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )

    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    update_data = vehicle_data.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(
            status_code=422,
            detail="No fields provided for update",
        )

    lock_booking_resource(db, f"rental_vehicle_booking_{vehicle.id}")
    db.refresh(vehicle)
    if update_data.get("location_id") is not None:
        require_owned_pickup_location(db, provider.id, update_data["location_id"])

    if {"seating_capacity", "vehicle_type"}.intersection(update_data):
        target_type = update_data.get("vehicle_type", vehicle.vehicle_type)
        target_seats = update_data.get("seating_capacity", vehicle.seating_capacity)
        if target_type == "tuk_tuk" and not valid_tuk_tuk_seating_capacity(target_seats):
            raise HTTPException(422, "Tuk-tuk total seating capacity must be 3 or 4")

    # Update vehicle fields
    for field, value in update_data.items():
        if field in {"approval_status", "verification_status"} and current_user.role != "admin":
            continue
        if value is None and field not in {
            "description", "cancellation_policy", "license_plate", "location_id",
            "helmet_count", "helmets_included", "helmet_sizes", "helmet_condition",
        }:
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )
        setattr(vehicle, field, value)

    try:
        db.commit()
        db.refresh(vehicle)
    except Exception:
        db.rollback()
        raise

    return vehicle


# ==========================================
# RENTAL AVAILABILITY BLOCKS
# ==========================================

@router.post(
    "/{vehicle_id}/availability-blocks",
    response_model=RentalAvailabilityBlockResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create rental vehicle availability block",
)
def create_rental_availability_block(
    vehicle_id: int,
    payload: RentalAvailabilityBlockCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """
    Block calendar dates for vehicle maintenance, repair, or inspection.
    Enforces approved rental provider ownership and checks for booking conflicts.
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

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )

    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found or not owned by you",
        )

    lock_booking_resource(db, f"rental_vehicle_booking_{vehicle_id}")
    start_at = datetime.combine(payload.start_date, datetime.min.time())
    end_at = datetime.combine(payload.end_date + timedelta(days=1), datetime.min.time())
    conflicting_booking = overlapping_rental_bookings(
        db, vehicle_id, start_at, end_at, utc_now(),
    ).first()

    if conflicting_booking:
        raise HTTPException(
            status_code=409,
            detail="Cannot block dates that have existing bookings",
        )

    # 2. Check for overlapping active availability blocks
    overlapping_block = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.vehicle_id == vehicle_id,
            RentalAvailabilityBlock.is_active.is_(True),
            RentalAvailabilityBlock.start_datetime < end_at,
            RentalAvailabilityBlock.end_datetime > start_at,
        )
        .first()
    )

    if overlapping_block:
        raise HTTPException(
            status_code=409,
            detail="Overlapping availability block already exists for these dates",
        )

    new_block = RentalAvailabilityBlock(
        vehicle_id=vehicle_id,
        start_datetime=start_at,
        end_datetime=end_at,
        block_type=payload.block_type,
        reason=payload.reason,
        is_active=True,
    )

    try:
        db.add(new_block)
        db.commit()
        db.refresh(new_block)
    except Exception:
        db.rollback()
        raise

    return new_block


@router.get(
    "/{vehicle_id}/availability-blocks",
    response_model=list[RentalAvailabilityBlockResponse],
    summary="List active rental availability blocks",
)
def list_rental_availability_blocks(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
):
    """List active availability blocks for a rental vehicle."""
    vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == vehicle_id, public_rental()).first()
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

    blocks = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.vehicle_id == vehicle_id,
            RentalAvailabilityBlock.is_active.is_(True),
        )
        .order_by(RentalAvailabilityBlock.start_datetime.asc())
        .all()
    )

    return blocks


@router.delete(
    "/{vehicle_id}/availability-blocks/{block_id}",
    summary="Remove rental vehicle availability block",
)
def delete_rental_availability_block(
    vehicle_id: int,
    block_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
):
    """Remove or deactivate an existing availability block."""
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

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )

    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found or not owned by you",
        )

    lock_booking_resource(db, f"rental_vehicle_booking_{vehicle_id}")
    block = (
        db.query(RentalAvailabilityBlock)
        .filter(
            RentalAvailabilityBlock.id == block_id,
            RentalAvailabilityBlock.vehicle_id == vehicle_id,
        )
        .first()
    )

    if not block:
        raise HTTPException(
            status_code=404,
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
        "is_active": False,
    }


# ==========================================
# VEHICLE DOCUMENTS MANAGEMENT
# ==========================================

@router.post(
    "/{vehicle_id}/documents",
    response_model=RentalVehicleDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload/register a document for a rental vehicle",
)
def add_rental_vehicle_document(
    vehicle_id: int,
    payload: RentalVehicleDocumentCreate,
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

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found or not owned by you",
        )

    doc = RentalVehicleDocument(
        vehicle_id=vehicle_id,
        document_type=payload.document_type.strip(),
        file_reference=payload.document_url.strip(),
        expiry_date=payload.expiry_date,
        insurance_effective_date=payload.insurance_effective_date,
        insurance_exclusions=payload.insurance_exclusions,
        coverage_amount=payload.coverage_amount,
        deductible_amount=payload.deductible_amount,
        insurance_provider=payload.insurance_provider,
        insurance_policy_number=payload.insurance_policy_number,
        is_verified=False,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.get(
    "/{vehicle_id}/documents",
    response_model=list[RentalVehicleDocumentResponse],
    summary="List documents for a rental vehicle",
)
def list_rental_vehicle_documents(
    vehicle_id: int,
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

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found or not owned by you",
        )

    return (
        db.query(RentalVehicleDocument)
        .filter(RentalVehicleDocument.vehicle_id == vehicle_id)
        .order_by(RentalVehicleDocument.created_at.desc())
        .all()
    )


@router.delete(
    "/{vehicle_id}/documents/{document_id}",
    summary="Delete a document for a rental vehicle",
)
def delete_rental_vehicle_document(
    vehicle_id: int,
    document_id: int,
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

    vehicle = (
        db.query(RentalVehicle)
        .filter(
            RentalVehicle.id == vehicle_id,
            RentalVehicle.provider_id == provider.id,
        )
        .first()
    )
    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found or not owned by you",
        )

    doc = (
        db.query(RentalVehicleDocument)
        .filter(
            RentalVehicleDocument.id == document_id,
            RentalVehicleDocument.vehicle_id == vehicle_id,
        )
        .first()
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    db.delete(doc)
    db.commit()
    return {"message": "Document deleted successfully", "id": document_id}
