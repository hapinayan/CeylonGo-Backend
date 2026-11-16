-- Migration: 005_provider_registration_drafts.sql
-- Description: Create provider_registration_drafts table for multi-step service provider registration drafts
-- Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only creates tables, constraints, and indexes if they do not already exist.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- 1. CREATE dbo.provider_registration_drafts
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE object_id = OBJECT_ID('dbo.provider_registration_drafts')
)
BEGIN
    CREATE TABLE dbo.provider_registration_drafts (
        id INT IDENTITY(1,1) NOT NULL,
        email NVARCHAR(255) NOT NULL,
        full_name NVARCHAR(150) NULL,
        primary_phone NVARCHAR(25) NULL,
        nic_number NVARCHAR(20) NULL,
        permanent_province NVARCHAR(100) NULL,
        permanent_district NVARCHAR(100) NULL,
        permanent_city NVARCHAR(100) NULL,
        permanent_landmark NVARCHAR(255) NULL,
        permanent_postal_code NVARCHAR(20) NULL,
        business_name NVARCHAR(150) NULL,
        provider_category NVARCHAR(30) NULL,
        secondary_phone NVARCHAR(25) NULL,
        business_province NVARCHAR(100) NULL,
        business_district NVARCHAR(100) NULL,
        business_city NVARCHAR(100) NULL,
        business_landmark NVARCHAR(255) NULL,
        business_postal_code NVARCHAR(20) NULL,
        business_registration_number NVARCHAR(50) NULL,
        business_description NVARCHAR(1000) NULL,
        selfie_file_reference NVARCHAR(255) NULL,
        nic_front_file_reference NVARCHAR(255) NULL,
        nic_back_file_reference NVARCHAR(255) NULL,
        registration_step NVARCHAR(50) NOT NULL CONSTRAINT DF_provider_registration_drafts_step DEFAULT N'personal',
        created_at DATETIME2(7) NOT NULL CONSTRAINT DF_provider_registration_drafts_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2(7) NOT NULL CONSTRAINT DF_provider_registration_drafts_updated DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_provider_registration_drafts PRIMARY KEY CLUSTERED (id ASC),
        CONSTRAINT UQ_provider_registration_drafts_email UNIQUE NONCLUSTERED (email ASC)
    );
    PRINT 'Created table dbo.provider_registration_drafts.';
END
ELSE
BEGIN
    PRINT 'Table dbo.provider_registration_drafts already exists.';
END
GO

-- =============================================================================
-- 2. INDEXES
-- =============================================================================

-- Filtered Index on nic_number for duplicate checking
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_provider_registration_drafts_nic_number'
      AND object_id = OBJECT_ID('dbo.provider_registration_drafts')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_nic_number
        ON dbo.provider_registration_drafts (nic_number)
        WHERE nic_number IS NOT NULL;
    PRINT 'Created index IX_provider_registration_drafts_nic_number.';
END
ELSE
BEGIN
    PRINT 'Index IX_provider_registration_drafts_nic_number already exists.';
END
GO

-- Index on provider_category for querying by category
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_provider_registration_drafts_provider_category'
      AND object_id = OBJECT_ID('dbo.provider_registration_drafts')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_provider_registration_drafts_provider_category
        ON dbo.provider_registration_drafts (provider_category)
        WHERE provider_category IS NOT NULL;
    PRINT 'Created index IX_provider_registration_drafts_provider_category.';
END
ELSE
BEGIN
    PRINT 'Index IX_provider_registration_drafts_provider_category already exists.';
END
GO
