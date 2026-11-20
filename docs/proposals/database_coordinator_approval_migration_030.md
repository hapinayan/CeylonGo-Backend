# Database Coordinator Approval Handoff: Migration 030

**Target Script**: `migrations/030_make_activity_slots_activity_id_nullable.sql`  
**Target Table**: `dbo.activity_slots` (Microsoft SQL Server / `CeylonGoDB`)  
**Audience**: Database Coordinator (Review & Execution Approval Required)  
**Status**: STAGED — Migration script has NOT been executed. Zero live database writes performed.

---

## 1. Executive Summary & Purpose

In the current schema (created in Module 03 baseline), `dbo.activity_slots.activity_id` is defined as `INT NOT NULL`.
In Migration 025 (Module 04 preparation), `eco_experience_id INT NULL` was added as a foreign key column to `dbo.activity_slots`.

To allow Eco-Tourism experiences to schedule independent availability slots without inventing fake dummy activities or invalid foreign keys, `activity_id` must be made nullable, subject to a mutual-exclusivity constraint (XOR).

---

## 2. Migration 030 Architecture & Operations

The script [migrations/030_make_activity_slots_activity_id_nullable.sql](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/migrations/030_make_activity_slots_activity_id_nullable.sql) executes the following idempotent batches:

### Batch 1: Prerequisite & Compatibility Validation
1. Asserts `dbo.activity_slots` table exists.
2. Asserts `dbo.activity_slots.eco_experience_id` exists (confirms Migration 025 has been applied).
3. **Pre-Migration Integrity Check**: Validates that all existing rows in `dbo.activity_slots` satisfy the mutual exclusivity rule:
   ```sql
   IF EXISTS (
       SELECT 1 FROM dbo.activity_slots
       WHERE (activity_id IS NULL AND eco_experience_id IS NULL)
          OR (activity_id IS NOT NULL AND eco_experience_id IS NOT NULL)
   )
   BEGIN
       RAISERROR('Prerequisite failed: Existing rows in dbo.activity_slots violate the activity_id XOR eco_experience_id constraint.', 16, 1);
       RETURN;
   END;
   ```
   *Analysis*: All 100% of existing Module 03 slot records have `activity_id IS NOT NULL` and `eco_experience_id IS NULL`, guaranteeing 100% compatibility.

### Batch 2: Idempotent Column Alteration
```sql
ALTER TABLE dbo.activity_slots
ALTER COLUMN activity_id INT NULL;
```
- Preserves existing foreign key constraints and indexes.
- Idempotent: checks `sys.columns.is_nullable` before executing.

### Batch 3: XOR Mutual-Exclusivity Constraint
Enforces that every slot is tied to **either** a classic Activity **or** an Eco-Tourism Experience, never both and never neither:
```sql
ALTER TABLE dbo.activity_slots
ADD CONSTRAINT CK_activity_slots_parent_target
    CHECK (
        (activity_id IS NOT NULL AND eco_experience_id IS NULL)
        OR
        (activity_id IS NULL AND eco_experience_id IS NOT NULL)
    );
```

### Batch 4: Verification Queries
Outputs schema metadata confirming `is_nullable = 1` for `activity_id` and the definition of `CK_activity_slots_parent_target`.

---

## 3. Rollback Procedure & Data Safety

The rollback procedure is safe and guarded against accidental data loss:
1. **Refusal Check**: The rollback script **strictly refuses to run** if any Eco-only slots (`activity_id IS NULL`) exist in the table:
   ```sql
   IF EXISTS (SELECT 1 FROM dbo.activity_slots WHERE activity_id IS NULL)
   BEGIN
       DECLARE @eco_slot_count INT;
       SELECT @eco_slot_count = COUNT(*) FROM dbo.activity_slots WHERE activity_id IS NULL;
       RAISERROR('Rollback ABORTED: Found %d Eco-only slot(s) with activity_id IS NULL in dbo.activity_slots. Cannot restore NOT NULL without data loss. Reassign, export, or archive these slots before proceeding.', 16, 1, @eco_slot_count);
       RETURN;
   END;
   ```
2. **Constraint Dropping**: Drops `CK_activity_slots_parent_target`.
3. **Nullability Restoration**: Executes `ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NOT NULL;`.
4. **Data Preservation Protocol**: In the event of an emergency rollback on a database where Eco-Tourism slots were created, operators must export rows with `activity_id IS NULL` to an archive table (`dbo.activity_slots_eco_archive`) before dropping them.

---

## 4. Verification & Testing

- Unmigrated schema behavior verified in `tests/test_eco_slots_router_staged.py`: Attempting slot creation without Migration 030 triggers database error, safely rolls back, and returns generic HTTP 500 without leaking column details.
- Integration test suite ready in `tests/test_eco_sqlserver_integration.py` for post-migration execution on disposable SQL Server instance.

---

## 5. Action Requested

Database Coordinator approval is requested to:
1. Execute `migrations/030_make_activity_slots_activity_id_nullable.sql` on `CeylonGoDB_Development`.
2. Confirm the post-migration validation query outputs.
