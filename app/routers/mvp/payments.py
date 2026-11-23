"""
CeylonGo Academic MVP — Unified Payments Router
Endpoints for Academic Simulated Payments and Pay-at-Location Tracking.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.schemas.mvp import (
    AtLocationPaymentRequest,
    PaymentResponse,
    SimulatedPaymentRequest,
)
from app.services.mvp_services import MVPPaymentService

router = APIRouter(prefix="/payments", tags=["MVP Payments"])


@router.post("/simulate", response_model=PaymentResponse, status_code=status.HTTP_200_OK)
def process_simulated_payment(
    req: SimulatedPaymentRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    payment = MVPPaymentService.process_simulated(db, req)
    return payment


@router.post("/at-location", response_model=PaymentResponse, status_code=status.HTTP_200_OK)
def process_at_location_payment(
    req: AtLocationPaymentRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    payment = MVPPaymentService.process_at_location(db, req)
    return payment
