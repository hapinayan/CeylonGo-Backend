
-- Migration: 003_create_provider_profiles.sql
-- Description: Create Service Provider profiles and approval workflow
-- Database: CeylonGoDB (Microsoft SQL Server)

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'provider_profiles'
      AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.provider_profiles (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,

        business_name NVARCHAR(150) NOT NULL,
        provider_type NVARCHAR(30) NOT NULL,
        phone NVARCHAR(25) NOT NULL,
        district NVARCHAR(100) NOT NULL,
        description NVARCHAR(1000) NULL,

        approval_status NVARCHAR(20)
            NOT NULL DEFAULT 'pending',

        reviewed_by INT NULL,
        reviewed_at DATETIME2 NULL,
        rejection_reason NVARCHAR(500) NULL,

        created_at DATETIME2
            NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_provider_profiles
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT UQ_provider_profiles_user_id
            UNIQUE (user_id),

        CONSTRAINT FK_provider_profiles_users
            FOREIGN KEY (user_id)
            REFERENCES dbo.users (id),

        CONSTRAINT FK_provider_profiles_reviewed_by
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users (id),

        CONSTRAINT CK_provider_profiles_type
            CHECK (provider_type IN (
                'homestay',
                'tour_guide',
                'activity',
                'eco_tourism',
                'artisan'
            )),

        CONSTRAINT CK_provider_profiles_approval
            CHECK (approval_status IN (
                'pending',
                'approved',
                'rejected'
            ))
    );

    CREATE NONCLUSTERED INDEX IX_provider_profiles_status
        ON dbo.provider_profiles (approval_status);

    CREATE NONCLUSTERED INDEX IX_provider_profiles_type
        ON dbo.provider_profiles (provider_type);

    PRINT 'Provider profiles table created successfully.';
END
ELSE
BEGIN
    PRINT 'Provider profiles table already exists. Skipping.';
END
GO
