"""
Artisan Product Reservation Auto-Expiry Service (Phase P1 Step 7).
Provides safe, idempotent batch expiration of past-due reservations
and atomic stock restoration back to showcase product inventory.

CONCURRENCY & SAFETY ARCHITECTURAL GUARANTEES:
1. Uses SQL Server application locks (sp_getapplock) on showcase products during stock restoration.
2. Idempotent: Never double-restores stock if an expired reservation is processed multiple times.
3. Terminal state preservation: Completed and cancelled reservations are never modified or expired.
4. Consistent UTC comparisons: Handles both timezone-aware and naive UTC datetime objects.
5. Isolation: Each reservation can be committed in an isolated unit of work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from typing import Sequence

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.booking_transactions import CAPTURED
from app.core.notification_service import create_notification
from app.models.artisan import ArtisanReservation
from app.models.shared import BookingReference, PaymentTransaction
from app.services.artisan_shared_reference import (
    get_artisan_booking_reference,
    sync_artisan_booking_reference,
)

from app.services.provider_reservations import (
    ARTISAN,
    ARTISAN_ACTIVE_STATES,
    LOCAL,
    artisan_capture_exists,
    artisan_reservation_expiry,
    lock_artisan_product,
    restore_artisan_stock,
)

logger = logging.getLogger("artisan_reservation_expiry")

# Allowed active statuses eligible for auto-expiry if expires_at has passed.
# Completed, cancelled, expired, and rejected reservations are terminal.
DEFAULT_ELIGIBLE_STATUSES = ARTISAN_ACTIVE_STATES


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to naive UTC for safe cross-database comparisons."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@dataclass
class ExpiredReservationDetail:
    """Details of a single reservation that was successfully expired and had stock restored."""
    reservation_id: int
    showcase_item_id: int
    artisan_listing_id: int
    restored_quantity: int
    previous_status: str
    new_status: str = "expired"
    expires_at: datetime | None = None


@dataclass
class ArtisanReservationExpiryResult:
    """Summary of batch reservation expiry processing."""
    scanned_count: int = 0
    expired_count: int = 0
    skipped_count: int = 0
    restored_stock_total: int = 0
    expired_reservations: list[ExpiredReservationDetail] = field(default_factory=list)


def expire_single_reservation(
    db: Session,
    reservation_id: int,
    eligible_statuses: Sequence[str] = DEFAULT_ELIGIBLE_STATUSES,
    now: datetime | None = None,
) -> tuple[bool, str, int]:
    """
    Safely and idempotently expire a single past-due reservation.

    Guarantees:
    - Verifies expires_at has passed relative to UTC now.
    - Locks the product inventory via lock_artisan_product.
    - Restores reserved quantity to showcase_items.stock_quantity exactly once.
    - Transitions reservation_status to 'expired'.
    - Terminal states ('completed', 'cancelled', 'expired', 'rejected') are strictly skipped without stock modification.

    Returns:
        (success: bool, message: str, restored_quantity: int)
    """
    curr_now = _to_naive_utc(now) if now else datetime.now(timezone.utc).replace(tzinfo=None)

    reservation = db.query(ArtisanReservation).filter(ArtisanReservation.id == reservation_id).first()
    if not reservation:
        return False, "Reservation not found", 0

    # 1. Terminal state check: completed reservations must never be altered
    if reservation.reservation_status == "completed":
        return False, "Completed reservations cannot be expired", 0

    # 2. Terminal state check: cancelled reservations already had stock restored
    if reservation.reservation_status == "cancelled":
        return False, "Cancelled reservations cannot be expired; stock already restored", 0

    # 3. Duplicate check: already expired reservations must not double-restore stock
    if reservation.reservation_status == "expired":
        return False, "Reservation is already expired; duplicate stock restoration prevented", 0

    if reservation.reservation_status == "rejected":
        return False, "Rejected reservations cannot be expired", 0

    # 4. Eligible status check
    if reservation.reservation_status not in eligible_statuses:
        return False, f"Reservation status '{reservation.reservation_status}' is not eligible for auto-expiry", 0

    expiry = artisan_reservation_expiry(reservation)
    if expiry is None:
        return False, "Reservation has no expires_at timestamp or pickup date", 0
    if expiry > curr_now:
        return False, "Reservation is not yet past due", 0

    lock_artisan_product(db, reservation.showcase_item_id)
    db.refresh(reservation)
    prev_status = reservation.reservation_status
    expiry = artisan_reservation_expiry(reservation)
    if (
        prev_status not in ARTISAN_ACTIVE_STATES
        or prev_status not in eligible_statuses
        or expiry is None
        or expiry > curr_now
    ):
        db.rollback()
        return False, "Reservation is no longer eligible for expiry", 0

    ref = get_artisan_booking_reference(db, reservation.id)
    if ref and (
        ref.payment_status != "unpaid"
        or artisan_capture_exists(db, ref.id)
        or ref.booking_status != prev_status
        or ref.user_id != reservation.traveler_id
        or ref.provider_profile_id != reservation.artisan_listing.provider_profile_id
    ):
        db.rollback()
        return False, "Reservation payment or booking state prevents expiry", 0

    try:
        changed = db.execute(update(ArtisanReservation).where(
            ArtisanReservation.id == reservation.id,
            ArtisanReservation.reservation_status == prev_status,
        ).values(
            reservation_status="expired",
            updated_at=func.sysutcdatetime(),
        ).execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            db.rollback()
            return False, "Reservation status changed during expiry", 0
        restore_artisan_stock(db, reservation)
        restored_qty = reservation.quantity
        db.refresh(reservation)

        ref = sync_artisan_booking_reference(
            db,
            reservation,
            changed_by=None,
            change_reason=f"Auto-expired from '{prev_status}' past pickup deadline",
        )

        create_notification(
            db, reservation.traveler_id, f"Reservation {ref.booking_reference} expired",
            "Your Artisan pickup reservation expired.", "artisan_reservation_expired",
        )
        db.commit()
        db.refresh(reservation)

    except Exception:
        db.rollback()
        raise

    return True, f"Successfully expired reservation {reservation.id} from '{prev_status}' and restored {restored_qty} stock", restored_qty


def due_artisan_reservations(now):
    """Select persisted deadlines and earlier pickup-day cutoffs, including older rows."""
    local_today = now.replace(tzinfo=timezone.utc).astimezone(LOCAL).date()
    payment_prevents_expiry = select(1).where(
        BookingReference.module_type == ARTISAN,
        BookingReference.target_booking_id == ArtisanReservation.id,
        or_(
            BookingReference.payment_status != "unpaid",
            select(1).where(
                PaymentTransaction.booking_reference_id == BookingReference.id,
                PaymentTransaction.payment_status.in_((*CAPTURED, "refunded")),
            ).exists(),
        ),
    ).exists()
    return (
        or_(ArtisanReservation.expires_at <= now, ArtisanReservation.pickup_date < local_today)
        & ~payment_prevents_expiry
    )


def process_expired_reservations(
    db: Session,
    batch_size: int = 100,
    eligible_statuses: Sequence[str] = DEFAULT_ELIGIBLE_STATUSES,
    now: datetime | None = None,
) -> ArtisanReservationExpiryResult:
    """
    Scan the database for past-due active reservations and process them in batches.

    Parameters:
        db: SQLAlchemy database session.
        batch_size: Maximum number of candidate reservations to process per invocation.
        eligible_statuses: Tuple of status strings eligible for expiry (defaults to pending, confirmed, ready_for_pickup).
        now: Explicit reference timestamp (defaults to UTC now).

    Returns:
        ArtisanReservationExpiryResult containing counters and list of modified reservations.
    """
    curr_now = _to_naive_utc(now) if now else datetime.now(timezone.utc).replace(tzinfo=None)
    result = ArtisanReservationExpiryResult()

    # Find candidate reservations ordered by expires_at ascending (oldest first)
    candidates = (
        db.query(ArtisanReservation.id)
        .filter(
            ArtisanReservation.reservation_status.in_(eligible_statuses),
            due_artisan_reservations(curr_now),
        )
        .order_by(ArtisanReservation.expires_at.asc())
        .limit(batch_size)
        .all()
    )

    result.scanned_count = len(candidates)

    for (res_id,) in candidates:
        try:
            # Query metadata before transition for result logging
            res_record = db.query(ArtisanReservation).filter(ArtisanReservation.id == res_id).first()
            if not res_record:
                result.skipped_count += 1
                continue

            prev_status = res_record.reservation_status
            item_id = res_record.showcase_item_id
            listing_id = res_record.artisan_listing_id
            exp_time = artisan_reservation_expiry(res_record)

            success, reason, restored = expire_single_reservation(
                db=db,
                reservation_id=res_id,
                eligible_statuses=eligible_statuses,
                now=curr_now,
            )

            if success:
                result.expired_count += 1
                result.restored_stock_total += restored
                result.expired_reservations.append(
                    ExpiredReservationDetail(
                        reservation_id=res_id,
                        showcase_item_id=item_id,
                        artisan_listing_id=listing_id,
                        restored_quantity=restored,
                        previous_status=prev_status,
                        new_status="expired",
                        expires_at=exp_time,
                    )
                )
            else:
                result.skipped_count += 1
                logger.debug(f"Reservation {res_id} skipped: {reason}")

        except Exception as e:
            db.rollback()
            result.skipped_count += 1
            logger.error(f"Error expiring reservation {res_id}: {e}", exc_info=True)

    return result
