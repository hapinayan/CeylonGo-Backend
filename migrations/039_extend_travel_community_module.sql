-- Migration: 039_extend_travel_community_module.sql
-- Description: Travel Community & Social Feed Module 06 Extensions
--              (Provider Promotional Listing Links, Featured Posts, Post Moderation Status,
--               Community-Only User Suspension Profiles, Hashtag Block/Feature Controls &
--               Structured Comment Status with Idempotent Placeholder Backfill)
-- Module: Module 06 (Travel Community & Social Feed)
-- Target Database: Microsoft SQL Server (CeylonGoDB / CeylonGoDB_Community_Test)
-- Prerequisites: 027_create_shared_infrastructure.sql, 028_create_travel_community_module.sql,
--                037_extend_artisan_reservation_rejection.sql,
--                038_create_artisan_product_favorites.sql
-- Safe & Idempotent: Can be executed multiple times safely without data loss.
-- Existing Data Compatibility: All new columns are nullable or NOT NULL with explicit defaults.
-- Security Note: Community-only suspension is isolated in dbo.community_user_profiles;
--                dbo.users.is_active is intentionally never altered.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Extend dbo.community_posts (Columns & Named Defaults)
-- =============================================================================
IF COL_LENGTH('dbo.community_posts', 'listing_reference_id') IS NULL
BEGIN
    ALTER TABLE dbo.community_posts
    ADD listing_reference_id INT NULL;
END;

IF COL_LENGTH('dbo.community_posts', 'is_promotional') IS NULL
BEGIN
    ALTER TABLE dbo.community_posts
    ADD is_promotional BIT NOT NULL
        CONSTRAINT DF_community_posts_promotional DEFAULT 0;
END;

IF COL_LENGTH('dbo.community_posts', 'is_featured') IS NULL
BEGIN
    ALTER TABLE dbo.community_posts
    ADD is_featured BIT NOT NULL
        CONSTRAINT DF_community_posts_featured DEFAULT 0;
END;

IF COL_LENGTH('dbo.community_posts', 'moderation_status') IS NULL
BEGIN
    ALTER TABLE dbo.community_posts
    ADD moderation_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_community_posts_mod_status DEFAULT N'active';
END;
GO

-- =============================================================================
-- Batch 2: Add dbo.community_posts FK, CHECK Constraint & Filtered Indexes
-- Note: ON DELETE SET NULL on listing_reference_id matches Migrations 029 & 030
--       (travel_plan_items & promotion_campaigns) and avoids blocking other modules;
--       live SQL Server cascade-path verification runs on CeylonGoDB_Community_Test.
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_community_posts_listing_reference'
      AND parent_object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    ALTER TABLE dbo.community_posts
    WITH CHECK ADD CONSTRAINT FK_community_posts_listing_reference
        FOREIGN KEY (listing_reference_id)
        REFERENCES dbo.listing_references(id)
        ON DELETE SET NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_posts_mod_status'
      AND parent_object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    ALTER TABLE dbo.community_posts
    WITH CHECK ADD CONSTRAINT CK_community_posts_mod_status
        CHECK (moderation_status IN (N'active', N'hidden_by_admin', N'removed_by_author'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_listing_ref'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_posts_listing_ref
        ON dbo.community_posts(listing_reference_id)
        WHERE listing_reference_id IS NOT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_promotional_feed'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_posts_promotional_feed
        ON dbo.community_posts(created_at DESC, id DESC)
        WHERE is_promotional = 1 AND is_published = 1;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_posts_featured_feed'
      AND object_id = OBJECT_ID('dbo.community_posts')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_posts_featured_feed
        ON dbo.community_posts(created_at DESC, id DESC)
        WHERE is_featured = 1 AND is_published = 1;
END;
GO

-- =============================================================================
-- Batch 3: Create dbo.community_user_profiles (Community-Only Suspension)
-- Note: Both FKs referencing dbo.users(id) use ON DELETE NO ACTION to prevent
--       SQL Server multiple cascade path conflicts (Error 1785).
-- =============================================================================
IF OBJECT_ID('dbo.community_user_profiles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.community_user_profiles (
        user_id INT NOT NULL,
        is_community_suspended BIT NOT NULL
            CONSTRAINT DF_community_user_profiles_suspended DEFAULT 0,
        suspended_until DATETIME2 NULL,
        suspension_reason NVARCHAR(500) NULL,
        suspended_by INT NULL,
        suspended_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL
            CONSTRAINT DF_community_user_profiles_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NULL,

        CONSTRAINT PK_community_user_profiles PRIMARY KEY CLUSTERED (user_id),

        CONSTRAINT FK_community_user_profiles_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_community_user_profiles_suspended_by
            FOREIGN KEY (suspended_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_community_user_profiles_suspension
            CHECK (
                is_community_suspended = 0
                OR (
                    suspended_by IS NOT NULL
                    AND suspended_at IS NOT NULL
                    AND suspension_reason IS NOT NULL
                    AND LEN(LTRIM(RTRIM(suspension_reason))) >= 3
                )
            ),

        CONSTRAINT CK_community_user_profiles_until
            CHECK (
                suspended_until IS NULL
                OR suspended_at IS NULL
                OR suspended_until > suspended_at
            )
    );

    PRINT 'community_user_profiles table created.';
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_user_profiles_suspended'
      AND object_id = OBJECT_ID('dbo.community_user_profiles')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_user_profiles_suspended
        ON dbo.community_user_profiles(is_community_suspended, suspended_until)
        WHERE is_community_suspended = 1;
END;
GO

-- =============================================================================
-- Batch 4: Extend dbo.community_hashtags (Columns & Named Defaults)
-- =============================================================================
IF COL_LENGTH('dbo.community_hashtags', 'is_blocked') IS NULL
BEGIN
    ALTER TABLE dbo.community_hashtags
    ADD is_blocked BIT NOT NULL
        CONSTRAINT DF_community_hashtags_blocked DEFAULT 0;
END;

IF COL_LENGTH('dbo.community_hashtags', 'is_featured') IS NULL
BEGIN
    ALTER TABLE dbo.community_hashtags
    ADD is_featured BIT NOT NULL
        CONSTRAINT DF_community_hashtags_featured DEFAULT 0;
END;
GO

-- =============================================================================
-- Batch 5: Add dbo.community_hashtags CHECK Constraint & Filtered Indexes
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_hashtags_block_feature'
      AND parent_object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    ALTER TABLE dbo.community_hashtags
    WITH CHECK ADD CONSTRAINT CK_community_hashtags_block_feature
        CHECK (NOT (is_blocked = 1 AND is_featured = 1));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_hashtags_blocked'
      AND object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_hashtags_blocked
        ON dbo.community_hashtags(is_blocked)
        WHERE is_blocked = 1;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_community_hashtags_featured'
      AND object_id = OBJECT_ID('dbo.community_hashtags')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_community_hashtags_featured
        ON dbo.community_hashtags(is_featured, usage_count DESC)
        WHERE is_featured = 1 AND is_blocked = 0;
END;
GO

-- =============================================================================
-- Batch 6: Extend dbo.community_comments (Structured Moderation Status Column)
-- =============================================================================
IF COL_LENGTH('dbo.community_comments', 'status') IS NULL
BEGIN
    ALTER TABLE dbo.community_comments
    ADD status NVARCHAR(30) NOT NULL
        CONSTRAINT DF_community_comments_status DEFAULT N'active';
END;
GO

-- =============================================================================
-- Batch 7: Add dbo.community_comments CHECK Constraint & Idempotent Backfill
-- Exact placeholder strings verified in app/services/community_engagement.py:
--   REDACTED_BY_AUTHOR_TEXT = "[Comment deleted by author]"
--   REDACTED_BY_ADMIN_TEXT  = "[Comment removed by moderator]"
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_community_comments_status'
      AND parent_object_id = OBJECT_ID('dbo.community_comments')
)
BEGIN
    ALTER TABLE dbo.community_comments
    WITH CHECK ADD CONSTRAINT CK_community_comments_status
        CHECK (status IN (N'active', N'deleted_by_author', N'removed_by_moderator'));
END;

IF COL_LENGTH('dbo.community_comments', 'status') IS NOT NULL
BEGIN
    UPDATE dbo.community_comments
    SET status = N'deleted_by_author'
    WHERE status = N'active'
      AND content = N'[Comment deleted by author]';

    UPDATE dbo.community_comments
    SET status = N'removed_by_moderator'
    WHERE status = N'active'
      AND content = N'[Comment removed by moderator]';
END;
GO

PRINT 'Migration 039_extend_travel_community_module applied successfully.';
GO
