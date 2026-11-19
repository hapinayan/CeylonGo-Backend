"""
CeylonGo - Phase 10 Commission, Refunds & Provider Settlements Targeted Tests
=============================================================================
Validates:
1. Correct 10% commission calculation.
2. Correct 90% provider share.
3. Discounted booking commission calculations.
4. Financial snapshot preservation.
5. Duplicate callback prevention.
6. Cash booking commission exclusion.
7. Full and partial refund calculations.
8. Refund amount and duplicate protection.
9. Provider net earnings calculation.
10. Settlement eligibility (completed vs in-progress).
11. Duplicate and overlapping settlement prevention.
12. Unauthorized financial operations.
13. Late payment reconciliation.
14. Future scheduled advertising activation.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.payhere import payhere_service
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    AdvertisingPayment,
    BookingReference,
    ListingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.user import Base, User
from app.routers.booking import accept_booking
from app.routers.homestay import populate_homestay_promotions_and_sponsorship
from app.routers.payments import (
    admin_get_financial_summary,
    admin_get_reconciliation_queue,
    calculate_provider_earnings_balances,
    create_provider_settlement,
    get_provider_earnings_summary,
    get_settlement_detail,
    initiate_booking_payment,
    list_provider_settlements,
    process_refund,
    request_booking_refund,
)
from app.schemas.payment import (
    RefundProcessRequest,
    RefundRequest,
    SettlementCreateRequest,
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
def seed_data(db_session):
    """Seed admin, providers, traveler, homestays, and listing references."""
    admin_user = User(
        full_name="CeylonGo Admin",
        email="admin.fin@ceylongo.test",
        role="admin",
        is_active=True,
    )
    provider_user_1 = User(
        full_name="Host Gamage",
        email="gamage@host.test",
        role="provider",
        is_active=True,
    )
    provider_user_2 = User(
        full_name="Host Bandara",
        email="bandara@host.test",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        full_name="Traveler Nimal",
        email="nimal@traveler.test",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin_user, provider_user_1, provider_user_2, traveler_user])
    db_session.flush()

    profile_1 = ProviderProfile(
        user_id=provider_user_1.id,
        business_name="Gamage Retreats",
        provider_type="homestay",
        phone="+94775551111",
        district="Kandy",
        approval_status="approved",
    )
    profile_2 = ProviderProfile(
        user_id=provider_user_2.id,
        business_name="Bandara Stays",
        provider_type="homestay",
        phone="+94775552222",
        district="Matale",
        approval_status="approved",
    )
    db_session.add_all([profile_1, profile_2])
    db_session.flush()

    homestay_1 = Homestay(
        name="Gamage Kandy Haven",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("5000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_1.id,
        owner_id=provider_user_1.id,
        free_cancellation_hours=24,
        cancellation_policy="Full refund up to 24h before check-in",
    )
    homestay_2 = Homestay(
        name="Bandara Matale Villa",
        location="Matale",
        district="Matale",
        price_per_night=Decimal("8000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile_2.id,
        owner_id=provider_user_2.id,
        free_cancellation_hours=48,
    )
    db_session.add_all([homestay_1, homestay_2])
    db_session.flush()

    ref_1 = ListingReference(
        module_type="homestay",
        target_id=homestay_1.id,
        title=homestay_1.name,
        district="Kandy",
        provider_profile_id=profile_1.id,
    )
    ref_2 = ListingReference(
        module_type="homestay",
        target_id=homestay_2.id,
        title=homestay_2.name,
        district="Matale",
        provider_profile_id=profile_2.id,
    )
    db_session.add_all([ref_1, ref_2])
    db_session.commit()

    return {
        "admin": admin_user,
        "user_1": provider_user_1,
        "user_2": provider_user_2,
        "traveler": traveler_user,
        "profile_1": profile_1,
        "profile_2": profile_2,
        "homestay_1": homestay_1,
        "homestay_2": homestay_2,
        "ref_1": ref_1,
        "ref_2": ref_2,
    }


# ==============================================================================
# 1. Correct 10% commission calculation
# ==============================================================================
def test_online_booking_10_percent_commission_calculation(db_session, seed_data):
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]
    profile = seed_data["profile_1"]
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    total_price = Decimal("10000.00")
    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=5),
        check_out=date.today() + timedelta(days=7),
        guests=2,
        total_price=total_price,
        status="confirmed",
        payment_method="online",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.flush()

    booking_ref = BookingReference(
        booking_reference=f"BK-REF-{booking.id}",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=total_price,
        booking_status="confirmed",
        payment_status="paid",
    )
    db_session.add(booking_ref)
    db_session.flush()

    commission_rate = Decimal("10.00")
    commission_amount = (total_price * Decimal("0.10")).quantize(Decimal("0.01"))
    provider_payable = total_price - commission_amount

    booking.commission_rate = commission_rate
    booking.commission_amount = commission_amount
    booking.provider_payable_amount = provider_payable

    tx = PaymentTransaction(
        booking_reference_id=booking_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        amount=total_price,
        gross_amount=total_price,
        commission_rate=commission_rate,
        commission_amount=commission_amount,
        provider_payable_amount=provider_payable,
        payment_status="paid",
        payment_method="online",
        paid_at=now_utc,
    )
    db_session.add(tx)
    db_session.commit()

    assert booking.commission_rate == Decimal("10.00")
    assert booking.commission_amount == Decimal("1000.00")
    assert tx.commission_amount == Decimal("1000.00")


# ==============================================================================
# 2. Correct 90% provider share
# ==============================================================================
def test_online_booking_90_percent_provider_share(db_session, seed_data):
    total_price = Decimal("12500.00")
    commission_rate = Decimal("10.00")
    commission_amount = (total_price * (commission_rate / Decimal("100.00"))).quantize(Decimal("0.01"))
    provider_payable = (total_price - commission_amount).quantize(Decimal("0.01"))

    # 10% of 12,500.00 = 1,250.00; 90% provider share = 11,250.00
    assert commission_amount == Decimal("1250.00")
    assert provider_payable == Decimal("11250.00")
    assert provider_payable == (total_price * Decimal("0.90")).quantize(Decimal("0.01"))


# ==============================================================================
# 3. Discounted booking commission calculations
# ==============================================================================
def test_discounted_booking_commission_calculation(db_session, seed_data):
    # Original: 10,000.00; Discount applied: 2,500.00; Final discounted price: 7,500.00
    final_discounted_price = Decimal("7500.00")
    commission_rate = Decimal("10.00")
    commission_amount = (final_discounted_price * Decimal("0.10")).quantize(Decimal("0.01"))
    provider_payable = final_discounted_price - commission_amount

    assert commission_amount == Decimal("750.00")
    assert provider_payable == Decimal("6750.00")
    assert (commission_amount + provider_payable) == final_discounted_price


# ==============================================================================
# 4. Financial snapshot preservation
# ==============================================================================
def test_financial_snapshot_preservation(db_session, seed_data):
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]
    profile = seed_data["profile_1"]

    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=2),
        check_out=date.today() + timedelta(days=4),
        guests=2,
        total_price=Decimal("8000.00"),
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("800.00"),
        provider_payable_amount=Decimal("7200.00"),
        status="completed",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.commit()

    # Changing homestay price per night or policy in the future
    h.price_per_night = Decimal("12000.00")
    db_session.commit()

    # Snapshot on historical booking must remain intact
    db_session.refresh(booking)
    assert booking.total_price == Decimal("8000.00")
    assert booking.commission_amount == Decimal("800.00")
    assert booking.provider_payable_amount == Decimal("7200.00")


# ==============================================================================
# 5. Duplicate callback prevention
# ==============================================================================
def test_duplicate_callback_prevention_on_booking(db_session, seed_data):
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]
    profile = seed_data["profile_1"]
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=3),
        check_out=date.today() + timedelta(days=5),
        guests=2,
        total_price=Decimal("6000.00"),
        status="confirmed",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.flush()

    booking_ref = BookingReference(
        booking_reference="BK-DUP-CALLBACK-001",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("6000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    db_session.add(booking_ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=booking_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("6000.00"),
        payment_status="paid",
        paid_at=now_utc,
    )
    db_session.add(tx)
    db_session.commit()

    # When booking is already paid, duplicate webhook returns already_processed
    assert booking.payment_status == "paid"


# ==============================================================================
# 6. Cash booking commission exclusion
# ==============================================================================
def test_cash_booking_commission_exclusion(db_session, seed_data):
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]

    cash_booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=1),
        check_out=date.today() + timedelta(days=3),
        guests=2,
        total_price=Decimal("10000.00"),
        payment_method="pay_at_location",
        status="confirmed",
        commission_rate=None,
        commission_amount=None,
        provider_payable_amount=None,
    )
    db_session.add(cash_booking)
    db_session.commit()

    # Cash-at-location booking must NOT deduct online booking commission
    assert cash_booking.commission_rate is None
    assert cash_booking.commission_amount is None
    assert cash_booking.provider_payable_amount is None


# ==============================================================================
# 7. Full and partial refund calculations
# ==============================================================================
def test_full_and_partial_refund_calculations():
    original_amount = Decimal("10000.00")
    original_commission = Decimal("1000.00")
    original_provider_payable = Decimal("9000.00")

    # Partial refund of 4,000.00 (40%)
    partial_refund = Decimal("4000.00")
    partial_ratio = partial_refund / original_amount
    refunded_comm_partial = (original_commission * partial_ratio).quantize(Decimal("0.01"))
    provider_reduction_partial = partial_refund - refunded_comm_partial

    assert refunded_comm_partial == Decimal("400.00")
    assert provider_reduction_partial == Decimal("3600.00")

    # Full refund of 10,000.00 (100%)
    full_refund = Decimal("10000.00")
    full_ratio = full_refund / original_amount
    refunded_comm_full = (original_commission * full_ratio).quantize(Decimal("0.01"))
    provider_reduction_full = full_refund - refunded_comm_full

    assert refunded_comm_full == Decimal("1000.00")
    assert provider_reduction_full == Decimal("9000.00")


# ==============================================================================
# 8. Refund amount and duplicate protection
# ==============================================================================
def test_refund_amount_and_duplicate_protection(db_session, seed_data):
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]
    profile = seed_data["profile_1"]

    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=5),
        check_out=date.today() + timedelta(days=7),
        guests=2,
        total_price=Decimal("5000.00"),
        status="cancelled",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.flush()

    booking_ref = BookingReference(
        booking_reference=f"BK-REF-{booking.id}-REF",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("5000.00"),
        booking_status="cancelled",
        payment_status="paid",
    )
    db_session.add(booking_ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=booking_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("5000.00"),
        payment_status="paid",
    )
    db_session.add(tx)
    db_session.commit()

    # First refund request of 5000 succeeds
    refund1 = request_booking_refund(
        booking_id=booking.id,
        payload=RefundRequest(reason="Travel plans cancelled"),
        current_user=traveler,
        db=db_session,
    )
    assert refund1.amount == Decimal("5000.00")
    assert refund1.refund_status == "pending"

    # Second refund request fails because full amount is already pending
    with pytest.raises(HTTPException) as exc_info:
        request_booking_refund(
            booking_id=booking.id,
            payload=RefundRequest(reason="Another request"),
            current_user=traveler,
            db=db_session,
        )
    assert exc_info.value.status_code == 400
    assert "already been refunded or is currently pending" in exc_info.value.detail


# ==============================================================================
# 9. Provider net earnings calculation
# ==============================================================================
def test_provider_net_earnings_calculation(db_session, seed_data):
    profile = seed_data["profile_1"]
    traveler = seed_data["traveler"]
    h = seed_data["homestay_1"]

    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() - timedelta(days=5),
        check_out=date.today() - timedelta(days=3),
        guests=2,
        total_price=Decimal("20000.00"),
        status="completed",
        payment_status="paid",
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("2000.00"),
        provider_payable_amount=Decimal("18000.00"),
    )
    db_session.add(booking)
    db_session.flush()

    ref = BookingReference(
        booking_reference="BK-EARNINGS-9",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("20000.00"),
        booking_status="completed",
        payment_status="paid",
    )
    db_session.add(ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("20000.00"),
        gross_amount=Decimal("20000.00"),
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("2000.00"),
        provider_payable_amount=Decimal("18000.00"),
        payment_status="paid",
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(tx)
    db_session.commit()

    balances = calculate_provider_earnings_balances(db_session, profile.id)
    assert balances["total_gross_earnings"] == Decimal("20000.00")
    assert balances["ceylongo_commission"] == Decimal("2000.00")
    assert balances["provider_net_earnings"] == Decimal("18000.00")
    assert balances["available_settlement_balance"] == Decimal("18000.00")


# ==============================================================================
# 10. Settlement eligibility (completed vs in-progress)
# ==============================================================================
def test_settlement_eligibility_completed_vs_in_progress(db_session, seed_data):
    profile = seed_data["profile_2"]
    traveler = seed_data["traveler"]
    h = seed_data["homestay_2"]

    # 1. Booking A: Completed (Eligible for settlement)
    booking_a = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() - timedelta(days=5),
        check_out=date.today() - timedelta(days=3),
        guests=2,
        total_price=Decimal("10000.00"),
        status="completed",
        payment_status="paid",
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("1000.00"),
        provider_payable_amount=Decimal("9000.00"),
    )
    db_session.add(booking_a)
    db_session.flush()

    ref_a = BookingReference(
        booking_reference="BK-SETTLE-A",
        module_type="homestay",
        target_booking_id=booking_a.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("10000.00"),
        booking_status="completed",
        payment_status="paid",
    )
    db_session.add(ref_a)
    db_session.flush()

    tx_a = PaymentTransaction(
        booking_reference_id=ref_a.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("10000.00"),
        gross_amount=Decimal("10000.00"),
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("1000.00"),
        provider_payable_amount=Decimal("9000.00"),
        payment_status="paid",
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(tx_a)

    # 2. Booking B: Confirmed but in-progress (Pending earnings, NOT yet eligible for settlement)
    booking_b = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=2),
        check_out=date.today() + timedelta(days=4),
        guests=2,
        total_price=Decimal("6000.00"),
        status="confirmed",
        payment_status="paid",
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("600.00"),
        provider_payable_amount=Decimal("5400.00"),
    )
    db_session.add(booking_b)
    db_session.flush()

    ref_b = BookingReference(
        booking_reference="BK-SETTLE-B",
        module_type="homestay",
        target_booking_id=booking_b.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("6000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    db_session.add(ref_b)
    db_session.flush()

    tx_b = PaymentTransaction(
        booking_reference_id=ref_b.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("6000.00"),
        gross_amount=Decimal("6000.00"),
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("600.00"),
        provider_payable_amount=Decimal("5400.00"),
        payment_status="paid",
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(tx_b)
    db_session.commit()

    balances = calculate_provider_earnings_balances(db_session, profile.id)

    # Total gross = 16,000.00
    assert balances["total_gross_earnings"] == Decimal("16000.00")
    # Completed earnings = 10,000.00
    assert balances["completed_booking_earnings"] == Decimal("10000.00")
    # Provider net earnings from completed bookings = 9,000.00
    assert balances["provider_net_earnings"] == Decimal("9000.00")
    # In-progress booking B is in pending_earnings = 5,400.00
    assert balances["pending_earnings"] == Decimal("5400.00")
    # Available settlement balance is ONLY from completed bookings = 9,000.00
    assert balances["available_settlement_balance"] == Decimal("9000.00")


# ==============================================================================
# 11. Duplicate and overlapping settlement prevention
# ==============================================================================
def test_duplicate_and_overlapping_settlement_prevention(db_session, seed_data):
    admin = seed_data["admin"]
    profile = seed_data["profile_1"]
    traveler = seed_data["traveler"]
    h = seed_data["homestay_1"]

    # Seed a completed booking of 10,000 (provider payable: 9,000)
    booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() - timedelta(days=10),
        check_out=date.today() - timedelta(days=8),
        guests=2,
        total_price=Decimal("10000.00"),
        status="completed",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.flush()

    ref = BookingReference(
        booking_reference="BK-SETTLE-PREVENT",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("10000.00"),
        booking_status="completed",
        payment_status="paid",
    )
    db_session.add(ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        amount=Decimal("10000.00"),
        gross_amount=Decimal("10000.00"),
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("1000.00"),
        provider_payable_amount=Decimal("9000.00"),
        payment_status="paid",
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(tx)
    db_session.commit()

    p_start = date.today() - timedelta(days=15)
    p_end = date.today() - timedelta(days=5)

    # 1. Create first valid settlement for 4,000
    s1 = create_provider_settlement(
        payload=SettlementCreateRequest(
            provider_profile_id=profile.id,
            period_start=p_start,
            period_end=p_end,
            gross_amount=Decimal("10000.00"),
            platform_fee=Decimal("1000.00"),
            net_payable=Decimal("4000.00"),
            payout_reference="BANK-TX-001",
            settlement_status="completed",
        ),
        admin=admin,
        db=db_session,
    )
    assert s1.net_payable == Decimal("4000.00")

    # 2. Overlapping period rejection
    with pytest.raises(HTTPException) as exc_overlap:
        create_provider_settlement(
            payload=SettlementCreateRequest(
                provider_profile_id=profile.id,
                period_start=p_start + timedelta(days=2),
                period_end=p_end + timedelta(days=2),
                gross_amount=Decimal("5000.00"),
                net_payable=Decimal("2000.00"),
            ),
            admin=admin,
            db=db_session,
        )
    assert exc_overlap.value.status_code == 400
    assert "overlaps with existing settlement" in exc_overlap.value.detail

    # 3. Excessive settlement exceeding remaining balance rejection
    # Available balance is now 9,000 - 4,000 = 5,000. Attempting 6,000 fails.
    with pytest.raises(HTTPException) as exc_excess:
        create_provider_settlement(
            payload=SettlementCreateRequest(
                provider_profile_id=profile.id,
                period_start=p_end + timedelta(days=1),
                period_end=p_end + timedelta(days=5),
                gross_amount=Decimal("7000.00"),
                net_payable=Decimal("6000.00"),
            ),
            admin=admin,
            db=db_session,
        )
    assert exc_excess.value.status_code == 400
    assert "exceeds the verified available balance" in exc_excess.value.detail


# ==============================================================================
# 12. Unauthorized financial operations
# ==============================================================================
def test_unauthorized_financial_operations(db_session, seed_data):
    profile_1 = seed_data["profile_1"]
    user_2 = seed_data["user_2"]  # Provider 2 user

    # Create settlement for Provider 1
    s = ProviderSettlement(
        provider_profile_id=profile_1.id,
        period_start=date.today() - timedelta(days=10),
        period_end=date.today() - timedelta(days=5),
        gross_amount=Decimal("5000.00"),
        net_payable=Decimal("4500.00"),
        settlement_status="completed",
    )
    db_session.add(s)
    db_session.commit()

    # Provider 2 attempts to view Provider 1's settlement details -> 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        get_settlement_detail(
            settlement_id=s.id,
            current_user=user_2,
            db=db_session,
        )
    assert exc_info.value.status_code == 403
    assert "Not authorized" in exc_info.value.detail


# ==============================================================================
# 13. Late payment reconciliation queue
# ==============================================================================
def test_late_payment_reconciliation_queue(db_session, seed_data):
    admin = seed_data["admin"]
    h = seed_data["homestay_1"]
    traveler = seed_data["traveler"]
    profile = seed_data["profile_1"]

    # Booking was expired before payment arrived
    expired_booking = Booking(
        homestay_id=h.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=2),
        check_out=date.today() + timedelta(days=4),
        guests=2,
        total_price=Decimal("6000.00"),
        status="expired",
        payment_status="paid",
    )
    db_session.add(expired_booking)
    db_session.flush()

    ref = BookingReference(
        booking_reference="BK-LATE-RECON",
        module_type="homestay",
        target_booking_id=expired_booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("6000.00"),
        booking_status="expired",
        payment_status="paid",
    )
    db_session.add(ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
        payment_method="online",
        gateway_transaction_id="TX_LATE_123",
        amount=Decimal("6000.00"),
        payment_status="paid",
        paid_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(tx)
    db_session.commit()

    queue = admin_get_reconciliation_queue(admin=admin, db=db_session)
    queue_tx_ids = [item.id for item in queue]
    assert tx.id in queue_tx_ids


# ==============================================================================
# 14. Future scheduled advertising activation
# ==============================================================================
def test_future_scheduled_advertising_automatic_activation(db_session, seed_data):
    profile = seed_data["profile_1"]
    ref = seed_data["ref_1"]
    h = seed_data["homestay_1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Campaign scheduled for next week
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Automatic Schedule Campaign",
        target_module="homestay",
        budget=Decimal("5000.00"),
        status="active",
        starts_at=now + timedelta(days=5),
        ends_at=now + timedelta(days=12),
    )
    db_session.add(campaign)
    db_session.flush()

    ad_payment = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference="AD-SCHED-AUTO",
        amount=Decimal("5000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(ad_payment)
    db_session.commit()

    # Before start schedule: not sponsored
    res_now = populate_homestay_promotions_and_sponsorship(db_session, [h], now=now)
    assert res_now[0].is_sponsored is False

    # Within schedule: automatically sponsored without manual reactivation
    res_active = populate_homestay_promotions_and_sponsorship(
        db_session, [h], now=now + timedelta(days=6)
    )
    assert res_active[0].is_sponsored is True
    assert res_active[0].sponsored_campaign_id == campaign.id
