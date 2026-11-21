"""
FastAPI Router for Platform-Wide Activity Booking Monitoring (C04).
Provides read-only oversight for authorized platform administrators.

Requirements covered:
- C04: Admin Booking Monitoring
  - Global listing with filtering (status, provider, activity, date ranges) and pagination.
  - Granular detail view with strict participant privacy protection.
  - Zero N+1 queries through joined loading.
  - Full audit trail integration via centralized dbo.booking_status_history.
"""

from datetime import date, datetime
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.dependencies import require_admin
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, BookingStatusHistory
from app.models.user import User
from app.schemas.activity_admin import (
    AdminActivityBookingDetail,
    AdminActivityBookingListResponse,
    AdminActivityBookingPagination,
    AdminActivityBookingSummary,
    AdminBookingStatusHistoryItem,
    AdminParticipantSummary,
)

router = APIRouter(
    prefix="/api/v1/admin/activity-bookings",
    tags=["Admin Activity Bookings"],
)

ALLOWED_BOOKING_STATUSES = {
    "pending",
    "confirmed",
    "rejected",
    "cancelled",
    "completed",
}

ALLOWED_PAYMENT_STATUSES = {
    "unpaid",
    "pending",
    "paid",
    "refund_pending",
    "refunded",
    "failed",
}


@router.get(
    "",
    response_model=AdminActivityBookingListResponse,
    summary="List and monitor platform-wide activity bookings (C04)",
)
def list_admin_activity_bookings(
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
    booking_status: str | None = Query(
        default=None,
        description="Filter by booking status: pending, confirmed, rejected, cancelled, completed",
    ),
    payment_status: str | None = Query(
        default=None,
        description="Filter by payment status: unpaid, pending, paid, refund_pending, refunded, failed",
    ),
    payment_method: str | None = Query(
        default=None,
        description="Filter by payment method: pay_at_location, online",
    ),
    provider_profile_id: int | None = Query(
        default=None,
        ge=1,
        description="Filter by provider profile ID",
    ),
    activity_id: int | None = Query(
        default=None,
        ge=1,
        description="Filter by activity ID",
    ),
    date_from: date | None = Query(
        default=None,
        description="Filter bookings created on or after date",
    ),
    date_to: date | None = Query(
        default=None,
        description="Filter bookings created on or before date",
    ),
    start_date: date | None = Query(
        default=None,
        description="Alias for date_from",
    ),
    end_date: date | None = Query(
        default=None,
        description="Alias for date_to",
    ),
    page: int = Query(
        default=1,
        ge=1,
        description="1-based page number",
    ),
    page_size: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Items per page (max 100)",
    ),
):
    """
    Platform-wide activity booking monitoring endpoint for administrators.
    Allows multi-criteria filtering, stable ordering, deterministic pagination,
    and joined fetching to prevent N+1 queries.
    """
    eff_from = date_from or start_date
    eff_to = date_to or end_date

    if eff_from and eff_to and eff_from > eff_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date / date_from cannot be after end_date / date_to",
        )

    cleaned_bs: str | None = None
    if booking_status is not None:
        cleaned_bs = booking_status.strip().lower()
        if cleaned_bs not in ALLOWED_BOOKING_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid booking_status '{booking_status}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_BOOKING_STATUSES))}"
                ),
            )

    cleaned_ps: str | None = None
    if payment_status is not None:
        cleaned_ps = payment_status.strip().lower()
        if cleaned_ps not in ALLOWED_PAYMENT_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Invalid payment_status '{payment_status}'. "
                    f"Allowed: {', '.join(sorted(ALLOWED_PAYMENT_STATUSES))}"
                ),
            )

    query = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(Activity, ActivitySlot.activity_id == Activity.id)
        .join(ProviderProfile, Activity.provider_profile_id == ProviderProfile.id)
        .join(User, ActivityBooking.user_id == User.id)
    )

    if cleaned_bs is not None:
        query = query.filter(ActivityBooking.booking_status == cleaned_bs)

    if cleaned_ps is not None:
        query = query.filter(ActivityBooking.payment_status == cleaned_ps)

    if payment_method is not None:
        query = query.filter(
            func.lower(ActivityBooking.payment_method) == payment_method.strip().lower()
        )

    if provider_profile_id is not None:
        query = query.filter(Activity.provider_profile_id == provider_profile_id)

    if activity_id is not None:
        query = query.filter(Activity.id == activity_id)

    if eff_from is not None:
        start_dt = datetime.combine(eff_from, datetime.min.time())
        query = query.filter(ActivityBooking.created_at >= start_dt)

    if eff_to is not None:
        end_dt = datetime.combine(eff_to, datetime.max.time())
        query = query.filter(ActivityBooking.created_at <= end_dt)

    total = query.count()
    total_pages = max(1, math.ceil(total / page_size)) if total > 0 else 0

    bookings = (
        query.options(
            joinedload(ActivityBooking.activity_slot)
            .joinedload(ActivitySlot.activity)
            .joinedload(Activity.provider_profile),
            joinedload(ActivityBooking.traveler),
        )
        .order_by(ActivityBooking.created_at.desc(), ActivityBooking.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    booking_ids = [b.id for b in bookings]
    refs_map: dict[int, str] = {}
    if booking_ids:
        refs = (
            db.query(BookingReference)
            .filter(
                BookingReference.module_type == "activity",
                BookingReference.target_booking_id.in_(booking_ids),
            )
            .all()
        )
        for r in refs:
            refs_map[r.target_booking_id] = r.booking_reference

    items: list[AdminActivityBookingSummary] = []
    for b in bookings:
        slot = b.activity_slot
        act = slot.activity if slot else None
        prov = act.provider_profile if act else None
        traveler = b.traveler

        items.append(
            AdminActivityBookingSummary(
                id=b.id,
                booking_reference=refs_map.get(b.id),
                activity_id=act.id if act else 0,
                activity_title=act.title if act else "Unknown Activity",
                provider_profile_id=prov.id if prov else 0,
                provider_business_name=prov.business_name if prov else None,
                traveler_id=traveler.id if traveler else b.user_id,
                traveler_name=traveler.full_name if traveler else "Unknown Traveler",
                traveler_email=traveler.email if traveler else "",
                booking_status=b.booking_status,
                created_at=b.created_at,
                slot_id=slot.id if slot else b.activity_slot_id,
                starts_at=slot.starts_at if slot else b.created_at,
                ends_at=slot.ends_at if slot else b.created_at,
                participants=b.participants,
                total_price=b.total_price,
                currency=b.currency or "LKR",
                payment_status=b.payment_status,
                payment_method=b.payment_method,
                cancellation_reason=b.cancellation_reason,
                cancelled_at=b.cancelled_at,
            )
        )

    pagination = AdminActivityBookingPagination(
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )

    return AdminActivityBookingListResponse(
        items=items,
        pagination=pagination,
    )


@router.get(
    "/{booking_id}",
    response_model=AdminActivityBookingDetail,
    summary="Get detailed view of an activity booking for admin (C04)",
)
def get_admin_activity_booking_detail(
    booking_id: int,
    admin: Annotated[User, Depends(require_admin)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Detailed inspection of a specific activity booking for administrators.

    Privacy & Security:
    - Participant sensitive fields (emergency contacts, phone, waivers, medical/special notes)
      are strictly excluded from the response.
    - Payment secrets or external tokens are excluded.
    - Lifecycle status history is extracted from dbo.booking_status_history.
    """
    booking = (
        db.query(ActivityBooking)
        .options(
            joinedload(ActivityBooking.activity_slot)
            .joinedload(ActivitySlot.activity)
            .joinedload(Activity.provider_profile),
            joinedload(ActivityBooking.traveler),
            joinedload(ActivityBooking.participants_list),
        )
        .filter(ActivityBooking.id == booking_id)
        .first()
    )

    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Activity booking not found",
        )

    slot = booking.activity_slot
    act = slot.activity if slot else None
    prov = act.provider_profile if act else None
    traveler = booking.traveler

    # Resolve central booking reference
    b_ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "activity",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )

    # Resolve lifecycle status history
    status_history: list[AdminBookingStatusHistoryItem] = []
    if b_ref:
        history_records = (
            db.query(BookingStatusHistory)
            .filter(BookingStatusHistory.booking_reference_id == b_ref.id)
            .order_by(
                BookingStatusHistory.created_at.asc(),
                BookingStatusHistory.id.asc(),
            )
            .all()
        )
        for h in history_records:
            status_history.append(
                AdminBookingStatusHistoryItem(
                    id=h.id,
                    from_status=h.from_status,
                    to_status=h.to_status,
                    changed_by=h.changed_by,
                    notes=h.notes,
                    created_at=h.created_at,
                )
            )

    # Safe participants strictly excluding sensitive emergency contacts / waivers
    safe_participants: list[AdminParticipantSummary] = []
    for p in (booking.participants_list or []):
        safe_participants.append(
            AdminParticipantSummary(
                id=p.id,
                full_name=p.full_name,
                age=p.age,
            )
        )

    return AdminActivityBookingDetail(
        id=booking.id,
        booking_reference=b_ref.booking_reference if b_ref else None,
        activity_id=act.id if act else 0,
        activity_title=act.title if act else "Unknown Activity",
        activity_category=act.category if act else None,
        district=act.district if act else None,
        city=act.city if act else None,
        provider_profile_id=prov.id if prov else 0,
        provider_business_name=prov.business_name if prov else "Unknown Provider",
        provider_phone=prov.phone if prov else None,
        traveler_id=traveler.id if traveler else booking.user_id,
        traveler_name=traveler.full_name if traveler else "Unknown Traveler",
        traveler_email=traveler.email if traveler else "",
        slot_id=slot.id if slot else booking.activity_slot_id,
        starts_at=slot.starts_at if slot else booking.created_at,
        ends_at=slot.ends_at if slot else booking.created_at,
        participants=booking.participants,
        participants_list=safe_participants,
        total_price=booking.total_price,
        currency=booking.currency or "LKR",
        payment_status=booking.payment_status,
        payment_method=booking.payment_method,
        booking_status=booking.booking_status,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        cancellation_reason=booking.cancellation_reason,
        cancelled_at=booking.cancelled_at,
        status_history=status_history,
    )
