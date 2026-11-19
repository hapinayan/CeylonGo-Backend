from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy.orm import Session

from app.core.booking_transactions import capture_status
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    PaymentTransaction,
)


def sync_homestay_listing_reference(db: Session, homestay: Homestay) -> ListingReference | None:
    """Synchronize an approved homestay listing with the central listing_references table."""
    if homestay.id is None:
        return None

    provider_profile_id = homestay.provider_profile_id
    if provider_profile_id is None and homestay.owner_id is not None:
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == homestay.owner_id)
            .first()
        )
        if profile is not None:
            provider_profile_id = profile.id

    is_ref_active = bool(homestay.is_active and homestay.approval_status == "approved")
    district = homestay.district or homestay.location or "General"
    city = homestay.city or homestay.location

    ref = (
        db.query(ListingReference)
        .filter(
            ListingReference.module_type == "homestay",
            ListingReference.target_id == homestay.id,
        )
        .first()
    )

    if ref is None:
        if is_ref_active and provider_profile_id is not None:
            ref = ListingReference(
                module_type="homestay",
                target_id=homestay.id,
                provider_profile_id=provider_profile_id,
                title=homestay.name,
                district=district,
                city=city,
                latitude=homestay.latitude,
                longitude=homestay.longitude,
                is_active=is_ref_active,
            )
            db.add(ref)
    else:
        ref.title = homestay.name
        ref.district = district
        ref.city = city
        ref.latitude = homestay.latitude
        ref.longitude = homestay.longitude
        ref.is_active = is_ref_active
        if provider_profile_id is not None:
            ref.provider_profile_id = provider_profile_id

    return ref


def sync_homestay_booking_reference(
    db: Session,
    booking: Booking,
    homestay: Homestay,
) -> BookingReference | None:
    """Synchronize a homestay booking with the central booking_references table."""
    if booking.id is None:
        return None

    provider_profile_id = homestay.provider_profile_id
    if provider_profile_id is None and homestay.owner_id is not None:
        profile = (
            db.query(ProviderProfile)
            .filter(ProviderProfile.user_id == homestay.owner_id)
            .first()
        )
        if profile is not None:
            provider_profile_id = profile.id

    if provider_profile_id is None:
        return None

    ref = (
        db.query(BookingReference)
        .filter(
            BookingReference.module_type == "homestay",
            BookingReference.target_booking_id == booking.id,
        )
        .first()
    )

    if ref is None:
        ref_code = f"BK-HS-{booking.id}-{uuid.uuid4().hex[:8].upper()}"
        ref = BookingReference(
            booking_reference=ref_code,
            module_type="homestay",
            target_booking_id=booking.id,
            user_id=booking.user_id,
            provider_profile_id=provider_profile_id,
            total_price=booking.total_price,
            currency=booking.currency or "LKR",
            booking_status=booking.status,
            payment_status=booking.payment_status or "unpaid",
        )
        db.add(ref)
        db.flush()
    else:
        ref.total_price = booking.total_price
        ref.booking_status = booking.status
        ref.payment_status = booking.payment_status or "unpaid"
        if booking.currency:
            ref.currency = booking.currency

    return ref


def record_booking_status_change(
    db: Session,
    booking_reference_id: int,
    from_status: str | None,
    to_status: str,
    changed_by: int | None,
    notes: str | None = None,
) -> BookingStatusHistory:
    """Record an entry in the centralized booking_status_history audit table."""
    history_entry = BookingStatusHistory(
        booking_reference_id=booking_reference_id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        notes=notes,
    )
    db.add(history_entry)
    return history_entry


def record_cash_payment_transaction(
    db: Session,
    booking_reference: BookingReference,
    user_id: int,
    amount: Decimal,
    notes: str | None = None,
) -> PaymentTransaction:
    """Record a cash payment transaction in the shared payment_transactions table."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    tx_code = f"CASH-{booking_reference.id}-{uuid.uuid4().hex[:8].upper()}"
    tx = PaymentTransaction(
        booking_reference_id=booking_reference.id,
        user_id=user_id,
        gateway_name="cash_on_arrival",
        gateway_transaction_id=tx_code,
        amount=amount,
        currency=booking_reference.currency or "LKR",
        payment_status=capture_status(db),
        payment_method="cash",
        gateway_response_payload=notes or "Cash received at property",
        paid_at=now,
    )
    db.add(tx)
    return tx
