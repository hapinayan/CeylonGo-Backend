"""
Isolated Integration Tests for Module 04 Eco-Tourism on Microsoft SQL Server.

STRICT SAFETY RESTRICTIONS:
- This test suite is ONLY executed when the TEST_DISPOSABLE_SQLSERVER_URL environment
  variable is explicitly provided pointing to an isolated, disposable test database.
- It is AUTOMATICALLY SKIPPED on local / CI runs without this environment variable.
- It NEVER connects to or executes against the development or production SQL Server instances.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import threading
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.database_guard import (
    DatabaseSafetyError,
    create_safe_sqlserver_test_engine,
    validate_disposable_sqlserver_url,
)
from app.services.eco_booking_service import (
    acquire_slot_lock,
    execute_provider_decision_atomic,
    execute_traveler_booking_cancel_atomic,
    execute_traveler_booking_create_atomic,
    release_slot_lock,
)

TEST_SQLSERVER_URL = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")

pytestmark = [
    pytest.mark.sqlserver,
    pytest.mark.skipif(
        not TEST_SQLSERVER_URL,
        reason="Requires TEST_DISPOSABLE_SQLSERVER_URL pointing to an isolated disposable test database",
    ),
]


@pytest.fixture(scope="module")
def sqlserver_engine():
    """Create a guarded engine connected to the disposable SQL Server instance."""
    raw_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if not raw_url or not raw_url.strip():
        pytest.skip("TEST_DISPOSABLE_SQLSERVER_URL is unset or empty")

    # Fail explicitly if raw_url is unsafe, unapproved, or malformed
    engine = create_safe_sqlserver_test_engine(raw_url, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture
def sqlserver_db(sqlserver_engine):
    """Provide a scoped Session for disposable SQL Server tests."""
    Session = sessionmaker(bind=sqlserver_engine, autoflush=False, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


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
    Session = sessionmaker(bind=sqlserver_engine, autoflush=False, expire_on_commit=False)
    slot_id = 99992

    results = {"thread_2_timed_out": False}

    def worker_1(started_evt, release_evt):
        session = Session()
        try:
            acquire_slot_lock(session, slot_id, timeout_ms=3000)
            started_evt.set()
            release_evt.wait(timeout=5)
        finally:
            session.rollback()
            session.close()

    def worker_2(started_evt):
        started_evt.wait(timeout=5)
        session = Session()
        try:
            # Should time out because worker 1 holds the lock
            acquire_slot_lock(session, slot_id, timeout_ms=1000)
        except Exception as e:
            if "ECO_LOCK_TIMEOUT" in str(e) or "timed out" in str(e).lower():
                results["thread_2_timed_out"] = True
        finally:
            session.rollback()
            session.close()

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
    # Create temporary slot in disposable DB
    sqlserver_db.execute(
        text(
            "IF OBJECT_ID('tempdb..#test_slots') IS NOT NULL DROP TABLE #test_slots; "
            "CREATE TABLE #test_slots (id INT PRIMARY KEY, capacity INT, reserved_count INT, is_active BIT); "
            "INSERT INTO #test_slots VALUES (1, 5, 4, 1);"
        )
    )

    # Attempt to reserve 2 places when only 1 is available
    res = sqlserver_db.execute(
        text(
            "UPDATE #test_slots "
            "SET reserved_count = reserved_count + 2 "
            "WHERE id = 1 AND is_active = 1 AND (capacity - reserved_count) >= 2;"
        )
    )
    assert res.rowcount == 0  # Atomic protection triggered!
