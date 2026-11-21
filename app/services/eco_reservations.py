import os
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import case, or_, select, true, update
from sqlalchemy.exc import IntegrityError

from app.core.booking_transactions import lock_booking_resource
from app.core.listing_sync import (
    record_booking_status_change,
    record_cash_payment_transaction,
)
from app.core.notification_service import create_notification
from app.core.publication import approved_provider, public_eco
from app.models.eco_experience import EcoExperience
from app.models.eco_reservation import EcoAvailabilitySlot as Slot
from app.models.eco_reservation import (
    EcoAgeLimit,
    EcoCancellationPolicy,
    EcoCancellationSnapshot,
    EcoReservation,
    EcoSlotClearance,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, Notification, PaymentTransaction
from app.services.eco_experience_eligibility import (
    EcoEligibilityException,
    validate_eco_booking_eligibility,
)
from app.services.eco_sponsorship import attribute_booking
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


def validate_slot_start(start):
    now = utc_now()
    if start <= now or local_day(start) > local_day(now) + timedelta(days=HORIZON_DAYS):
        raise HTTPException(422, "Eco slot must start within the next 365 local days")


def require_slot_no_overlap(db, experience_id, start, end, *, exclude=None):
    query = select(Slot.id).where(
        Slot.eco_experience_id == experience_id,
        Slot.starts_at < end,
        Slot.ends_at > start,
        (Slot.is_open == true()) | (Slot.reserved_count > 0),
    )
    if exclude is not None:
        query = query.where(Slot.id != exclude)
    if db.scalar(query.limit(1)) is not None:
        raise HTTPException(409, "An overlapping Eco slot already exists")


def create_slot(db, provider, experience_id, request):
    lock_schedule(db, experience_id)
    listing = experience(db, experience_id, provider=provider, hold=True)
    start = request.starts_at.astimezone(UTC).replace(tzinfo=None)
    validate_slot_start(start)
    end = start + timedelta(seconds=int(listing.duration_hours * 3600))
    rules = eligible(db, listing.id, start, end)
    if not rules.min_participants <= request.capacity <= rules.max_group_size:
        raise HTTPException(
            422, "Slot capacity must be within the listing's group limits"
        )
    require_slot_no_overlap(db, listing.id, start, end)
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
    if slot.is_open:
        slot.is_open = False
        rows = db.execute(reservation_query(experience_id=listing.id).where(
            EcoReservation.slot_id == slot.id,
            EcoReservation.status.in_(("pending", "confirmed")),
            EcoReservation.holds_capacity.is_(True),
        )).all()
        for row, _, ref, _ in rows:
            create_notification(
                db, row.traveler_id, f"Reservation {ref.booking_reference}",
                "The Eco slot is closed to new requests. Your reservation time is unchanged.",
                "reservation_slot_closed",
            )
    result = slot_result(slot, listing.pricing_type)
    db.commit()
    return result


def reschedule_slot(db, provider, experience_id, slot_id, request):
    lock_schedule(db, experience_id)
    listing = experience(db, experience_id, provider=provider, hold=True)
    slot = db.scalar(
        select(Slot).where(Slot.id == slot_id, Slot.eco_experience_id == listing.id)
    )
    if slot is None:
        raise HTTPException(404, "Eco slot not found")
    start = request.starts_at.astimezone(UTC).replace(tzinfo=None)
    if start == slot.starts_at:
        return slot_result(slot, listing.pricing_type)
    expected = request.expected_starts_at.astimezone(UTC).replace(tzinfo=None)
    if expected != slot.starts_at:
        raise HTTPException(409, "Eco slot schedule has changed")
    if not slot.is_open or slot.starts_at <= utc_now():
        raise HTTPException(409, "Only future open Eco slots can be rescheduled")
    validate_slot_start(start)
    end = start + (slot.ends_at - slot.starts_at)
    rules = eligible(db, listing.id, start, end)
    if not rules.min_participants <= slot.capacity <= rules.max_group_size:
        raise HTTPException(409, "Slot capacity is outside the listing's group limits")
    require_slot_no_overlap(db, listing.id, start, end, exclude=slot.id)
    historical_booking = db.scalar(select(EcoReservation.id).where(
        EcoReservation.slot_id == slot.id,
        EcoReservation.status.not_in(("pending", "confirmed")),
    ).limit(1))
    if historical_booking is not None:
        raise HTTPException(409, "Eco slot has historical reservations")
    rows = db.execute(reservation_query(experience_id=listing.id).where(
        EcoReservation.slot_id == slot.id,
        EcoReservation.status.in_(("pending", "confirmed")),
        EcoReservation.holds_capacity.is_(True),
    )).all()
    slot.starts_at, slot.ends_at = start, end
    require_slot_clearance(db, listing, slot)
    local_start = timestamp(start).astimezone(LOCAL).isoformat(timespec="seconds")
    for row, _, ref, _ in rows:
        eligible(db, listing.id, start, end, row.participants)
        record_booking_status_change(
            db, ref.id, row.status, row.status, provider.user_id,
            notes=f"Eco slot rescheduled to {local_start}",
        )
        # The locked schedule change is the deduplication boundary. Distinct
        # consecutive moves each need a notification, even within 30 seconds.
        db.add(Notification(
            user_id=row.traveler_id,
            title=f"Reservation {ref.booking_reference}",
            message=f"Reservation {ref.booking_reference} moved to {local_start}.",
            notification_type="reservation_slot_rescheduled",
            is_read=False,
        ))
    result = slot_result(slot, rules.pricing_type)
    db.commit()
    return result


def reservation_result(row, slot, ref, online=False):
    return {
        "id": row.id,
        "module": MODULE,
        "reference": ref.booking_reference,
        "experience_id": slot.eco_experience_id,
        "slot_id": slot.id,
        "participants": row.participants,
        "status": row.status,
        "payment_status": row.payment_status,
        "payment_method": "online" if online else "pay_at_location",
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
    require_slot_clearance(db, listing, slot)
    ages = request.participant_ages
    if ages is not None and len(ages) != request.participants:
        raise HTTPException(422, "Participant ages must match the group size")
    if listing.min_age and (ages is None or min(ages) < listing.min_age):
        raise HTTPException(422, "All participants must meet the listing minimum age")
    if os.getenv("ECO_NATIVE_SAFETY_RECORDS_ENABLED") == "1":
        age_limit = db.get(EcoAgeLimit, listing.id)
        if age_limit is not None and (ages is None or max(ages) > age_limit.maximum_age):
            raise HTTPException(422, "All participants must meet the listing maximum age")
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
    attribute_booking(db, listing.id, ref.id, request.sponsored_tracking_token)
    notify_reservation_actor(db, traveler.id, ref, "requested")
    if os.getenv("ECO_NATIVE_POLICY_SNAPSHOTS_ENABLED") == "1":
        policy = db.get(EcoCancellationPolicy, listing.id)
        if policy is not None:
            db.add(EcoCancellationSnapshot(
                reservation_id=row.id,
                late_refund_percent=policy.late_refund_percent,
            ))
    result = reservation_result(row, slot, ref)
    db.commit()
    return result


def reservation_query(
    *, traveler_id=None, provider_id=None, reference_id=None, experience_id=None,
    admin=False,
):
    row, ref = EcoReservation, BookingReference
    query = (
        select(row, Slot, ref, case((online_attempt_exists(ref.id), 1), else_=0))
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
    if reference_id is not None:
        return query.where(ref.id == reference_id)
    if experience_id is not None:
        return query.where(EcoExperience.id == experience_id)
    if admin:
        return query
    raise ValueError("Reservation query must be scoped to an owner")


def online_attempt_exists(reference_id):
    return select(PaymentTransaction.id).where(
        PaymentTransaction.booking_reference_id == reference_id,
        PaymentTransaction.gateway_name == "payhere",
        PaymentTransaction.payment_method == "online",
    ).exists()


def require_slot_clearance(db, listing, slot):
    required = {
        kind for kind, needed in (
            ("permit", listing.protected_area_permit_required),
            ("guide", listing.certified_guide_required),
        ) if needed
    }
    if not required:
        return
    if os.getenv("ECO_NATIVE_SAFETY_RECORDS_ENABLED") == "1":
        verified = set(db.scalars(select(EcoSlotClearance.kind).where(
            EcoSlotClearance.slot_id == slot.id,
            EcoSlotClearance.kind.in_(required),
            EcoSlotClearance.revoked_at.is_(None),
            EcoSlotClearance.valid_until >= slot.ends_at,
        )))
        if required <= verified:
            return
    raise HTTPException(409, "Required permit or guide verification is unavailable")


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
    row, slot, ref, online = result
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
        require_slot_clearance(db, listing, slot)
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
        and not online
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
        release_capacity(db, slot.id, row.capacity_held)
    if action == "cash":
        record_cash_payment_transaction(db, ref, row.traveler_id, row.total_price)
    new = values.get("status", old)
    ref.booking_status = new
    if "payment_status" in values:
        ref.payment_status = values["payment_status"]
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
    notify_reservation_actor(db, user.id, ref, action)
    db.refresh(row)
    result = reservation_result(row, slot, ref, online)
    db.commit()
    return result


def release_capacity(db, slot_id, held):
    released = db.execute(
        update(Slot)
        .where(Slot.id == slot_id, Slot.reserved_count >= held)
        .values(reserved_count=Slot.reserved_count - held)
        .execution_options(synchronize_session=False)
    )
    if released.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "Eco slot capacity could not be released")


def notify_reservation_actor(db, user_id, ref, event):
    create_notification(
        db, user_id, f"Reservation {ref.booking_reference}",
        f"Reservation {ref.booking_reference}: {event.replace('_', ' ')}.",
        f"reservation_{event}",
    )
