"""
CeylonGo - Centralized SQL Server Migration Runner
==================================================
Target Database: CeylonGoDB (Microsoft SQL Server)
Driver: pyodbc (ODBC Driver 18 for SQL Server / SQL Server)

Features:
- Robust T-SQL GO batch separator parsing with string literal & comment preservation
- Checksum verification (SHA-256 with LF line ending normalization)
- Distributed concurrency locking via sp_getapplock / sp_releaseapplock
- Isolated per-migration transaction management & rollback on error
- Target database identity verification (prevents accidental execution against wrong DB)
- Baseline verification strategy for pre-existing migrations 001-006
- Idempotent schema_migrations tracking table
- CLI commands: status, validate, plan, baseline, upgrade
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Sequence

logger = logging.getLogger("ceylongo_migrate")


# =============================================================================
# Custom Exceptions
# =============================================================================

class MigrationError(Exception):
    """Base exception for all migration runner errors."""
    pass


class TargetDatabaseMismatchError(MigrationError):
    """Raised when connected database does not match expected DB_NAME."""
    pass


class MigrationLockError(MigrationError):
    """Raised when exclusive lock via sp_getapplock cannot be acquired."""
    pass


class ChecksumMismatchError(MigrationError):
    """Raised when an applied migration file checksum differs from recorded checksum."""
    pass


class SequenceIntegrityError(MigrationError):
    """Raised when migration sequence contains duplicates or gaps."""
    pass


class BaselineVerificationError(MigrationError):
    """Raised when database schema does not match baseline expectations."""
    pass


class BatchExecutionError(MigrationError):
    """Raised when a specific batch in a migration fails."""
    def __init__(self, version: str, filename: str, batch_index: int, total_batches: int, sql_preview: str, original_error: Exception):
        self.version = version
        self.filename = filename
        self.batch_index = batch_index
        self.total_batches = total_batches
        self.sql_preview = sql_preview
        self.original_error = original_error
        super().__init__(
            f"Migration {version} ({filename}) failed at batch {batch_index}/{total_batches}: {original_error}\n"
            f"Failing Batch SQL:\n{sql_preview}"
        )


# =============================================================================
# Migration Data Models
# =============================================================================

@dataclass
class MigrationFile:
    version: str
    filename: str
    filepath: Path
    checksum: str
    raw_content: str
    batches: list[str]


@dataclass
class AppliedRecord:
    version: str
    filename: str
    checksum: str
    applied_at: datetime
    execution_time_ms: int
    execution_method: str


# =============================================================================
# T-SQL GO Batch Separator Parser
# =============================================================================

def split_sql_batches(sql: str) -> list[str]:
    """
    Splits a T-SQL script into discrete execution batches on GO boundaries.

    Rules strictly enforced:
    1. GO inside string literals ('...') is NEVER a batch separator (handles '' escapes).
    2. GO inside single-line comments (-- ...) is NEVER a batch separator.
    3. GO inside multi-line block comments (/* ... */) is NEVER a batch separator (handles nested comments).
    4. GO is case-insensitive (GO, go, Go, gO).
    5. Leading and trailing whitespace on the GO line is permitted.
    6. Trailing inline comments after GO (e.g., 'GO -- comment') are permitted.
    7. GO with execution count (e.g., 'GO 2') duplicates the preceding batch.
    8. Transact-SQL statements cannot share a line with GO.
    """
    batches: list[str] = []
    current_batch_chars: list[str] = []
    current_line_chars: list[str] = []

    # Lexer state
    in_string = False
    in_single_comment = False
    comment_depth = 0

    i = 0
    n = len(sql)

    # Line tracking: record normal characters on the current line outside strings/comments
    line_normal_chars: list[str] = []

    while i < n:
        c = sql[i]
        next_c = sql[i + 1] if i + 1 < n else ""

        # Check for newline
        if c == "\n":
            current_line_chars.append(c)
            line_str = "".join(current_line_chars)
            normal_str = "".join(line_normal_chars).strip()

            # End single-line comment on newline
            in_single_comment = False

            # Check if this line is a GO statement (only if not in multi-line comment or string)
            if comment_depth == 0 and not in_string and _is_go_command(normal_str):
                count = _parse_go_count(normal_str)
                # The line itself is the GO delimiter; do not include it in batch text
                batch_text = "".join(current_batch_chars).strip()
                if batch_text:
                    for _ in range(count):
                        batches.append(batch_text)
                current_batch_chars = []
            else:
                # Normal line; append to batch buffer
                current_batch_chars.extend(current_line_chars)

            current_line_chars = []
            line_normal_chars = []
            i += 1
            continue

        # Handle CR (normalize \r\n to \n)
        if c == "\r":
            if next_c == "\n":
                i += 1
                continue
            # Standalone CR treated as newline
            c = "\n"
            current_line_chars.append(c)
            line_str = "".join(current_line_chars)
            normal_str = "".join(line_normal_chars).strip()
            in_single_comment = False

            if comment_depth == 0 and not in_string and _is_go_command(normal_str):
                count = _parse_go_count(normal_str)
                batch_text = "".join(current_batch_chars).strip()
                if batch_text:
                    for _ in range(count):
                        batches.append(batch_text)
                current_batch_chars = []
            else:
                current_batch_chars.extend(current_line_chars)

            current_line_chars = []
            line_normal_chars = []
            i += 1
            continue

        # Within string literal
        if in_string:
            current_line_chars.append(c)
            if c == "'":
                if next_c == "'":
                    # Escaped quote inside string
                    current_line_chars.append(next_c)
                    i += 2
                    continue
                else:
                    # String terminates
                    in_string = False
            i += 1
            continue

        # Within single-line comment
        if in_single_comment:
            current_line_chars.append(c)
            i += 1
            continue

        # Within multi-line comment
        if comment_depth > 0:
            current_line_chars.append(c)
            if c == "/" and next_c == "*":
                comment_depth += 1
                current_line_chars.append(next_c)
                i += 2
                continue
            elif c == "*" and next_c == "/":
                comment_depth -= 1
                current_line_chars.append(next_c)
                i += 2
                continue
            i += 1
            continue

        # Outside strings and comments:
        if c == "'":
            in_string = True
            current_line_chars.append(c)
            i += 1
            continue
        elif c == "-" and next_c == "-":
            in_single_comment = True
            current_line_chars.append(c)
            current_line_chars.append(next_c)
            i += 2
            continue
        elif c == "/" and next_c == "*":
            comment_depth += 1
            current_line_chars.append(c)
            current_line_chars.append(next_c)
            i += 2
            continue
        else:
            current_line_chars.append(c)
            line_normal_chars.append(c)
            i += 1

    # End of string / last line processing
    if current_line_chars:
        normal_str = "".join(line_normal_chars).strip()
        if comment_depth == 0 and not in_string and _is_go_command(normal_str):
            count = _parse_go_count(normal_str)
            batch_text = "".join(current_batch_chars).strip()
            if batch_text:
                for _ in range(count):
                    batches.append(batch_text)
            current_batch_chars = []
        else:
            current_batch_chars.extend(current_line_chars)

    # Trailing content
    remaining = "".join(current_batch_chars).strip()
    if remaining:
        batches.append(remaining)

    return batches


_GO_REGEX = re.compile(r"^GO(?:\s+(\d+))?$", re.IGNORECASE)


def _is_go_command(normal_line: str) -> bool:
    """Returns True if the line consists solely of a GO command and optional count."""
    return bool(_GO_REGEX.match(normal_line.strip()))


def _parse_go_count(normal_line: str) -> int:
    """Extracts the execution count from a GO statement (defaults to 1)."""
    match = _GO_REGEX.match(normal_line.strip())
    if match and match.group(1):
        try:
            val = int(match.group(1))
            return max(val, 1)
        except ValueError:
            return 1
    return 1


# =============================================================================
# Checksum Calculation
# =============================================================================

def calculate_checksum(content_or_path: str | Path) -> str:
    """
    Computes a deterministic SHA-256 checksum of migration content.
    Normalizes all line endings (CRLF -> LF, CR -> LF) to ensure
    identical checksums across Windows, Linux, and macOS platforms.
    """
    if isinstance(content_or_path, Path) or (isinstance(content_or_path, str) and os.path.isfile(content_or_path)):
        with open(content_or_path, "rb") as f:
            raw_bytes = f.read()
    elif isinstance(content_or_path, str):
        raw_bytes = content_or_path.encode("utf-8")
    else:
        raw_bytes = bytes(content_or_path)

    text = raw_bytes.decode("utf-8", errors="replace")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# =============================================================================
# Migration Discovery & Sequence Integrity
# =============================================================================

_MIGRATION_FILENAME_PATTERN = re.compile(r"^(\d{3,})_(.+)\.sql$", re.IGNORECASE)


def discover_migrations(migrations_dir: Path | str) -> list[MigrationFile]:
    """
    Discovers all SQL migration files in the specified directory.
    Validates numeric sequence, detects duplicates, and checks for sequence gaps.
    """
    path = Path(migrations_dir)
    if not path.is_dir():
        raise MigrationError(f"Migrations directory does not exist: {path.resolve()}")

    discovered: list[MigrationFile] = []
    seen_versions: dict[str, str] = {}

    sql_files = sorted(path.glob("*.sql"), key=lambda p: p.name)

    for f in sql_files:
        match = _MIGRATION_FILENAME_PATTERN.match(f.name)
        if not match:
            logger.debug(f"Skipping non-migration file: {f.name}")
            continue

        version = match.group(1)
        if version in seen_versions:
            raise SequenceIntegrityError(
                f"Duplicate migration version '{version}' detected:\n"
                f"  - {seen_versions[version]}\n"
                f"  - {f.name}"
            )
        seen_versions[version] = f.name

        with open(f, "rb") as fp:
            raw = fp.read().decode("utf-8", errors="replace")

        checksum = calculate_checksum(raw)
        batches = split_sql_batches(raw)

        discovered.append(
            MigrationFile(
                version=version,
                filename=f.name,
                filepath=f,
                checksum=checksum,
                raw_content=raw,
                batches=batches,
            )
        )

    # Sort numerically by version
    discovered.sort(key=lambda m: int(m.version))

    # Verify unbroken sequence (no gaps)
    if discovered:
        for idx in range(len(discovered)):
            expected_num = int(discovered[0].version) + idx
            actual_num = int(discovered[idx].version)
            if actual_num != expected_num:
                raise SequenceIntegrityError(
                    f"Migration sequence gap detected: expected version {expected_num:03d}, "
                    f"found {actual_num:03d} ({discovered[idx].filename})"
                )

    return discovered


# =============================================================================
# Database Connection & Configuration
# =============================================================================

@dataclass
class DatabaseConfig:
    server: str
    database: str
    user: str | None = None
    password: str | None = None
    driver: str = "ODBC Driver 18 for SQL Server"
    trust_server_certificate: str = "yes"
    connection_timeout: int = 60
    expected_server: str | None = None
    integration_opt_in: str | None = None

    @classmethod
    def from_env(cls, env_path: Path | str | None = None, database_override: str | None = None) -> DatabaseConfig:
        """Reads configuration from environment variables or .env file."""
        if env_path is None:
            if Path(".env").is_file():
                env_path = Path(".env")
            elif (Path(__file__).resolve().parent.parent / ".env").is_file():
                env_path = Path(__file__).resolve().parent.parent / ".env"

        if env_path and Path(env_path).is_file():
            _load_env_file(Path(env_path))

        server = os.getenv("DB_SERVER", r".\SQLEXPRESS")
        database = database_override or os.getenv("DB_NAME", "CeylonGoDB")
        user = os.getenv("DB_USER") or None
        password = os.getenv("DB_PASSWORD") or None
        driver = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
        trust_cert = os.getenv("DB_TRUST_CERTIFICATE", "yes")

        return cls(
            server=server,
            database=database,
            user=user,
            password=password,
            driver=driver,
            trust_server_certificate=trust_cert,
            expected_server=server,
        )

    def build_connection_string(self) -> str:
        """Builds an ODBC connection string for pyodbc."""
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={self.database}",
            f"TrustServerCertificate={self.trust_server_certificate}",
        ]
        if self.user and self.password:
            parts.append(f"UID={self.user}")
            parts.append(f"PWD={self.password}")
        else:
            parts.append("Trusted_Connection=yes")

        return ";".join(parts) + ";"


def _load_env_file(path: Path) -> None:
    """Simple parser for .env files without requiring external python-dotenv."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        logger.warning(f"Could not read .env file at {path}: {e}")


def get_connection(config: DatabaseConfig):
    """Establishes an active pyodbc connection."""
    import pyodbc
    conn_str = config.build_connection_string()
    return pyodbc.connect(conn_str, timeout=config.connection_timeout, autocommit=False)


DISALLOWED_SYSTEM_DATABASES = {"master", "tempdb", "model", "msdb"}


def verify_target_database(connection, expected_database: str, expected_server: str | None = None) -> str:
    """
    Verifies that the current connection is connected to the expected database
    and SQL Server instance. Prevents accidental execution against system
    databases or mismatched instances.
    """
    cursor = connection.cursor()
    cursor.execute("SELECT DB_NAME() AS current_db, @@SERVERNAME AS server_name, CAST(SERVERPROPERTY('MachineName') AS NVARCHAR(100)) AS machine_name, CAST(SERVERPROPERTY('InstanceName') AS NVARCHAR(100)) AS instance_name;")
    row = cursor.fetchone()
    current_db = str(row[0]) if row and len(row) > 0 and row[0] else ""
    server_name = str(row[1]) if row and len(row) > 1 and row[1] else ""
    machine_name = str(row[2]) if row and len(row) > 2 and row[2] else ""
    instance_name = str(row[3]) if row and len(row) > 3 and row[3] else ""

    if current_db.lower() in DISALLOWED_SYSTEM_DATABASES:
        raise TargetDatabaseMismatchError(
            f"Execution rejected: Connected to system database '{current_db}'. "
            f"Migrations must never target system databases."
        )

    if current_db.lower() != expected_database.lower():
        raise TargetDatabaseMismatchError(
            f"Target database mismatch: connected to '{current_db}' on '{server_name}', "
            f"but expected '{expected_database}'. Execution aborted for safety."
        )

    if expected_server:
        clean_exp = expected_server.strip(r".\ ").lower()
        clean_srv = server_name.strip(r".\ ").lower()
        # Check instance match if expected_server contains instance name (e.g. SQLEXPRESS)
        if "\\" in expected_server:
            exp_inst = expected_server.split("\\", 1)[1].lower()
            if instance_name and instance_name.lower() != exp_inst and exp_inst not in clean_srv:
                raise TargetDatabaseMismatchError(
                    f"Target SQL Server instance mismatch: connected to instance '{instance_name}', "
                    f"expected '{exp_inst}'. Execution aborted."
                )

    return current_db


# =============================================================================
# Distributed Concurrency Locking
# =============================================================================

@contextmanager
def migration_lock(connection, timeout_ms: int = 15000) -> Generator[None, None, None]:
    """
    Acquires an exclusive application lock using sp_getapplock.
    Ensures that only one migration runner executes at any given time.
    Releases lock automatically via sp_releaseapplock.
    """
    cursor = connection.cursor()
    cursor.execute("""
        DECLARE @res INT;
        EXEC @res = sp_getapplock
            @Resource = 'CeylonGo_Migrations_Lock',
            @LockMode = 'Exclusive',
            @LockOwner = 'Session',
            @LockTimeout = ?;
        SELECT @res AS LockResult;
    """, (timeout_ms,))
    row = cursor.fetchone()
    result = row[0] if row else -999

    if result < 0:
        err_msg = {
            -1: "Lock request timed out",
            -2: "Lock request canceled",
            -3: "Lock request chosen as deadlock victim",
            -999: "Parameter validation or general error",
        }.get(result, f"Unknown error code {result}")
        raise MigrationLockError(f"Could not acquire migration lock: {err_msg} (result code {result}).")

    logger.debug("Acquired exclusive migration lock (CeylonGo_Migrations_Lock).")
    try:
        yield
    finally:
        try:
            cursor.execute("""
                EXEC sp_releaseapplock
                    @Resource = 'CeylonGo_Migrations_Lock',
                    @LockOwner = 'Session';
            """)
            logger.debug("Released exclusive migration lock (CeylonGo_Migrations_Lock).")
        except Exception as e:
            logger.warning(f"Failed to release migration lock: {e}")


# =============================================================================
# Schema Migrations History Table Management
# =============================================================================

SCHEMA_MIGRATIONS_DDL = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'schema_migrations'
      AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.schema_migrations (
        version VARCHAR(50) NOT NULL,
        filename VARCHAR(255) NOT NULL,
        checksum VARCHAR(64) NOT NULL,
        applied_at DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        execution_time_ms INT NOT NULL,
        execution_method VARCHAR(20) NOT NULL,
        CONSTRAINT PK_schema_migrations PRIMARY KEY CLUSTERED (version),
        CONSTRAINT CK_schema_migrations_method CHECK (execution_method IN ('executed', 'verified_baseline'))
    );

    CREATE NONCLUSTERED INDEX IX_schema_migrations_applied_at
        ON dbo.schema_migrations (applied_at);
END;
"""


def ensure_schema_migrations_table(connection) -> None:
    """Ensures dbo.schema_migrations table exists on the target database."""
    cursor = connection.cursor()
    cursor.execute(SCHEMA_MIGRATIONS_DDL)
    connection.commit()


def get_applied_migrations(connection) -> dict[str, AppliedRecord]:
    """
    Reads all applied migration records from dbo.schema_migrations.
    Returns empty dict if table does not exist.
    """
    cursor = connection.cursor()
    # Check if table exists
    cursor.execute("""
        SELECT COUNT(*)
        FROM sys.tables
        WHERE name = 'schema_migrations' AND schema_id = SCHEMA_ID('dbo');
    """)
    if cursor.fetchone()[0] == 0:
        return {}

    cursor.execute("""
        SELECT version, filename, checksum, applied_at, execution_time_ms, execution_method
        FROM dbo.schema_migrations
        ORDER BY version;
    """)
    records: dict[str, AppliedRecord] = {}
    for row in cursor.fetchall():
        records[row[0]] = AppliedRecord(
            version=row[0],
            filename=row[1],
            checksum=row[2],
            applied_at=row[3],
            execution_time_ms=row[4],
            execution_method=row[5],
        )
    return records


# =============================================================================
# Baseline Verification Strategy
# =============================================================================

def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SELECT OBJECT_ID(?, 'U');", (table_name,))
    row = cursor.fetchone()
    return bool(row and row[0] is not None)


def _col_exists(cursor, table_name: str, col_name: str) -> bool:
    cursor.execute("SELECT COL_LENGTH(?, ?);", (table_name, col_name))
    row = cursor.fetchone()
    return bool(row and row[0] is not None)


def _fk_exists(cursor, table_name: str, fk_name: str) -> bool:
    cursor.execute("""
        SELECT 1 FROM sys.foreign_keys
        WHERE name = ? AND parent_object_id = OBJECT_ID(?);
    """, (fk_name, table_name))
    row = cursor.fetchone()
    return bool(row and row[0] is not None)


def _index_exists(cursor, table_name: str, index_name: str) -> bool:
    cursor.execute("""
        SELECT 1 FROM sys.indexes
        WHERE name = ? AND object_id = OBJECT_ID(?);
    """, (index_name, table_name))
    row = cursor.fetchone()
    return bool(row and row[0] is not None)


def _check_constraint_exists(cursor, table_name: str, constraint_name: str) -> bool:
    cursor.execute("""
        SELECT 1 FROM sys.check_constraints
        WHERE name = ? AND parent_object_id = OBJECT_ID(?);
    """, (constraint_name, table_name))
    row = cursor.fetchone()
    return bool(row and row[0] is not None)


def verify_baseline_schema(connection) -> tuple[bool, list[str]]:
    """
    Inspects live database schema to confirm that migrations 001-006 structures exist.
    Validates tables, columns, nullability, foreign keys, indexes, and check constraints.
    Returns (True, []) if verified, or (False, missing_items) if any check fails.
    """
    cursor = connection.cursor()
    missing: list[str] = []

    # 001: dbo.password_resets table, columns, FK, and index
    if not _table_exists(cursor, "dbo.password_resets"):
        missing.append("001: Table dbo.password_resets missing")
    else:
        if not _col_exists(cursor, "dbo.password_resets", "token_hash"):
            missing.append("001: Column dbo.password_resets.token_hash missing")
        if not _col_exists(cursor, "dbo.password_resets", "expires_at"):
            missing.append("001: Column dbo.password_resets.expires_at missing")
        if not _fk_exists(cursor, "dbo.password_resets", "FK_password_resets_users"):
            missing.append("001: Foreign key FK_password_resets_users missing")
        if not _index_exists(cursor, "dbo.password_resets", "IX_password_resets_token_hash"):
            missing.append("001: Index IX_password_resets_token_hash missing")

    # 002: dbo.users table, google_sub column, nullable password_hash, unique index
    if not _table_exists(cursor, "dbo.users"):
        missing.append("002: Table dbo.users missing")
    else:
        if not _col_exists(cursor, "dbo.users", "google_sub"):
            missing.append("002: Column dbo.users.google_sub missing")
        if not _index_exists(cursor, "dbo.users", "UQ_users_google_sub"):
            missing.append("002: Unique index UQ_users_google_sub missing")

    # 003: dbo.provider_profiles table, category/provider_type, constraints & indexes
    if not _table_exists(cursor, "dbo.provider_profiles"):
        missing.append("003: Table dbo.provider_profiles missing")
    else:
        if not _col_exists(cursor, "dbo.provider_profiles", "provider_type"):
            missing.append("003: Column dbo.provider_profiles.provider_type missing")
        if not _col_exists(cursor, "dbo.provider_profiles", "approval_status"):
            missing.append("003: Column dbo.provider_profiles.approval_status missing")
        cursor.execute("""
            SELECT 1 FROM sys.indexes i
            INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
            INNER JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
            WHERE i.object_id = OBJECT_ID('dbo.provider_profiles')
              AND i.is_unique = 1
              AND c.name = 'user_id';
        """)
        row = cursor.fetchone()
        if not (row and row[0] is not None):
            missing.append("003: Unique constraint on dbo.provider_profiles(user_id) missing")
        if not _fk_exists(cursor, "dbo.provider_profiles", "FK_provider_profiles_users"):
            missing.append("003: Foreign key FK_provider_profiles_users missing")
        if not _check_constraint_exists(cursor, "dbo.provider_profiles", "CK_provider_profiles_type"):
            missing.append("003: Check constraint CK_provider_profiles_type missing")
        if not _check_constraint_exists(cursor, "dbo.provider_profiles", "CK_provider_profiles_approval"):
            missing.append("003: Check constraint CK_provider_profiles_approval missing")

    # 004: extended provider profile columns & auth tables
    if not _table_exists(cursor, "dbo.provider_email_otps"):
        missing.append("004: Table dbo.provider_email_otps missing")

    if not _table_exists(cursor, "dbo.provider_registration_authorizations"):
        missing.append("004: Table dbo.provider_registration_authorizations missing")

    if not _col_exists(cursor, "dbo.provider_profiles", "nic_number"):
        missing.append("004: Column dbo.provider_profiles.nic_number missing")
    if not _col_exists(cursor, "dbo.provider_profiles", "permanent_province"):
        missing.append("004: Column dbo.provider_profiles.permanent_province missing")

    # 005: provider registration drafts
    if not _table_exists(cursor, "dbo.provider_registration_drafts"):
        missing.append("005: Table dbo.provider_registration_drafts missing")

    # 006: personal details fields
    if not _col_exists(cursor, "dbo.provider_profiles", "gender"):
        missing.append("006: Column dbo.provider_profiles.gender missing")
    if not _col_exists(cursor, "dbo.provider_profiles", "date_of_birth"):
        missing.append("006: Column dbo.provider_profiles.date_of_birth missing")
    if not _col_exists(cursor, "dbo.provider_profiles", "permanent_landmark"):
        missing.append("006: Column dbo.provider_profiles.permanent_landmark missing")

    return (len(missing) == 0, missing)


# =============================================================================
# Core Runner Commands
# =============================================================================

def cmd_status(config: DatabaseConfig, migrations_dir: Path) -> int:
    """Displays the status of all migrations (applied, pending, baseline, checksum validity)."""
    migrations = discover_migrations(migrations_dir)
    conn = get_connection(config)
    try:
        current_db = verify_target_database(conn, config.database)
        applied = get_applied_migrations(conn)

        print("\n" + "=" * 95)
        print(f" CEYLONGO SQL SERVER MIGRATION STATUS — Database: {current_db} on {config.server}")
        print("=" * 95)
        print(f"{'Version':<8} {'Filename':<38} {'Status':<18} {'Applied At (UTC)':<20} {'Checksum'}")
        print("-" * 95)

        for m in migrations:
            if m.version in applied:
                rec = applied[m.version]
                status_str = f"Applied ({rec.execution_method})"
                applied_time = rec.applied_at.strftime("%Y-%m-%d %H:%M:%S") if rec.applied_at else "N/A"
                if rec.checksum == m.checksum:
                    chk_str = "[OK] Valid"
                else:
                    chk_str = "[MISMATCH] File Modified!"
            else:
                status_str = "PENDING"
                applied_time = "-"
                chk_str = "[Pending]"

            print(f"{m.version:<8} {m.filename:<38} {status_str:<18} {applied_time:<20} {chk_str}")

        print("=" * 95)
        applied_count = len([m for m in migrations if m.version in applied])
        pending_count = len(migrations) - applied_count
        print(f"Total Discovered: {len(migrations)} | Applied/Baseline: {applied_count} | Pending: {pending_count}\n")
        return 0
    finally:
        conn.close()


def cmd_validate(config: DatabaseConfig | None, migrations_dir: Path, check_db: bool = True) -> int:
    """
    Validates migration file integrity, batch parsing, sequential numbering,
    and (if check_db is True) verifies applied checksum matches on the database.
    """
    print("\n--- Validating Migration Files ---")
    migrations = discover_migrations(migrations_dir)
    print(f"Discovered {len(migrations)} migration files in {migrations_dir.resolve()}.")

    # Validate batches
    for m in migrations:
        if not m.batches:
            raise MigrationError(f"Migration file {m.filename} contains no executable batches.")
        print(f"  [OK] {m.filename} -> {len(m.batches)} batch(es) parsed successfully.")

    if check_db and config:
        conn = get_connection(config)
        try:
            verify_target_database(conn, config.database)
            applied = get_applied_migrations(conn)
            mismatches: list[str] = []
            for m in migrations:
                if m.version in applied:
                    rec = applied[m.version]
                    if rec.checksum != m.checksum:
                        mismatches.append(
                            f"Version {m.version} ({m.filename}): DB Checksum={rec.checksum[:12]}..., "
                            f"File Checksum={m.checksum[:12]}..."
                        )
            if mismatches:
                print("\n[FAILED] CHECKSUM TAMPERING OR MODIFICATION DETECTED:")
                for mm in mismatches:
                    print(f"  - {mm}")
                raise ChecksumMismatchError("Applied migrations have been modified on disk.")
            else:
                print("\n[OK] All applied migration checksums match on-disk migration files.")
        finally:
            conn.close()

    print("[SUCCESS] All migrations validated successfully.\n")
    return 0


def cmd_plan(config: DatabaseConfig, migrations_dir: Path, target_version: str | None = None) -> int:
    """Performs a dry-run plan showing pending migrations and statements without modifying the database."""
    migrations = discover_migrations(migrations_dir)
    conn = get_connection(config)
    try:
        current_db = verify_target_database(conn, config.database)
        applied = get_applied_migrations(conn)

        pending = [m for m in migrations if m.version not in applied]
        if target_version:
            pending = [m for m in pending if int(m.version) <= int(target_version)]

        print("\n" + "=" * 90)
        print(f" CEYLONGO MIGRATION EXECUTION PLAN (DRY-RUN) — Target: {current_db}")
        print("=" * 90)

        if not pending:
            print("No pending migrations to apply. Database schema is up to date.\n")
            return 0

        print(f"Found {len(pending)} pending migration(s) to execute:\n")
        for idx, m in enumerate(pending, 1):
            print(f"[{idx}/{len(pending)}] Migration {m.version}: {m.filename}")
            print(f"       Checksum : {m.checksum}")
            print(f"       Batches  : {len(m.batches)}")
            for b_idx, batch in enumerate(m.batches, 1):
                preview = "\n".join("         | " + line for line in batch.strip().splitlines()[:5])
                print(f"       Batch {b_idx} Preview:\n{preview}")
                if len(batch.strip().splitlines()) > 5:
                    print(f"         | ... ({len(batch.strip().splitlines()) - 5} more lines)")
            print("-" * 90)

        print(f"Execution would apply {len(pending)} migration(s) in sequential order.")
        print("=" * 90 + "\n")
        return 0
    finally:
        conn.close()


def cmd_baseline(config: DatabaseConfig, migrations_dir: Path, confirmed: bool = False) -> int:
    """
    Verifies that the existing schema matches migrations 001-006 and registers
    them in dbo.schema_migrations as 'verified_baseline' without executing DDL.
    Requires --yes flag.
    """
    if not confirmed:
        print("\n[ABORTED] Baseline registration requires explicit confirmation.")
        print("Pass '--yes' to confirm that you intend to register migrations 001-006 as baseline.")
        return 1

    migrations = discover_migrations(migrations_dir)
    baseline_migrations = [m for m in migrations if int(m.version) <= 6]
    if len(baseline_migrations) != 6:
        raise MigrationError(f"Expected 6 baseline migrations (001-006), discovered {len(baseline_migrations)}.")

    conn = get_connection(config)
    try:
        current_db = verify_target_database(conn, config.database)

        with migration_lock(conn):
            ensure_schema_migrations_table(conn)
            applied = get_applied_migrations(conn)

            # Check if any baseline migrations are already recorded
            already = [m.version for m in baseline_migrations if m.version in applied]
            if already:
                print(f"[INFO] Baseline versions already recorded: {', '.join(already)}")

            # Perform comprehensive schema verification
            print("\nVerifying existing database schema against baseline requirements (001-006)...")
            is_valid, missing = verify_baseline_schema(conn)
            if not is_valid:
                print("\n[FAILED] BASELINE SCHEMA VERIFICATION DISCREPANCIES DETECTED:")
                for item in missing:
                    print(f"  - {item}")
                raise BaselineVerificationError("Database schema does not match expected baseline for migrations 001-006.")

            print("[OK] Schema verification passed: all required tables, columns, and constraints for 001-006 exist.")

            cursor = conn.cursor()
            recorded_count = 0
            for m in baseline_migrations:
                if m.version in applied:
                    continue
                cursor.execute("""
                    INSERT INTO dbo.schema_migrations (
                        version, filename, checksum, applied_at, execution_time_ms, execution_method
                    ) VALUES (?, ?, ?, GETUTCDATE(), 0, 'verified_baseline');
                """, (m.version, m.filename, m.checksum))
                recorded_count += 1
                print(f"  [RECORDED BASELINE] Version {m.version}: {m.filename}")

            conn.commit()
            print(f"\n[SUCCESS] Successfully registered {recorded_count} baseline migration(s) on {current_db}.\n")
            return 0
    finally:
        conn.close()


def cmd_upgrade(
    config: DatabaseConfig,
    migrations_dir: Path,
    target_version: str | None = None,
    confirmed: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Applies all pending migrations in order within transactional safety wrappers.
    Requires --yes flag.
    """
    if dry_run:
        return cmd_plan(config, migrations_dir, target_version)

    if not confirmed:
        print("\n[ABORTED] Database upgrade requires explicit confirmation.")
        print("Pass '--yes' to confirm applying pending migrations to the database.")
        return 1

    isolated = config.integration_opt_in is not None or (
        config.database.strip().casefold() == "ceylongodb_integrationtest"
    )
    if isolated:
        from app.core.database_guard import (
            ARTISAN_INTEGRATION_ALIAS,
            DatabaseSafetyError,
            normalize_server_name,
            verify_connected_server_identity,
            verify_safe_integration_target,
        )

        verify_safe_integration_target(
            config.server, config.database, config.integration_opt_in
        )
        if (
            normalize_server_name(config.server) != ARTISAN_INTEGRATION_ALIAS.lower()
            or config.user or config.password or target_version not in {"037", "038"}
        ):
            raise DatabaseSafetyError(
                "Artisan rehearsal requires the approved Windows-authenticated target "
                "and a migration ceiling of 037 or 038."
            )

    migrations = discover_migrations(migrations_dir)
    conn = get_connection(config)
    try:
        current_db = verify_target_database(conn, config.database)
        if isolated:
            verify_connected_server_identity(conn, server_alias=config.server)

        with migration_lock(conn):
            if not isolated:
                ensure_schema_migrations_table(conn)
            applied = get_applied_migrations(conn)
            if isolated and not {f"{version:03d}" for version in range(1, 37)} <= applied.keys():
                raise MigrationError("Artisan rehearsal requires a verified ledger through 036.")

            # 1. Verify unknown migrations in DB history and verify checksums
            discovered_map = {m.version: m for m in migrations}
            for ver, rec in applied.items():
                if ver not in discovered_map:
                    raise MigrationError(
                        f"Unknown migration version '{ver}' recorded in database history but missing from disk."
                    )
                if rec.filename != discovered_map[ver].filename:
                    raise MigrationError(
                        f"Migration {ver} filename mismatch: DB recorded '{rec.filename}', disk has '{discovered_map[ver].filename}'."
                    )
                if rec.checksum != discovered_map[ver].checksum:
                    raise ChecksumMismatchError(
                        f"Applied migration {ver} ({rec.filename}) checksum mismatch!\n"
                        f"Recorded in DB: {rec.checksum}\n"
                        f"File on disk  : {discovered_map[ver].checksum}\n"
                        f"Execution aborted to prevent database corruption."
                    )

            # 2. Determine pending migrations
            pending = [m for m in migrations if m.version not in applied]
            if target_version:
                pending = [m for m in pending if int(m.version) <= int(target_version)]

            if not pending:
                print(f"\n[UP TO DATE] Database '{current_db}' has no pending migrations.")
                return 0

            # 3. Check for untracked tables from pending migrations
            cursor = conn.cursor()
            pending_tables_map = {
                "007": ["dbo.homestays", "dbo.homestay_images"],
                "008": ["dbo.homestay_amenities"],
                "010": ["dbo.homestay_bookings"],
                "011": ["dbo.homestay_promotions"],
                "012": ["dbo.artisan_listings"],
                "013": ["dbo.showcase_items"],
                "014": ["dbo.artisan_promotions"],
            }
            untracked_conflicts: list[str] = []
            for m in pending:
                if m.version in pending_tables_map:
                    for tbl in pending_tables_map[m.version]:
                        if _table_exists(cursor, tbl):
                            untracked_conflicts.append(f"Table '{tbl}' already exists in database, but migration {m.version} is pending.")
            if untracked_conflicts:
                for conf in untracked_conflicts:
                    print(f"  [CONFLICT] {conf}")
                raise MigrationError("Existing untracked tables detected. Explicit reconciliation required before upgrading.")

            print(f"\nStarting upgrade of '{current_db}' ({len(pending)} pending migrations)...")

            cursor = conn.cursor()
            for idx, m in enumerate(pending, 1):
                print(f"\n[{idx}/{len(pending)}] Applying Migration {m.version}: {m.filename}")
                start_time = time.perf_counter()

                try:
                    # Execute batches within isolated transaction
                    for b_idx, batch in enumerate(m.batches, 1):
                        if verbose:
                            print(f"  -> Executing batch {b_idx}/{len(m.batches)}...")
                        cursor.execute(batch)

                    duration_ms = int((time.perf_counter() - start_time) * 1000)

                    # Record execution in history table
                    cursor.execute("""
                        INSERT INTO dbo.schema_migrations (
                            version, filename, checksum, applied_at, execution_time_ms, execution_method
                        ) VALUES (?, ?, ?, GETUTCDATE(), ?, 'executed');
                    """, (m.version, m.filename, m.checksum, duration_ms))

                    # Commit migration transaction
                    conn.commit()
                    print(f"  [APPLIED] Migration {m.version} completed successfully in {duration_ms}ms ({len(m.batches)} batch(es)).")

                except Exception as err:
                    conn.rollback()
                    preview = batch.strip()[:300] if "batch" in locals() else "N/A"
                    logger.error(f"Migration {m.version} failed: {err}")
                    raise BatchExecutionError(
                        version=m.version,
                        filename=m.filename,
                        batch_index=b_idx if "b_idx" in locals() else 1,
                        total_batches=len(m.batches),
                        sql_preview=preview,
                        original_error=err,
                    ) from err

            print(f"\n[SUCCESS] All {len(pending)} pending migrations applied successfully.\n")
            return 0
    finally:
        conn.close()


# =============================================================================
# CLI Entry Point
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate.py",
        description="CeylonGo SQL Server Centralized Migration Runner",
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help="Path to .env configuration file (default: looks in working dir).",
    )
    parser.add_argument(
        "--database",
        type=str,
        default=None,
        help="Target database name override (e.g. CeylonGoDB_Rehearsal).",
    )
    parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Target server instance override.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=str,
        default=None,
        help="Path to migrations directory (default: CeylonGo-Backend/migrations).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug output.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Show migration status and history.")

    # validate
    val_p = subparsers.add_parser("validate", help="Validate migration files and checksums.")
    val_p.add_argument("--no-db", action="store_true", help="Skip database connection check.")

    # plan
    plan_p = subparsers.add_parser("plan", help="Dry-run preview of pending migrations.")
    plan_p.add_argument("--target", type=str, default=None, help="Target version to plan up to.")

    # baseline
    base_p = subparsers.add_parser("baseline", help="Record baseline migrations 001-006.")
    base_p.add_argument("--yes", action="store_true", help="Confirm baseline registration.")

    # upgrade
    up_p = subparsers.add_parser("upgrade", help="Execute pending migrations.")
    up_p.add_argument("--target", type=str, default=None, help="Target version to migrate up to.")
    up_p.add_argument("--dry-run", action="store_true", help="Preview plan without executing.")
    up_p.add_argument("--yes", action="store_true", help="Confirm execution of pending migrations.")
    up_p.add_argument("-v", "--verbose", action="store_true", help="Enable verbose batch output.")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Locate project paths
    base_dir = Path(__file__).resolve().parent.parent
    env_file = Path(args.env_file) if args.env_file else base_dir / ".env"
    migrations_dir = Path(args.migrations_dir) if args.migrations_dir else base_dir / "migrations"

    config = DatabaseConfig.from_env(env_file if env_file.is_file() else None, database_override=args.database)
    if args.server:
        config.server = args.server
        config.expected_server = args.server

    try:
        if args.command == "status":
            return cmd_status(config, migrations_dir)
        elif args.command == "validate":
            return cmd_validate(config, migrations_dir, check_db=not args.no_db)
        elif args.command == "plan":
            return cmd_plan(config, migrations_dir, target_version=args.target)
        elif args.command == "baseline":
            return cmd_baseline(config, migrations_dir, confirmed=args.yes)
        elif args.command == "upgrade":
            return cmd_upgrade(
                config=config,
                migrations_dir=migrations_dir,
                target_version=args.target,
                confirmed=args.yes,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        else:
            parser.print_help()
            return 1
    except MigrationError as err:
        print(f"\n[ERROR] {err}\n", file=sys.stderr)
        return 1
    except Exception as err:
        logger.exception("Unexpected runner error")
        print(f"\n[FATAL ERROR] {err}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
