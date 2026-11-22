"""
CeylonGo - Staging Rehearsal Safety & Security Test Suite
==========================================================
Comprehensive mock-based tests validating fail-closed safety guards for:
1. Development database rejection (CeylonGoDB_Development).
2. Production database rejection (CeylonGoDB).
3. System database rejection (master, tempdb, model, msdb).
4. Empty / None database rejection.
5. Staging database acceptance (CeylonGoDB_Migration_Staging).
6. Incorrect SQL Server instance rejection.
7. Restore destination collision detection (sys.databases & disk files).
8. Missing backup evidence & offline failure mode.
9. Migration target boundary enforcement (030-040 strictly, 041+ rejected, <030 rejected for execution).
10. Missing / empty target rejection on upgrade (unbounded upgrade forbidden).
11. Malformed target rejection.
12. Migration runner argument injection prevention.
13. No accidental fallback to development database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.staging_guard import (
    ALLOWED_STAGING_DATABASES,
    FORBIDDEN_DATABASES,
    StagingDatabaseSafetyError,
    assert_staging_database_does_not_exist,
    verify_connected_staging_identity,
    verify_safe_staging_database_name,
)
from scripts.staging_restore_preparation import (
    generate_safe_restore_sql,
    validate_offline_restore_contract,
)
from scripts.staging_rehearsal_runner import (
    IMMUTABLE_STAGING_DB,
    main as runner_main,
    validate_migration_target,
)


class TestStagingDatabaseGuard:
    """Tests for scripts/staging_guard.py."""

    def test_development_database_rejection(self):
        """1. Rejection of development database."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_safe_staging_database_name("CeylonGoDB_Development")
        assert "PROTECTED DATABASE SAFETY VIOLATION" in str(exc_info.value)
        assert "CeylonGoDB_Development" in str(exc_info.value)

    def test_production_database_rejection(self):
        """2. Rejection of production database."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_safe_staging_database_name("CeylonGoDB")
        assert "PROTECTED DATABASE SAFETY VIOLATION" in str(exc_info.value)

    @pytest.mark.parametrize("sys_db", ["master", "tempdb", "model", "msdb", "ceylongodb_logintest"])
    def test_system_database_rejection(self, sys_db: str):
        """3. Rejection of system and login test databases."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_safe_staging_database_name(sys_db)
        assert "PROTECTED DATABASE SAFETY VIOLATION" in str(exc_info.value)

    @pytest.mark.parametrize("empty_val", ["", "   ", None])
    def test_empty_database_rejection(self, empty_val):
        """4. Rejection of empty, whitespace, or None database."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_safe_staging_database_name(empty_val)
        assert "unspecified or empty" in str(exc_info.value)

    def test_staging_database_acceptance(self):
        """5. Acceptance of approved staging database."""
        canonical = verify_safe_staging_database_name("CeylonGoDB_Migration_Staging")
        assert canonical == "ceylongodb_migration_staging"

        # Case-insensitive acceptance
        assert verify_safe_staging_database_name("ceylongodb_migration_staging") == "ceylongodb_migration_staging"
        assert verify_safe_staging_database_name("CEYLONGODB_MIGRATION_STAGING") == "ceylongodb_migration_staging"

    def test_incorrect_sqlserver_instance_rejection(self):
        """6. Rejection of mismatched SQL Server instance."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            "ceylongodb_migration_staging",
            "PROD-SERVER\\MSSQLSERVER",
            "PROD-SERVER",
            "MSSQLSERVER",
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_connected_staging_identity(
                mock_conn,
                expected_instance="SQLEXPRESS",
            )
        assert "SQL SERVER INSTANCE MISMATCH" in str(exc_info.value)

    def test_connected_identity_development_db_rejection(self):
        """Live connection pointing to development DB is immediately aborted."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            "CeylonGoDB_Development",
            "DESKTOP-CRQSRGE\\SQLEXPRESS",
            "DESKTOP-CRQSRGE",
            "SQLEXPRESS",
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            verify_connected_staging_identity(mock_conn)
        assert "LIVE IDENTITY SAFETY VIOLATION" in str(exc_info.value)


class TestStagingRestoreSafety:
    """Tests for scripts/staging_restore_preparation.py."""

    def test_restore_sql_never_contains_with_replace(self):
        """Generated restore SQL strictly omits WITH REPLACE."""
        sql = generate_safe_restore_sql()
        assert "WITH REPLACE" not in sql
        assert "REPLACE," not in sql
        assert "REPLACE\n" not in sql

    def test_restore_sql_uses_throw_instead_of_severity_20_raiserror(self):
        """Generated restore SQL uses THROW, not severity-20 RAISERROR."""
        sql = generate_safe_restore_sql()
        assert "RAISERROR" not in sql
        assert "THROW 50001" in sql
        assert "THROW 50002" in sql
        assert "THROW 50003" in sql
        assert "THROW 50004" in sql

    def test_restore_destination_collision_database_exists(self):
        """7a. Refusal when staging DB already exists in sys.databases."""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)  # DB already exists
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            assert_staging_database_does_not_exist(mock_conn, "CeylonGoDB_Migration_Staging")
        assert "DESTINATION DATABASE COLLISION" in str(exc_info.value)

    def test_restore_destination_collision_files_exist_locally(self, tmp_path):
        """7b. Refusal when destination MDF/LDF files already exist on disk."""
        fake_data_dir = tmp_path / "DATA"
        fake_data_dir.mkdir()
        fake_mdf = fake_data_dir / "CeylonGoDB_Migration_Staging.mdf"
        fake_mdf.write_text("existing data file")

        results = validate_offline_restore_contract(
            data_dir=str(fake_data_dir),
            target_db="CeylonGoDB_Migration_Staging",
        )
        assert results["target_mdf_exists_locally"] is True
        assert any("Physical file collision" in v for v in results["safety_violations"])

    def test_missing_backup_evidence_offline(self, tmp_path):
        """8. Offline validation correctly flags non-existent backup file."""
        non_existent_bak = tmp_path / "non_existent.bak"
        results = validate_offline_restore_contract(
            backup_path=str(non_existent_bak),
            data_dir=str(tmp_path),
            target_db="CeylonGoDB_Migration_Staging",
        )
        assert results["backup_file_exists_locally"] is False

    def test_offline_mode_does_not_connect_to_sql_server(self):
        """Offline script generation must not import or call pyodbc / get_connection."""
        with patch("scripts.migrate.get_connection") as mock_conn:
            sql = generate_safe_restore_sql()
            assert "RESTORE DATABASE [CeylonGoDB_Migration_Staging]" in sql
            mock_conn.assert_not_called()


class TestStagingRehearsalRunner:
    """Tests for scripts/staging_rehearsal_runner.py."""

    # Task 1: Accepted targets within rehearsal boundary (030-040)
    @pytest.mark.parametrize(
        ("valid_target", "is_execution"),
        [
            ("030", True),
            ("030", False),
            ("036", True),
            ("036", False),
            ("040", True),
            ("040", False),
        ],
    )
    def test_valid_migration_target_accepted(self, valid_target: str, is_execution: bool):
        """030, 036, and 040 accepted for both planning and execution."""
        assert validate_migration_target(valid_target, is_execution=is_execution) == valid_target

    def test_target_029_rejected_for_execution(self):
        """029 rejected for execution (below minimum rehearsal threshold 030)."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            validate_migration_target("029", is_execution=True)
        assert "below minimum rehearsal target" in str(exc_info.value)
        assert "030" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("disallowed_target", "is_execution"),
        [
            ("041", True),
            ("041", False),
            ("042", True),
            ("042", False),
            ("999", True),
            ("999", False),
        ],
    )
    def test_targets_041_and_higher_rejected(self, disallowed_target: str, is_execution: bool):
        """041, 042, 999 rejected across all commands (exceeds 040 ceiling)."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            validate_migration_target(disallowed_target, is_execution=is_execution)
        assert "exceeds maximum rehearsal ceiling of '040'" in str(exc_info.value)

    @pytest.mark.parametrize("missing_target", [None, "", "   "])
    def test_missing_target_rejected_for_execution(self, missing_target):
        """Missing or empty target rejected for execution (unbounded upgrade forbidden)."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            validate_migration_target(missing_target, is_execution=True)
        assert "MISSING MIGRATION TARGET" in str(exc_info.value)
        assert "Unbounded upgrades are strictly forbidden" in str(exc_info.value)

    def test_missing_target_allowed_for_planning(self):
        """Missing target is permitted for non-execution commands (e.g. status, plan)."""
        assert validate_migration_target(None, is_execution=False) is None

    @pytest.mark.parametrize(
        "malformed_target",
        [
            "40",
            "0040",
            "040; DROP TABLE users",
            "040 --database CeylonGoDB_Development",
            "abc",
            "04a",
            "040 ",
            " 040",
            "04-0",
        ],
    )
    def test_malformed_targets_rejected(self, malformed_target: str):
        """Malformed targets rejected across both execution and planning."""
        with pytest.raises(StagingDatabaseSafetyError) as exc_info:
            validate_migration_target(malformed_target, is_execution=True)
        assert "MALFORMED MIGRATION TARGET" in str(exc_info.value)

        with pytest.raises(StagingDatabaseSafetyError) as exc_info2:
            validate_migration_target(malformed_target, is_execution=False)
        assert "MALFORMED MIGRATION TARGET" in str(exc_info2.value)

    def test_migration_runner_argument_injection_database_flag_blocked(self):
        """Blocking of arbitrary --database override in CLI arguments."""
        exit_code = runner_main(["plan", "--database", "CeylonGoDB_Development"])
        assert exit_code == 1

        exit_code2 = runner_main(["upgrade", "--db=CeylonGoDB"])
        assert exit_code2 == 1

    @pytest.mark.parametrize("injection_arg", ["; rm -rf /", "& dir", "| whoami", "`calc`", "$VAR"])
    def test_migration_runner_shell_injection_characters_blocked(self, injection_arg: str):
        """Argument injection characters rejected before argparse parsing."""
        exit_code = runner_main(["plan", injection_arg])
        assert exit_code == 1

    def test_unbounded_upgrade_via_cli_main_blocked(self):
        """Calling upgrade without --target via runner CLI fails closed."""
        with patch("subprocess.run") as mock_subproc, \
             patch("scripts.staging_rehearsal_runner.verify_connected_staging_identity"):
            exit_code = runner_main(["upgrade", "--dry-run", "--no-db"])
            assert exit_code == 1
            mock_subproc.assert_not_called()

    def test_no_accidental_fallback_to_development_database(self):
        """Runner strictly sets --database CeylonGoDB_Migration_Staging and passes clean target."""
        with patch("subprocess.run") as mock_subproc, \
             patch("scripts.staging_rehearsal_runner.verify_connected_staging_identity"):
            mock_subproc.return_value = MagicMock(returncode=0)

            exit_code = runner_main(["upgrade", "--target", "040", "--dry-run", "--no-db"])
            assert exit_code == 0

            # Inspect command passed to subprocess
            call_args = mock_subproc.call_args[0][0]
            assert "--database" in call_args
            db_idx = call_args.index("--database")
            assert call_args[db_idx + 1] == IMMUTABLE_STAGING_DB
            assert "--target" in call_args
            target_idx = call_args.index("--target")
            assert call_args[target_idx + 1] == "040"
            assert "CeylonGoDB_Development" not in call_args
            assert "CeylonGoDB" not in call_args or IMMUTABLE_STAGING_DB in call_args
