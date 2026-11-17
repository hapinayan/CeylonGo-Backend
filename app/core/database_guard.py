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


class DatabaseSafetyError(RuntimeError):
    """Raised when a test or harness attempts to connect to an unauthorized database or CeylonGoDB."""
    pass


def verify_safe_integration_database(
    database_name: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
) -> bool:
    """
    Fail-closed guard: validates that the target database is an explicitly
    approved integration database and strictly prevents execution against CeylonGoDB
    or any unapproved database.

    Raises:
        DatabaseSafetyError: If database_name is empty, targets CeylonGoDB,
                             or is not in the approved database allowlist.
    """
    if not database_name or not str(database_name).strip():
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target database name is unspecified or empty."
        )

    normalized = str(database_name).strip()
    if normalized.lower() == "ceylongodb":
        raise DatabaseSafetyError(
            f"LIVE DATABASE SAFETY VIOLATION: Target database is '{normalized}'. "
            "Integration test writes against CeylonGoDB are strictly prohibited."
        )

    allowed = approved_databases if approved_databases is not None else APPROVED_INTEGRATION_DATABASES
    allowed_lower = {d.lower() for d in allowed}
    if normalized.lower() not in allowed_lower:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION DATABASE: '{normalized}' is not in the "
            f"approved integration database allowlist ({sorted(allowed)})."
        )

    return True
