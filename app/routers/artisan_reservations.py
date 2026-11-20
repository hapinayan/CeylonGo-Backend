"""
Artisan Product Reservation API Router (Phase P0 - Step 3).
Provides secure, transactional endpoints for reserving handcrafted products for
in-store pickup and in-person cash payment, with concurrency locking, stock
allocation, and lifecycle status management.

DATABASE CONSTRAINT ARCHITECTURAL NOTE:
The existing SQL Server check constraint CK_artisan_reservations_status supports:
['pending', 'confirmed', 'ready_for_pickup', 'completed', 'cancelled', 'expired'].
Provider rejection ('rejected') is BLOCKED pending Database Coordinator approval
of Migration 030_add_rejected_to_artisan_reservation_status.sql.
Per CeylonGo strict safety rules, provider rejections MUST NOT be silently mapped
to 'cancelled'. Only supported states and transitions are implemented here.
"""

from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider, require_traveler
from app.database.connection import get_db
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.artisan_reservation import (
    ArtisanReservationCancelRequest,
    ArtisanReservationCreate,
    ArtisanReservationResponse,
)
from app.services.artisan_shared_reference import sync_artisan_booking_reference

router = APIRouter(
    prefix="/api/v1/artisan-reservations",
    tags=["Artisan Reservations"],
)


def acquire_product_lock(db: Session, showcase_item_id: int) -> None:
    """
    Acquire an exclusive lock for the showcase product during reservation or stock mutation.
    Uses SQL Server sp_getapplock on MSSQL; handles dialects gracefully in non-MSSQL test environments.
    """
    bind = db.get_bind()
    if bind.dialect.name in ("mssql", "pyodbc"):
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
            {"resource": f"artisan_product_stock_{showcase_item_id}"},
        ).scalar()
        if lock_result is None or lock_result < 0:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reservation service is busy. Please try again.",
            )


# ==============================================================================
# 1. Create Reservation (Traveler Only)
# ==============================================================================

@router.post(
    "",
    response_model=ArtisanReservationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve a handcrafted product for in-store pickup",
)
def create_artisan_reservation(
    payload: ArtisanReservationCreate,
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Traveler endpoint: Reserve an eligible handcrafted product for in-store pickup.
    Allocates stock immediately under an exclusive lock to prevent overselling.
    Payment is completed in person (cash/card at the artisan shop).
    """
    # 1. Acquire transaction lock on showcase product
    acquire_product_lock(db, payload.showcase_item_id)

    # 2. Fetch showcase item
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == payload.showcase_item_id).first()
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Showcase item not found",
        )

    # 3. Item classification check: only products are reservable
    if item.item_type != "product":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only handcrafted products can be reserved for shop pickup. Services/workshops do not support item reservation.",
        )

    # 4. Item availability and active status
    if not item.is_active or not item.is_available or item.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This product is currently unavailable or not approved for reservation",
        )

    # 5. Artisan Listing active and approved checks
    listing = item.artisan_listing
    if not listing or not listing.is_active or listing.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artisan business listing is not active or approved",
        )

    # 6. Provider profile active and approved checks
    provider_profile = listing.provider_profile
    if not provider_profile or provider_profile.approval_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artisan provider profile is not approved",
        )

    # 7. Self-reservation check: artisan cannot reserve from own listing
    if provider_profile.user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artisans cannot reserve products from their own listing",
        )

    # 8. Stock availability validation
    if item.stock_quantity < payload.quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient stock available. Requested: {payload.quantity}, Available: {item.stock_quantity}",
        )

    # 9. Server-side price calculation with Decimal precision
    unit_price = Decimal(str(item.price)) if item.price is not None else Decimal("0.00")
    total_price = (unit_price * Decimal(payload.quantity)).quantize(Decimal("0.01"))

    # 10. Deduct stock immediately (Reservation allocation strategy)
    item.stock_quantity -= payload.quantity
    item.updated_at = func.sysutcdatetime()

    # 11. Calculate default expiration (end of pickup date)
    expires_at = datetime.combine(payload.pickup_date, time(23, 59, 59))

    # 12. Create reservation record with 'pending' status
    reservation = ArtisanReservation(
        showcase_item_id=item.id,
        artisan_listing_id=listing.id,
        traveler_id=current_user.id,
        quantity=payload.quantity,
        total_price=total_price,
        currency=item.currency or "LKR",
        reservation_status="pending",
        pickup_date=payload.pickup_date,
        special_notes=payload.special_notes,
        expires_at=expires_at,
    )
    db.add(reservation)
    db.flush()
    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=current_user.id,
        change_reason="Artisan product reserved for in-store pickup",
    )
    db.commit()
    db.refresh(reservation)

    return ArtisanReservationResponse.model_validate(reservation)


# ==============================================================================
# 2. Traveler Reservations Lookup
# ==============================================================================

@router.get(
    "/me",
    response_model=list[ArtisanReservationResponse],
    summary="Get authenticated traveler's product reservations",
)
def get_my_reservations(
    current_user: Annotated[User, Depends(require_traveler)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default=None, alias="status", description="Filter by reservation status"),
):
    """
    Traveler endpoint: View all reservations created by the authenticated traveler,
    ordered by creation date descending.
    """
    query = (
        db.query(ArtisanReservation)
        .filter(ArtisanReservation.traveler_id == current_user.id)
    )
    if status_filter and status_filter.strip():
        query = query.filter(ArtisanReservation.reservation_status == status_filter.strip().lower())

    reservations = query.order_by(ArtisanReservation.created_at.desc()).all()
    return [ArtisanReservationResponse.model_validate(r) for r in reservations]


# ==============================================================================
# 3. Provider Reservations Management
# ==============================================================================

@router.get(
    "/provider",
    response_model=list[ArtisanReservationResponse],
    summary="Get reservations for authenticated artisan provider's listings",
)
def get_provider_reservations(
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: str | None = Query(default=None, alias="status", description="Filter by reservation status"),
    listing_id: int | None = Query(default=None, description="Filter by specific artisan listing ID"),
):
    """
    Provider endpoint: View reservations made across all artisan listings owned
    by the authenticated approved artisan provider.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can access provider reservations",
        )

    query = (
        db.query(ArtisanReservation)
        .join(ArtisanListing, ArtisanReservation.artisan_listing_id == ArtisanListing.id)
        .filter(ArtisanListing.provider_profile_id == provider_profile.id)
    )

    if status_filter and status_filter.strip():
        query = query.filter(ArtisanReservation.reservation_status == status_filter.strip().lower())
    if listing_id is not None:
        query = query.filter(ArtisanReservation.artisan_listing_id == listing_id)

    reservations = query.order_by(ArtisanReservation.created_at.desc()).all()
    return [ArtisanReservationResponse.model_validate(r) for r in reservations]


# ==============================================================================
# 4. View Single Reservation
# ==============================================================================

@router.get(
    "/{id}",
    response_model=ArtisanReservationResponse,
    summary="Get details of a specific artisan reservation",
)
def get_artisan_reservation(
    id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Lookup a single reservation. Allowed for the traveler who created it,
    the artisan who owns the listing, or an administrator.
    """
    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == id).first()
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan reservation not found",
        )

    is_traveler = reservation.traveler_id == current_user.id
    is_owner = (
        reservation.artisan_listing.provider_profile.user_id == current_user.id
        if reservation.artisan_listing and reservation.artisan_listing.provider_profile
        else False
    )
    is_admin = current_user.role == "admin"

    if not (is_traveler or is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you are not authorized to view this reservation",
        )

    return ArtisanReservationResponse.model_validate(reservation)


# ==============================================================================
# 5. Provider Confirmation
# ==============================================================================

@router.patch(
    "/{id}/confirm",
    response_model=ArtisanReservationResponse,
    summary="Provider confirms product reservation",
)
def confirm_reservation(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider endpoint: Confirm a pending product reservation.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can manage reservations",
        )

    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == id).first()
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan reservation not found",
        )

    if reservation.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own the artisan listing for this reservation",
        )

    if reservation.reservation_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only pending reservations can be confirmed. Current status: '{reservation.reservation_status}'",
        )

    reservation.reservation_status = "confirmed"
    reservation.updated_at = func.sysutcdatetime()
    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=provider_profile.user_id,
        change_reason="Artisan provider confirmed reservation",
    )
    db.commit()
    db.refresh(reservation)

    return ArtisanReservationResponse.model_validate(reservation)


# ==============================================================================
# 6. Ready for Pickup
# ==============================================================================

@router.patch(
    "/{id}/ready",
    response_model=ArtisanReservationResponse,
    summary="Provider marks product ready for shop pickup",
)
def ready_for_pickup(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider endpoint: Mark reservation as ready for customer pickup at the workshop/store.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can manage reservations",
        )

    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == id).first()
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan reservation not found",
        )

    if reservation.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own the artisan listing for this reservation",
        )

    if reservation.reservation_status not in ("confirmed", "pending"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reservation must be confirmed before marking ready for pickup. Current status: '{reservation.reservation_status}'",
        )

    reservation.reservation_status = "ready_for_pickup"
    reservation.updated_at = func.sysutcdatetime()
    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=provider_profile.user_id,
        change_reason="Artisan provider marked reservation ready for pickup",
    )
    db.commit()
    db.refresh(reservation)

    return ArtisanReservationResponse.model_validate(reservation)


# ==============================================================================
# 7. Complete Pickup
# ==============================================================================

@router.post(
    "/{id}/complete-pickup",
    response_model=ArtisanReservationResponse,
    summary="Provider completes in-store pickup and records in-person cash payment",
)
def complete_pickup(
    id: int,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider endpoint: Finalize the reservation when the traveler arrives, picks up
    the handcrafted product, and completes in-store cash payment.
    """
    if provider_profile.provider_type != "artisan":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: only artisan service providers can manage reservations",
        )

    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == id).first()
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan reservation not found",
        )

    if reservation.artisan_listing.provider_profile_id != provider_profile.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you do not own the artisan listing for this reservation",
        )

    if reservation.reservation_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reservation is already completed",
        )

    if reservation.reservation_status in ("cancelled", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot complete a {reservation.reservation_status} reservation",
        )

    if reservation.reservation_status not in ("ready_for_pickup", "confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reservation must be confirmed or ready for pickup before completion. Current status: '{reservation.reservation_status}'",
        )

    reservation.reservation_status = "completed"
    reservation.updated_at = func.sysutcdatetime()
    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=provider_profile.user_id,
        change_reason="Customer picked up product; payment completed in-store",
    )
    db.commit()
    db.refresh(reservation)

    return ArtisanReservationResponse.model_validate(reservation)


# ==============================================================================
# 8. Cancel Reservation (Traveler or Artisan Provider)
# ==============================================================================

@router.post(
    "/{id}/cancel",
    response_model=ArtisanReservationResponse,
    summary="Cancel an active reservation and release allocated stock",
)
def cancel_reservation(
    id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    payload: ArtisanReservationCancelRequest | None = None,
):
    """
    Cancellation endpoint: Allows the traveler or the artisan provider to cancel
    an active reservation ('pending', 'confirmed', or 'ready_for_pickup').
    Restores the reserved quantity back to product stock exactly once.
    """
    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == id).first()
    if not reservation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artisan reservation not found",
        )

    is_traveler = reservation.traveler_id == current_user.id
    is_owner = (
        reservation.artisan_listing.provider_profile.user_id == current_user.id
        if reservation.artisan_listing and reservation.artisan_listing.provider_profile
        else False
    )
    is_admin = current_user.role == "admin"

    if not (is_traveler or is_owner or is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: you are not authorized to cancel this reservation",
        )

    if reservation.reservation_status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Completed reservations cannot be cancelled",
        )

    if reservation.reservation_status in ("cancelled", "expired"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reservation is already {reservation.reservation_status}",
        )

    if reservation.reservation_status not in ("pending", "confirmed", "ready_for_pickup"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel reservation in '{reservation.reservation_status}' status",
        )

    # 1. Acquire product lock to restore stock safely
    acquire_product_lock(db, reservation.showcase_item_id)

    # 2. Release and restore reserved stock back to inventory exactly once
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == reservation.showcase_item_id).first()
    if item:
        item.stock_quantity += reservation.quantity
        item.updated_at = func.sysutcdatetime()

    # 3. Update reservation status and cancellation audit record
    reservation.reservation_status = "cancelled"
    reservation.cancelled_by = current_user.id
    if payload and payload.cancellation_reason:
        reservation.cancellation_reason = payload.cancellation_reason
    reservation.updated_at = func.sysutcdatetime()

    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=current_user.id,
        change_reason=payload.cancellation_reason if payload and payload.cancellation_reason else "Reservation cancelled",
    )
    db.commit()
    db.refresh(reservation)

    return ArtisanReservationResponse.model_validate(reservation)

