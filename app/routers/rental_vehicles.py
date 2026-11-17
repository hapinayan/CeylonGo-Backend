
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user import User
from app.models.rental_vehicle import RentalVehicle
from app.models.provider_profile import ProviderProfile
from app.routers.auth import get_current_user
from app.schemas.rental_vehicle import (
    RentalVehicleCreate,
    RentalVehicleUpdate,
    RentalVehicleResponse,
)


router = APIRouter(
    prefix="/api/v1/rental-vehicles",
    tags=["Rental Vehicles"],
)


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
            RentalVehicle.status == "available"
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
):
    vehicle = db.get(
        RentalVehicle,
        vehicle_id,
    )

    if not vehicle:
        raise HTTPException(
            status_code=404,
            detail="Rental vehicle not found",
        )

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

    # Create rental vehicle
    new_vehicle = RentalVehicle(
        provider_id=provider.id,
        vehicle_type=vehicle_data.vehicle_type,
        vehicle_name=vehicle_data.vehicle_name,
        description=vehicle_data.description,
        price_per_day=vehicle_data.price_per_day,
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

    # Update vehicle fields
    for field, value in update_data.items():
        if value is None and field != "description":
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
