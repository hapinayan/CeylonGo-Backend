"""
CeylonGo Homestay Booking Expiration Service
============================================
Handles automatic and lazy expiration of homestay bookings whose online payment
deadline has elapsed.

Business Rules:
1. Online payment deadline is 30 minutes after provider approval.
2. An eligible booking for expiration has:
   - status == 'awaiting_payment'
   - payment_status != 'paid'
   - payment_expires_at <= current_utc_time
3. Upon expiration:
   - status transitions to 'expired'
   - expired_at is recorded
   - Centralized booking reference is synchronized
   - Reserved availability is automatically released
   - Booking status history audit record is created
   - Notifications sent to traveler and provider
4. Paid bookings are NEVER expired.
5. Legacy bookings without payment_expires_at are NEVER automatically expired.
6. Safe concurrency protection prevents duplicate expiration execution across workers.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, or_, text
from sqlalchemy.orm import Session

from app.core.listing_sync import (
    record_booking_status_change,
    sync_homestay_booking_reference,
)
from app.core.notification_service import create_notification
from app.database.connection import SessionLocal
from app.models.booking import Booking
from app.models.homestay import Homestay

logger = logging.getLogger("ceylongo.booking_expiration")

# Background worker cancellation control
_worker_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


def expire_single_booking(
    db: Session,
    booking: Booking,
    now_utc: Optional[datetime] = None,
) -> bool:
    """
    Expire a single homestay booking if it is eligible for payment timeout.
    Returns True if the booking was transitioned to 'expired', False otherwise.
    Safe and idempotent.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Eligibility check
    if booking.status != "awaiting_payment":
        return False

    if booking.payment_status == "paid":
        return False

    if booking.payment_expires_at is None:
        return False

    if booking.payment_expires_at > now_utc:
        return False

    # 2. Concurrency lock on SQL Server & re-verify under lock
    if db.bind and db.bind.dialect.name == "mssql":
        try:
            lock_result = db.execute(
                text("""
                    DECLARE @result INT;
                    EXEC @result = sp_getapplock
                        @Resource = :resource,
                        @LockMode = 'Exclusive',
                        @LockOwner = 'Transaction',
                        @LockTimeout = 5000;
                    SELECT @result;
                """),
                {"resource": f"homestay_booking_{booking.homestay_id}"},
            ).scalar()
            if lock_result is None or lock_result < 0:
                return False
            db.refresh(booking)

            if booking.status != "awaiting_payment" or booking.payment_status == "paid":
                return False
            if booking.payment_expires_at is None or booking.payment_expires_at > now_utc:
                return False
        except Exception:
            pass

    old_status = booking.status
    booking.status = "expired"
    booking.expired_at = now_utc

    # 3. Synchronize booking reference & record status history
    homestay = (
        db.query(Homestay)
        .filter(Homestay.id == booking.homestay_id)
        .first()
    )

    if homestay:
        ref = sync_homestay_booking_reference(db, booking, homestay)
        if ref:
            record_booking_status_change(
                db=db,
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status="expired",
                changed_by=None,
                notes="Booking expired: online payment window (30m) exceeded without payment",
            )

    # 4. In-app notifications
    h_name = homestay.name if homestay else "the homestay"

    # Notify traveler
    create_notification(
        db=db,
        user_id=booking.user_id,
        title="Booking Expired",
        message=(
            f"Your booking for {h_name} ({booking.check_in} to {booking.check_out}) "
            f"expired because online payment was not completed within 30 minutes."
        ),
        notification_type="booking_expired",
        link_url=f"/bookings/{booking.id}",
    )

    # Notify property provider
    provider_user_id = homestay.owner_id if homestay else None
    if not provider_user_id and homestay and homestay.provider_profile:
        provider_user_id = homestay.provider_profile.user_id

    if provider_user_id:
        create_notification(
            db=db,
            user_id=provider_user_id,
            title="Booking Window Expired",
            message=(
                f"Booking #{booking.id} ({booking.check_in} to {booking.check_out}) "
                f"expired without payment. The reserved calendar availability has been released."
            ),
            notification_type="booking_expired",
            link_url=f"/provider/bookings/{booking.id}",
        )

    logger.info(
        f"Expired unpaid homestay booking #{booking.id} (expired_at={now_utc.isoformat()})"
    )
    return True


def process_expired_bookings(db: Session) -> int:
    """
    Find and expire all bookings whose payment deadline has passed.
    Uses SQL Server applock when available to prevent multiple workers
    from running the expiration batch simultaneously.
    Returns the number of bookings expired.
    """
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # Distributed concurrency lock on SQL Server
    lock_acquired = True
    if db.bind and db.bind.dialect.name == "mssql":
        try:
            lock_res = db.execute(
                text("""
                    DECLARE @res INT;
                    EXEC @res = sp_getapplock
                        @Resource = 'ceylongo_booking_expiration_worker',
                        @LockMode = 'Exclusive',
                        @LockOwner = 'Transaction',
                        @LockTimeout = 2000;
                    SELECT @res;
                """)
            ).scalar()
            if lock_res is None or lock_res < 0:
                lock_acquired = False
        except Exception as lock_err:
            logger.debug(f"Could not acquire worker app lock: {lock_err}")
            lock_acquired = True

    if not lock_acquired:
        logger.debug("Another worker is currently processing booking expirations. Skipping cycle.")
        return 0

    # Query eligible expired bookings
    eligible_bookings = (
        db.query(Booking)
        .filter(
            Booking.status == "awaiting_payment",
            Booking.payment_status != "paid",
            Booking.payment_expires_at.is_not(None),
            Booking.payment_expires_at <= now_utc,
        )
        .all()
    )

    expired_count = 0
    for booking in eligible_bookings:
        try:
            if expire_single_booking(db, booking, now_utc):
                expired_count += 1
        except Exception as e:
            logger.error(
                f"Error expiring booking #{booking.id}: {e}", exc_info=True
            )

    if expired_count > 0:
        db.commit()
        logger.info(f"Processed {expired_count} expired bookings successfully.")

    return expired_count


def _run_worker_cycle():
    """Runs a single expiration cycle with a dedicated session."""
    try:
        db = SessionLocal()
        try:
            process_expired_bookings(db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning(f"Booking expiration cycle encountered an error: {exc}")


async def _worker_loop(interval_seconds: int = 60):
    """Periodic background worker loop."""
    logger.info(f"Booking expiration worker started (interval: {interval_seconds}s)")
    while _stop_event is not None and not _stop_event.is_set():
        try:
            if _stop_event is not None:
                await asyncio.wait_for(_stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass

        if _stop_event is not None and _stop_event.is_set():
            break

        try:
            await asyncio.to_thread(_run_worker_cycle)
        except Exception as exc:
            logger.error(f"Error in booking expiration worker loop: {exc}")

    logger.info("Booking expiration worker loop stopped.")


def start_expiration_worker(interval_seconds: int = 60) -> Optional[asyncio.Task]:
    """Start the background expiration worker task."""
    import os
    import sys
    if "pytest" in sys.modules or os.getenv("TESTING") == "1" or os.getenv("DISABLE_EXPIRATION_WORKER") == "1":
        logger.info("Running under test environment; skipping background worker thread.")
        return None

    global _worker_task, _stop_event
    if _worker_task is not None and not _worker_task.done():
        logger.warning("Booking expiration worker is already running.")
        return _worker_task

    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(interval_seconds=interval_seconds))
    return _worker_task

    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_worker_loop(interval_seconds=interval_seconds))
    return _worker_task


async def stop_expiration_worker():
    """Gracefully stop the background expiration worker task."""
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()

    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    _stop_event = None
    logger.info("Booking expiration worker gracefully shut down.")
