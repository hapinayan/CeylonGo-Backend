-- Migration: 006_add_personal_details_fields.sql
-- Description: Add demographic and identity document fields to provider drafts and profiles
-- Database: CeylonGoDB (Microsoft SQL Server / SQL Server Express compatible)
-- Safety: Fully transactional, atomic rollback protection, idempotent, non-destructive.
-- Constraints: Restricts identity_document_type to ('nic', 'passport') via CHECK constraints.
-- Uniqueness: Enforces composite uniqueness on provider_profiles (identity_document_type, nic_number) WHERE nic_number IS NOT NULL.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


BEGIN TRY
    BEGIN TRANSACTION;
    -- =============================================================================
    -- 1. EXTEND dbo.provider_registration_drafts
    -- =============================================================================

    -- 1.1 date_of_birth
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_registration_drafts') AND name = N'date_of_birth'
    )
    BEGIN
        ALTER TABLE dbo.provider_registration_drafts ADD date_of_birth DATE NULL;
        PRINT 'Added date_of_birth to dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'Column date_of_birth already exists on dbo.provider_registration_drafts. Skipping.';
    END

    -- 1.2 gender
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_registration_drafts') AND name = N'gender'
    )
    BEGIN
        ALTER TABLE dbo.provider_registration_drafts ADD gender NVARCHAR(20) NULL;
        PRINT 'Added gender to dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'Column gender already exists on dbo.provider_registration_drafts. Skipping.';
    END

    -- 1.3 nationality
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_registration_drafts') AND name = N'nationality'
    )
    BEGIN
        ALTER TABLE dbo.provider_registration_drafts ADD nationality NVARCHAR(50) NULL;
        PRINT 'Added nationality to dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'Column nationality already exists on dbo.provider_registration_drafts. Skipping.';
    END

    -- 1.4 identity_document_type with DEFAULT 'nic'
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_registration_drafts') AND name = N'identity_document_type'
    )
    BEGIN
        ALTER TABLE dbo.provider_registration_drafts ADD identity_document_type NVARCHAR(20) NOT NULL
            CONSTRAINT DF_provider_registration_drafts_doc_type DEFAULT N'nic';
        PRINT 'Added identity_document_type with default ''nic'' to dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'Column identity_document_type already exists on dbo.provider_registration_drafts. Skipping.';
    END

    -- 1.5 CHECK constraint for identity_document_type
    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_provider_registration_drafts_doc_type'
          AND parent_object_id = OBJECT_ID(N'dbo.provider_registration_drafts')
    )
    BEGIN
        EXEC sp_executesql N'
            ALTER TABLE dbo.provider_registration_drafts
                ADD CONSTRAINT CK_provider_registration_drafts_doc_type
                CHECK (identity_document_type IN (N''nic'', N''passport''));
        ';
        PRINT 'Added CHECK constraint CK_provider_registration_drafts_doc_type to dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'CHECK constraint CK_provider_registration_drafts_doc_type already exists. Skipping.';
    END


    -- =============================================================================
    -- 2. EXTEND dbo.provider_profiles
    -- =============================================================================

    -- 2.1 date_of_birth
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_profiles') AND name = N'date_of_birth'
    )
    BEGIN
        ALTER TABLE dbo.provider_profiles ADD date_of_birth DATE NULL;
        PRINT 'Added date_of_birth to dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'Column date_of_birth already exists on dbo.provider_profiles. Skipping.';
    END

    -- 2.2 gender
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_profiles') AND name = N'gender'
    )
    BEGIN
        ALTER TABLE dbo.provider_profiles ADD gender NVARCHAR(20) NULL;
        PRINT 'Added gender to dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'Column gender already exists on dbo.provider_profiles. Skipping.';
    END

    -- 2.3 nationality
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_profiles') AND name = N'nationality'
    )
    BEGIN
        ALTER TABLE dbo.provider_profiles ADD nationality NVARCHAR(50) NULL;
        PRINT 'Added nationality to dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'Column nationality already exists on dbo.provider_profiles. Skipping.';
    END

    -- 2.4 identity_document_type with DEFAULT 'nic'
    IF NOT EXISTS (
        SELECT 1 FROM sys.columns
        WHERE object_id = OBJECT_ID(N'dbo.provider_profiles') AND name = N'identity_document_type'
    )
    BEGIN
        ALTER TABLE dbo.provider_profiles ADD identity_document_type NVARCHAR(20) NOT NULL
            CONSTRAINT DF_provider_profiles_doc_type DEFAULT N'nic';
        PRINT 'Added identity_document_type with default ''nic'' to dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'Column identity_document_type already exists on dbo.provider_profiles. Skipping.';
    END

    -- 2.5 CHECK constraint for identity_document_type
    IF NOT EXISTS (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_provider_profiles_doc_type'
          AND parent_object_id = OBJECT_ID(N'dbo.provider_profiles')
    )
    BEGIN
        EXEC sp_executesql N'
            ALTER TABLE dbo.provider_profiles
                ADD CONSTRAINT CK_provider_profiles_doc_type
                CHECK (identity_document_type IN (N''nic'', N''passport''));
        ';
        PRINT 'Added CHECK constraint CK_provider_profiles_doc_type to dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'CHECK constraint CK_provider_profiles_doc_type already exists. Skipping.';
    END


    -- =============================================================================
    -- 3. INDEXES FOR MULTI-DOCUMENT-TYPE LOOKUPS & COMPOSITE UNIQUENESS
    -- =============================================================================

    -- 3.1 Composite Nonclustered Lookup Index on Drafts (Filtered for non-null nic_number)
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = N'IX_provider_registration_drafts_doc_lookup'
          AND object_id = OBJECT_ID(N'dbo.provider_registration_drafts')
    )
    BEGIN
        EXEC sp_executesql N'
            CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_doc_lookup
                ON dbo.provider_registration_drafts (identity_document_type, nic_number)
                WHERE nic_number IS NOT NULL;
        ';
        PRINT 'Created index IX_provider_registration_drafts_doc_lookup on dbo.provider_registration_drafts.';
    END
    ELSE
    BEGIN
        PRINT 'Index IX_provider_registration_drafts_doc_lookup already exists. Skipping.';
    END

    -- 3.2 Composite Unique Filtered Index on Provider Profiles
    -- Enforces that no two registered providers can share the same (identity_document_type, nic_number)
    -- Allows multiple NULL nic_number rows for backward compatibility with incomplete profiles
    IF NOT EXISTS (
        SELECT 1 FROM sys.indexes
        WHERE name = N'UQ_provider_profiles_doc_type_number'
          AND object_id = OBJECT_ID(N'dbo.provider_profiles')
    )
    BEGIN
        EXEC sp_executesql N'
            CREATE UNIQUE NONCLUSTERED INDEX UQ_provider_profiles_doc_type_number
                ON dbo.provider_profiles (identity_document_type, nic_number)
                WHERE nic_number IS NOT NULL;
        ';
        PRINT 'Created UNIQUE filtered index UQ_provider_profiles_doc_type_number on dbo.provider_profiles.';
    END
    ELSE
    BEGIN
        PRINT 'Index UQ_provider_profiles_doc_type_number already exists. Skipping.';
    END

    -- Commit atomic transaction
    COMMIT TRANSACTION;
    PRINT 'Migration 006 executed and committed successfully.';

END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0
    BEGIN
        ROLLBACK TRANSACTION;
        PRINT 'Migration 006 encountered an error. Transaction rolled back cleanly.';
    END

    DECLARE @ErrMsg NVARCHAR(4000) = ERROR_MESSAGE();
    DECLARE @ErrSeverity INT = ERROR_SEVERITY();
    DECLARE @ErrState INT = ERROR_STATE();
    DECLARE @ErrLine INT = ERROR_LINE();

    RAISERROR(N'Migration 006 failed at line %d: %s', @ErrSeverity, @ErrState, @ErrLine, @ErrMsg);
END CATCH;
GO
