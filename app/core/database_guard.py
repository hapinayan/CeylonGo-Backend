"""
CeylonGo - Database Identity Guard
===================================
Enforces fail-closed safety checks preventing integration and rehearsal test suites
from performing write operations against the live production database (CeylonGoDB),
and restricts integration testing strictly to explicitly approved integration databases.
"""

from __future__ import annotations

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
    from sqlalchemy import text

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
        row = connection_or_engine.cursor().execute(query).fetchone()
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
