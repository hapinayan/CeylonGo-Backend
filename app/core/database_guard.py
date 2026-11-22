"""
CeylonGo - Database Identity Guard
===================================
Enforces fail-closed safety checks preventing integration and rehearsal test suites
from performing write operations against the live production database (CeylonGoDB),
and restricts integration testing strictly to explicitly approved integration databases.
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError

APPROVED_INTEGRATION_DATABASES: frozenset[str] = frozenset({
    "ceylongodb_integrationtest",
})

APPROVED_INTEGRATION_SERVERS: frozenset[str] = frozenset({
    r"desktop-jl1bgsi\sqlexpress01",
    r".\sqlexpress01",
    r"localhost\sqlexpress01",
    r"127.0.0.1\sqlexpress01",
})

ARTISAN_INTEGRATION_ALIAS = r"localhost\SQLEXPRESS"
ARTISAN_INTEGRATION_IDENTITY = r"VISHNUGA-KAJARASH\SQLEXPRESS"
ARTISAN_INTEGRATION_DATABASE = "CeylonGoDB_IntegrationTest"
LEGACY_INTEGRATION_IDENTITY = r"DESKTOP-JL1BGSI\SQLEXPRESS01"

PROTECTED_DATABASES: frozenset[str] = frozenset({
    "ceylongodb",
    "ceylongodb_logintest",
    "master",
    "tempdb",
    "model",
    "msdb",
})

DISALLOWED_INTEGRATION_SERVERS: frozenset[str] = frozenset({
    r".\sqlexpress",
    r"desktop-jl1bgsi\sqlexpress",
    r"localhost\sqlexpress",
    r"127.0.0.1\sqlexpress",
    r"localhost",
    r"127.0.0.1",
    r".",
})


class DatabaseSafetyError(RuntimeError):
    """Raised when a test or harness attempts to connect to an unauthorized database, server or CeylonGoDB."""
    pass


def normalize_server_name(server_name: str | None) -> str:
    """Normalize server name by converting slashes, stripping whitespace and lowercasing."""
    if not server_name:
        return ""
    return str(server_name).strip().replace("/", "\\").lower()


def verify_safe_integration_server(
    server_name: str | None,
    approved_servers: frozenset[str] | set[str] | None = None,
) -> bool:
    """
    Fail-closed guard: validates that the target SQL Server instance is an explicitly
    approved integration server instance and strictly prevents execution against
    unapproved instances or default SQLEXPRESS.

    Raises:
        DatabaseSafetyError: If server_name is empty, disallowed, or not in the allowlist.
    """
    if not server_name or not str(server_name).strip():
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target server instance is unspecified or empty."
        )

    normalized = normalize_server_name(server_name)

    # Check explicitly disallowed servers
    if normalized in DISALLOWED_INTEGRATION_SERVERS:
        raise DatabaseSafetyError(
            f"DISALLOWED INTEGRATION SERVER: '{server_name}' is explicitly forbidden for integration tests. "
            f"Expected approved instance: DESKTOP-JL1BGSI\\SQLEXPRESS01."
        )

    allowed = approved_servers if approved_servers is not None else APPROVED_INTEGRATION_SERVERS
    allowed_lower = {normalize_server_name(s) for s in allowed}
    if normalized not in allowed_lower:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION SERVER: '{server_name}' is not in the "
            f"approved integration server allowlist ({sorted(allowed)})."
        )

    return True


def verify_safe_integration_database(
    database_name: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
) -> bool:
    """
    Fail-closed guard: validates that the target database is an explicitly
    approved integration database and strictly prevents execution against CeylonGoDB,
    CeylonGoDB_LoginTest, system databases, or any unapproved database.

    Raises:
        DatabaseSafetyError: If database_name is empty, targets protected databases,
                             or is not in the approved database allowlist.
    """
    if not database_name or not str(database_name).strip():
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target database name is unspecified or empty."
        )

    normalized = str(database_name).strip().lower()

    # Protected databases check
    if normalized == "ceylongodb":
        raise DatabaseSafetyError(
            f"LIVE DATABASE SAFETY VIOLATION: Target database is '{database_name}'. "
            "Integration test writes against CeylonGoDB are strictly prohibited."
        )

    if normalized in PROTECTED_DATABASES:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION DATABASE: '{database_name}' is a protected/system database. "
            "Integration test writes against login test or system databases are strictly prohibited."
        )

    allowed = approved_databases if approved_databases is not None else APPROVED_INTEGRATION_DATABASES
    allowed_lower = {d.strip().lower() for d in allowed}
    if normalized not in allowed_lower:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION DATABASE: '{database_name}' is not in the "
            f"approved integration database allowlist ({sorted(allowed)})."
        )

    return True


def verify_safe_integration_target(
    server_name: str | None,
    database_name: str | None,
    opt_in: bool | str | None = None,
) -> bool:
    """
    Combined fail-closed guard: validates server instance, database name,
    and explicit integration test opt-in flag.
    """
    if str(opt_in).strip().lower() not in ("1", "true", "yes"):
        raise DatabaseSafetyError(
            "INTEGRATION OPT-IN REQUIRED: SQL Server live integration testing requires explicit opt-in. "
            "Set RUN_SQLSERVER_INTEGRATION_TESTS=1 to enable."
        )

    verify_safe_integration_database(database_name)
    # The default instance is authorized only through this database-paired check.
    if normalize_server_name(server_name) != ARTISAN_INTEGRATION_ALIAS.lower():
        verify_safe_integration_server(server_name)
    return True


def verify_connected_server_identity(
    connection_or_engine, *, server_alias: str | None = None
) -> dict[str, str]:
    """
    Performs read-only query against the live connection to verify actual connected
    SQL Server instance and database identity before permitting write execution.
    """
    query = (
        "SELECT DB_NAME() AS current_db, "
        "@@SERVERNAME AS server_name, "
        "CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(100)) AS machine_name, "
        "CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(100)) AS instance_name;"
    )

    use_artisan_instance = (
        normalize_server_name(server_alias) == ARTISAN_INTEGRATION_ALIAS.lower()
    )
    if server_alias is not None and not use_artisan_instance:
        verify_safe_integration_server(server_alias)
    expected_identity = (
        ARTISAN_INTEGRATION_IDENTITY if use_artisan_instance else LEGACY_INTEGRATION_IDENTITY
    )
    expected_machine, expected_instance = expected_identity.lower().split("\\", 1)

    if hasattr(connection_or_engine, "connect"):
        with connection_or_engine.connect() as conn:
            row = conn.execute(text(query)).fetchone()
    elif hasattr(connection_or_engine, "cursor"):
        cursor = connection_or_engine.cursor()
        try:
            row = cursor.execute(query).fetchone()
        finally:
            cursor.close()
    else:
        row = connection_or_engine.execute(text(query)).fetchone()

    if not row:
        raise DatabaseSafetyError(
            "LIVE IDENTITY SAFETY VIOLATION: Connected database identity is unavailable."
        )
    current_db = str(row[0]).strip().lower() if row[0] else ""
    instance_name = str(row[3]).strip().lower() if row[3] else ""

    if current_db in PROTECTED_DATABASES or current_db != "ceylongodb_integrationtest":
        raise DatabaseSafetyError(
            f"LIVE IDENTITY SAFETY VIOLATION: Connected database is '{row[0]}'. "
            "Integration writes are restricted strictly to 'ceylongodb_integrationtest'."
        )

    if (
        normalize_server_name(row[1]) != expected_identity.lower()
        or str(row[2]).strip().lower() != expected_machine
        or instance_name != expected_instance
    ):
        raise DatabaseSafetyError(
            "LIVE IDENTITY SAFETY VIOLATION: Connected SQL Server identity "
            f"does not match approved identity '{expected_identity}'."
        )

    return {
        "current_db": str(row[0]),
        "server_name": str(row[1]),
        "machine_name": str(row[2]),
        "instance_name": str(row[3]),
    }


def sanitize_url_for_logging(raw_url: str | None) -> str:
    """
    Sanitize connection URLs or ODBC connection strings to remove passwords
    and sensitive credentials before formatting into error messages or logs.
    """
    if not raw_url or not str(raw_url).strip():
        return "<empty>"

    try:
        url = make_url(str(raw_url).strip())
    except (ArgumentError, TypeError, ValueError):
        return "<redacted connection string>"
    return url.set(query={}).render_as_string(hide_password=True)



def _parse_odbc_database_names(odbc_str: str) -> list[str]:
    """Extract all DATABASE or DB parameter values from an ODBC connection string."""
    dbs: list[str] = []
    # If the odbc_str is URL-encoded, decode it
    decoded = urllib.parse.unquote_plus(odbc_str)
    tokens = decoded.split(";")
    for token in tokens:
        if "=" in token:
            key, val = token.split("=", 1)
            key_clean = key.strip().upper()
            val_clean = val.strip().strip("{}").strip()
            if key_clean in ("DATABASE", "DB"):
                if val_clean:
                    dbs.append(val_clean)
    return dbs



def extract_database_name_from_url(raw_url: str | None) -> str:
    """
    Extract and validate the target database name from a database URL or ODBC string.
    Enforces fail-closed semantics:
    - Rejects empty, missing, or malformed URLs.
    - Resolves both standard URL path database names and ODBC odbc_connect parameters.
    - Rejects conflicting or ambiguous database declarations.
    - Sanitizes any error messages to prevent credential leakage.
    """
    if not raw_url or not str(raw_url).strip():
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target database connection URL is unspecified or empty."
        )

    url_str = str(raw_url).strip()

    try:
        url_obj = make_url(url_str)
    except (ArgumentError, TypeError, ValueError):
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Malformed database connection URL."
        ) from None

    declared_dbs: dict[str, str] = {}

    # 1. Path database (e.g., mssql+pyodbc://host/DatabaseName)
    if url_obj.database and str(url_obj.database).strip():
        declared_dbs["url_path"] = str(url_obj.database).strip()

    # 2. Query parameter direct database (e.g. ?database=DatabaseName)
    if url_obj.query:
        for qkey, qval in url_obj.query.items():
            if str(qkey).lower() in ("database", "db") and qval:
                declared_dbs[f"query_{qkey.lower()}"] = str(qval).strip()

        # 3. ODBC odbc_connect parameter (e.g. ?odbc_connect=...DATABASE=DatabaseName...)
        if "odbc_connect" in url_obj.query:
            odbc_raw = url_obj.query["odbc_connect"]
            if not isinstance(odbc_raw, str):
                raise DatabaseSafetyError("Ambiguous ODBC connection parameters.")
            odbc_dbs = _parse_odbc_database_names(odbc_raw)
            if odbc_dbs:
                unique_odbc = set(d.lower() for d in odbc_dbs)
                if len(unique_odbc) > 1:
                    raise DatabaseSafetyError(
                        "DATABASE SAFETY VIOLATION: Conflicting database declarations "
                        "within ODBC connection string."
                    )
                declared_dbs["odbc_connect"] = odbc_dbs[0]

    if not declared_dbs:
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target database name is unspecified "
            "or missing in connection URL."
        )

    # Check for conflicts across different declarations
    distinct_names = {v.lower(): v for v in declared_dbs.values()}
    if len(distinct_names) > 1:
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Ambiguous database selection with "
            "conflicting database declarations."
        )

    # Return the first declared form
    return next(iter(declared_dbs.values()))



def validate_disposable_sqlserver_url(
    raw_url: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
) -> str:
    """
    Fail-closed validation of disposable SQL Server connection URLs.
    Extracts database name from URL, verifies against allowlist and protected databases,
    and returns the validated database name.
    """
    db_name = extract_database_name_from_url(raw_url)
    verify_safe_integration_database(db_name, approved_databases=approved_databases)
    return db_name


def attach_runtime_database_guard(
    engine: Engine,
    approved_databases: frozenset[str] | set[str] | None = None,
    expected_db: str | None = None,
    query_override: str | None = None,
    *,
    server_alias: str | None = None,
) -> None:
    """Verify database identity on every checkout and discard rejected connections."""

    @event.listens_for(engine, "checkout")
    def validate_checkout(dbapi_connection, connection_record, connection_proxy):
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(query_override or "SELECT DB_NAME();")
                row = cursor.fetchone()
            finally:
                cursor.close()
            active_db = str(row[0]).strip() if row and row[0] is not None else ""
            # Protected targets stay forbidden even if the expected name matches.
            if active_db.lower() in PROTECTED_DATABASES or not active_db:
                verify_safe_integration_database(active_db, approved_databases)
            if expected_db and active_db.lower() != expected_db.strip().lower():
                raise DatabaseSafetyError(
                    "RUNTIME DATABASE MISMATCH: Connected database differs from "
                    "the expected integration database."
                )
            verify_safe_integration_database(active_db, approved_databases)
            if server_alias is not None:
                verify_connected_server_identity(dbapi_connection, server_alias=server_alias)
        except Exception as exc:
            try:
                dbapi_connection.close()
            except Exception:
                pass
            if isinstance(exc, DatabaseSafetyError):
                raise
            raise DatabaseSafetyError(
                "RUNTIME DATABASE SAFETY VIOLATION: Failed to verify connected identity."
            ) from None


def create_safe_sqlserver_test_engine(
    raw_url: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
    **engine_kwargs: Any,
) -> Engine:
    """Open only an opted-in, allowlisted SQL Server target with verified identity."""
    from sqlalchemy import create_engine

    target_db = validate_disposable_sqlserver_url(raw_url, approved_databases)
    url = make_url(raw_url)
    if url.drivername != "mssql+pyodbc":
        raise DatabaseSafetyError("Expected a SQL Server pyodbc test URL.")
    server_alias = url.host
    if "odbc_connect" in url.query:
        odbc = url.query["odbc_connect"]
        if not isinstance(odbc, str):
            raise DatabaseSafetyError("Ambiguous ODBC connection parameters.")
        servers = [
            value.strip().strip("{}").strip()
            for token in odbc.split(";")
            for key, sep, value in [token.partition("=")]
            if sep and key.strip().lower() in ("server", "address", "addr", "network address")
        ]
        if server_alias or len(servers) != 1:
            raise DatabaseSafetyError("Expected exactly one ODBC server declaration.")
        server_alias = servers[0]
    verify_safe_integration_target(
        server_alias, target_db, os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS")
    )

    engine = create_engine(url, **engine_kwargs)
    attach_runtime_database_guard(
        engine, approved_databases, expected_db=target_db, server_alias=server_alias
    )
    try:
        with engine.connect():
            pass
    except DatabaseSafetyError:
        engine.dispose()
        raise
    except Exception:
        engine.dispose()
        raise DatabaseSafetyError("Could not verify the SQL Server test connection.") from None
    return engine
