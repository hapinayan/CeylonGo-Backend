
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user import User
from app.models.rental_vehicle import RentalVehicle
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalAvailabilityBlock, RentalVehicleDocument
from app.models.rental_booking import RentalBooking
from app.routers.auth import get_current_user
from app.schemas.rental_vehicle import (
    RentalAvailabilityBlockCreate,
    RentalAvailabilityBlockResponse,
    RentalVehicleCreate,
    RentalVehicleDocumentCreate,
    RentalVehicleDocumentResponse,
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

    # 1. Check for overlapping confirmed or pending bookings
    conflicting_booking = (
        db.query(RentalBooking)
        .filter(
            RentalBooking.vehicle_id == vehicle_id,
            RentalBooking.status.in_(["pending", "confirmed"]),
            RentalBooking.start_date <= payload.end_date,
            RentalBooking.end_date >= payload.start_date,
        )
        .first()
    )

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
            RentalAvailabilityBlock.start_date <= payload.end_date,
            RentalAvailabilityBlock.end_date >= payload.start_date,
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
        start_date=payload.start_date,
        end_date=payload.end_date,
        block_type=payload.block_type,
        reason=payload.reason,
        is_active=True,
        created_by=current_user.id,
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
    vehicle = db.get(RentalVehicle, vehicle_id)
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
        .order_by(RentalAvailabilityBlock.start_date.asc())
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
        document_url=payload.document_url.strip(),
        expiry_date=payload.expiry_date,
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
