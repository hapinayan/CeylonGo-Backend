-- =============================================================================
-- Migration: 009_m04_create_eco_wishlists.sql
-- Module: 04 (Eco-Tourism Backend) - Requirement T14 (Traveler Wishlist)
-- Database: CeylonGoDB (Microsoft SQL Server)
-- =============================================================================

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = N'eco_wishlists' AND schema_id = SCHEMA_ID(N'dbo')
)
BEGIN
    CREATE TABLE dbo.eco_wishlists (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        eco_experience_id INT NOT NULL,
        created_at DATETIME2 NOT NULL
            CONSTRAINT DF_eco_wishlists_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_eco_wishlists
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT FK_eco_wishlists_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users (id)
            ON DELETE CASCADE,

        CONSTRAINT FK_eco_wishlists_eco_experience
            FOREIGN KEY (eco_experience_id)
            REFERENCES dbo.eco_experiences (id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_eco_wishlists_user_experience
            UNIQUE NONCLUSTERED (user_id, eco_experience_id)
    );

    CREATE NONCLUSTERED INDEX IX_eco_wishlists_user_created
        ON dbo.eco_wishlists (user_id, created_at DESC)
        INCLUDE (eco_experience_id);

    CREATE NONCLUSTERED INDEX IX_eco_wishlists_experience_id
        ON dbo.eco_wishlists (eco_experience_id);

    PRINT 'Table dbo.eco_wishlists created successfully.';
END
ELSE
BEGIN
    PRINT 'Table dbo.eco_wishlists already exists. Skipping.';
END
