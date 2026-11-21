"""
FastAPI Routers for Local Tour Guide Module 05 — Phase 3 Bookings & Cash Payment.

Traveler Endpoints:
- POST /api/v1/tour-guides/{guide_id}/bookings          Request a tour guide booking
- GET  /api/v1/tour-guide-bookings/me                   List own upcoming/past/cancelled bookings
- GET  /api/v1/tour-guide-bookings/{booking_id}         View own booking detail & status history
- POST /api/v1/tour-guide-bookings/{booking_id}/cancel  Policy-based traveler cancellation

Provider Endpoints (/api/v1/provider/tour-guides/bookings):
- GET  ""                                               List booking requests & history
- GET  "/calendar"                                      Unified availability + booking calendar
- GET  "/{booking_id}"                                  View owned booking detail & history
- POST "/{booking_id}/accept"                           Accept pending request (overlap-checked)
- POST "/{booking_id}/reject"                           Reject pending request with reason
- POST "/{booking_id}/cash-received"                    Record direct cash receipt (CASH_RECEIVED)
- POST "/{booking_id}/start"                            Mark confirmed booking as in_progress
- POST "/{booking_id}/complete"                         Mark paid booking as completed
- POST "/{booking_id}/cancel"                           Provider cancellation with reason
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_traveler
from app.database.connection import get_db
from app.models.tour_guide import TourGuide, TourGuideBooking
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.tour_guide import OwnedGuide
from app.schemas.tour_guide import (
    BookingOptionalNotesRequest,
    BookingReasonRequest,
    CashPaymentRecordRequest,
    ProviderBookingBucket,
    ProviderBookingCalendarResponse,
    TourGuideBookingCancelResponse,
    TourGuideBookingCreate,
    TourGuideBookingDetailResponse,
    TourGuideBookingListResponse,
    TravelerBookingBucket,
)
from app.services.tour_guide_booking import (
    _booking_eager_query,
    _require_provider_owned_booking,
    accept_tour_guide_booking,
    cancel_tour_guide_booking,
    complete_tour_guide_booking,
    create_tour_guide_booking,
    get_provider_booking_calendar,
    get_provider_booking_detail,
    get_traveler_booking_detail,
    list_provider_tour_guide_bookings,
    list_traveler_tour_guide_bookings,
    record_tour_guide_cash_payment,
    reject_tour_guide_booking,
    start_tour_guide_booking,
)

DatabaseSession = Annotated[Session, Depends(get_db)]

traveler_router = APIRouter(
    tags=["Tour Guide Bookings"],
)

provider_router = APIRouter(
    prefix="/api/v1/provider/tour-guides/bookings",
    tags=["Tour Guide Provider Bookings"],
)


# =============================================================================
# 1. TRAVELER BOOKING ENDPOINTS (TR-11, TR-13, TR-14, TR-15)
# =============================================================================

@traveler_router.post(
    "/api/v1/tour-guides/{guide_id}/bookings",
    response_model=TourGuideBookingDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a tour guide booking for selected date/time/package",
)
def traveler_create_tour_guide_booking(
    guide_id: Annotated[int, Path(gt=0)],
    payload: TourGuideBookingCreate,
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
):
    return create_tour_guide_booking(
        db,
        guide_id=guide_id,
        traveler=traveler,
        payload=payload,
    )


@traveler_router.get(
    "/api/v1/tour-guide-bookings/me",
    response_model=TourGuideBookingListResponse,
    summary="List authenticated traveler's tour guide bookings",
)
def traveler_list_my_tour_guide_bookings(
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
    bucket: TravelerBookingBucket = "all",
    booking_status: str | None = Query(default=None),
    payment_method: str | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_traveler_tour_guide_bookings(
        db,
        traveler=traveler,
        bucket=bucket,
        booking_status=booking_status,
        payment_method=payment_method,
        page=page,
        page_size=page_size,
    )


@traveler_router.get(
    "/api/v1/tour-guide-bookings/{booking_id}",
    response_model=TourGuideBookingDetailResponse,
    summary="Get detail and status history for a tour guide booking",
)
def traveler_get_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: DatabaseSession,
):
    return get_traveler_booking_detail(
        db,
        booking_id=booking_id,
        caller=current_user,
    )


@traveler_router.post(
    "/api/v1/tour-guide-bookings/{booking_id}/cancel",
    response_model=TourGuideBookingCancelResponse,
    summary="Cancel your own tour guide booking under the snapshotted cancellation policy",
)
def traveler_cancel_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    payload: BookingReasonRequest,
    traveler: Annotated[User, Depends(require_traveler)],
    db: DatabaseSession,
):
    booking = _booking_eager_query(db).filter(
        TourGuideBooking.id == booking_id
    ).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )
    if booking.traveler_id != traveler.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only cancel your own tour guide bookings.",
        )
    return cancel_tour_guide_booking(
        db,
        booking=booking,
        actor=traveler,
        actor_role="traveler",
        reason=payload.reason,
    )


# =============================================================================
# 2. PROVIDER BOOKING MANAGEMENT, CASH & CALENDAR (PR-11..14)
# =============================================================================

@provider_router.get(
    "",
    response_model=TourGuideBookingListResponse,
    summary="List tour guide booking requests and history for your profile",
)
def provider_list_tour_guide_bookings(
    guide: OwnedGuide,
    db: DatabaseSession,
    bucket: ProviderBookingBucket = "all",
    booking_status: str | None = Query(default=None),
    payment_method: str | None = Query(default=None),
    payment_status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return list_provider_tour_guide_bookings(
        db,
        guide=guide,
        bucket=bucket,
        booking_status=booking_status,
        payment_method=payment_method,
        payment_status=payment_status,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )


@provider_router.get(
    "/calendar",
    response_model=ProviderBookingCalendarResponse,
    summary="View unified provider availability slots and bookings calendar",
)
def provider_get_tour_guide_booking_calendar(
    guide: OwnedGuide,
    db: DatabaseSession,
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    return get_provider_booking_calendar(
        db,
        guide=guide,
        date_from=date_from,
        date_to=date_to,
    )


@provider_router.get(
    "/{booking_id}",
    response_model=TourGuideBookingDetailResponse,
    summary="View detailed information and audit trail for one of your bookings",
)
def provider_get_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return get_provider_booking_detail(
        db,
        guide=guide,
        booking_id=booking_id,
    )


@provider_router.post(
    "/{booking_id}/accept",
    response_model=TourGuideBookingDetailResponse,
    summary="Accept a pending booking request after overlap verification",
)
def provider_accept_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    payload: BookingOptionalNotesRequest | None = None,
):
    return accept_tour_guide_booking(
        db,
        guide=guide,
        booking_id=booking_id,
        notes=payload.notes if payload else None,
    )


@provider_router.post(
    "/{booking_id}/reject",
    response_model=TourGuideBookingDetailResponse,
    summary="Reject a pending booking request with a mandatory reason",
)
def provider_reject_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    payload: BookingReasonRequest,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    return reject_tour_guide_booking(
        db,
        guide=guide,
        booking_id=booking_id,
        reason=payload.reason,
    )


@provider_router.post(
    "/{booking_id}/cash-received",
    response_model=TourGuideBookingDetailResponse,
    summary="Record direct cash payment received (CASH_RECEIVED) without implying online payment",
)
def provider_record_cash_received(
    booking_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    payload: CashPaymentRecordRequest | None = None,
):
    return record_tour_guide_cash_payment(
        db,
        guide=guide,
        booking_id=booking_id,
        notes=payload.notes if payload else None,
    )


@provider_router.post(
    "/{booking_id}/start",
    response_model=TourGuideBookingDetailResponse,
    summary="Mark a confirmed booking as in_progress",
)
def provider_start_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    payload: BookingOptionalNotesRequest | None = None,
):
    return start_tour_guide_booking(
        db,
        guide=guide,
        booking_id=booking_id,
        notes=payload.notes if payload else None,
    )


@provider_router.post(
    "/{booking_id}/complete",
    response_model=TourGuideBookingDetailResponse,
    summary="Mark a paid tour guide booking as completed after service",
)
def provider_complete_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    guide: OwnedGuide,
    db: DatabaseSession,
    payload: BookingOptionalNotesRequest | None = None,
):
    return complete_tour_guide_booking(
        db,
        guide=guide,
        booking_id=booking_id,
        notes=payload.notes if payload else None,
    )


@provider_router.post(
    "/{booking_id}/cancel",
    response_model=TourGuideBookingCancelResponse,
    summary="Cancel a confirmed booking as the tour guide with mandatory reason",
)
def provider_cancel_tour_guide_booking(
    booking_id: Annotated[int, Path(gt=0)],
    payload: BookingReasonRequest,
    guide: OwnedGuide,
    db: DatabaseSession,
):
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)
    return cancel_tour_guide_booking(
        db,
        booking=booking,
        actor=guide.provider_profile.user,
        actor_role="provider",
        reason=payload.reason,
    )
