from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, true, update

from app.core.booking_transactions import CAPTURED, lock_booking_resource
from app.core.listing_sync import (
    record_booking_status_change,
    record_cash_payment_transaction,
)
from app.core.notification_service import create_notification
from app.core.publication import approved_provider, public_artisan
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentTransaction
from app.models.showcase_item import ShowcaseItem
from app.models.tour_guide import (
    TourGuide,
    TourGuideBooking,
    TourGuidePackage,
)
from app.services.activity_capacity import release_activity_capacity
from app.services.guide_booking import (
    LOCAL,
    expire_hold,
    is_expired,
    lock_guide,
    release_expired_holds,
    require_availability,
)

GUIDE = "tour_guide"
ARTISAN = "artisan_reservation"
MODELS = {GUIDE: TourGuideBooking, ARTISAN: ArtisanReservation}


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def cash_recorded(db, reference_id):
    return (
        db.scalar(
            select(PaymentTransaction.id)
            .where(
                PaymentTransaction.booking_reference_id == reference_id,
                PaymentTransaction.payment_method == "cash",
                PaymentTransaction.payment_status.in_(CAPTURED),
            )
            .limit(1)
        )
        is not None
    )


def save_event(db, ref, old, new, actor, recipient, action):
    record_booking_status_change(db, ref.id, old, new, actor, notes=action)
    create_notification(
        db=db,
        user_id=recipient,
        title=f"Reservation {ref.booking_reference}",
        message=f"Reservation {ref.booking_reference}: {action.replace('_', ' ')}.",
        notification_type=f"reservation_{action}",
    )


def create_reference(db, native, module, provider_id, buyer_id, amount):
    reference = BookingReference(
        module_type=module,
        target_booking_id=native.id,
        provider_profile_id=provider_id,
        user_id=buyer_id,
        total_price=amount,
        currency=native.currency,
        booking_reference=f"RS-{uuid4().hex}",
        booking_status="pending",
        payment_status="unpaid",
    )
    db.add(reference)
    db.flush()
    provider = db.get(ProviderProfile, provider_id)
    save_event(db, reference, None, "pending", buyer_id, provider.user_id, "requested")
    return reference


def record_native_event(db, row, module, provider_id, old, new, actor, recipient):
    ref = db.scalar(
        select(BookingReference)
        .where(
            BookingReference.module_type == module,
            BookingReference.target_booking_id == row.id,
            BookingReference.user_id == row.user_id,
            BookingReference.provider_profile_id == provider_id,
        )
        .order_by(BookingReference.id)
        .limit(1)
    )
    if ref is None:
        ref = BookingReference(
            module_type=module,
            target_booking_id=row.id,
            provider_profile_id=provider_id,
            user_id=row.user_id,
            booking_reference=f"RS-{uuid4().hex}",
            total_price=row.total_price,
            currency=row.currency,
            booking_status=new,
            payment_status=row.payment_status,
        )
        db.add(ref)
        db.flush()
    else:
        ref.booking_status = new
        ref.payment_status = row.payment_status
    save_event(db, ref, old, new, actor, recipient, new)


def change_activity_booking(db, provider, booking_id, action, reason):
    booking = ActivityBooking
    owned_activity = select(Activity.id).where(
        Activity.provider_profile_id == provider.id,
        approved_provider(provider.id, hold=True),
    )
    result = db.execute(
        select(booking, ActivitySlot)
        .join(ActivitySlot, ActivitySlot.id == booking.activity_slot_id)
        .where(booking.id == booking_id, ActivitySlot.activity_id.in_(owned_activity))
    ).first()
    if result is None:
        raise HTTPException(404, "Owned activity booking not found")
    row, slot = result
    now = utc_now()
    old = row.booking_status
    if action == "complete" and old == "confirmed" and slot.ends_at <= now:
        changes = {"booking_status": "completed"}
    elif (
        action == "cancel"
        and old in ("pending", "confirmed")
        and (
            slot.starts_at > now
            and row.payment_status == "unpaid"
            and row.payment_method == "pay_at_location"
        )
    ):
        changes = {
            "booking_status": "cancelled",
            "cancellation_reason": reason,
            "cancelled_at": now,
        }
    else:
        raise HTTPException(409, "Action is unavailable for this activity booking")
    changed = db.execute(
        update(booking)
        .where(
            booking.id == row.id,
            booking.booking_status == old,
            booking.payment_status == row.payment_status,
            select(ActivitySlot.id)
            .where(
                ActivitySlot.id == booking.activity_slot_id,
                ActivitySlot.activity_id.in_(owned_activity),
            )
            .exists(),
        )
        .values(**changes)
        .execution_options(synchronize_session=False)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Booking changed. Reload and retry.")
    new = changes["booking_status"]
    if new == "cancelled" and not release_activity_capacity(
        db, slot.id, row.participants
    ):
        db.rollback()
        raise HTTPException(409, "Unable to release reserved capacity")
    record_native_event(
        db, row, "activity", provider.id, old, new, provider.user_id, row.user_id
    )
    db.commit()


def reserve_guide(db, buyer, request):
    now = datetime.now(UTC)
    if not now < request.starts_at <= now + timedelta(days=365):
        raise HTTPException(422, "Tour must start within the next 365 days")
    lock_guide(db, request.guide_id)
    guide = db.scalar(
        select(TourGuide)
        .with_hint(TourGuide, "WITH (HOLDLOCK)", "mssql")
        .where(
            TourGuide.id == request.guide_id,
            TourGuide.is_active == true(),
            TourGuide.is_available == true(),
            TourGuide.approval_status == "approved",
            approved_provider(TourGuide.provider_profile_id, hold=True),
        )
    )
    package = db.scalar(
        select(TourGuidePackage)
        .with_hint(TourGuidePackage, "WITH (HOLDLOCK)", "mssql")
        .where(
            TourGuidePackage.id == request.package_id,
            TourGuidePackage.tour_guide_id == request.guide_id,
            TourGuidePackage.is_active == true(),
        )
    )
    if guide is None or package is None:
        raise HTTPException(404, "Available guide package not found")
    if request.people > min(package.max_people, guide.max_group_size):
        raise HTTPException(409, "Group exceeds package capacity")
    start = request.starts_at.astimezone(UTC).replace(tzinfo=None)
    end = start + timedelta(seconds=int(package.duration_hours * 3600))
    if start <= utc_now():
        raise HTTPException(422, "Tour must start in the future")
    day = request.starts_at.astimezone(LOCAL).date()
    require_availability(db, guide.id, start, end)
    release_expired_holds(db, guide.id, utc_now())
    conflict = db.scalar(
        select(TourGuideBooking.id)
        .where(
            TourGuideBooking.tour_guide_id == guide.id,
            TourGuideBooking.booking_status.in_(
                ("pending", "confirmed", "in_progress")
            ),
            TourGuideBooking.start_datetime < end,
            TourGuideBooking.end_datetime > start,
        )
        .limit(1)
    )
    if conflict is not None:
        raise HTTPException(409, "An overlapping reservation already exists")
    total = Decimal(package.price) * (
        request.people if package.pricing_type == "per_person" else 1
    )
    if not 0 <= total < 10_000_000_000:
        raise HTTPException(422, "Reservation total exceeds the supported range")
    row = TourGuideBooking(
        tour_guide_id=guide.id,
        package_id=package.id,
        traveler_id=buyer.id,
        booking_date=day,
        start_datetime=start,
        end_datetime=end,
        number_of_people=request.people,
        pickup_location=package.meeting_point,
        total_price=total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        currency=package.currency,
        payment_method=request.payment_method,
        cancellation_policy_snapshot=guide.cancellation_policy,
        free_cancellation_hours_snapshot=guide.free_cancellation_hours,
    )
    db.add(row)
    db.flush()
    ref = create_reference(
        db, row, GUIDE, guide.provider_profile_id, buyer.id, row.total_price
    )
    db.commit()
    return reservation_payload(row, ref)


def reserve_artisan(db, buyer, request):
    today = datetime.now(LOCAL).date()
    if not today <= request.pickup_date <= today + timedelta(days=90):
        raise HTTPException(422, "Pickup must be scheduled within 90 days")
    lock_booking_resource(db, f"artisan_stock_{request.item_id}")
    item = db.scalar(
        select(ShowcaseItem)
        .join(ArtisanListing)
        .with_hint(ArtisanListing, "WITH (HOLDLOCK)", "mssql")
        .where(
            ShowcaseItem.id == request.item_id,
            ShowcaseItem.item_type == "product",
            ShowcaseItem.is_available == true(),
            ShowcaseItem.is_active == true(),
            ShowcaseItem.approval_status == "approved",
            ShowcaseItem.price.is_not(None),
            public_artisan(),
            approved_provider(ArtisanListing.provider_profile_id, hold=True),
        )
    )
    if item is None:
        raise HTTPException(404, "Reservable product not found")
    if len(item.currency) != 3:
        raise HTTPException(409, "Product currency is unsupported")
    duplicate = db.scalar(
        select(ArtisanReservation.id)
        .where(
            ArtisanReservation.showcase_item_id == item.id,
            ArtisanReservation.traveler_id == buyer.id,
            ArtisanReservation.pickup_date == request.pickup_date,
            ArtisanReservation.reservation_status.in_(
                ("pending", "confirmed", "ready_for_pickup")
            ),
        )
        .limit(1)
    )
    if duplicate is not None:
        raise HTTPException(
            409, "An open pickup reservation already exists for this date"
        )
    changed = db.execute(
        update(ShowcaseItem)
        .where(
            ShowcaseItem.id == item.id,
            ShowcaseItem.stock_quantity >= request.quantity,
            ShowcaseItem.price == item.price,
            ShowcaseItem.currency == item.currency,
            ShowcaseItem.is_available == true(),
            ShowcaseItem.is_active == true(),
            ShowcaseItem.approval_status == "approved",
        )
        .values(stock_quantity=ShowcaseItem.stock_quantity - request.quantity)
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Product stock or pricing changed")
    total = item.price * request.quantity
    if total >= 10_000_000_000:
        raise HTTPException(422, "Reservation total exceeds the supported range")
    row = ArtisanReservation(
        artisan_listing_id=item.artisan_listing_id,
        showcase_item_id=item.id,
        traveler_id=buyer.id,
        quantity=request.quantity,
        total_price=total,
        currency=item.currency,
        pickup_date=request.pickup_date,
    )
    db.add(row)
    db.flush()
    ref = create_reference(
        db, row, ARTISAN, item.artisan_listing.provider_profile_id, buyer.id, total
    )
    db.commit()
    return reservation_payload(row, ref)


def reservation_payload(row, ref):
    guide = isinstance(row, TourGuideBooking)
    starts = row.start_datetime.replace(tzinfo=UTC) if guide else None
    return {
        "id": row.id,
        "module": GUIDE if guide else ARTISAN,
        "reference": ref.booking_reference,
        "status": (
            "expired"
            if is_expired(row, utc_now())
            else "awaiting_payment"
            if row.booking_status == "pending"
            and row.expires_at is not None
            and row.payment_method == "online"
            else row.booking_status
        )
        if guide
        else row.reservation_status,
        "payment_method": row.payment_method if guide else None,
        "payment_expires_at": row.expires_at.replace(tzinfo=UTC)
        if guide and row.expires_at
        else None,
        "payment_status": ref.payment_status,
        "total": ref.total_price,
        "currency": ref.currency,
        "starts_at": starts,
        "ends_at": row.end_datetime.replace(tzinfo=UTC) if guide else None,
        "pickup_date": None if guide else row.pickup_date,
        "meeting_point": row.pickup_location if guide else None,
    }


def reservations(db, module, *, user_id=None, provider_id=None):
    model = MODELS[module]
    listing = TourGuide if module == GUIDE else ArtisanListing
    listing_id = model.tour_guide_id if module == GUIDE else model.artisan_listing_id
    query = (
        select(model, BookingReference)
        .join(listing, listing.id == listing_id)
        .join(
            BookingReference,
            (BookingReference.module_type == module)
            & (BookingReference.target_booking_id == model.id)
            & (BookingReference.user_id == model.traveler_id)
            & (BookingReference.provider_profile_id == listing.provider_profile_id),
        )
    )
    if user_id is not None:
        return query.where(model.traveler_id == user_id)
    return query.where(listing.provider_profile_id == provider_id)


def change_reservation(db, module, booking_id, user, action, reason, provider=None):
    owned = {"provider_id": provider.id} if provider else {"user_id": user.id}
    result = db.execute(
        reservations(db, module, **owned).where(MODELS[module].id == booking_id)
    ).first()
    if result is None:
        raise HTTPException(404, "Owned reservation not found")
    row, ref = result
    guide = module == GUIDE
    if guide:
        lock_guide(db, row.tour_guide_id)
    else:
        lock_booking_resource(db, f"artisan_stock_{row.showcase_item_id}")
    if (
        provider
        and db.scalar(
            select(ProviderProfile.id).where(
                ProviderProfile.id == provider.id,
                ProviderProfile.user_id == user.id,
                ProviderProfile.provider_type == ("tour_guide" if guide else "artisan"),
                approved_provider(provider.id, hold=True),
            )
        )
        is None
    ):
        raise HTTPException(403, "Approved category access is required")
    result = db.execute(
        reservations(db, module, **owned)
        .where(MODELS[module].id == booking_id)
        .execution_options(populate_existing=True)
    ).first()
    if result is None:
        raise HTTPException(404, "Owned reservation not found")
    row, ref = result
    now = utc_now()
    if guide and expire_hold(db, row, ref, now):
        db.commit()
        raise HTTPException(409, "Tour checkout hold has expired")
    old = row.booking_status if guide else row.reservation_status
    future = (
        row.start_datetime > now
        if guide
        else row.pickup_date is not None
        and row.pickup_date >= datetime.now(LOCAL).date()
    )
    next_status = old
    awaiting_payment = (
        guide and row.payment_method == "online" and row.expires_at is not None
    )
    if (
        action == "accept"
        and provider
        and old == "pending"
        and future
        and not awaiting_payment
    ):
        if guide:
            require_availability(
                db, row.tour_guide_id, row.start_datetime, row.end_datetime
            )
        if guide and row.payment_method == "online" and row.total_price > 0:
            row.expires_at = min(now + timedelta(minutes=30), row.start_datetime)
            row.payment_status = ref.payment_status = "pending"
        else:
            next_status = "confirmed"
    elif action == "reject" and provider and old == "pending" and not awaiting_payment:
        next_status = "rejected" if guide else "cancelled"
    elif (
        action == "cancel"
        and old in ("pending", "confirmed", "ready_for_pickup")
        and (
            ref.payment_status == "unpaid"
            or (awaiting_payment and ref.payment_status in ("pending", "failed"))
        )
        and (not guide or future)
        and (not guide or row.payment_status in ("unpaid", "pending", "failed"))
    ):
        if (
            guide
            and (old == "confirmed" or awaiting_payment)
            and not provider
            and (
                row.start_datetime - now
                < timedelta(hours=row.free_cancellation_hours_snapshot)
            )
        ):
            raise HTTPException(
                409, "Cancellation is outside the free cancellation window"
            )
        next_status = "cancelled"
        if awaiting_payment:
            row.payment_status = ref.payment_status = "failed"
    elif action == "ready" and provider and not guide and old == "confirmed":
        next_status = "ready_for_pickup"
    elif (
        action == "cash"
        and provider
        and old in ("confirmed", "ready_for_pickup")
        and ref.payment_status == "unpaid"
    ):
        if guide and row.payment_method != "pay_at_location":
            raise HTTPException(409, "This booking uses online payment")
        if ref.total_price <= 0 or cash_recorded(db, ref.id):
            raise HTTPException(409, "A positive outstanding cash balance is required")
        record_cash_payment_transaction(db, ref, row.traveler_id, ref.total_price)
        ref.payment_status = "paid"
        if guide:
            row.payment_status = "paid"
    elif (
        action == "complete"
        and provider
        and old in ("confirmed", "ready_for_pickup")
        and (ref.payment_status == "paid" or ref.total_price == 0)
        and (
            row.end_datetime <= now
            if guide
            else row.pickup_date is not None
            and row.pickup_date <= datetime.now(LOCAL).date()
        )
    ):
        next_status = "completed"
    else:
        raise HTTPException(409, "Action is unavailable for this reservation state")

    if next_status in ("cancelled", "rejected"):
        row.cancelled_by = user.id
        row.cancellation_reason = reason
        if guide:
            row.cancelled_at = now
        else:
            db.execute(
                update(ShowcaseItem)
                .where(ShowcaseItem.id == row.showcase_item_id)
                .values(stock_quantity=ShowcaseItem.stock_quantity + row.quantity)
            )
    if guide:
        row.booking_status = next_status
    else:
        row.reservation_status = next_status
    ref.booking_status = next_status
    recipient = (
        row.traveler_id
        if provider
        else db.get(ProviderProfile, ref.provider_profile_id).user_id
    )
    audit_old = "awaiting_payment" if awaiting_payment and old == "pending" else old
    audit_new = (
        "awaiting_payment"
        if guide and action == "accept" and next_status == "pending"
        else next_status
    )
    save_event(db, ref, audit_old, audit_new, user.id, recipient, action)
    db.commit()
    return reservation_payload(row, ref)
