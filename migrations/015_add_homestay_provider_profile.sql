-- Migration: 015_add_homestay_provider_profile.sql
-- Description: Standardize Homestay ownership with dual foreign key architecture
-- Module: M01 Homestay Management & Provider Authorization Stabilization
-- Target Database: CeylonGoDB (Microsoft SQL Server 2017+)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

/*
================================================================================
MIGRATION EXECUTION & RUNTIME REQUIREMENTS:
================================================================================
1. GO BATCH SEPARATOR HANDLING:
   - 'GO' is a client utility command recognized by SQL Server Management Studio
     (SSMS), Azure Data Studio, sqlcmd, and mssql-cli.
   - It is NOT a T-SQL command.
   - When executing this migration via Python (pyodbc / SQLAlchemy), scripts
     must split the script on '\bGO\b' boundaries and execute each batch
     separately, or execute via an automation runner (e.g., alembic / sqlcmd).

2. TRANSACTION & ROLLBACK REQUIREMENTS:
   - DDL statements (ALTER TABLE, ADD CONSTRAINT, CREATE INDEX) and data backfills
     should be executed in staging/production within an explicit transaction:
         BEGIN TRANSACTION;
         BEGIN TRY
             -- execute batches
             COMMIT TRANSACTION;
         END TRY
         BEGIN CATCH
             IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
             THROW;
         END CATCH
   - In SQL Server, schema alterations and metadata updates are fully transactional
     and can be safely rolled back in the event of any runtime failure.

3. RELATIONSHIP CONSTRAINTS & UNIQUENESS VERIFICATION:
   - The provider_profiles table defines:
         CONSTRAINT UQ_provider_profiles_user_id UNIQUE (user_id)
     in migration 003_create_provider_profiles.sql.
   - This database-enforced unique constraint guarantees that at most ONE provider
     profile can exist per user, ensuring the join (p.user_id = h.owner_id) is
     strictly 1:1 and cannot introduce Cartesian duplicates during backfill.
================================================================================
*/

-- -----------------------------------------------------------------------------
-- Batch 1: Add provider_profile_id column to dbo.homestays if not exists
-- -----------------------------------------------------------------------------
IF COL_LENGTH('dbo.homestays', 'provider_profile_id') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD provider_profile_id INT NULL;

    PRINT 'Column provider_profile_id added to dbo.homestays as NULLABLE.';
END
ELSE
BEGIN
    PRINT 'Column provider_profile_id already exists in dbo.homestays.';
END;
GO

-- -----------------------------------------------------------------------------
-- Batch 2: Add foreign key constraint referencing dbo.provider_profiles(id)
--          Preserves ON DELETE NO ACTION (prevents cascade deletion)
--          Preserves owner_id constraints and nullability
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1
    FROM sys.foreign_keys
    WHERE name = 'FK_homestays_provider_profile'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT FK_homestays_provider_profile
        FOREIGN KEY (provider_profile_id)
        REFERENCES dbo.provider_profiles (id)
        ON DELETE NO ACTION;

    PRINT 'Foreign key FK_homestays_provider_profile created with ON DELETE NO ACTION.';
END
ELSE
BEGIN
    PRINT 'Foreign key FK_homestays_provider_profile already exists.';
END;
GO

-- -----------------------------------------------------------------------------
-- Batch 3: Create non-clustered index on provider_profile_id for fast joins
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_homestays_provider_profile_id'
      AND object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestays_provider_profile_id
        ON dbo.homestays(provider_profile_id);

    PRINT 'Index IX_homestays_provider_profile_id created on dbo.homestays(provider_profile_id).';
END
ELSE
BEGIN
    PRINT 'Index IX_homestays_provider_profile_id already exists.';
END;
GO

-- -----------------------------------------------------------------------------
-- Batch 4: Safe, Idempotent Backfill & Non-Overlapping Discrepancy Audit
-- -----------------------------------------------------------------------------
-- Invariant Rules:
-- 1. Explicitly exclude administrator-owned listings (u.role = 'admin').
-- 2. Backfill ONLY unambiguous, valid homestay providers (provider_type = 'homestay' AND approval_status = 'approved').
-- 3. Preserve existing non-null provider_profile_id values unless an inconsistency is detected.
-- 4. Do not backfill artisan or unrelated provider profiles.
-- 5. Audit and report mutually exclusive, non-overlapping categories.
-- -----------------------------------------------------------------------------

DECLARE @TotalHomestaysCount INT = 0;
DECLARE @AlreadyConsistentCount INT = 0;
DECLARE @NewlyBackfilledCount INT = 0;
DECLARE @PreservedAdminCount INT = 0;
DECLARE @MismatchedTypeCount INT = 0;
DECLARE @UnapprovedStatusCount INT = 0;
DECLARE @InconsistentMismatchedLinks INT = 0;
DECLARE @UnmatchedOrphanCount INT = 0;

-- Count total homestay records
SELECT @TotalHomestaysCount = COUNT(*) FROM dbo.homestays;

-- Category 1: Inconsistent existing links (provider_profile_id exists, but points to wrong user or invalid profile)
SELECT @InconsistentMismatchedLinks = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.provider_profiles p ON p.id = h.provider_profile_id
WHERE h.owner_id IS NOT NULL
  AND p.user_id <> h.owner_id;

-- Category 2: Already linked and consistent prior to backfill
SELECT @AlreadyConsistentCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.provider_profiles p ON p.id = h.provider_profile_id
WHERE h.owner_id IS NOT NULL
  AND p.user_id = h.owner_id;

-- Execute Backfill: Link ONLY unlinked homestays owned by approved homestay providers (excluding admin)
UPDATE h
SET h.provider_profile_id = p.id
FROM dbo.homestays h
INNER JOIN dbo.users u
    ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p
    ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND p.provider_type = 'homestay'
  AND p.approval_status = 'approved';

SET @NewlyBackfilledCount = @@ROWCOUNT;

-- Category 3: Admin-owned listings (intentionally unlinked, provider_profile_id remains NULL)
SELECT @PreservedAdminCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role = 'admin';

-- Category 4: Owner is an artisan or other non-homestay provider (not backfilled, flagged for review)
SELECT @MismatchedTypeCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND p.provider_type <> 'homestay';

-- Category 5: Owner has a homestay profile but it is pending or rejected (not backfilled, flagged for review)
SELECT @UnapprovedStatusCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND p.provider_type = 'homestay'
  AND p.approval_status <> 'approved';

-- Category 6: Unmatched / orphan homestays (owner_id is NULL or user has no provider profile and is not admin)
SELECT @UnmatchedOrphanCount = COUNT(*)
FROM dbo.homestays h
LEFT JOIN dbo.users u ON u.id = h.owner_id
LEFT JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND (h.owner_id IS NULL OR (u.role <> 'admin' AND p.id IS NULL));

PRINT '===================================================================';
PRINT '             MIGRATION 015 AUDIT & BACKFILL REPORT                 ';
PRINT '===================================================================';
PRINT 'Total Homestay Listings in Database: ' + CAST(@TotalHomestaysCount AS NVARCHAR(10));
PRINT '-------------------------------------------------------------------';
PRINT '[A] Pre-existing Consistent Links:    ' + CAST(@AlreadyConsistentCount AS NVARCHAR(10));
PRINT '[B] Newly Backfilled Homestays:       ' + CAST(@NewlyBackfilledCount AS NVARCHAR(10));
PRINT '[C] Admin-Created Listings (NULL FK): ' + CAST(@PreservedAdminCount AS NVARCHAR(10));
PRINT '[D] Flagged - Non-Homestay Providers: ' + CAST(@MismatchedTypeCount AS NVARCHAR(10));
PRINT '[E] Flagged - Unapproved Providers:   ' + CAST(@UnapprovedStatusCount AS NVARCHAR(10));
PRINT '[F] Flagged - Inconsistent FK Links:  ' + CAST(@InconsistentMismatchedLinks AS NVARCHAR(10));
PRINT '[G] Flagged - Unmatched / Orphans:    ' + CAST(@UnmatchedOrphanCount AS NVARCHAR(10));
PRINT '-------------------------------------------------------------------';
PRINT 'Verification Sum ([A]+[B]+[C]+[D]+[E]+[F]+[G]): ' +
      CAST((@AlreadyConsistentCount + @NewlyBackfilledCount + @PreservedAdminCount + @MismatchedTypeCount + @UnapprovedStatusCount + @InconsistentMismatchedLinks + @UnmatchedOrphanCount) AS NVARCHAR(10));
PRINT '===================================================================';
GO
