from datetime import UTC, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, true, update

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
from app.services.artisan_shared_reference import (
    get_artisan_booking_reference,
    sync_artisan_booking_reference,
)
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


def validate_artisan_pickup_window(pickup_date):
    today = datetime.now(LOCAL).date()
    if not today <= pickup_date <= today + timedelta(days=90):
        raise HTTPException(422, "Pickup must be scheduled within 90 days")


def ensure_unique_artisan_pickup(db, item_id, traveler_id, pickup_date):
    duplicate = db.scalar(
        select(ArtisanReservation.id)
        .where(
            ArtisanReservation.showcase_item_id == item_id,
            ArtisanReservation.traveler_id == traveler_id,
            ArtisanReservation.pickup_date == pickup_date,
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


ARTISAN_ACTIVE_STATES = ("pending", "confirmed", "ready_for_pickup")


def artisan_pickup_expiry(pickup_date):
    # Midnight at the start of the following Sri Lanka day, stored as naive UTC.
    return datetime.combine(pickup_date + timedelta(days=1), time.min, LOCAL).astimezone(
        UTC
    ).replace(tzinfo=None)


def effective_artisan_expiry(pickup_date, stored_expiry):
    pickup_cutoff = artisan_pickup_expiry(pickup_date) if pickup_date else None
    if stored_expiry is not None and stored_expiry.tzinfo is not None:
        stored_expiry = stored_expiry.astimezone(UTC).replace(tzinfo=None)
    if stored_expiry is not None and pickup_cutoff is not None:
        return min(stored_expiry, pickup_cutoff)
    return stored_expiry or pickup_cutoff


def artisan_reservation_expiry(row):
    return effective_artisan_expiry(row.pickup_date, row.expires_at)


def lock_artisan_product(db, product_id):
    lock_booking_resource(db, f"artisan_stock_{product_id}")


def create_artisan_hold(db, buyer, item_id, quantity, pickup_date, *, detailed=False, notes=None):
    validate_artisan_pickup_window(pickup_date)
    try:
        lock_artisan_product(db, item_id)
        item = db.get(ShowcaseItem, item_id, populate_existing=True)
        if item is None:
            raise HTTPException(404, "Showcase item not found")
        if item.item_type != "product":
            raise HTTPException(400 if detailed else 404, "Only handcrafted products can be reserved")
        if not (item.is_active and item.is_available and item.approval_status == "approved"):
            raise HTTPException(
                400 if detailed else 404,
                "This product is currently unavailable or not approved for reservation",
            )
        listing = db.scalar(
            select(ArtisanListing)
            .with_hint(ArtisanListing, "WITH (HOLDLOCK)", "mssql")
            .where(ArtisanListing.id == item.artisan_listing_id, public_artisan())
            .execution_options(populate_existing=True)
        )
        if listing is None:
            raise HTTPException(400 if detailed else 404, "Artisan business listing is not active or approved")
        provider = db.scalar(
            select(ProviderProfile).where(
                ProviderProfile.id == listing.provider_profile_id,
                ProviderProfile.provider_type == "artisan",
                approved_provider(listing.provider_profile_id, hold=True),
            )
        )
        if provider is None or provider.user_id == buyer.id:
            raise HTTPException(403, "This provider cannot accept the reservation")
        if item.price is None or item.price < 0 or len(item.currency) != 3:
            raise HTTPException(409, "Product price or currency is unsupported")
        insufficient = HTTPException(400 if detailed else 409, "Insufficient stock available")
        if detailed and item.stock_quantity < quantity:
            raise insufficient
        ensure_unique_artisan_pickup(db, item.id, buyer.id, pickup_date)
        total = item.price * quantity
        if total >= 10_000_000_000:
            raise HTTPException(422, "Reservation total exceeds the supported range")
        changed = db.execute(
            update(ShowcaseItem)
            .where(
                ShowcaseItem.id == item.id,
                ShowcaseItem.artisan_listing_id == listing.id,
                ShowcaseItem.stock_quantity >= quantity,
                ShowcaseItem.item_type == "product",
                ShowcaseItem.price == item.price,
                ShowcaseItem.currency == item.currency,
                ShowcaseItem.is_available == true(),
                ShowcaseItem.is_active == true(),
                ShowcaseItem.approval_status == "approved",
            )
            .values(
                stock_quantity=ShowcaseItem.stock_quantity - quantity,
                updated_at=func.sysutcdatetime(),
            )
        )
        if changed.rowcount != 1:
            raise insufficient
        row = ArtisanReservation(
            artisan_listing_id=listing.id, showcase_item_id=item.id,
            traveler_id=buyer.id, quantity=quantity, total_price=total,
            currency=item.currency, pickup_date=pickup_date, special_notes=notes,
            expires_at=artisan_pickup_expiry(pickup_date),
        )
        db.add(row)
        db.flush()
        if detailed:
            ref = sync_artisan_booking_reference(db, row, changed_by=buyer.id)
            create_notification(
                db, provider.user_id, f"Reservation {ref.booking_reference}",
                f"Reservation {ref.booking_reference}: requested.", "reservation_requested",
            )
        else:
            ref = create_reference(db, row, ARTISAN, provider.id, buyer.id, total)
        db.commit()
        return row, ref
    except Exception:
        db.rollback()
        raise


def reserve_artisan(db, buyer, request):
    row, ref = create_artisan_hold(
        db, buyer, request.item_id, request.quantity, request.pickup_date
    )
    return reservation_payload(row, ref)


def artisan_capture_exists(db, reference_id):
    return db.scalar(
        select(PaymentTransaction.id)
        .where(
            PaymentTransaction.booking_reference_id == reference_id,
            PaymentTransaction.payment_status.in_((*CAPTURED, "refunded")),
        )
        .limit(1)
    ) is not None


def restore_artisan_stock(db, row):
    changed = db.execute(
        update(ShowcaseItem)
        .where(
            ShowcaseItem.id == row.showcase_item_id,
            ShowcaseItem.artisan_listing_id == row.artisan_listing_id,
        )
        .values(
            stock_quantity=ShowcaseItem.stock_quantity + row.quantity,
            updated_at=func.sysutcdatetime(),
        )
    )
    if changed.rowcount != 1:
        raise HTTPException(409, "Reserved product does not match the listing")


def artisan_actor(db, row, user, provider=None, *, hold=False):
    listing = row.artisan_listing
    if provider is None and user.role == "provider":
        provider = listing.provider_profile
    if (
        provider is not None
        and user.role == "provider"
        and listing.provider_profile_id == provider.id
        and db.scalar(select(ProviderProfile.id).where(
            ProviderProfile.id == provider.id,
            ProviderProfile.user_id == user.id,
            ProviderProfile.provider_type == "artisan",
            approved_provider(provider.id, hold=hold),
        )) is not None
    ):
        return "provider"
    if user.role == "traveler" and row.traveler_id == user.id:
        return "traveler"
    if user.role == "admin":
        return "admin"
    if user.role == "provider":
        raise HTTPException(403, "You do not own the artisan listing or lack approved access")
    raise HTTPException(403, "Access forbidden for this artisan reservation")


def change_artisan_reservation(db, booking_id, user, action, reason=None,
                               *, provider=None, detailed=False):
    invalid = HTTPException(400 if detailed else 409, "Invalid reservation transition")
    row = db.get(ArtisanReservation, booking_id)
    if row is None:
        raise HTTPException(404, "Artisan reservation not found")
    try:
        # Authorize before obtaining a product lock, then re-check under the lock.
        artisan_actor(db, row, user, provider)
        lock_artisan_product(db, row.showcase_item_id)
        db.refresh(row)
        actor = artisan_actor(db, row, user, provider, hold=True)
        if action != "cancel" and actor != "provider":
            raise HTTPException(403, "Approved owning artisan provider required")
        old = row.reservation_status
        expiry = artisan_reservation_expiry(row)
        if action == "cancel" and old not in ARTISAN_ACTIVE_STATES:
            detail = (
                "Completed reservations cannot be cancelled"
                if old == "completed" else f"Reservation is already {old}"
            )
            raise HTTPException(invalid.status_code, detail)
        ref = get_artisan_booking_reference(db, row.id)
        if ref is None and action == "cancel":
            # Older native-only holds have no payment transactions: payments require a reference.
            ref = sync_artisan_booking_reference(db, row, changed_by=user.id)
        if ref is None or (
            ref.user_id != row.traveler_id
            or ref.provider_profile_id != row.artisan_listing.provider_profile_id
            or ref.booking_status != old
        ):
            raise HTTPException(409, "Reservation has no matching booking reference")
        if expiry is not None and expiry <= utc_now():
            if action != "complete" or ref.payment_status != "paid":
                raise invalid
        captured = artisan_capture_exists(db, ref.id)
        unpaid = ref.payment_status == "unpaid" and not captured
        if action == "accept" and old == "pending" and row.pickup_date is not None:
            item = db.get(ShowcaseItem, row.showcase_item_id, populate_existing=True)
            if (
                row.pickup_date < datetime.now(LOCAL).date()
                or row.quantity <= 0
                or item is None
                or item.artisan_listing_id != row.artisan_listing_id
            ):
                raise invalid
            new = "confirmed"
        elif action == "ready" and old == "confirmed":
            new = "ready_for_pickup"
        elif action == "cancel" and old in ARTISAN_ACTIVE_STATES:
            if not unpaid:
                raise HTTPException(409, "Paid reservations cannot be cancelled")
            new = "cancelled"
        elif action in ("cash", "complete") and old in ("confirmed", "ready_for_pickup"):
            if action == "cash" and (not unpaid or row.total_price <= 0):
                raise invalid
            if action == "complete" and row.pickup_date != datetime.now(LOCAL).date():
                raise invalid
            if action == "complete" and ref.payment_status not in ("paid", "unpaid"):
                raise invalid
            if action == "complete" and row.total_price > 0 and ref.payment_status != "paid":
                if not unpaid or not detailed:
                    raise invalid
            if unpaid and (row.total_price > 0 or action == "complete"):
                payment = db.execute(
                    update(BookingReference).where(
                        BookingReference.id == ref.id,
                        BookingReference.payment_status == "unpaid",
                        BookingReference.booking_status == old,
                    ).values(payment_status="paid").execution_options(
                        synchronize_session=False
                    )
                )
                if payment.rowcount != 1:
                    raise invalid
                if row.total_price > 0:
                    record_cash_payment_transaction(db, ref, row.traveler_id, row.total_price)
                db.refresh(ref)
            new = "completed" if action == "complete" else old
        else:
            raise invalid

        if new != old:
            updates = {"reservation_status": new, "updated_at": func.sysutcdatetime()}
            if new == "cancelled":
                updates.update(cancelled_by=user.id, cancellation_reason=reason)
            changed = db.execute(update(ArtisanReservation).where(
                ArtisanReservation.id == row.id,
                ArtisanReservation.reservation_status == old,
            ).values(**updates).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                raise invalid
            if new == "cancelled":
                restore_artisan_stock(db, row)
            db.refresh(row)
        ref.booking_status = new
        recipient = (
            row.artisan_listing.provider_profile.user_id if actor == "traveler"
            else row.traveler_id
        )
        if reason and new == "cancelled":
            record_booking_status_change(db, ref.id, old, new, user.id, notes=reason[:500])
            create_notification(
                db, recipient, f"Reservation {ref.booking_reference}", reason,
                "reservation_cancel",
            )
        else:
            save_event(db, ref, old, new, user.id, recipient, action)
        db.commit()
        return row, ref
    except Exception:
        db.rollback()
        raise


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


def reject_artisan_reservation(db, booking_id, provider, reason):
    reason = (reason or "").strip()
    if not 3 <= len(reason) <= 1000:
        raise HTTPException(422, "Provide a rejection reason of 3 to 1000 characters")

    row = db.get(ArtisanReservation, booking_id)
    if row is None:
        raise HTTPException(404, "Artisan reservation not found")
    if row.artisan_listing.provider_profile_id != provider.id:
        raise HTTPException(403, "You do not own this artisan reservation")

    try:
        lock_booking_resource(db, f"artisan_stock_{row.showcase_item_id}")
        if db.scalar(
            select(ProviderProfile.id).where(
                ProviderProfile.id == provider.id,
                ProviderProfile.user_id == provider.user_id,
                ProviderProfile.provider_type == "artisan",
                approved_provider(provider.id, hold=True),
            )
        ) is None:
            raise HTTPException(403, "Approved artisan provider access is required")

        result = db.execute(
            reservations(db, ARTISAN, provider_id=provider.id)
            .where(ArtisanReservation.id == booking_id)
            .execution_options(populate_existing=True)
        ).first()
        if result is None:
            raise HTTPException(409, "Reservation has no matching booking reference")
        row, ref = result
        if row.reservation_status != "pending" or ref.booking_status != "pending":
            raise HTTPException(409, "Only pending reservations can be rejected")
        if ref.payment_status != "unpaid" or artisan_capture_exists(db, ref.id):
            raise HTTPException(409, "Only unpaid reservations can be rejected")

        changed = db.execute(
            update(ArtisanReservation)
            .where(
                ArtisanReservation.id == booking_id,
                ArtisanReservation.reservation_status == "pending",
            )
            .values(
                reservation_status="rejected",
                rejected_by=provider.user_id,
                rejected_at=func.sysutcdatetime(),
                rejection_reason=reason,
                updated_at=func.sysutcdatetime(),
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise HTTPException(409, "Reservation status changed. Retry.")
        restore_artisan_stock(db, row)

        db.refresh(row)
        ref = sync_artisan_booking_reference(
            db, row, changed_by=provider.user_id, change_reason=reason[:500]
        )
        title = f"Artisan reservation {row.id} rejected"
        create_notification(
            db, row.traveler_id, title, reason, "artisan_reservation_rejected"
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return row, ref


def change_reservation(db, module, booking_id, user, action, reason, provider=None):
    if module == ARTISAN:
        if action == "reject" and provider:
            row, ref = reject_artisan_reservation(db, booking_id, provider, reason)
        else:
            row, ref = change_artisan_reservation(
                db, booking_id, user, action, reason, provider=provider
            )
        return reservation_payload(row, ref)

    owned = {"provider_id": provider.id} if provider else {"user_id": user.id}
    result = db.execute(
        reservations(db, module, **owned).where(MODELS[module].id == booking_id)
    ).first()
    if result is None:
        raise HTTPException(404, "Owned reservation not found")
    row, ref = result
    lock_guide(db, row.tour_guide_id)
    if (
        provider
        and db.scalar(
            select(ProviderProfile.id).where(
                ProviderProfile.id == provider.id,
                ProviderProfile.user_id == user.id,
                ProviderProfile.provider_type == "tour_guide",
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
    if expire_hold(db, row, ref, now):
        db.commit()
        raise HTTPException(409, "Tour checkout hold has expired")
    old = row.booking_status
    future = row.start_datetime > now
    next_status = old
    awaiting_payment = (
        row.payment_method == "online" and row.expires_at is not None
    )
    if (
        action == "accept"
        and provider
        and old == "pending"
        and future
        and not awaiting_payment
    ):
        require_availability(
            db, row.tour_guide_id, row.start_datetime, row.end_datetime
        )
        if row.payment_method == "online" and row.total_price > 0:
            row.expires_at = min(now + timedelta(minutes=30), row.start_datetime)
            row.payment_status = ref.payment_status = "pending"
        else:
            next_status = "confirmed"
    elif action == "reject" and provider and old == "pending" and not awaiting_payment:
        next_status = "rejected"
    elif (
        action == "cancel"
        and old in ("pending", "confirmed", "ready_for_pickup")
        and (
            ref.payment_status == "unpaid"
            or (awaiting_payment and ref.payment_status in ("pending", "failed"))
        )
        and future
        and row.payment_status in ("unpaid", "pending", "failed")
    ):
        if (
            (old == "confirmed" or awaiting_payment)
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
    elif (
        action == "cash"
        and provider
        and old in ("confirmed", "ready_for_pickup")
        and ref.payment_status == "unpaid"
    ):
        if row.payment_method != "pay_at_location":
            raise HTTPException(409, "This booking uses online payment")
        if ref.total_price <= 0 or cash_recorded(db, ref.id):
            raise HTTPException(409, "A positive outstanding cash balance is required")
        record_cash_payment_transaction(db, ref, row.traveler_id, ref.total_price)
        ref.payment_status = "paid"
        row.payment_status = "paid"
    elif (
        action == "complete"
        and provider
        and old in ("confirmed", "ready_for_pickup")
        and (ref.payment_status == "paid" or ref.total_price == 0)
        and row.end_datetime <= now
    ):
        next_status = "completed"
    else:
        raise HTTPException(409, "Action is unavailable for this reservation state")

    if next_status in ("cancelled", "rejected"):
        row.cancelled_by = user.id
        row.cancellation_reason = reason
        row.cancelled_at = now
    row.booking_status = next_status
    ref.booking_status = next_status
    recipient = (
        row.traveler_id
        if provider
        else db.get(ProviderProfile, ref.provider_profile_id).user_id
    )
    audit_old = "awaiting_payment" if awaiting_payment and old == "pending" else old
    audit_new = (
        "awaiting_payment"
        if action == "accept" and next_status == "pending"
        else next_status
    )
    save_event(db, ref, audit_old, audit_new, user.id, recipient, action)
    db.commit()
    return reservation_payload(row, ref)
