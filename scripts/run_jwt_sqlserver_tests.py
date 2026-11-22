"""One-shot, explicitly opted-in rehearsal against the dedicated JWT scratch DB."""
import os
import secrets
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pyodbc

from scripts import migrate

SERVER = r"VISHNUGA-KAJARASH\SQLEXPRESS"
SERVER_ALIAS = r"localhost\SQLEXPRESS"
DATABASE = "CeylonGoDB_JWT_Test"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
MARKER_TABLE = "dbo.jwt_rehearsal_owner"


def require_opt_in():
    if os.environ.get("RUN_JWT_SQLSERVER_TESTS") != "1":
        raise RuntimeError("Explicit JWT SQL Server rehearsal opt-in is required.")


def verify_identity(cursor, database):
    row = cursor.execute(
        "SELECT CONVERT(nvarchar(128), SERVERPROPERTY('ServerName')), "
        "CONVERT(nvarchar(128), SERVERPROPERTY('MachineName')), "
        "CONVERT(nvarchar(128), SERVERPROPERTY('InstanceName')), DB_NAME()"
    ).fetchone()
    expected = (SERVER, "VISHNUGA-KAJARASH", "SQLEXPRESS", database)
    if not row or tuple(row) != expected:
        raise RuntimeError("Unexpected SQL Server identity. Stopping.")


def connect(database, *, autocommit=False):
    require_opt_in()
    if database not in {"master", DATABASE}:
        raise RuntimeError("Unapproved JWT rehearsal database.")
    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=" + SERVER_ALIAS + ";DATABASE=" + database + ";"
        "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;",
        timeout=5,
        autocommit=autocommit,
    )
    try:
        conn.timeout = 30
        cursor = conn.cursor()
        verify_identity(cursor, database)
        cursor.execute("SET LOCK_TIMEOUT 10000;")
    except BaseException:
        conn.close()
        raise
    return conn


def verified_connection(marker, *, autocommit=False):
    conn = connect(DATABASE, autocommit=autocommit)
    try:
        row = conn.cursor().execute(
            f"SELECT run_id FROM {MARKER_TABLE} WHERE id = 1"
        ).fetchone()
        if not row or row[0] != marker:
            raise RuntimeError("JWT rehearsal ownership marker mismatch. Stopping.")
        if not autocommit:
            conn.rollback()
    except BaseException:
        conn.close()
        raise
    return conn


def create_and_bootstrap(marker):
    # This connection starts in master and is used only for guarded provisioning.
    with closing(connect("master", autocommit=True)) as conn:
        cursor = conn.cursor()
        row = cursor.execute(
            "SELECT HAS_PERMS_BY_NAME(NULL, NULL, 'ALTER ANY DATABASE')"
        ).fetchone()
        if not row or row[0] != 1:
            raise RuntimeError("Cannot confirm complete database visibility.")
        if cursor.execute(
            "SELECT 1 FROM sys.databases WHERE LOWER(name) = LOWER(?)", DATABASE
        ).fetchone():
            raise RuntimeError("Scratch database already exists. Stopping.")
        cursor.execute(f"CREATE DATABASE [{DATABASE}]")
        cursor.execute(f"USE [{DATABASE}]")
        verify_identity(cursor, DATABASE)
        cursor.execute(
            f"CREATE TABLE {MARKER_TABLE} ("
            "id int NOT NULL PRIMARY KEY CHECK (id = 1), "
            "run_id varchar(32) NOT NULL);"
        )
        cursor.execute(f"INSERT INTO {MARKER_TABLE} (id, run_id) VALUES (1, ?)", marker)
    print("Created dedicated scratch database and recorded ownership marker.", flush=True)
    with closing(verified_connection(marker)) as conn:
        conn.cursor().execute(
            "CREATE TABLE dbo.users ("
            "id int IDENTITY(1,1) NOT NULL PRIMARY KEY, "
            "full_name nvarchar(150) NOT NULL, "
            "email nvarchar(255) NOT NULL UNIQUE, "
            "password_hash nvarchar(255) NULL, "
            "role nvarchar(30) NOT NULL DEFAULT 'traveler', "
            "is_active bit NOT NULL DEFAULT 1, "
            "google_sub nvarchar(255) NULL, "
            "created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME());"
            "CREATE UNIQUE INDEX UQ_users_google_sub ON dbo.users (google_sub) "
            "WHERE google_sub IS NOT NULL;"
        )
        conn.commit()


def verify_ledger(marker, migrations, target):
    expected = {
        item.version: (item.filename, item.checksum)
        for item in migrations if int(item.version) <= target
    }
    with closing(verified_connection(marker)) as conn:
        records = migrate.get_applied_migrations(conn)
    actual = {version: (item.filename, item.checksum) for version, item in records.items()}
    if actual != expected or any(item.execution_method != "executed" for item in records.values()):
        raise RuntimeError("Migration ledger does not match the executed inventory.")
    print(f"Verified executed ledger 001–{target:03d}.", flush=True)


def upgrade(marker, target, *, inventory=None):
    if target not in {"039", "040"}:
        raise RuntimeError("Unapproved migration target.")
    config = migrate.DatabaseConfig(SERVER, DATABASE, connection_timeout=5)

    def guarded_connection(requested):
        if requested is not config or requested.server != SERVER or requested.database != DATABASE:
            raise RuntimeError("Migration runner requested an unapproved connection.")
        return verified_connection(marker)

    # Keep the existing Artisan guard intact. This entry point pins and verifies
    # every connection used by the generic migration executor.
    from contextlib import ExitStack
    with ExitStack() as stack:
        stack.enter_context(patch.object(migrate, "get_connection", guarded_connection))
        if inventory is not None:
            stack.enter_context(patch.object(migrate, "discover_migrations", return_value=inventory))
        result = migrate.cmd_upgrade(config, MIGRATIONS, target_version=target, confirmed=True)
    if result:
        raise RuntimeError("Migration runner returned a failure.")


def checkpoint(marker):
    with closing(verified_connection(marker, autocommit=True)) as conn:
        cursor = conn.cursor()
        path = cursor.execute(
            "SELECT CONVERT(nvarchar(4000), SERVERPROPERTY('InstanceDefaultBackupPath'))"
        ).fetchone()[0]
        if not path:
            raise RuntimeError("No SQL Server backup path is available.")
        backup = str(Path(path) / f"{DATABASE}_pre040_{marker}.bak")
        for statement in (
            f"BACKUP DATABASE [{DATABASE}] TO DISK = ? WITH COPY_ONLY, CHECKSUM",
            "RESTORE VERIFYONLY FROM DISK = ? WITH CHECKSUM",
        ):
            cursor.execute(statement, backup)
            while cursor.nextset():
                pass
    print("Pre-040 backup verified with checksum.", flush=True)


def verify_rollback(marker, migrations):
    original = migrations[-1]
    failure = "THROW 51040, 'Expected JWT rehearsal rollback probe.', 1;"
    altered = replace(original, batches=[original.batches[0], failure])
    try:
        upgrade(marker, "040", inventory=[*migrations[:-1], altered])
    except migrate.BatchExecutionError as exc:
        if exc.version != "040" or exc.batch_index != 2 or "51040" not in str(exc.original_error):
            raise
    else:
        raise RuntimeError("Injected migration failure did not occur.")
    verify_ledger(marker, migrations, 39)
    with closing(verified_connection(marker)) as conn:
        row = conn.cursor().execute(
            "SELECT COL_LENGTH('dbo.users', 'credential_version'), "
            "OBJECT_ID('dbo.DF_users_credential_version'), "
            "OBJECT_ID('dbo.CK_users_credential_version_nonnegative')"
        ).fetchone()
        if tuple(row) != (None, None, None):
            raise RuntimeError("Migration rollback left credential-version schema behind.")
    print("Injected 040 failure rolled back schema and ledger.", flush=True)


def main():
    require_opt_in()
    migrations = migrate.discover_migrations(MIGRATIONS)
    if [item.version for item in migrations] != [f"{i:03d}" for i in range(1, 41)]:
        raise RuntimeError("Expected exactly migrations 001–040.")
    if migrations[-1].filename != "040_add_users_credential_version.sql":
        raise RuntimeError("Unexpected migration 040 filename.")
    marker = uuid4().hex
    create_and_bootstrap(marker)
    upgrade(marker, "039")
    verify_ledger(marker, migrations, 39)
    with closing(verified_connection(marker)) as conn:
        conn.cursor().execute(
            "INSERT INTO dbo.users (full_name, email) VALUES "
            "('JWT pre-migration fixture', 'jwt-pre040@example.invalid');"
        )
        conn.commit()
    checkpoint(marker)
    verify_rollback(marker, migrations)
    upgrade(marker, "040")
    verify_ledger(marker, migrations, 40)
    upgrade(marker, "040")
    verify_ledger(marker, migrations, 40)

    os.environ.update(
        DB_NAME=DATABASE, DB_SERVER=SERVER,
        JWT_SECRET_KEY=secrets.token_urlsafe(48), JWT_ALGORITHM="HS256",
        JWT_ACCESS_TOKEN_EXPIRE_MINUTES="30",
    )
    os.environ.pop("JWT_LEGACY_CREDENTIAL_VERSION_UNTIL", None)
    import pytest
    from tests import test_jwt_revocation_sqlserver

    test_jwt_revocation_sqlserver.RUN_MARKER = marker
    return pytest.main([
        "-q", "-p", "no:cacheprovider", "--tb=no",
        "tests/test_jwt_revocation_sqlserver.py",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
