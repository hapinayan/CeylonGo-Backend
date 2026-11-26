import hashlib
import os
from datetime import UTC, datetime, time, timedelta, timezone
from decimal import Decimal
from urllib.parse import quote as url_quote, urlsplit

from fastapi import HTTPException
from sqlalchemy import select, true
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, undefer

from app.models.mvp import (
    MVPAvailabilitySlot, MVPBooking, MVPBookingAllocation, MVPBookingStatusHistory,
    MVPInventoryUnit, MVPListing, MVPProviderProfile, MVPUser,
)
from app.schemas.mvp import ListingResponse
from app.schemas.mvp_homestay import (
    HomestayBookingDetails, HomestayDetails, HomestayMetadata, HomestayQuote, StayDates,
)
from app.services import mvp_listing_verification as verification

SRI_LANKA = timezone(timedelta(hours=5, minutes=30))
MAX_NIGHTS = 30


def utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def local_date(value: datetime):
    return utc_naive(value).replace(tzinfo=UTC).astimezone(SRI_LANKA).date()


def stay_window(dates: StayDates) -> tuple[datetime, datetime]:
    start = datetime.combine(dates.check_in, time.min, SRI_LANKA)
    end = datetime.combine(dates.check_out, time.min, SRI_LANKA)
    return utc_naive(start), utc_naive(end)


def nights_for(start: datetime, end: datetime) -> int:
    start_day, end_day = local_date(start), local_date(end)
    nights = (end_day - start_day).days
    if (start_day < datetime.now(SRI_LANKA).date() or end <= start
            or not 1 <= nights <= MAX_NIGHTS):
        raise HTTPException(422, "Select a current or future stay of 1 to 30 nights")
    return nights


def public_query():
    return (
        select(MVPListing)
        .join(MVPProviderProfile, MVPListing.provider_id == MVPProviderProfile.id)
        .join(MVPUser, MVPProviderProfile.user_id == MVPUser.id)
        .where(
            MVPListing.category == "homestay", MVPListing.is_active == true(),
            MVPListing.approval_status == "approved",
            MVPProviderProfile.verification_status == "approved",
            MVPUser.role == "provider", MVPUser.is_active == true(),
        )
    )


def listing_details(query):
    return query.options(
        selectinload(MVPListing.provider), selectinload(MVPListing.media),
        selectinload(MVPListing.units), undefer(MVPListing.homestay_metadata),
    )


def get_public(db: Session, listing_id: int, *, lock=False, details=False) -> MVPListing:
    query = public_query().where(MVPListing.id == listing_id)
    if lock:
        query = query.with_hint(MVPListing, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
    if details:
        query = listing_details(query)
    listing = db.scalar(query)
    if listing is None:
        raise HTTPException(404, "Published homestay not found")
    return listing


def metadata(listing: MVPListing) -> HomestayMetadata:
    return (
        HomestayMetadata.model_validate_json(listing.homestay_metadata)
        if listing.homestay_metadata else HomestayMetadata()
    )


def serialize(listing: MVPListing, enabled: bool = True) -> HomestayDetails:
    data = ListingResponse.model_validate(listing).model_dump()
    meta = metadata(listing)
    visible_ids = {item.id for item in verification.visible_media(listing, enabled)}
    data["media"] = sorted(
        (item for item in data["media"] if item["id"] in visible_ids),
        key=lambda item: (item["id"] != meta.cover_photo_id, item["sort_order"]),
    )
    base = os.getenv("MVP_LISTING_MEDIA_BASE_URL", "").rstrip("/")
    for image in data["media"]:
        image["image_url"] = (
            f"/api/v1/listings/{listing.id}/photos/{image['id']}"
            if image["storage_key"].startswith(verification.PHOTO_PREFIX)
            else _image_url(image["storage_key"], base)
        )
    data["units"] = sorted(
        (verification.room_details(unit, enabled) for unit in listing.units if unit.is_active),
        key=lambda item: item.id,
    )
    if data["units"]:
        data["price"] = min(unit.nightly_price or listing.price for unit in data["units"])
    return HomestayDetails(
        **data, **meta.model_dump(),
        host={"id": listing.provider.id, "business_name": listing.provider.business_name},
    )


def _image_url(key: str, base: str) -> str | None:
    if _has_http_host(key):
        return key
    if _has_http_host(base) and not key.startswith("/") and not {"..", "."}.intersection(key.split("/")):
        return f"{base}/{url_quote(key)}"
    return None


def _has_http_host(value: str) -> bool:
    try:
        url = urlsplit(value)
        return url.scheme in {"http", "https"} and bool(url.hostname) and not url.username
    except ValueError:
        return False


def available_rooms(db: Session, listing_id: int, start: datetime, end: datetime,
                    guests: int, room_id: int | None = None,
                    exclude_booking_id: int | None = None) -> dict[int, tuple]:
    """Return each room and the slots that cover the complete requested stay."""
    rows = db.execute(
        select(MVPInventoryUnit, MVPAvailabilitySlot)
        .join(MVPAvailabilitySlot, MVPAvailabilitySlot.unit_id == MVPInventoryUnit.id)
        .where(
            MVPInventoryUnit.listing_id == listing_id, MVPInventoryUnit.is_active == true(),
            MVPInventoryUnit.capacity >= guests, MVPAvailabilitySlot.listing_id == listing_id,
            MVPAvailabilitySlot.starts_at < end, MVPAvailabilitySlot.ends_at > start,
            *([MVPInventoryUnit.id == room_id] if room_id is not None else []),
        )
        .order_by(MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.id)
    )
    rooms = {}
    for unit, slot in rows:
        rooms.setdefault(unit.id, (unit, []))[1].append(slot)
    if not rooms:
        return {}
    reservations = (
        select(MVPAvailabilitySlot.unit_id).distinct()
        .join(MVPBookingAllocation, MVPBookingAllocation.slot_id == MVPAvailabilitySlot.id)
        .join(MVPBooking, MVPBooking.id == MVPBookingAllocation.booking_id)
        .where(
            MVPAvailabilitySlot.listing_id == listing_id,
            MVPBookingAllocation.released_at.is_(None),
            MVPBooking.status.in_(("pending", "confirmed", "completed")),
            MVPBooking.starts_at < end, MVPBooking.ends_at > start,
        )
    )
    if exclude_booking_id is not None:
        reservations = reservations.where(MVPBooking.id != exclude_booking_id)
    booked = set(db.scalars(reservations))
    available = {}
    for unit_id, (unit, slots) in rooms.items():
        if unit_id in booked or any(not slot.is_open for slot in slots):
            continue
        until = start
        used = []
        for slot in slots:
            if slot.capacity < guests or slot.ends_at <= until:
                continue
            if slot.starts_at > until:
                break
            until = slot.ends_at
            used.append(slot)
            if until >= end:
                available[unit_id] = (unit, used)
                break
    return available


def quote(db: Session, listing: MVPListing, dates: StayDates, room_id=None) -> HomestayQuote:
    start, end = stay_window(dates)
    nights = nights_for(start, end)
    selected = db.get(MVPInventoryUnit, room_id) if room_id else None
    if room_id is not None and (
        not selected or selected.listing_id != listing.id or not selected.is_active
    ):
        raise HTTPException(404, "Room not found for this homestay")
    rooms = available_rooms(db, listing.id, start, end, dates.guests, room_id)
    enabled = verification.installed(db)
    available = [verification.room_details(rooms[key][0], enabled) for key in sorted(rooms)]
    for room in available:
        if room.nightly_price is None:
            room.nightly_price = listing.price
    nightly_rate = (
        (verification.room_details(selected, enabled).nightly_price or listing.price) if selected
        else min((room.nightly_price for room in available), default=listing.price)
    )
    return HomestayQuote(
        **dates.model_dump(), listing_id=listing.id, nights=nights,
        nightly_rate=nightly_rate, total_amount=nightly_rate * nights,
        available_rooms=available,
    )


def validate_confirmation(db: Session, booking: MVPBooking) -> None:
    listing = get_public(db, booking.listing_id, lock=True)
    rooms = {
        allocation.slot.unit_id for allocation in booking.allocations
        if allocation.released_at is None
    }
    if not rooms or not rooms.issubset(available_rooms(
        db, listing.id, booking.starts_at, booking.ends_at, booking.quantity,
        exclude_booking_id=booking.id,
    )):
        raise HTTPException(409, "The reserved room is no longer available")


def booking_details(booking: MVPBooking) -> HomestayBookingDetails:
    return HomestayBookingDetails(
        id=booking.id, listing_id=booking.listing_id, traveler_id=booking.traveler_id,
        request_key=booking.request_key,
        starts_at=utc_naive(booking.starts_at).replace(tzinfo=UTC),
        ends_at=utc_naive(booking.ends_at).replace(tzinfo=UTC),
        quantity=booking.quantity, total_amount=booking.total_amount, currency=booking.currency,
        status=booking.status, created_at=utc_naive(booking.created_at).replace(tzinfo=UTC),
        booking_reference=booking.booking_reference, slot_id=booking.slot_id,
        package_id=booking.package_id,
        rental_unit_ids=booking.rental_unit_ids,
        room_ids=sorted({item.slot.unit_id for item in booking.allocations}),
        payments=sorted(booking.payments, key=lambda item: item.attempt_no),
    )


def book(db: Session, traveler: MVPUser, listing_id: int, start: datetime, end: datetime,
         guests: int, request_key: str, room_id: int | None = None,
         expected_total: Decimal | None = None) -> MVPBooking:
    if traveler.role != "traveler":
        raise HTTPException(403, "Traveler authentication required")
    start, end = utc_naive(start), utc_naive(end)
    start = start.replace(microsecond=start.microsecond // 1000 * 1000)
    end = end.replace(microsecond=end.microsecond // 1000 * 1000)
    if not 1 <= guests <= 50:
        raise HTTPException(422, "Guest count must be between 1 and 50")
    listing = get_public(db, listing_id, lock=True)
    existing = db.scalar(select(MVPBooking).where(
        MVPBooking.traveler_id == traveler.id, MVPBooking.request_key == request_key
    ))
    if existing:
        if (
            existing.listing_id != listing.id or existing.starts_at != start
            or existing.ends_at != end or existing.quantity != guests
            or (room_id is not None and room_id not in booking_details(existing).room_ids)
            or (expected_total is not None and expected_total != existing.total_amount)
        ):
            raise HTTPException(409, "Request key is already used for another booking")
        return existing
    nights = nights_for(start, end)
    if room_id is not None and not db.scalar(select(MVPInventoryUnit.id).where(
        MVPInventoryUnit.id == room_id, MVPInventoryUnit.listing_id == listing.id
    )):
        raise HTTPException(404, "Room not found for this homestay")
    rooms = available_rooms(db, listing.id, start, end, guests, room_id)
    if not rooms:
        raise HTTPException(409, "No room is available for the full stay and guest count")
    enabled = verification.installed(db)
    def rate(unit):
        return verification.room_details(unit, enabled).nightly_price or listing.price
    room, slots = min(rooms.values(), key=lambda pair: (rate(pair[0]), pair[0].id))
    total = rate(room) * nights
    if total > Decimal("9999999999.99"):
        raise HTTPException(422, "Stay total exceeds the booking amount limit")
    if expected_total is not None and expected_total != total:
        raise HTTPException(409, "Price changed. Request a new quote.")
    now = datetime.now(UTC).replace(tzinfo=None)
    booking = MVPBooking(
        listing_id=listing.id, traveler_id=traveler.id, request_key=request_key,
        request_hash=hashlib.sha256(f"{traveler.id}:{request_key}".encode()).digest(),
        starts_at=start, ends_at=end, quantity=guests, total_amount=total, currency="LKR",
        status="pending", created_at=now, updated_at=now,
    )
    booking.allocations = [
        MVPBookingAllocation(slot_id=slot.id, listing_id=listing.id, quantity=guests,
                             reserved_at=now)
        for slot in slots
    ]
    booking.status_history = [MVPBookingStatusHistory(
        actor_user_id=traveler.id, to_status="pending",
        reason="Initial homestay booking request created", occurred_at=now,
    )]
    db.add(booking)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Booking conflicts with an existing request") from None
    return booking
