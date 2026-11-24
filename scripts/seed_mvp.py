"""Insert synthetic demo data into an explicitly configured disposable database.

Run from the repository root with python -m scripts.seed_mvp --help.
Provisioning and schema installation are separate operations.
"""

import argparse
from datetime import date
from hashlib import sha256
import os
from pathlib import Path
import re
import sys

from sqlalchemy import URL, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from app.models.mvp import MVPBase, MVPUser
from scripts.mvp_seed_data import populate

# This pair is reserved for the seeder on a dedicated local SQL Server instance.
APPROVED_TARGET = (r"localhost\CEYLONGOSEEDTEST", "CeylonGoDB_Academic_MVP_Seeder_Test")
DDL = Path(__file__).with_name("bootstrap_mvp_schema.sql")


class SeedError(RuntimeError):
    pass


def validate_target(server, database):
    normalized_server = re.sub(r"\\+", lambda _: "\\", server)
    if (normalized_server, database) != APPROVED_TARGET:
        raise SeedError("Database target is outside the dedicated seeder allowlist.")
    return normalized_server


def verify_sql_server(session, database, login, instance_identity):
    actual = session.execute(text("""
        SELECT DB_NAME(), ORIGINAL_LOGIN(), SUSER_SNAME(),
               CAST(SERVERPROPERTY('ServerName') AS nvarchar(128)),
               COALESCE(IS_SRVROLEMEMBER('sysadmin'), -1),
               COALESCE(IS_MEMBER('db_owner'), -1),
               COALESCE(HAS_PERMS_BY_NAME(NULL, NULL, 'CONTROL SERVER'), -1),
               COALESCE(HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL'), -1),
               COALESCE(HAS_PERMS_BY_NAME('dbo', 'SCHEMA', 'ALTER'), -1),
               COALESCE(HAS_PERMS_BY_NAME('dbo', 'SCHEMA', 'VIEW DEFINITION'), -1)
    """)).one()
    if tuple(actual) != (database, login, login, instance_identity, 0, 0, 0, 0, 0, 1):
        raise SeedError("Connected database, instance or principal failed verification.")

    # Use the same transaction for the lock, schema checks and all demo writes.
    result = session.execute(text("""
        DECLARE @result int;
        EXEC @result = sys.sp_getapplock
          @Resource='CeylonGo Academic MVP seeder', @LockMode='Exclusive',
          @LockOwner='Transaction', @LockTimeout=10000;
        SELECT @result;
    """)).scalar_one()
    if result < 0:
        raise SeedError("Unable to acquire the dedicated database seed lock.")

    catalog = inspect(session.connection())
    if set(catalog.get_table_names(schema="dbo")) != set(MVPBase.metadata.tables):
        raise SeedError("Target table set does not match the Academic MVP.")
    for table in MVPBase.metadata.sorted_tables:
        columns = catalog.get_columns(table.name, schema="dbo")
        if {column["name"] for column in columns} != set(table.columns.keys()):
            raise SeedError(f"Unexpected column set for {table.name}.")

    ddl = DDL.read_text(encoding="utf-8")
    required_objects = set(re.findall(r"\bCONSTRAINT\s+(\w+)", ddl, re.I))
    required_objects.update(re.findall(r"\bINDEX\s+(\w+)", ddl, re.I))
    actual_objects = set(session.execute(text("""
        SELECT o.name FROM sys.objects o
        JOIN sys.tables t ON t.object_id = o.parent_object_id
        WHERE SCHEMA_NAME(t.schema_id) = 'dbo'
          AND o.type IN ('PK', 'F', 'UQ', 'C', 'D')
        UNION
        SELECT i.name FROM sys.indexes i
        JOIN sys.tables t ON t.object_id = i.object_id
        WHERE SCHEMA_NAME(t.schema_id) = 'dbo' AND i.is_disabled = 0
    """)).scalars())
    if required_objects - actual_objects:
        raise SeedError("Target is missing expected schema constraints or indexes.")
    inactive_constraints = session.execute(text("""
        SELECT name FROM sys.foreign_keys WHERE is_disabled = 1 OR is_not_trusted = 1
        UNION ALL
        SELECT name FROM sys.check_constraints WHERE is_disabled = 1 OR is_not_trusted = 1
    """)).scalars()
    if required_objects.intersection(inactive_constraints):
        raise SeedError("Target has disabled or untrusted MVP constraints.")
    if session.execute(text("""
        SELECT TOP (1) tr.name FROM sys.triggers tr
        JOIN sys.tables t ON t.object_id = tr.parent_id
        WHERE SCHEMA_NAME(t.schema_id) = 'dbo' AND tr.is_disabled = 0
    """)).first():
        raise SeedError("Unexpected enabled DML trigger in the MVP schema.")
    ledger = session.execute(text("""
        SELECT version, artifact_name, sha256 FROM dbo.mvp_schema_versions
    """)).all()
    expected = ("1.0.0", DDL.name, sha256(ddl.encode("utf-8")).hexdigest())
    # Future versions require explicit admission backed by definition-level
    # parity and fixture verification. A matching baseline row alone is insufficient.
    if [tuple(row) for row in ledger] != [expected]:
        raise SeedError("Target schema ledger does not match the bootstrap artifact.")


def write_dataset(session, anchor, password_factory):
    """Insert or verify rows inside the transaction owned by the caller."""
    counts = {"inserted": 0, "verified": 0}

    def put(model, identity, *, on_insert=None, **values):
        query = select(model)
        if model is MVPUser:
            query = query.where(func.lower(model.email) == identity["email"])
        else:
            query = query.filter_by(**identity)
        query = query.limit(2)
        matches = session.scalars(query).all()
        if len(matches) > 1:
            raise SeedError(f"Ambiguous fixture identity in {model.__tablename__}.")
        if matches:
            row = matches[0]
            # Credential changes also update the account's modification time.
            compared = values.keys() - {"updated_at"} if model is MVPUser else values
            if any(getattr(row, key) != values[key] for key in compared):
                raise SeedError(f"Fixture collision or drift in {model.__tablename__}.")
            if model is MVPUser and (row.email != identity["email"] or not row.is_active):
                raise SeedError("A matching demo account has email or activation drift.")
            counts["verified"] += 1
        else:
            extra = on_insert() if on_insert else {}
            row = model(**identity, **values, **extra)
            session.add(row)
            session.flush()
            counts["inserted"] += 1
        return getattr(row, "id", None)

    populate(put, anchor, password_factory)
    return counts


def run_sql_server(server, database, login, db_password, instance_identity, anchor, password_factory):
    server = validate_target(server, database)
    if not login or not db_password or not instance_identity:
        raise SeedError("Explicit SQL credentials and expected instance identity are required.")
    engine = create_engine(
        URL.create(
            "mssql+pyodbc", username=login, password=db_password,
            host=server, database=database,
            query={"driver": "ODBC Driver 18 for SQL Server", "Encrypt": "yes",
                   "TrustServerCertificate": "yes"},
        ),
        hide_parameters=True,
    )
    try:
        with Session(engine.execution_options(schema_translate_map={None: "dbo"})) as session:
            with session.begin():
                verify_sql_server(session, database, login, instance_identity)
                result = write_dataset(session, anchor, password_factory)
            return result
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help=r"Reserved instance localhost\CEYLONGOSEEDTEST")
    parser.add_argument("--database", required=True, help=f"Reserved database {APPROVED_TARGET[1]}")
    parser.add_argument("--instance-identity", required=True, help="Expected SQL Server ServerName")
    parser.add_argument("--anchor", required=True, type=date.fromisoformat, help="Stable UTC scenario date")
    args = parser.parse_args(argv)

    def demo_hash(role):
        password = os.environ.get(f"MVP_SEED_{role.upper()}_PASSWORD")
        if not password or len(password) < 12:
            raise SeedError(f"Set MVP_SEED_{role.upper()}_PASSWORD to at least 12 characters.")
        from app.core.security import hash_password

        return hash_password(password)

    try:
        result = run_sql_server(
            args.server, args.database, os.environ.get("MVP_SEED_DB_USER"),
            os.environ.get("MVP_SEED_DB_PASSWORD"), args.instance_identity, args.anchor, demo_hash,
        )
    except SeedError as exc:
        print(f"Seeder stopped. {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        # Driver diagnostics may contain sensitive connection or parameter data.
        print(f"Seeder failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(f"Seed committed. Inserted {result['inserted']}, verified {result['verified']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
