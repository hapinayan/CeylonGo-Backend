"""Test-only SQLite table tracing for the Academic MVP profile."""
import sqlite3
from collections import defaultdict
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

PROFILE = (
    Path(__file__).resolve().parents[1]
    / "docs/academic-mvp/database-simplification/DB01_PROPOSED_MVP_SCHEMA.md"
)
ACTIONS = {
    sqlite3.SQLITE_READ: "read",
    sqlite3.SQLITE_INSERT: "insert",
    sqlite3.SQLITE_UPDATE: "update",
    sqlite3.SQLITE_DELETE: "delete",
}


def retained_tables():
    return {
        table.strip()
        for row in PROFILE.read_text(encoding="utf-8").splitlines()
        if row.startswith("| ") and " | " in row and row.split("|")[2].strip()
        and row.split("|")[1].strip() != "Workflow"
        for table in row.split("|")[2].split(",")
    }


class SQLiteTableTrace:
    """Record statements executed during a test call, excluding fixture setup."""

    def __init__(self):
        self.access = defaultdict(set)
        self.statements = 0

    def authorize(self, action, arg1, _arg2, _database, _trigger):
        if action in ACTIONS and arg1 and not arg1.startswith("sqlite_"):
            self.access[arg1].add(ACTIONS[action])
        return sqlite3.SQLITE_OK

    def before(self, connection, *_args):
        driver = connection.connection.driver_connection
        if not isinstance(driver, sqlite3.Connection):
            raise AssertionError("DB-02 permits only SQLite execution")
        self.statements += 1
        driver.set_authorizer(self.authorize)

    @staticmethod
    def clear(connection, *_args):
        driver = connection.connection.driver_connection
        if isinstance(driver, sqlite3.Connection):
            driver.set_authorizer(None)

    def on_error(self, context):
        if context.connection is not None:
            self.clear(context.connection)

    def __enter__(self):
        event.listen(Engine, "before_cursor_execute", self.before)
        event.listen(Engine, "after_cursor_execute", self.clear)
        event.listen(Engine, "handle_error", self.on_error)
        return self

    def __exit__(self, *_args):
        event.remove(Engine, "before_cursor_execute", self.before)
        event.remove(Engine, "after_cursor_execute", self.clear)
        event.remove(Engine, "handle_error", self.on_error)

    def require_profile(self, allowed):
        unexpected = self.access.keys() - allowed
        assert not unexpected, f"Unexpected table access: {', '.join(sorted(unexpected))}"


def test_db01_profile_has_49_mapped_tables_and_fk_closure():
    import app.models  # noqa: F401
    from app.models.user import Base
    from sqlalchemy.orm import configure_mappers

    configure_mappers()
    mapped = {mapper.local_table.name for mapper in Base.registry.mappers}
    retained = retained_tables()
    assert len(mapped) == len(Base.metadata.tables) == 99
    assert len(retained) == 49
    assert retained <= mapped
    for table in retained:
        assert {fk.column.table.name for fk in Base.metadata.tables[table].foreign_keys} <= retained


def test_trace_detects_unexpected_raw_sql_join_and_dml():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE wishlists (user_id INTEGER)")
            with SQLiteTableTrace() as trace:
                connection.exec_driver_sql("INSERT INTO wishlists VALUES (1)")
                connection.exec_driver_sql("SELECT u.id FROM users u JOIN wishlists w ON w.user_id=u.id")
                connection.exec_driver_sql("SELECT u.id FROM users u JOIN wishlists w ON w.user_id=u.id")
            assert trace.statements == 3
            assert trace.access == {"users": {"read"}, "wishlists": {"read", "insert"}}
            with pytest.raises(AssertionError, match="Unexpected table access: wishlists"):
                trace.require_profile(retained_tables())
    finally:
        engine.dispose()
