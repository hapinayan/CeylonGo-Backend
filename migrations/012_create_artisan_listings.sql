-- Migration: 004_create_artisan_listings.sql
-- Description: Create artisan_listings table for M05.1 Local Business & Artisan Listings
-- Target Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only creates table and indexes if they do not already exist.

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'artisan_listings'
      AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.artisan_listings (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,

        title NVARCHAR(150) NOT NULL,
        craft_category NVARCHAR(50) NOT NULL,
        description NVARCHAR(2000) NULL,

        district NVARCHAR(100) NOT NULL,
        city NVARCHAR(100) NOT NULL,
        address NVARCHAR(255) NULL,

        latitude DECIMAL(9, 6) NULL,
        longitude DECIMAL(9, 6) NULL,

        phone NVARCHAR(25) NULL,
        email NVARCHAR(255) NULL,
        website_url NVARCHAR(500) NULL,
        opening_hours NVARCHAR(255) NULL,
        cover_image_url NVARCHAR(1000) NULL,

        is_active BIT NOT NULL DEFAULT 1,
        is_verified BIT NOT NULL DEFAULT 0,
        is_featured BIT NOT NULL DEFAULT 0,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_artisan_listings
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT FK_artisan_listings_provider_profile
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles (id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_artisan_listings_latitude
            CHECK (latitude IS NULL OR (latitude >= -90.0 AND latitude <= 90.0)),

        CONSTRAINT CK_artisan_listings_longitude
            CHECK (longitude IS NULL OR (longitude >= -180.0 AND longitude <= 180.0))
    );

    -- Foreign key index for relationship lookups and joins
    CREATE NONCLUSTERED INDEX IX_artisan_listings_provider_profile_id
        ON dbo.artisan_listings (provider_profile_id);

    -- Search and filter indexes for discovery
    CREATE NONCLUSTERED INDEX IX_artisan_listings_district
        ON dbo.artisan_listings (district);

    CREATE NONCLUSTERED INDEX IX_artisan_listings_craft_category
        ON dbo.artisan_listings (craft_category);

    -- Filtered/Composite index for public discovery and featured promotions
    CREATE NONCLUSTERED INDEX IX_artisan_listings_active_featured
        ON dbo.artisan_listings (is_active, is_featured);

    PRINT 'Table artisan_listings created successfully.';
END
ELSE
BEGIN
    PRINT 'Table artisan_listings already exists. Skipping.';
END
GO
