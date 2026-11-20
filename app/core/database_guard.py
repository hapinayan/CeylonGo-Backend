"""
CeylonGo - Database Identity Guard
===================================
Enforces fail-closed safety checks preventing integration and rehearsal test suites
from performing write operations against the live production database (CeylonGoDB),
and restricts integration testing strictly to explicitly approved integration databases.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url

APPROVED_INTEGRATION_DATABASES: frozenset[str] = frozenset({
    "ceylongodb_integrationtest",
    "ceylongodb_disposable_qa",
})

PROTECTED_DATABASES: frozenset[str] = frozenset({
    "ceylongodb",
    "master",
    "tempdb",
    "model",
    "msdb",
})


class DatabaseSafetyError(RuntimeError):
    """Raised when a test or harness attempts to connect to an unauthorized database or CeylonGoDB."""
    pass


def sanitize_url_for_logging(raw_url: str | None) -> str:
    """
    Sanitize connection URLs or ODBC connection strings to remove passwords
    and sensitive credentials before formatting into error messages or logs.
    """
    if not raw_url or not str(raw_url).strip():
        return "<empty>"

    s = str(raw_url).strip()

    # Mask standard user:password@host
    s = re.sub(r":([^/@]+)@", r":***@", s)

    # Mask ODBC PWD=... or password=...
    s = re.sub(
        r"(?i)\b(pwd|password)\s*=\s*([^;]+)",
        r"\1=***",
        s,
    )

    return s


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
    except Exception as exc:
        sanitized = sanitize_url_for_logging(url_str)
        raise DatabaseSafetyError(
            f"DATABASE SAFETY VIOLATION: Malformed database connection URL: '{sanitized}'"
        ) from exc

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
            odbc_dbs = _parse_odbc_database_names(odbc_raw)
            if odbc_dbs:
                unique_odbc = set(d.lower() for d in odbc_dbs)
                if len(unique_odbc) > 1:
                    raise DatabaseSafetyError(
                        f"DATABASE SAFETY VIOLATION: Conflicting database declarations within ODBC connection string: {sorted(odbc_dbs)}."
                    )
                declared_dbs["odbc_connect"] = odbc_dbs[0]

    if not declared_dbs:
        sanitized = sanitize_url_for_logging(url_str)
        raise DatabaseSafetyError(
            f"DATABASE SAFETY VIOLATION: Target database name is unspecified or missing in connection URL: '{sanitized}'"
        )

    # Check for conflicts across different declarations
    distinct_names = {v.lower(): v for v in declared_dbs.values()}
    if len(distinct_names) > 1:
        sources_desc = ", ".join(f"{k}='{v}'" for k, v in declared_dbs.items())
        raise DatabaseSafetyError(
            f"DATABASE SAFETY VIOLATION: Ambiguous database selection with conflicting database declarations: {sources_desc}."
        )

    # Return the first declared form
    return next(iter(declared_dbs.values()))


def verify_safe_integration_database(
    database_name: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
) -> bool:
    """
    Fail-closed guard: validates that the target database is an explicitly
    approved integration database and strictly prevents execution against CeylonGoDB,
    SQL Server system databases (master, tempdb, model, msdb), or any unapproved database.

    Raises:
        DatabaseSafetyError: If database_name is empty, targets CeylonGoDB,
                             targets a system database, or is not in the approved database allowlist.
    """
    if not database_name or not str(database_name).strip():
        raise DatabaseSafetyError(
            "DATABASE SAFETY VIOLATION: Target database name is unspecified or empty."
        )

    normalized = str(database_name).strip()
    norm_lower = normalized.lower()

    allowed = approved_databases if approved_databases is not None else APPROVED_INTEGRATION_DATABASES
    allowed_lower = {d.lower() for d in allowed}

    if norm_lower == "ceylongodb":
        raise DatabaseSafetyError(
            f"LIVE DATABASE SAFETY VIOLATION: Target database is '{normalized}'. "
            "Integration test writes against CeylonGoDB are strictly prohibited."
        )

    if norm_lower in PROTECTED_DATABASES:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION DATABASE (SYSTEM DATABASE SAFETY VIOLATION): "
            f"Target database '{normalized}' is a protected SQL Server system database "
            f"and is not in the approved integration database allowlist ({sorted(allowed)})."
        )

    if norm_lower not in allowed_lower:
        raise DatabaseSafetyError(
            f"UNAPPROVED INTEGRATION DATABASE: '{normalized}' is not in the "
            f"approved integration database allowlist ({sorted(allowed)})."
        )

    return True


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
) -> None:
    """
    Attaches a fail-closed SQLAlchemy checkout listener to an engine.
    Before any query is allowed to execute on a connection checked out from the pool,
    this hook queries SELECT DB_NAME() on the raw DBAPI connection and verifies:
    1. Active runtime database is not empty.
    2. Active database is not CeylonGoDB or any protected system database.
    3. Active database is in the approved integration database allowlist.
    4. Active database matches expected_db (if provided).

    If any check fails, the underlying connection is closed immediately and DatabaseSafetyError is raised.
    Uses dbapi_connection.cursor() directly to avoid recursive SQLAlchemy checkout triggers.
    """
    allowed = approved_databases if approved_databases is not None else APPROVED_INTEGRATION_DATABASES
    allowed_lower = {d.lower() for d in allowed}

    @event.listens_for(engine, "checkout")
    def _validate_connection_on_checkout(dbapi_connection: Any, connection_record: Any, connection_proxy: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            # Query the physical database identity from SQL Server
            query = query_override or "SELECT DB_NAME();"
            cursor.execute(query)
            row = cursor.fetchone()
            active_db = str(row[0]).strip() if row and row[0] is not None else ""
        except Exception as query_exc:
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                f"RUNTIME DATABASE SAFETY VIOLATION: Failed to query runtime database identity: {query_exc}"
            ) from query_exc
        finally:
            try:
                cursor.close()
            except Exception:
                pass

        if not active_db:
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                "RUNTIME DATABASE SAFETY VIOLATION: Active database name is empty or could not be determined."
            )

        act_lower = active_db.lower()

        if act_lower == "ceylongodb":
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                f"LIVE DATABASE SAFETY VIOLATION: Runtime database is '{active_db}'. "
                "Integration test execution against CeylonGoDB is strictly prohibited."
            )

        if act_lower in PROTECTED_DATABASES:
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                f"SYSTEM DATABASE SAFETY VIOLATION: Runtime database '{active_db}' is a protected SQL Server system database."
            )

        if act_lower not in allowed_lower:
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                f"UNAPPROVED INTEGRATION DATABASE: Runtime database '{active_db}' is not in the "
                f"approved integration database allowlist ({sorted(allowed)})."
            )

        if expected_db and act_lower != str(expected_db).strip().lower():
            try:
                dbapi_connection.close()
            except Exception:
                pass
            raise DatabaseSafetyError(
                f"RUNTIME DATABASE MISMATCH: Connected to database '{active_db}', but expected '{expected_db}'."
            )


def create_safe_sqlserver_test_engine(
    raw_url: str | None,
    approved_databases: frozenset[str] | set[str] | None = None,
    **engine_kwargs: Any,
) -> Engine:
    """
    Factory to create a verified, fail-closed SQLAlchemy engine for disposable SQL Server testing.
    1. Validates raw_url before creating any engine.
    2. Attaches attach_runtime_database_guard to the engine.
    3. Executes an initial connect test to verify runtime identity immediately.
    """
    target_db = validate_disposable_sqlserver_url(raw_url, approved_databases=approved_databases)

    engine = Engine.from_uri(raw_url, **engine_kwargs) if hasattr(Engine, "from_uri") else None
    # Use standard create_engine
    from sqlalchemy import create_engine
    engine = create_engine(raw_url, **engine_kwargs)

    attach_runtime_database_guard(
        engine,
        approved_databases=approved_databases,
        expected_db=target_db,
    )

    # Initial preflight checkout verification
    with engine.connect() as conn:
        pass

    return engine
