-- Migration: 002_add_google_sign_in_to_users.sql
-- Description: Add Google Sign-In support to users table (google_sub column, nullable password_hash)
-- Target Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only alters columns or creates index if not already present.

-- 1. Allow nullable password_hash for users created via Google OAuth (who do not have a local password)
IF EXISTS (
    SELECT * FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_NAME = 'users' AND COLUMN_NAME = 'password_hash' AND IS_NULLABLE = 'NO'
)
BEGIN
    ALTER TABLE dbo.users ALTER COLUMN password_hash NVARCHAR(255) NULL;
    PRINT 'Altered dbo.users.password_hash to NULL.';
END
ELSE
BEGIN
    PRINT 'dbo.users.password_hash is already nullable.';
END

-- 2. Add google_sub column to store Google stable user ID (subject claim)
IF NOT EXISTS (
    SELECT * FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.users') AND name = 'google_sub'
)
BEGIN
    ALTER TABLE dbo.users ADD google_sub NVARCHAR(255) NULL;
    PRINT 'Added google_sub column to dbo.users.';
END
ELSE
BEGIN
    PRINT 'Column google_sub already exists on dbo.users.';
END

-- 3. Create unique filtered index on google_sub (ignoring NULLs for local-only users)
IF NOT EXISTS (
    SELECT * FROM sys.indexes
    WHERE name = 'UQ_users_google_sub' AND object_id = OBJECT_ID('dbo.users')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UQ_users_google_sub
        ON dbo.users (google_sub)
        WHERE google_sub IS NOT NULL;
    PRINT 'Created unique filtered index UQ_users_google_sub.';
END
ELSE
BEGIN
    PRINT 'Index UQ_users_google_sub already exists.';
END
GO

