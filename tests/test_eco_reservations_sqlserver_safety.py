"""
Unit Tests for Hardened Eco Reservations SQL Server Safety Protections.
======================================================================
Tests verify offline (using mocks and synthetic connections):
1. Opt-in enforcement: requires both RUN_SQLSERVER_INTEGRATION_TESTS=1
   and ECO_PHASE03A_SQLSERVER=1.
2. Target verification: strictly rejects live production database CeylonGoDB,
   unapproved databases, and wrong server instance identities.
3. Target verification: accepts approved target CeylonGoDB_IntegrationTest
   on approved artisan server identity and sets lock timeout.
4. Data isolation and reliable cleanup: verifies leaf-to-root deletion of all
   16 entity levels without modifying or deleting non-test records.
5. Setup failure recovery: cleans up partially created users and profiles.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core.database_guard import DatabaseSafetyError


def make_mock_connection(
    current_db: str = "ceylongodb_integrationtest",
    server_name: str = "VISHNUGA-KAJARASH\\SQLEXPRESS",
    machine_name: str = "VISHNUGA-KAJARASH",
    instance_name: str = "SQLEXPRESS",
    server_host: str = "localhost\\SQLEXPRESS",
) -> MagicMock:
    """Create a mock SQLAlchemy Connection without connect/cursor attributes."""
    mock_conn = MagicMock(spec=["scalar", "execute", "engine", "exec_driver_sql"])
    mock_conn.scalar.return_value = current_db
    mock_conn.engine.url.host = server_host
    mock_conn.execute.return_value.fetchone.return_value = (
        current_db,
        server_name,
        machine_name,
        instance_name,
    )
    return mock_conn


def test_eco_reservations_opt_in_requirements(monkeypatch):
    """Requires both RUN_SQLSERVER_INTEGRATION_TESTS=1 and ECO_PHASE03A_SQLSERVER=1."""
    from tests.test_eco_reservations_sqlserver import is_sqlserver_opted_in

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.delenv("ECO_PHASE03A_SQLSERVER", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.delenv("ECO_PHASE03A_SQLSERVER", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.setenv("ECO_PHASE03A_SQLSERVER", "1")
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "0")
    monkeypatch.setenv("ECO_PHASE03A_SQLSERVER", "1")
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("ECO_PHASE03A_SQLSERVER", "1")
    assert is_sqlserver_opted_in() is True


def test_eco_reservations_target_verification_rejects_ceylongodb():
    """verify_target strictly rejects live production database CeylonGoDB."""
    from tests.test_eco_reservations_sqlserver import verify_target

    mock_conn = make_mock_connection(current_db="CeylonGoDB")

    with pytest.raises(
        DatabaseSafetyError, match="LIVE DATABASE SAFETY VIOLATION"
    ):
        verify_target(mock_conn)


def test_eco_reservations_target_verification_rejects_unapproved_db():
    """verify_target strictly rejects arbitrary unapproved databases."""
    from tests.test_eco_reservations_sqlserver import verify_target

    mock_conn = make_mock_connection(current_db="RandomTestDB")

    with pytest.raises(
        DatabaseSafetyError, match="UNAPPROVED INTEGRATION DATABASE"
    ):
        verify_target(mock_conn)


def test_eco_target_verification_rejects_wrong_server_identity():
    """verify_target rejects wrong server identity even on approved DB name."""
    from tests.test_eco_reservations_sqlserver import verify_target

    mock_conn = make_mock_connection(
        server_name="WRONG-MACHINE\\SQLEXPRESS",
        machine_name="WRONG-MACHINE",
        instance_name="SQLEXPRESS",
    )

    with pytest.raises(
        DatabaseSafetyError, match="LIVE IDENTITY SAFETY VIOLATION"
    ):
        verify_target(mock_conn)


def test_eco_reservations_target_verification_accepts_valid_target():
    """verify_target passes on approved DB and identity, setting lock timeout."""
    from tests.test_eco_reservations_sqlserver import verify_target

    mock_conn = make_mock_connection()
    verify_target(mock_conn)
    mock_conn.exec_driver_sql.assert_called_with("SET LOCK_TIMEOUT 6000")


def test_eco_cleanup_targets_all_child_tables_in_order():
    """_perform_isolated_cleanup executes deletions in leaf-to-root order."""
    from tests.test_eco_reservations_sqlserver import _perform_isolated_cleanup

    executed_tables = []
    mock_engine = MagicMock()
    mock_db = MagicMock()
    mock_conn = make_mock_connection()
    mock_db.connection.return_value = mock_conn

    def record_delete(stmt):
        if hasattr(stmt, "table"):
            executed_tables.append(stmt.table.name)
        return MagicMock()

    mock_db.execute.side_effect = record_delete
    mock_db.get.return_value = None

    state = {
        "slot_id": 101,
        "listing_id": 202,
        "owner_id": 303,
        "owner_user_id": 404,
        "buyer_ids": [505, 506],
    }

    with patch(
        "tests.test_eco_reservations_sqlserver.Session", return_value=mock_db
    ):
        mock_db.__enter__.return_value = mock_db
        _perform_isolated_cleanup(mock_engine, state)

    expected_table_order = [
        "eco_refund_reconciliations",
        "payment_refunds",
        "eco_promotion_attributions",
        "booking_status_history",
        "payment_transactions",
        "booking_references",
        "eco_cancellation_snapshots",
        "eco_reservations",
        "eco_slot_clearances",
        "eco_availability_slots",
        "eco_cancellation_policies",
        "eco_age_limits",
        "eco_experiences",
        "notifications",
        "provider_profiles",
        "users",
    ]
    assert executed_tables == expected_table_order
    mock_db.commit.assert_called_once()


def test_eco_cleanup_handles_partial_setup_failure():
    """_perform_isolated_cleanup safely cleans up users on partial setup."""
    from tests.test_eco_reservations_sqlserver import _perform_isolated_cleanup

    executed_tables = []
    mock_engine = MagicMock()
    mock_db = MagicMock()
    mock_conn = make_mock_connection()
    mock_db.connection.return_value = mock_conn

    def record_delete(stmt):
        if hasattr(stmt, "table"):
            executed_tables.append(stmt.table.name)
        return MagicMock()

    mock_db.execute.side_effect = record_delete
    mock_db.get.return_value = None

    partial_state = {
        "slot_id": None,
        "listing_id": None,
        "owner_id": 303,
        "owner_user_id": 404,
        "buyer_ids": [505],
    }

    with patch(
        "tests.test_eco_reservations_sqlserver.Session", return_value=mock_db
    ):
        mock_db.__enter__.return_value = mock_db
        _perform_isolated_cleanup(mock_engine, partial_state)

    assert executed_tables == ["notifications", "provider_profiles", "users"]
    mock_db.commit.assert_called_once()


def test_eco_sqlserver_integration_opt_in_requirements(monkeypatch):
    """Requires both global and suite-specific opt-ins."""
    from tests.test_eco_sqlserver_integration import is_sqlserver_opted_in

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.delenv("ECO_SQLSERVER_INTEGRATION", raising=False)
    monkeypatch.delenv("TEST_DISPOSABLE_SQLSERVER_URL", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.delenv("ECO_SQLSERVER_INTEGRATION", raising=False)
    monkeypatch.delenv("TEST_DISPOSABLE_SQLSERVER_URL", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.setenv("ECO_SQLSERVER_INTEGRATION", "1")
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("ECO_SQLSERVER_INTEGRATION", "1")
    assert is_sqlserver_opted_in() is True


def test_eco_payment_sqlserver_integration_opt_in_requirements(monkeypatch):
    """Requires both RUN_SQLSERVER_INTEGRATION_TESTS=1 and ECO_PAYMENT_SQLSERVER=1."""
    from tests.integration.test_eco_payment_sqlserver_integration import (
        is_sqlserver_opted_in,
    )

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.delenv("ECO_PAYMENT_SQLSERVER", raising=False)
    monkeypatch.delenv("TEST_DISPOSABLE_SQLSERVER_URL", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.delenv("ECO_PAYMENT_SQLSERVER", raising=False)
    monkeypatch.delenv("TEST_DISPOSABLE_SQLSERVER_URL", raising=False)
    assert is_sqlserver_opted_in() is False

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    monkeypatch.setenv("ECO_PAYMENT_SQLSERVER", "1")
    assert is_sqlserver_opted_in() is False

    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    monkeypatch.setenv("ECO_PAYMENT_SQLSERVER", "1")
    assert is_sqlserver_opted_in() is True


def test_eco_payment_cleanup_context_handles_partial_data():
    """_cleanup_payment_context executes scoped deletions and commits."""
    from tests.integration.test_eco_payment_sqlserver_integration import (
        _cleanup_payment_context,
    )

    executed_tables = []
    mock_db = MagicMock()

    def record_delete(stmt):
        if hasattr(stmt, "table"):
            executed_tables.append(stmt.table.name)
        return MagicMock()

    mock_db.execute.side_effect = record_delete

    context = {
        "booking_id": 11,
        "booking_reference_id": 22,
        "slot_id": 33,
        "activity_id": 44,
        "user_id": 55,
        "provider_id": 66,
        "provider_user_id": 77,
    }

    _cleanup_payment_context(mock_db, context)

    expected = [
        "payment_transactions",
        "booking_references",
        "activity_bookings",
        "activity_slots",
        "activities",
        "provider_profiles",
        "users",
    ]
    assert executed_tables == expected
    mock_db.commit.assert_called_once()


def test_eco_payment_cleanup_context_handles_missing_slot_and_activity():
    """_cleanup_payment_context safely skips missing slot/activity IDs."""
    from tests.integration.test_eco_payment_sqlserver_integration import (
        _cleanup_payment_context,
    )

    executed_tables = []
    mock_db = MagicMock()

    def record_delete(stmt):
        if hasattr(stmt, "table"):
            executed_tables.append(stmt.table.name)
        return MagicMock()

    mock_db.execute.side_effect = record_delete

    context = {
        "booking_id": 11,
        "booking_reference_id": 22,
        "user_id": 55,
        "provider_id": 66,
    }

    _cleanup_payment_context(mock_db, context)

    expected = [
        "payment_transactions",
        "booking_references",
        "activity_bookings",
        "provider_profiles",
        "users",
    ]
    assert executed_tables == expected
    mock_db.commit.assert_called_once()
    mock_db.rollback.assert_called_once()


def test_eco_payment_cleanup_context_handles_prior_aborted_transaction():
    """_cleanup_payment_context rolls back prior failure before deletes."""
    from tests.integration.test_eco_payment_sqlserver_integration import (
        _cleanup_payment_context,
    )

    calls = []
    mock_db = MagicMock()

    def record_rollback():
        calls.append("rollback")

    def record_delete(stmt):
        calls.append(f"delete_{stmt.table.name}")
        return MagicMock()

    def record_commit():
        calls.append("commit")

    mock_db.rollback.side_effect = record_rollback
    mock_db.execute.side_effect = record_delete
    mock_db.commit.side_effect = record_commit

    context = {
        "booking_reference_id": 99,
        "booking_id": 88,
    }

    _cleanup_payment_context(mock_db, context)

    # Initial rollback must precede delete statements
    assert calls[0] == "rollback"
    assert "delete_payment_transactions" in calls
    assert "delete_booking_references" in calls
    assert "delete_activity_bookings" in calls
    assert calls[-1] == "commit"


def test_eco_payment_merchant_id_consistency_and_validation():
    """Verify test IPN payload matches test PayHereService merchant ID."""
    from app.core.payhere import PayHereService
    from app.services.eco_payhere_webhook_security import (
        TrustedEcoPaymentContext,
        validate_eco_payhere_ipn,
    )
    from tests.integration.test_eco_payment_sqlserver_integration import (
        TEST_PAYHERE_MERCHANT_ID,
        TEST_PAYHERE_MERCHANT_SECRET,
        generate_test_signed_ipn,
    )

    from decimal import Decimal

    svc = PayHereService()
    svc.merchant_id = TEST_PAYHERE_MERCHANT_ID
    svc.merchant_secret = TEST_PAYHERE_MERCHANT_SECRET

    payload = generate_test_signed_ipn("ECO-ORDER-TEST")
    ctx = TrustedEcoPaymentContext(
        booking_id=10,
        expected_merchant_id=TEST_PAYHERE_MERCHANT_ID,
        trusted_order_id="ECO-ORDER-TEST",
        expected_amount=Decimal("12500.00"),
        expected_currency="LKR",
    )

    val_res = validate_eco_payhere_ipn(payload, ctx, payhere_svc=svc)
    assert val_res.is_valid is True

    # Intentionally mismatched merchant ID must be rejected
    bad_payload = dict(payload)
    bad_payload["merchant_id"] = "WRONG_MERCHANT_ID"
    val_bad = validate_eco_payhere_ipn(bad_payload, ctx, payhere_svc=svc)
    assert val_bad.is_valid is False
    assert val_bad.safe_error_code == "ECO_IPN_MERCHANT_MISMATCH"
