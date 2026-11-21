from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.dependencies import require_admin, require_approved_eco_provider
from app.models.eco_reservation import EcoReservation
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers.provider_reservations import (
    DB,
    BookingId,
    CancelReservation,
    Page,
    PageSize,
    Traveler,
)
from app.schemas.eco_reservation import (
    EcoRefundDecision,
    EcoRefundReconcile,
    EcoRefundStatus,
    EcoReservationCreate,
    EcoReservationResponse,
    EcoSlotCreate,
    EcoSlotReschedule,
    EcoSlotResponse,
)
from app.schemas.payment import (
    PaymentInitiateResponse,
    PaymentStatusResponse,
    RefundResponse,
)
from app.schemas.eco_experience import EcoNativeAdminReport
from app.services import eco_financial_reporting as reporting
from app.services import eco_native_payments as payments
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


@router.patch(
    "/provider/eco-experiences/{experience_id}/slots/{slot_id}/schedule",
    response_model=EcoSlotResponse,
)
def reschedule_slot(
    experience_id: BookingId, slot_id: BookingId, payload: EcoSlotReschedule,
    db: DB, provider: Provider,
):
    return service.reschedule_slot(db, provider, experience_id, slot_id, payload)


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


@router.post(
    "/eco-reservations/{booking_id}/payments/initiate",
    response_model=PaymentInitiateResponse,
)
def initiate_eco_payment(booking_id: BookingId, traveler: Traveler, db: DB):
    return payments.checkout(db, traveler, booking_id)


@router.get(
    "/eco-reservations/{booking_id}/payments/status",
    response_model=PaymentStatusResponse,
)
def eco_payment_status(booking_id: BookingId, traveler: Traveler, db: DB):
    return payments.payment_status(db, traveler, booking_id)


@router.post(
    "/eco-reservations/{booking_id}/payments/cancel",
    response_model=EcoReservationResponse,
)
def cancel_paid_eco_reservation(booking_id: BookingId, traveler: Traveler, db: DB):
    return payments.cancel_paid(db, traveler, booking_id)


@router.get(
    "/eco-reservations/{booking_id}/payments/refunds",
    response_model=list[EcoRefundStatus],
)
def eco_refund_statuses(booking_id: BookingId, traveler: Traveler, db: DB):
    return payments.refund_statuses(db, traveler, booking_id)


@router.post(
    "/admin/eco-reservations/refunds/{refund_id}/decision", response_model=RefundResponse
)
def decide_eco_refund(
    refund_id: BookingId,
    payload: EcoRefundDecision,
    admin: Annotated[User, Depends(require_admin)],
    db: DB,
):
    return payments.decide_refund(db, admin, refund_id, payload.decision)


@router.post("/admin/eco-reservations/refunds/{refund_id}/reconcile")
def reconcile_eco_refund(
    refund_id: BookingId,
    payload: EcoRefundReconcile,
    admin: Annotated[User, Depends(require_admin)],
    db: DB,
):
    return payments.reconcile_refund(db, admin, refund_id, payload)


@router.post(
    "/admin/eco-experiences/{experience_id}/restrictions/{restriction_id}/cancel-slots"
)
def cancel_restricted_eco_slots(
    experience_id: BookingId,
    restriction_id: BookingId,
    admin: Annotated[User, Depends(require_admin)],
    db: DB,
) -> dict[str, int]:
    return payments.cancel_restricted_slots(db, admin, experience_id, restriction_id)


@router.get("/admin/eco-reservations/report", response_model=EcoNativeAdminReport)
def report_native_eco_bookings(
    admin: Annotated[User, Depends(require_admin)],
    db: DB,
    page: Page = 1,
    page_size: PageSize = 20,
    provider_id: Annotated[int | None, Query(gt=0, le=2147483647)] = None,
    booking_status: str | None = None,
    payment_status: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    if start_date and end_date and end_date < start_date:
        raise HTTPException(422, "End date must be on or after the start date")
    query = reporting.scope(
        provider_id=provider_id, status=booking_status, payment_status=payment_status,
        start=datetime.combine(start_date, time.min) if start_date else None,
        end=datetime.combine(end_date, time.max) if end_date else None,
    )
    return {
        **reporting.page(db, query, page, page_size),
        "totals_by_currency": reporting.totals(db, query),
        "provider_settlements_by_currency": reporting.settlement_totals(db, provider_id),
    }
