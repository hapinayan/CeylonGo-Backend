"""
Automated Unit Tests for SQL Server Disposable Test Safety Protections.
========================================================================
Verifies fail-closed database identity guard, connection URL parsing,
ODBC parameter resolution, credential masking, and runtime DB_NAME() checks.

CRITICAL: Uses mocks and SQLite in-memory engines.
NEVER connects to or mutates real Microsoft SQL Server instances.
"""

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine, text

from app.core.database_guard import (
    APPROVED_INTEGRATION_DATABASES,
    PROTECTED_DATABASES,
    DatabaseSafetyError,
    attach_runtime_database_guard,
    create_safe_sqlserver_test_engine,
    extract_database_name_from_url,
    sanitize_url_for_logging,
    validate_disposable_sqlserver_url,
    verify_safe_integration_database,
)


# ==============================================================================
# Task 5 — 18 Comprehensive Safety Test Cases
# ==============================================================================

def test_safety_01_missing_test_url():
    """1. Missing TEST_DISPOSABLE_SQLSERVER_URL (None) fails closed."""
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(None)
    assert "target database connection url is unspecified or empty" in str(exc_info.value).lower()


def test_safety_02_empty_test_url():
    """2. Empty or whitespace test URL fails closed."""
    for empty_val in ["", "   ", "\t\n  "]:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(empty_val)
        assert "target database connection url is unspecified or empty" in str(exc_info.value).lower()


def test_safety_03_valid_allowlisted_databases():
    """3. Valid allowlisted database names pass URL validation."""
    urls = [
        "mssql+pyodbc://user:pass@localhost/CeylonGoDB_IntegrationTest?driver=ODBC+Driver+18+for+SQL+Server",
        "mssql+pyodbc://user:pass@localhost/CeylonGoDB_Disposable_QA?driver=ODBC+Driver+18+for+SQL+Server",
    ]
    assert validate_disposable_sqlserver_url(urls[0]) == "CeylonGoDB_IntegrationTest"
    assert validate_disposable_sqlserver_url(urls[1]) == "CeylonGoDB_Disposable_QA"


def test_safety_04_case_insensitive_allowlist_matching():
    """4. Allowlist matching is strictly case-insensitive."""
    casing_variants = [
        ("mssql+pyodbc://u:p@host/ceylongodb_integrationtest?driver=ODBC", "ceylongodb_integrationtest"),
        ("mssql+pyodbc://u:p@host/CEYLONGODB_INTEGRATIONTEST?driver=ODBC", "CEYLONGODB_INTEGRATIONTEST"),
        ("mssql+pyodbc://u:p@host/ceylongodb_disposable_qa?driver=ODBC", "ceylongodb_disposable_qa"),
        ("mssql+pyodbc://u:p@host/CEYLONGODB_DISPOSABLE_QA?driver=ODBC", "CEYLONGODB_DISPOSABLE_QA"),
    ]
    for url, expected_extracted in casing_variants:
        res = validate_disposable_sqlserver_url(url)
        assert res.lower() == expected_extracted.lower()


def test_safety_05_protected_ceylongodb_rejection():
    """5. Protected production/development database CeylonGoDB is strictly rejected."""
    live_variants = [
        "mssql+pyodbc://user:pass@localhost/CeylonGoDB?driver=ODBC",
        "mssql+pyodbc://user:pass@localhost/ceylongodb?driver=ODBC",
        "mssql+pyodbc://user:pass@localhost/CEYLONGODB?driver=ODBC",
        "mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%3BSERVER%3Dlocalhost%3BDATABASE%3DCeylonGoDB%3B",
    ]
    for url in live_variants:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "live database safety violation" in str(exc_info.value).lower()
        assert "ceylongodb" in str(exc_info.value).lower()


def test_safety_06_sqlserver_system_database_rejection():
    """6. SQL Server system databases (master, tempdb, model, msdb) are strictly rejected."""
    for sys_db in ["master", "tempdb", "model", "msdb", "MASTER", "TempDB"]:
        url = f"mssql+pyodbc://user:pass@localhost/{sys_db}?driver=ODBC"
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "system database safety violation" in str(exc_info.value).lower() or "unapproved" in str(exc_info.value).lower()


def test_safety_07_unknown_database_rejection():
    """7. Unknown/unapproved databases outside the allowlist are rejected."""
    unapproved = ["ProductionDB", "TestDB", "CeylonGoDB_Dev", "CeylonGoDB_Rehearsal", "random_db"]
    for db in unapproved:
        url = f"mssql+pyodbc://user:pass@localhost/{db}?driver=ODBC"
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "unapproved integration database" in str(exc_info.value).lower()


def test_safety_08_missing_database_component():
    """8. URLs with missing database segment fail closed."""
    no_db_urls = [
        "mssql+pyodbc://user:pass@localhost/?driver=ODBC",
        "mssql+pyodbc://user:pass@localhost?driver=ODBC",
        "mssql+pyodbc://localhost",
        "mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%3BSERVER%3Dlocalhost%3B",
    ]
    for url in no_db_urls:
        with pytest.raises(DatabaseSafetyError) as exc_info:
            validate_disposable_sqlserver_url(url)
        assert "target database name is unspecified or missing" in str(exc_info.value).lower()


def test_safety_09_malformed_sqlalchemy_url():
    """9. Malformed connection strings fail closed with sanitized error."""
    malformed = "not_a_valid_url ::: /\\/\\"
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(malformed)
    assert "malformed database connection url" in str(exc_info.value).lower()


def test_safety_10_odbc_connection_string_with_database_parameter():
    """10. Correctly extracts database declared in ODBC odbc_connect parameter."""
    odbc_url = "mssql+pyodbc:///?odbc_connect=DRIVER%3D%7BODBC+Driver+18+for+SQL+Server%7D%3BSERVER%3Dlocalhost%3BDATABASE%3DCeylonGoDB_IntegrationTest%3BTrusted_Connection%3Dyes%3B"
    extracted = validate_disposable_sqlserver_url(odbc_url)
    assert extracted == "CeylonGoDB_IntegrationTest"

    # Also test alternative DB= parameter
    odbc_url_short = "mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%3BSERVER%3Dlocalhost%3BDB%3DCeylonGoDB_Disposable_QA%3B"
    extracted_short = validate_disposable_sqlserver_url(odbc_url_short)
    assert extracted_short == "CeylonGoDB_Disposable_QA"


def test_safety_11_odbc_conflicting_database_parameters():
    """11. Rejects ODBC string with conflicting internal DATABASE parameters."""
    conflicting_odbc = "mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%3BDATABASE%3DCeylonGoDB_IntegrationTest%3BDATABASE%3DCeylonGoDB%3B"
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(conflicting_odbc)
    assert "conflicting database declarations within odbc" in str(exc_info.value).lower()


def test_safety_12_conflicting_database_declarations():
    """12. Rejects conflicting database declarations between URL path and ODBC query."""
    conflicting_url = "mssql+pyodbc://localhost/CeylonGoDB_IntegrationTest?odbc_connect=DRIVER%3DODBC%3BDATABASE%3DCeylonGoDB_Disposable_QA%3B"
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(conflicting_url)
    assert "ambiguous database selection" in str(exc_info.value).lower()


def test_safety_13_no_fallback_to_database_url(monkeypatch):
    """13. Ensures test URL validation never falls back to DATABASE_URL or DB_NAME."""
    monkeypatch.setenv("DATABASE_URL", "mssql+pyodbc://localhost/CeylonGoDB")
    monkeypatch.setenv("DB_NAME", "CeylonGoDB")

    # None must fail closed, NOT fall back to CeylonGoDB
    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(None)
    assert "unspecified or empty" in str(exc_info.value).lower()


def test_safety_14_runtime_dbname_mismatch():
    """14. Runtime checkout hook detects and rejects DB_NAME mismatch against expected."""
    engine = create_engine("sqlite:///:memory:")
    # Simulate DB returning CeylonGoDB_Disposable_QA when expected was CeylonGoDB_IntegrationTest
    attach_runtime_database_guard(
        engine,
        expected_db="CeylonGoDB_IntegrationTest",
        query_override="SELECT 'CeylonGoDB_Disposable_QA';",
    )
    with pytest.raises(DatabaseSafetyError) as exc_info:
        with engine.connect():
            pass
    assert "runtime database mismatch" in str(exc_info.value).lower()


def test_safety_15_runtime_protected_database_rejection():
    """15. Runtime checkout hook detects and rejects protected database (CeylonGoDB)."""
    engine = create_engine("sqlite:///:memory:")
    # Simulate DB returning CeylonGoDB at runtime
    attach_runtime_database_guard(
        engine,
        query_override="SELECT 'CeylonGoDB';",
    )
    with pytest.raises(DatabaseSafetyError) as exc_info:
        with engine.connect():
            pass
    assert "live database safety violation" in str(exc_info.value).lower()


def test_safety_16_engine_session_cleanup_on_guard_failure():
    """16. Verifies underlying connection is closed on runtime guard failure."""
    mock_dbapi_conn = MagicMock()
    mock_cursor = MagicMock()

    current_query = []

    def mock_execute(stmt, *args, **kwargs):
        current_query.append(str(stmt))

    def mock_fetchone():
        if current_query and "PRAGMA" in current_query[-1]:
            return (0,)
        return ("CeylonGoDB",)

    mock_cursor.execute.side_effect = mock_execute
    mock_cursor.fetchone.side_effect = mock_fetchone
    mock_dbapi_conn.cursor.return_value = mock_cursor

    engine = create_engine("sqlite://", creator=lambda: mock_dbapi_conn)
    attach_runtime_database_guard(engine)

    with pytest.raises(DatabaseSafetyError):
        with engine.connect():
            pass

    # Verify that dbapi_connection.close() was explicitly called upon guard rejection
    mock_dbapi_conn.close.assert_called()


def test_safety_17_no_database_connection_for_rejected_urls():
    """17. Rejection happens before engine creation; create_engine is never called for invalid URL."""
    with patch("sqlalchemy.create_engine") as mock_create:
        with pytest.raises(DatabaseSafetyError):
            create_safe_sqlserver_test_engine("mssql+pyodbc://user:pass@localhost/CeylonGoDB?driver=ODBC")
        mock_create.assert_not_called()


def test_safety_18_no_credential_leakage_in_errors_or_logs():
    """18. Passwords and credentials in URLs or ODBC strings are never leaked in error messages."""
    secret_pass = "SuperSecret_P@ssw0rd!_12345"
    url_with_pass = f"mssql+pyodbc://admin:{secret_pass}@localhost/CeylonGoDB?driver=ODBC"

    with pytest.raises(DatabaseSafetyError) as exc_info:
        validate_disposable_sqlserver_url(url_with_pass)
    error_msg = str(exc_info.value)
    error_repr = repr(exc_info.value)

    assert secret_pass not in error_msg
    assert secret_pass not in error_repr

    # Test ODBC PWD= masking
    odbc_secret = "VeryHiddenPwd_999!"
    odbc_url = f"mssql+pyodbc:///?odbc_connect=DRIVER%3DODBC%3BUID%3Dtest%3BPWD%3D{odbc_secret}%3BDATABASE%3DForbiddenDB%3B"
    with pytest.raises(DatabaseSafetyError) as exc_info_odbc:
        validate_disposable_sqlserver_url(odbc_url)
    assert odbc_secret not in str(exc_info_odbc.value)
