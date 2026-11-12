-- Migration: 001_create_password_resets.sql
-- Description: Create password_resets table for Phase 2 password reset flow
-- Target Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only creates table and indexes if they do not already exist.

IF NOT EXISTS (
    SELECT * FROM sys.tables WHERE name = 'password_resets' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.password_resets (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        token_hash NVARCHAR(64) NOT NULL,
        expires_at DATETIME2 NOT NULL,
        is_used BIT NOT NULL DEFAULT 0,
        used_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_password_resets PRIMARY KEY CLUSTERED (id),
        CONSTRAINT FK_password_resets_users FOREIGN KEY (user_id)
            REFERENCES dbo.users (id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_password_resets_token_hash
        ON dbo.password_resets (token_hash);

    CREATE NONCLUSTERED INDEX IX_password_resets_user_id
        ON dbo.password_resets (user_id);

    PRINT 'Table password_resets created successfully.';
END
ELSE
BEGIN
    PRINT 'Table password_resets already exists. Skipping.';
END
GO

