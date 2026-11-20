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

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artisan import ArtisanReservation
from app.models.showcase_item import ShowcaseItem
from app.routers.artisan_reservations import acquire_product_lock
from app.services.artisan_shared_reference import sync_artisan_booking_reference

logger = logging.getLogger("artisan_reservation_expiry")

# Allowed active statuses eligible for auto-expiry if expires_at has passed.
# 'completed', 'cancelled', and 'expired' are terminal states and strictly excluded.
DEFAULT_ELIGIBLE_STATUSES: tuple[str, ...] = ("pending", "confirmed", "ready_for_pickup")


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
    - Locks the product inventory via acquire_product_lock.
    - Restores reserved quantity to showcase_items.stock_quantity exactly once.
    - Transitions reservation_status to 'expired'.
    - Terminal states ('completed', 'cancelled', 'expired') are strictly skipped without stock modification.

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

    # 4. Eligible status check
    if reservation.reservation_status not in eligible_statuses:
        return False, f"Reservation status '{reservation.reservation_status}' is not eligible for auto-expiry", 0

    # 5. Expiration timestamp check
    if reservation.expires_at is None:
        return False, "Reservation has no expires_at timestamp; cannot auto-expire", 0

    exp_time = _to_naive_utc(reservation.expires_at)
    if exp_time > curr_now:
        return False, f"Reservation is not yet past due (expires_at={exp_time}, now={curr_now})", 0

    # 6. Acquire product stock concurrency lock
    acquire_product_lock(db, reservation.showcase_item_id)

    # 7. Re-check reservation state after acquiring lock (guards against race conditions)
    db.refresh(reservation)
    if reservation.reservation_status in ("completed", "cancelled", "expired"):
        return False, f"Reservation status changed to '{reservation.reservation_status}' during lock acquisition", 0

    if reservation.reservation_status not in eligible_statuses:
        return False, f"Reservation status changed to '{reservation.reservation_status}'; skipping", 0

    # 8. Restore allocated stock back to showcase product inventory
    item = db.query(ShowcaseItem).filter(ShowcaseItem.id == reservation.showcase_item_id).first()
    restored_qty = reservation.quantity
    if item:
        item.stock_quantity += restored_qty
        item.updated_at = func.sysutcdatetime()

    # 9. Transition reservation to 'expired'
    prev_status = reservation.reservation_status
    reservation.reservation_status = "expired"
    reservation.updated_at = func.sysutcdatetime()

    sync_artisan_booking_reference(
        db,
        reservation,
        changed_by=None,
        change_reason=f"Auto-expired from '{prev_status}' past pickup deadline",
    )

    db.commit()
    db.refresh(reservation)

    return True, f"Successfully expired reservation {reservation.id} from '{prev_status}' and restored {restored_qty} stock", restored_qty


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
            ArtisanReservation.expires_at.is_not(None),
            ArtisanReservation.expires_at <= curr_now,
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
            exp_time = res_record.expires_at

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

