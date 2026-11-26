from datetime import UTC, timedelta
from decimal import Decimal
import hashlib
import hmac
from uuid import uuid4

import jwt
from fastapi import HTTPException
from sqlalchemy import func, or_, select, true

from app.core.security import get_jwt_secret_key
from app.models.mvp import MVPAvailabilitySlot, MVPBooking, MVPListing
from app.routers.mvp.activities import published_listings
from app.schemas.mvp import BookingCreateRequest, BookingDetailResponse
from app.schemas.mvp_homestay import StayDates
from app.schemas.mvp_planner import (
    BookingStatus, Confirm, Proposal, Quote, Search, Selection, ServiceDetails, ServiceOption,
)
from app.services import mvp_activity, mvp_guide, mvp_homestay, mvp_rental
from app.services import mvp_listing_verification as verification
from app.services.mvp_services import MVPBookingService

PUBLIC = published_listings("homestay", "adventure", "guide", "bike", "bicycle", "artisan")
REGIONS = {
    "northern": ("Jaffna", "Mannar", "Kilinochchi", "Mullaitivu", "Vavuniya"),
    "eastern": ("Trincomalee", "Batticaloa", "Ampara"),
}
AUDIENCE = "ceylongo-planner-booking"


def listing(db, listing_id):
    row = db.scalar(PUBLIC.where(MVPListing.id == listing_id))
    if row is None:
        raise HTTPException(404, "Published listing not found")
    return row


def search(db, request: Search):
    term = request.destination.casefold()
    region = term.removesuffix(" sri lanka").removesuffix(" province")
    conditions = [
        func.lower(column).contains(term, autoescape=True)
        for column in (MVPListing.district, MVPListing.locality, MVPListing.title)
    ]
    if region in REGIONS:
        conditions.append(MVPListing.district.in_(REGIONS[region]))
    return db.scalars(PUBLIC.where(
        MVPListing.category == request.category, or_(*conditions),
    ).order_by(MVPListing.price, MVPListing.id).limit(6)).all()


def details(db, row):
    if row.category == "homestay":
        options = [
            ServiceOption(id=unit.id, title=unit.label, capacity=unit.capacity,
                          advertised_price_lkr=verification.room_details(unit).nightly_price
                          or row.price, pricing_type="per_night")
            for unit in sorted(row.units, key=lambda item: item.id)[:12] if unit.is_active
        ]
        return ServiceDetails(amenities=mvp_homestay.metadata(row).amenities, options=options)
    if row.category == "guide":
        options = [
            ServiceOption(id=item.id, title=item.title, capacity=item.max_people,
                          advertised_price_lkr=item.price, pricing_type=item.pricing_type)
            for unit in sorted(row.units, key=lambda value: value.id)[:12]
            if unit.is_active and (item := verification.guide_package(unit)) is not None
        ]
        return ServiceDetails(options=options)
    if row.category == "adventure":
        config = mvp_activity.metadata(db, row)
        return ServiceDetails(duration_minutes=config.duration_minutes if config else None)
    if row.category == "artisan":
        data = verification.details(db, row)
        return ServiceDetails(craft_category=data.get("artisan", {}).get("craft_category"))
    return ServiceDetails()


def status(db, traveler_id, booking_id):
    booking = db.scalar(select(MVPBooking).where(
        MVPBooking.id == booking_id, MVPBooking.traveler_id == traveler_id,
    ))
    if booking is None:
        raise HTTPException(404, "Booking not found")
    latest = max(booking.payments, key=lambda payment: payment.id, default=None)
    return BookingStatus(booking_id=booking.id, status=booking.status,
                         total_lkr=booking.total_amount,
                         payment_status=latest.status if latest else None,
                         payment_method=latest.method if latest else None)


def quote(db, selection: Selection):
    row = listing(db, selection.listing_id)
    count = selection.quantity
    start, end = selection.starts_at, selection.ends_at
    room_id = slot_id = package_id = None
    if row.category == "artisan":
        raise HTTPException(422, "Artisans are discovery and direct-contact services")
    if row.category == "homestay":
        if start is None:
            raise HTTPException(422, "Supply check-in and check-out dates")
        stay = StayDates(check_in=mvp_homestay.local_date(start),
                         check_out=mvp_homestay.local_date(end), guests=count)
        start, end = mvp_homestay.stay_window(stay)
        result = mvp_homestay.quote(db, row, stay, selection.room_id)
        if not result.available_rooms:
            raise HTTPException(409, "No room is available for this stay")
        room = min(result.available_rooms, key=lambda item: (item.nightly_price, item.id))
        room_id, total = room.id, room.nightly_price * result.nights
    elif row.category in {"bike", "bicycle"}:
        if start is None or count > 20:
            raise HTTPException(422, "Supply pickup and return times and up to 20 vehicles")
        start, end = mvp_rental.interval(start, end)
        result = mvp_rental.quote(db, row, start, end, count)
        if not result.is_available:
            raise HTTPException(409, "Insufficient rentals for this interval")
        total = result.total_price
    else:
        slot, total, package_id = _scheduled_quote(db, row, selection)
        start, end, slot_id = slot.starts_at, slot.ends_at, slot.id
    if total is None or not Decimal(0) < total <= Decimal("9999999999"):
        raise HTTPException(422, "Unsupported booking total")
    return Quote(
        listing_id=row.id, title=row.title, category=row.category, quantity=count,
        starts_at=start.replace(tzinfo=UTC), ends_at=end.replace(tzinfo=UTC),
        total_lkr=total, room_id=room_id, slot_id=slot_id, package_id=package_id,
        motorcycle_license_required=row.category == "bike",
    )


def _scheduled_quote(db, row, selection):
    activity = row.category == "adventure"
    if activity:
        mvp_activity.validate_count(mvp_activity.metadata(db, row), selection.quantity)
        query = mvp_activity.slots().where(MVPAvailabilitySlot.listing_id == row.id)
    else:
        query = mvp_guide.available_slots(row, selection.package_id)
    if selection.slot_id is not None:
        query = query.where(MVPAvailabilitySlot.id == selection.slot_id)
    if selection.starts_at is not None:
        query = query.where(
            MVPAvailabilitySlot.starts_at == mvp_activity.slot_time(selection.starts_at),
            MVPAvailabilitySlot.ends_at == mvp_activity.slot_time(selection.ends_at),
        )
    elif selection.day is not None and selection.day < selection.day.max:
        dates = StayDates(check_in=selection.day, check_out=selection.day + timedelta(days=1))
        start, end = mvp_homestay.stay_window(dates)
        query = query.where(MVPAvailabilitySlot.starts_at >= start,
                            MVPAvailabilitySlot.starts_at < end)
    elif selection.slot_id is None:
        raise HTTPException(422, "Supply a local activity date or a slot ID")
    for slot, capacity_or_busy in db.execute(query.where(
        MVPAvailabilitySlot.is_open == true(),
    ).order_by(MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.id).limit(24)):
        if activity:
            result = mvp_activity.quote(row, slot, capacity_or_busy, selection.quantity)
            if result.available:
                return slot, result.total_amount, None
        elif (package := verification.guide_package(slot.unit)) is not None:
            result = mvp_guide.quote(slot, package, capacity_or_busy, selection.quantity)
            if result.available and selection.quantity <= package.max_people:
                return slot, result.total_amount, package.id
    raise HTTPException(409, "No scheduled slot is available for this selection")


def _signing_key():
    return hmac.digest(get_jwt_secret_key().encode(), AUDIENCE.encode(), hashlib.sha256)


def propose(traveler, summary: Quote):
    expires = verification.now().replace(tzinfo=UTC) + timedelta(minutes=10)
    token = jwt.encode({
        "aud": AUDIENCE, "sub": str(traveler.id), "exp": expires,
        "jti": str(uuid4()), "auth_version": traveler.auth_version,
        "credential_version": traveler.credential_version,
        "quote": summary.model_dump(mode="json"),
    }, _signing_key(), algorithm="HS256")
    return Proposal(quote=summary, confirmation_token=token, expires_at=expires)


def confirm(db, traveler, request: Confirm):
    try:
        token = jwt.decode(request.confirmation_token, _signing_key(),
                           algorithms=["HS256"], audience=AUDIENCE,
                           options={"require": ["sub", "jti", "exp", "aud"]})
    except jwt.InvalidTokenError:
        raise HTTPException(409, "Invalid or expired quote. Request a new quote.") from None
    if (token["sub"] != str(traveler.id) or token.get("auth_version") != traveler.auth_version
            or token.get("credential_version") != traveler.credential_version):
        raise HTTPException(403, "Confirmation does not belong to this traveler session")
    summary = Quote.model_validate(token["quote"])
    lookup = select(MVPBooking).where(
        MVPBooking.traveler_id == traveler.id, MVPBooking.request_key == token["jti"],
    )
    existing = db.scalar(lookup)
    if existing is not None:
        return BookingDetailResponse.model_validate(existing)
    try:
        if summary.category == "homestay":
            result = mvp_homestay.book(
                db, traveler, summary.listing_id, summary.starts_at, summary.ends_at,
                summary.quantity, token["jti"], summary.room_id, summary.total_lkr,
            )
        else:
            result = MVPBookingService.create_booking(db, traveler, BookingCreateRequest(
                listing_id=summary.listing_id, starts_at=summary.starts_at,
                ends_at=summary.ends_at, slot_id=summary.slot_id, quantity=summary.quantity,
                expected_total=summary.total_lkr, request_key=token["jti"],
                motorcycle_license_confirmed=request.motorcycle_license_confirmed,
                license_valid_until=request.license_valid_until,
            ))
    except HTTPException as error:
        if error.status_code != 409:
            raise
        db.rollback()
        result = db.scalar(lookup)
        if result is None:
            raise
    return BookingDetailResponse.model_validate(result)
