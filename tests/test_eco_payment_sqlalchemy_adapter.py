"""
Tests for Module 04 SQLAlchemy Eco Payment Persistence Adapter.
==============================================================
Target: app/services/eco_payment_sqlalchemy_adapter.py

Key Test Categories:
1. Protocol satisfaction: satisfies EcoPaymentPersistenceAdapter.
2. Constructor validation: requires Session, never creates engine.
3. Query lookups: ActivityBooking, BookingReference, PaymentTransaction.
4. Uniqueness checks: is_gateway_transaction_unique.
5. Atomic payment confirmation:
   - Updates payment_status to 'paid'.
   - Synchronizes BookingReference.
   - Inserts PaymentTransaction.
   - Idempotent re-entry protection.
6. Transaction boundary:
   - Zero auto-commits; caller rollback completely reverts state.
7. Terminal status & conflict protections:
   - Rejects cancelled or rejected bookings.
   - Rejects cash (pay_at_location) bookings.
   - Rejects conflicting gateway transaction IDs.
8. Failure recording:
   - Records failed transaction and updates status if unpaid.
   - Rejects failure recording on already paid bookings.

STRICT ISOLATION:
- Runs in-memory against SQLite only.
- ZERO connections to Microsoft SQL Server.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import Base, User
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    EcoPaymentPersistenceAdapter,
    PaymentTransactionPlan,
)
from app.services.eco_payment_sqlalchemy_adapter import (
    EcoPaymentConcurrencyConflictError,
    EcoPaymentPersistenceError,
    EcoPaymentRecordNotFoundError,
    SqlAlchemyEcoPaymentAdapter,
)


@pytest.fixture
def sqlite_session():
    """In-memory SQLite session with table creation."""
    from sqlalchemy import event

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
        # Seed basic User
        traveler = User(
            id=1,
            email="traveler@example.com",
            full_name="Anura Perera",
            role="traveler",
            is_active=True,
        )
        session.add(traveler)
        session.flush()

        # Seed sample ActivityBooking
        booking = ActivityBooking(
            id=101,
            activity_slot_id=10,
            eco_experience_id=1,
            user_id=1,
            participants=2,
            total_price=Decimal("15000.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        session.add(booking)
        session.flush()

        # Seed sample BookingReference
        booking_ref = BookingReference(
            id=501,
            booking_reference="ECO-20261201-101",
            module_type="activity",
            target_booking_id=101,
            user_id=1,
            provider_profile_id=1,
            total_price=Decimal("15000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
        )
        session.add(booking_ref)
        session.commit()

        yield session
    finally:
        session.close()


# ==============================================================================
# TESTS
# ==============================================================================


def test_adapter_01_protocol_satisfaction(sqlite_session: Session):
    """Adapter instance must satisfy the EcoPaymentPersistenceAdapter Protocol."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    assert isinstance(adapter, EcoPaymentPersistenceAdapter)


def test_adapter_02_constructor_requires_session():
    """Passing None as session must fail closed with EcoPaymentPersistenceError."""
    with pytest.raises(EcoPaymentPersistenceError):
        SqlAlchemyEcoPaymentAdapter(None)  # type: ignore


def test_adapter_03_get_booking_by_id_found(sqlite_session: Session):
    """Can retrieve an existing ActivityBooking by integer ID."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    booking = adapter.get_booking_by_id(101)
    assert booking is not None
    assert booking.id == 101
    assert booking.total_price == Decimal("15000.00")
    assert booking.payment_status == "unpaid"


def test_adapter_04_get_booking_by_id_not_found(sqlite_session: Session):
    """Returns None for non-existent or invalid booking IDs."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    assert adapter.get_booking_by_id(9999) is None
    assert adapter.get_booking_by_id(-1) is None
    assert adapter.get_booking_by_id("invalid") is None  # type: ignore


def test_adapter_05_get_booking_reference_found(sqlite_session: Session):
    """Can retrieve BookingReference by reference code."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    ref = adapter.get_booking_reference("ECO-20261201-101")
    assert ref is not None
    assert ref.id == 501
    assert ref.target_booking_id == 101
    assert ref.booking_reference == "ECO-20261201-101"


def test_adapter_06_get_booking_reference_not_found(sqlite_session: Session):
    """Returns None for non-existent reference code."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    assert adapter.get_booking_reference("NON-EXISTENT") is None
    assert adapter.get_booking_reference("") is None


def test_adapter_07_get_booking_reference_by_booking_id(sqlite_session: Session):
    """Can retrieve BookingReference by target booking ID."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    ref = adapter.get_booking_reference_by_booking_id(101)
    assert ref is not None
    assert ref.id == 501
    assert ref.booking_reference == "ECO-20261201-101"


def test_adapter_08_get_latest_payment_transaction_empty(sqlite_session: Session):
    """Returns None when no transaction has been recorded for the reference."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    tx = adapter.get_latest_payment_transaction(501, "payhere")
    assert tx is None


def test_adapter_09_get_latest_payment_transaction_order(sqlite_session: Session):
    """Returns the most recent transaction when multiple exist."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    # Insert two transactions
    tx1 = PaymentTransaction(
        booking_reference_id=501,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_OLD",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="failed",
        payment_method="online",
    )
    tx2 = PaymentTransaction(
        booking_reference_id=501,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_NEW",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
    )
    sqlite_session.add_all([tx1, tx2])
    sqlite_session.commit()

    latest = adapter.get_latest_payment_transaction(501, "payhere")
    assert latest is not None
    assert latest.gateway_transaction_id == "TX_NEW"
    assert latest.payment_status == "paid"


def test_adapter_10_is_gateway_transaction_unique(sqlite_session: Session):
    """Correctly evaluates whether a gateway transaction ID is unique."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    assert adapter.is_gateway_transaction_unique("payhere", "TX_32001") is True

    # Record TX_32001
    tx = PaymentTransaction(
        booking_reference_id=501,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_32001",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
    )
    sqlite_session.add(tx)
    sqlite_session.commit()

    assert adapter.is_gateway_transaction_unique("payhere", "TX_32001") is False
    assert adapter.is_gateway_transaction_unique("payhere", "TX_OTHER") is True


def test_adapter_11_execute_payment_confirmation_success(sqlite_session: Session):
    """Successfully transitions booking and reference to paid and inserts PaymentTransaction."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="PayHere online payment confirmed",
        action_required=True,
    )

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_TX_9999",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
        gateway_response_summary={"method": "VISA", "status_code": "2"},
        created_at=datetime.now(timezone.utc),
    )

    res = adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert res is True

    # Inspect modified records in session
    booking = adapter.get_booking_by_id(101)
    assert booking.payment_status == "paid"
    assert booking.booking_status == "confirmed"

    ref = adapter.get_booking_reference("ECO-20261201-101")
    assert ref.payment_status == "paid"
    assert ref.booking_status == "confirmed"

    tx = adapter.get_latest_payment_transaction(501, "payhere")
    assert tx is not None
    assert tx.gateway_transaction_id == "PH_TX_9999"
    assert tx.amount == Decimal("15000.00")
    assert tx.payment_status == "paid"
    assert tx.payment_method == "VISA"


def test_adapter_12_execute_payment_confirmation_caller_controlled_transaction(
    sqlite_session: Session,
):
    """Adapter must NOT auto-commit; caller can roll back all operations cleanly."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Test rollback",
        action_required=True,
    )

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_TX_ROLLBACK",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )

    adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)

    # Caller decides to roll back
    sqlite_session.rollback()

    booking = adapter.get_booking_by_id(101)
    assert booking.payment_status == "unpaid"
    assert booking.booking_status == "pending"

    tx = adapter.get_latest_payment_transaction(501, "payhere")
    assert tx is None


def test_adapter_13_execute_payment_confirmation_idempotent_reentry(
    sqlite_session: Session,
):
    """Calling with same gateway transaction ID on paid booking returns True idempotently."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Initial payment",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_TX_IDEMPOTENT",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )

    # First call
    assert adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan) is True

    # Duplicate call with identical gateway transaction ID
    assert adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan) is True

    # Count of transactions must still be exactly 1
    count = (
        sqlite_session.query(PaymentTransaction)
        .filter(PaymentTransaction.gateway_transaction_id == "PH_TX_IDEMPOTENT")
        .count()
    )
    assert count == 1


def test_adapter_14_execute_payment_confirmation_rejects_missing_booking(
    sqlite_session: Session,
):
    """Non-existent booking raises EcoPaymentRecordNotFoundError."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=9999,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Missing booking",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_MISSING",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )
    with pytest.raises(EcoPaymentRecordNotFoundError):
        adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)


def test_adapter_15_execute_payment_confirmation_rejects_cancelled_booking(
    sqlite_session: Session,
):
    """Cancelled booking raises EcoPaymentConcurrencyConflictError."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 101).first()
    booking.booking_status = "cancelled"
    sqlite_session.commit()

    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="cancelled",
        to_booking_status="confirmed",
        audit_note="Cancelled booking",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_CANCELLED",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )
    with pytest.raises(EcoPaymentConcurrencyConflictError) as exc_info:
        adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert "cancelled" in str(exc_info.value).lower()


def test_adapter_16_execute_payment_confirmation_rejects_rejected_booking(
    sqlite_session: Session,
):
    """Rejected booking raises EcoPaymentConcurrencyConflictError."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 101).first()
    booking.booking_status = "rejected"
    sqlite_session.commit()

    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="rejected",
        to_booking_status="confirmed",
        audit_note="Rejected booking",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_REJECTED",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )
    with pytest.raises(EcoPaymentConcurrencyConflictError) as exc_info:
        adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert "rejected" in str(exc_info.value).lower()


def test_adapter_17_execute_payment_confirmation_rejects_non_online_payment_method(
    sqlite_session: Session,
):
    """Booking with payment_method='pay_at_location' raises EcoPaymentConcurrencyConflictError."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 101).first()
    booking.payment_method = "pay_at_location"
    sqlite_session.commit()

    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Cash booking",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="TX_CASH",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )
    with pytest.raises(EcoPaymentConcurrencyConflictError) as exc_info:
        adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert "pay_at_location" in str(exc_info.value).lower()


def test_adapter_18_execute_payment_confirmation_rejects_conflicting_gateway_tx_id(
    sqlite_session: Session,
):
    """If gateway transaction ID was already recorded for another booking, raises conflict."""
    # Record TX_SHARED on another booking reference ID 999
    other_ref = BookingReference(
        id=999,
        booking_reference="ECO-OTHER-999",
        module_type="activity",
        target_booking_id=999,
        user_id=1,
        provider_profile_id=1,
        total_price=Decimal("5000.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_status="paid",
    )
    sqlite_session.add(other_ref)
    other_tx = PaymentTransaction(
        booking_reference_id=999,
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_TX_SHARED",
        amount=Decimal("5000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
    )
    sqlite_session.add(other_tx)
    sqlite_session.commit()

    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=101,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Conflicting TX",
    )
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=501,
        booking_reference_code="ECO-20261201-101",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_TX_SHARED",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
    )

    with pytest.raises(EcoPaymentConcurrencyConflictError) as exc_info:
        adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert "already recorded for a different booking reference" in str(exc_info.value)


def test_adapter_19_record_payment_failure_atomic(sqlite_session: Session):
    """Recording payment failure updates status to failed and inserts failure transaction."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)

    res = adapter.record_payment_failure_atomic(
        booking_id=101,
        gateway_tx_id="TX_FAIL_123",
        reason_note="Payment cancelled by traveler",
        gateway_payload={"method": "VISA", "status_code": "-1"},
    )
    assert res is True

    booking = adapter.get_booking_by_id(101)
    assert booking.payment_status == "failed"

    tx = adapter.get_latest_payment_transaction(501, "payhere")
    assert tx is not None
    assert tx.gateway_transaction_id == "TX_FAIL_123"
    assert tx.payment_status == "failed"


def test_adapter_20_record_payment_failure_rejects_already_paid(sqlite_session: Session):
    """Cannot record payment failure on an already paid booking."""
    booking = sqlite_session.query(ActivityBooking).filter(ActivityBooking.id == 101).first()
    booking.payment_status = "paid"
    sqlite_session.commit()

    adapter = SqlAlchemyEcoPaymentAdapter(sqlite_session)
    with pytest.raises(EcoPaymentConcurrencyConflictError) as exc_info:
        adapter.record_payment_failure_atomic(
            booking_id=101,
            gateway_tx_id="TX_FAIL_AFTER_PAID",
            reason_note="Failure",
            gateway_payload={},
        )
    assert "already paid" in str(exc_info.value)
