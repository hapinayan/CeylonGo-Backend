-- ============================================================================
-- CeylonGo Database Migration: 001_create_password_reset_tokens.sql
-- Description: Creates dbo.PasswordResetTokens table with foreign key to dbo.Users
-- Engine: Microsoft SQL Server (MSSQL)
-- Database: CeylonGoDB
-- Safe, idempotent execution: checks for existence before creation
-- ============================================================================

USE [CeylonGoDB];
GO

-- 1. Verify dbo.Users table exists
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Users' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    RAISERROR('Migration aborted: Required table dbo.Users does not exist in CeylonGoDB.', 16, 1);
    RETURN;
END;
GO

-- 2. Create dbo.PasswordResetTokens table if not exists
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'PasswordResetTokens' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    PRINT 'Creating table [dbo].[PasswordResetTokens]...';

    CREATE TABLE [dbo].[PasswordResetTokens] (
        [TokenId] INT IDENTITY(1,1) NOT NULL,
        [UserId] INT NOT NULL,
        [TokenHash] NVARCHAR(64) NOT NULL,
        [ExpiresAtUtc] DATETIME2 NOT NULL,
        [IsUsed] BIT NOT NULL CONSTRAINT [DF_PasswordResetTokens_IsUsed] DEFAULT (0),
        [CreatedAtUtc] DATETIME2 NOT NULL CONSTRAINT [DF_PasswordResetTokens_CreatedAtUtc] DEFAULT (SYSUTCDATETIME()),
        [UsedAtUtc] DATETIME2 NULL,

        CONSTRAINT [PK_PasswordResetTokens] PRIMARY KEY CLUSTERED ([TokenId] ASC),
        CONSTRAINT [FK_PasswordResetTokens_Users] FOREIGN KEY ([UserId])
            REFERENCES [dbo].[Users] ([UserId])
            ON DELETE NO ACTION
    );

    PRINT 'Creating unique index [UQ_PasswordResetTokens_TokenHash]...';
    CREATE UNIQUE NONCLUSTERED INDEX [UQ_PasswordResetTokens_TokenHash]
        ON [dbo].[PasswordResetTokens] ([TokenHash] ASC);

    PRINT 'Creating composite index [IX_PasswordResetTokens_UserId_IsUsed_ExpiresAtUtc]...';
    CREATE NONCLUSTERED INDEX [IX_PasswordResetTokens_UserId_IsUsed_ExpiresAtUtc]
        ON [dbo].[PasswordResetTokens] ([UserId] ASC, [IsUsed] ASC, [ExpiresAtUtc] ASC);

    PRINT 'Table [dbo].[PasswordResetTokens] successfully created.';
END
ELSE
BEGIN
    PRINT 'Table [dbo].[PasswordResetTokens] already exists. Skipping creation.';
END;
GO
