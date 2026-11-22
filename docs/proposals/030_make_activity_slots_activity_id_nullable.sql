-- Migration: 030_make_activity_slots_activity_id_nullable.sql
-- Module: Activity & Eco-Tourism Unified Slot Infrastructure (Module 04 / Module 03)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Description: Make dbo.activity_slots.activity_id column NULL to support standalone Eco-Tourism slots
-- Prerequisite Approvals: Database Coordinator Approval Required
-- Execution Status: STAGED / PENDING APPROVAL - DO NOT EXECUTE AUTOMATICALLY
-- Idempotency: Fully safe to run multiple times.

-- =============================================================================
-- Batch 1: Prerequisite Checks
-- =============================================================================
IF OBJECT_ID('dbo.activity_slots', 'U') IS NULL
BEGIN
    RAISERROR('Prerequisite failed: Table dbo.activity_slots does not exist.', 16, 1);
    RETURN;
END;

IF COL_LENGTH('dbo.activity_slots', 'eco_experience_id') IS NULL
BEGIN
    RAISERROR('Prerequisite failed: Column dbo.activity_slots.eco_experience_id does not exist (Migration 025 required).', 16, 1);
    RETURN;
END;

-- Verify existing rows comply with the upcoming XOR constraint
IF EXISTS (
    SELECT 1 FROM dbo.activity_slots
    WHERE (activity_id IS NULL AND eco_experience_id IS NULL)
       OR (activity_id IS NOT NULL AND eco_experience_id IS NOT NULL)
)
BEGIN
    RAISERROR('Prerequisite failed: Existing rows in dbo.activity_slots violate the activity_id XOR eco_experience_id constraint.', 16, 1);
    RETURN;
END;
GO

-- =============================================================================
-- Batch 2: Alter Column nullability (activity_id INT NULL)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.activity_slots')
      AND name = 'activity_id'
      AND is_nullable = 0
)
BEGIN
    PRINT 'Altering dbo.activity_slots.activity_id from NOT NULL to NULL...';
    ALTER TABLE dbo.activity_slots
    ALTER COLUMN activity_id INT NULL;
    PRINT 'Successfully altered dbo.activity_slots.activity_id to NULL.';
END
ELSE
BEGIN
    PRINT 'dbo.activity_slots.activity_id is already nullable. Skipping ALTER.';
END;
GO

-- =============================================================================
-- Batch 3: Add Check Constraint ensuring mutual exclusivity (XOR)
-- Ensures slot is tied to EITHER an activity OR an eco-experience, never both and never neither
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_activity_slots_parent_target'
      AND parent_object_id = OBJECT_ID('dbo.activity_slots')
)
BEGIN
    PRINT 'Adding mutual-exclusivity check constraint CK_activity_slots_parent_target...';
    ALTER TABLE dbo.activity_slots
    ADD CONSTRAINT CK_activity_slots_parent_target
        CHECK (
            (activity_id IS NOT NULL AND eco_experience_id IS NULL)
            OR
            (activity_id IS NULL AND eco_experience_id IS NOT NULL)
        );
    PRINT 'Successfully added CK_activity_slots_parent_target (XOR parent enforced).';
END
ELSE
BEGIN
    PRINT 'Check constraint CK_activity_slots_parent_target already exists. Skipping.';
END;
GO

-- =============================================================================
-- Batch 4: Post-Migration Verification Queries
-- =============================================================================
PRINT '--- Post-Migration Validation ---';
SELECT
    c.name AS column_name,
    t.name AS data_type,
    c.is_nullable
FROM sys.columns c
JOIN sys.types t ON c.user_type_id = t.user_type_id
WHERE c.object_id = OBJECT_ID('dbo.activity_slots')
  AND c.name IN ('activity_id', 'eco_experience_id');

SELECT
    name AS constraint_name,
    definition
FROM sys.check_constraints
WHERE parent_object_id = OBJECT_ID('dbo.activity_slots')
  AND name = 'CK_activity_slots_parent_target';
GO

-- =============================================================================
-- ROLLBACK PROCEDURE (Documented for Database Coordinator)
-- =============================================================================
/*
-- STEP 1: Refuse rollback if any Eco-only slots exist (activity_id IS NULL)
-- This strictly prevents silent data loss or foreign key corruptions.
IF EXISTS (SELECT 1 FROM dbo.activity_slots WHERE activity_id IS NULL)
BEGIN
    DECLARE @eco_slot_count INT;
    SELECT @eco_slot_count = COUNT(*) FROM dbo.activity_slots WHERE activity_id IS NULL;
    RAISERROR('Rollback ABORTED: Found %d Eco-only slot(s) with activity_id IS NULL in dbo.activity_slots. Cannot restore NOT NULL without data loss. Reassign, export, or archive these slots before proceeding.', 16, 1, @eco_slot_count);
    RETURN;
END;

-- STEP 2: Drop the XOR check constraint
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_activity_slots_parent_target'
      AND parent_object_id = OBJECT_ID('dbo.activity_slots')
)
BEGIN
    ALTER TABLE dbo.activity_slots DROP CONSTRAINT CK_activity_slots_parent_target;
    PRINT 'Dropped constraint CK_activity_slots_parent_target.';
END;

-- STEP 3: Restore activity_id INT NOT NULL
ALTER TABLE dbo.activity_slots ALTER COLUMN activity_id INT NOT NULL;
PRINT 'Successfully restored dbo.activity_slots.activity_id to NOT NULL.';
*/
