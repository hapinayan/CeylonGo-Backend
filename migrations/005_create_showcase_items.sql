-- Migration: 005_create_showcase_items.sql
-- Description: Create showcase_items table for M05.2 Products & Services Showcase
-- Target Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only creates table and indexes if they do not already exist.

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'showcase_items'
      AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.showcase_items (
        id INT IDENTITY(1,1) NOT NULL,
        artisan_listing_id INT NOT NULL,

        title NVARCHAR(150) NOT NULL,
        item_type NVARCHAR(30) NOT NULL DEFAULT 'product',
        description NVARCHAR(2000) NULL,

        price DECIMAL(10, 2) NULL,
        currency NVARCHAR(10) NOT NULL DEFAULT 'LKR',

        image_url NVARCHAR(1000) NULL,
        materials NVARCHAR(500) NULL,
        duration_minutes INT NULL,

        is_available BIT NOT NULL DEFAULT 1,
        is_featured BIT NOT NULL DEFAULT 0,
        is_active BIT NOT NULL DEFAULT 1,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_showcase_items
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT FK_showcase_items_artisan_listing
            FOREIGN KEY (artisan_listing_id)
            REFERENCES dbo.artisan_listings (id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_showcase_items_type
            CHECK (item_type IN ('product', 'service')),

        CONSTRAINT CK_showcase_items_price
            CHECK (price IS NULL OR price >= 0),

        CONSTRAINT CK_showcase_items_duration
            CHECK (duration_minutes IS NULL OR duration_minutes >= 0)
    );

    -- Foreign key index for fast retrieval of an artisan listing's showcase catalog
    CREATE NONCLUSTERED INDEX IX_showcase_items_artisan_listing_id
        ON dbo.showcase_items (artisan_listing_id);

    -- Index for filtering items by product vs service/workshop
    CREATE NONCLUSTERED INDEX IX_showcase_items_type
        ON dbo.showcase_items (item_type);

    -- Composite index for active catalog search and featured item spotlights
    CREATE NONCLUSTERED INDEX IX_showcase_items_discovery
        ON dbo.showcase_items (is_active, is_featured);

    PRINT 'Table showcase_items created successfully.';
END
ELSE
BEGIN
    PRINT 'Table showcase_items already exists. Skipping.';
END
GO
