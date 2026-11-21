# CeylonGo Module 04 — Phase 5Q: T09 Real SQL Server Integration Handoff Guide

## 1. Document Purpose

This handoff guide specifies the exact prerequisites, environmental configurations, and execution procedures for QA Engineers and Database Administrators to execute the live Microsoft SQL Server payment integration test suite once **Gate 3** is approved.

---

## 2. Governance Baseline & Gate Status

- **T09 Requirement Status**: **BLOCKED**
- **Module 04 Completion**: 37 COMPLETE, 10 STAGED, 2 PARTIAL (T16, E06), 1 BLOCKED (T09), 0 NOT STARTED (74% strict completion).
- **Gate 1 (Shared ORM Patch)**: Technical package ready; approval pending.
- **Gate 2 (Migration 030 Execution)**: Technical package ready; approval pending.
- **Gate 3 (Disposable SQL Server Execution)**: Safety hardening complete; provisioning & authorization pending.
- **Gate 4 (Router Activation & Staging Removal)**: Staged; not authorized.

---

## 3. QA / DBA Execution Prerequisites

Before executing the live SQL Server integration suite:

1. **Gate 3 Authorization**: Formal sign-off from the Tech Lead / DBA to provision and execute tests against an isolated disposable SQL Server instance.
2. **Approved Database Provisioning**:
   - The database name **MUST** match one of the approved patterns:
     - `ceylongodb_disposable_qa`
     - `ceylongodb_integrationtest`
   - Target database **MUST NOT** be `CeylonGoDB`, `CeylonGoDB_Development`, or any system database (`master`, `tempdb`, `model`, `msdb`).
3. **Applied Schema**:
   - The disposable database must have the current CeylonGo schema plus Migration 030 applied so that `ActivityBookings`, `PaymentTransactions`, and `AuditLogs` tables exist with expected schema constraints.

---

## 4. Execution Step-by-Step Runbook

### Step 1: Set the Disposable Database Environment Variable
In PowerShell (QA test runner session):
```powershell
$env:TEST_DISPOSABLE_SQLSERVER_URL = "mssql+pyodbc://<user>:<password>@<host>:<port>/ceylongodb_disposable_qa?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
```
*(Ensure `<user>`, `<password>`, `<host>`, and `<port>` reflect the disposable QA instance.)*

### Step 2: Validate Fail-Closed Safety Guards First
Before executing live tests, verify the safety guards reject invalid strings:
```powershell
pytest tests/test_eco_sqlserver_test_safety.py tests/integration/test_eco_payment_sqlserver_integration.py -k "test_safety" -v
```
*Expected: 10 passed in ~6 seconds.*

### Step 3: Run the Live SQL Server Payment Integration Suite
```powershell
pytest tests/integration/test_eco_payment_sqlserver_integration.py -v
```
*Expected: 14 passed (5 safety + 9 live SQL Server integration tests) in ~5 seconds.*

### Step 4: Tear Down Disposable Test Database
After test execution, the disposable database should be dropped or sanitized:
```sql
DROP DATABASE [ceylongodb_disposable_qa];
```

---

## 5. Summary of Implemented Payment Test Capabilities

| Capability | Module/Class | Integration Test |
|---|---|---|
| Checkout Preparation & Hashes | `EcoPaymentService` | `test_eco_payhere_integration.py` (24 passed) |
| Webhook Verification & Status | `EcoPayHereWebhookSecurity` | `test_eco_payhere_webhook_security.py` (42 passed) |
| Application Orchestration | `EcoPaymentApplicationService` | `test_eco_payment_application.py` (21 passed) |
| Staged Endpoint API Contract | `app/routers/eco_payments_proposed.py` | `test_eco_payments_proposed_router.py` (15 passed) |
| SQLAlchemy Unit of Work | `EcoPaymentSQLAlchemyAdapter` | `test_eco_payment_sqlalchemy_adapter.py` (20 passed) |
| Transaction Coordination | `EcoPaymentTransactionCoordinator` | `test_eco_payment_transaction_coordinator.py` (20 passed) |
| Real MSSQL DB Semantics & Guards | `tests/integration/test_eco_payment_sqlserver_integration.py` | 5 passed, 9 skipped (Gate 3 ready) |
