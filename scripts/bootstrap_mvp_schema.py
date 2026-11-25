"""Bootstrap and incrementally upgrade the Academic MVP SQL Server schema."""

import argparse
import hashlib
import os
import socket
import sys
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path

import pyodbc

if __package__:
    from .migrate import MigrationError, split_sql_batches
else:
    from migrate import MigrationError, split_sql_batches


MAIN_DATABASE = "CeylonGoDB"
ALLOWED_TARGETS = {
    "CeylonGoDB_MVP_Rehearsal",
    "CeylonGoDB_IntegrationTest",
    "CeylonGoDB_Rehearsal_V3",
    "CeylonGoDB_Test",
}
SCRIPT_DIR = Path(__file__).resolve().parent
OTP_TABLE = "dbo.traveler_email_challenges"


@dataclass(frozen=True)
class SchemaVersion:
    version: str
    path: Path
    sha256: str
    creates_table: str | None = None

    def batches(self) -> list[str]:
        # Keep the text hashing convention of the original MVP bootstrap runner.
        sql = self.path.read_text(encoding="utf-8")
        if hashlib.sha256(sql.encode("utf-8")).hexdigest() != self.sha256:
            raise MigrationError(f"File checksum mismatch for MVP {self.version}")
        batches = split_sql_batches(sql)
        if not batches:
            raise MigrationError(f"Empty MVP migration {self.version}")
        return batches


BOOTSTRAP = SchemaVersion(
    "1.0.0",
    SCRIPT_DIR / "bootstrap_mvp_schema.sql",
    "23cba1afd852bead782688de5ef62b36067585dcdb306792e0b678d8573483e1",
)
UPGRADES = (
    SchemaVersion(
        "1.0.1",
        SCRIPT_DIR.parent / "migrations" / "044_create_traveler_email_challenges.sql",
        "16d8827bf0c2871a032383b7cc07f6e3bc4e050e28216c9ebd951addc92f1800",
        OTP_TABLE,
    ),
    SchemaVersion(
        "1.0.2",
        SCRIPT_DIR / "mvp_upgrades" / "1.0.2_provider_registration_details.sql",
        "27f980efb71da6b4c7616177ff7a7ea482fb7eb7eab511ccf04cb1880501cb6c",
    ),
    SchemaVersion(
        "1.0.3",
        SCRIPT_DIR / "mvp_upgrades" / "1.0.3_homestay_metadata_and_wishlist.sql",
        "f8762aae4b96f5b948c7cc66992fea30873dd73adcae5e7b7dacba9a5fa6a8b4",
        "dbo.homestay_wishlist",
    ),
    SchemaVersion(
        "1.0.4", SCRIPT_DIR / "mvp_upgrades" / "1.0.4_listing_verification.sql",
        "d58a794b9bf53402a0d1c535f0a292128bccdfe82758b50db9e992c02547faf6",
        "dbo.listing_verification",
    ),
)


def _connect(server: str, database: str, *, read_only: bool):
    intent = "ReadOnly" if read_only else "ReadWrite"
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={database};Trusted_Connection=yes;"
        f"TrustServerCertificate=yes;ApplicationIntent={intent};",
        autocommit=read_only,
        timeout=8,
    )


def _expected_server(server: str) -> str:
    address = server.removeprefix("lpc:").removeprefix("tcp:")
    host, separator, instance = address.partition("\\")
    if host.casefold() in {".", "(local)", "localhost"}:
        host = socket.gethostname()
    return f"{host}{separator}{instance}"


def _verify_identity(cursor, database: str, server: str) -> None:
    actual_db, actual_server = cursor.execute(
        "SELECT DB_NAME(), CONVERT(nvarchar(128), SERVERPROPERTY('ServerName'))"
    ).fetchone()
    if (
        actual_db.casefold() != database.casefold()
        or actual_server.casefold() != _expected_server(server).casefold()
    ):
        raise MigrationError(f"Unexpected SQL target {actual_server} / {actual_db}")
    print(f"Verified SQL target {actual_server} / {actual_db}")


def _table_exists(cursor, name: str) -> bool:
    return cursor.execute("SELECT OBJECT_ID(?, N'U')", name).fetchone()[0] is not None


@contextmanager
def _migration_lock(cursor):
    # Keep lock and migration statements on one ODBC cursor.
    resource = "CeylonGo_Migrations_Lock"
    result = cursor.execute(
        """
        DECLARE @result int;
        EXEC @result = sys.sp_getapplock @Resource = ?, @LockMode = 'Exclusive',
            @LockOwner = 'Session', @LockTimeout = 15000;
        SELECT @result;
        """,
        resource,
    ).fetchone()
    if result is None or result[0] < 0:
        raise MigrationError(f"Could not acquire MVP migration lock: {result}")
    try:
        yield
    finally:
        cursor.execute(
            "EXEC sys.sp_releaseapplock @Resource = ?, @LockOwner = 'Session'",
            resource,
        )


def _applied_versions(cursor, versions: tuple[SchemaVersion, ...]) -> int:
    if not _table_exists(cursor, "dbo.mvp_schema_versions"):
        return 0
    rows = cursor.execute(
        "SELECT version, artifact_name, sha256 FROM dbo.mvp_schema_versions"
    ).fetchall()
    applied = {row[0]: (row[1], row[2]) for row in rows}
    expected = {item.version for item in versions[:len(rows)]}
    if len(applied) != len(rows) or applied.keys() != expected:
        raise MigrationError("MVP ledger contains unknown versions or a sequence gap")
    if not applied:
        raise MigrationError("MVP ledger exists without a bootstrap entry")
    for item in versions[:len(rows)]:
        artifact, digest = applied[item.version]
        if artifact != item.path.name or digest.lower() != item.sha256:
            raise MigrationError(f"Ledger checksum or artifact mismatch for MVP {item.version}")
    return len(rows)


def _apply(cursor, version: SchemaVersion, batches: list[str]) -> None:
    for batch in batches:
        cursor.execute(batch)
    if version.creates_table and not _table_exists(cursor, version.creates_table):
        raise MigrationError(f"MVP {version.version} did not create {version.creates_table}")
    cursor.execute(
        """
        INSERT INTO dbo.mvp_schema_versions
            (version, artifact_name, sha256, applied_at, applied_by)
        VALUES (?, ?, ?, SYSUTCDATETIME(), SUSER_SNAME())
        """,
        version.version, version.path.name, version.sha256,
    )


def run(
    database: str,
    action: str,
    *,
    allow_main: bool = False,
    server: str | None = None,
    target: str | None = None,
    dry_run: bool = False,
) -> None:
    if action not in {"bootstrap", "status", "upgrade"}:
        raise ValueError(f"Unknown MVP migration action {action}")
    if action != "upgrade" and (dry_run or target is not None):
        raise ValueError("A target version and dry-run apply only to upgrade")
    read_only = action == "status" or dry_run
    if database.casefold() == MAIN_DATABASE.casefold():
        if not read_only and not allow_main:
            raise PermissionError("Writing CeylonGoDB requires explicit authorization")
    elif database not in ALLOWED_TARGETS:
        raise ValueError(f"Database {database!r} is outside the MVP target allowlist")

    server = (server or os.getenv("DB_SERVER", r".\SQLEXPRESS")).replace("\\\\", "\\")
    if any(char in server for char in ";{}"):
        raise ValueError("Invalid SQL Server address")
    versions = (BOOTSTRAP, *UPGRADES)
    positions = {item.version: index for index, item in enumerate(versions, start=1)}
    target = target or versions[-1].version
    if target not in positions:
        raise ValueError(f"Unknown MVP schema version {target}")
    batches = {item.version: item.batches() for item in versions}

    with closing(_connect(server, database, read_only=read_only)) as connection:
        connection.timeout = 30
        cursor = connection.cursor()
        _verify_identity(cursor, database, server)
        # Keep the lock across validation, DDL and commit so a second writer
        # rechecks the ledger only after the first one has committed.
        with (nullcontext() if read_only else _migration_lock(cursor)):
            try:
                applied = _applied_versions(cursor, versions)
                current = versions[applied - 1].version if applied else "uninitialized"
                print(f"Current MVP schema version: {current}")
                for index, item in enumerate(versions, start=1):
                    if item.creates_table:
                        exists = _table_exists(cursor, item.creates_table)
                        if exists != (index <= applied):
                            raise MigrationError(
                                f"Ledger and schema disagree for {item.creates_table}"
                            )

                if action == "status":
                    print("Pending:", ", ".join(v.version for v in versions[applied:]) or "none")
                    return
                if action == "bootstrap":
                    if not applied:
                        if cursor.execute(
                            "SELECT TOP (1) 1 FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')"
                        ).fetchone():
                            raise MigrationError("Fresh bootstrap requires an empty dbo schema")
                        _apply(cursor, BOOTSTRAP, batches[BOOTSTRAP.version])
                        connection.commit()
                        print(f"Applied MVP schema {BOOTSTRAP.version}")
                    return
                if not applied:
                    raise MigrationError("Initialize MVP 1.0.0 before upgrading")
                if positions[target] < applied:
                    raise MigrationError("MVP downgrades are unsupported")
                pending = versions[applied:positions[target]]
                print("Pending:", ", ".join(v.version for v in pending) or "none")
                if dry_run or not pending:
                    return
                for item in pending:
                    _apply(cursor, item, batches[item.version])
                connection.commit()
                print(f"Applied MVP schema {target}")
            except BaseException:
                if not read_only:
                    connection.rollback()
                raise


def bootstrap(target_db: str, allow_main: bool = False) -> None:
    """Keep the original callable used by the MVP cutover tooling."""
    run(target_db, "bootstrap", allow_main=allow_main)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database", nargs="?", default=os.getenv("DB_NAME", "CeylonGoDB_MVP_Rehearsal")
    )
    parser.add_argument(
        "action", nargs="?", choices=("bootstrap", "status", "upgrade"), default="bootstrap"
    )
    parser.add_argument("--server")
    parser.add_argument("--target")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Authorize an upgrade of CeylonGoDB")
    args = parser.parse_args(argv)
    if args.action != "upgrade" and (args.dry_run or args.target or args.yes):
        parser.error("--dry-run, --target and --yes apply only to upgrade")
    try:
        allow_main = (
            args.yes if args.action == "upgrade"
            else os.getenv("MVP_CUTOVER_AUTHORIZED", "false").lower() == "true"
        )
        run(
            args.database, args.action, allow_main=allow_main, server=args.server,
            target=args.target, dry_run=args.dry_run,
        )
    except (MigrationError, OSError, pyodbc.Error, ValueError) as exc:
        print(f"MVP migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
