from datetime import UTC
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select, true

from app.models.mvp import MVPAvailabilitySlot, MVPBookingAllocation, MVPInventoryUnit
from app.schemas.mvp_activity import ActivityMetadata, ActivitySlotDetails
from app.services import mvp_homestay, mvp_listing_verification as verification


def metadata(db, listing):
    value = verification.details(db, listing).get("activity")
    return ActivityMetadata.model_validate(value) if value is not None else None


def remaining_capacity():
    allocated = select(func.coalesce(func.sum(MVPBookingAllocation.quantity), 0)).where(
        MVPBookingAllocation.slot_id == MVPAvailabilitySlot.id,
        MVPBookingAllocation.released_at.is_(None),
    ).correlate(MVPAvailabilitySlot).scalar_subquery()
    return MVPAvailabilitySlot.capacity - allocated


def slots():
    return select(MVPAvailabilitySlot, remaining_capacity().label("remaining")).join(
        MVPInventoryUnit, MVPInventoryUnit.id == MVPAvailabilitySlot.unit_id,
    ).where(
        MVPInventoryUnit.is_active == true(),
        MVPInventoryUnit.listing_id == MVPAvailabilitySlot.listing_id,
        MVPAvailabilitySlot.starts_at > verification.now(),
    )


def validate_count(config, quantity):
    if config is None:
        raise HTTPException(409, "Activity booking configuration is incomplete")
    if not config.min_participants <= quantity <= config.max_participants:
        raise HTTPException(422, "Participant count is outside the activity limits")


def total(listing, quantity):
    amount = listing.price * quantity
    if not 0 < amount <= Decimal("9999999999.99"):
        raise HTTPException(422, "Activity total is outside supported LKR amounts")
    return amount


def slot_time(value):
    try:
        value = mvp_homestay.utc_naive(value)
    except OverflowError:
        raise HTTPException(422, "Activity time is outside the supported range") from None
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def quote(listing, slot, remaining, quantity):
    return ActivitySlotDetails(
        id=slot.id, date=mvp_homestay.local_date(slot.starts_at),
        starts_at=slot.starts_at.replace(tzinfo=UTC), ends_at=slot.ends_at.replace(tzinfo=UTC),
        remaining_capacity=remaining if slot.is_open else 0,
        available=slot.is_open and remaining >= quantity, total_amount=total(listing, quantity),
    )


def booking_slot(db, listing, req):
    verification.require_schema(db)
    validate_count(metadata(db, listing), req.quantity)
    if req.slot_id is None or req.expected_total is None:
        raise HTTPException(422, "Activity bookings require slot_id and expected_total")
    start, end = map(slot_time, (req.starts_at, req.ends_at))
    row = db.execute(slots().where(
        MVPAvailabilitySlot.id == req.slot_id, MVPAvailabilitySlot.listing_id == listing.id,
        MVPAvailabilitySlot.starts_at == start, MVPAvailabilitySlot.ends_at == end,
    )).one_or_none()
    if row is None or not row[0].is_open or row[1] < req.quantity:
        raise HTTPException(409, "Selected activity slot is unavailable")
    if req.expected_total != total(listing, req.quantity):
        raise HTTPException(409, "Activity price changed")
    return row[0]


def validate_calendar(db, listing, unit, payload, start, end, slot=None):
    verification.require_schema(db)
    config = metadata(db, listing)
    if (config is None or not unit.is_active or payload.capacity > config.max_participants
            or payload.capacity > unit.capacity or start <= verification.now() or end <= start):
        raise HTTPException(422, "Invalid activity slot dates, unit or capacity")
    if payload.is_open and payload.capacity < config.min_participants:
        raise HTTPException(422, "Open slot capacity is below the activity minimum")
    overlap = select(MVPAvailabilitySlot.id).where(
        MVPAvailabilitySlot.listing_id == listing.id,
        MVPAvailabilitySlot.starts_at < end, MVPAvailabilitySlot.ends_at > start,
    )
    if slot is not None:
        overlap = overlap.where(MVPAvailabilitySlot.id != slot.id)
        held = db.scalar(select(func.coalesce(func.sum(MVPBookingAllocation.quantity), 0)).where(
            MVPBookingAllocation.slot_id == slot.id,
            MVPBookingAllocation.released_at.is_(None),
        ))
        if held and (
            not payload.is_open or (start, end) != (slot.starts_at, slot.ends_at)
            or payload.capacity < held
        ):
            raise HTTPException(409, "Activity slot has an existing reservation")
    if db.scalar(overlap.limit(1)) is not None:
        raise HTTPException(409, "Activity slots cannot overlap")
