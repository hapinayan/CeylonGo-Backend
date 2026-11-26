from datetime import UTC, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select, true

from app.core.rental_pricing import rental_days
from app.models.mvp import MVPAvailabilitySlot, MVPBooking, MVPBookingAllocation
from app.models.mvp import MVPInventoryUnit, MVPListing
from app.schemas.mvp_rental import RentalQuote
from app.services import mvp_activity, mvp_homestay
from app.services import mvp_listing_verification as verification

RENTAL_CATEGORIES = {"bike", "bicycle"}


def interval(pickup, returned):
    start, end = map(mvp_activity.slot_time, (pickup, returned))
    valid_duration = timedelta(0) < end - start <= timedelta(days=90)
    if start < verification.now() or not valid_duration:
        raise HTTPException(422, "Choose a future pickup and a rental of up to 90 days")
    return start, end


def unit_reservations(unit_id, start, end):
    return select(MVPBooking.id).join(MVPBookingAllocation).join(
        MVPAvailabilitySlot, MVPAvailabilitySlot.id == MVPBookingAllocation.slot_id,
    ).where(
        MVPAvailabilitySlot.unit_id == unit_id,
        MVPBookingAllocation.released_at.is_(None),
        MVPBooking.status.in_(("pending", "confirmed", "completed")),
        MVPBooking.starts_at < end, MVPBooking.ends_at > start,
    )


def available_units(listing_id, start, end, *outer_tables, ignore_reservations=False):
    unit = MVPInventoryUnit
    slot = MVPAvailabilitySlot
    covering = select(func.min(slot.id)).where(
        slot.unit_id == unit.id, slot.is_open == true(),
        slot.starts_at <= start, slot.ends_at >= end,
    ).correlate(unit).scalar_subquery()
    blocked = select(slot.id).where(
        slot.unit_id == unit.id, slot.is_open == False,
        slot.starts_at < end, slot.ends_at > start,
    ).correlate(unit)
    query = select(covering.label("slot_id")).select_from(unit).where(
        unit.listing_id == listing_id, unit.is_active == true(), unit.capacity == 1,
        covering.is_not(None), ~blocked.exists(),
    ).correlate(*outer_tables)
    if not ignore_reservations:
        reserved = unit_reservations(unit.id, start, end).correlate(unit)
        query = query.where(~reserved.exists())
    return query


def available_quantity(db, listing_id, start, end, *, ignore_reservations=False):
    units = available_units(
        listing_id, start, end, ignore_reservations=ignore_reservations,
    ).subquery()
    return db.scalar(select(func.count()).select_from(units))


def amount(listing, start, end, quantity):
    total = listing.price * rental_days(start, end) * quantity
    if not 0 < total <= Decimal("9999999999.99"):
        raise HTTPException(422, "Rental total is outside supported LKR amounts")
    return total


def quote(db, listing, start, end, quantity):
    count = available_quantity(db, listing.id, start, end)
    return RentalQuote(
        vehicle_id=listing.id, pickup_at=start.replace(tzinfo=UTC),
        return_at=end.replace(tzinfo=UTC), quantity=quantity,
        is_available=count >= quantity,
        available_quantity=count, duration_days=rental_days(start, end),
        duration_hours=round((end - start).total_seconds() / 3600, 2),
        rate=listing.price, total_price=amount(listing, start, end, quantity),
        requires_driving_license=listing.category == "bike",
    )


def booking_selection(db, listing, req):
    start, end = interval(req.starts_at, req.ends_at)
    if req.quantity > 20 or req.expected_total is None:
        raise HTTPException(
            422, "Rental bookings require expected_total and at most 20 vehicles",
        )
    if listing.category == "bike" and (
        not req.motorcycle_license_confirmed or req.license_valid_until is None
        or req.license_valid_until < mvp_homestay.local_date(end)
    ):
        raise HTTPException(
            422, "Confirm a motorcycle license valid through the return date",
        )
    total = amount(listing, start, end, req.quantity)
    if total != req.expected_total:
        raise HTTPException(409, "Rental price changed")
    ids = db.scalars(available_units(listing.id, start, end).order_by(
        MVPInventoryUnit.id
    ).limit(req.quantity)).all()
    if len(ids) < req.quantity:
        raise HTTPException(409, "Insufficient available vehicles for this interval")
    slots = db.scalars(select(MVPAvailabilitySlot).where(
        MVPAvailabilitySlot.id.in_(ids)
    )).all()
    return slots, total, start, end


def validate_calendar(db, unit, payload, start, end, slot=None):
    if unit.capacity != 1 or payload.capacity != 1 or end <= start:
        raise HTTPException(422, "A rental slot requires one vehicle and valid dates")
    if payload.is_open and not unit.is_active:
        raise HTTPException(422, "An inactive vehicle cannot be scheduled")
    if not payload.is_open and db.scalar(
        unit_reservations(unit.id, start, end).limit(1)
    ) is not None:
        raise HTTPException(409, "This block conflicts with a vehicle reservation")
    if slot and (
        start > slot.starts_at or end < slot.ends_at or not payload.is_open
    ) and db.scalar(select(MVPBookingAllocation.booking_id).where(
        MVPBookingAllocation.slot_id == slot.id,
        MVPBookingAllocation.released_at.is_(None),
    ).limit(1)) is not None:
        raise HTTPException(409, "This calendar window supports a reservation")


def validate_confirmation(booking):
    if len(booking.allocations) != booking.quantity or any(
        allocation.released_at is not None or not allocation.slot.is_open
        or not allocation.slot.unit.is_active for allocation in booking.allocations
    ):
        raise HTTPException(409, "The rental reservation cannot be confirmed")
