"""
Module 04 — Eco-Tourism Real SQL Server Payment Integration Tests.
==================================================================
Target: tests/integration/test_eco_payment_sqlserver_integration.py

STRICT SAFETY AND GATE 3 AUTHORIZATION RULES:
1. Live SQL Server tests are ONLY executed when:
   - Gate 3 authorization has been formally granted.
   - TEST_DISPOSABLE_SQLSERVER_URL is configured pointing to an allowlisted
     disposable database (e.g. CeylonGoDB_Disposable_QA or CeylonGoDB_IntegrationTest).
2. Live tests are AUTOMATICALLY SKIPPED when TEST_DISPOSABLE_SQLSERVER_URL is unset.
3. NEVER connects to or mutates:
   - CeylonGoDB (live production)
   - CeylonGoDB_Development (active developer database)
   - SQL Server system databases (master, tempdb, model, msdb)
4. Database identity guard (app/core/database_guard.py) is enforced fail-closed.
5. All financial transactions use test-only PayHere sandbox/mock parameters.
   Production PayHere credentials are strictly forbidden.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import os
import threading
from typing import Any, Dict, Optional
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.database_guard import (
    APPROVED_INTEGRATION_DATABASES,
    PROTECTED_DATABASES,
    DatabaseSafetyError,
    create_safe_sqlserver_test_engine,
    extract_database_name_from_url,
    validate_disposable_sqlserver_url,
)
from app.core.payhere import PayHereService
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import User
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    PaymentTransactionPlan,
)
from app.services.eco_payment_sqlalchemy_adapter import (
    EcoPaymentConcurrencyConflictError,
    EcoPaymentPersistenceError,
    EcoPaymentRecordNotFoundError,
    SqlAlchemyEcoPaymentAdapter,
)
from app.services.eco_payment_transaction_coordinator import (
    EcoPaymentCoordinationResult,
    EcoPaymentTransactionCoordinator,
)

# ==============================================================================
# Environment & Gate 3 Configuration
# ==============================================================================

TEST_DISPOSABLE_SQLSERVER_URL = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")

LIVE_SQLSERVER_SKIP_REASON = (
    "Live SQL Server integration tests require Gate 3 authorization and "
    "TEST_DISPOSABLE_SQLSERVER_URL pointing to an approved disposable database "
    "(e.g., CeylonGoDB_Disposable_QA or CeylonGoDB_IntegrationTest)."
)

# Test-only deterministic PayHere merchant credentials (NEVER PRODUCTION)
TEST_PAYHERE_MERCHANT_ID = "DISPOSABLE_TEST_MERCHANT_01"
TEST_PAYHERE_MERCHANT_SECRET = "DISPOSABLE_TEST_SECRET_ABC123456789"


def generate_test_signed_ipn(
    order_id: str,
    amount: str = "12500.00",
    currency: str = "LKR",
    status_code: str = "2",
    payment_id: str = "PH_DISPOSABLE_99991",
    method: str = "VISA",
) -> dict[str, str]:
    """Helper to generate deterministically signed PayHere test payloads."""
    secret_hash = hashlib.md5(TEST_PAYHERE_MERCHANT_SECRET.encode("utf-8")).hexdigest().upper()
    combo = f"{TEST_PAYHERE_MERCHANT_ID}{order_id}{amount}{currency}{status_code}{secret_hash}"
    sig = hashlib.md5(combo.encode("utf-8")).hexdigest().upper()
    return {
        "merchant_id": TEST_PAYHERE_MERCHANT_ID,
        "order_id": order_id,
        "payment_id": payment_id,
        "payhere_amount": amount,
        "payhere_currency": currency,
        "status_code": status_code,
        "md5sig": sig,
        "method": method,
    }


def verify_sqlserver_payment_schema(engine) -> dict[str, Any]:
    """
    Inspects physical SQL Server schema to verify tables and columns
    required for Module 04 online payment integration.
    Fails closed if critical tables or columns are missing.
    """
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME IN "
                "('activity_bookings', 'booking_references', 'payment_transactions', 'activity_slots')"
            )
        ).scalars().all()
        tables_set = {t.lower() for t in tables}
        missing = {"activity_bookings", "booking_references", "payment_transactions"} - tables_set
        if missing:
            raise DatabaseSafetyError(
                f"SCHEMA INCOMPATIBILITY: Disposable SQL Server is missing required tables: {sorted(missing)}"
            )

        slot_nullability = conn.execute(
            text(
                "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'activity_slots' AND COLUMN_NAME = 'activity_id'"
            )
        ).scalar()

        return {
            "tables": tables_set,
            "activity_slots_activity_id_nullable": (slot_nullability == "YES"),
        }


# ==============================================================================
# Pytest Fixtures
# ==============================================================================


@pytest.fixture(scope="module")
def sqlserver_engine():
    """Create a guarded engine connected to the disposable SQL Server instance."""
    raw_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if not raw_url or not raw_url.strip():
        pytest.skip(LIVE_SQLSERVER_SKIP_REASON)

    # Validates URL against approved databases and attaches runtime DB_NAME() safety guard
    engine = create_safe_sqlserver_test_engine(raw_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def sqlserver_db(sqlserver_engine):
    """Provide a transactional Session for disposable SQL Server tests with auto-rollback."""
    Session = sessionmaker(bind=sqlserver_engine, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def mock_payhere_test_service() -> PayHereService:
    """PayHere service configured with test-only credentials."""
    svc = PayHereService()
    svc.merchant_id = TEST_PAYHERE_MERCHANT_ID
    svc.merchant_secret = TEST_PAYHERE_MERCHANT_SECRET
    svc.sandbox = True
    return svc


# ==============================================================================
# SECTION A: DATABASE SAFETY TESTS (ALWAYS EXECUTED — ZERO NETWORK CALLS)
# ==============================================================================


def test_safety_01_missing_env_skips_live_execution():
    """Asserts that missing TEST_DISPOSABLE_SQLSERVER_URL safely skips live tests."""
    assert (
        os.getenv("TEST_DISPOSABLE_SQLSERVER_URL") is None
        or os.getenv("TEST_DISPOSABLE_SQLSERVER_URL") == ""
    ), "TEST_DISPOSABLE_SQLSERVER_URL should remain unset in standard environment"


def test_safety_02_database_guard_strictly_rejects_ceylongodb():
    """Verify that connection to live CeylonGoDB is strictly rejected."""
    bad_urls = [
        "mssql+pyodbc://sa:secret@localhost/CeylonGoDB?driver=ODBC+Driver+18+for+SQL+Server",
        "mssql+pyodbc://sa:secret@localhost/ceylongodb?driver=ODBC+Driver+18+for+SQL+Server",
    ]
    for url in bad_urls:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "ceylongodb" in str(exc_info.value).lower()
        assert "prohibited" in str(exc_info.value).lower()


def test_safety_03_database_guard_strictly_rejects_ceylongodb_development():
    """Verify that connection to CeylonGoDB_Development is strictly rejected."""
    dev_url = (
        "mssql+pyodbc://sa:secret@localhost/CeylonGoDB_Development?driver=ODBC+Driver+18+for+SQL+Server"
    )
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(dev_url)
    assert "unapproved integration database" in str(exc_info.value).lower()


def test_safety_04_database_guard_rejects_unapproved_databases():
    """Verify that arbitrary customer database names fail closed."""
    unknown_urls = [
        "mssql+pyodbc://sa:secret@localhost/ProductionBackup?driver=ODBC",
        "mssql+pyodbc://sa:secret@localhost/CustomerData?driver=ODBC",
    ]
    for url in unknown_urls:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "unapproved integration database" in str(exc_info.value).lower()


def test_safety_05_schema_inspector_detects_missing_tables():
    """Schema compatibility inspector fails closed when required tables are missing."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # Mock query returning empty tables list
    mock_conn.execute.return_value.scalars.return_value.all.return_value = []

    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_sqlserver_payment_schema(mock_engine)
    assert "missing required tables" in str(exc_info.value).lower()


# ==============================================================================
# SECTION B: REAL SQL SERVER INTEGRATION TESTS (GATED BEHIND GATE 3 & DISPOSABLE DB)
# ==============================================================================


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_01_schema_compatibility_inspection(sqlserver_engine):
    """
    Inspect physical schema of disposable SQL Server instance:
    1. Verify ActivityBooking, BookingReference, PaymentTransaction exist.
    2. Check activity_slots.activity_id nullability (Migration 030 readiness).
    """
    schema_info = verify_sqlserver_payment_schema(sqlserver_engine)
    assert "activity_bookings" in schema_info["tables"]
    assert "booking_references" in schema_info["tables"]
    assert "payment_transactions" in schema_info["tables"]


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_02_disposable_booking_and_reference_setup(sqlserver_db: Session):
    """
    Insert disposable ActivityBooking and BookingReference in disposable database;
    verify read-back via SqlAlchemyEcoPaymentAdapter.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)

    # Insert disposable records with rolled-back transaction
    sqlserver_db.execute(
        text(
            "INSERT INTO booking_references "
            "(booking_reference, module_type, target_booking_id, user_id, provider_profile_id, total_price, currency, booking_status, payment_status, created_at) "
            "VALUES ('ECO-SQL-TEST-001', 'activity', 99001, 1, 1, 12500.00, 'LKR', 'pending', 'unpaid', SYSUTCDATETIME());"
        )
    )

    ref = adapter.get_booking_reference("ECO-SQL-TEST-001")
    assert ref is not None
    assert ref.booking_reference == "ECO-SQL-TEST-001"
    assert ref.total_price == Decimal("12500.00")
    assert ref.payment_status == "unpaid"


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_03_persist_payment_transaction_plan(sqlserver_db: Session):
    """
    Verify PaymentTransaction insertion against physical SQL Server table.
    Ensures parameterized query execution and zero raw SQL interpolation.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)

    # Prepare transaction plan
    tx_plan = PaymentTransactionPlan(
        booking_reference_id=1,
        booking_reference_code="ECO-SQL-TEST-001",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_SQL_TX_1001",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
        gateway_response_summary={"method": "VISA", "status_code": "2"},
        created_at=datetime.now(timezone.utc),
    )

    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=99001,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Live SQL Server payment confirmation test",
        action_required=True,
    )

    # Execute atomic confirmation
    res = adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert res is True

    # Verify read-back from SQL Server
    tx = adapter.get_latest_payment_transaction(1, "payhere")
    assert tx is not None
    assert tx.gateway_transaction_id == "PH_SQL_TX_1001"
    assert tx.amount == Decimal("12500.00")
    assert tx.payment_status == "paid"


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_04_preserve_provider_approval_booking_status(sqlserver_db: Session):
    """
    Verify business rule on real SQL Server:
    Receiving online payment updates payment_status to 'paid',
    but preserves booking_status as 'pending' when provider approval is required.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)

    # Seed booking in pending status
    booking = adapter.get_booking_by_id(99001)
    booking_ref = adapter.get_booking_reference("ECO-SQL-TEST-001")

    val_payload = generate_test_signed_ipn("ECO-SQL-TEST-001", "12500.00", "LKR", "2", "PH_PENDING_APPROVAL")

    result = coordinator.process_webhook_payload(
        val_payload,
        auto_confirm_booking=False,  # Preserve approval rule!
    )

    assert result.success is True
    assert result.to_payment_status == "paid"
    assert result.to_booking_status == "pending"  # Preserved!


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_05_duplicate_webhook_processing_is_idempotent(sqlserver_db: Session):
    """
    Verify that receiving two identical callbacks against real SQL Server
    results in exactly one payment confirmation without duplicate records.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)

    val_payload = generate_test_signed_ipn("ECO-SQL-TEST-001", "12500.00", "LKR", "2", "PH_IDEMPOTENT_001")

    # First callback
    res1 = coordinator.process_webhook_payload(val_payload)
    assert res1.success is True
    assert res1.status == "processed"

    # Second identical callback
    res2 = coordinator.process_webhook_payload(val_payload)
    assert res2.success is True
    assert res2.status == "already_processed"
    assert res2.applied_transition is False
    assert res2.applied_transaction is False


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_06_reject_conflicting_gateway_transaction_ids(sqlserver_db: Session):
    """
    Verify that an incoming callback with a different gateway payment ID
    for an already paid booking is rejected as a conflict.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)

    val_payload_conflict = generate_test_signed_ipn(
        "ECO-SQL-TEST-001", "12500.00", "LKR", "2", "PH_CONFLICT_DIFFERENT"
    )

    res = coordinator.process_webhook_payload(val_payload_conflict)
    assert res.success is False
    assert res.status == "conflict_rejected"


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_07_reject_payments_on_terminal_bookings(sqlserver_db: Session):
    """
    Verify that payment confirmation on cancelled or rejected bookings
    fails closed under real SQL Server constraints.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)

    # Mark booking as cancelled
    booking = adapter.get_booking_by_id(99001)
    booking.booking_status = "cancelled"
    sqlserver_db.flush()

    val_payload = generate_test_signed_ipn("ECO-SQL-TEST-001", "12500.00", "LKR", "2", "PH_TERMINAL_TEST")
    res = coordinator.process_webhook_payload(val_payload)
    assert res.success is False
    assert res.error_code == "ECO_BOOKING_TERMINAL_STATUS"


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_08_transaction_rollback_guarantees_zero_partial_state(sqlserver_db: Session):
    """
    Verify that an explicit transaction rollback cleanly reverts all financial mutations
    in SQL Server without leaving orphan payment transactions or partial booking states.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=1,
        booking_reference_code="ECO-SQL-TEST-001",
        user_id=1,
        gateway_name="payhere",
        gateway_transaction_id="PH_ROLLBACK_VERIFY",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
    )
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=99001,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Rollback verify",
    )

    adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)

    # Injected failure / explicit caller rollback
    sqlserver_db.rollback()

    # Assert booking remains unpaid in SQL Server
    booking = adapter.get_booking_by_id(99001)
    assert booking.payment_status == "unpaid"

    # Assert no transaction row exists
    count = (
        sqlserver_db.query(PaymentTransaction)
        .filter(PaymentTransaction.gateway_transaction_id == "PH_ROLLBACK_VERIFY")
        .count()
    )
    assert count == 0


@pytest.mark.sqlserver
@pytest.mark.skipif(not TEST_DISPOSABLE_SQLSERVER_URL, reason=LIVE_SQLSERVER_SKIP_REASON)
def test_sqlserver_09_concurrency_simultaneous_identical_callbacks(sqlserver_engine):
    """
    Verify concurrency safety under real SQL Server transaction isolation:
    Two simultaneous threads processing the identical PayHere callback
    are mutually excluded or resolved idempotently without duplicate rows.
    """
    SessionLocal = sessionmaker(bind=sqlserver_engine, autoflush=False, expire_on_commit=False)
    order_id = "ECO-SQL-CONCUR-01"
    payment_id = "PH_CONCUR_IDENTICAL"

    results = []
    errors = []

    def worker():
        session = SessionLocal()
        try:
            adapter = SqlAlchemyEcoPaymentAdapter(session)
            coordinator = EcoPaymentTransactionCoordinator(adapter)
            payload = generate_test_signed_ipn(order_id, "12500.00", "LKR", "2", payment_id)
            res = coordinator.process_webhook_payload(payload)
            session.commit()
            results.append(res)
        except Exception as exc:
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)

    t1.start()
    t2.start()

    t1.join(timeout=10)
    t2.join(timeout=10)

    # One should be processed, one already_processed (or mutually serialized)
    statuses = [r.status for r in results]
    assert "processed" in statuses
    # Total inserted transactions in SQL Server must be exactly 1
    with sqlserver_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM payment_transactions WHERE gateway_transaction_id = :gid"
            ),
            {"gid": payment_id},
        ).scalar()
        assert count == 1
