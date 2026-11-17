-- =============================================================================
-- Migration: 010_m04_create_eco_promotions.sql
-- Module: 04 (Eco-Tourism Backend) - Phase P1.1A: Sponsored Promotions Foundation
-- Database: CeylonGoDB (Microsoft SQL Server / SQL Server Express)
-- Prerequisite Tables: dbo.users, dbo.provider_profiles, dbo.eco_experiences
-- =============================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    -- =========================================================================
    -- 1. Table: dbo.eco_promotion_packages
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_promotion_packages' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_promotion_packages (
            id INT IDENTITY(1,1) NOT NULL,
            name NVARCHAR(100) NOT NULL,
            description NVARCHAR(500) NULL,
            duration_days INT NOT NULL,
            price DECIMAL(12, 2) NOT NULL,
            currency NVARCHAR(10) NOT NULL CONSTRAINT DF_eco_promo_pkg_currency DEFAULT N'LKR',
            placement_tier NVARCHAR(30) NOT NULL CONSTRAINT DF_eco_promo_pkg_tier DEFAULT N'standard',
            is_active BIT NOT NULL CONSTRAINT DF_eco_promo_pkg_active DEFAULT 1,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_eco_promo_pkg_created DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2 NOT NULL CONSTRAINT DF_eco_promo_pkg_updated DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_promotion_packages PRIMARY KEY CLUSTERED (id),
            CONSTRAINT UQ_eco_promotion_packages_name UNIQUE NONCLUSTERED (name),
            CONSTRAINT CK_eco_promo_pkg_days CHECK (duration_days > 0),
            CONSTRAINT CK_eco_promo_pkg_price CHECK (price >= 0.0),
            CONSTRAINT CK_eco_promo_pkg_tier CHECK (placement_tier IN (N'standard', N'featured', N'spotlight'))
        );

        CREATE NONCLUSTERED INDEX IX_eco_promotion_packages_active
            ON dbo.eco_promotion_packages (is_active)
            INCLUDE (name, duration_days, price, placement_tier);

        PRINT 'Table dbo.eco_promotion_packages created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_promotion_packages already exists. Skipping creation.';
    END;

    -- =========================================================================
    -- 1.1 Seed Approved Default Packages (Duplicate-Safe / Idempotent)
    -- =========================================================================
    IF NOT EXISTS (SELECT 1 FROM dbo.eco_promotion_packages WHERE name = N'Starter Spotlight')
    BEGIN
        INSERT INTO dbo.eco_promotion_packages (name, description, duration_days, price, currency, placement_tier, is_active)
        VALUES (N'Starter Spotlight', N'7-day standard boosted visibility in public discovery.', 7, 2500.00, N'LKR', N'standard', 1);
        PRINT 'Seeded package: Starter Spotlight.';
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.eco_promotion_packages WHERE name = N'Growth Boost')
    BEGIN
        INSERT INTO dbo.eco_promotion_packages (name, description, duration_days, price, currency, placement_tier, is_active)
        VALUES (N'Growth Boost', N'14-day featured placement across discovery and search filters.', 14, 4500.00, N'LKR', N'featured', 1);
        PRINT 'Seeded package: Growth Boost.';
    END;

    IF NOT EXISTS (SELECT 1 FROM dbo.eco_promotion_packages WHERE name = N'Premier Showcase')
    BEGIN
        INSERT INTO dbo.eco_promotion_packages (name, description, duration_days, price, currency, placement_tier, is_active)
        VALUES (N'Premier Showcase', N'30-day top-tier spotlight visibility with priority badge labeling.', 30, 8500.00, N'LKR', N'spotlight', 1);
        PRINT 'Seeded package: Premier Showcase.';
    END;

    -- =========================================================================
    -- 2. Table: dbo.eco_sponsored_promotions
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_sponsored_promotions' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_sponsored_promotions (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            provider_profile_id INT NOT NULL,
            package_id INT NOT NULL,

            -- Immutable Package Snapshot at time of booking
            package_price_snapshot DECIMAL(12, 2) NOT NULL,
            currency_snapshot NVARCHAR(10) NOT NULL CONSTRAINT DF_eco_spon_currency DEFAULT N'LKR',
            duration_days_snapshot INT NOT NULL,

            -- Status and Lifecycle
            campaign_status NVARCHAR(30) NOT NULL CONSTRAINT DF_eco_spon_status DEFAULT N'pending_approval',
            payment_status NVARCHAR(30) NOT NULL CONSTRAINT DF_eco_spon_pay_status DEFAULT N'unpaid',
            payment_reference NVARCHAR(100) NULL,

            -- Scheduled Validity Window
            starts_at DATETIME2 NULL,
            ends_at DATETIME2 NULL,

            -- Review & Audit
            requested_at DATETIME2 NOT NULL CONSTRAINT DF_eco_spon_requested DEFAULT SYSUTCDATETIME(),
            reviewed_at DATETIME2 NULL,
            reviewed_by INT NULL,
            review_reason NVARCHAR(1000) NULL,

            created_at DATETIME2 NOT NULL CONSTRAINT DF_eco_spon_created DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2 NOT NULL CONSTRAINT DF_eco_spon_updated DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_sponsored_promotions PRIMARY KEY CLUSTERED (id),
            CONSTRAINT FK_eco_spon_experience FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id) ON DELETE CASCADE,
            CONSTRAINT FK_eco_spon_provider FOREIGN KEY (provider_profile_id)
                REFERENCES dbo.provider_profiles (id) ON DELETE NO ACTION,
            CONSTRAINT FK_eco_spon_package FOREIGN KEY (package_id)
                REFERENCES dbo.eco_promotion_packages (id) ON DELETE NO ACTION,
            CONSTRAINT FK_eco_spon_reviewer FOREIGN KEY (reviewed_by)
                REFERENCES dbo.users (id) ON DELETE SET NULL,

            CONSTRAINT CK_eco_spon_price CHECK (package_price_snapshot >= 0.0),
            CONSTRAINT CK_eco_spon_days CHECK (duration_days_snapshot > 0),
            CONSTRAINT CK_eco_spon_status CHECK (campaign_status IN (
                N'pending_approval', N'approved', N'active', N'rejected', N'expired', N'cancelled'
            )),
            CONSTRAINT CK_eco_spon_payment CHECK (payment_status IN (
                N'unpaid', N'pending_verification', N'paid', N'failed', N'refunded'
            )),
            CONSTRAINT CK_eco_spon_dates CHECK (
                ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at
            ),
            CONSTRAINT CK_eco_spon_active_consistent CHECK (
                campaign_status <> N'active' OR (starts_at IS NOT NULL AND ends_at IS NOT NULL AND payment_status = N'paid')
            )
        );

        CREATE NONCLUSTERED INDEX IX_eco_sponsored_active_window
            ON dbo.eco_sponsored_promotions (campaign_status, starts_at, ends_at)
            INCLUDE (eco_experience_id);

        CREATE NONCLUSTERED INDEX IX_eco_sponsored_experience_status
            ON dbo.eco_sponsored_promotions (eco_experience_id, campaign_status);

        CREATE NONCLUSTERED INDEX IX_eco_sponsored_provider
            ON dbo.eco_sponsored_promotions (provider_profile_id, created_at DESC);

        PRINT 'Table dbo.eco_sponsored_promotions created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_sponsored_promotions already exists. Skipping creation.';
    END;

    -- =========================================================================
    -- 3. Table: dbo.eco_promotion_analytics
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_promotion_analytics' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_promotion_analytics (
            id INT IDENTITY(1,1) NOT NULL,
            promotion_id INT NOT NULL,
            eco_experience_id INT NOT NULL,
            metric_date DATE NOT NULL,
            impressions_count INT NOT NULL CONSTRAINT DF_eco_promo_impressions DEFAULT 0,
            clicks_count INT NOT NULL CONSTRAINT DF_eco_promo_clicks DEFAULT 0,
            created_at DATETIME2 NOT NULL CONSTRAINT DF_eco_promo_analytics_created DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2 NOT NULL CONSTRAINT DF_eco_promo_analytics_updated DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_promotion_analytics PRIMARY KEY CLUSTERED (id),
            CONSTRAINT FK_eco_analytics_promotion FOREIGN KEY (promotion_id)
                REFERENCES dbo.eco_sponsored_promotions (id) ON DELETE CASCADE,
            CONSTRAINT FK_eco_analytics_experience FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id) ON DELETE NO ACTION,
            CONSTRAINT UQ_eco_promotion_analytics UNIQUE NONCLUSTERED (promotion_id, metric_date),
            CONSTRAINT CK_eco_promo_counts CHECK (impressions_count >= 0 AND clicks_count >= 0)
        );

        CREATE NONCLUSTERED INDEX IX_eco_promotion_analytics_experience
            ON dbo.eco_promotion_analytics (eco_experience_id, metric_date DESC)
            INCLUDE (impressions_count, clicks_count);

        PRINT 'Table dbo.eco_promotion_analytics created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_promotion_analytics already exists. Skipping creation.';
    END;

    COMMIT TRANSACTION;
    PRINT 'Migration 010_m04_create_eco_promotions.sql completed successfully.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
        ROLLBACK TRANSACTION;

    DECLARE @ErrorMessage NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrorSeverity INT = ERROR_SEVERITY();
    DECLARE @ErrorState INT = ERROR_STATE();
    RAISERROR(@ErrorMessage, @ErrorSeverity, @ErrorState);
END CATCH;
GO
