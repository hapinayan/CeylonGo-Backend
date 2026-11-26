import json
from datetime import UTC, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import and_, case, select, true

from app.models.mvp import MVPAvailabilitySlot, MVPBooking, MVPInventoryUnit, MVPListing
from app.schemas.mvp_guide import GuideSlot
from app.services import mvp_activity, mvp_listing_verification as verification
from app.services.tour_guide_sync import package_subtotal


def save_package(db, listing, payload, unit=None):
    verification.require_editable(listing)
    if listing.category != "guide":
        raise HTTPException(404, "Tour guide listing not found")
    material = payload.model_dump(
        mode="json", exclude={"title", "max_people", "is_active", "price"},
    )
    if unit is None:
        if len(listing.units) >= 50:
            raise HTTPException(409, "Guide package limit reached")
        unit = MVPInventoryUnit(listing_id=listing.id, provider_id=listing.provider_id)
        db.add(unit)
        verification.invalidate_review(listing)
    else:
        previous = verification.guide_package(unit)
        if previous is None:
            raise HTTPException(404, "Tour guide package not found")
        future_slots = select(MVPAvailabilitySlot.id).where(
            MVPAvailabilitySlot.unit_id == unit.id,
            MVPAvailabilitySlot.starts_at > verification.now(),
            MVPAvailabilitySlot.is_open == true(),
        )
        if previous.duration_hours != payload.duration_hours and db.scalar(
            future_slots.limit(1)
        ) is not None:
            raise HTTPException(
                409, "Block future slots before changing package duration",
            )
        if db.scalar(future_slots.where(
            MVPAvailabilitySlot.capacity > payload.max_people,
        ).limit(1)) is not None:
            raise HTTPException(
                409, "Reduce future slot capacity before reducing package capacity",
            )
        previous_fields = previous.model_dump(
            mode="json", include=set(material),
        )
        if previous.title != payload.title or previous_fields != material:
            verification.invalidate_review(listing)
    unit.label = payload.title
    unit.capacity = payload.max_people
    unit.is_active = payload.is_active
    unit.room_metadata = json.dumps({
        "guide_package": payload.model_dump(
            mode="json", exclude={"title", "max_people", "is_active"},
        )
    })
    verification.commit(db)
    return verification.guide_package(unit)


def duration(hours):
    return timedelta(milliseconds=int(hours * 3_600_000))


def validate_calendar(db, unit, payload, start, end, slot=None):
    item = verification.guide_package(unit)
    if (item is None or not unit.is_active or payload.capacity > unit.capacity
            or start <= verification.now() or end <= start
            or (payload.is_open and end - start != duration(item.duration_hours))):
        raise HTTPException(422, "Invalid guide package, slot duration or capacity")
    overlap = select(MVPAvailabilitySlot.id).where(
        MVPAvailabilitySlot.unit_id == unit.id,
        MVPAvailabilitySlot.starts_at < end, MVPAvailabilitySlot.ends_at > start,
    )
    if slot is not None:
        overlap = overlap.where(MVPAvailabilitySlot.id != slot.id)
    if db.scalar(overlap.limit(1)) is not None:
        raise HTTPException(409, "Slots for the same package cannot overlap")


def busy(listing, start, end):
    return select(MVPBooking.id).join(MVPListing).where(
        MVPListing.category == "guide", MVPListing.provider_id == listing.provider_id,
        MVPBooking.status.in_(("pending", "confirmed", "completed")),
        MVPBooking.starts_at < end, MVPBooking.ends_at > start,
    ).correlate(MVPAvailabilitySlot)


def available_slots(listing, package_id=None):
    collision = busy(
        listing, MVPAvailabilitySlot.starts_at, MVPAvailabilitySlot.ends_at,
    ).exists()
    query = select(MVPAvailabilitySlot, case((collision, 1), else_=0).label("busy")).join(
        MVPInventoryUnit
    ).where(
        MVPAvailabilitySlot.listing_id == listing.id,
        MVPInventoryUnit.listing_id == listing.id,
        MVPInventoryUnit.is_active == true(),
        MVPAvailabilitySlot.starts_at > verification.now(),
    )
    if package_id is not None:
        query = query.where(MVPAvailabilitySlot.unit_id == package_id)
    return query


def total(item, quantity):
    amount = package_subtotal(item.price, item.pricing_type, quantity)
    if amount > Decimal("9999999999.99"):
        raise HTTPException(422, "Guide total is outside supported LKR amounts")
    return amount


def quote(slot, item, occupied, quantity):
    return GuideSlot(
        id=slot.id, package_id=item.id, capacity=slot.capacity,
        starts_at=slot.starts_at.replace(tzinfo=UTC), ends_at=slot.ends_at.replace(tzinfo=UTC),
        available=(not occupied and slot.is_open and quantity <= slot.capacity
                   and slot.ends_at - slot.starts_at == duration(item.duration_hours)),
        total_amount=total(item, quantity),
    )


def booking_selection(db, listing, req):
    verification.require_schema(db)
    if req.slot_id is None or req.expected_total is None:
        raise HTTPException(422, "Guide bookings require slot_id and expected_total")
    start, end = map(mvp_activity.slot_time, (req.starts_at, req.ends_at))
    row = db.execute(available_slots(listing).where(and_(
        MVPAvailabilitySlot.id == req.slot_id, MVPAvailabilitySlot.starts_at == start,
        MVPAvailabilitySlot.ends_at == end,
    ))).one_or_none()
    if row is None:
        raise HTTPException(409, "Selected guide slot is unavailable")
    slot, occupied = row
    item = verification.guide_package(slot.unit)
    if item is None or not slot.is_open or occupied:
        raise HTTPException(409, "Selected guide slot is unavailable")
    if req.quantity > min(slot.capacity, item.max_people):
        raise HTTPException(422, "Participant count exceeds the guide package capacity")
    selection = quote(slot, item, occupied, req.quantity)
    if not selection.available or selection.total_amount != req.expected_total:
        raise HTTPException(409, "Guide slot or price changed")
    return slot, selection.total_amount
