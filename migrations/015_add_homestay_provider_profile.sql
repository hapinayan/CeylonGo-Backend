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
-- 1. Verify UQ_provider_profiles_user_id unique constraint exists before backfilling.
-- 2. Explicitly exclude administrator-owned listings (u.role = 'admin').
-- 3. Backfill ONLY unambiguous, active, valid homestay providers:
--    (provider_type = 'homestay' AND approval_status = 'approved' AND is_active = 1).
-- 4. Preserve existing non-null provider_profile_id values unless an inconsistency is detected.
-- 5. Do not backfill artisan or unrelated provider profiles.
-- 6. Audit and report mutually exclusive, non-overlapping categories ([A] through [G]).
-- -----------------------------------------------------------------------------

-- Pre-condition check: verify uniqueness on dbo.provider_profiles(user_id)
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes i
    INNER JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
    INNER JOIN sys.columns c ON ic.object_id = c.object_id AND ic.column_id = c.column_id
    WHERE i.object_id = OBJECT_ID('dbo.provider_profiles')
      AND i.is_unique = 1
      AND c.name = 'user_id'
)
BEGIN
    RAISERROR('Pre-condition failed: dbo.provider_profiles must have a UNIQUE constraint on user_id.', 16, 1);
    RETURN;
END;

DECLARE @TotalHomestaysCount INT = 0;
DECLARE @AlreadyConsistentCount INT = 0;
DECLARE @NewlyBackfilledCount INT = 0;
DECLARE @PreservedAdminCount INT = 0;
DECLARE @MismatchedTypeCount INT = 0;
DECLARE @UnapprovedOrInactiveStatusCount INT = 0;
DECLARE @InconsistentMismatchedLinks INT = 0;
DECLARE @UnmatchedOrphanCount INT = 0;

-- Count total homestay records
SELECT @TotalHomestaysCount = COUNT(*) FROM dbo.homestays;

-- Category [A]: Already linked and consistent prior to backfill
SELECT @AlreadyConsistentCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.provider_profiles p ON p.id = h.provider_profile_id
WHERE h.owner_id IS NOT NULL
  AND p.user_id = h.owner_id;

-- Category [F]: Inconsistent existing links (provider_profile_id exists, but points to wrong user, missing user, or invalid profile)
SELECT @InconsistentMismatchedLinks = COUNT(*)
FROM dbo.homestays h
LEFT JOIN dbo.provider_profiles p ON p.id = h.provider_profile_id
WHERE h.provider_profile_id IS NOT NULL
  AND (h.owner_id IS NULL OR p.id IS NULL OR p.user_id <> h.owner_id);

-- Execute Backfill: Link ONLY unlinked homestays owned by active, approved homestay providers (excluding admin)
UPDATE h
SET h.provider_profile_id = p.id
FROM dbo.homestays h
INNER JOIN dbo.users u
    ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p
    ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND u.is_active = 1
  AND p.provider_type = 'homestay'
  AND p.approval_status = 'approved';

SET @NewlyBackfilledCount = @@ROWCOUNT;

-- Category [C]: Admin-owned listings (intentionally unlinked, provider_profile_id remains NULL)
SELECT @PreservedAdminCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role = 'admin';

-- Category [D]: Owner is an artisan or other non-homestay provider (not backfilled, flagged for review)
SELECT @MismatchedTypeCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND p.provider_type <> 'homestay';

-- Category [E]: Owner has a homestay profile but it is pending, rejected, or inactive (not backfilled, flagged for review)
SELECT @UnapprovedOrInactiveStatusCount = COUNT(*)
FROM dbo.homestays h
INNER JOIN dbo.users u ON u.id = h.owner_id
INNER JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND u.role <> 'admin'
  AND p.provider_type = 'homestay'
  AND (p.approval_status <> 'approved' OR u.is_active = 0);

-- Category [G]: Unmatched / orphan homestays (owner_id is NULL or user has no provider profile and is not admin)
SELECT @UnmatchedOrphanCount = COUNT(*)
FROM dbo.homestays h
LEFT JOIN dbo.users u ON u.id = h.owner_id
LEFT JOIN dbo.provider_profiles p ON p.user_id = h.owner_id
WHERE h.provider_profile_id IS NULL
  AND (h.owner_id IS NULL OR u.id IS NULL OR (u.role <> 'admin' AND p.id IS NULL));

PRINT '===================================================================';
PRINT '             MIGRATION 015 AUDIT & BACKFILL REPORT                 ';
PRINT '===================================================================';
PRINT 'Total Homestay Listings in Database: ' + CAST(@TotalHomestaysCount AS NVARCHAR(10));
PRINT '-------------------------------------------------------------------';
PRINT '[A] Pre-existing Consistent Links:      ' + CAST(@AlreadyConsistentCount AS NVARCHAR(10));
PRINT '[B] Newly Backfilled Homestays:         ' + CAST(@NewlyBackfilledCount AS NVARCHAR(10));
PRINT '[C] Admin-Created Listings (NULL FK):   ' + CAST(@PreservedAdminCount AS NVARCHAR(10));
PRINT '[D] Flagged - Non-Homestay Providers:   ' + CAST(@MismatchedTypeCount AS NVARCHAR(10));
PRINT '[E] Flagged - Unapproved/Inactive:      ' + CAST(@UnapprovedOrInactiveStatusCount AS NVARCHAR(10));
PRINT '[F] Flagged - Inconsistent FK Links:    ' + CAST(@InconsistentMismatchedLinks AS NVARCHAR(10));
PRINT '[G] Flagged - Unmatched / Orphans:      ' + CAST(@UnmatchedOrphanCount AS NVARCHAR(10));
PRINT '-------------------------------------------------------------------';
PRINT 'Verification Sum ([A]+[B]+[C]+[D]+[E]+[F]+[G]): ' +
      CAST((@AlreadyConsistentCount + @NewlyBackfilledCount + @PreservedAdminCount + @MismatchedTypeCount + @UnapprovedOrInactiveStatusCount + @InconsistentMismatchedLinks + @UnmatchedOrphanCount) AS NVARCHAR(10));
PRINT '===================================================================';
GO
