"""
Module 04 — Eco-Tourism Real SQL Server Payment Integration Tests.
==================================================================
Target: tests/integration/test_eco_payment_sqlserver_integration.py

STRICT SAFETY AND GATE 3 AUTHORIZATION RULES:
1. Live SQL Server tests are ONLY executed when:
   - RUN_SQLSERVER_INTEGRATION_TESTS=1 AND ECO_PAYMENT_SQLSERVER=1
     (or TEST_DISPOSABLE_SQLSERVER_URL pointing to an approved database).
2. Live tests are AUTOMATICALLY SKIPPED when opt-in is unset or false.
3. NEVER connects to or mutates:
   - CeylonGoDB (live production)
   - CeylonGoDB_Development (active developer database)
   - SQL Server system databases (master, tempdb, model, msdb)
4. Database identity guard (app/core/database_guard.py) is enforced fail-closed.
5. All financial transactions use test-only PayHere sandbox/mock parameters.
   Production PayHere credentials are strictly forbidden.
6. Cleans up all test data reliably, leaf-to-root, without modifying non-test rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
import threading
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, text
from sqlalchemy.orm import Session

from app.core.database_guard import (
    ARTISAN_INTEGRATION_ALIAS,
    ARTISAN_INTEGRATION_DATABASE,
    DatabaseSafetyError,
    attach_runtime_database_guard,
    create_safe_sqlserver_test_engine,
    normalize_server_name,
    validate_disposable_sqlserver_url,
    verify_connected_server_identity,
    verify_safe_integration_database,
    verify_safe_integration_target,
)
from app.core.payhere import PayHereService, payhere_service
from app.database.connection import DATABASE_URL
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import User
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    PaymentTransactionPlan,
)
from app.services.eco_payment_sqlalchemy_adapter import (
    SqlAlchemyEcoPaymentAdapter,
)
from app.services.eco_payment_transaction_coordinator import (
    EcoPaymentTransactionCoordinator,
)

# ==============================================================================
# Environment & Opt-In Configuration
# ==============================================================================

APPROVED_DB_NAME = "ceylongodb_integrationtest"
APPROVED_DATABASE_SET = frozenset({APPROVED_DB_NAME})


def is_sqlserver_opted_in() -> bool:
    """Fail-closed check requiring both global and payment-specific opt-ins."""
    return (
        os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS") == "1"
        and (
            os.getenv("ECO_PAYMENT_SQLSERVER") == "1"
            or os.getenv("TEST_DISPOSABLE_SQLSERVER_URL") is not None
        )
    )


LIVE_SQLSERVER_SKIP_REASON = (
    "Live SQL Server integration tests require Gate 3 authorization, "
    "RUN_SQLSERVER_INTEGRATION_TESTS=1, and ECO_PAYMENT_SQLSERVER=1 "
    "(or TEST_DISPOSABLE_SQLSERVER_URL) pointing to an approved database."
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
    secret_hash = (
        hashlib.md5(TEST_PAYHERE_MERCHANT_SECRET.encode("utf-8"))
        .hexdigest()
        .upper()
    )
    combo = (
        f"{TEST_PAYHERE_MERCHANT_ID}{order_id}{amount}{currency}"
        f"{status_code}{secret_hash}"
    )
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


def verify_target(connection, expected_db: str | None = None) -> None:
    """
    Fail-closed runtime verification:
    - Verifies current DB name is strictly allowlisted.
    - Verifies live production CeylonGoDB is strictly rejected.
    - Verifies connected server instance identity (machine + instance name).
    - Sets lock timeout (6000ms) to prevent indefinite hangs.
    """
    target = (expected_db or APPROVED_DB_NAME).strip().lower()
    db_name = connection.scalar(text("SELECT DB_NAME()"))
    verify_safe_integration_database(
        db_name, approved_databases=frozenset({target})
    )
    verify_connected_server_identity(
        connection, server_alias=connection.engine.url.host
    )
    connection.exec_driver_sql("SET LOCK_TIMEOUT 6000")


def verify_sqlserver_payment_schema(engine) -> dict[str, Any]:
    """
    Inspects physical SQL Server schema to verify tables and columns
    required for Module 04 online payment integration.
    Fails closed if critical tables or columns are missing.
    """
    with engine.connect() as conn:
        verify_target(conn)
        tables = conn.execute(
            text(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME IN "
                "('activity_bookings', 'booking_references', "
                "'payment_transactions', 'activity_slots')"
            )
        ).scalars().all()
        tables_set = {t.lower() for t in tables}
        missing = {
            "activity_bookings",
            "booking_references",
            "payment_transactions",
        } - tables_set
        if missing:
            raise DatabaseSafetyError(
                "SCHEMA INCOMPATIBILITY: Database is missing required "
                f"tables: {sorted(missing)}"
            )

        slot_nullability = conn.execute(
            text(
                "SELECT IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = 'activity_slots' AND "
                "COLUMN_NAME = 'activity_id'"
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
    """Create a guarded engine connected to the disposable or integration SQL Server."""
    raw_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if raw_url and raw_url.strip():
        target_db = validate_disposable_sqlserver_url(
            raw_url, approved_databases=APPROVED_DATABASE_SET
        )
        engine = create_safe_sqlserver_test_engine(
            raw_url,
            approved_databases=APPROVED_DATABASE_SET,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        expected_db = target_db
    else:
        server = os.getenv(
            "ECO_SQLSERVER_TEST_SERVER", ARTISAN_INTEGRATION_ALIAS
        )
        database = ARTISAN_INTEGRATION_DATABASE
        opt_in = os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS")

        verify_safe_integration_target(server, database, opt_in)
        if normalize_server_name(server) != ARTISAN_INTEGRATION_ALIAS.lower():
            raise DatabaseSafetyError(
                "Eco payment SQL Server integration tests require approved "
                f"alias '{ARTISAN_INTEGRATION_ALIAS}'."
            )

        db_url = DATABASE_URL.set(host=server, database=database)
        engine = create_engine(
            db_url,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        attach_runtime_database_guard(
            engine,
            approved_databases=APPROVED_DATABASE_SET,
            expected_db=database,
            server_alias=server,
        )
        expected_db = database

    try:
        with engine.connect() as conn:
            verify_target(conn, expected_db=expected_db)
    except Exception:
        engine.dispose()
        raise

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sqlserver_db(sqlserver_engine):
    """Provide a scoped Session for disposable SQL Server tests."""
    with Session(
        sqlserver_engine, autoflush=False, expire_on_commit=False
    ) as session:
        verify_target(session.connection())
        try:
            yield session
        finally:
            session.rollback()


def _cleanup_payment_context(db: Session, context: dict) -> None:
    """Reliably cleans up test records in leaf-to-root order."""
    ref_id = context.get("booking_reference_id")
    booking_id = context.get("booking_id")
    slot_id = context.get("slot_id")
    activity_id = context.get("activity_id")
    user_id = context.get("user_id")
    provider_user_id = context.get("provider_user_id")
    provider_id = context.get("provider_id")

    try:
        db.rollback()
    except Exception:
        pass

    try:
        if ref_id:
            db.execute(
                delete(PaymentTransaction).where(
                    PaymentTransaction.booking_reference_id == ref_id
                )
            )
            db.execute(
                delete(BookingReference).where(BookingReference.id == ref_id)
            )

        if booking_id:
            db.execute(
                delete(ActivityBooking).where(ActivityBooking.id == booking_id)
            )

        if slot_id:
            db.execute(
                delete(ActivitySlot).where(ActivitySlot.id == slot_id)
            )

        if activity_id:
            db.execute(
                delete(Activity).where(Activity.id == activity_id)
            )

        if provider_id:
            db.execute(
                delete(ProviderProfile).where(ProviderProfile.id == provider_id)
            )

        users_to_delete = list(filter(None, [user_id, provider_user_id]))
        if users_to_delete:
            db.execute(delete(User).where(User.id.in_(users_to_delete)))

        db.commit()
    except Exception:
        db.rollback()


@pytest.fixture
def seeded_payment_booking(sqlserver_db: Session):
    """
    Creates an isolated test booking and booking reference with unique IDs.
    Cleans up all child payment transactions, references, bookings, slots,
    activities, profiles, and users in finally block. Never leaves data behind.
    Handles partial setup failures gracefully by populating context progressively.
    """
    tag = uuid4().hex[:12]
    ref_code = f"ECO-PAY-{tag}"

    context = {
        "booking_reference": ref_code,
        "amount": Decimal("12500.00"),
    }

    try:
        # 1. Create test buyer user
        buyer = User(
            full_name=f"Test Buyer {tag}",
            email=f"test-buyer-{tag}@example.test",
            role="traveler",
            is_active=True,
        )
        sqlserver_db.add(buyer)
        sqlserver_db.flush()
        context["user_id"] = buyer.id

        # 2. Create test provider user & profile
        provider_user = User(
            full_name=f"Test Provider {tag}",
            email=f"test-provider-{tag}@example.test",
            role="provider",
            is_active=True,
        )
        sqlserver_db.add(provider_user)
        sqlserver_db.flush()
        context["provider_user_id"] = provider_user.id

        provider = ProviderProfile(
            user_id=provider_user.id,
            provider_type="eco_tourism",
            approval_status="approved",
            business_name=f"Test Eco Provider {tag}",
            district="Colombo",
            phone="0771234567",
        )
        sqlserver_db.add(provider)
        sqlserver_db.flush()
        context["provider_id"] = provider.id

        # 3. Create test activity & slot
        activity = Activity(
            provider_profile_id=provider.id,
            title=f"Test Eco Activity {tag}",
            description=f"Test Eco Description {tag}",
            category="adventure",
            district="Colombo",
            city="Colombo",
            meeting_point="Test Meeting Point",
            duration_minutes=120,
            minimum_age=10,
            pricing_type="per_person",
            price=Decimal("6250.00"),
            currency="LKR",
            approval_status="approved",
            is_active=True,
        )
        sqlserver_db.add(activity)
        sqlserver_db.flush()
        context["activity_id"] = activity.id

        start_time = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) + timedelta(days=2)
        slot = ActivitySlot(
            activity_id=activity.id,
            starts_at=start_time,
            ends_at=start_time + timedelta(hours=2),
            capacity=10,
            reserved_count=2,
            is_active=True,
        )
        sqlserver_db.add(slot)
        sqlserver_db.flush()
        context["slot_id"] = slot.id

        # 4. Create test activity booking
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=buyer.id,
            participants=2,
            total_price=Decimal("12500.00"),
            currency="LKR",
            payment_method="online",
            payment_status="unpaid",
            booking_status="pending",
        )
        sqlserver_db.add(booking)
        sqlserver_db.flush()
        context["booking_id"] = booking.id

        # 5. Create test booking reference
        booking_ref = BookingReference(
            booking_reference=ref_code,
            module_type="activity",
            target_booking_id=booking.id,
            user_id=buyer.id,
            provider_profile_id=provider.id,
            total_price=Decimal("12500.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
        )
        sqlserver_db.add(booking_ref)
        sqlserver_db.flush()
        context["booking_reference_id"] = booking_ref.id

        sqlserver_db.commit()

        yield context
    finally:
        _cleanup_payment_context(sqlserver_db, context)


@pytest.fixture(autouse=True)
def configure_test_payhere_credentials(monkeypatch):
    """
    Ensure global payhere_service and environment variables use the
    isolated test merchant credentials for all tests in this suite.
    """
    monkeypatch.setenv("PAYHERE_MERCHANT_ID", TEST_PAYHERE_MERCHANT_ID)
    monkeypatch.setenv("PAYHERE_MERCHANT_SECRET", TEST_PAYHERE_MERCHANT_SECRET)
    monkeypatch.setattr(
        payhere_service, "merchant_id", TEST_PAYHERE_MERCHANT_ID
    )
    monkeypatch.setattr(
        payhere_service, "merchant_secret", TEST_PAYHERE_MERCHANT_SECRET
    )


@pytest.fixture
def test_payhere_service() -> PayHereService:
    """PayHere service configured with test-only credentials."""
    svc = PayHereService()
    svc.merchant_id = TEST_PAYHERE_MERCHANT_ID
    svc.merchant_secret = TEST_PAYHERE_MERCHANT_SECRET
    svc.sandbox = True
    return svc


# Retain alias for backward compatibility
mock_payhere_test_service = test_payhere_service


# ==============================================================================
# SECTION A: DATABASE SAFETY TESTS (ALWAYS EXECUTED — ZERO NETWORK CALLS)
# ==============================================================================


def test_safety_01_missing_env_skips_live_execution(monkeypatch):
    """Asserts that missing opt-in safely skips live tests."""
    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.delenv("ECO_PAYMENT_SQLSERVER", raising=False)
    monkeypatch.delenv("TEST_DISPOSABLE_SQLSERVER_URL", raising=False)
    assert not is_sqlserver_opted_in(), (
        "Integration opt-in must be false when environment variables are cleared"
    )


def test_safety_02_database_guard_strictly_rejects_ceylongodb():
    """Verify that connection to live CeylonGoDB is strictly rejected."""
    bad_urls = [
        "mssql+pyodbc://sa:secret@localhost/CeylonGoDB?"
        "driver=ODBC+Driver+18+for+SQL+Server",
        "mssql+pyodbc://sa:secret@localhost/ceylongodb?"
        "driver=ODBC+Driver+18+for+SQL+Server",
    ]
    for url in bad_urls:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "ceylongodb" in str(exc_info.value).lower()
        assert "prohibited" in str(exc_info.value).lower()


def test_safety_03_database_guard_strictly_rejects_ceylongodb_development():
    """Verify that connection to CeylonGoDB_Development is strictly rejected."""
    dev_url = (
        "mssql+pyodbc://sa:secret@localhost/CeylonGoDB_Development?"
        "driver=ODBC+Driver+18+for+SQL+Server"
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
    mock_conn = MagicMock(spec=["scalar", "execute", "engine", "exec_driver_sql"])
    mock_conn.scalar.return_value = "ceylongodb_integrationtest"
    mock_conn.engine.url.host = "localhost\\SQLEXPRESS"
    mock_conn.execute.return_value.fetchone.return_value = (
        "ceylongodb_integrationtest",
        "VISHNUGA-KAJARASH\\SQLEXPRESS",
        "VISHNUGA-KAJARASH",
        "SQLEXPRESS",
    )
    mock_engine.connect.return_value.__enter__.return_value = mock_conn

    # Mock query returning empty tables list
    mock_conn.execute.return_value.scalars.return_value.all.return_value = []

    with pytest.raises(DatabaseSafetyError) as exc_info:
        verify_sqlserver_payment_schema(mock_engine)
    assert "missing required tables" in str(exc_info.value).lower()


# ==============================================================================
# SECTION B: REAL SQL SERVER INTEGRATION TESTS (GATED BEHIND OPT-IN)
# ==============================================================================


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
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


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_02_disposable_booking_and_reference_setup(
    sqlserver_db: Session, seeded_payment_booking: dict
):
    """Verify seeded ActivityBooking via SqlAlchemyEcoPaymentAdapter."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    ref_code = seeded_payment_booking["booking_reference"]

    ref = adapter.get_booking_reference(ref_code)
    assert ref is not None
    assert ref.booking_reference == ref_code
    assert ref.total_price == Decimal("12500.00")
    assert ref.payment_status == "unpaid"


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_03_persist_payment_transaction_plan(
    sqlserver_db: Session, seeded_payment_booking: dict
):
    """Verify PaymentTransaction insertion against physical SQL Server table."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    ref_id = seeded_payment_booking["booking_reference_id"]
    ref_code = seeded_payment_booking["booking_reference"]
    user_id = seeded_payment_booking["user_id"]
    booking_id = seeded_payment_booking["booking_id"]

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=ref_id,
        booking_reference_code=ref_code,
        user_id=user_id,
        gateway_name="payhere",
        gateway_transaction_id="PH_SQL_TX_1001",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
        gateway_response_summary={"method": "VISA", "status_code": "2"},
        created_at=datetime.now(timezone.utc),
    )

    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=booking_id,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Live SQL Server payment confirmation test",
        action_required=True,
    )

    res = adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)
    assert res is True

    tx = adapter.get_latest_payment_transaction(ref_id, "payhere")
    assert tx is not None
    assert tx.gateway_transaction_id == "PH_SQL_TX_1001"
    assert tx.amount == Decimal("12500.00")
    assert tx.payment_status == "paid"


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_04_preserve_provider_approval_booking_status(
    sqlserver_db: Session,
    seeded_payment_booking: dict,
    test_payhere_service: PayHereService,
):
    """
    Verify business rule on real SQL Server:
    Receiving online payment updates payment_status to 'paid',
    but preserves booking_status as 'pending' when provider approval is required.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)
    ref_code = seeded_payment_booking["booking_reference"]

    val_payload = generate_test_signed_ipn(
        ref_code, "12500.00", "LKR", "2", "PH_PENDING_APPROVAL"
    )

    result = coordinator.process_webhook_payload(
        val_payload,
        payhere_svc=test_payhere_service,
        auto_confirm_booking=False,  # Preserve approval rule!
    )

    assert result.success is True
    assert result.to_payment_status == "paid"
    assert result.to_booking_status == "pending"  # Preserved!


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_05_duplicate_webhook_processing_is_idempotent(
    sqlserver_db: Session,
    seeded_payment_booking: dict,
    test_payhere_service: PayHereService,
):
    """
    Verify that receiving two identical callbacks against real SQL Server
    results in exactly one payment confirmation without duplicate records.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)
    ref_code = seeded_payment_booking["booking_reference"]

    val_payload = generate_test_signed_ipn(
        ref_code, "12500.00", "LKR", "2", "PH_IDEMPOTENT_001"
    )

    # First callback
    res1 = coordinator.process_webhook_payload(
        val_payload, payhere_svc=test_payhere_service
    )
    assert res1.success is True
    assert res1.status == "processed"

    # Second identical callback
    res2 = coordinator.process_webhook_payload(
        val_payload, payhere_svc=test_payhere_service
    )
    assert res2.success is True
    assert res2.status == "already_processed"
    assert res2.applied_transition is False
    assert res2.applied_transaction is False


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_06_reject_conflicting_gateway_transaction_ids(
    sqlserver_db: Session,
    seeded_payment_booking: dict,
    test_payhere_service: PayHereService,
):
    """
    Verify that an incoming callback with a different gateway payment ID
    for an already paid booking is rejected as a conflict.
    """
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)
    ref_code = seeded_payment_booking["booking_reference"]

    # First mark as paid
    val_payload = generate_test_signed_ipn(
        ref_code, "12500.00", "LKR", "2", "PH_PAID_INITIAL"
    )
    res_initial = coordinator.process_webhook_payload(
        val_payload, payhere_svc=test_payhere_service
    )
    assert res_initial.success is True
    assert res_initial.status == "processed"

    val_payload_conflict = generate_test_signed_ipn(
        ref_code, "12500.00", "LKR", "2", "PH_CONFLICT_DIFFERENT"
    )

    res = coordinator.process_webhook_payload(
        val_payload_conflict, payhere_svc=test_payhere_service
    )
    assert res.success is False
    assert res.status == "conflict_rejected"


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_07_reject_payments_on_terminal_bookings(
    sqlserver_db: Session,
    seeded_payment_booking: dict,
    test_payhere_service: PayHereService,
):
    """Verify that payment confirmation on cancelled bookings fails closed."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    coordinator = EcoPaymentTransactionCoordinator(adapter)
    booking_id = seeded_payment_booking["booking_id"]
    ref_code = seeded_payment_booking["booking_reference"]

    # Mark booking as cancelled
    booking = adapter.get_booking_by_id(booking_id)
    booking.booking_status = "cancelled"
    sqlserver_db.flush()

    val_payload = generate_test_signed_ipn(
        ref_code, "12500.00", "LKR", "2", "PH_TERMINAL_TEST"
    )
    res = coordinator.process_webhook_payload(
        val_payload, payhere_svc=test_payhere_service
    )
    assert res.success is False
    assert res.error_code == "ECO_BOOKING_TERMINAL_STATUS"


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_08_transaction_rollback_guarantees_zero_partial_state(
    sqlserver_db: Session, seeded_payment_booking: dict
):
    """Verify explicit rollback reverts all financial mutations in SQL Server."""
    adapter = SqlAlchemyEcoPaymentAdapter(sqlserver_db)
    ref_id = seeded_payment_booking["booking_reference_id"]
    ref_code = seeded_payment_booking["booking_reference"]
    user_id = seeded_payment_booking["user_id"]
    booking_id = seeded_payment_booking["booking_id"]

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=ref_id,
        booking_reference_code=ref_code,
        user_id=user_id,
        gateway_name="payhere",
        gateway_transaction_id="PH_ROLLBACK_VERIFY",
        amount=Decimal("12500.00"),
        currency="LKR",
        payment_status="paid",
    )
    trans_plan = BookingPaymentStateTransitionPlan(
        booking_id=booking_id,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Rollback verify",
    )

    adapter.execute_payment_confirmation_atomic(trans_plan, tx_plan)

    # Injected failure / explicit caller rollback
    sqlserver_db.rollback()

    booking = adapter.get_booking_by_id(booking_id)
    assert booking.payment_status == "unpaid"

    count = (
        sqlserver_db.query(PaymentTransaction)
        .filter(PaymentTransaction.gateway_transaction_id == "PH_ROLLBACK_VERIFY")
        .count()
    )
    assert count == 0


@pytest.mark.skipif(
    not is_sqlserver_opted_in(), reason=LIVE_SQLSERVER_SKIP_REASON
)
def test_sqlserver_09_concurrency_simultaneous_identical_callbacks(
    sqlserver_engine,
    seeded_payment_booking: dict,
    test_payhere_service: PayHereService,
):
    """Verify concurrency safety under real SQL Server transaction isolation."""
    order_id = seeded_payment_booking["booking_reference"]
    payment_id = f"PH_CONCUR_{uuid4().hex[:8]}"

    results = []
    errors = []

    def worker():
        with Session(
            sqlserver_engine, autoflush=False, expire_on_commit=False
        ) as session:
            verify_target(session.connection())
            try:
                adapter = SqlAlchemyEcoPaymentAdapter(session)
                coordinator = EcoPaymentTransactionCoordinator(adapter)
                payload = generate_test_signed_ipn(
                    order_id, "12500.00", "LKR", "2", payment_id
                )
                res = coordinator.process_webhook_payload(
                    payload, payhere_svc=test_payhere_service
                )
                session.commit()
                results.append(res)
            except Exception as exc:
                session.rollback()
                errors.append(exc)

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
        verify_target(conn)
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM payment_transactions "
                "WHERE gateway_transaction_id = :gid"
            ),
            {"gid": payment_id},
        ).scalar()
        assert count == 1
