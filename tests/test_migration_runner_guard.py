"""
Unit Tests for Migration Runner Safety Guards and Isolated Target Authorization.
================================================================================
Verifies fail-closed protections in scripts/migrate.py:
- Bounded migration ceilings (037-042 allowed on integration database).
- Rejection of out-of-bounds targets (<=036, >=043, arbitrary strings).
- Rejection of unbounded migration execution (target_version=None).
- Enforcement of RUN_SQLSERVER_INTEGRATION_TESTS opt-in requirement.
- Server identity verification against ARTISAN_INTEGRATION_ALIAS.
- Live production database (CeylonGoDB) and system database rejection.
- Rejection of SQL authentication (username/password).
- Verification of historical ledger continuity requirements.
- Detection of untracked pending table conflicts for all 8 destination tables.
- Verification that legacy table names are not used as substitutes.

CRITICAL: All tests run offline with mocks and SQLite fixtures.
NEVER connects to or mutates real Microsoft SQL Server instances.
"""

from unittest.mock import MagicMock, patch
import pytest

from app.core.database_guard import (
    ARTISAN_INTEGRATION_IDENTITY,
    DatabaseSafetyError,
)
from scripts.migrate import (
    ALLOWED_INTEGRATION_TARGET_VERSIONS,
    DatabaseConfig,
    MigrationError,
    calculate_checksum,
    cmd_upgrade,
)

# ------------------------------------------------------------------------------
# Exact Eight Destination Tables for Migrations 041 and 042
# ------------------------------------------------------------------------------
DESTINATION_TABLES_041 = [
    "dbo.eco_cancellation_policies",
    "dbo.eco_cancellation_snapshots",
    "dbo.eco_age_limits",
    "dbo.eco_slot_clearances",
    "dbo.eco_refund_reconciliations",
]

DESTINATION_TABLES_042 = [
    "dbo.eco_sponsorship_captures",
    "dbo.eco_promotion_event_receipts",
    "dbo.eco_promotion_attributions",
]

ALL_EIGHT_DESTINATION_TABLES = (
    DESTINATION_TABLES_041 + DESTINATION_TABLES_042
)

# Legacy wrong names from preliminary drafts that must NOT be substituted
LEGACY_WRONG_TABLE_NAMES = [
    "dbo.eco_accommodations",
    "dbo.eco_guides",
    "dbo.eco_equipment_rentals",
    "dbo.eco_sponsorships",
    "dbo.eco_sponsorship_milestones",
    "dbo.eco_sponsorship_contributions",
]


@pytest.fixture
def mock_isolated_db_environment(tmp_path):
    """
    Sets up a mocked environment matching the approved integration target:
    - server: localhost\\SQLEXPRESS
    - database: CeylonGoDB_IntegrationTest
    - Windows Integrated Authentication (no user/password)
    - Applied migrations: 001 through 038 with exact matching filenames and checksums
    """
    mock_conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    # Live identity query: DB_NAME(), @@SERVERNAME, MachineName, InstanceName
    machine, instance = ARTISAN_INTEGRATION_IDENTITY.split("\\")
    identity_row = (
        "CeylonGoDB_IntegrationTest",
        ARTISAN_INTEGRATION_IDENTITY,
        machine,
        instance,
    )

    # Create dummy migration files on disk for 001-042 with matching names/checksums
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    applied_records = []
    for i in range(1, 43):
        content = f"-- Migration {i:03d}\nSELECT 1;\nGO\n"
        fname = f"{i:03d}_test_step.sql"
        (migrations_dir / fname).write_text(content, encoding="utf-8")
        chk = calculate_checksum(content)
        if i <= 38:
            applied_records.append(
                (f"{i:03d}", fname, chk, None, 10, "executed")
            )

    def mock_execute(sql, *args, **kwargs):
        sql_str = str(sql).strip().upper()
        if "DB_NAME()" in sql_str:
            mock_cursor.fetchone.return_value = identity_row
        elif "SP_GETAPPLOCK" in sql_str:
            mock_cursor.fetchone.return_value = (0,)
        elif "OBJECT_ID" in sql_str:
            # By default, untracked tables do not exist
            mock_cursor.fetchone.return_value = None
        elif (
            "SELECT VERSION, FILENAME" in sql_str
            or "FROM DBO.SCHEMA_MIGRATIONS" in sql_str
        ):
            mock_cursor.fetchall.return_value = applied_records
        return mock_cursor

    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchone.return_value = identity_row
    mock_cursor.fetchall.return_value = applied_records

    return mock_conn, migrations_dir


# ==============================================================================
# 1. Allowed Migration Targets (037 - 042)
# ==============================================================================

@pytest.mark.parametrize("target", ["037", "038", "039", "040", "041", "042"])
def test_guard_permits_explicitly_approved_targets(
    mock_isolated_db_environment, monkeypatch, target
):
    """Verifies that each authorized version in 037-042 passes the ceiling guard."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn), \
         patch("scripts.migrate.migration_lock") as mock_lock:

        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)

        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        exit_code = cmd_upgrade(
            cfg,
            migrations_dir,
            target_version=target,
            confirmed=True,
        )
        assert exit_code == 0
        assert target in ALLOWED_INTEGRATION_TARGET_VERSIONS


# ==============================================================================
# 2. Rejected Migration Targets
# ==============================================================================

@pytest.mark.parametrize(
    "bad_target", ["036", "043", "044", "050", "999", "invalid"]
)
def test_guard_rejects_unapproved_or_out_of_bounds_targets(
    mock_isolated_db_environment, monkeypatch, bad_target
):
    """Targets below 037, above 042, or non-numeric must fail-closed."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version=bad_target,
                confirmed=True,
            )
        assert "migration ceiling" in str(exc_info.value).lower()


# ==============================================================================
# 3. Unbounded Migration Execution Rejected
# ==============================================================================

def test_guard_rejects_unbounded_migration_execution(
    mock_isolated_db_environment, monkeypatch
):
    """Running upgrade without --target (target_version=None) must fail-closed."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version=None,
                confirmed=True,
            )
        assert "migration ceiling" in str(exc_info.value).lower()


# ==============================================================================
# 4. Opt-in Wiring Tests (RUN_SQLSERVER_INTEGRATION_TESTS)
# ==============================================================================

def test_guard_rejects_missing_opt_in(
    mock_isolated_db_environment, monkeypatch
):
    """Missing opt-in flag in config and environment must raise DatabaseSafetyError."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in=None,
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        assert "integration opt-in required" in str(exc_info.value).lower()


@pytest.mark.parametrize("invalid_opt_in", ["0", "false", "no", "disabled", ""])
def test_guard_rejects_invalid_opt_in_values(
    mock_isolated_db_environment, monkeypatch, invalid_opt_in
):
    """Non-truthy opt-in values must fail closed."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", invalid_opt_in)

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in=invalid_opt_in,
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        assert "integration opt-in required" in str(exc_info.value).lower()


def test_database_config_from_env_wires_opt_in(monkeypatch):
    """Verifies DatabaseConfig.from_env reads RUN_SQLSERVER_INTEGRATION_TESTS."""
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")
    cfg = DatabaseConfig.from_env()
    assert cfg.integration_opt_in == "1"

    monkeypatch.delenv("RUN_SQLSERVER_INTEGRATION_TESTS", raising=False)
    cfg2 = DatabaseConfig.from_env()
    assert cfg2.integration_opt_in is None


# ==============================================================================
# 5. Wrong Server Rejection
# ==============================================================================

@pytest.mark.parametrize("bad_server", [
    r".\SQLEXPRESS",
    r"DESKTOP-JL1BGSI\SQLEXPRESS01",
    r"OTHER_MACHINE\SQLEXPRESS",
    r"127.0.0.1",
    r"localhost",
    r"10.0.0.1\SQLEXPRESS",
])
def test_guard_rejects_unapproved_servers(
    mock_isolated_db_environment, monkeypatch, bad_server
):
    """Server alias must strictly normalize to localhost\\SQLEXPRESS."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=bad_server,
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        err = str(exc_info.value).lower()
        assert (
            "approved windows-authenticated target" in err
            or "disallowed integration server" in err
            or "unapproved integration server" in err
        )


# ==============================================================================
# 6. Production Database Rejection
# ==============================================================================

@pytest.mark.parametrize("protected_db", [
    "CeylonGoDB", "ceylongodb", "CEYLONGODB",
    "master", "tempdb", "model", "msdb",
])
def test_guard_strictly_rejects_production_and_system_databases(
    mock_isolated_db_environment, monkeypatch, protected_db
):
    """Targeting CeylonGoDB or SQL Server system databases must fail closed."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database=protected_db,
            integration_opt_in="1",
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        err = str(exc_info.value).lower()
        assert (
            "live database safety violation" in err
            or "unapproved integration database" in err
        )


# ==============================================================================
# 7. SQL Authentication Rejection
# ==============================================================================

def test_guard_rejects_sql_authentication_credentials(
    mock_isolated_db_environment, monkeypatch
):
    """Specifying user and password for isolated integration target must fail."""
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    with patch("scripts.migrate.get_connection", return_value=mock_conn):
        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            user="sa",
            password="StrongPassword123!",
            integration_opt_in="1",
        )
        with pytest.raises(DatabaseSafetyError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        assert "approved windows-authenticated target" in str(exc_info.value).lower()


# ==============================================================================
# 8. Historical Ledger Continuity Verification
# ==============================================================================

def test_guard_requires_verified_historical_ledger_through_038(
    tmp_path, monkeypatch
):
    """Upgrading to 039-042 requires verified records 001-038 in the ledger."""
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    mock_conn = MagicMock(spec=["cursor", "commit", "rollback", "close"])
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    machine, instance = ARTISAN_INTEGRATION_IDENTITY.split("\\")
    identity_row = (
        "CeylonGoDB_IntegrationTest",
        ARTISAN_INTEGRATION_IDENTITY,
        machine,
        instance,
    )
    mock_cursor.fetchone.return_value = identity_row

    # Ledger only has up to 036 (missing 037 and 038)
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)
    incomplete_applied = []
    for i in range(1, 43):
        content = f"-- Migration {i:03d}\nSELECT 1;\nGO\n"
        fname = f"{i:03d}_test_step.sql"
        (migrations_dir / fname).write_text(content, encoding="utf-8")
        chk = calculate_checksum(content)
        if i <= 36:
            incomplete_applied.append(
                (f"{i:03d}", fname, chk, None, 10, "executed")
            )

    def mock_execute(sql, *args, **kwargs):
        sql_str = str(sql).strip().upper()
        if "DB_NAME()" in sql_str:
            mock_cursor.fetchone.return_value = identity_row
        elif "SP_GETAPPLOCK" in sql_str:
            mock_cursor.fetchone.return_value = (0,)
        elif (
            "SELECT VERSION, FILENAME" in sql_str
            or "FROM DBO.SCHEMA_MIGRATIONS" in sql_str
        ):
            mock_cursor.fetchall.return_value = incomplete_applied
        return mock_cursor

    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchall.return_value = incomplete_applied

    with patch("scripts.migrate.get_connection", return_value=mock_conn), \
         patch("scripts.migrate.migration_lock") as mock_lock:
        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)

        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        with pytest.raises(MigrationError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        assert "requires a verified ledger through 038" in str(exc_info.value)


# ==============================================================================
# 9. Untracked Pending Table Collision Detection for All 8 Destination Tables
# ==============================================================================

@pytest.mark.parametrize("destination_table", ALL_EIGHT_DESTINATION_TABLES)
def test_guard_detects_untracked_table_collision_for_destination_table(
    mock_isolated_db_environment, monkeypatch, capsys, destination_table
):
    """
    Verifies that if ANY of the eight actual destination tables already exists
    in the database, cmd_upgrade detects the conflict and aborts.
    """
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    mock_cursor = mock_conn.cursor.return_value
    orig_execute = mock_cursor.execute.side_effect

    def table_conflict_execute(sql, *args, **kwargs):
        sql_str = str(sql).strip().upper()
        if "OBJECT_ID" in sql_str:
            params = args[0] if args else kwargs.get("params", ())
            if params and destination_table in params:
                mock_cursor.fetchone.return_value = (9999,)
                return mock_cursor
            mock_cursor.fetchone.return_value = None
            return mock_cursor
        return orig_execute(sql, *args, **kwargs)

    mock_cursor.execute.side_effect = table_conflict_execute

    with patch("scripts.migrate.get_connection", return_value=mock_conn), \
         patch("scripts.migrate.migration_lock") as mock_lock:
        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)

        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        with pytest.raises(MigrationError) as exc_info:
            cmd_upgrade(
                cfg,
                migrations_dir,
                target_version="042",
                confirmed=True,
            )
        err = str(exc_info.value)
        assert "Existing untracked tables detected" in err
        captured = capsys.readouterr().out
        assert destination_table in captured


# ==============================================================================
# 10. Verification That Legacy Table Names Are NOT Used as Substitutes
# ==============================================================================

@pytest.mark.parametrize("legacy_table", LEGACY_WRONG_TABLE_NAMES)
def test_legacy_wrong_tables_do_not_trigger_destination_collision(
    mock_isolated_db_environment, monkeypatch, legacy_table
):
    """
    Verifies that legacy wrong table names (e.g. dbo.eco_accommodations)
    are NOT registered in pending_tables_map for 041/042, and therefore their
    presence does NOT act as a false conflict for 041/042 destination tables.
    """
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    mock_cursor = mock_conn.cursor.return_value
    orig_execute = mock_cursor.execute.side_effect

    checked_tables = []

    def recording_execute(sql, *args, **kwargs):
        sql_str = str(sql).strip().upper()
        if "OBJECT_ID" in sql_str:
            params = args[0] if args else kwargs.get("params", ())
            if params:
                checked_tables.extend(params)
            # Legacy table returns existence, but real destination tables do not
            if params and legacy_table in params:
                mock_cursor.fetchone.return_value = (5555,)
                return mock_cursor
            mock_cursor.fetchone.return_value = None
            return mock_cursor
        return orig_execute(sql, *args, **kwargs)

    mock_cursor.execute.side_effect = recording_execute

    with patch("scripts.migrate.get_connection", return_value=mock_conn), \
         patch("scripts.migrate.migration_lock") as mock_lock:
        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)

        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        exit_code = cmd_upgrade(
            cfg,
            migrations_dir,
            target_version="042",
            confirmed=True,
        )
        # Upgrade succeeds because legacy table is not a pending destination
        assert exit_code == 0
        # The legacy table was never queried as a collision target
        assert legacy_table not in checked_tables


def test_runner_checks_exact_eight_destination_tables_and_no_legacy(
    mock_isolated_db_environment, monkeypatch
):
    """
    Verifies that cmd_upgrade queries OBJECT_ID for exactly all eight destination
    tables during the 041-042 upgrade check, and queries zero legacy wrong names.
    """
    mock_conn, migrations_dir = mock_isolated_db_environment
    monkeypatch.setenv("RUN_SQLSERVER_INTEGRATION_TESTS", "1")

    mock_cursor = mock_conn.cursor.return_value
    orig_execute = mock_cursor.execute.side_effect

    queried_tables = []

    def recording_execute(sql, *args, **kwargs):
        sql_str = str(sql).strip().upper()
        if "OBJECT_ID" in sql_str:
            params = args[0] if args else kwargs.get("params", ())
            if params:
                queried_tables.extend(params)
            mock_cursor.fetchone.return_value = None
            return mock_cursor
        return orig_execute(sql, *args, **kwargs)

    mock_cursor.execute.side_effect = recording_execute

    with patch("scripts.migrate.get_connection", return_value=mock_conn), \
         patch("scripts.migrate.migration_lock") as mock_lock:
        mock_lock.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_lock.return_value.__exit__ = MagicMock(return_value=None)

        cfg = DatabaseConfig(
            server=r"localhost\SQLEXPRESS",
            database="CeylonGoDB_IntegrationTest",
            integration_opt_in="1",
        )
        exit_code = cmd_upgrade(
            cfg,
            migrations_dir,
            target_version="042",
            confirmed=True,
        )
        assert exit_code == 0

    # All eight actual destination tables must have been checked
    for tbl in ALL_EIGHT_DESTINATION_TABLES:
        assert tbl in queried_tables, f"Expected {tbl} to be checked in OBJECT_ID"

    # None of the legacy wrong names should have been queried
    for legacy in LEGACY_WRONG_TABLE_NAMES:
        assert legacy not in queried_tables, f"Legacy name {legacy} must not be checked"
