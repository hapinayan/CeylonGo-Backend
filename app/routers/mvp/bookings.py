"""
CeylonGo Academic MVP — Unified Bookings Router
Endpoints for Booking Creation with Atomic Slot Allocation, Status Transitions, and Traveler Booking History.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.mvp_security import get_current_mvp_user, require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPBooking, MVPListing, MVPUser
from app.schemas.mvp import (
    BookingCreateRequest,
    BookingDetailResponse,
    BookingResponse,
    BookingStatusUpdateRequest,
    ServiceCategory,
)
from app.services.mvp_services import MVPBookingService

router = APIRouter(prefix="/bookings", tags=["MVP Bookings"])


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
def create_booking(
    req: BookingCreateRequest,
    current_user: MVPUser = Depends(require_mvp_role("traveler")),
    db: Session = Depends(get_mvp_db),
):
    booking = MVPBookingService.create_booking(db, current_user, req)
    return booking


@router.get("/my", response_model=list[BookingResponse])
def get_my_bookings(
    current_user: MVPUser = Depends(require_mvp_role("traveler")),
    db: Session = Depends(get_mvp_db),
    limit: int = Query(50, ge=1, le=100),
    before_id: int | None = Query(None, gt=0),
    category: ServiceCategory | None = None,
):
    query = db.query(MVPBooking).filter(MVPBooking.traveler_id == current_user.id)
    if category:
        query = query.join(MVPListing).filter(MVPListing.category == category)
    if before_id is not None:
        query = query.filter(MVPBooking.id < before_id)
    return query.order_by(MVPBooking.id.desc()).limit(limit).all()


@router.get("/{booking_id}", response_model=BookingDetailResponse)
def get_booking(
    booking_id: int,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    booking = db.query(MVPBooking).filter(MVPBooking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found.")
    # Authorization: traveler or provider who owns the listing
    if booking.traveler_id != current_user.id and current_user.role != "admin":
        if not current_user.provider_profile or booking.listing.provider_id != current_user.provider_profile.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    return booking


@router.patch("/{booking_id}/status", response_model=BookingResponse)
def update_booking_status(
    booking_id: int,
    req: BookingStatusUpdateRequest,
    current_user: MVPUser = Depends(get_current_mvp_user),
    db: Session = Depends(get_mvp_db),
):
    booking = MVPBookingService.update_booking_status(db, current_user, booking_id, req)
    return booking
