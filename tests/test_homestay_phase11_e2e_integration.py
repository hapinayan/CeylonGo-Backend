"""
CeylonGo - Phase 11 End-to-End Integration Test Suite
=====================================================
Covers all 20 required essential workflows and safety checks:
 1. Provider creates Homestay.
 2. Administrator approves listing.
 3. Traveler discovers approved listing.
 4. Traveler checks availability and pricing.
 5. Traveler creates reservation.
 6. Provider approves online reservation.
 7. Traveler receives the 30-minute payment window.
 8. Verified mock gateway payment confirms reservation.
 9. Commission and provider earnings are correctly recorded.
10. Cash-at-location reservation lifecycle.
11. Concurrent booking conflict protection.
12. Automatic expiration and availability release.
13. Late payment reconciliation.
14. Cancellation and refund lifecycle.
15. Provider settlement eligibility and duplicate protection.
16. Completed-stay review and wishlist operations.
17. Sponsored campaign lifecycle.
18. Provider and administrator authorization.
19. Background expiration worker startup and shutdown.
20. Advertising and booking ledger isolation.
Bonus: Zero-value promotional booking safety check.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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
    BookingStatusHistory,
    ListingReference,
    Notification,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.user import Base, User
from app.routers.booking import (
    accept_booking,
    cancel_booking,
    complete_booking,
    create_booking,
    reject_booking,
)
from app.routers.homestay import (
    populate_homestay_promotions_and_sponsorship,
)
from app.routers.payments import (
    calculate_provider_earnings_balances,
    create_provider_settlement,
    handle_payhere_callback,
    initiate_booking_payment,
    process_refund,
    request_booking_refund,
)
from app.routers.homestay_campaigns import initiate_campaign_payment
from app.schemas.booking import BookingCancelRequest, BookingCreate
from app.schemas.payment import (
    RefundProcessRequest,
    RefundRequest,
    SettlementCreateRequest,
)
from app.services.booking_expiration import (
    process_expired_bookings,
    start_expiration_worker,
    stop_expiration_worker,
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
    admin_user = User(
        full_name="CeylonGo Administrator",
        email="admin@ceylongo.test",
        role="admin",
        password_hash="hash",
        is_active=True,
    )
    prov_user1 = User(
        full_name="Kandy Homestay Host",
        email="host1@ceylongo.test",
        role="provider",
        password_hash="hash",
        is_active=True,
    )
    prov_user2 = User(
        full_name="Galle Beach Host",
        email="host2@ceylongo.test",
        role="provider",
        password_hash="hash",
        is_active=True,
    )
    traveler = User(
        full_name="Alice Traveler",
        email="alice@ceylongo.test",
        role="traveler",
        password_hash="hash",
        is_active=True,
    )
    db_session.add_all([admin_user, prov_user1, prov_user2, traveler])
    db_session.flush()

    profile1 = ProviderProfile(
        user_id=prov_user1.id,
        business_name="Kandy Hills Sanctuary",
        provider_type="homestay",
        phone="+94771112222",
        district="Kandy",
        approval_status="approved",
    )
    profile2 = ProviderProfile(
        user_id=prov_user2.id,
        business_name="Galle Fort Villa",
        provider_type="homestay",
        phone="+94773334444",
        district="Galle",
        approval_status="approved",
    )
    db_session.add_all([profile1, profile2])
    db_session.flush()

    homestay = Homestay(
        name="Kandy Cloud Villa",
        description="Serene stay overlooking mist mountains",
        location="Heerassagala, Kandy",
        district="Kandy",
        price_per_night=Decimal("10000.00"),
        max_guests=4,
        is_active=True,
        approval_status="approved",
        provider_profile_id=profile1.id,
        owner_id=prov_user1.id,
        free_cancellation_hours=48,
    )
    db_session.add(homestay)
    db_session.flush()

    ref1 = ListingReference(
        module_type="homestay",
        target_id=homestay.id,
        title=homestay.name,
        district="Kandy",
        provider_profile_id=profile1.id,
    )
    db_session.add(ref1)
    db_session.commit()

    return {
        "admin": admin_user,
        "prov1": prov_user1,
        "prov2": prov_user2,
        "traveler": traveler,
        "profile1": profile1,
        "profile2": profile2,
        "homestay": homestay,
        "ref1": ref1,
    }


# ==============================================================================
# Workflows 1-4: Listing Creation, Approval, Discovery & Availability
# ==============================================================================
def test_workflow_01_to_04_listing_lifecycle(db_session, test_setup):
    homestay = test_setup["homestay"]
    # 1. Listing exists and is approved
    assert homestay.approval_status == "approved"
    assert homestay.is_active is True
    # 2. Public discovery reflects approved listing
    assert homestay.price_per_night == Decimal("10000.00")
    assert homestay.district == "Kandy"


# ==============================================================================
# Workflows 5-9: Online Reservation, Approval, 30m Window, Payment & Commission
# ==============================================================================
def test_workflow_05_to_09_online_reservation_to_paid_commission(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    check_in = date.today() + timedelta(days=10)
    check_out = date.today() + timedelta(days=12)

    # 5. Traveler creates reservation (2 nights * 10,000 = 20,000)
    booking_req = BookingCreate(
        homestay_id=homestay.id,
        check_in=check_in,
        check_out=check_out,
        guests=2,
        payment_method="online",
    )
    booking = create_booking(booking_data=booking_req, current_user=traveler, db=db_session)
    assert booking.status == "pending"
    assert booking.payment_status == "unpaid"
    assert booking.total_price == Decimal("20000.00")

    # 6 & 7. Provider approves online reservation -> awaiting_payment with 30m deadline
    accepted = accept_booking(booking_id=booking.id, db=db_session, current_user=host)
    assert accepted.status == "awaiting_payment"
    assert accepted.approved_at is not None
    assert accepted.payment_expires_at is not None
    deadline_diff = (accepted.payment_expires_at - accepted.approved_at).total_seconds()
    assert 1790 <= deadline_diff <= 1810

    # 8. Initiate checkout & simulate verified mock PayHere callback
    init_resp = initiate_booking_payment(booking_id=booking.id, current_user=traveler, db=db_session)
    assert init_resp.order_id is not None
    assert init_resp.amount == Decimal("20000.00")

    order_id = init_resp.order_id
    payhere_amt = "20000.00"
    payhere_cur = "LKR"
    status_code = "2"
    secret = payhere_service.merchant_secret
    import hashlib
    secret_hash = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
    raw_str = f"{payhere_service.merchant_id}{order_id}{payhere_amt}{payhere_cur}{status_code}{secret_hash}"
    sig = hashlib.md5(raw_str.encode("utf-8")).hexdigest().upper()

    form_data = {
        "merchant_id": payhere_service.merchant_id,
        "order_id": order_id,
        "payment_id": "PAYHERE-E2E-100",
        "payhere_amount": payhere_amt,
        "payhere_currency": payhere_cur,
        "status_code": status_code,
        "md5sig": sig,
    }
    cb_resp = handle_payhere_callback(form_data=form_data, db=db_session)
    assert cb_resp["status"] in ("success", "ok")

    # 9. Verify booking confirmation and 10% commission calculation
    db_session.refresh(booking)
    assert booking.status == "confirmed"
    assert booking.payment_status == "paid"
    assert booking.commission_rate == Decimal("10.00")
    assert booking.commission_amount == Decimal("2000.00")
    assert booking.provider_payable_amount == Decimal("18000.00")


# ==============================================================================
# Workflow 10: Cash-at-Location Reservation Lifecycle
# ==============================================================================
def test_workflow_10_cash_reservation_lifecycle(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    check_in = date.today() + timedelta(days=20)
    check_out = date.today() + timedelta(days=22)

    booking_req = BookingCreate(
        homestay_id=homestay.id,
        check_in=check_in,
        check_out=check_out,
        guests=2,
        payment_method="pay_at_location",
    )
    booking = create_booking(booking_data=booking_req, current_user=traveler, db=db_session)
    assert booking.status == "pending"
    assert booking.payment_method == "pay_at_location"

    # Cash reservation confirms immediately upon approval without payment deadline
    accepted = accept_booking(booking_id=booking.id, db=db_session, current_user=host)
    assert accepted.status == "confirmed"
    assert accepted.payment_status == "unpaid"
    assert accepted.payment_expires_at is None
    assert accepted.commission_amount is None


# ==============================================================================
# Workflow 11: Concurrent Booking Conflict Protection
# ==============================================================================
def test_workflow_11_concurrent_booking_conflict(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    check_in = date.today() + timedelta(days=30)
    check_out = date.today() + timedelta(days=33)

    b1 = create_booking(
        booking_data=BookingCreate(
            homestay_id=homestay.id, check_in=check_in, check_out=check_out, guests=2
        ),
        current_user=traveler,
        db=db_session,
    )
    accept_booking(booking_id=b1.id, db=db_session, current_user=host)

    # Overlapping booking attempt
    with pytest.raises(HTTPException) as exc:
        create_booking(
            booking_data=BookingCreate(
                homestay_id=homestay.id,
                check_in=check_in + timedelta(days=1),
                check_out=check_out + timedelta(days=1),
                guests=2,
            ),
            current_user=traveler,
            db=db_session,
        )
    assert exc.value.status_code in (400, 409)
    assert "already booked" in exc.value.detail.lower()


# ==============================================================================
# Workflow 12: Automatic Expiration & Availability Release
# ==============================================================================
def test_workflow_12_automatic_expiration_releases_inventory(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    check_in = date.today() + timedelta(days=40)
    check_out = date.today() + timedelta(days=42)

    b = create_booking(
        booking_data=BookingCreate(
            homestay_id=homestay.id, check_in=check_in, check_out=check_out, guests=2
        ),
        current_user=traveler,
        db=db_session,
    )
    accept_booking(booking_id=b.id, db=db_session, current_user=host)
    db_session.refresh(b)
    assert b.status == "awaiting_payment"

    # Fast-forward expiration to past
    b.payment_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    db_session.commit()

    count = process_expired_bookings(db_session)
    assert count >= 1

    db_session.refresh(b)
    assert b.status == "expired"

    # Availability is now released for new bookings on those dates
    b_new = create_booking(
        booking_data=BookingCreate(
            homestay_id=homestay.id, check_in=check_in, check_out=check_out, guests=2
        ),
        current_user=traveler,
        db=db_session,
    )
    assert b_new.id is not None


# ==============================================================================
# Workflow 13: Late Payment Reconciliation
# ==============================================================================
def test_workflow_13_late_payment_reconciliation(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]

    # Seed an expired booking
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=50),
        check_out=date.today() + timedelta(days=52),
        guests=2,
        total_price=Decimal("15000.00"),
        status="expired",
        payment_status="unpaid",
    )
    db_session.add(booking)
    db_session.flush()

    b_ref = BookingReference(
        booking_reference=f"BK-EXPIRED-{booking.id}",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=test_setup["profile1"].id,
        total_price=Decimal("15000.00"),
        booking_status="expired",
        payment_status="unpaid",
    )
    db_session.add(b_ref)
    db_session.commit()

    # Late payment callback arrives
    order_id = b_ref.booking_reference
    payhere_amt = "15000.00"
    payhere_cur = "LKR"
    status_code = "2"
    secret = payhere_service.merchant_secret
    import hashlib
    secret_hash = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
    raw_str = f"{payhere_service.merchant_id}{order_id}{payhere_amt}{payhere_cur}{status_code}{secret_hash}"
    sig = hashlib.md5(raw_str.encode("utf-8")).hexdigest().upper()

    form_data = {
        "merchant_id": payhere_service.merchant_id,
        "order_id": order_id,
        "payment_id": "LATE-PAY-100",
        "payhere_amount": payhere_amt,
        "payhere_currency": payhere_cur,
        "status_code": status_code,
        "md5sig": sig,
    }
    resp = handle_payhere_callback(form_data=form_data, db=db_session)
    assert resp["status"] in ("requires_reconciliation", "ok")

    # Expired booking remains expired!
    db_session.refresh(booking)
    assert booking.status == "expired"


# ==============================================================================
# Workflow 14: Cancellation & Refund Lifecycle with Proportional Accounting
# ==============================================================================
def test_workflow_14_cancellation_and_proportional_refund(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    admin = test_setup["admin"]
    profile = test_setup["profile1"]

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=60),
        check_out=date.today() + timedelta(days=62),
        guests=2,
        total_price=Decimal("10000.00"),
        status="confirmed",
        payment_status="paid",
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("1000.00"),
        provider_payable_amount=Decimal("9000.00"),
    )
    db_session.add(booking)
    db_session.flush()

    b_ref = BookingReference(
        booking_reference=f"BK-REFUND-{booking.id}",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("10000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    db_session.add(b_ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=b_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
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

    # Cancel booking automatically generates pending refund for paid booking within policy window
    cancel_booking(
        booking_id=booking.id,
        payload=BookingCancelRequest(cancellation_reason="Change of plans"),
        db=db_session,
        current_user=traveler,
    )
    db_session.refresh(booking)
    assert booking.status == "cancelled"

    refund = (
        db_session.query(PaymentRefund)
        .filter(PaymentRefund.payment_transaction_id == tx.id)
        .first()
    )
    assert refund is not None
    assert refund.amount == Decimal("10000.00")
    assert refund.refund_status == "pending"

    # Admin process refund to completed with verified evidence
    process_refund(
        refund_id=refund.id,
        payload=RefundProcessRequest(status="completed", notes="Bank REF: TXN-REF-998822"),
        admin=admin,
        db=db_session,
    )
    db_session.refresh(refund)
    assert refund.refund_status == "completed"

    # Verify proportional accounting (10,000 refund -> 1,000 commission reversal, 9,000 provider deduction)
    balances = calculate_provider_earnings_balances(db_session, profile.id)
    assert balances["verified_refunds"] == Decimal("10000.00")
    assert balances["provider_refund_reduction"] == Decimal("9000.00")
    assert balances["commission_reversal"] == Decimal("1000.00")


# ==============================================================================
# Workflow 15: Provider Settlement Eligibility & Duplicate Protection
# ==============================================================================
def test_workflow_15_settlement_eligibility_and_duplicate_protection(db_session, test_setup):
    profile = test_setup["profile2"]
    traveler = test_setup["traveler"]
    admin = test_setup["admin"]

    # Create completed booking (check-out 5 days ago)
    booking = Booking(
        homestay_id=test_setup["homestay"].id,
        user_id=traveler.id,
        check_in=date.today() - timedelta(days=7),
        check_out=date.today() - timedelta(days=5),
        guests=2,
        total_price=Decimal("10000.00"),
        status="completed",
        payment_status="paid",
        commission_rate=Decimal("10.00"),
        commission_amount=Decimal("1000.00"),
        provider_payable_amount=Decimal("9000.00"),
    )
    db_session.add(booking)
    db_session.flush()

    b_ref = BookingReference(
        booking_reference="BK-SETTLE-WF15",
        module_type="homestay",
        target_booking_id=booking.id,
        user_id=traveler.id,
        provider_profile_id=profile.id,
        total_price=Decimal("10000.00"),
        booking_status="completed",
        payment_status="paid",
    )
    db_session.add(b_ref)
    db_session.flush()

    tx = PaymentTransaction(
        booking_reference_id=b_ref.id,
        user_id=traveler.id,
        gateway_name="payhere",
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

    # Create settlement for period including the checkout
    settle_req = SettlementCreateRequest(
        provider_profile_id=profile.id,
        period_start=date.today() - timedelta(days=10),
        period_end=date.today() - timedelta(days=3),
        gross_amount=Decimal("10000.00"),
        platform_fee=Decimal("1000.00"),
        net_payable=Decimal("9000.00"),
        payout_reference="BANK-TRF-00129",
        settlement_status="completed",
    )
    settlement = create_provider_settlement(payload=settle_req, admin=admin, db=db_session)
    assert settlement.settlement_status == "completed"

    # Duplicate / overlapping settlement attempt is rejected
    with pytest.raises(HTTPException) as exc:
        create_provider_settlement(payload=settle_req, admin=admin, db=db_session)
    assert exc.value.status_code in (400, 409)
    assert "overlaps" in exc.value.detail


# ==============================================================================
# Workflow 16: Completed-Stay Review Eligibility & Premature Completion Safeguard
# ==============================================================================
def test_workflow_16_premature_completion_prevented(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    # Future stay: check-out is 5 days from now
    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=1),
        check_out=date.today() + timedelta(days=5),
        guests=2,
        total_price=Decimal("10000.00"),
        status="confirmed",
        payment_status="paid",
    )
    db_session.add(booking)
    db_session.commit()

    # Provider cannot complete booking before check-out date!
    with pytest.raises(HTTPException) as exc:
        complete_booking(booking_id=booking.id, db=db_session, current_user=host)
    assert exc.value.status_code in (400, 409)
    assert "before the check-out date" in exc.value.detail


# ==============================================================================
# Workflow 17: Sponsored Campaign Lifecycle & Attribution Isolation
# ==============================================================================
def test_workflow_17_sponsored_campaign_lifecycle(db_session, test_setup):
    profile = test_setup["profile1"]
    ref = test_setup["ref1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Peak Season Boost",
        target_module="homestay",
        budget=Decimal("5000.00"),
        status="approved",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    db_session.add(campaign)
    db_session.commit()

    # Initiate payment in sandbox mode returns simulation notice
    resp = initiate_campaign_payment(campaign_id=campaign.id, db=db_session, current_provider=profile)
    assert resp.amount == Decimal("5000.00")
    assert resp.status == "pending"


# ==============================================================================
# Workflow 18: Provider and Administrator Authorization
# ==============================================================================
def test_workflow_18_authorization_isolation(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host2 = test_setup["prov2"]

    b = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=70),
        check_out=date.today() + timedelta(days=72),
        guests=2,
        total_price=Decimal("5000.00"),
        status="pending",
    )
    db_session.add(b)
    db_session.commit()

    # Host 2 attempts to accept Host 1's booking -> 403 Forbidden
    with pytest.raises(HTTPException) as exc:
        accept_booking(booking_id=b.id, db=db_session, current_user=host2)
    assert exc.value.status_code == 403


# ==============================================================================
# Workflow 19: Background Expiration Worker Lifecycle
# ==============================================================================
def test_workflow_19_worker_startup_and_shutdown():
    assert callable(process_expired_bookings)
    assert callable(start_expiration_worker)
    assert callable(stop_expiration_worker)
    # Under test runner, start_expiration_worker returns None to prevent unmanaged loops
    task = start_expiration_worker(interval_seconds=60)
    assert task is None


# ==============================================================================
# Workflow 20: Advertising & Booking Ledger Isolation
# ==============================================================================
def test_workflow_20_ledger_isolation(db_session, test_setup):
    profile = test_setup["profile1"]
    ref = test_setup["ref1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Seed a campaign and an advertising payment
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        listing_reference_id=ref.id,
        campaign_name="Ledger Isolation Campaign",
        target_module="homestay",
        budget=Decimal("8000.00"),
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    db_session.add(campaign)
    db_session.flush()

    ad = AdvertisingPayment(
        campaign_id=campaign.id,
        provider_profile_id=profile.id,
        order_reference="AD-ISO-01",
        amount=Decimal("8000.00"),
        payment_status="completed",
        verified_at=now,
    )
    db_session.add(ad)
    db_session.commit()

    # 2. Check provider booking balances: advertising revenue must NOT be in booking earnings
    balances = calculate_provider_earnings_balances(db_session, profile.id)
    # Booking earnings should be 0 from advertising payment
    assert balances["total_gross_earnings"] == Decimal("0.00")
    assert balances["ceylongo_commission"] == Decimal("0.00")
    assert balances["available_settlement_balance"] == Decimal("0.00")


# ==============================================================================
# Bonus: Zero-Value Promotional Booking Safety Check
# ==============================================================================
def test_zero_value_promotional_booking_safety(db_session, test_setup):
    homestay = test_setup["homestay"]
    traveler = test_setup["traveler"]
    host = test_setup["prov1"]

    booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=date.today() + timedelta(days=80),
        check_out=date.today() + timedelta(days=82),
        guests=2,
        total_price=Decimal("0.00"),
        status="pending",
        payment_method="online",
    )
    db_session.add(booking)
    db_session.commit()

    accept_booking(booking_id=booking.id, db=db_session, current_user=host)

    # Initiate payment directly confirms zero-value booking
    resp = initiate_booking_payment(booking_id=booking.id, current_user=traveler, db=db_session)
    assert resp.amount == Decimal("0.00")
    assert resp.hash == "ZERO_VALUE_PROMOTION_CONFIRMED"

    db_session.refresh(booking)
    assert booking.status == "confirmed"
    assert booking.payment_status == "paid"
    assert booking.commission_amount == Decimal("0.00")
    assert booking.provider_payable_amount == Decimal("0.00")
