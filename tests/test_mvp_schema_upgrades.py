import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import bootstrap_mvp_schema as runner


class IsolatedConnection:
    """Exercise runner transactions with SQLite and adapt SQL Server catalog calls."""

    def __init__(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        self.result = None
        self.batches = []
        self.fail_ledger = False
        self.read_only = False
        self.locks = 0
        self.server = "TESTHOST\\SQLEXPRESS"

    def cursor(self):
        return self

    def execute(self, sql, *params):
        sql = sql.strip()
        if sql.startswith("SELECT DB_NAME()"):
            self.result = self.db.execute("SELECT 'CeylonGoDB_Test', ?", (self.server,))
        elif sql.startswith("SELECT OBJECT_ID"):
            self.result = self.db.execute(
                "SELECT (SELECT name FROM sqlite_master WHERE type='table' AND name=?)",
                (params[0].removeprefix("dbo."),),
            )
        elif sql.startswith("SELECT TOP (1)"):
            self.result = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'")
        elif sql.startswith("SELECT version, artifact_name, sha256"):
            self.result = self.db.execute(
                "SELECT version, artifact_name, sha256 FROM mvp_schema_versions"
            )
        else:
            assert not self.read_only and self.db.in_transaction and self.locks == 1
            if sql.startswith("INSERT INTO dbo.mvp_schema_versions"):
                self.db.execute(
                    "INSERT INTO mvp_schema_versions VALUES (?, ?, ?)", params,
                )
                if self.fail_ledger:
                    raise sqlite3.OperationalError("injected failure after ledger insert")
            else:
                self.batches.append(sql)
                # Execute the 044 CREATE TABLE in SQLite with dialect syntax adapted.
                match = re.search(r"CREATE TABLE dbo\.(\w+)\s*\((.*?)\);", sql, re.S)
                if match and match[1] == "traveler_email_challenges":
                    ddl = match[2].replace(" COLLATE Latin1_General_100_BIN2", "")
                    self.db.execute(f"CREATE TABLE traveler_email_challenges ({ddl})")
                elif match and match[1] == "mvp_schema_versions":
                    self.db.execute(
                        "CREATE TABLE mvp_schema_versions ("
                        "version TEXT PRIMARY KEY, artifact_name TEXT UNIQUE, sha256 TEXT)"
                    )
        return self

    def fetchone(self):
        return self.result.fetchone()

    def fetchall(self):
        return self.result.fetchall()

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        # Keep the isolated backing store alive for assertions and subsequent runs.
        self.db.rollback()


@pytest.fixture
def isolated(monkeypatch):
    connection = IsolatedConnection()
    monkeypatch.setattr(runner, "UPGRADES", runner.UPGRADES[:1])

    def connect(_server, _database, *, read_only):
        connection.read_only = read_only
        return connection

    @contextmanager
    def lock(_connection):
        assert connection.locks == 0
        connection.locks += 1
        connection.db.execute("BEGIN")
        try:
            yield
        finally:
            connection.locks -= 1

    monkeypatch.setenv("DB_SERVER", connection.server)
    monkeypatch.setattr(runner, "_connect", connect)
    monkeypatch.setattr(runner, "_migration_lock", lock)
    yield connection
    connection.db.close()


def versions(db):
    return db.execute("SELECT version FROM mvp_schema_versions ORDER BY version").fetchall()


def test_fresh_bootstrap_and_existing_bootstrap_compatibility(isolated):
    runner.bootstrap("CeylonGoDB_Test")
    assert versions(isolated.db) == [("1.0.0",)]
    assert isolated.batches == runner.BOOTSTRAP.batches()
    runner.bootstrap("CeylonGoDB_Test")
    assert isolated.batches == runner.BOOTSTRAP.batches()
    assert isolated.db.execute("SELECT sha256 FROM mvp_schema_versions").fetchone() == (
        runner.BOOTSTRAP.sha256,
    )


def test_incremental_upgrade_and_repeat_safety(isolated):
    runner.bootstrap("CeylonGoDB_Test")
    isolated.batches.clear()
    runner.run("CeylonGoDB_Test", "upgrade", target="1.0.1")
    assert versions(isolated.db) == [("1.0.0",), ("1.0.1",)]
    assert isolated.batches == runner.UPGRADES[0].batches()
    columns = isolated.db.execute("PRAGMA table_info(traveler_email_challenges)").fetchall()
    assert [row[1] for row in columns] == [
        "email", "registration_hash", "otp_hash", "full_name", "phone",
        "password_hash", "sent_at", "attempts",
    ]
    assert columns[0][5] == 1
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        isolated.db.execute(
            "INSERT INTO traveler_email_challenges (email,sent_at,attempts) VALUES ('a','x',6)"
        )
    runner.run("CeylonGoDB_Test", "upgrade")
    assert isolated.batches == runner.UPGRADES[0].batches()
    assert versions(isolated.db) == [("1.0.0",), ("1.0.1",)]


def test_rolls_back_table_and_ledger_on_failure(isolated):
    runner.bootstrap("CeylonGoDB_Test")
    isolated.fail_ledger = True
    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        runner.run("CeylonGoDB_Test", "upgrade")
    assert versions(isolated.db) == [("1.0.0",)]
    assert not runner._table_exists(isolated, runner.OTP_TABLE)
    isolated.fail_ledger = False
    runner.run("CeylonGoDB_Test", "upgrade")
    assert versions(isolated.db)[-1] == ("1.0.1",)


def test_rejects_ledger_checksum_mismatch(isolated):
    runner.bootstrap("CeylonGoDB_Test")
    isolated.db.execute("UPDATE mvp_schema_versions SET sha256 = 'tampered'")
    with pytest.raises(runner.MigrationError, match="Ledger checksum"):
        runner.run("CeylonGoDB_Test", "upgrade")
    assert not runner._table_exists(isolated, runner.OTP_TABLE)


def test_rejects_local_checksum_mismatch():
    wrong_file = runner.SchemaVersion("1.0.1", Path(__file__), runner.UPGRADES[0].sha256)
    with pytest.raises(runner.MigrationError, match="File checksum"):
        wrong_file.batches()


def test_status_and_dry_run_issue_no_writes(isolated, capsys):
    runner.bootstrap("CeylonGoDB_Test")
    isolated.batches.clear()
    runner.run("CeylonGoDB_Test", "status")
    runner.run("CeylonGoDB_Test", "upgrade", dry_run=True)
    assert capsys.readouterr().out.count("Pending: 1.0.1") == 2
    assert isolated.batches == []
    assert versions(isolated.db) == [("1.0.0",)]


def test_rejects_unexpected_sql_server(isolated, monkeypatch):
    monkeypatch.setenv("DB_SERVER", "OTHERHOST\\SQLEXPRESS")
    with pytest.raises(runner.MigrationError, match="Unexpected SQL target"):
        runner.bootstrap("CeylonGoDB_Test")
    assert isolated.batches == []


def test_lock_contention_stops_before_migration():
    cursor = MagicMock()
    cursor.execute.return_value.fetchone.return_value = (-1,)
    with pytest.raises(runner.MigrationError, match="Could not acquire"):
        with runner._migration_lock(cursor):
            pytest.fail("Migration entered without acquiring the lock")
    cursor.execute.assert_called_once()


def test_main_upgrade_requires_explicit_approval(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(runner, "_connect", connect)
    assert runner.main(["CeylonGoDB", "upgrade", "--target", "1.0.1"]) == 1
    connect.assert_not_called()
