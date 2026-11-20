"""
Module 04 — Eco-Tourism Proposed Online Payment Router Contract.
================================================================
Defines the REST API endpoint contract for traveler PayHere checkout preparation.

STRICT ISOLATION & SAFETY RULES:
- UNREGISTERED in app/main.py.
- ZERO database queries or writes executed directly.
- Fails closed with HTTP 503 if trusted persistence adapter is not configured.
- Reuses prepare_eco_checkout_application from application layer.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.payhere import PayHereService, payhere_service
from app.models.user import User
from app.routers.auth import get_current_user
from app.services.eco_payment_application import prepare_eco_checkout_application
from app.services.eco_payment_persistence_contract import EcoPaymentPersistenceAdapter
from app.services.eco_payment_service import (
    EcoPayHereCheckoutPayload,
    EcoPaymentServiceError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/eco-bookings",
    tags=["Eco-Tourism Payments (Proposed / Unmounted)"],
)

# Global or dependency-injected persistence adapter placeholder (None until Gate 4)
_configured_persistence_adapter: Optional[EcoPaymentPersistenceAdapter] = None


def get_persistence_adapter() -> Optional[EcoPaymentPersistenceAdapter]:
    """Dependency provider for the persistence adapter. None until approved."""
    return _configured_persistence_adapter


def set_persistence_adapter_for_testing(
    adapter: Optional[EcoPaymentPersistenceAdapter],
) -> None:
    """Helper for testing isolated adapter dependency injection."""
    global _configured_persistence_adapter
    _configured_persistence_adapter = adapter


@router.post(
    "/{booking_id}/payhere-checkout",
    response_model=EcoPayHereCheckoutPayload,
    status_code=status.HTTP_200_OK,
    summary="Prepare PayHere online checkout redirect payload (Proposed / Staged)",
)
def create_eco_payhere_checkout_endpoint(
    booking_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    adapter: Annotated[
        Optional[EcoPaymentPersistenceAdapter], Depends(get_persistence_adapter)
    ],
    payhere_svc: Annotated[PayHereService, Depends(lambda: payhere_service)],
):
    """
    Traveler-only endpoint: Prepares an authoritative PayHere checkout payload.

    Security & Business Rules:
    1. Authenticated caller must own the booking or possess the 'admin' role.
    2. Fail-closed: Requires an approved persistence adapter to fetch trusted booking state.
       Returns HTTP 503 if the adapter is not configured (preventing dummy checkouts).
    3. Server-authoritative: Total price derived strictly from booking.total_price.
    4. Merchant secret is never exposed.
    """
    # 1. Fail closed if persistence adapter is not configured (Staging guard)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Eco-Tourism online payment checkout is staged and awaiting Gate 4 activation. "
                "No live database payment adapter is currently mounted."
            ),
        )

    # 2. Fetch trusted booking record via adapter
    booking = adapter.get_booking_by_id(booking_id)
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Eco booking with ID {booking_id} was not found.",
        )

    # 3. Ownership / RBAC check
    user_role = getattr(current_user, "role", "traveler")
    user_id = getattr(current_user, "id", None)
    if booking.user_id != user_id and user_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to initiate payment for this booking.",
        )

    # 4. Fetch trusted booking reference record
    booking_ref = adapter.get_booking_reference(
        getattr(booking, "booking_reference", "")
    )

    # 5. Delegate to application service
    try:
        payload = prepare_eco_checkout_application(
            booking=booking,
            current_user=current_user,
            booking_ref=booking_ref,
            payhere_svc=payhere_svc,
        )
    except EcoPaymentServiceError as err:
        if err.code in ("ECO_BOOKING_NOT_FOUND",):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code in ("ECO_UNAUTHORIZED", "ECO_PAYMENT_UNAUTHORIZED"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=err.message)
        elif err.code in (
            "ECO_BOOKING_CANCELLED",
            "ECO_BOOKING_REJECTED",
            "ECO_PAYMENT_ALREADY_PAID",
            "ECO_INVALID_BOOKING_STATUS",
            "ECO_INVALID_PAYMENT_STATUS",
            "ECO_INVALID_PAYMENT_METHOD",
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err.message)

    return payload
