-- Migration: 004_extend_provider_registration.sql
-- Description: Extend provider profiles for comprehensive registration & add email OTP and authorization tables
-- Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only alters or creates tables/columns/indexes if they do not already exist.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- 1. EXTEND dbo.provider_profiles
-- =============================================================================

-- Personal details: NIC number
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'nic_number'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD nic_number NVARCHAR(20) NULL;
    PRINT 'Added nic_number to dbo.provider_profiles.';
END
ELSE
BEGIN
    PRINT 'Column nic_number already exists on dbo.provider_profiles.';
END
GO

-- Personal details: Permanent Address
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'permanent_province'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD permanent_province NVARCHAR(100) NULL;
    PRINT 'Added permanent_province to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'permanent_district'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD permanent_district NVARCHAR(100) NULL;
    PRINT 'Added permanent_district to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'permanent_city'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD permanent_city NVARCHAR(100) NULL;
    PRINT 'Added permanent_city to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'permanent_landmark'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD permanent_landmark NVARCHAR(255) NULL;
    PRINT 'Added permanent_landmark to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'permanent_postal_code'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD permanent_postal_code NVARCHAR(20) NULL;
    PRINT 'Added permanent_postal_code to dbo.provider_profiles.';
END
GO

-- Business details: Second contact phone
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'secondary_phone'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD secondary_phone NVARCHAR(25) NULL;
    PRINT 'Added secondary_phone to dbo.provider_profiles.';
END
GO

-- Business details: Business Address
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_province'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_province NVARCHAR(100) NULL;
    PRINT 'Added business_province to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_district'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_district NVARCHAR(100) NULL;
    PRINT 'Added business_district to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_city'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_city NVARCHAR(100) NULL;
    PRINT 'Added business_city to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_landmark'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_landmark NVARCHAR(255) NULL;
    PRINT 'Added business_landmark to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_postal_code'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_postal_code NVARCHAR(20) NULL;
    PRINT 'Added business_postal_code to dbo.provider_profiles.';
END
GO

-- Business details: Registration number
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'business_registration_number'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD business_registration_number NVARCHAR(50) NULL;
    PRINT 'Added business_registration_number to dbo.provider_profiles.';
END
GO

-- Identity Verification: File references
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'selfie_image_path'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD selfie_image_path NVARCHAR(255) NULL;
    PRINT 'Added selfie_image_path to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'nic_front_image_path'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD nic_front_image_path NVARCHAR(255) NULL;
    PRINT 'Added nic_front_image_path to dbo.provider_profiles.';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.provider_profiles') AND name = 'nic_back_image_path'
)
BEGIN
    ALTER TABLE dbo.provider_profiles ADD nic_back_image_path NVARCHAR(255) NULL;
    PRINT 'Added nic_back_image_path to dbo.provider_profiles.';
END
GO

-- Filtered index on NIC number
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_provider_profiles_nic_number' AND object_id = OBJECT_ID('dbo.provider_profiles')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_provider_profiles_nic_number
        ON dbo.provider_profiles (nic_number)
        WHERE nic_number IS NOT NULL;
    PRINT 'Created index IX_provider_profiles_nic_number.';
END
GO


-- =============================================================================
-- 2. CREATE dbo.provider_email_otps
-- =============================================================================

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'provider_email_otps' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.provider_email_otps (
        id INT IDENTITY(1,1) NOT NULL,
        email NVARCHAR(255) NOT NULL,
        otp_hash NVARCHAR(255) NOT NULL,
        purpose NVARCHAR(50) NOT NULL,
        expires_at DATETIME2 NOT NULL,
        attempts_count INT NOT NULL DEFAULT 0,
        max_attempts INT NOT NULL DEFAULT 5,
        is_used BIT NOT NULL DEFAULT 0,
        used_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_provider_email_otps
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT CK_provider_email_otps_purpose
            CHECK (purpose IN ('registration', 'resubmission', 'password_setup'))
    );

    CREATE NONCLUSTERED INDEX IX_provider_email_otps_email_purpose
        ON dbo.provider_email_otps (email, purpose);

    CREATE NONCLUSTERED INDEX IX_provider_email_otps_expires_at
        ON dbo.provider_email_otps (expires_at);

    PRINT 'Table dbo.provider_email_otps created successfully.';
END
ELSE
BEGIN
    PRINT 'Table dbo.provider_email_otps already exists. Skipping.';
END
GO


-- =============================================================================
-- 3. CREATE dbo.provider_registration_authorizations
-- =============================================================================

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'provider_registration_authorizations' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.provider_registration_authorizations (
        id INT IDENTITY(1,1) NOT NULL,
        email NVARCHAR(255) NOT NULL,
        token_hash NVARCHAR(64) NOT NULL,
        purpose NVARCHAR(50) NOT NULL,
        expires_at DATETIME2 NOT NULL,
        is_consumed BIT NOT NULL DEFAULT 0,
        consumed_at DATETIME2 NULL,
        is_revoked BIT NOT NULL DEFAULT 0,
        revoked_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_provider_registration_authorizations
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT UQ_provider_reg_auth_token_hash
            UNIQUE (token_hash),

        CONSTRAINT CK_provider_reg_auth_purpose
            CHECK (purpose IN ('registration', 'resubmission', 'password_setup'))
    );

    CREATE NONCLUSTERED INDEX IX_provider_reg_auth_email_purpose
        ON dbo.provider_registration_authorizations (email, purpose);

    CREATE NONCLUSTERED INDEX IX_provider_reg_auth_expires_at
        ON dbo.provider_registration_authorizations (expires_at);

    PRINT 'Table dbo.provider_registration_authorizations created successfully.';
END
ELSE
BEGIN
    PRINT 'Table dbo.provider_registration_authorizations already exists. Skipping.';
END
GO
