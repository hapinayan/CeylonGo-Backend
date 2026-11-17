"""
Unit & Integration Tests for CeylonGo SQL Server Migration Runner
================================================================
Tests coverage:
- T-SQL GO batch parser (string literals, comments, count, case-insensitivity)
- Checksum calculation with LF/CRLF normalization invariance
- Migration file discovery and sequential validation (duplicates, gaps)
- Target database validation guards
- Baseline verification logic with schema mock
- Transaction rollback on simulated batch execution failure
- Checksum tampering detection
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from scripts.migrate import (
    AppliedRecord,
    BaselineVerificationError,
    BatchExecutionError,
    ChecksumMismatchError,
    DatabaseConfig,
    MigrationError,
    MigrationFile,
    SequenceIntegrityError,
    TargetDatabaseMismatchError,
    calculate_checksum,
    cmd_baseline,
    cmd_plan,
    cmd_upgrade,
    cmd_validate,
    discover_migrations,
    split_sql_batches,
    verify_baseline_schema,
    verify_target_database,
)


# =============================================================================
# 1. T-SQL GO Batch Separator Parser Tests
# =============================================================================

class TestGOBatchParser:
    def test_simple_batches(self):
        sql = "SELECT 1;\nGO\nSELECT 2;\nGO"
        batches = split_sql_batches(sql)
        assert len(batches) == 2
        assert batches[0] == "SELECT 1;"
        assert batches[1] == "SELECT 2;"

    def test_go_inside_string_literal(self):
        sql = """
        SELECT 'CeylonGo' AS brand;
        INSERT INTO locations (city) VALUES ('Galle');
        PRINT 'This is GO time!';
        GO
        SELECT 'Another batch';
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 2
        assert "CeylonGo" in batches[0]
        assert "Galle" in batches[0]
        assert "This is GO time!" in batches[0]
        assert batches[1] == "SELECT 'Another batch';"

    def test_go_inside_escaped_quote_string(self):
        sql = """
        SELECT 'O''Reilly and CeylonGo';
        SELECT 'Batch ''GO'' string';
        GO
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 1
        assert "O''Reilly and CeylonGo" in batches[0]

    def test_go_inside_single_line_comment(self):
        sql = """
        -- GO should not trigger a batch split here
        SELECT 1 AS num; -- inline GO comment
        GO
        SELECT 2 AS num;
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 2
        assert "SELECT 1 AS num;" in batches[0]
        assert "SELECT 2 AS num;" in batches[1]

    def test_go_inside_multi_line_comment(self):
        sql = """
        /*
        GO
        This is inside a multi-line comment.
        GO
        */
        SELECT 'Real statement';
        GO
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 1
        assert "Real statement" in batches[0]

    def test_nested_block_comments(self):
        sql = """
        /* Outer comment
           /* Inner comment with GO */
           Still in outer comment
        */
        SELECT 42;
        GO
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 1
        assert "SELECT 42;" in batches[0]

    def test_case_insensitivity_and_whitespace(self):
        sql = """
        SELECT 'a';
           go   
        SELECT 'b';
        \tGo\t
        SELECT 'c';
        gO -- comment after GO
        SELECT 'd';
        GO
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 4
        assert "SELECT 'a';" in batches[0]
        assert "SELECT 'b';" in batches[1]
        assert "SELECT 'c';" in batches[2]
        assert "SELECT 'd';" in batches[3]

    def test_go_count_execution(self):
        sql = """
        INSERT INTO counters (val) VALUES (1);
        GO 3
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 3
        assert all(b == "INSERT INTO counters (val) VALUES (1);" for b in batches)

    def test_script_without_go(self):
        sql = """
        CREATE TABLE dbo.test (id INT PRIMARY KEY);
        CREATE INDEX IX_test_id ON dbo.test(id);
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 1
        assert "CREATE TABLE dbo.test" in batches[0]
        assert "CREATE INDEX IX_test_id" in batches[0]

    def test_empty_batches_ignored(self):
        sql = """
        GO
        GO
        SELECT 1;
        GO
        GO
        """
        batches = split_sql_batches(sql)
        assert len(batches) == 1
        assert batches[0] == "SELECT 1;"


# =============================================================================
# 2. Checksum Calculation & Line-Ending Invariance Tests
# =============================================================================

class TestChecksumCalculation:
    def test_crlf_lf_invariance(self):
        lf_content = "CREATE TABLE users (\n    id INT PRIMARY KEY,\n    name NVARCHAR(100)\n);\n"
        crlf_content = "CREATE TABLE users (\r\n    id INT PRIMARY KEY,\r\n    name NVARCHAR(100)\r\n);\r\n"
        cr_content = "CREATE TABLE users (\r    id INT PRIMARY KEY,\r    name NVARCHAR(100)\r);\r"

        hash_lf = calculate_checksum(lf_content)
        hash_crlf = calculate_checksum(crlf_content)
        hash_cr = calculate_checksum(cr_content)

        assert hash_lf == hash_crlf
        assert hash_lf == hash_cr
        assert len(hash_lf) == 64  # SHA-256 length

    def test_file_checksum(self, tmp_path):
        f = tmp_path / "test_migration.sql"
        f.write_bytes(b"SELECT 1;\r\nGO\r\n")
        file_hash = calculate_checksum(f)
        str_hash = calculate_checksum("SELECT 1;\nGO\n")
        assert file_hash == str_hash


# =============================================================================
# 3. Migration Discovery & Sequence Integrity Tests
# =============================================================================

class TestMigrationDiscovery:
    def test_discover_valid_sequence(self, tmp_path):
        (tmp_path / "001_create_table_a.sql").write_text("SELECT 1;\nGO", encoding="utf-8")
        (tmp_path / "002_create_table_b.sql").write_text("SELECT 2;\nGO", encoding="utf-8")
        (tmp_path / "003_create_table_c.sql").write_text("SELECT 3;\nGO", encoding="utf-8")

        discovered = discover_migrations(tmp_path)
        assert len(discovered) == 3
        assert [m.version for m in discovered] == ["001", "002", "003"]
        assert discovered[0].batches == ["SELECT 1;"]

    def test_duplicate_version_detection(self, tmp_path):
        (tmp_path / "001_create_table_a.sql").write_text("SELECT 1;\nGO", encoding="utf-8")
        (tmp_path / "001_duplicate.sql").write_text("SELECT 2;\nGO", encoding="utf-8")

        with pytest.raises(SequenceIntegrityError, match="Duplicate migration version '001'"):
            discover_migrations(tmp_path)

    def test_sequence_gap_detection(self, tmp_path):
        (tmp_path / "001_step_one.sql").write_text("SELECT 1;\nGO", encoding="utf-8")
        (tmp_path / "003_step_three.sql").write_text("SELECT 3;\nGO", encoding="utf-8")

        with pytest.raises(SequenceIntegrityError, match="sequence gap detected"):
            discover_migrations(tmp_path)

    def test_non_migration_files_ignored(self, tmp_path):
        (tmp_path / "001_step_one.sql").write_text("SELECT 1;\nGO", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Documentation", encoding="utf-8")
        (tmp_path / "schema_backup.bak").write_bytes(b"fake backup")

        discovered = discover_migrations(tmp_path)
        assert len(discovered) == 1
        assert discovered[0].version == "001"


# =============================================================================
# 4. Target Database Validation Tests
# =============================================================================

class TestTargetDatabaseValidation:
    def test_verify_target_database_match(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("CeylonGoDB", "DEV-SERVER\\SQLEXPRESS")
        mock_conn.cursor.return_value = mock_cursor

        db_name = verify_target_database(mock_conn, "CeylonGoDB")
        assert db_name == "CeylonGoDB"

    def test_verify_target_database_case_insensitive_match(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("ceylongodb", "DEV-SERVER\\SQLEXPRESS")
        mock_conn.cursor.return_value = mock_cursor

        db_name = verify_target_database(mock_conn, "CeylonGoDB")
        assert db_name.lower() == "ceylongodb"

    def test_verify_target_database_mismatch_raises(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("master", "PRODUCTION-CLUSTER")
        mock_conn.cursor.return_value = mock_cursor

        with pytest.raises(TargetDatabaseMismatchError, match="connected to 'master'"):
            verify_target_database(mock_conn, "CeylonGoDB")


# =============================================================================
# 5. Baseline Schema Verification Tests
# =============================================================================

class TestBaselineSchemaVerification:
    def test_baseline_verification_all_pass(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Mock responses for all checks in verify_baseline_schema
        # 1. password_resets exists (101)
        # 2. token_hash exists (64)
        # 3. users exists (102)
        # 4. google_sub exists (255)
        # 5. provider_profiles exists (103)
        # 6. provider_type exists (30)
        # 7. UQ_provider_profiles_user_id exists (row)
        # 8. provider_email_otps exists (104)
        # 9. provider_registration_authorizations exists (105)
        # 10. nic_number exists (20)
        # 11. provider_registration_drafts exists (106)
        # 12. gender exists (10)
        # 13. date_of_birth exists (10)
        mock_cursor.fetchone.side_effect = [
            (101,), (64,),      # 001
            (102,), (255,),     # 002
            (103,), (30,), (1,),# 003
            (104,), (105,), (20,), # 004
            (106,),             # 005
            (10,), (10,),       # 006
        ]
        mock_conn.cursor.return_value = mock_cursor

        is_valid, missing = verify_baseline_schema(mock_conn)
        assert is_valid is True
        assert len(missing) == 0

    def test_baseline_verification_missing_column(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()

        # Simulate missing date_of_birth (006)
        mock_cursor.fetchone.side_effect = [
            (101,), (64,),
            (102,), (255,),
            (103,), (30,), (1,),
            (104,), (105,), (20,),
            (106,),
            (10,), None,  # date_of_birth is None
        ]
        mock_conn.cursor.return_value = mock_cursor

        is_valid, missing = verify_baseline_schema(mock_conn)
        assert is_valid is False
        assert any("date_of_birth" in item for item in missing)


# =============================================================================
# 6. Runner Execution & Safety Tests
# =============================================================================

class TestRunnerSafetyAndTransactions:
    @patch("scripts.migrate.get_connection")
    def test_plan_mode_dry_run(self, mock_get_conn, tmp_path):
        (tmp_path / "001_init.sql").write_text("CREATE TABLE x (id INT);\nGO", encoding="utf-8")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            ("CeylonGoDB", "SERVER"),  # verify_target_database
            (0,),                     # schema_migrations table exists check -> 0
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        cfg = DatabaseConfig(server=".\\SQLEXPRESS", database="CeylonGoDB")
        exit_code = cmd_plan(cfg, tmp_path)
        assert exit_code == 0
        # Dry-run should not call commit
        mock_conn.commit.assert_not_called()

    @patch("scripts.migrate.get_connection")
    def test_baseline_requires_confirmation(self, mock_get_conn, tmp_path):
        cfg = DatabaseConfig(server=".\\SQLEXPRESS", database="CeylonGoDB")
        exit_code = cmd_baseline(cfg, tmp_path, confirmed=False)
        assert exit_code == 1
        mock_get_conn.assert_not_called()

    @patch("scripts.migrate.get_connection")
    def test_upgrade_requires_confirmation(self, mock_get_conn, tmp_path):
        cfg = DatabaseConfig(server=".\\SQLEXPRESS", database="CeylonGoDB")
        exit_code = cmd_upgrade(cfg, tmp_path, confirmed=False)
        assert exit_code == 1
        mock_get_conn.assert_not_called()

    @patch("scripts.migrate.get_connection")
    def test_upgrade_rollback_on_batch_error(self, mock_get_conn, tmp_path):
        (tmp_path / "001_multi_batch.sql").write_text("SELECT 1;\nGO\nSELECT 1/0;\nGO", encoding="utf-8")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            ("CeylonGoDB", "SERVER"),  # verify_target_database
            (0,),                     # migration lock sp_getapplock -> 0
            (1,),                     # schema_migrations exists -> 1
        ]
        mock_cursor.fetchall.return_value = []  # no applied migrations

        # Simulate SQL failure on second batch execution
        mock_cursor.execute.side_effect = [
            None,                     # ensure_schema_migrations_table
            None,                     # sp_getapplock
            None,                     # get_applied_migrations table exists
            None,                     # get_applied_migrations query
            None,                     # batch 1 succeeds
            RuntimeError("Divide by zero error encountered."),  # batch 2 fails
        ]

        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        cfg = DatabaseConfig(server=".\\SQLEXPRESS", database="CeylonGoDB")
        with pytest.raises(BatchExecutionError, match="Divide by zero"):
            cmd_upgrade(cfg, tmp_path, confirmed=True)

        # Rollback must have been executed, and commit was only called for ensure_schema_migrations_table
        mock_conn.rollback.assert_called_once()
        assert mock_conn.commit.call_count == 1

    @patch("scripts.migrate.get_connection")
    def test_validate_detects_checksum_mismatch(self, mock_get_conn, tmp_path):
        f = tmp_path / "001_init.sql"
        f.write_text("CREATE TABLE x (id INT);\nGO", encoding="utf-8")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            ("CeylonGoDB", "SERVER"),
            (1,),  # schema_migrations exists
        ]
        # Applied migration has different checksum
        mock_cursor.fetchall.return_value = [
            ("001", "001_init.sql", "tampered_checksum_value_1234567890", None, 10, "executed")
        ]
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        cfg = DatabaseConfig(server=".\\SQLEXPRESS", database="CeylonGoDB")
        with pytest.raises(ChecksumMismatchError):
            cmd_validate(cfg, tmp_path, check_db=True)
