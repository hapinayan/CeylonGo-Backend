from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import require_approved_eco_provider
from app.models.eco_reservation import EcoReservation
from app.models.provider_profile import ProviderProfile
from app.routers.provider_reservations import (
    DB,
    BookingId,
    CancelReservation,
    Page,
    PageSize,
    Traveler,
)
from app.schemas.eco_reservation import (
    EcoReservationCreate,
    EcoReservationResponse,
    EcoSlotCreate,
    EcoSlotResponse,
)
from app.services import eco_reservations as service

router = APIRouter(prefix="/api/v1", tags=["Eco Reservations"])
Provider = Annotated[ProviderProfile, Depends(require_approved_eco_provider)]


@router.get(
    "/eco-experiences/{experience_id}/availability",
    response_model=list[EcoSlotResponse],
)
def public_availability(
    experience_id: BookingId,
    db: DB,
    day: date | None = None,
    page: Page = 1,
    page_size: PageSize = 20,
):
    return service.slot_list(db, experience_id, page, page_size, day=day)


@router.get(
    "/provider/eco-experiences/{experience_id}/slots",
    response_model=list[EcoSlotResponse],
)
def provider_slots(
    experience_id: BookingId,
    db: DB,
    provider: Provider,
    page: Page = 1,
    page_size: PageSize = 20,
):
    return service.slot_list(db, experience_id, page, page_size, provider=provider)


@router.post(
    "/provider/eco-experiences/{experience_id}/slots",
    response_model=EcoSlotResponse,
    status_code=201,
)
def create_slot(
    experience_id: BookingId, payload: EcoSlotCreate, db: DB, provider: Provider
):
    return service.create_slot(db, provider, experience_id, payload)


@router.patch(
    "/provider/eco-experiences/{experience_id}/slots/{slot_id}/close",
    response_model=EcoSlotResponse,
)
def close_slot(
    experience_id: BookingId, slot_id: BookingId, db: DB, provider: Provider
):
    return service.close_slot(db, provider, experience_id, slot_id)


@router.post(
    "/eco-reservations", response_model=EcoReservationResponse, status_code=201
)
def reserve(payload: EcoReservationCreate, traveler: Traveler, db: DB):
    return service.reserve(db, traveler, payload)


@router.get("/eco-reservations/me", response_model=list[EcoReservationResponse])
def traveler_reservations(
    traveler: Traveler, db: DB, page: Page = 1, page_size: PageSize = 20
):
    rows = db.execute(
        service.reservation_query(traveler_id=traveler.id)
        .order_by(EcoReservation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [service.reservation_result(*row) for row in rows]


@router.get("/eco-reservations/{booking_id}", response_model=EcoReservationResponse)
def reservation_detail(booking_id: BookingId, traveler: Traveler, db: DB):
    result = db.execute(
        service.reservation_query(traveler_id=traveler.id).where(
            EcoReservation.id == booking_id
        )
    ).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    return service.reservation_result(*result)


@router.post(
    "/eco-reservations/{booking_id}/cancel", response_model=EcoReservationResponse
)
def cancel(
    booking_id: BookingId, payload: CancelReservation, traveler: Traveler, db: DB
):
    return service.change_reservation(
        db, traveler, booking_id, "cancel", payload.reason
    )
