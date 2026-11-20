"""
FastAPI Router for Local Tour Guide Module 05 — Phase 4 Online Payment
Preparation, Payment Status, Policy-Verified Refund Requests & Provider Earnings.

All routes are Tour Guide-namespaced so app/routers/payments.py remains untouched
until explicit approval for shared PayHere webhook integration.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user import User
from app.routers.auth import get_current_user
from app.routers.tour_guide import OwnedGuide
from app.schemas.tour_guide import (
    TourGuidePaymentPrepareResponse,
    TourGuidePaymentStatusResponse,
    TourGuideProviderEarningsResponse,
    TourGuideRefundRequest,
    TourGuideRefundResponse,
)
from app.services.tour_guide_payment import (
    calculate_tour_guide_provider_earnings,
    get_tour_guide_payment_status,
    prepare_tour_guide_online_payment,
    request_tour_guide_booking_refund,
)

DatabaseSession = Annotated[Session, Depends(get_db)]

router = APIRouter(
    tags=["Tour Guide Payments"],
)


@router.post(
    "/api/v1/tour-guide-bookings/{booking_id}/payment/prepare",
    response_model=TourGuidePaymentPrepareResponse,
    summary="Prepare and validate signed PayHere checkout parameters for an accepted online tour guide booking",
)
def prepare_tour_guide_booking_payment_endpoint(
    booking_id: Annotated[int, Path(gt=0)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: DatabaseSession,
):
    return prepare_tour_guide_online_payment(
        db,
        booking_id=booking_id,
        current_user=current_user,
    )


@router.get(
    "/api/v1/tour-guide-bookings/{booking_id}/payment/status",
    response_model=TourGuidePaymentStatusResponse,
    summary="Get payment, commission (provider/admin), and refund status for a tour guide booking",
)
def get_tour_guide_booking_payment_status_endpoint(
    booking_id: Annotated[int, Path(gt=0)],
    current_user: Annotated[User, Depends(get_current_user)],
    db: DatabaseSession,
):
    return get_tour_guide_payment_status(
        db,
        booking_id=booking_id,
        current_user=current_user,
    )


@router.post(
    "/api/v1/tour-guide-bookings/{booking_id}/request-refund",
    response_model=TourGuideRefundResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a policy-verified refund for a cancelled, online-paid tour guide booking",
)
def request_tour_guide_booking_refund_endpoint(
    booking_id: Annotated[int, Path(gt=0)],
    payload: TourGuideRefundRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: DatabaseSession,
):
    return request_tour_guide_booking_refund(
        db,
        booking_id=booking_id,
        reason=payload.reason,
        current_user=current_user,
    )


@router.get(
    "/api/v1/provider/tour-guides/earnings",
    response_model=TourGuideProviderEarningsResponse,
    summary="Provider summary of online tour guide earnings, 10% commission, settlements, and direct cash totals",
)
def get_tour_guide_provider_earnings_endpoint(
    guide: OwnedGuide,
    db: DatabaseSession,
    period_start: date | None = Query(default=None),
    period_end: date | None = Query(default=None),
):
    if period_start and period_end and period_end < period_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="period_end must be on or after period_start",
        )
    return calculate_tour_guide_provider_earnings(
        db,
        provider_profile_id=guide.provider_profile_id,
        period_start=period_start,
        period_end=period_end,
    )
