"""
Local Tour Guide Module 05 — Phase 3 Booking Engine & Cash Payment Service.

Implements:
- Server-side price calculation, duration calculation, and group-size enforcement
- Transactional overlap validation & SQL Server sp_getapplock concurrency guard
- Lazy expiration of stale pending booking requests
- Provider booking request acceptance, rejection, start, completion, and cancellation
- Direct cash payment recording (CASH_RECEIVED) without implying online payment
- Policy-based cancellation & refund eligibility calculation
- Centralized dbo.booking_references & dbo.booking_status_history synchronization
- In-app notifications for both traveler and guide across all lifecycle transitions
- Provider unified booking/availability calendar & Admin dispute-safe booking oversight
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import math
import uuid

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, true
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.core.notification_service import create_notification
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, BookingStatusHistory
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuidePackage,
)
from app.models.user import User
from app.schemas.tour_guide import (
    AdminTourGuideBookingListResponse,
    BookingStatusHistoryItem,
    ProviderBookingBucket,
    ProviderBookingCalendarResponse,
    ProviderCalendarBookingItem,
    ProviderCalendarDay,
    RefundEligibilitySummary,
    TourGuideAvailabilityResponse,
    TourGuideBookingCancelResponse,
    TourGuideBookingCreate,
    TourGuideBookingDetailResponse,
    TourGuideBookingListResponse,
    TourGuideBookingResponse,
    TravelerBookingBucket,
)
from app.services.tour_guide_public import get_public_guide_or_404
from app.services.tour_guide_sync import acquire_guide_booking_lock, package_subtotal

PENDING_REQUEST_EXPIRY_HOURS = 24
ACTIVE_OCCUPYING_STATUSES = ("confirmed", "in_progress")
MAX_CALENDAR_SPAN_DAYS = 90
DEFAULT_CALENDAR_SPAN_DAYS = 30
ISSUE_BOOKING_STATUSES = ("cancelled", "rejected", "expired")
ISSUE_PAYMENT_STATUSES = ("refund_pending", "failed")


def _now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise


def _acquire_guide_booking_lock(db: Session, guide_id: int) -> None:
    acquire_guide_booking_lock(db, f"tour_guide_booking_{guide_id}")


# =============================================================================
# 1. SHARED BOOKING REFERENCE & STATUS HISTORY SYNC
# =============================================================================

def sync_tour_guide_booking_reference(
    db: Session,
    booking: TourGuideBooking,
    guide: TourGuide,
) -> BookingReference:
    """Create or synchronize dbo.booking_references for a tour_guide booking."""
    ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )
    if ref is None:
        code = f"BK-TG-{booking.id}-{uuid.uuid4().hex[:8].upper()}"
        ref = BookingReference(
            booking_reference=code,
            module_type="tour_guide",
            target_booking_id=booking.id,
            user_id=booking.traveler_id,
            provider_profile_id=guide.provider_profile_id,
            total_price=booking.total_price,
            currency=booking.currency or "LKR",
            booking_status=booking.booking_status,
            payment_status=booking.payment_status,
        )
        db.add(ref)
        db.flush()
    else:
        ref.total_price = booking.total_price
        ref.currency = booking.currency or "LKR"
        ref.booking_status = booking.booking_status
        ref.payment_status = booking.payment_status
    return ref


def record_tour_guide_status_change(
    db: Session,
    *,
    booking_reference_id: int,
    from_status: str | None,
    to_status: str,
    changed_by: int | None,
    notes: str | None = None,
) -> BookingStatusHistory:
    entry = BookingStatusHistory(
        booking_reference_id=booking_reference_id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        notes=notes[:500] if notes else None,
    )
    db.add(entry)
    return entry


def _get_booking_reference_map(
    db: Session,
    booking_ids: list[int],
) -> dict[int, BookingReference]:
    if not booking_ids:
        return {}
    refs = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id.in_(booking_ids),
        )
        .all()
    )
    return {r.target_booking_id: r for r in refs}


def _get_status_history(
    db: Session,
    booking_ref: BookingReference | None,
) -> list[BookingStatusHistoryItem]:
    if booking_ref is None:
        return []
    rows = (
        db.query(BookingStatusHistory)
        .filter(BookingStatusHistory.booking_reference_id == booking_ref.id)
        .order_by(BookingStatusHistory.created_at.asc(), BookingStatusHistory.id.asc())
        .all()
    )
    return [BookingStatusHistoryItem.model_validate(r) for r in rows]


# =============================================================================
# 2. REFUND ELIGIBILITY & SERIALIZATION
# =============================================================================

def calculate_refund_eligibility(
    booking: TourGuideBooking,
    *,
    cancelled_by_role: str = "traveler",
    as_of: datetime | None = None,
) -> RefundEligibilitySummary:
    now = as_of or _now_utc()
    hours_until = round((booking.start_datetime - now).total_seconds() / 3600.0, 2)
    free_hours = booking.free_cancellation_hours_snapshot

    if cancelled_by_role == "provider":
        is_free = True
        eligible_pct = 100
        policy_note = "Provider-initiated cancellation qualifies for full refund eligibility."
    elif booking.booking_status == "pending":
        is_free = True
        eligible_pct = 100
        policy_note = "Pending booking request cancelled before confirmation (no penalty)."
    elif hours_until >= free_hours:
        is_free = True
        eligible_pct = 100
        policy_note = (
            f"Cancelled at least {free_hours} hour(s) before tour start — "
            "eligible for free cancellation."
        )
    else:
        is_free = False
        eligible_pct = 0
        policy_note = (
            f"Cancelled within {free_hours} hour(s) of tour start — "
            "outside free cancellation window."
        )

    if booking.payment_method == "pay_at_location":
        eligible_amount = Decimal("0.00")
        if booking.payment_status == "unpaid":
            policy_note += " Direct cash reservation was unpaid; no online refund required."
        else:
            policy_note += " Direct cash payment must be settled directly with the guide."
    elif booking.payment_status in ("paid", "refund_pending") and is_free:
        eligible_amount = booking.total_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        eligible_amount = Decimal("0.00")

    return RefundEligibilitySummary(
        is_free_cancellation=is_free,
        free_cancellation_hours=free_hours,
        hours_until_start=hours_until,
        eligible_refund_percent=eligible_pct,
        eligible_refund_amount=eligible_amount,
        currency=booking.currency or "LKR",
        policy_note=policy_note,
    )


def _cash_payment_label(booking: TourGuideBooking) -> str | None:
    if booking.payment_method != "pay_at_location":
        return None
    if booking.payment_status == "paid":
        return "CASH_RECEIVED"
    return "UNPAID"


def _serialize_booking(
    booking: TourGuideBooking,
    booking_ref: BookingReference | None = None,
) -> TourGuideBookingResponse:
    guide = booking.tour_guide
    provider = guide.provider_profile
    return TourGuideBookingResponse(
        id=booking.id,
        booking_reference=booking_ref.booking_reference if booking_ref else None,
        tour_guide_id=booking.tour_guide_id,
        guide_business_name=provider.business_name,
        guide_display_name=provider.user.full_name,
        guide_base_city=guide.base_city,
        guide_base_district=guide.base_district,
        traveler_id=booking.traveler_id,
        traveler_name=booking.traveler.full_name,
        package_id=booking.package_id,
        package_title=booking.package.title if booking.package else None,
        booking_date=booking.booking_date,
        start_datetime=booking.start_datetime,
        end_datetime=booking.end_datetime,
        number_of_people=booking.number_of_people,
        pickup_location=booking.pickup_location,
        special_requests=booking.special_requests,
        total_price=booking.total_price,
        currency=booking.currency,
        booking_status=booking.booking_status,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        is_online_payment=(booking.payment_method == "online"),
        cash_payment_status=_cash_payment_label(booking),
        expires_at=booking.expires_at,
        cancellation_policy_snapshot=booking.cancellation_policy_snapshot,
        free_cancellation_hours_snapshot=booking.free_cancellation_hours_snapshot,
        cancellation_reason=booking.cancellation_reason,
        cancelled_by=booking.cancelled_by,
        cancelled_at=booking.cancelled_at,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


def _serialize_booking_detail(
    db: Session,
    booking: TourGuideBooking,
    booking_ref: BookingReference | None = None,
    *,
    include_refund_preview: bool = True,
) -> TourGuideBookingDetailResponse:
    base = _serialize_booking(booking, booking_ref)
    eligibility = None
    if include_refund_preview and booking.booking_status in ("pending", "confirmed", "cancelled"):
        if booking.booking_status == "cancelled":
            guide_user_id = booking.tour_guide.provider_profile.user_id
            actor_role = "provider" if booking.cancelled_by == guide_user_id else "traveler"
            eligibility = calculate_refund_eligibility(
                booking,
                cancelled_by_role=actor_role,
                as_of=booking.cancelled_at or _now_utc(),
            )
        else:
            eligibility = calculate_refund_eligibility(booking)
    return TourGuideBookingDetailResponse(
        **base.model_dump(),
        refund_eligibility=eligibility,
        status_history=_get_status_history(db, booking_ref),
    )


def _booking_eager_query(db: Session):
    return db.query(TourGuideBooking).options(
        selectinload(TourGuideBooking.tour_guide)
        .selectinload(TourGuide.provider_profile)
        .selectinload(ProviderProfile.user),
        selectinload(TourGuideBooking.traveler),
        selectinload(TourGuideBooking.package),
    )


# =============================================================================
# 3. LAZY EXPIRATION & OVERLAP CHECKING
# =============================================================================

def expire_stale_pending_bookings(
    db: Session,
    *,
    tour_guide_id: int | None = None,
    traveler_id: int | None = None,
) -> int:
    """
    Lazily expire pending tour guide bookings whose expires_at timestamp has passed.
    Never expires paid or confirmed bookings.
    """
    now = _now_utc()
    query = _booking_eager_query(db).filter(
        TourGuideBooking.booking_status == "pending",
        TourGuideBooking.payment_status != "paid",
        TourGuideBooking.expires_at.is_not(None),
        TourGuideBooking.expires_at <= now,
    )
    if tour_guide_id is not None:
        query = query.filter(TourGuideBooking.tour_guide_id == tour_guide_id)
    if traveler_id is not None:
        query = query.filter(TourGuideBooking.traveler_id == traveler_id)

    stale = query.all()
    if not stale:
        return 0

    for booking in stale:
        booking.booking_status = "expired"
        ref = sync_tour_guide_booking_reference(db, booking, booking.tour_guide)
        record_tour_guide_status_change(
            db,
            booking_reference_id=ref.id,
            from_status="pending",
            to_status="expired",
            changed_by=None,
            notes="Booking request expired without provider confirmation.",
        )
        create_notification(
            db=db,
            user_id=booking.traveler_id,
            title="Tour Guide Booking Request Expired",
            message=(
                f"Your booking request #{booking.id} for {booking.tour_guide.provider_profile.business_name} "
                f"on {booking.booking_date} expired without confirmation."
            ),
            notification_type="guide_booking_expired",
            link_url=f"/tour-guide-bookings/{booking.id}",
        )
        create_notification(
            db=db,
            user_id=booking.tour_guide.provider_profile.user_id,
            title="Pending Booking Request Expired",
            message=f"Pending tour guide booking request #{booking.id} on {booking.booking_date} has expired.",
            notification_type="guide_booking_expired",
            link_url=f"/provider/tour-guides/bookings/{booking.id}",
        )

    _commit(db)
    return len(stale)


def _assert_window_has_no_conflicts(
    db: Session,
    *,
    tour_guide_id: int,
    start_dt: datetime,
    end_dt: datetime,
    exclude_booking_id: int | None = None,
) -> None:
    """
    Validate that [start_dt, end_dt) does not overlap any blocked availability
    slot or any confirmed/in_progress booking for the given guide.
    """
    # 1. Blocked availability slots
    blocked_slots = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.tour_guide_id == tour_guide_id,
            TourGuideAvailability.is_available == False,
            TourGuideAvailability.available_date >= start_dt.date(),
            TourGuideAvailability.available_date <= end_dt.date(),
        )
        .all()
    )
    for slot in blocked_slots:
        slot_start = datetime.combine(slot.available_date, slot.start_time or time.min)
        slot_end = (
            datetime.combine(slot.available_date, slot.end_time)
            if slot.end_time is not None
            else datetime.combine(slot.available_date, time(23, 59, 59, 999999))
        )
        if slot_start < end_dt and slot_end > start_dt:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Guide is unavailable on {slot.available_date} during the requested time window.",
            )

    # 2. Overlapping confirmed or in_progress bookings
    overlap_q = db.query(TourGuideBooking).filter(
        TourGuideBooking.tour_guide_id == tour_guide_id,
        TourGuideBooking.booking_status.in_(ACTIVE_OCCUPYING_STATUSES),
        TourGuideBooking.start_datetime < end_dt,
        TourGuideBooking.end_datetime > start_dt,
    )
    if exclude_booking_id is not None:
        overlap_q = overlap_q.filter(TourGuideBooking.id != exclude_booking_id)

    conflict = overlap_q.first()
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tour guide already has a confirmed booking overlapping the requested date/time.",
        )


# =============================================================================
# 4. TRAVELER BOOKING CREATION, LISTING, DETAIL & CANCELLATION (TR-11,13,14,15,19)
# =============================================================================

def create_tour_guide_booking(
    db: Session,
    *,
    guide_id: int,
    traveler: User,
    payload: TourGuideBookingCreate,
) -> TourGuideBookingDetailResponse:
    guide = get_public_guide_or_404(db, guide_id)

    if not guide.is_available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This tour guide is currently not accepting new bookings.",
        )

    if guide.provider_profile.user_id == traveler.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot book your own tour guide profile.",
        )

    # Server-authoritative pricing, duration, and group-size enforcement (GS-04, GS-05)
    package: TourGuidePackage | None = None
    start_dt = datetime.combine(payload.booking_date, payload.start_time)

    if payload.rate_type == "package":
        package = (
            db.query(TourGuidePackage)
            .filter(
                TourGuidePackage.id == payload.package_id,
                TourGuidePackage.tour_guide_id == guide.id,
                TourGuidePackage.is_active == true(),
            )
            .first()
        )
        if package is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Active tour package not found for this guide.",
            )
        if payload.number_of_people > package.max_people:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"package maximum of {package.max_people} travelers."
                ),
            )
        end_dt = start_dt + timedelta(hours=float(package.duration_hours))
        currency = package.currency
        total_price = package_subtotal(package.price, package.pricing_type, payload.number_of_people)

    elif payload.rate_type == "hourly":
        if guide.hourly_rate is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This tour guide does not offer hourly bookings.",
            )
        if payload.number_of_people > guide.max_group_size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"guide maximum of {guide.max_group_size} travelers."
                ),
            )
        end_dt = start_dt + timedelta(hours=float(payload.duration_hours))
        currency = guide.currency
        total_price = (guide.hourly_rate * payload.duration_hours).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    else:  # "daily"
        if payload.number_of_people > guide.max_group_size:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"Requested group size ({payload.number_of_people}) exceeds "
                    f"guide maximum of {guide.max_group_size} travelers."
                ),
            )
        last_day = payload.booking_date + timedelta(days=payload.duration_days - 1)
        end_dt = datetime.combine(last_day, time(18, 0))
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=8)
        currency = guide.currency
        total_price = (guide.daily_rate * Decimal(payload.duration_days)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    now = _now_utc()
    if start_dt <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Booking start date and time must be in the future.",
        )

    # Transactional lock + overlap checks + duplicate pending guard
    _acquire_guide_booking_lock(db, guide.id)
    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    _assert_window_has_no_conflicts(
        db, tour_guide_id=guide.id, start_dt=start_dt, end_dt=end_dt
    )

    duplicate_pending = (
        db.query(TourGuideBooking.id)
        .filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.traveler_id == traveler.id,
            TourGuideBooking.booking_status == "pending",
            TourGuideBooking.start_datetime < end_dt,
            TourGuideBooking.end_datetime > start_dt,
        )
        .first()
    )
    if duplicate_pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a pending booking request for this guide during this time window.",
        )

    expires_at = min(
        now + timedelta(hours=PENDING_REQUEST_EXPIRY_HOURS),
        start_dt,
    )

    booking = TourGuideBooking(
        tour_guide_id=guide.id,
        traveler_id=traveler.id,
        package_id=package.id if package else None,
        booking_date=payload.booking_date,
        start_datetime=start_dt,
        end_datetime=end_dt,
        number_of_people=payload.number_of_people,
        pickup_location=payload.pickup_location.strip() if payload.pickup_location else None,
        special_requests=payload.special_requests.strip() if payload.special_requests else None,
        total_price=total_price,
        currency=currency,
        booking_status="pending",
        payment_method=payload.payment_method,
        payment_status="unpaid",
        expires_at=expires_at,
        cancellation_policy_snapshot=guide.cancellation_policy,
        free_cancellation_hours_snapshot=guide.free_cancellation_hours,
    )
    db.add(booking)
    db.flush()

    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status=None,
        to_status="pending",
        changed_by=traveler.id,
        notes=(
            f"Booking requested ({payload.payment_method}, {payload.number_of_people} traveler(s), "
            f"{total_price} {currency})."
        ),
    )

    # Dual in-app notifications (TR-19)
    create_notification(
        db=db,
        user_id=traveler.id,
        title="Tour Guide Booking Request Submitted",
        message=(
            f"Your booking request with {guide.provider_profile.business_name} on "
            f"{booking.booking_date} ({total_price} {currency}, {payload.payment_method}) "
            "is pending guide approval."
        ),
        notification_type="guide_booking_requested",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )
    create_notification(
        db=db,
        user_id=guide.provider_profile.user_id,
        title="New Tour Guide Booking Request",
        message=(
            f"{traveler.full_name} requested a tour on {booking.booking_date} for "
            f"{booking.number_of_people} traveler(s) ({total_price} {currency})."
        ),
        notification_type="guide_booking_requested",
        link_url=f"/provider/tour-guides/bookings/{booking.id}",
    )

    _commit(db)
    reloaded = _booking_eager_query(db).filter(TourGuideBooking.id == booking.id).first()
    return _serialize_booking_detail(db, reloaded, ref)


def list_traveler_tour_guide_bookings(
    db: Session,
    *,
    traveler: User,
    bucket: TravelerBookingBucket = "all",
    booking_status: str | None = None,
    payment_method: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideBookingListResponse:
    expire_stale_pending_bookings(db, traveler_id=traveler.id)
    now = _now_utc()

    query = _booking_eager_query(db).filter(TourGuideBooking.traveler_id == traveler.id)

    if bucket == "upcoming":
        query = query.filter(
            TourGuideBooking.booking_status.in_(("pending", "confirmed", "in_progress")),
            TourGuideBooking.end_datetime >= now,
        )
    elif bucket == "past":
        query = query.filter(
            or_(
                TourGuideBooking.booking_status == "completed",
                and_(
                    TourGuideBooking.booking_status.in_(("confirmed", "in_progress")),
                    TourGuideBooking.end_datetime < now,
                ),
            )
        )
    elif bucket == "cancelled":
        query = query.filter(
            TourGuideBooking.booking_status.in_(("cancelled", "rejected", "expired"))
        )

    if booking_status:
        query = query.filter(TourGuideBooking.booking_status == booking_status.strip().lower())
    if payment_method:
        query = query.filter(TourGuideBooking.payment_method == payment_method.strip().lower())

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(TourGuideBooking.start_datetime.desc(), TourGuideBooking.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ref_map = _get_booking_reference_map(db, [b.id for b in rows])
    items = [_serialize_booking(b, ref_map.get(b.id)) for b in rows]

    return TourGuideBookingListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def get_traveler_booking_detail(
    db: Session,
    *,
    booking_id: int,
    caller: User,
) -> TourGuideBookingDetailResponse:
    booking = _booking_eager_query(db).filter(TourGuideBooking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )
    if caller.role != "admin" and booking.traveler_id != caller.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to access this booking.",
        )
    expire_stale_pending_bookings(db, traveler_id=booking.traveler_id)
    db.refresh(booking)
    ref = _get_booking_reference_map(db, [booking.id]).get(booking.id)
    return _serialize_booking_detail(db, booking, ref)


# =============================================================================
# 5. PROVIDER BOOKING MANAGEMENT, APPROVAL, CASH & LIFECYCLE (PR-11..14)
# =============================================================================

def list_provider_tour_guide_bookings(
    db: Session,
    *,
    guide: TourGuide,
    bucket: ProviderBookingBucket = "all",
    booking_status: str | None = None,
    payment_method: str | None = None,
    payment_status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> TourGuideBookingListResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )

    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    now = _now_utc()

    query = _booking_eager_query(db).filter(TourGuideBooking.tour_guide_id == guide.id)

    if bucket == "requests":
        query = query.filter(TourGuideBooking.booking_status == "pending")
    elif bucket == "upcoming":
        query = query.filter(
            TourGuideBooking.booking_status.in_(("confirmed", "in_progress")),
            TourGuideBooking.end_datetime >= now,
        )
    elif bucket == "completed":
        query = query.filter(TourGuideBooking.booking_status == "completed")
    elif bucket == "cancelled":
        query = query.filter(
            TourGuideBooking.booking_status.in_(("cancelled", "rejected", "expired"))
        )
    elif bucket == "past":
        query = query.filter(
            or_(
                TourGuideBooking.booking_status == "completed",
                TourGuideBooking.end_datetime < now,
            )
        )

    if booking_status:
        query = query.filter(TourGuideBooking.booking_status == booking_status.strip().lower())
    if payment_method:
        query = query.filter(TourGuideBooking.payment_method == payment_method.strip().lower())
    if payment_status:
        query = query.filter(TourGuideBooking.payment_status == payment_status.strip().lower())
    if date_from:
        query = query.filter(TourGuideBooking.booking_date >= date_from)
    if date_to:
        query = query.filter(TourGuideBooking.booking_date <= date_to)

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(TourGuideBooking.start_datetime.desc(), TourGuideBooking.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ref_map = _get_booking_reference_map(db, [b.id for b in rows])
    items = [_serialize_booking(b, ref_map.get(b.id)) for b in rows]

    return TourGuideBookingListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
    )


def _require_provider_owned_booking(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
) -> TourGuideBooking:
    booking = (
        _booking_eager_query(db)
        .filter(
            TourGuideBooking.id == booking_id,
            TourGuideBooking.tour_guide_id == guide.id,
        )
        .first()
    )
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )
    return booking


def get_provider_booking_detail(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
) -> TourGuideBookingDetailResponse:
    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)
    ref = _get_booking_reference_map(db, [booking.id]).get(booking.id)
    return _serialize_booking_detail(db, booking, ref)


def accept_tour_guide_booking(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
    notes: str | None = None,
) -> TourGuideBookingDetailResponse:
    _acquire_guide_booking_lock(db, guide.id)
    if guide.approval_status != "approved" or not guide.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only active, approved tour guides can accept new booking requests.",
        )
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)

    now = _now_utc()
    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending bookings can be accepted (current status: '{booking.booking_status}').",
        )

    if booking.expires_at is not None and booking.expires_at <= now:
        booking.booking_status = "expired"
        ref = sync_tour_guide_booking_reference(db, booking, guide)
        record_tour_guide_status_change(
            db,
            booking_reference_id=ref.id,
            from_status="pending",
            to_status="expired",
            changed_by=None,
            notes="Pending booking expired before provider acceptance.",
        )
        _commit(db)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This booking request has expired and can no longer be accepted.",
        )

    # Transactional overlap check prevents duplicate confirmations for overlapping windows
    _assert_window_has_no_conflicts(
        db,
        tour_guide_id=guide.id,
        start_dt=booking.start_datetime,
        end_dt=booking.end_datetime,
        exclude_booking_id=booking.id,
    )

    booking.booking_status = "confirmed"
    booking.expires_at = None
    # Keep payment_status strictly 'unpaid' on confirmation (cash remains unpaid until receipt)
    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status="pending",
        to_status="confirmed",
        changed_by=guide.provider_profile.user_id,
        notes=notes or f"Booking confirmed by guide ({booking.payment_method}, payment {booking.payment_status}).",
    )

    payment_hint = (
        "Please pay cash directly to your guide at the meeting point."
        if booking.payment_method == "pay_at_location"
        else "Please complete online payment to finalize settlement."
    )
    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Tour Guide Booking Confirmed!",
        message=(
            f"{guide.provider_profile.business_name} accepted your booking for "
            f"{booking.booking_date}. {payment_hint}"
        ),
        notification_type="guide_booking_confirmed",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(booking)
    return _serialize_booking_detail(db, booking, ref)


def reject_tour_guide_booking(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
    reason: str,
) -> TourGuideBookingDetailResponse:
    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)

    if booking.booking_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only pending bookings can be rejected (current status: '{booking.booking_status}').",
        )

    now = _now_utc()
    booking.booking_status = "rejected"
    booking.expires_at = None
    booking.cancellation_reason = reason
    booking.cancelled_by = guide.provider_profile.user_id
    booking.cancelled_at = now

    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status="pending",
        to_status="rejected",
        changed_by=guide.provider_profile.user_id,
        notes=f"Rejected by guide: {reason}",
    )

    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Tour Guide Booking Request Declined",
        message=(
            f"{guide.provider_profile.business_name} declined your booking request for "
            f"{booking.booking_date}. Reason: {reason}"
        ),
        notification_type="guide_booking_rejected",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(booking)
    return _serialize_booking_detail(db, booking, ref)


def record_tour_guide_cash_payment(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
    notes: str | None = None,
) -> TourGuideBookingDetailResponse:
    """
    Record direct cash received by the tour guide (PR-14 / TR-13).
    Strictly preserves payment_method='pay_at_location' and NEVER writes into
    the online CeylonGo gateway payment ledger.
    """
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)

    if booking.payment_method != "pay_at_location":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cash receipt can only be recorded for 'pay_at_location' bookings.",
        )

    if booking.booking_status not in ("confirmed", "in_progress"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cash receipt can only be recorded for confirmed or in-progress bookings "
                f"(current status: '{booking.booking_status}')."
            ),
        )

    if booking.payment_status == "paid":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cash payment has already been recorded for this booking.",
        )

    booking.payment_status = "paid"
    ref = sync_tour_guide_booking_reference(db, booking, guide)

    audit_note = "CASH_RECEIVED: Direct cash payment recorded by guide (pay_at_location; not an online payment)."
    if notes and notes.strip():
        audit_note = f"{audit_note} Notes: {notes.strip()}"

    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status=booking.booking_status,
        to_status=booking.booking_status,
        changed_by=guide.provider_profile.user_id,
        notes=audit_note,
    )

    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Cash Payment Recorded",
        message=(
            f"{guide.provider_profile.business_name} confirmed receipt of your direct cash "
            f"payment ({booking.total_price} {booking.currency}) for booking #{booking.id}."
        ),
        notification_type="guide_cash_payment_received",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(booking)
    return _serialize_booking_detail(db, booking, ref)


def start_tour_guide_booking(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
    notes: str | None = None,
) -> TourGuideBookingDetailResponse:
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)

    if booking.booking_status != "confirmed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only confirmed bookings can be started (current status: '{booking.booking_status}').",
        )

    booking.booking_status = "in_progress"
    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status="confirmed",
        to_status="in_progress",
        changed_by=guide.provider_profile.user_id,
        notes=notes or "Tour started by guide.",
    )

    _commit(db)
    db.refresh(booking)
    return _serialize_booking_detail(db, booking, ref)


def complete_tour_guide_booking(
    db: Session,
    *,
    guide: TourGuide,
    booking_id: int,
    notes: str | None = None,
) -> TourGuideBookingDetailResponse:
    booking = _require_provider_owned_booking(db, guide=guide, booking_id=booking_id)

    if booking.booking_status not in ("confirmed", "in_progress"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Only confirmed or in-progress bookings can be completed "
                f"(current status: '{booking.booking_status}')."
            ),
        )

    if booking.payment_status != "paid":
        if booking.payment_method == "pay_at_location":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Record cash received (CASH_RECEIVED) before marking a cash booking as completed.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Online payment must be verified as paid before marking booking as completed.",
        )

    old_status = booking.booking_status
    booking.booking_status = "completed"
    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status=old_status,
        to_status="completed",
        changed_by=guide.provider_profile.user_id,
        notes=notes or "Tour guide service completed.",
    )

    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Tour Completed — Thank You!",
        message=(
            f"Your tour with {guide.provider_profile.business_name} on {booking.booking_date} "
            "is now marked completed."
        ),
        notification_type="guide_booking_completed",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(booking)
    return _serialize_booking_detail(db, booking, ref)


# =============================================================================
# 6. POLICY-BASED CANCELLATION (TRAVELER & PROVIDER) (TR-15 / TR-19)
# =============================================================================

def cancel_tour_guide_booking(
    db: Session,
    *,
    booking: TourGuideBooking,
    actor: User,
    actor_role: str,
    reason: str,
) -> TourGuideBookingCancelResponse:
    expire_stale_pending_bookings(db, tour_guide_id=booking.tour_guide_id)
    db.refresh(booking)
    allowed_from = ("pending", "confirmed") if actor_role == "traveler" else ("confirmed",)
    if booking.booking_status not in allowed_from:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot cancel booking with status '{booking.booking_status}'. "
                f"Allowed statuses for {actor_role} cancellation: {', '.join(allowed_from)}."
            ),
        )

    now = _now_utc()
    old_status = booking.booking_status
    eligibility = calculate_refund_eligibility(
        booking, cancelled_by_role=actor_role, as_of=now
    )

    booking.booking_status = "cancelled"
    booking.expires_at = None
    booking.cancellation_reason = reason
    booking.cancelled_by = actor.id
    booking.cancelled_at = now

    # Flag paid online bookings for refund when eligible (Phase 4 executes actual refund)
    if (
        booking.payment_method == "online"
        and booking.payment_status == "paid"
        and eligibility.eligible_refund_amount > Decimal("0.00")
    ):
        booking.payment_status = "refund_pending"

    guide = booking.tour_guide
    ref = sync_tour_guide_booking_reference(db, booking, guide)
    record_tour_guide_status_change(
        db,
        booking_reference_id=ref.id,
        from_status=old_status,
        to_status="cancelled",
        changed_by=actor.id,
        notes=(
            f"Cancelled by {actor_role}: {reason} | "
            f"Refund eligibility: {eligibility.eligible_refund_percent}% "
            f"({eligibility.eligible_refund_amount} {eligibility.currency})"
        ),
    )

    # Notify both parties (Spec Section 7 & TR-19)
    create_notification(
        db=db,
        user_id=booking.traveler_id,
        title="Tour Guide Booking Cancelled",
        message=(
            f"Booking #{booking.id} with {guide.provider_profile.business_name} on "
            f"{booking.booking_date} was cancelled by {actor_role}. {eligibility.policy_note}"
        ),
        notification_type="guide_booking_cancelled",
        link_url=f"/tour-guide-bookings/{booking.id}",
    )
    create_notification(
        db=db,
        user_id=guide.provider_profile.user_id,
        title="Tour Guide Booking Cancelled",
        message=(
            f"Booking #{booking.id} on {booking.booking_date} was cancelled by {actor_role}. "
            f"Reason: {reason}. Your calendar availability has been released."
        ),
        notification_type="guide_booking_cancelled",
        link_url=f"/provider/tour-guides/bookings/{booking.id}",
    )

    _commit(db)
    db.refresh(booking)
    detail = _serialize_booking_detail(db, booking, ref, include_refund_preview=False)
    detail.refund_eligibility = eligibility

    return TourGuideBookingCancelResponse(
        booking=detail,
        refund_eligibility=eligibility,
        availability_released=(old_status == "confirmed"),
        message="Tour guide booking cancelled successfully.",
    )


# =============================================================================
# 7. PROVIDER UNIFIED BOOKING & AVAILABILITY CALENDAR (SCOPE #8)
# =============================================================================

def get_provider_booking_calendar(
    db: Session,
    *,
    guide: TourGuide,
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProviderBookingCalendarResponse:
    expire_stale_pending_bookings(db, tour_guide_id=guide.id)
    today_utc = _now_utc().date()
    start_d = date_from or today_utc
    end_d = date_to or (start_d + timedelta(days=DEFAULT_CALENDAR_SPAN_DAYS))

    if end_d < start_d:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )
    if (end_d - start_d).days > MAX_CALENDAR_SPAN_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Calendar range cannot exceed {MAX_CALENDAR_SPAN_DAYS} days",
        )

    slots = (
        db.query(TourGuideAvailability)
        .filter(
            TourGuideAvailability.tour_guide_id == guide.id,
            TourGuideAvailability.available_date >= start_d,
            TourGuideAvailability.available_date <= end_d,
        )
        .order_by(
            TourGuideAvailability.available_date.asc(),
            TourGuideAvailability.start_time.asc(),
        )
        .all()
    )
    slots_by_date: dict[date, list[TourGuideAvailability]] = {}
    for s in slots:
        slots_by_date.setdefault(s.available_date, []).append(s)

    range_start_dt = datetime.combine(start_d, time.min)
    range_end_dt = datetime.combine(end_d, time(23, 59, 59, 999999))
    bookings = (
        _booking_eager_query(db)
        .filter(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.booking_status.in_(("pending", "confirmed", "in_progress")),
            TourGuideBooking.start_datetime <= range_end_dt,
            TourGuideBooking.end_datetime >= range_start_dt,
        )
        .order_by(TourGuideBooking.start_datetime.asc())
        .all()
    )
    ref_map = _get_booking_reference_map(db, [b.id for b in bookings])

    days: list[ProviderCalendarDay] = []
    for offset in range((end_d - start_d).days + 1):
        cur_date = start_d + timedelta(days=offset)
        cur_start = datetime.combine(cur_date, time.min)
        cur_end = datetime.combine(cur_date, time(23, 59, 59, 999999))
        day_slots = slots_by_date.get(cur_date, [])
        day_bookings: list[ProviderCalendarBookingItem] = []

        for b in bookings:
            if b.start_datetime < cur_end and b.end_datetime > cur_start:
                ref = ref_map.get(b.id)
                day_bookings.append(
                    ProviderCalendarBookingItem(
                        booking_id=b.id,
                        booking_reference=ref.booking_reference if ref else None,
                        traveler_name=b.traveler.full_name,
                        package_title=b.package.title if b.package else None,
                        start_datetime=b.start_datetime,
                        end_datetime=b.end_datetime,
                        number_of_people=b.number_of_people,
                        booking_status=b.booking_status,
                        payment_method=b.payment_method,
                        payment_status=b.payment_status,
                    )
                )

        days.append(
            ProviderCalendarDay(
                date=cur_date,
                availability_slots=[
                    TourGuideAvailabilityResponse.model_validate(s) for s in day_slots
                ],
                bookings=day_bookings,
                has_confirmed_booking=any(
                    b.booking_status in ACTIVE_OCCUPYING_STATUSES for b in day_bookings
                ),
                has_pending_request=any(b.booking_status == "pending" for b in day_bookings),
                is_blocked=any(not s.is_available for s in day_slots),
            )
        )

    return ProviderBookingCalendarResponse(
        tour_guide_id=guide.id,
        date_from=start_d,
        date_to=end_d,
        guide_is_available=guide.is_available,
        days=days,
    )


# =============================================================================
# 8. ADMIN BOOKING MONITORING & DISPUTE-SAFE OVERSIGHT (AD-05)
# =============================================================================

def admin_list_tour_guide_bookings(
    db: Session,
    *,
    booking_status: str | None = None,
    payment_method: str | None = None,
    payment_status: str | None = None,
    tour_guide_id: int | None = None,
    traveler_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    issues_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> AdminTourGuideBookingListResponse:
    if date_from and date_to and date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="date_to must be on or after date_from",
        )

    expire_stale_pending_bookings(db, tour_guide_id=tour_guide_id, traveler_id=traveler_id)

    base_q = _booking_eager_query(db)
    if tour_guide_id is not None:
        base_q = base_q.filter(TourGuideBooking.tour_guide_id == tour_guide_id)
    if traveler_id is not None:
        base_q = base_q.filter(TourGuideBooking.traveler_id == traveler_id)
    if date_from is not None:
        base_q = base_q.filter(TourGuideBooking.booking_date >= date_from)
    if date_to is not None:
        base_q = base_q.filter(TourGuideBooking.booking_date <= date_to)

    # Summary counts across scoped bookings
    all_scoped = base_q.all()
    status_counts = {
        "pending": 0,
        "confirmed": 0,
        "in_progress": 0,
        "completed": 0,
        "cancelled": 0,
        "rejected": 0,
        "expired": 0,
    }
    issues_count = 0
    for b in all_scoped:
        if b.booking_status in status_counts:
            status_counts[b.booking_status] += 1
        if (
            b.booking_status in ISSUE_BOOKING_STATUSES
            or b.payment_status in ISSUE_PAYMENT_STATUSES
        ):
            issues_count += 1

    query = base_q
    if booking_status:
        query = query.filter(TourGuideBooking.booking_status == booking_status.strip().lower())
    if payment_method:
        query = query.filter(TourGuideBooking.payment_method == payment_method.strip().lower())
    if payment_status:
        query = query.filter(TourGuideBooking.payment_status == payment_status.strip().lower())
    if issues_only:
        query = query.filter(
            or_(
                TourGuideBooking.booking_status.in_(ISSUE_BOOKING_STATUSES),
                TourGuideBooking.payment_status.in_(ISSUE_PAYMENT_STATUSES),
            )
        )

    total = query.count()
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    rows = (
        query.order_by(TourGuideBooking.created_at.desc(), TourGuideBooking.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    ref_map = _get_booking_reference_map(db, [b.id for b in rows])
    items = [_serialize_booking(b, ref_map.get(b.id)) for b in rows]

    return AdminTourGuideBookingListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_more=(page * page_size) < total,
        status_counts=status_counts,
        issues_count=issues_count,
    )


def admin_get_tour_guide_booking_detail(
    db: Session,
    *,
    booking_id: int,
) -> TourGuideBookingDetailResponse:
    booking = _booking_eager_query(db).filter(TourGuideBooking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tour guide booking not found.",
        )
    expire_stale_pending_bookings(db, tour_guide_id=booking.tour_guide_id)
    db.refresh(booking)
    ref = _get_booking_reference_map(db, [booking.id]).get(booking.id)
    return _serialize_booking_detail(db, booking, ref)
