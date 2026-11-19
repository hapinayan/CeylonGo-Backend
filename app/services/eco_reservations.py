from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select, true, update
from sqlalchemy.exc import IntegrityError

from app.core.booking_transactions import lock_booking_resource
from app.core.listing_sync import record_cash_payment_transaction
from app.core.publication import approved_provider, public_eco
from app.models.eco_experience import EcoExperience
from app.models.eco_reservation import EcoAvailabilitySlot as Slot
from app.models.eco_reservation import EcoReservation
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.services.eco_experience_eligibility import (
    EcoEligibilityException,
    validate_eco_booking_eligibility,
)
from app.services.provider_reservations import (
    LOCAL,
    cash_recorded,
    create_reference,
    save_event,
    utc_now,
)

MODULE = "eco_experience"
HORIZON_DAYS = 365


def timestamp(value):
    return value.replace(tzinfo=UTC)


def local_day(now):
    return timestamp(now).astimezone(LOCAL).date()


def local_midnight(day):
    return (
        datetime.combine(day, datetime.min.time(), LOCAL)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


def lock_schedule(db, experience_id):
    # SQLite fixtures still enforce the conditional updates and unique index.
    lock_booking_resource(db, f"eco_schedule_{experience_id}")


def eligible(db, experience_id, start, end, participants=None):
    try:
        return validate_eco_booking_eligibility(
            db,
            experience_id,
            participants,
            start,
            end,
            calendar_timezone=LOCAL,
        )
    except EcoEligibilityException as exc:
        raise HTTPException(
            409, detail={"code": exc.code, "message": exc.message}
        ) from exc


def experience(db, experience_id, *, provider=None, published=True, hold=False):
    profile = ProviderProfile
    query = select(EcoExperience).join(
        profile, profile.id == EcoExperience.provider_profile_id
    )
    query = query.where(
        EcoExperience.id == experience_id,
        profile.provider_type == "eco_tourism",
    )
    if provider is not None:
        query = query.where(
            profile.id == provider.id,
            profile.user_id == provider.user_id,
            approved_provider(provider.id, hold=hold),
        )
    if published:
        query = query.where(public_eco(hold=hold))
    if hold:
        query = query.with_hint(EcoExperience, "WITH (HOLDLOCK)", "mssql")
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Eco experience not found")
    return row


def slot_result(slot, pricing_type):
    available = slot.capacity - slot.reserved_count
    if pricing_type == "per_group" and slot.reserved_count:
        available = 0
    return {
        "id": slot.id,
        "experience_id": slot.eco_experience_id,
        "starts_at": timestamp(slot.starts_at),
        "ends_at": timestamp(slot.ends_at),
        "capacity": slot.capacity,
        "available_places": available,
        "is_open": slot.is_open,
    }


def create_slot(db, provider, experience_id, request):
    lock_schedule(db, experience_id)
    listing = experience(db, experience_id, provider=provider, hold=True)
    start = request.starts_at.astimezone(UTC).replace(tzinfo=None)
    now = utc_now()
    if start <= now or local_day(start) > local_day(now) + timedelta(days=HORIZON_DAYS):
        raise HTTPException(422, "Eco slot must start within the next 365 local days")
    end = start + timedelta(seconds=int(listing.duration_hours * 3600))
    rules = eligible(db, listing.id, start, end)
    if not rules.min_participants <= request.capacity <= rules.max_group_size:
        raise HTTPException(
            422, "Slot capacity must be within the listing's group limits"
        )
    overlap = db.scalar(
        select(Slot.id)
        .where(
            Slot.eco_experience_id == listing.id,
            Slot.starts_at < end,
            Slot.ends_at > start,
            (Slot.is_open == true()) | (Slot.reserved_count > 0),
        )
        .limit(1)
    )
    if overlap is not None:
        raise HTTPException(409, "An overlapping Eco slot already exists")
    slot = Slot(
        eco_experience_id=listing.id,
        starts_at=start,
        ends_at=end,
        capacity=request.capacity,
    )
    db.add(slot)
    db.flush()
    result = slot_result(slot, rules.pricing_type)
    db.commit()
    return result


def slot_list(
    db, experience_id, page, page_size, *, day: date | None = None, provider=None
):
    listing = experience(
        db, experience_id, provider=provider, published=provider is None
    )
    rows = select(Slot).where(Slot.eco_experience_id == listing.id)
    now = utc_now()
    if day is not None:
        today = local_day(now)
        if not today <= day <= today + timedelta(days=HORIZON_DAYS):
            raise HTTPException(422, "Use a date within the next 365 local days")
        start = local_midnight(day)
        rows = rows.where(
            Slot.starts_at >= start, Slot.starts_at < start + timedelta(days=1)
        )
    if provider is None:
        rows = rows.where(
            Slot.is_open == true(),
            Slot.starts_at > now,
            Slot.capacity - Slot.reserved_count >= listing.min_participants,
        )
        if listing.pricing_type == "per_group":
            rows = rows.where(Slot.reserved_count == 0)
        today = local_day(now)
        for rule in listing.season_restrictions:
            if rule.is_active and rule.end_date >= today:
                first = local_midnight(max(today, rule.start_date))
                if rule.end_date == date.max:
                    rows = rows.where(Slot.ends_at <= first)
                else:
                    last = local_midnight(rule.end_date + timedelta(days=1))
                    rows = rows.where(
                        or_(Slot.ends_at <= first, Slot.starts_at >= last)
                    )
    rows = db.scalars(
        rows.order_by(Slot.starts_at, Slot.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [slot_result(slot, listing.pricing_type) for slot in rows]


def close_slot(db, provider, experience_id, slot_id):
    lock_schedule(db, experience_id)
    listing = experience(
        db, experience_id, provider=provider, published=False, hold=True
    )
    slot = db.scalar(
        select(Slot).where(Slot.id == slot_id, Slot.eco_experience_id == listing.id)
    )
    if slot is None:
        raise HTTPException(404, "Eco slot not found")
    # A closure stops new requests. Existing reservations keep their schedule and holds.
    slot.is_open = False
    result = slot_result(slot, listing.pricing_type)
    db.commit()
    return result


def reservation_result(row, slot, ref):
    return {
        "id": row.id,
        "module": MODULE,
        "reference": ref.booking_reference,
        "experience_id": slot.eco_experience_id,
        "slot_id": slot.id,
        "participants": row.participants,
        "status": row.status,
        "payment_status": row.payment_status,
        "payment_method": "pay_at_location",
        "total": row.total_price,
        "currency": row.currency,
        "starts_at": timestamp(slot.starts_at),
        "ends_at": timestamp(slot.ends_at),
    }


def reserve(db, traveler, request):
    experience_id = db.scalar(
        select(Slot.eco_experience_id).where(Slot.id == request.slot_id)
    )
    if experience_id is None:
        raise HTTPException(404, "Eco slot not found")
    lock_schedule(db, experience_id)
    listing = experience(db, experience_id, hold=True)
    slot = db.get(Slot, request.slot_id)
    now = utc_now()
    if not slot.is_open or slot.starts_at <= now:
        raise HTTPException(409, "Eco slot is closed or has already started")
    rules = eligible(db, listing.id, slot.starts_at, slot.ends_at, request.participants)
    if request.participants < rules.min_participants:
        raise HTTPException(
            422, "Group is below the listing's minimum participant count"
        )
    if rules.mandatory_safety_rules and not request.accepts_safety_rules:
        raise HTTPException(422, "Acknowledge the mandatory Eco safety rules")
    if request.participants > slot.capacity:
        raise HTTPException(409, "Group exceeds slot capacity")
    if (
        db.scalar(
            select(EcoReservation.id)
            .where(
                EcoReservation.slot_id == slot.id,
                EcoReservation.traveler_id == traveler.id,
                EcoReservation.holds_capacity == true(),
            )
            .limit(1)
        )
        is not None
    ):
        raise HTTPException(409, "Traveler already has a reservation for this slot")
    private = rules.pricing_type == "per_group"
    held = slot.capacity if private else request.participants
    total = (rules.base_price * (1 if private else request.participants)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if not 0 <= total < 10_000_000_000:
        raise HTTPException(422, "Reservation total is outside the supported range")
    changed = db.execute(
        update(Slot)
        .where(
            Slot.id == slot.id,
            Slot.is_open == true(),
            Slot.starts_at > now,
            Slot.reserved_count + held <= Slot.capacity,
        )
        .values(reserved_count=Slot.reserved_count + held)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Insufficient capacity for this reservation")
    row = EcoReservation(
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=request.participants,
        capacity_held=held,
        total_price=total,
        currency=rules.currency,
        free_cancellation_hours=rules.free_cancellation_hours,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            409, "Reservation conflicts with an existing request"
        ) from exc
    ref = create_reference(
        db, row, MODULE, listing.provider_profile_id, traveler.id, total
    )
    result = reservation_result(row, slot, ref)
    db.commit()
    return result


def reservation_query(*, traveler_id=None, provider_id=None):
    row, ref = EcoReservation, BookingReference
    query = (
        select(row, Slot, ref)
        .join(Slot, Slot.id == row.slot_id)
        .join(
            EcoExperience,
            EcoExperience.id == Slot.eco_experience_id,
        )
        .join(
            ref,
            (
                (ref.module_type == MODULE)
                & (ref.target_booking_id == row.id)
                & (ref.user_id == row.traveler_id)
                & (ref.provider_profile_id == EcoExperience.provider_profile_id)
            ),
        )
    )
    if traveler_id is not None:
        return query.where(row.traveler_id == traveler_id)
    if provider_id is not None:
        return query.where(EcoExperience.provider_profile_id == provider_id)
    raise ValueError("Reservation query must be scoped to an owner")


def change_reservation(db, user, reservation_id, action, reason, *, provider=None):
    scope = {"provider_id": provider.id} if provider else {"traveler_id": user.id}
    query = reservation_query(**scope).where(EcoReservation.id == reservation_id)
    result = db.execute(query).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    lock_schedule(db, result[1].eco_experience_id)
    result = db.execute(query.execution_options(populate_existing=True)).first()
    if result is None:
        raise HTTPException(404, "Eco reservation not found")
    row, slot, ref = result
    listing = experience(
        db,
        slot.eco_experience_id,
        provider=provider,
        published=action == "accept",
        hold=True,
    )
    now = utc_now()
    old, payment = row.status, row.payment_status
    values = {}
    if action == "accept" and provider and old == "pending" and slot.starts_at > now:
        eligible(db, listing.id, slot.starts_at, slot.ends_at, row.participants)
        values = {"status": "confirmed", "decided_at": now}
    elif action == "reject" and provider and old == "pending":
        values = {"status": "rejected", "decided_at": now, "holds_capacity": False}
    elif (
        action == "cancel"
        and old in ("pending", "confirmed")
        and payment == "unpaid"
        and slot.starts_at > now
    ):
        if (
            not provider
            and old == "confirmed"
            and slot.starts_at - now < timedelta(hours=row.free_cancellation_hours)
        ):
            raise HTTPException(409, "The free cancellation window has closed")
        values = {"status": "cancelled", "cancelled_at": now, "holds_capacity": False}
    elif (
        action == "cash"
        and provider
        and old == "confirmed"
        and payment == "unpaid"
        and row.total_price > 0
    ):
        if cash_recorded(db, ref.id):
            raise HTTPException(409, "A cash receipt already exists")
        values = {"payment_status": "paid"}
    elif (
        action == "complete"
        and provider
        and old == "confirmed"
        and slot.ends_at <= now
        and (payment == "paid" or row.total_price == 0)
    ):
        values = {"status": "completed", "completed_at": now}
    else:
        raise HTTPException(409, "Action is unavailable for this Eco reservation")
    changed = db.execute(
        update(EcoReservation)
        .where(
            EcoReservation.id == row.id,
            EcoReservation.status == old,
            EcoReservation.payment_status == payment,
            EcoReservation.holds_capacity == true(),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Eco reservation has changed")
    if values.get("holds_capacity") is False:
        released = db.execute(
            update(Slot)
            .where(
                Slot.id == slot.id,
                Slot.reserved_count >= row.capacity_held,
            )
            .values(reserved_count=Slot.reserved_count - row.capacity_held)
            .execution_options(synchronize_session=False)
        )
        if released.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Eco slot capacity could not be released")
    if action == "cash":
        record_cash_payment_transaction(db, ref, row.traveler_id, row.total_price)
    new = values.get("status", old)
    ref.booking_status = new
    ref.payment_status = values.get("payment_status", payment)
    provider_user_id = db.get(ProviderProfile, listing.provider_profile_id).user_id
    save_event(
        db,
        ref,
        old,
        new,
        user.id,
        row.traveler_id if provider else provider_user_id,
        action,
    )
    db.refresh(row)
    result = reservation_result(row, slot, ref)
    db.commit()
    return result
