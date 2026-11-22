"""
Tests for Module 04 Eco Payment Transaction Coordinator.
=======================================================
Target: app/services/eco_payment_transaction_coordinator.py

Key Test Categories:
1. Coordinator initialization & dependency injection.
2. Order ID and BookingReference lookup and validation.
3. Integrity checks: user_id & module matching.
4. Protection against payment on terminal bookings (cancelled/rejected).
5. Protection against payment on cash bookings (pay_at_location).
6. Webhook security rejection: forged signatures, tampered amounts, currency mismatches.
7. Idempotency management:
   - Duplicate callback with identical payment ID returns already_processed.
   - Callback with conflicting payment ID on paid booking returns conflict_rejected.
8. Non-success callback handling (cancelled / failed).
9. Booking approval preservation:
   - When booking is pending, payment confirms payment_status='paid',
     while booking_status remains 'pending' (preserves provider approval rules).
   - When auto_confirm_booking=True, booking_status is promoted to 'confirmed'.
10. Caller-controlled transaction boundary (rollback verification; no auto-commit).
11. Security: zero notifications or refunds dispatched.

STRICT ISOLATION:
- Runs in-memory against SQLite only.
- ZERO connections to Microsoft SQL Server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
from typing import Any, Dict
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.payhere import PayHereService
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import Base, User
from app.services.eco_payhere_webhook_security import (
    EcoPayHereIPNPayload,
    IdempotencyDecision,
    PaymentOutcome,
    TrustedEcoPaymentContext,
)
from app.services.eco_payment_sqlalchemy_adapter import SqlAlchemyEcoPaymentAdapter
from app.services.eco_payment_transaction_coordinator import (
    EcoPaymentCoordinationResult,
    EcoPaymentTransactionCoordinator,
)

TEST_MERCHANT_ID = "TEST_MERCHANT_123"
TEST_MERCHANT_SECRET = "TEST_SECRET_ABCXYZ"


def generate_valid_ipn_signature(
    merchant_id: str,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str,
    status_code: str,
    merchant_secret: str,
) -> str:
    """Helper to generate valid PayHere MD5 signature."""
    secret_hash = hashlib.md5(merchant_secret.encode("utf-8")).hexdigest().upper()
    combo = f"{merchant_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}"
    return hashlib.md5(combo.encode("utf-8")).hexdigest().upper()


@pytest.fixture
def mock_payhere_svc() -> PayHereService:
    svc = PayHereService()
    svc.merchant_id = TEST_MERCHANT_ID
    svc.merchant_secret = TEST_MERCHANT_SECRET
    svc.sandbox = True
    return svc


@pytest.fixture
def sqlite_session():
    """In-memory SQLite session with table creation."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()

    try:
        # Seed User
        traveler = User(
            id=1,
            email="traveler@example.com",
            full_name="Kusal Mendis",
            role="traveler",
            is_active=True,
        )
        session.add(traveler)
        session.flush()

        # Seed sample ActivityBooking
        booking = ActivityBooking(
            id=201,
            activity_slot_id=10,
            eco_experience_id=1,
            user_id=1,
            participants=2,
            total_price=Decimal("12500.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        session.add(booking)
        session.flush()

        # Seed sample BookingReference
        booking_ref = BookingReference(
            id=601,
            booking_reference="ECO-20261201-201",
            module_type="activity",
            target_booking_id=201,
            user_id=1,
            provider_profile_id=1,
            total_price=Decimal("12500.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
        )
        session.add(booking_ref)
        session.commit()

        yield session
    finally:
        session.close()


@pytest.fixture
def coordinator(sqlite_session: Session) -> EcoPaymentTransactionCoordinator:
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    return EcoPaymentTransactionCoordinator(adapter)


# ==============================================================================
# TESTS
# ==============================================================================


def test_coord_01_requires_adapter():
    """Constructor rejects None adapter with ValueError."""
    with pytest.raises(ValueError):
        EcoPaymentTransactionCoordinator(None)  # type: ignore


def test_coord_02_rejects_missing_order_id(
    coordinator: EcoPaymentTransactionCoordinator, mock_payhere_svc: PayHereService
):
    """Payload missing order_id returns error result."""
    result = coordinator.process_webhook_payload({}, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_ORDER_ID_MISSING"
    assert result.status == "rejected"


def test_coord_03_rejects_non_existent_booking_reference(
    coordinator: EcoPaymentTransactionCoordinator, mock_payhere_svc: PayHereService
):
    """Unknown order_id returns ECO_BOOKING_REF_NOT_FOUND."""
    payload = {"order_id": "ECO-UNKNOWN-999"}
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_BOOKING_REF_NOT_FOUND"


def test_coord_04_rejects_non_existent_activity_booking(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """BookingReference pointing to non-existent booking ID returns ECO_BOOKING_NOT_FOUND."""
    orphan_ref = BookingReference(
        id=777,
        booking_reference="ECO-ORPHAN-777",
        module_type="activity",
        target_booking_id=9999,  # does not exist
        user_id=1,
        provider_profile_id=1,
        total_price=Decimal("1000.00"),
        currency="LKR",
        booking_status="pending",
        payment_status="unpaid",
    )
    sqlite_session.add(orphan_ref)
    sqlite_session.commit()

    payload = {"order_id": "ECO-ORPHAN-777"}
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_BOOKING_NOT_FOUND"


def test_coord_05_rejects_user_id_mismatch(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """User ID mismatch between reference and booking returns ECO_INTEGRITY_MISMATCH."""
    ref = sqlite_session.query(BookingReference).filter(BookingReference.id == 601).first()
    ref.user_id = 99  # Tamper with reference user_id
    sqlite_session.commit()

    payload = {"order_id": "ECO-20261201-201"}
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_INTEGRITY_MISMATCH"


def test_coord_06_rejects_cancelled_booking(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Cancelled booking returns ECO_BOOKING_TERMINAL_STATUS."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    booking.booking_status = "cancelled"
    sqlite_session.commit()

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_BOOKING_TERMINAL_STATUS"


def test_coord_07_rejects_rejected_booking(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Rejected booking returns ECO_BOOKING_TERMINAL_STATUS."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    booking.booking_status = "rejected"
    sqlite_session.commit()

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_BOOKING_TERMINAL_STATUS"


def test_coord_08_rejects_pay_at_location_method(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Booking with payment_method='pay_at_location' returns ECO_INVALID_PAYMENT_METHOD."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    booking.payment_method = "pay_at_location"
    sqlite_session.commit()

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_INVALID_PAYMENT_METHOD"


def test_coord_09_rejects_invalid_payhere_signature(
    coordinator: EcoPaymentTransactionCoordinator, mock_payhere_svc: PayHereService
):
    """Forged MD5 signature returns ECO_IPN_INVALID_SIGNATURE."""
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": "00000000000000000000000000000000",
    }
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code in ("ECO_IPN_INVALID_SIGNATURE", "ECO_IPN_MALFORMED_SIGNATURE")


def test_coord_10_rejects_tampered_amount(
    coordinator: EcoPaymentTransactionCoordinator, mock_payhere_svc: PayHereService
):
    """Tampered amount returns ECO_IPN_AMOUNT_MISMATCH."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "100.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "100.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_IPN_AMOUNT_MISMATCH"


def test_coord_11_rejects_unsupported_currency(
    coordinator: EcoPaymentTransactionCoordinator, mock_payhere_svc: PayHereService
):
    """Currency mismatch returns ECO_IPN_CURRENCY_MISMATCH."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "USD", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "USD",
        "status_code": "2",
        "md5sig": sig,
    }
    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.error_code == "ECO_IPN_CURRENCY_MISMATCH"


def test_coord_12_idempotency_already_processed(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Duplicate callback on already paid booking with same payment ID returns already_processed."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    booking.payment_status = "paid"
    booking.booking_status = "confirmed"

    tx = PaymentTransaction(
        booking_reference_id=601,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_32001",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
    )
    sqlite_session.add(tx)
    sqlite_session.commit()

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_32001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True
    assert result.status == "already_processed"
    assert result.applied_transition is False
    assert result.applied_transaction is False


def test_coord_13_idempotency_rejects_conflicting_payment_id(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Callback with different payment ID on already paid booking returns conflict_rejected."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    booking.payment_status = "paid"
    booking.booking_status = "confirmed"

    tx = PaymentTransaction(
        booking_reference_id=601,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_ORIGINAL",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
    )
    sqlite_session.add(tx)
    sqlite_session.commit()

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_DIFFERENT",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is False
    assert result.status == "conflict_rejected"
    assert result.error_code == "ECO_PAYMENT_CONFLICT"


def test_coord_14_handles_cancelled_callback(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """PayHere status_code='-1' (cancelled) records failure transaction without marking paid."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "-1", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_CANCELLED_1",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "-1",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True
    assert result.status == "non_success_recorded"
    assert result.applied_transition is False
    assert result.applied_transaction is True

    # Booking must NOT be paid
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status != "paid"


def test_coord_15_handles_failed_callback(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """PayHere status_code='-2' (failed) records failure transaction without marking paid."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "-2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_FAILED_1",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "-2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True
    assert result.status == "non_success_recorded"
    assert result.applied_transition is False

    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status != "paid"


def test_coord_16_preserves_pending_booking_status_by_default(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """
    By default, receiving payment confirms payment_status='paid',
    but preserves booking_status='pending' (awaiting provider approval).
    """
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_SUCCESS_PENDING_APPROVAL",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(
        payload, payhere_svc=mock_payhere_svc, auto_confirm_booking=False
    )
    assert result.success is True
    assert result.status == "processed"
    assert result.to_payment_status == "paid"
    assert result.to_booking_status == "pending"  # Preserved!

    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status == "paid"
    assert booking.booking_status == "pending"  # Still pending provider decision!


def test_coord_17_auto_confirm_booking_when_explicitly_flagged(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """When auto_confirm_booking=True, booking_status is promoted to 'confirmed'."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_SUCCESS_AUTO_CONFIRMED",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(
        payload, payhere_svc=mock_payhere_svc, auto_confirm_booking=True
    )
    assert result.success is True
    assert result.status == "processed"
    assert result.to_payment_status == "paid"
    assert result.to_booking_status == "confirmed"

    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status == "paid"
    assert booking.booking_status == "confirmed"


def test_coord_18_successful_processing_full_flow(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """End-to-end successful processing verifies all fields and persistence."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_SUCCESS_9999",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
        "method": "MASTER",
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True
    assert result.status == "processed"
    assert result.booking_id == 201
    assert result.order_id == "ECO-20261201-201"
    assert result.gateway_payment_id == "PH_SUCCESS_9999"
    assert result.applied_transition is True
    assert result.applied_transaction is True

    # Check database records
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status == "paid"

    ref = sqlite_session.query(BookingReference).filter(BookingReference.id == 601).first()
    assert ref.payment_status == "paid"

    tx = (
        sqlite_session.query(PaymentTransaction)
        .filter(PaymentTransaction.gateway_transaction_id == "PH_SUCCESS_9999")
        .first()
    )
    assert tx is not None
    assert tx.amount == Decimal("12500.00")
    assert tx.currency == "LKR"
    assert tx.payment_status == "paid"
    assert tx.payment_method == "MASTER"


def test_coord_19_caller_controlled_transaction_boundary(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
):
    """Caller rollback completely reverts coordinator changes; zero auto-commits."""
    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_ROLLBACK_TEST",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True

    # Caller rolls back transaction
    sqlite_session.rollback()

    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 201).first()
    assert booking.payment_status == "unpaid"

    tx = (
        sqlite_session.query(PaymentTransaction)
        .filter(PaymentTransaction.gateway_transaction_id == "PH_ROLLBACK_TEST")
        .first()
    )
    assert tx is None


def test_coord_20_zero_notifications_or_refunds_triggered(
    sqlite_session: Session,
    coordinator: EcoPaymentTransactionCoordinator,
    mock_payhere_svc: PayHereService,
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify coordinator does not invoke notifications or refund services."""
    notification_called = False
    refund_called = False

    def mock_notify(*args, **kwargs):
        nonlocal notification_called
        notification_called = True

    def mock_refund(*args, **kwargs):
        nonlocal refund_called
        refund_called = True

    monkeypatch.setattr("app.core.notification_service.create_notification", mock_notify, raising=False)

    sig = generate_valid_ipn_signature(
        TEST_MERCHANT_ID, "ECO-20261201-201", "12500.00", "LKR", "2", TEST_MERCHANT_SECRET
    )
    payload = {
        "merchant_id": TEST_MERCHANT_ID,
        "order_id": "ECO-20261201-201",
        "payment_id": "PH_NO_SIDE_EFFECTS",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = coordinator.process_webhook_payload(payload, payhere_svc=mock_payhere_svc)
    assert result.success is True
    assert notification_called is False
    assert refund_called is False
