-- Migration: 006_create_promotions.sql
-- Description: Create promotions table for M05.3 Business Promotions & Management
-- Target Database: CeylonGoDB (Microsoft SQL Server)
-- Safe & Idempotent: Only creates table and indexes if they do not already exist.

IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'promotions'
      AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.promotions (
        id INT IDENTITY(1,1) NOT NULL,
        artisan_listing_id INT NOT NULL,
        showcase_item_id INT NULL,

        title NVARCHAR(150) NOT NULL,
        description NVARCHAR(2000) NULL,
        promo_code NVARCHAR(50) NULL,

        discount_percentage DECIMAL(5, 2) NULL,
        discount_amount DECIMAL(10, 2) NULL,

        start_date DATETIME2 NOT NULL,
        end_date DATETIME2 NOT NULL,

        is_active BIT NOT NULL DEFAULT 1,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_promotions
            PRIMARY KEY CLUSTERED (id),

        CONSTRAINT FK_promotions_artisan_listing
            FOREIGN KEY (artisan_listing_id)
            REFERENCES dbo.artisan_listings (id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_promotions_showcase_item
            FOREIGN KEY (showcase_item_id)
            REFERENCES dbo.showcase_items (id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_promotions_date_range
            CHECK (end_date >= start_date),

        CONSTRAINT CK_promotions_discount_percentage
            CHECK (discount_percentage IS NULL OR (discount_percentage >= 0 AND discount_percentage <= 100)),

        CONSTRAINT CK_promotions_discount_amount
            CHECK (discount_amount IS NULL OR discount_amount >= 0),

        -- Ensures a promotion cannot have conflicting simultaneous percentage and fixed amount discounts
        CONSTRAINT CK_promotions_discount_exclusive
            CHECK (NOT (discount_percentage IS NOT NULL AND discount_amount IS NOT NULL))
    );

    -- Foreign key index for querying an artisan listing's promotions
    CREATE NONCLUSTERED INDEX IX_promotions_artisan_listing_id
        ON dbo.promotions (artisan_listing_id);

    -- Filtered foreign key index for item-specific promotions
    CREATE NONCLUSTERED INDEX IX_promotions_showcase_item_id
        ON dbo.promotions (showcase_item_id)
        WHERE showcase_item_id IS NOT NULL;

    -- Composite index for querying active, non-expired promotions
    CREATE NONCLUSTERED INDEX IX_promotions_active_dates
        ON dbo.promotions (is_active, start_date, end_date);

    PRINT 'Table promotions created successfully.';
END
ELSE
BEGIN
    PRINT 'Table promotions already exists. Skipping.';
END
GO
