from datetime import UTC, datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.core.booking_transactions import lock_booking_resource
from app.core.listing_sync import record_booking_status_change
from app.core.notification_service import create_notification
from app.models.shared import BookingReference
from app.models.tour_guide import TourGuideAvailability, TourGuideBooking

LOCAL = timezone(timedelta(hours=5, minutes=30))
GUIDE_MODULE = "tour_guide"


def lock_guide(db, guide_id):
    lock_booking_resource(db, f"tour_guide_reservations_{guide_id}")


def require_availability(db, guide_id, start, end):
    """Require uninterrupted coverage of the half-open UTC reservation interval."""
    local_start = start.replace(tzinfo=UTC).astimezone(LOCAL)
    local_end = end.replace(tzinfo=UTC).astimezone(LOCAL)
    first, last = local_start.date(), (local_end - timedelta(microseconds=1)).date()
    rows = db.scalars(
        select(TourGuideAvailability)
        .with_hint(TourGuideAvailability, "WITH (HOLDLOCK)", "mssql")
        .where(
            TourGuideAvailability.tour_guide_id == guide_id,
            TourGuideAvailability.available_date.between(first, last),
        )
    )
    windows = []
    for row in rows:
        day = row.available_date
        if row.start_time is None:
            opens = datetime.combine(day, time.min, LOCAL)
            closes = opens + timedelta(days=1)
        else:
            opens = datetime.combine(day, row.start_time, LOCAL)
            closes = datetime.combine(day, row.end_time, LOCAL)
        if opens >= local_end or closes <= local_start:
            continue
        if not row.is_available:
            raise HTTPException(409, "Guide is unavailable for the requested period")
        windows.append((opens, closes))
    covered = local_start
    for opens, closes in sorted(windows):
        if opens > covered:
            break
        covered = max(covered, closes)
        if covered >= local_end:
            return
    raise HTTPException(409, "Guide is unavailable for the requested period")


def is_expired(row, now):
    return (
        row.booking_status == "pending"
        and row.payment_method == "online"
        and row.expires_at is not None
        and row.expires_at <= now
    )


def expire_hold(db, row, ref, now):
    if not is_expired(row, now):
        return False
    row.booking_status = ref.booking_status = "expired"
    row.payment_status = ref.payment_status = "failed"
    record_booking_status_change(
        db,
        ref.id,
        "awaiting_payment",
        "expired",
        None,
        notes="Guide checkout hold expired",
    )
    create_notification(
        db=db,
        user_id=row.traveler_id,
        title="Tour payment hold expired",
        message=f"Payment time for reservation {ref.booking_reference} expired.",
        notification_type="guide_payment_expired",
    )
    return True


def release_expired_holds(db, guide_id, now):
    row, ref = TourGuideBooking, BookingReference
    for booking, reference in db.execute(
        select(row, ref)
        .join(
            ref,
            (ref.module_type == GUIDE_MODULE)
            & (ref.target_booking_id == row.id)
            & (ref.user_id == row.traveler_id),
        )
        .where(
            row.tour_guide_id == guide_id,
            row.booking_status == "pending",
            row.payment_method == "online",
            row.expires_at <= now,
        )
    ):
        expire_hold(db, booking, reference, now)
    # The application session disables autoflush. Make the overlap check see releases.
    db.flush()
