"""
Artisan Product Reservation API Router (Phase P0 - Step 3).
Provides secure, transactional endpoints for reserving handcrafted products for
in-store pickup and in-person cash payment, with concurrency locking, stock
allocation, and lifecycle status management.

Provider rejection and its audit fields require migration 037.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_provider, require_traveler
from app.database.connection import get_db
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.artisan_reservation import (
    ArtisanReservationCancelRequest,
    ArtisanReservationCreate,
    ArtisanReservationRejectRequest,
    ArtisanReservationResponse,
)
from app.services.provider_reservations import (
    ARTISAN,
    artisan_actor,
    artisan_reservation_expiry,
    change_artisan_reservation,
    create_artisan_hold,
    reject_artisan_reservation,
)

router = APIRouter(
    prefix="/api/v1/artisan-reservations",
    tags=["Artisan Reservations"],
)


def detailed_responses(db, reservations):
    ids = [row.id for row in reservations]
    payments = {}
    for offset in range(0, len(ids), 1000):  # Below SQL Server's parameter limit.
        references = db.scalars(select(BookingReference).where(
            BookingReference.module_type == ARTISAN,
            BookingReference.target_booking_id.in_(ids[offset:offset + 1000]),
        ))
        payments.update({
            (ref.target_booking_id, ref.user_id, ref.provider_profile_id): ref.payment_status
            for ref in references
        })
    return [
        ArtisanReservationResponse.model_validate(row).model_copy(update={
            "payment_status": payments.get((
                row.id, row.traveler_id, row.artisan_listing.provider_profile_id,
            )),
            "expires_at": artisan_reservation_expiry(row),
        })
        for row in reservations
    ]


def create_artisan_reservation(payload: ArtisanReservationCreate, current_user: User, db: Session):
    row, _ = create_artisan_hold(
        db, current_user, payload.showcase_item_id, payload.quantity, payload.pickup_date,
        detailed=True, notes=payload.special_notes,
    )
    return detailed_responses(db, [row])[0]


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
    return detailed_responses(db, reservations)


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
    return detailed_responses(db, reservations)


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

    artisan_actor(db, reservation, current_user)
    return detailed_responses(db, [reservation])[0]


def provider_transition(db, id, profile, action):
    row, _ = change_artisan_reservation(
        db, id, profile.user, action, provider=profile, detailed=True
    )
    return detailed_responses(db, [row])[0]


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
    return provider_transition(db, id, provider_profile, "accept")


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
    return provider_transition(db, id, provider_profile, "ready")


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
    return provider_transition(db, id, provider_profile, "complete")


@router.post("/{id}/reject", response_model=ArtisanReservationResponse)
def reject_reservation(
    id: int,
    payload: ArtisanReservationRejectRequest,
    provider_profile: Annotated[ProviderProfile, Depends(require_approved_provider)],
    db: Annotated[Session, Depends(get_db)],
):
    reservation, _ = reject_artisan_reservation(
        db, id, provider_profile, payload.rejection_reason
    )
    return detailed_responses(db, [reservation])[0]


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
    row, _ = change_artisan_reservation(
        db, id, current_user, "cancel",
        reason=payload.cancellation_reason if payload else None,
        detailed=True,
    )
    return detailed_responses(db, [row])[0]
