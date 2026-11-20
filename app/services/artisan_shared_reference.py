"""
Artisan Shared Infrastructure Integration Service (Phase P1 Step 8).
Provides atomic synchronization and idempotent lifecycle management for:
- Shared Listing References (dbo.listing_references)
- Shared Booking References (dbo.booking_references)
- Shared Booking Status History (dbo.booking_status_history)

CONTRACT SPECIFICATION:
1. Listing References:
   - module_type: 'artisan' (enforced by CK_listing_references_type)
   - target_id: artisan_listings.id
   - provider_profile_id: artisan_listings.provider_profile_id
   - is_active: True ONLY when (artisan_listing.is_active AND approval_status == 'approved')
   - Idempotent: UQ_listing_references (module_type, target_id) prevents duplicates.

2. Booking References:
   - module_type: 'artisan_reservation' (enforced by CK_booking_references_type)
   - target_id: artisan_reservations.id
   - user_id: artisan_reservations.traveler_id
   - provider_profile_id: artisan_listings.provider_profile_id
   - total_amount: artisan_reservations.total_price
   - booking_status: synchronized with reservation_status ('pending', 'confirmed', 'ready_for_pickup', 'completed', 'cancelled', 'expired')
   - payment_status: 'paid' when reservation_status == 'completed' (in-store payment), else 'unpaid'
   - Idempotent: UQ_booking_references_target (module_type, target_id) prevents duplicates.
   - Status history: Every status change logs an immutable entry to dbo.booking_status_history.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    ListingReference,
)

logger = logging.getLogger("artisan_shared_reference")

ARTISAN_MODULE_TYPE: str = "artisan"
ARTISAN_RESERVATION_MODULE_TYPE: str = "artisan_reservation"


def sync_artisan_listing_reference(
    db: Session,
    listing: ArtisanListing,
) -> ListingReference:
    """
    Atomically creates or updates the centralized ListingReference for an ArtisanListing.

    Rules:
    - module_type is strictly 'artisan'.
    - is_active is True only when listing.is_active is True AND listing.approval_status == 'approved'.
    - If unapproved (pending, rejected, suspended) or deactivated, is_active is set to False.
    - Idempotent: updates existing reference without creating duplicate records.
    """
    is_active = bool(listing.is_active and listing.approval_status == "approved")

    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing.id,
        )
        .first()
    )

    if ref:
        ref.provider_profile_id = listing.provider_profile_id
        ref.title = listing.title
        ref.district = listing.district
        ref.city = listing.city
        ref.latitude = listing.latitude
        ref.longitude = listing.longitude
        ref.is_active = is_active
        logger.debug(
            "Updated existing ListingReference id=%s for artisan listing id=%s (is_active=%s)",
            ref.id,
            listing.id,
            is_active,
        )
    else:
        ref = ListingReference(
            module_type=ARTISAN_MODULE_TYPE,
            target_id=listing.id,
            provider_profile_id=listing.provider_profile_id,
            title=listing.title,
            district=listing.district,
            city=listing.city,
            latitude=listing.latitude,
            longitude=listing.longitude,
            is_active=is_active,
        )
        db.add(ref)
        logger.debug(
            "Created new ListingReference for artisan listing id=%s (is_active=%s)",
            listing.id,
            is_active,
        )

    db.flush()
    return ref


def sync_artisan_booking_reference(
    db: Session,
    reservation: ArtisanReservation,
    changed_by: int | None = None,
    change_reason: str | None = None,
) -> BookingReference:
    """
    Atomically creates or updates the centralized BookingReference for an ArtisanReservation.

    Rules:
    - module_type is strictly 'artisan_reservation'.
    - booking_status is kept in 1:1 parity with reservation.reservation_status.
    - payment_status is 'paid' when reservation_status == 'completed' (in-store pickup settled),
      and 'unpaid' for 'pending', 'confirmed', 'ready_for_pickup', 'cancelled', 'expired'.
    - Logs state transitions to dbo.booking_status_history.
    - Idempotent: never generates duplicate booking references for the same reservation.
    """
    # 1. Resolve provider_profile_id safely
    provider_profile_id = 0
    if reservation.artisan_listing and reservation.artisan_listing.provider_profile_id:
        provider_profile_id = reservation.artisan_listing.provider_profile_id
    elif reservation.artisan_listing_id:
        listing = (
            db.query(ArtisanListing)
            .filter(ArtisanListing.id == reservation.artisan_listing_id)
            .first()
        )
        if listing:
            provider_profile_id = listing.provider_profile_id

    # 2. Determine payment status based on reservation completion
    payment_status = "paid" if reservation.reservation_status == "completed" else "unpaid"

    # 3. Lookup existing BookingReference
    ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == ARTISAN_RESERVATION_MODULE_TYPE,
            BookingReference.target_booking_id == reservation.id,
        )
        .first()
    )

    if ref:
        old_status = ref.booking_status
        new_status = reservation.reservation_status

        # If status changed, record status transition in BookingStatusHistory
        if old_status != new_status:
            history = BookingStatusHistory(
                booking_reference_id=ref.id,
                from_status=old_status,
                to_status=new_status,
                changed_by=changed_by,
                notes=change_reason or f"Transition from {old_status} to {new_status}",
            )
            db.add(history)

        ref.booking_status = new_status
        ref.total_price = reservation.total_price
        ref.payment_status = payment_status
        if provider_profile_id:
            ref.provider_profile_id = provider_profile_id

        logger.debug(
            "Updated BookingReference id=%s for reservation id=%s (status=%s, payment=%s)",
            ref.id,
            reservation.id,
            new_status,
            payment_status,
        )
    else:
        # Generate unique code formatted: ART-RES-<id:06d>-<hex:6>
        ref_code = f"ART-RES-{reservation.id:06d}-{uuid.uuid4().hex[:6].upper()}"

        ref = BookingReference(
            booking_reference=ref_code,
            module_type=ARTISAN_RESERVATION_MODULE_TYPE,
            target_booking_id=reservation.id,
            user_id=reservation.traveler_id,
            provider_profile_id=provider_profile_id,
            total_price=reservation.total_price,
            currency=reservation.currency or "LKR",
            booking_status=reservation.reservation_status,
            payment_status=payment_status,
        )
        db.add(ref)
        db.flush()

        # Log initial creation in BookingStatusHistory
        history = BookingStatusHistory(
            booking_reference_id=ref.id,
            from_status=None,
            to_status=reservation.reservation_status,
            changed_by=changed_by,
            notes=change_reason or "Artisan reservation created",
        )
        db.add(history)

        logger.debug(
            "Created BookingReference id=%s (code=%s) for reservation id=%s (status=%s)",
            ref.id,
            ref_code,
            reservation.id,
            reservation.reservation_status,
        )

    db.flush()
    return ref


def get_artisan_listing_reference(
    db: Session,
    listing_id: int,
) -> ListingReference | None:
    """Retrieve the shared ListingReference for a given artisan listing ID."""
    return (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == ARTISAN_MODULE_TYPE,
            ListingReference.target_id == listing_id,
        )
        .first()
    )


def get_artisan_booking_reference(
    db: Session,
    reservation_id: int,
) -> BookingReference | None:
    """Retrieve the shared BookingReference for a given artisan reservation ID."""
    return (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == ARTISAN_RESERVATION_MODULE_TYPE,
            BookingReference.target_booking_id == reservation_id,
        )
        .first()
    )

