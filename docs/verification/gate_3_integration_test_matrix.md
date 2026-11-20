# Gate 3: Integration & Concurrency Test Matrix

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Project**: `CeylonGo-Backend` (`develop` branch)  
**Target Suite**: `tests/test_eco_sqlserver_integration.py`  
**Target Engine**: Microsoft SQL Server 2025  
**Approval Owner**: QA Lead / Backend Team Leader  
**Status**: **PREPARED — PENDING ENVIRONMENT PROVISIONING (BLOCKED)**  

---

## 1. Executive Summary & Verification Baseline

This document provides the exhaustive specification of the SQL Server integration and concurrency test suite for Module 04 Eco-Tourism.

- **Current Regression Baseline**: **487 passed, 3 skipped, 0 failed**.
- **The 3 Skipped Tests**: `tests/test_eco_sqlserver_integration.py` skipped because `TEST_DISPOSABLE_SQLSERVER_URL` is unconfigured.
- **Verification Rule**: These tests are **PREPARED**, but must **NOT** be claimed as PASSED until executed against an authorized, provisioned disposable database.

---

## 2. Inventory of Existing SQL Server Integration Tests

The following three tests currently exist in `tests/test_eco_sqlserver_integration.py`:

### Test 1: `test_sqlserver_sp_getapplock_acquisition_and_release`
- **Purpose**: Verify that stored procedures `sp_getapplock` and `sp_releaseapplock` execute synchronously against the Microsoft SQL Server engine via SQLAlchemy, returning status code 0 (success) or 1 (lock granted after wait).
- **Required Tables**: None (operates entirely within the SQL Server application lock metadata subsystem).
- **Required Fixtures**: `sqlserver_db` (scoped SQLAlchemy `Session` bound to disposable SQL Server).
- **SQL Server-Specific Behavior**: Executes:
  ```sql
  DECLARE @res INT;
  EXEC @res = sp_getapplock @Resource = 'eco_slot_99991', @LockMode = 'Exclusive', @LockOwner = 'Transaction', @LockTimeout = 2000;
  SELECT @res AS result;
  ```
  Followed by `sp_releaseapplock`.
- **Expected Assertions**:
  ```python
  assert code in (0, 1)
  assert rel_code == 0
  ```
- **Required Migration State**: None (no schema tables involved).
- **Cleanup Behavior**: Explicit release in test body; fixture teardown executes `session.rollback()` and `session.close()`.
- **Dependencies**: Live SQL Server instance supporting `sp_getapplock`.
- **Possible Failure Conditions**: Network timeout, lack of execute permission on `sp_getapplock`, conflicting lock held by another orphaned connection.
- **Current Status**: **PREPARED / SKIPPED**.

---

### Test 2: `test_sqlserver_concurrent_applock_mutual_exclusion`
- **Purpose**: Verify that two concurrent operating system threads attempting to acquire an exclusive lock on the same eco slot (`eco_slot_99992`) are strictly mutually excluded by SQL Server.
- **Required Tables**: None.
- **Required Fixtures**: `sqlserver_engine` (independent connection pool for multi-threaded sessions).
- **SQL Server-Specific Behavior**:
  - Thread 1 acquires exclusive lock with 3000ms timeout and holds it.
  - Thread 2 attempts to acquire the same lock with 1000ms timeout.
  - SQL Server blocks Thread 2 until 1000ms expires, returning code `-1` (timeout).
  - `_handle_applock_result` catches `-1` and raises `EcoBookingServiceError(code="ECO_LOCK_TIMEOUT")`.
- **Expected Assertions**:
  ```python
  assert results["thread_2_timed_out"] is True
  ```
- **Required Migration State**: None.
- **Cleanup Behavior**: Thread 1 rolls back and closes session in `finally:`. Thread 2 rolls back and closes in `finally:`. Main thread joins both threads before exiting.
- **Dependencies**: Multithreaded execution against SQL Server connection pool (min pool size $\ge 2$).
- **Possible Failure Conditions**: Thread starvation, pool exhaustion, or timing jitter if Thread 1 fails to acquire before Thread 2 attempts.
- **Current Status**: **PREPARED / SKIPPED**.

---

### Test 3: `test_sqlserver_conditional_capacity_oversell_barrier`
- **Purpose**: Verify that an atomic conditional `UPDATE` query evaluates capacity bounds inside SQL Server's query engine, ensuring `rowcount == 0` when available capacity is insufficient.
- **Required Tables**: Temporary table `#test_slots` in SQL Server's `tempdb`.
- **Required Fixtures**: `sqlserver_db`.
- **SQL Server-Specific Behavior**:
  - Creates `#test_slots (id INT PRIMARY KEY, capacity INT, reserved_count INT, is_active BIT)` with initial values `(1, 5, 4, 1)` (only 1 seat remaining).
  - Attempts an atomic update for 2 seats:
    ```sql
    UPDATE #test_slots
    SET reserved_count = reserved_count + 2
    WHERE id = 1 AND is_active = 1 AND (capacity - reserved_count) >= 2;
    ```
- **Expected Assertions**:
  ```python
  assert res.rowcount == 0  # Atomic protection triggered, no rows updated!
  ```
- **Required Migration State**: None (isolated in `tempdb`).
- **Cleanup Behavior**: `DROP TABLE #test_slots` executed if existing; session close automatically discards connection-scoped temp tables.
- **Dependencies**: SQL Server arithmetic evaluation in UPDATE statements.
- **Possible Failure Conditions**: Syntax errors if executed on non-T-SQL dialect.
- **Current Status**: **PREPARED / SKIPPED**.

---

## 3. Concurrency & Transaction Coverage Matrix (12 Focus Areas)

| Area # | Operational Scenario | Existing Status | Missing Test Requirements | Prerequisite Gate |
|:---:|---|:---:|---|:---:|
| **1** | `sp_getapplock` acquisition & release | **Covered** (Test 1) | None. | None |
| **2** | Concurrent booking attempts on same slot | **Partially Covered** (Test 2) | End-to-end multi-threaded test with `execute_traveler_booking_create_atomic` competing for last seat. | Gates 1 & 2 |
| **3** | Capacity overselling prevention | **Partially Covered** (Test 3) | Service-level test against persistent table `dbo.activity_slots` triggering `ECO_CAPACITY_EXCEEDED`. | Gates 1 & 2 |
| **4** | Atomic booking & participant insertion | **Missing** | Test verifying simultaneous insert into `dbo.activity_bookings`, `dbo.activity_booking_participants`, and `dbo.booking_references`. | Gates 1 & 2 |
| **5** | Rollback on participant validation failure | **Missing** | Test verifying that an invalid participant (e.g. check constraint failure) completely rolls back the booking and slot reserved count. | Gates 1 & 2 |
| **6** | Rollback on booking reference failure | **Missing** | Test simulating reference duplication or unexpected failure, verifying slot `reserved_count` remains untouched. | Gates 1 & 2 |
| **7** | Slot reservation count consistency | **Missing** | Lifecycle test: slot count moves from $0 \to 2$ on booking, and $2 \to 0$ on cancellation. | Gates 1 & 2 |
| **8** | Concurrent booking cancellation | **Missing** | Two threads attempting to cancel the same booking; exactly one succeeds, second receives `ECO_BOOKING_ALREADY_CANCELLED`. | Gates 1 & 2 |
| **9** | Idempotent weather emergency cancellation | **Missing** | Invoking weather cancellation twice on the same experience: first run cancels and releases slots, second run is a clean no-op. | Gates 1 & 2 |
| **10** | Duplicate refund record prevention | **Missing** | Verifying `dbo.payment_refunds` unique constraints prevent duplicate refund entries for cancelled bookings. | Gates 1 & 2 |
| **11** | Seasonal restriction enforcement | **Missing** | Verifying slot creation/booking is rejected when slot date falls within an active `dbo.eco_season_restrictions` blackout. | Gate 2 |
| **12** | Module 03 activity booking compatibility | **Missing** | Verifying Module 03 activity slot (`activity_id IS NOT NULL, eco_experience_id IS NULL`) coexists with Module 04 slot under constraint `CK_activity_slots_parent_target`. | Gate 2 |

---

## 4. Proposed Specification for Missing Tests (Expansion Suite)

The following tests are fully designed and prepared to be added to `tests/test_eco_sqlserver_integration.py` once Gates 1 and 2 are signed off and the disposable database is provisioned.

### Test 4: `test_sqlserver_atomic_booking_creation_and_participant_persistence`
```python
def test_sqlserver_atomic_booking_creation_and_participant_persistence(sqlserver_db):
    """
    Verify full atomic transaction in SQL Server:
    1. Slot capacity incremented.
    2. ActivityBooking record created.
    3. ActivityBookingParticipant records persisted (with emergency contact and waiver fields).
    4. BookingReference record created with module_type='activity'.
    """
    # Requires Gate 1 and Gate 2
    # Rolls back in finally:
```

### Test 5: `test_sqlserver_transaction_rollback_on_participant_error`
```python
def test_sqlserver_transaction_rollback_on_participant_error(sqlserver_db):
    """
    Verify that if participant insertion fails (e.g., negative age violating CK_activity_booking_participants_age),
    the entire transaction is rolled back:
    - ActivityBooking is NOT persisted.
    - Slot reserved_count is NOT incremented.
    """
```

### Test 6: `test_sqlserver_concurrent_booking_race_condition_last_seat`
```python
def test_sqlserver_concurrent_booking_race_condition_last_seat(sqlserver_engine):
    """
    Race condition test:
    Slot has capacity=1, reserved_count=0.
    Thread A and Thread B simultaneously attempt to book 1 participant.
    Outcome:
    - Exactly one thread succeeds (HTTP 201 / booking confirmed).
    - Exactly one thread fails (ECO_CAPACITY_EXCEEDED or ECO_LOCK_TIMEOUT).
    - Slot reserved_count == 1 (never 2).
    """
```

### Test 7: `test_sqlserver_concurrent_booking_cancellation_race`
```python
def test_sqlserver_concurrent_booking_cancellation_race(sqlserver_engine):
    """
    Two threads attempt to cancel the same confirmed booking simultaneously.
    Outcome:
    - Exactly one thread transitions booking to 'cancelled' and releases capacity.
    - Second thread fails with ECO_BOOKING_ALREADY_CANCELLED.
    - Slot reserved_count decremented exactly once.
    """
```

### Test 8: `test_sqlserver_module_03_and_module_04_slot_coexistence`
```python
def test_sqlserver_module_03_and_module_04_slot_coexistence(sqlserver_db):
    """
    Verify Migration 030 XOR constraint CK_activity_slots_parent_target:
    - Insert Module 03 slot (activity_id=1, eco_experience_id=NULL) -> SUCCEEDS.
    - Insert Module 04 slot (activity_id=NULL, eco_experience_id=1) -> SUCCEEDS.
    - Insert invalid slot (activity_id=1, eco_experience_id=1) -> FAILS (CK violation).
    - Insert invalid slot (activity_id=NULL, eco_experience_id=NULL) -> FAILS (CK violation).
    """
```

---

## 5. Execution Preconditions & Safety Summary

- **Prerequisite 1**: Team Leader approval for Gate 1 (`docs/proposals/gate_1_shared_orm_approval_package.md`).
- **Prerequisite 2**: Database Coordinator approval for Gate 2 (`docs/proposals/gate_2_migration_030_approval_package.md`).
- **Prerequisite 3**: Provisioning of dedicated disposable database `CeylonGoDB_IntegrationTest`.
- **Zero-Write Guarantee**: No tests will be executed against `CeylonGoDB`.
