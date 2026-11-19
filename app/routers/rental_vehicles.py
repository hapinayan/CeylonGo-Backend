
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, true
from sqlalchemy.orm import Session

from app.core.publication import public_rental
from app.core.dependencies import require_approved_provider
from app.database.connection import get_db
from app.models.user import User
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.provider_profile import ProviderProfile
from app.routers.auth import get_current_user, get_optional_current_user
from app.schemas.rental_vehicle import (
    RentalPickupInput,
    RentalPickupResponse,
    RentalVehicleCreate,
    RentalVehicleUpdate,
    RentalVehicleResponse,
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


# ==========================================
# GET ALL AVAILABLE RENTAL VEHICLES
# ==========================================

@router.get(
    "",
    response_model=list[RentalVehicleResponse],
)
def get_rental_vehicles(
    db: Annotated[Session, Depends(get_db)],
):
    vehicles = (
        db.query(RentalVehicle)
        .filter(
            public_rental()
        )
        .order_by(
            RentalVehicle.created_at.desc()
        )
        .all()
    )

    return vehicles


# ==========================================
# GET RENTAL VEHICLE BY ID
# ==========================================

@router.get(
    "/{vehicle_id}",
    response_model=RentalVehicleResponse,
)
def get_rental_vehicle(
    vehicle_id: int,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_current_user)],
):
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
        raise HTTPException(404, detail="Rental vehicle not found")
    return vehicle


# ==========================================
# CREATE RENTAL VEHICLE
# APPROVED RENTAL PROVIDERS ONLY
# ==========================================

@router.post(
    "",
    response_model=RentalVehicleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rental_vehicle(
    vehicle_data: RentalVehicleCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(get_current_user)
    ],
):
    # Find rental provider profile
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
        )
        .first()
    )

    # Check provider exists
    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Only rental providers can add vehicles",
        )

    # Check provider approval
    if provider.approval_status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Rental provider is not approved",
        )

    if vehicle_data.location_id is not None:
        require_owned_pickup_location(db, provider.id, vehicle_data.location_id)

    # Create rental vehicle
    new_vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type=vehicle_data.vehicle_type,
        vehicle_name=vehicle_data.vehicle_name,
        description=vehicle_data.description,
        price_per_day=vehicle_data.price_per_day,
        location_id=vehicle_data.location_id,
        status="available",
    )

    # Save to database
    try:
        db.add(new_vehicle)
        db.commit()
        db.refresh(new_vehicle)

    except Exception:
        db.rollback()
        raise

    return new_vehicle


# ==========================================
# UPDATE RENTAL VEHICLE
# APPROVED RENTAL PROVIDER / OWNER ONLY
# ==========================================

@router.patch(
    "/{vehicle_id}",
    response_model=RentalVehicleResponse,
)
def update_rental_vehicle(
    vehicle_id: int,
    vehicle_data: RentalVehicleUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[
        User,
        Depends(get_current_user)
    ],
):
    # Find logged-in rental provider
    provider = (
        db.query(ProviderProfile)
        .filter(
            ProviderProfile.user_id == current_user.id,
            ProviderProfile.provider_type == "rental",
        )
        .first()
    )

    # Check provider exists
    if not provider:
        raise HTTPException(
            status_code=403,
            detail="Only rental providers can update vehicles",
        )

    # Check provider approval
    if provider.approval_status != "approved":
        raise HTTPException(
            status_code=403,
            detail="Rental provider is not approved",
        )

    # Find vehicle owned by this provider
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

    # Get only supplied update fields
    update_data = vehicle_data.model_dump(
        exclude_unset=True
    )

    if not update_data:
        raise HTTPException(
            status_code=422,
            detail="No fields provided for update",
        )

    if update_data.get("location_id") is not None:
        require_owned_pickup_location(db, provider.id, update_data["location_id"])

    # Update vehicle fields
    for field, value in update_data.items():
        if value is None and field not in ("description", "location_id"):
            raise HTTPException(
                status_code=422,
                detail=f"{field} cannot be null",
            )

        setattr(vehicle, field, value)

    # Save changes to database
    try:
        db.commit()
        db.refresh(vehicle)

    except Exception:
        db.rollback()
        raise

    return vehicle
