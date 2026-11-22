"""
Isolated Integration Tests for Module 04 Eco-Tourism on Microsoft SQL Server.
=============================================================================
Verifies sp_getapplock concurrency locks and atomic conditional updates.

STRICT SAFETY RESTRICTIONS:
- Enforces fail-closed opt-in: RUN_SQLSERVER_INTEGRATION_TESTS=1 AND
  ECO_SQLSERVER_INTEGRATION=1 (or TEST_DISPOSABLE_SQLSERVER_URL).
- Restricts target database strictly to 'ceylongodb_integrationtest' or an
  approved disposable test database URL.
- Live production database 'CeylonGoDB' is strictly rejected at all levels.
- Verifies actual connected server instance identity (machine + instance name).
- Scopes temporary structures strictly to session tempdb and cleans them up.
- Never mutates or deletes non-test database records.
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.database_guard import (
    ARTISAN_INTEGRATION_ALIAS,
    ARTISAN_INTEGRATION_DATABASE,
    DatabaseSafetyError,
    attach_runtime_database_guard,
    create_safe_sqlserver_test_engine,
    normalize_server_name,
    validate_disposable_sqlserver_url,
    verify_connected_server_identity,
    verify_safe_integration_database,
    verify_safe_integration_target,
)
from app.database.connection import DATABASE_URL
from app.services.eco_booking_service import acquire_slot_lock, release_slot_lock

APPROVED_DB_NAME = "ceylongodb_integrationtest"
APPROVED_DATABASE_SET = frozenset({APPROVED_DB_NAME})


def is_sqlserver_opted_in() -> bool:
    """Fail-closed check requiring both global and eco-specific opt-ins."""
    return (
        os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS") == "1"
        and (
            os.getenv("ECO_SQLSERVER_INTEGRATION") == "1"
            or os.getenv("TEST_DISPOSABLE_SQLSERVER_URL") is not None
        )
    )


pytestmark = pytest.mark.skipif(
    not is_sqlserver_opted_in(),
    reason=(
        "Requires explicit opt-in (RUN_SQLSERVER_INTEGRATION_TESTS=1 and "
        "ECO_SQLSERVER_INTEGRATION=1 or TEST_DISPOSABLE_SQLSERVER_URL) "
        "pointing to an isolated test database"
    ),
)


def verify_target(connection, expected_db: str | None = None) -> None:
    """
    Fail-closed runtime verification:
    - Verifies current DB name is strictly allowlisted.
    - Verifies live production CeylonGoDB is strictly rejected.
    - Verifies connected server instance identity (machine + instance name).
    - Sets lock timeout (6000ms) to prevent indefinite hangs.
    """
    target = (expected_db or APPROVED_DB_NAME).strip().lower()
    db_name = connection.scalar(text("SELECT DB_NAME()"))
    verify_safe_integration_database(
        db_name, approved_databases=frozenset({target})
    )
    verify_connected_server_identity(
        connection, server_alias=connection.engine.url.host
    )
    connection.exec_driver_sql("SET LOCK_TIMEOUT 6000")


@pytest.fixture(scope="module")
def sqlserver_engine():
    """Create a guarded engine for disposable or integration SQL Server."""
    raw_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if raw_url and raw_url.strip():
        target_db = validate_disposable_sqlserver_url(
            raw_url, approved_databases=APPROVED_DATABASE_SET
        )
        engine = create_safe_sqlserver_test_engine(
            raw_url,
            approved_databases=APPROVED_DATABASE_SET,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        expected_db = target_db
    else:
        server = os.getenv(
            "ECO_SQLSERVER_TEST_SERVER", ARTISAN_INTEGRATION_ALIAS
        )
        database = ARTISAN_INTEGRATION_DATABASE
        opt_in = os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS")

        verify_safe_integration_target(server, database, opt_in)
        if normalize_server_name(server) != ARTISAN_INTEGRATION_ALIAS.lower():
            raise DatabaseSafetyError(
                "Eco SQL Server integration tests require the approved local "
                f"alias '{ARTISAN_INTEGRATION_ALIAS}'."
            )

        db_url = DATABASE_URL.set(host=server, database=database)
        engine = create_engine(
            db_url,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        attach_runtime_database_guard(
            engine,
            approved_databases=APPROVED_DATABASE_SET,
            expected_db=database,
            server_alias=server,
        )
        expected_db = database

    try:
        with engine.connect() as conn:
            verify_target(conn, expected_db=expected_db)
    except Exception:
        engine.dispose()
        raise

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def sqlserver_db(sqlserver_engine):
    """Provide a scoped Session for disposable SQL Server tests."""
    with Session(
        sqlserver_engine, autoflush=False, expire_on_commit=False
    ) as session:
        verify_target(session.connection())
        try:
            yield session
        finally:
            session.rollback()


def test_sqlserver_sp_getapplock_acquisition_and_release(sqlserver_db):
    """
    Verify sp_getapplock and sp_releaseapplock execution against real SQL Server engine.
    Confirms return code 0 (synchronous grant) and release code 0.
    """
    slot_id = 99991
    code = acquire_slot_lock(sqlserver_db, slot_id=slot_id, timeout_ms=2000)
    assert code in (0, 1)

    rel_code = release_slot_lock(sqlserver_db, slot_id=slot_id)
    assert rel_code == 0


def test_sqlserver_concurrent_applock_mutual_exclusion(sqlserver_engine):
    """
    Verify that two concurrent transactions trying to lock the same eco slot
    are mutually excluded by sp_getapplock.
    """
    slot_id = 99992
    results = {"thread_2_timed_out": False}

    def worker_1(started_evt, release_evt):
        with Session(
            sqlserver_engine, autoflush=False, expire_on_commit=False
        ) as session:
            verify_target(session.connection())
            try:
                acquire_slot_lock(session, slot_id, timeout_ms=3000)
                started_evt.set()
                release_evt.wait(timeout=5)
            finally:
                session.rollback()

    def worker_2(started_evt):
        started_evt.wait(timeout=5)
        with Session(
            sqlserver_engine, autoflush=False, expire_on_commit=False
        ) as session:
            verify_target(session.connection())
            try:
                # Should time out because worker 1 holds the lock
                acquire_slot_lock(session, slot_id, timeout_ms=1000)
            except Exception as e:
                if "ECO_LOCK_TIMEOUT" in str(e) or "timed out" in str(e).lower():
                    results["thread_2_timed_out"] = True
            finally:
                session.rollback()

    evt_started = threading.Event()
    evt_release = threading.Event()

    t1 = threading.Thread(target=worker_1, args=(evt_started, evt_release))
    t2 = threading.Thread(target=worker_2, args=(evt_started,))

    t1.start()
    t2.start()

    t2.join(timeout=6)
    evt_release.set()
    t1.join(timeout=6)

    assert results["thread_2_timed_out"] is True


def test_sqlserver_conditional_capacity_oversell_barrier(sqlserver_db):
    """
    Verify atomic conditional update against SQL Server:
    UPDATE activity_slots SET reserved_count = reserved_count + :p
    WHERE id = :slot_id AND is_active = 1 AND (capacity - reserved_count) >= :p
    Verifies rowcount == 0 prevents overbooking.
    """
    # Create temporary slot in session tempdb
    try:
        sqlserver_db.execute(
            text(
                "IF OBJECT_ID('tempdb..#test_slots') IS NOT NULL "
                "DROP TABLE #test_slots; "
                "CREATE TABLE #test_slots "
                "(id INT PRIMARY KEY, capacity INT, "
                "reserved_count INT, is_active BIT); "
                "INSERT INTO #test_slots VALUES (1, 5, 4, 1);"
            )
        )

        # Attempt to reserve 2 places when only 1 is available
        res = sqlserver_db.execute(
            text(
                "UPDATE #test_slots "
                "SET reserved_count = reserved_count + 2 "
                "WHERE id = 1 AND is_active = 1 "
                "AND (capacity - reserved_count) >= 2;"
            )
        )
        assert res.rowcount == 0  # Atomic protection triggered!
    finally:
        sqlserver_db.execute(
            text(
                "IF OBJECT_ID('tempdb..#test_slots') IS NOT NULL "
                "DROP TABLE #test_slots;"
            )
        )
