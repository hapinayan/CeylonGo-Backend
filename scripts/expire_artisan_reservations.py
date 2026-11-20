"""
CeylonGo - Artisan Reservation Auto-Expiry CLI Task Runner
===========================================================
Safe standalone runner for expiring past-due artisan product reservations
and restoring allocated stock back to showcase items.

Usage:
    python scripts/expire_artisan_reservations.py [--batch-size 100] [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Set up project root in sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.database.connection import SessionLocal
from app.services.artisan_reservation_expiry import (
    DEFAULT_ELIGIBLE_STATUSES,
    _to_naive_utc,
    process_expired_reservations,
)
from app.models.artisan import ArtisanReservation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("expire_artisan_reservations")


def main() -> int:
    parser = argparse.ArgumentParser(description="Expire past-due artisan product reservations")
    parser.add_argument("--batch-size", type=int, default=100, help="Max reservations to process per run (default: 100)")
    parser.add_argument("--dry-run", action="store_true", help="Scan only without mutating database records")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    logger.info(f"Starting Artisan Reservation Expiry Task at {now.isoformat()} (dry_run={args.dry_run}, batch_size={args.batch_size})")

    db = SessionLocal()
    try:
        if args.dry_run:
            curr_now = _to_naive_utc(now)
            count = (
                db.query(ArtisanReservation.id)
                .filter(
                    ArtisanReservation.reservation_status.in_(DEFAULT_ELIGIBLE_STATUSES),
                    ArtisanReservation.expires_at.is_not(None),
                    ArtisanReservation.expires_at <= curr_now,
                )
                .count()
            )
            logger.info(f"[DRY RUN] Found {count} candidate past-due reservations eligible for expiry.")
            return 0

        result = process_expired_reservations(
            db=db,
            batch_size=args.batch_size,
            eligible_statuses=DEFAULT_ELIGIBLE_STATUSES,
            now=now,
        )

        logger.info(
            f"Expiry Run Complete: Scanned={result.scanned_count}, Expired={result.expired_count}, "
            f"Skipped={result.skipped_count}, RestoredStockTotal={result.restored_stock_total}"
        )
        for detail in result.expired_reservations:
            logger.info(
                f"  - Reservation #{detail.reservation_id} (Item #{detail.showcase_item_id}): "
                f"Status {detail.previous_status} -> {detail.new_status}, Restored Qty={detail.restored_quantity}"
            )
        return 0

    except Exception as e:
        logger.error(f"Fatal error during reservation expiry run: {e}", exc_info=True)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

