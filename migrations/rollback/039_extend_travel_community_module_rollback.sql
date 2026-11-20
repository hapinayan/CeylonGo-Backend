-- Rollback Script: 039_extend_travel_community_module_rollback.sql
-- Description: Reverses all schema changes applied by 039_extend_travel_community_module.sql
-- Module: Module 06 (Travel Community & Social Feed)
-- Target Database: Microsoft SQL Server (CeylonGoDB / CeylonGoDB_Community_Test)
-- Safe & Idempotent: Can be executed multiple times safely without error.
--
-- IMPORTANT DEPLOYMENT & POLICY NOTES:
-- 1. Application code MUST be rolled back to the pre-Migration-039 revision BEFORE
--    executing this script; otherwise running application instances referencing the
--    new ORM columns will fail with SQL Server Error 207 (Invalid column name).
-- 2. Take a verified full database backup before executing any rollback in staging/prod.
-- 3. This script intentionally does NOT delete version '039' from dbo.schema_migrations
--    outside the established migration runner policy. History reconciliation or
--    backup restore is handled by the Database Coordinator.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Preflight Data-Loss Protection Guards (Dynamic SQL for Re-Run Safety)
-- Aborts BEFORE dropping any object if non-default Migration 039 data would be lost.
-- Using sp_executesql prevents SQL Server compile-time Error 207 on idempotent re-runs
-- after columns/tables have already been removed.
-- =============================================================================
IF OBJECT_ID('dbo.community_user_profiles', 'U') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM dbo.community_user_profiles
            WHERE is_community_suspended = 1
               OR suspended_at IS NOT NULL
               OR suspended_by IS NOT NULL
               OR suspension_reason IS NOT NULL
        )
            THROW 50391, ''Cannot rollback Migration 039 while community suspension records exist in dbo.community_user_profiles. Archive or clear them first.'', 1;
    ';
END;

IF COL_LENGTH('dbo.community_posts', 'listing_reference_id') IS NOT NULL
   OR COL_LENGTH('dbo.community_posts', 'is_promotional') IS NOT NULL
   OR COL_LENGTH('dbo.community_posts', 'is_featured') IS NOT NULL
BEGIN
    IF COL_LENGTH('dbo.community_posts', 'listing_reference_id') IS NOT NULL
    BEGIN
        EXEC sp_executesql N'
            IF EXISTS (
                SELECT 1 FROM dbo.community_posts
                WHERE listing_reference_id IS NOT NULL
            )
                THROW 50392, ''Cannot rollback Migration 039 while promotional listing links exist in dbo.community_posts.listing_reference_id. Archive or unlink them first.'', 1;
        ';
    END;

    IF COL_LENGTH('dbo.community_posts', 'is_promotional') IS NOT NULL
    BEGIN
        EXEC sp_executesql N'
            IF EXISTS (
                SELECT 1 FROM dbo.community_posts
                WHERE is_promotional = 1
            )
                THROW 50393, ''Cannot rollback Migration 039 while promotional posts exist (is_promotional = 1). Archive or reset them first.'', 1;
        ';
    END;

    IF COL_LENGTH('dbo.community_posts', 'is_featured') IS NOT NULL
    BEGIN
        EXEC sp_executesql N'
            IF EXISTS (
                SELECT 1 FROM dbo.community_posts
                WHERE is_featured = 1
            )
                THROW 50394, ''Cannot rollback Migration 039 while featured posts exist (is_featured = 1). Archive or unfeature them first.'', 1;
        ';
    END;
END;

IF COL_LENGTH('dbo.community_posts', 'moderation_status') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM dbo.community_posts
            WHERE moderation_status <> N''active''
        )
            THROW 50395, ''Cannot rollback Migration 039 while admin-hidden or author-removed posts exist in dbo.community_posts.moderation_status. Archive or resolve them first.'', 1;
    ';
END;

IF COL_LENGTH('dbo.community_hashtags', 'is_blocked') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM dbo.community_hashtags
            WHERE is_blocked = 1
        )
            THROW 50396, ''Cannot rollback Migration 039 while blocked hashtags exist (is_blocked = 1). Archive or unblock them first.'', 1;
    ';
END;

IF COL_LENGTH('dbo.community_hashtags', 'is_featured') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM dbo.community_hashtags
            WHERE is_featured = 1
        )
            THROW 50397, ''Cannot rollback Migration 039 while featured hashtags exist (is_featured = 1). Archive or unfeature them first.'', 1;
    ';
END;

IF COL_LENGTH('dbo.community_comments', 'status') IS NOT NULL
BEGIN
    EXEC sp_executesql N'
        IF EXISTS (
            SELECT 1 FROM dbo.community_comments
            WHERE status <> N''active''
        )
            THROW 50398, ''Cannot rollback Migration 039 while moderated or author-deleted comment statuses exist in dbo.community_comments.status. Archive or reset them first.'', 1;
    ';
END;
GO

-- =============================================================================
-- Batch 2: Rollback dbo.community_comments Changes
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_comments_status'
      AND parent_object_id = OBJECT_ID('dbo.community_comments')
)
BEGIN
    ALTER TABLE dbo.community_comments DROP CONSTRAINT CK_community_comments_status;
END;

IF OBJECT_ID('dbo.DF_community_comments_status', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_comments DROP CONSTRAINT DF_community_comments_status;
END;
GO

IF COL_LENGTH('dbo.community_comments', 'status') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_comments DROP COLUMN status;
END;
GO

-- =============================================================================
-- Batch 3: Rollback dbo.community_hashtags Changes
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_hashtags_featured'
      AND object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    DROP INDEX IX_community_hashtags_featured ON dbo.community_hashtags;
END;

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_hashtags_blocked'
      AND object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    DROP INDEX IX_community_hashtags_blocked ON dbo.community_hashtags;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_hashtags_block_feature'
      AND parent_object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    ALTER TABLE dbo.community_hashtags DROP CONSTRAINT CK_community_hashtags_block_feature;
END;

IF OBJECT_ID('dbo.DF_community_hashtags_featured', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_hashtags DROP CONSTRAINT DF_community_hashtags_featured;
END;

IF OBJECT_ID('dbo.DF_community_hashtags_blocked', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_hashtags DROP CONSTRAINT DF_community_hashtags_blocked;
END;
GO

IF COL_LENGTH('dbo.community_hashtags', 'is_featured') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_hashtags DROP COLUMN is_featured;
END;

IF COL_LENGTH('dbo.community_hashtags', 'is_blocked') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_hashtags DROP COLUMN is_blocked;
END;
GO

-- =============================================================================
-- Batch 4: Rollback dbo.community_user_profiles Table & Index
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_user_profiles_suspended'
      AND object_id = OBJECT_ID('dbo.community_user_profiles')
)
BEGIN
    DROP INDEX IX_community_user_profiles_suspended ON dbo.community_user_profiles;
END;
GO

IF OBJECT_ID('dbo.community_user_profiles', 'U') IS NOT NULL
BEGIN
    DROP TABLE dbo.community_user_profiles;
END;
GO

-- =============================================================================
-- Batch 5: Rollback dbo.community_posts Indexes, FK, Constraints & Columns
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_featured_feed'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    DROP INDEX IX_community_posts_featured_feed ON dbo.community_posts;
END;

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_promotional_feed'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    DROP INDEX IX_community_posts_promotional_feed ON dbo.community_posts;
END;

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_listing_ref'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    DROP INDEX IX_community_posts_listing_ref ON dbo.community_posts;
END;

IF EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_community_posts_listing_reference'
      AND parent_object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    ALTER TABLE dbo.community_posts DROP CONSTRAINT FK_community_posts_listing_reference;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_posts_mod_status'
      AND parent_object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    ALTER TABLE dbo.community_posts DROP CONSTRAINT CK_community_posts_mod_status;
END;

IF OBJECT_ID('dbo.DF_community_posts_mod_status', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP CONSTRAINT DF_community_posts_mod_status;
END;

IF OBJECT_ID('dbo.DF_community_posts_featured', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP CONSTRAINT DF_community_posts_featured;
END;

IF OBJECT_ID('dbo.DF_community_posts_promotional', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP CONSTRAINT DF_community_posts_promotional;
END;
GO

IF COL_LENGTH('dbo.community_posts', 'moderation_status') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP COLUMN moderation_status;
END;

IF COL_LENGTH('dbo.community_posts', 'is_featured') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP COLUMN is_featured;
END;

IF COL_LENGTH('dbo.community_posts', 'is_promotional') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP COLUMN is_promotional;
END;

IF COL_LENGTH('dbo.community_posts', 'listing_reference_id') IS NOT NULL
BEGIN
    ALTER TABLE dbo.community_posts DROP COLUMN listing_reference_id;
END;
GO

PRINT 'Rollback 039_extend_travel_community_module_rollback completed successfully.';
GO
