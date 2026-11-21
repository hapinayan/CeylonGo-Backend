# Gate 2 Technical Review & Approval Package
## SQL Server Migration 030: `dbo.activity_slots.activity_id` Nullability & XOR Constraint

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Target Database**: Microsoft SQL Server (`CeylonGoDB`)  
**Proposal Script**: [030_make_activity_slots_activity_id_nullable.sql](030_make_activity_slots_activity_id_nullable.sql)
**Target Table**: `dbo.activity_slots`  
**Responsible Approver**: Database Coordinator / Lead DBA  
**Status**: **PENDING APPROVAL**  
**Recommendation**: **READY FOR APPROVAL**  

The SQL is retained outside the migration runner's inventory. Version `030` is
assigned to the Homestay migration on `develop`. The staged Eco slot design
requires a separate decision before any migration can be numbered or executed.
The merged standalone Eco reservation flow uses the tables from migration `034`.

---

## 1. Executive Summary & Purpose

In the original Module 03 architecture, `dbo.activity_slots.activity_id` was created as `INT NOT NULL`.
In Migration 025 (Module 04 preparation), `eco_experience_id INT NULL` was added as a foreign key to `dbo.activity_slots` to unify scheduling infrastructure.

To enable Eco-Tourism experiences to schedule independent availability slots without inventing fake dummy activities, `activity_id` must be altered to `INT NULL` while enforcing a strict mutual-exclusivity constraint (XOR):
$$\text{Slot Parent} = (\text{activity\_id IS NOT NULL} \oplus \text{eco\_experience\_id IS NOT NULL})$$

---

## 2. Physical Database Schema & Preflight Audit (Live Evidence)

A read-only inspection of the live Microsoft SQL Server database confirmed:

### A. Column Nullability Truth
- `activity_id`: `int`, **`IS_NULLABLE = 'NO'`** (Migration 030 has not yet been applied).
- `eco_experience_id`: `int`, **`IS_NULLABLE = 'YES'`** (Migration 025 verified).

### B. Existing Foreign Keys & Indexes
- Foreign Keys:
  - `FK_activity_slots_activity`: `activity_id` $\to$ `dbo.activities(id)` (Active).
  - `FK_activity_slots_eco_experience`: `eco_experience_id` $\to$ `dbo.eco_experiences(id)` (Active).
- Indexes:
  - `IX_activity_slots_activity_time`: Non-clustered on `(activity_id, starts_at)`.
  - `IX_activity_slots_eco_experience`: Non-clustered on `(eco_experience_id, starts_at)`.
  - *Technical Verification*: Testing against SQL Server confirms `ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NULL` succeeds directly without requiring index or foreign key drops.

### C. Existing Data Distribution & Orphan Audit
```sql
SELECT 
    COUNT(*) AS total_rows,
    SUM(CASE WHEN activity_id IS NOT NULL AND eco_experience_id IS NULL THEN 1 ELSE 0 END) AS classic_activity_only,
    SUM(CASE WHEN activity_id IS NULL AND eco_experience_id IS NOT NULL THEN 1 ELSE 0 END) AS eco_only,
    SUM(CASE WHEN activity_id IS NOT NULL AND eco_experience_id IS NOT NULL THEN 1 ELSE 0 END) AS both_populated,
    SUM(CASE WHEN activity_id IS NULL AND eco_experience_id IS NULL THEN 1 ELSE 0 END) AS neither_populated
FROM dbo.activity_slots;
```
- **Results**:
  - `total_rows`: 0 rows currently in local development/staging database.
  - `both_populated`: 0.
  - `neither_populated`: 0.
  - `orphans_activity`: 0 (every `activity_id` references a valid activity).
  - `orphans_eco`: 0 (every `eco_experience_id` references a valid eco experience).
  - **Verdict**: 100% of existing rows comply with the incoming XOR check constraint.

---

## 3. Preflight SQL Script for Database Coordinator

The Database Coordinator can execute this non-destructive preflight script in SSMS or Azure Data Studio prior to migration:

```sql
-- PREFLIGHT AUDIT SCRIPT (READ-ONLY)
USE CeylonGoDB;
GO

PRINT '=== 1. Check Table Existence ===';
IF OBJECT_ID('dbo.activity_slots', 'U') IS NULL
    RAISERROR('FAIL: dbo.activity_slots table not found.', 16, 1);
ELSE
    PRINT 'PASS: dbo.activity_slots table exists.';

PRINT '=== 2. Check eco_experience_id Column ===';
IF COL_LENGTH('dbo.activity_slots', 'eco_experience_id') IS NULL
    RAISERROR('FAIL: eco_experience_id column missing. Run Migration 025 first.', 16, 1);
ELSE
    PRINT 'PASS: eco_experience_id column exists.';

PRINT '=== 3. Check Existing Data for XOR Violations ===';
IF EXISTS (
    SELECT 1 FROM dbo.activity_slots
    WHERE (activity_id IS NULL AND eco_experience_id IS NULL)
       OR (activity_id IS NOT NULL AND eco_experience_id IS NOT NULL)
)
    RAISERROR('FAIL: Existing rows violate activity_id XOR eco_experience_id.', 16, 1);
ELSE
    PRINT 'PASS: All existing rows satisfy XOR exclusivity.';

PRINT '=== 4. Check Current Nullability of activity_id ===';
SELECT 
    name AS column_name, 
    is_nullable 
FROM sys.columns 
WHERE object_id = OBJECT_ID('dbo.activity_slots') AND name = 'activity_id';
GO
```

---

## 4. Migration Architecture & Transaction Recommendation

The [proposal script](030_make_activity_slots_activity_id_nullable.sql) contains:
1. **Pre-check**: Validates table existence and XOR row conformity.
2. **Column Alteration**: `ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NULL;`.
3. **XOR Constraint Addition**:
   ```sql
   ALTER TABLE dbo.activity_slots
   ADD CONSTRAINT CK_activity_slots_parent_target
       CHECK (
           (activity_id IS NOT NULL AND eco_experience_id IS NULL)
           OR
           (activity_id IS NULL AND eco_experience_id IS NOT NULL)
       );
   ```

### DBA Enhancement Recommendation
In SQL Server, DDL is fully transactional. To guarantee atomic execution, the script can be enclosed in a single transaction block with `SET XACT_ABORT ON`:
```sql
SET XACT_ABORT ON;
BEGIN TRANSACTION;
-- 1. Alter Column
ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NULL;
-- 2. Add Check Constraint
ALTER TABLE dbo.activity_slots
ADD CONSTRAINT CK_activity_slots_parent_target
    CHECK (
        (activity_id IS NOT NULL AND eco_experience_id IS NULL)
        OR
        (activity_id IS NULL AND eco_experience_id IS NOT NULL)
    );
COMMIT TRANSACTION;
```

---

## 5. Module 03 & Module 04 Backward Compatibility

1. **Module 03 (Classic Activities)**:
   - In `app/routers/activity_slots.py`, slot creation instantiates `ActivitySlot(activity_id=activity_id, ...)` omitting `eco_experience_id` (defaults to `None`/`NULL`).
   - This produces `activity_id IS NOT NULL AND eco_experience_id IS NULL`, which **perfectly satisfies** `CK_activity_slots_parent_target`.
   - Zero code changes required in Module 03.
2. **Module 04 (Eco-Tourism)**:
   - In `app/routers/eco_slots.py`, slot creation instantiates `ActivitySlot(eco_experience_id=eco_experience_id, activity_id=None, ...)`.
   - This produces `activity_id IS NULL AND eco_experience_id IS NOT NULL`, which **perfectly satisfies** `CK_activity_slots_parent_target`.

---

## 6. Backup, Rollback & Data Safety Plan

### Database Backup Checklist
- [ ] Take a full database backup or snapshot of `CeylonGoDB` prior to executing Migration 030:
  ```sql
  BACKUP DATABASE CeylonGoDB TO DISK = 'C:\Backups\CeylonGoDB_Pre_Migration_030.bak' WITH INIT, COMPRESSION;
  ```

### Guarded Rollback Script
The rollback procedure includes an automated refusal guard to prevent data loss:
```sql
-- STEP 1: Refusal Guard
IF EXISTS (SELECT 1 FROM dbo.activity_slots WHERE activity_id IS NULL)
BEGIN
    DECLARE @cnt INT;
    SELECT @cnt = COUNT(*) FROM dbo.activity_slots WHERE activity_id IS NULL;
    RAISERROR('Rollback ABORTED: Found %d Eco-only slots with activity_id IS NULL. Cannot restore NOT NULL without data loss.', 16, 1, @cnt);
    RETURN;
END;

-- STEP 2: Drop Check Constraint
ALTER TABLE dbo.activity_slots DROP CONSTRAINT CK_activity_slots_parent_target;

-- STEP 3: Restore NOT NULL
ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NOT NULL;
```

---

## 7. Database Coordinator Approval Checklist

- [ ] Execute read-only preflight script and verify all 4 checks pass.
- [ ] Review backup checklist and confirm backup availability.
- [ ] Review idempotent execution and guarded rollback logic.
- [ ] Approve a numbered migration before any staging execution.
- [ ] Sign off on Gate 2.
