-- Migration: 008_m04_create_eco_tourism_core.sql
-- Module: M04 Eco-Tourism Core Entities (P0.1)
-- Description: Create eco_experiences, eco_experience_images, eco_safety_rules,
--              eco_season_restrictions, eco_practices, eco_evidences, and eco_verifications
-- Target Database: CeylonGoDB (Microsoft SQL Server / SQL Server Express compatible)
-- Safety: Fully transactional, atomic rollback protection, idempotent, non-destructive.
-- Prerequisite Tables: dbo.users, dbo.provider_profiles, dbo.homestays

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    -- =========================================================================
    -- 1. CREATE dbo.eco_experiences
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_experiences' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_experiences (
            id INT IDENTITY(1,1) NOT NULL,
            provider_profile_id INT NOT NULL,
            linked_homestay_id INT NULL,

            -- Core Listing Details
            title NVARCHAR(150) NOT NULL,
            eco_category NVARCHAR(60) NOT NULL,
            description NVARCHAR(MAX) NULL,
            itinerary NVARCHAR(MAX) NULL,
            duration_hours DECIMAL(5, 2) NOT NULL,

            -- Location & Maps
            district NVARCHAR(100) NOT NULL,
            city NVARCHAR(100) NOT NULL,
            address NVARCHAR(255) NULL,
            meeting_point NVARCHAR(500) NOT NULL,
            directions_info NVARCHAR(1000) NULL,
            latitude DECIMAL(9, 6) NULL,
            longitude DECIMAL(9, 6) NULL,

            -- Pricing & Environmental Group Size Limits
            pricing_type NVARCHAR(20) NOT NULL
                CONSTRAINT DF_eco_experiences_pricing_type DEFAULT N'per_person',
            base_price DECIMAL(12, 2) NOT NULL,
            currency NVARCHAR(10) NOT NULL
                CONSTRAINT DF_eco_experiences_currency DEFAULT N'LKR',
            max_group_size INT NOT NULL,
            min_participants INT NOT NULL
                CONSTRAINT DF_eco_experiences_min_participants DEFAULT (1),
            min_age INT NULL,

            -- Eco-Specific Guidelines, Wildlife Safety & Conservation Contribution
            environmental_guidelines NVARCHAR(2000) NOT NULL,
            wildlife_safety_summary NVARCHAR(2000) NOT NULL,
            conservation_factual_contribution NVARCHAR(1000) NULL,
            conservation_marketing_statement NVARCHAR(1000) NULL,
            protected_area_permit_required BIT NOT NULL
                CONSTRAINT DF_eco_experiences_permit_req DEFAULT (0),
            certified_guide_required BIT NOT NULL
                CONSTRAINT DF_eco_experiences_guide_req DEFAULT (0),
            permit_details NVARCHAR(500) NULL,
            cancellation_policy NVARCHAR(1000) NULL,
            free_cancellation_hours INT NOT NULL
                CONSTRAINT DF_eco_experiences_cancel_hours DEFAULT (24),

            -- Listing Lifecycle & Admin Moderation State
            listing_status NVARCHAR(30) NOT NULL
                CONSTRAINT DF_eco_experiences_listing_status DEFAULT N'draft',
            is_published BIT NOT NULL
                CONSTRAINT DF_eco_experiences_is_published DEFAULT (0),
            approval_reviewed_by INT NULL,
            approval_reviewed_at DATETIME2 NULL,
            approval_rejection_reason NVARCHAR(500) NULL,

            -- Eco-Friendly Badge Status (never automatically granted)
            eco_badge_status NVARCHAR(30) NOT NULL
                CONSTRAINT DF_eco_experiences_badge_status DEFAULT N'none',

            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_experiences_created_at DEFAULT SYSUTCDATETIME(),
            updated_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_experiences_updated_at DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_experiences
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_experiences_provider_profile
                FOREIGN KEY (provider_profile_id)
                REFERENCES dbo.provider_profiles (id)
                ON DELETE NO ACTION,

            CONSTRAINT FK_eco_experiences_linked_homestay
                FOREIGN KEY (linked_homestay_id)
                REFERENCES dbo.homestays (id)
                ON DELETE NO ACTION,

            CONSTRAINT FK_eco_experiences_approval_reviewer
                FOREIGN KEY (approval_reviewed_by)
                REFERENCES dbo.users (id)
                ON DELETE NO ACTION,

            CONSTRAINT CK_eco_experiences_category
                CHECK (eco_category IN (
                    N'Forest & Nature Trails',
                    N'Wildlife & Birdwatching',
                    N'Sustainable Village Experiences',
                    N'Conservation Activities',
                    N'Eco-Friendly Stays',
                    N'Community Eco-Tourism'
                )),

            CONSTRAINT CK_eco_experiences_pricing_type
                CHECK (pricing_type IN (N'per_person', N'per_group')),

            CONSTRAINT CK_eco_experiences_base_price
                CHECK (base_price >= 0),

            CONSTRAINT CK_eco_experiences_duration_hours
                CHECK (duration_hours > 0),

            CONSTRAINT CK_eco_experiences_max_group_size
                CHECK (max_group_size > 0 AND max_group_size <= 50),

            CONSTRAINT CK_eco_experiences_min_participants
                CHECK (min_participants > 0 AND min_participants <= max_group_size),

            CONSTRAINT CK_eco_experiences_min_age
                CHECK (min_age IS NULL OR (min_age >= 0 AND min_age <= 100)),

            CONSTRAINT CK_eco_experiences_latitude
                CHECK (latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0)),

            CONSTRAINT CK_eco_experiences_longitude
                CHECK (longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0)),

            CONSTRAINT CK_eco_experiences_listing_status
                CHECK (listing_status IN (
                    N'draft',
                    N'pending_approval',
                    N'approved',
                    N'rejected',
                    N'deactivated'
                )),

            CONSTRAINT CK_eco_experiences_badge_status
                CHECK (eco_badge_status IN (
                    N'none',
                    N'pending',
                    N'granted',
                    N'rejected',
                    N'revoked'
                )),

            CONSTRAINT CK_eco_experiences_cancellation_hours
                CHECK (free_cancellation_hours >= 0)
        );

        CREATE NONCLUSTERED INDEX IX_eco_experiences_provider_profile_id
            ON dbo.eco_experiences (provider_profile_id);

        CREATE NONCLUSTERED INDEX IX_eco_experiences_linked_homestay_id
            ON dbo.eco_experiences (linked_homestay_id)
            WHERE linked_homestay_id IS NOT NULL;

        CREATE NONCLUSTERED INDEX IX_eco_experiences_discovery
            ON dbo.eco_experiences (listing_status, is_published, eco_category, district);

        CREATE NONCLUSTERED INDEX IX_eco_experiences_badge_status
            ON dbo.eco_experiences (eco_badge_status);

        PRINT 'Table dbo.eco_experiences created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_experiences already exists. Skipping.';
    END

    -- =========================================================================
    -- 2. CREATE dbo.eco_experience_images
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_experience_images' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_experience_images (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            image_url NVARCHAR(1000) NOT NULL,
            caption NVARCHAR(255) NULL,
            is_primary BIT NOT NULL
                CONSTRAINT DF_eco_experience_images_primary DEFAULT (0),
            sort_order INT NOT NULL
                CONSTRAINT DF_eco_experience_images_sort DEFAULT (0),
            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_experience_images_created DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_experience_images
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_experience_images_experience
                FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id)
                ON DELETE CASCADE,

            CONSTRAINT CK_eco_experience_images_sort_order
                CHECK (sort_order >= 0)
        );

        CREATE NONCLUSTERED INDEX IX_eco_experience_images_eco_experience_id
            ON dbo.eco_experience_images (eco_experience_id, sort_order);

        PRINT 'Table dbo.eco_experience_images created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_experience_images already exists. Skipping.';
    END

    -- =========================================================================
    -- 3. CREATE dbo.eco_safety_rules
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_safety_rules' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_safety_rules (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            rule_type NVARCHAR(50) NOT NULL,
            title NVARCHAR(150) NOT NULL,
            description NVARCHAR(1000) NOT NULL,
            is_mandatory BIT NOT NULL
                CONSTRAINT DF_eco_safety_rules_mandatory DEFAULT (1),
            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_safety_rules_created DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_safety_rules
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_safety_rules_experience
                FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id)
                ON DELETE CASCADE,

            CONSTRAINT CK_eco_safety_rules_type
                CHECK (rule_type IN (
                    N'wildlife_no_feeding',
                    N'environmental_conduct',
                    N'operator_instruction',
                    N'equipment_requirement',
                    N'health_age_restriction'
                ))
        );

        CREATE NONCLUSTERED INDEX IX_eco_safety_rules_eco_experience_id
            ON dbo.eco_safety_rules (eco_experience_id);

        PRINT 'Table dbo.eco_safety_rules created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_safety_rules already exists. Skipping.';
    END

    -- =========================================================================
    -- 4. CREATE dbo.eco_season_restrictions
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_season_restrictions' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_season_restrictions (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            restriction_type NVARCHAR(40) NOT NULL,
            reason NVARCHAR(500) NOT NULL,
            is_active BIT NOT NULL
                CONSTRAINT DF_eco_season_restrictions_active DEFAULT (1),
            created_by INT NULL,
            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_season_restrictions_created DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_season_restrictions
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_season_restrictions_experience
                FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id)
                ON DELETE CASCADE,

            CONSTRAINT FK_eco_season_restrictions_creator
                FOREIGN KEY (created_by)
                REFERENCES dbo.users (id)
                ON DELETE NO ACTION,

            CONSTRAINT CK_eco_season_restrictions_dates
                CHECK (end_date >= start_date),

            CONSTRAINT CK_eco_season_restrictions_type
                CHECK (restriction_type IN (
                    N'monsoon_season',
                    N'wildlife_breeding',
                    N'weather_alert',
                    N'park_closure'
                ))
        );

        CREATE NONCLUSTERED INDEX IX_eco_season_restrictions_experience_dates
            ON dbo.eco_season_restrictions (eco_experience_id, is_active, start_date, end_date);

        PRINT 'Table dbo.eco_season_restrictions created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_season_restrictions already exists. Skipping.';
    END

    -- =========================================================================
    -- 5. CREATE dbo.eco_practices
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_practices' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_practices (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            practice_category NVARCHAR(60) NOT NULL,
            claim_description NVARCHAR(1000) NOT NULL,
            is_verified BIT NOT NULL
                CONSTRAINT DF_eco_practices_verified DEFAULT (0),
            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_practices_created DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_practices
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_practices_experience
                FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id)
                ON DELETE CASCADE,

            CONSTRAINT CK_eco_practices_category
                CHECK (practice_category IN (
                    N'waste_reduction',
                    N'renewable_energy',
                    N'community_employment',
                    N'wildlife_protection',
                    N'habitat_restoration',
                    N'sustainable_sourcing'
                ))
        );

        CREATE NONCLUSTERED INDEX IX_eco_practices_eco_experience_id
            ON dbo.eco_practices (eco_experience_id);

        PRINT 'Table dbo.eco_practices created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_practices already exists. Skipping.';
    END

    -- =========================================================================
    -- 6. CREATE dbo.eco_evidences
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_evidences' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_evidences (
            id INT IDENTITY(1,1) NOT NULL,
            eco_practice_id INT NOT NULL,
            file_reference NVARCHAR(255) NOT NULL,
            document_type NVARCHAR(50) NOT NULL,
            notes NVARCHAR(500) NULL,
            uploaded_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_evidences_uploaded DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_evidences
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_evidences_practice
                FOREIGN KEY (eco_practice_id)
                REFERENCES dbo.eco_practices (id)
                ON DELETE CASCADE,

            CONSTRAINT CK_eco_evidences_document_type
                CHECK (document_type IN (
                    N'certificate',
                    N'permit',
                    N'photo_proof',
                    N'audit_report'
                ))
        );

        CREATE NONCLUSTERED INDEX IX_eco_evidences_eco_practice_id
            ON dbo.eco_evidences (eco_practice_id);

        PRINT 'Table dbo.eco_evidences created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_evidences already exists. Skipping.';
    END

    -- =========================================================================
    -- 7. CREATE dbo.eco_verifications
    -- =========================================================================
    IF NOT EXISTS (
        SELECT 1 FROM sys.tables
        WHERE name = N'eco_verifications' AND schema_id = SCHEMA_ID(N'dbo')
    )
    BEGIN
        CREATE TABLE dbo.eco_verifications (
            id INT IDENTITY(1,1) NOT NULL,
            eco_experience_id INT NOT NULL,
            submitted_by_provider_id INT NOT NULL,
            reviewed_by_admin_id INT NULL,
            decision NVARCHAR(30) NOT NULL
                CONSTRAINT DF_eco_verifications_decision DEFAULT N'submitted',
            reason NVARCHAR(1000) NULL,
            valid_until DATETIME2 NULL,
            created_at DATETIME2 NOT NULL
                CONSTRAINT DF_eco_verifications_created DEFAULT SYSUTCDATETIME(),

            CONSTRAINT PK_eco_verifications
                PRIMARY KEY CLUSTERED (id),

            CONSTRAINT FK_eco_verifications_experience
                FOREIGN KEY (eco_experience_id)
                REFERENCES dbo.eco_experiences (id)
                ON DELETE CASCADE,

            CONSTRAINT FK_eco_verifications_provider
                FOREIGN KEY (submitted_by_provider_id)
                REFERENCES dbo.provider_profiles (id)
                ON DELETE NO ACTION,

            CONSTRAINT FK_eco_verifications_admin
                FOREIGN KEY (reviewed_by_admin_id)
                REFERENCES dbo.users (id)
                ON DELETE NO ACTION,

            CONSTRAINT CK_eco_verifications_decision
                CHECK (decision IN (
                    N'submitted',
                    N'granted',
                    N'rejected',
                    N'revoked'
                ))
        );

        CREATE NONCLUSTERED INDEX IX_eco_verifications_experience_created
            ON dbo.eco_verifications (eco_experience_id, created_at DESC);

        PRINT 'Table dbo.eco_verifications created successfully.';
    END
    ELSE
    BEGIN
        PRINT 'Table dbo.eco_verifications already exists. Skipping.';
    END

    COMMIT TRANSACTION;
    PRINT 'Migration 008_m04_create_eco_tourism_core executed and committed successfully.';
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
    BEGIN
        ROLLBACK TRANSACTION;
        PRINT 'Migration 008_m04_create_eco_tourism_core encountered an error. Transaction rolled back cleanly.';
    END

    DECLARE @ErrMsg NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrSeverity INT = ERROR_SEVERITY();
    DECLARE @ErrState INT = ERROR_STATE();
    DECLARE @ErrLine INT = ERROR_LINE();

    RAISERROR(N'Migration 008_m04_create_eco_tourism_core failed at line %d: %s', @ErrSeverity, @ErrState, @ErrLine, @ErrMsg);
END CATCH;
GO
