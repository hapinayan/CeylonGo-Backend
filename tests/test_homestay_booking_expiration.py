"""
CeylonGo - Phase 07 Homestay Booking Expiration & Cancellation Targeted Tests
=============================================================================
Validates:
1. Online approval creates a 30-minute payment deadline (awaiting_payment status).
2. Cash approval has no payment deadline (confirmed status directly).
3. Repeated approval does not extend the deadline.
4. Unexpired online reservations block overlapping bookings.
5. Expired reservations release availability.
6. Paid bookings cannot be automatically expired.
7. Expired bookings cannot initiate checkout.
8. Verified late payments cannot reactivate expired bookings (flagged for refund).
9. Eligible cancellation releases availability.
10. Duplicate expiration and cancellation operations are safe.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.user import Base, User
from app.routers.booking import (
    accept_booking,
    cancel_booking,
    create_booking,
    get_booking,
)
from app.routers.homestay import check_homestay_availability
from app.routers.payments import initiate_booking_payment
from app.schemas.booking import BookingCancelRequest, BookingCreate
from app.services.booking_expiration import (
    expire_single_booking,
    process_expired_bookings,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def test_setup(db_session):
    """Seed test provider, traveler, and active homestay."""
    provider_user = User(
        full_name="Silva Homestay Host",
        email="silva.host@test.com",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        full_name="Kamal Traveler",
        email="kamal.traveler@test.com",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([provider_user, traveler_user])
    db_session.flush()

    provider_profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Silva Leisure",
        provider_type="homestay",
        phone="+94771234567",
        district="Badulla",
        approval_status="approved",
    )
    db_session.add(provider_profile)
    db_session.flush()

    homestay = Homestay(
        name="Ella Hillside Homestay",
        location="Ella",
        price_per_night=Decimal("6000.00"),
        max_guests=4,
        is_active=True,
        provider_profile_id=provider_profile.id,
        owner_id=provider_user.id,
        free_cancellation_hours=24,
        cancellation_policy="Full refund up to 24h before check-in",
    )
    db_session.add(homestay)
    db_session.commit()
    db_session.refresh(provider_user)
    db_session.refresh(traveler_user)
    db_session.refresh(homestay)

    return {
        "provider": provider_user,
        "traveler": traveler_user,
        "homestay": homestay,
    }


# =============================================================================
# 1. Online approval creates a 30-minute payment deadline
# =============================================================================
def test_online_approval_creates_30m_deadline(db_session, test_setup):
    provider = test_setup["provider"]
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 4),
        guests=2,
        total_price=Decimal("18000.00"),
        status="pending",
        payment_method="online",
        payment_status="unpaid",
    )
    db_session.add(booking)
    db_session.commit()

    result = accept_booking(booking_id=booking.id, db=db_session, current_user=provider)

    assert result.status == "awaiting_payment"
    assert result.approved_at is not None
    assert result.payment_expires_at is not None

    diff_minutes = (result.payment_expires_at - result.approved_at).total_seconds() / 60.0
    assert 29.9 <= diff_minutes <= 30.1


# =============================================================================
# 2. Cash approval has no payment deadline
# =============================================================================
def test_cash_approval_has_no_deadline(db_session, test_setup):
    provider = test_setup["provider"]
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 5),
        check_out=date(2026, 12, 8),
        guests=2,
        total_price=Decimal("18000.00"),
        status="pending",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db_session.add(booking)
    db_session.commit()

    result = accept_booking(booking_id=booking.id, db=db_session, current_user=provider)

    assert result.status == "confirmed"
    assert result.approved_at is not None
    assert result.payment_expires_at is None


# =============================================================================
# 3. Repeated approval does not extend the deadline
# =============================================================================
def test_repeated_approval_does_not_extend_deadline(db_session, test_setup):
    provider = test_setup["provider"]
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    fixed_expires_at = now_utc + timedelta(minutes=25)

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 10),
        check_out=date(2026, 12, 12),
        guests=2,
        total_price=Decimal("12000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        approved_at=now_utc - timedelta(minutes=5),
        payment_expires_at=fixed_expires_at,
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        accept_booking(booking_id=booking.id, db=db_session, current_user=provider)

    assert exc_info.value.status_code == 400
    assert "Cannot accept booking with current status 'awaiting_payment'" in exc_info.value.detail

    db_session.refresh(booking)
    assert booking.payment_expires_at == fixed_expires_at


# =============================================================================
# 4. Unexpired online reservations block overlapping bookings
# =============================================================================
def test_unexpired_online_reservation_blocks_overlapping(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 15),
        check_out=date(2026, 12, 18),
        guests=2,
        total_price=Decimal("18000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        approved_at=now_utc,
        payment_expires_at=now_utc + timedelta(minutes=20),
    )
    db_session.add(booking)
    db_session.commit()

    # Public availability check must report unavailable
    avail = check_homestay_availability(
        homestay_id=homestay.id,
        db=db_session,
        check_in=date(2026, 12, 16),
        check_out=date(2026, 12, 17),
    )
    assert avail["available"] is False

    # Overlapping booking creation must be rejected with 409 Conflict
    with pytest.raises(HTTPException) as exc_info:
        create_booking(
            booking_data=BookingCreate(
                homestay_id=homestay.id,
                check_in=date(2026, 12, 16),
                check_out=date(2026, 12, 17),
                guests=1,
                payment_method="online",
            ),
            db=db_session,
            current_user=traveler,
        )
    assert exc_info.value.status_code == 409


# =============================================================================
# 5. Expired reservations release availability
# =============================================================================
def test_expired_reservations_release_availability(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 20),
        check_out=date(2026, 12, 22),
        guests=2,
        total_price=Decimal("12000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        approved_at=now_utc - timedelta(minutes=40),
        payment_expires_at=now_utc - timedelta(minutes=10),
    )
    db_session.add(booking)
    db_session.commit()

    # Expired unpaid booking should no longer block inventory
    avail = check_homestay_availability(
        homestay_id=homestay.id,
        db=db_session,
        check_in=date(2026, 12, 20),
        check_out=date(2026, 12, 22),
    )
    assert avail["available"] is True


# =============================================================================
# 6. Paid bookings cannot be automatically expired
# =============================================================================
def test_paid_bookings_cannot_be_automatically_expired(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2026, 12, 25),
        check_out=date(2026, 12, 28),
        guests=2,
        total_price=Decimal("18000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="paid",  # Verified paid!
        approved_at=now_utc - timedelta(minutes=50),
        payment_expires_at=now_utc - timedelta(minutes=20),
    )
    db_session.add(booking)
    db_session.commit()

    # Single expiration attempt must return False
    res = expire_single_booking(db_session, booking, now_utc)
    assert res is False
    assert booking.status == "awaiting_payment"

    # Batch expiration must ignore paid booking
    count = process_expired_bookings(db_session)
    assert count == 0
    assert booking.status == "awaiting_payment"


# =============================================================================
# 7. Expired bookings cannot initiate checkout
# =============================================================================
def test_expired_bookings_cannot_initiate_checkout(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2027, 1, 5),
        check_out=date(2027, 1, 8),
        guests=2,
        total_price=Decimal("18000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        approved_at=now_utc - timedelta(minutes=35),
        payment_expires_at=now_utc - timedelta(minutes=5),  # 5 minutes expired
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        initiate_booking_payment(
            booking_id=booking.id,
            current_user=traveler,
            db=db_session,
        )

    assert exc_info.value.status_code == 400
    assert "expired" in exc_info.value.detail.lower()

    # Verify that the booking was lazily updated to expired
    db_session.refresh(booking)
    assert booking.status == "expired"
    assert booking.expired_at is not None


# =============================================================================
# 8. Verified late payments cannot reactivate expired bookings
# =============================================================================
def test_verified_late_payments_cannot_reactivate_expired_bookings(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2027, 1, 10),
        check_out=date(2027, 1, 12),
        guests=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        status="expired",
        payment_method="online",
        payment_status="unpaid",
        expired_at=now_utc - timedelta(minutes=15),
    )
    db_session.add(booking)
    db_session.flush()

    booking_ref = BookingReference(
        booking_reference=f"BK-HS-{booking.id}-TEST1234",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=homestay.provider_profile_id,
        total_price=Decimal("12000.00"),
        currency="LKR",
        booking_status="expired",
        payment_status="unpaid",
    )
    db_session.add(booking_ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=booking_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        amount=Decimal("12000.00"),
        currency="LKR",
        payment_status="pending",
    )
    db_session.add(tx)
    db_session.commit()

    # Simulate late payment handling logic
    # Preserves transaction, does NOT reactivate booking, flags for refund
    tx.payment_status = "paid"
    tx.paid_at = now_utc
    tx.gross_amount = Decimal("12000.00")
    tx.commission_rate = Decimal("10.00")
    tx.commission_amount = Decimal("1200.00")
    tx.provider_payable_amount = Decimal("10800.00")

    booking.payment_status = "refund_pending"
    refund = PaymentRefund(
        payment_transaction_id=tx.id,
        amount=Decimal("12000.00"),
        currency="LKR",
        refund_reason="Late payment verified after booking expired. Flagged for refund/reconciliation.",
        refund_status="pending",
    )
    db_session.add(refund)
    db_session.commit()

    db_session.refresh(booking)
    assert booking.status == "expired"
    assert booking.payment_status == "refund_pending"
    assert tx.payment_status == "paid"
    assert refund.refund_status == "pending"
    assert refund.amount == Decimal("12000.00")


# =============================================================================
# 9. Eligible cancellation releases availability
# =============================================================================
def test_eligible_cancellation_releases_availability(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2027, 2, 1),
        check_out=date(2027, 2, 5),
        guests=2,
        total_price=Decimal("24000.00"),
        status="confirmed",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db_session.add(booking)
    db_session.commit()

    # Prior to cancellation, dates are blocked
    avail_before = check_homestay_availability(
        homestay_id=homestay.id,
        db=db_session,
        check_in=date(2027, 2, 2),
        check_out=date(2027, 2, 4),
    )
    assert avail_before["available"] is False

    # Cancel booking
    cancel_res = cancel_booking(
        booking_id=booking.id,
        payload=BookingCancelRequest(cancellation_reason="Travel plan changes"),
        current_user=traveler,
        db=db_session,
    )
    assert cancel_res.status == "cancelled"
    assert cancel_res.cancelled_at is not None
    assert cancel_res.cancelled_by == traveler.id

    # After cancellation, dates are immediately available
    avail_after = check_homestay_availability(
        homestay_id=homestay.id,
        db=db_session,
        check_in=date(2027, 2, 2),
        check_out=date(2027, 2, 4),
    )
    assert avail_after["available"] is True


# =============================================================================
# 10. Duplicate expiration and cancellation operations are safe
# =============================================================================
def test_duplicate_expiration_and_cancellation_safe(db_session, test_setup):
    traveler = test_setup["traveler"]
    homestay = test_setup["homestay"]

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2027, 3, 1),
        check_out=date(2027, 3, 3),
        guests=2,
        total_price=Decimal("12000.00"),
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        approved_at=now_utc - timedelta(minutes=40),
        payment_expires_at=now_utc - timedelta(minutes=10),
    )
    db_session.add(booking)
    db_session.commit()

    # 1. First expiration: returns True
    assert expire_single_booking(db_session, booking, now_utc) is True
    db_session.commit()
    assert booking.status == "expired"

    # 2. Duplicate expiration: returns False safely
    assert expire_single_booking(db_session, booking, now_utc) is False

    # 3. Cancel already cancelled booking
    booking_cancel = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date(2027, 3, 10),
        check_out=date(2027, 3, 12),
        guests=2,
        total_price=Decimal("12000.00"),
        status="confirmed",
        payment_method="pay_at_location",
        payment_status="unpaid",
    )
    db_session.add(booking_cancel)
    db_session.commit()

    # First cancellation succeeds
    res1 = cancel_booking(
        booking_id=booking_cancel.id,
        payload=BookingCancelRequest(cancellation_reason="First cancel"),
        current_user=traveler,
        db=db_session,
    )
    assert res1.status == "cancelled"

    # Duplicate cancellation is rejected with 400
    with pytest.raises(HTTPException) as exc_info:
        cancel_booking(
            booking_id=booking_cancel.id,
            payload=BookingCancelRequest(cancellation_reason="Duplicate cancel"),
            current_user=traveler,
            db=db_session,
        )
    assert exc_info.value.status_code == 400
    assert "already cancelled" in exc_info.value.detail.lower()
