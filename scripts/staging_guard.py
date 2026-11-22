"""
CeylonGo - Staging Rehearsal Database Identity Guard
=====================================================
Fail-closed security module that strictly restricts staging migration rehearsal
operations to the approved staging database: CeylonGoDB_Migration_Staging.

Prevents any staging scripts, rehearsal test suites, or restore procedures from
connecting to, writing to, or modifying CeylonGoDB, CeylonGoDB_Development,
or any system/login test database.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ceylongo_staging_guard")

# Exact allowlist for staging rehearsal operations
ALLOWED_STAGING_DATABASES: frozenset[str] = frozenset({
    "ceylongodb_migration_staging",
})

# Forbidden databases that must never be targeted by rehearsal or restore operations
FORBIDDEN_DATABASES: frozenset[str] = frozenset({
    "ceylongodb",
    "ceylongodb_development",
    "ceylongodb_logintest",
    "ceylongodb_integrationtest",
    "master",
    "tempdb",
    "model",
    "msdb",
})


class StagingDatabaseSafetyError(RuntimeError):
    """Raised when a staging operation attempts to connect to or target an unauthorized database."""
    pass


def normalize_database_name(database_name: str | None) -> str:
    """Normalize database name by stripping whitespace and converting to lowercase."""
    if not database_name or not isinstance(database_name, str):
        return ""
    return str(database_name).strip().lower()


def normalize_server_name(server_name: str | None) -> str:
    """Normalize server name by stripping whitespace, slashes, and converting to lowercase."""
    if not server_name:
        return ""
    return str(server_name).strip().replace("/", "\\").lower()


def verify_safe_staging_database_name(database_name: str | None) -> str:
    """
    Fail-closed guard: validates that the target database name is non-empty,
    is NOT in the forbidden database list (especially development or production),
    and strictly matches the allowed staging database name.

    Returns:
        The canonical normalized database name if valid.

    Raises:
        StagingDatabaseSafetyError: If database_name is empty, forbidden, or unapproved.
    """
    if database_name is None or not isinstance(database_name, str) or not database_name.strip():
        raise StagingDatabaseSafetyError(
            "STAGING SAFETY VIOLATION: Target staging database name is unspecified or empty."
        )

    normalized = normalize_database_name(database_name)

    if normalized in FORBIDDEN_DATABASES:
        raise StagingDatabaseSafetyError(
            f"PROTECTED DATABASE SAFETY VIOLATION: Target database is '{database_name}'. "
            "Staging rehearsal, restore, or migration operations against development, "
            "production, or system databases are strictly prohibited."
        )

    if normalized not in ALLOWED_STAGING_DATABASES:
        raise StagingDatabaseSafetyError(
            f"UNAPPROVED STAGING DATABASE: '{database_name}' is not in the "
            f"approved staging database allowlist ({sorted(ALLOWED_STAGING_DATABASES)})."
        )

    return normalized


def verify_connected_staging_identity(
    connection_or_cursor: Any,
    expected_server: str | None = None,
    expected_instance: str | None = None,
) -> dict[str, str]:
    """
    Executes a read-only query against the live database connection to verify
    the actual connected database identity using DB_NAME() and SQL Server instance.

    Fails closed if DB_NAME() is not 'ceylongodb_migration_staging' or if
    the connected instance does not match expected server/instance criteria.

    Returns:
        Dict containing current_db, server_name, machine_name, and instance_name.

    Raises:
        StagingDatabaseSafetyError: If DB_NAME() does not match CeylonGoDB_Migration_Staging
                                   or if the connected server/instance is disallowed.
    """
    query = (
        "SELECT DB_NAME() AS current_db, "
        "@@SERVERNAME AS server_name, "
        "CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(100)) AS machine_name, "
        "CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(100)) AS instance_name;"
    )

    if hasattr(connection_or_cursor, "cursor"):
        cursor = connection_or_cursor.cursor()
        cursor.execute(query)
        row = cursor.fetchone()
    elif hasattr(connection_or_cursor, "execute"):
        row = connection_or_cursor.execute(query).fetchone()
    else:
        raise StagingDatabaseSafetyError(
            "STAGING SAFETY VIOLATION: Invalid connection object supplied to identity guard."
        )

    if not row or not row[0]:
        raise StagingDatabaseSafetyError(
            "LIVE IDENTITY SAFETY VIOLATION: Connected database identity could not be retrieved."
        )

    current_db = str(row[0]).strip().lower()
    server_name = str(row[1]).strip() if len(row) > 1 and row[1] else ""
    machine_name = str(row[2]).strip() if len(row) > 2 and row[2] else ""
    instance_name = str(row[3]).strip() if len(row) > 3 and row[3] else ""

    if current_db in FORBIDDEN_DATABASES or current_db != "ceylongodb_migration_staging":
        raise StagingDatabaseSafetyError(
            f"LIVE IDENTITY SAFETY VIOLATION: Connected database is '{row[0]}'. "
            "Rehearsal operations are strictly confined to 'CeylonGoDB_Migration_Staging'. "
            "Aborting execution immediately to protect database integrity."
        )

    # Optional server/instance validation
    if expected_instance:
        if instance_name.lower() != expected_instance.lower():
            raise StagingDatabaseSafetyError(
                f"SQL SERVER INSTANCE MISMATCH: Connected instance '{instance_name}' does not "
                f"match expected instance '{expected_instance}'."
            )

    if expected_server:
        clean_exp = normalize_server_name(expected_server)
        clean_srv = normalize_server_name(server_name)
        if "\\" in clean_exp:
            exp_inst = clean_exp.split("\\", 1)[1]
            if instance_name and instance_name.lower() != exp_inst and exp_inst not in clean_srv:
                raise StagingDatabaseSafetyError(
                    f"SQL SERVER INSTANCE MISMATCH: Connected to server '{server_name}', "
                    f"expected server '{expected_server}'."
                )

    return {
        "current_db": str(row[0]),
        "server_name": server_name,
        "machine_name": machine_name,
        "instance_name": instance_name,
    }


def assert_staging_database_does_not_exist(connection_or_cursor: Any, database_name: str) -> None:
    """
    Verifies that the target staging database does NOT currently exist on SQL Server.
    Ensures that a restore operation will never silently overwrite an existing database.

    Raises:
        StagingDatabaseSafetyError: If the target database already exists.
    """
    normalized = verify_safe_staging_database_name(database_name)
    query = "SELECT COUNT(*) FROM sys.databases WHERE LOWER(name) = ?;"

    if hasattr(connection_or_cursor, "cursor"):
        cursor = connection_or_cursor.cursor()
        cursor.execute(query, (normalized,))
        count = cursor.fetchone()[0]
    elif hasattr(connection_or_cursor, "execute"):
        row = connection_or_cursor.execute(query, (normalized,)).fetchone()
        count = row[0]
    else:
        raise StagingDatabaseSafetyError(
            "STAGING SAFETY VIOLATION: Invalid connection object supplied to database existence check."
        )

    if count > 0:
        raise StagingDatabaseSafetyError(
            f"DESTINATION DATABASE COLLISION: Database '{database_name}' already exists on the server. "
            "To prevent accidental data loss, automatic overwrite via WITH REPLACE is strictly forbidden. "
            "Drop the existing staging database manually or teardown previous rehearsal before proceeding."
        )


if __name__ == "__main__":
    import sys

    print("--- Running Staging Database Guard Self-Test ---")
    try:
        # Test 1: Valid staging DB name
        assert verify_safe_staging_database_name("CeylonGoDB_Migration_Staging") == "ceylongodb_migration_staging"
        print("  [PASS] Test 1: CeylonGoDB_Migration_Staging allowed.")

        # Test 2: Rejection of development DB
        try:
            verify_safe_staging_database_name("CeylonGoDB_Development")
            assert False, "Failed to reject CeylonGoDB_Development"
        except StagingDatabaseSafetyError:
            print("  [PASS] Test 2: CeylonGoDB_Development rejected.")

        # Test 3: Rejection of production DB
        try:
            verify_safe_staging_database_name("CeylonGoDB")
            assert False, "Failed to reject CeylonGoDB"
        except StagingDatabaseSafetyError:
            print("  [PASS] Test 3: CeylonGoDB rejected.")

        # Test 4: Rejection of system DB
        try:
            verify_safe_staging_database_name("master")
            assert False, "Failed to reject master"
        except StagingDatabaseSafetyError:
            print("  [PASS] Test 4: master rejected.")

        # Test 5: Rejection of empty string
        try:
            verify_safe_staging_database_name("")
            assert False, "Failed to reject empty name"
        except StagingDatabaseSafetyError:
            print("  [PASS] Test 5: Empty string rejected.")

        print("[SUCCESS] Staging Database Guard self-test passed completely.")
    except Exception as exc:
        print(f"[FAIL] Self-test failed: {exc}", file=sys.stderr)
        sys.exit(1)
