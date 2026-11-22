# Gate 3 Readiness: Disposable SQL Server Integration & Concurrency Environment

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Project**: `CeylonGo-Backend` (`develop` branch)  
**Scope**: Technical Environment Audit & Safety Architecture for Gate 3 Integration Testing  
**Approval Owner**: QA Lead / Database Coordinator  
**Status**: **PREPARATION COMPLETE — EXECUTION BLOCKED (Awaiting Provisioning & Gates 1/2 Sign-off)**  

---

## 1. Executive Summary

Gate 3 represents the live SQL Server integration and concurrency verification stage for CeylonGo Module 04 (Eco-Tourism). Its primary purpose is to prove that:
1. SQL Server application locking (`sp_getapplock` / `sp_releaseapplock`) guarantees strict serializability and mutual exclusion during booking reservations.
2. Capacity overselling is mathematically prevented at the database level under concurrent load.
3. Multi-table booking operations (`dbo.activity_slots`, `dbo.activity_bookings`, `dbo.activity_booking_participants`, `dbo.booking_references`, `dbo.payment_refunds`) execute with strict transaction atomicity and rollback integrity.
4. Shared Module 03 activity scheduling coexists without constraint collisions.

**Critical Safety Finding**:
This phase is **PREPARATION ONLY**. No disposable database is currently provisioned, `TEST_DISPOSABLE_SQLSERVER_URL` is unset, and zero writes have been executed against `CeylonGoDB`.

---

## 2. Environment Readiness Audit (10-Point Evaluation)

| # | Audit Item | Findings & Live Evidence | Safety Status |
|:---:|---|---|:---:|
| **1** | **How `TEST_DISPOSABLE_SQLSERVER_URL` is loaded** | Loaded in `tests/test_eco_sqlserver_integration.py` line 27 via `os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")`. If empty or unset, the entire suite is skipped at module level via `pytest.mark.skipif`. | **VERIFIED (Fail-Safe)** |
| **2** | **Target SQL Server Database** | Determined dynamically from the connection string in `TEST_DISPOSABLE_SQLSERVER_URL`. Parsed via SQLAlchemy URL structure (`mssql+pyodbc://.../<database_name>`). | **VERIFIED** |
| **3** | **Risk of accidental connection to `CeylonGoDB`** | **Remediated & Verified in Phase 5J**: `app/core/database_guard.py` enforces dual-layer protection (`validate_disposable_sqlserver_url` + `attach_runtime_database_guard`). `tests/test_eco_sqlserver_integration.py` wired to `create_safe_sqlserver_test_engine`. Verified by 18 unit tests in `tests/test_eco_sqlserver_test_safety.py`. Unsafe URLs fail before engine creation. | **IMPLEMENTED & VERIFIED** |
| **4** | **Fixture table creation / dropping behavior** | Existing fixtures do **not** create or drop persistent database tables. `test_sqlserver_conditional_capacity_oversell_barrier` creates a session-scoped temporary table `#test_slots` in `tempdb`, which vanishes on connection close. | **VERIFIED (Zero Mutation)** |
| **5** | **Transaction commit vs rollback behavior** | Fixtures and tests strictly enforce `session.rollback()` in `finally:` blocks. No test commits transactions to persistent tables. | **VERIFIED (Non-Persistent)** |
| **6** | **Guaranteed cleanup after failures** | Both single-threaded and multi-threaded worker tests wrap execution in `try ... finally: session.rollback(); session.close()`. Temp tables in `tempdb` are cleaned up automatically upon session termination. | **VERIFIED** |
| **7** | **Migration 030 requirement** | The 3 existing skipped tests operate on virtual locks and `tempdb`, not requiring Migration 030. However, **end-to-end booking tests** inserting slots with `eco_experience_id` and `activity_id = NULL` **strictly require Migration 030** to avoid SQL Server Error 515 (Cannot insert NULL into `activity_id`). | **GATE 2 PREREQUISITE** |
| **8** | **Shared ORM patch requirement** | The 3 existing tests do not touch participant entities. However, any end-to-end test querying `booking.participants_list` or instantiating `ActivityBookingParticipant` **strictly requires Gate 1 ORM patch** to avoid SQL Server Error 207 (Invalid column name `'id_or_passport'`). | **GATE 1 PREREQUISITE** |
| **9** | **Seed data requirements** | The 3 existing tests require zero seed data. Comprehensive booking and cancellation integration tests require baseline seed entities: active provider user (`dbo.users`), provider profile (`dbo.provider_profiles`), published Eco experience (`dbo.eco_experiences`), and future slots (`dbo.activity_slots`). | **PREREQUISITE FOR STEP F** |
| **10** | **Isolation of test environment** | Currently 100% isolated because tests are skipped when the variable is unset. For active testing, physical isolation requires a dedicated, ephemeral database instance. | **VERIFIED** |

---

## 3. Test Database Safety Design & Guard Architecture

### A. Core Safety Principles
1. **Dedicated Disposable Database Only**: Tests may only target a dedicated test database (e.g. `CeylonGoDB_IntegrationTest` or `CeylonGoDB_Disposable_QA`).
2. **Absolute Prohibition of Protected Databases**: Execution against `CeylonGoDB`, `master`, `tempdb`, `model`, `msdb`, or any production/staging database is strictly blocked.
3. **Fail-Closed Target Identity Verification**:
   Before executing any query, the test harness must perform two layers of validation:
   - **Static URL Inspection**: Parse the database name from `TEST_DISPOSABLE_SQLSERVER_URL` and assert it is in the allowlist.
   - **Runtime Live Query Verification**: Execute `SELECT DB_NAME() AS current_db, @@SERVERNAME AS current_server;` on every checkout and verify `current_db` matches the approved integration database.
4. **No Accidental Fallback to `DATABASE_URL`**:
   The test harness must never fall back to `app.database.connection.DATABASE_URL` or `.env` defaults. If `TEST_DISPOSABLE_SQLSERVER_URL` is missing, tests must skip immediately.

### B. Approved Integration Database Allowlist
The canonical allowlist enforced by `app/core/database_guard.py` is:
```python
APPROVED_INTEGRATION_DATABASES: frozenset[str] = frozenset({
    "ceylongodb_integrationtest",
    "ceylongodb_disposable_qa",
})
```

### C. Fail-Closed Connection Guard Implementation
To seal the identified vulnerability in `test_eco_sqlserver_integration.py`, the engine fixture must be structured as follows:

```python
from sqlalchemy.engine import make_url
from app.core.database_guard import verify_safe_integration_database, DatabaseSafetyError

@pytest.fixture(scope="module")
def sqlserver_engine():
    raw_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if not raw_url:
        pytest.skip("TEST_DISPOSABLE_SQLSERVER_URL not configured")
        
    url = make_url(raw_url)
    target_db = url.database
    
    # Layer 1: Static URL verification
    verify_safe_integration_database(target_db)
    
    engine = create_engine(raw_url, pool_pre_ping=True)
    
    # Layer 2: Runtime server query verification
    with engine.connect() as conn:
        active_db = conn.execute(text("SELECT DB_NAME();")).scalar()
        verify_safe_integration_database(active_db)
        
    yield engine
    engine.dispose()
```

### D. Least-Privilege Test Credentials Specification
When provisioning the disposable database, the test account must adhere to the principle of least privilege:
- **Server Level**: No `sysadmin`, `serveradmin`, or `securityadmin` roles.
- **Database Level (Disposable DB Only)**:
  - `db_datareader` (read access)
  - `db_datawriter` (write/modify test rows)
  - `db_ddladmin` (create/alter test tables/constraints if executing migrations on disposable DB)
- **Database Level (`CeylonGoDB`)**:
  - `DENY CONNECT` to prevent cross-database accidental execution.

---

## 4. Test Database Safety Checklist

Before executing any integration test against SQL Server, QA and DBA teams must complete and sign this checklist:

- [ ] **1. Isolation Confirmation**: Target database is physically separate from `CeylonGoDB`.
- [ ] **2. Allowlist Match**: Database name exactly matches `CeylonGoDB_IntegrationTest` or `CeylonGoDB_Disposable_QA`.
- [ ] **3. Fail-Closed Guard Active**: URL parsing and runtime `SELECT DB_NAME();` checks are in place.
- [ ] **4. Credential Isolation**: Test user has zero permissions on `CeylonGoDB`.
- [ ] **5. Outbound Network Isolation**: `SMTP_HOST=""` and payment gateways disabled (mocked).
- [ ] **6. Prerequisite Gate 1 Signed**: Backend Team Leader approval for `ActivityBookingParticipant` ORM alignment.
- [ ] **7. Prerequisite Gate 2 Signed**: Database Coordinator approval for Migration 030 applied to disposable DB.
- [ ] **8. Automated Cleanup Ready**: Session rollback and teardown scripts verified.

---

## 5. Risk Assessment & Mitigations

| Risk | Likelihood | Impact | Mitigation Strategy |
|---|:---:|:---:|---|
| Test runner connects to live `CeylonGoDB` | Low | Critical | Dual-layer fail-closed guard (URL check + runtime `SELECT DB_NAME()`). |
| Orphan test records pollute database | Medium | Low | Explicit `session.rollback()` in all test teardowns; test prefixes for isolation. |
| Lock timeout under concurrent CI execution | Medium | Medium | Configurable lock timeout parameters (`timeout_ms=5000`) and pooled connections. |
| Test failure due to unapplied Migration 030 | High | High | Gate 2 dependency check prevents running end-to-end tests until migration is applied to test DB. |
| Test failure due to phantom column in ORM | High | High | Gate 1 dependency check prevents running participant tests until ORM model is aligned. |

---

## 6. Remaining Actions

1. Await formal approval of Gate 1 (Backend Team Leader) and Gate 2 (Database Coordinator).
2. DBA to provision `CeylonGoDB_IntegrationTest` with least-privilege credentials.
3. Apply Migrations 001–030 to `CeylonGoDB_IntegrationTest` only.
4. Export `TEST_DISPOSABLE_SQLSERVER_URL` in QA environment.
5. Execute QA Execution Runbook (Steps A–J).
