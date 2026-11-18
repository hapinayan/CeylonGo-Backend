-- Migration: 024_extend_artisan_module.sql
-- Description: Centralized Artisan Module Schema Extension
-- Module: Artisan Crafts, Business Hours, Images, Reservations & Stock Management
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Extend dbo.artisan_listings with approval, review & verification fields
-- =============================================================================
IF COL_LENGTH('dbo.artisan_listings', 'approval_status') IS NULL
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD approval_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_artisan_listings_approval_status DEFAULT 'approved';
END;

IF COL_LENGTH('dbo.artisan_listings', 'reviewed_by') IS NULL
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD reviewed_by INT NULL;
END;

IF COL_LENGTH('dbo.artisan_listings', 'reviewed_at') IS NULL
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD reviewed_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.artisan_listings', 'rejection_reason') IS NULL
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD rejection_reason NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.artisan_listings', 'verification_status') IS NULL
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD verification_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_artisan_listings_verification_status DEFAULT 'unverified';
END;
GO

-- =============================================================================
-- Batch 2: Add constraints & indexes to dbo.artisan_listings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_artisan_listings_reviewer'
      AND parent_object_id = OBJECT_ID('dbo.artisan_listings')
)
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD CONSTRAINT FK_artisan_listings_reviewer
        FOREIGN KEY (reviewed_by)
        REFERENCES dbo.users(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_artisan_listings_approval'
      AND parent_object_id = OBJECT_ID('dbo.artisan_listings')
)
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD CONSTRAINT CK_artisan_listings_approval
        CHECK (approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_artisan_listings_verification'
      AND parent_object_id = OBJECT_ID('dbo.artisan_listings')
)
BEGIN
    ALTER TABLE dbo.artisan_listings
    ADD CONSTRAINT CK_artisan_listings_verification
        CHECK (verification_status IN ('unverified', 'pending', 'verified', 'rejected', 'suspended'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_artisan_listings_discovery'
      AND object_id = OBJECT_ID('dbo.artisan_listings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_artisan_listings_discovery
        ON dbo.artisan_listings(approval_status, is_active, district);
END;
GO

-- =============================================================================
-- Batch 3: Extend dbo.showcase_items with stock quantity & SKU fields
-- =============================================================================
IF COL_LENGTH('dbo.showcase_items', 'stock_quantity') IS NULL
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD stock_quantity INT NOT NULL
        CONSTRAINT DF_showcase_items_stock_quantity DEFAULT 0;
END;

IF COL_LENGTH('dbo.showcase_items', 'sku') IS NULL
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD sku NVARCHAR(50) NULL;
END;

IF COL_LENGTH('dbo.showcase_items', 'is_custom_order_available') IS NULL
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD is_custom_order_available BIT NOT NULL
        CONSTRAINT DF_showcase_items_is_custom_order DEFAULT 0;
END;

IF COL_LENGTH('dbo.showcase_items', 'approval_status') IS NULL
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD approval_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_showcase_items_approval_status DEFAULT 'approved';
END;
GO

-- =============================================================================
-- Batch 4: Add constraints & indexes to dbo.showcase_items
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_showcase_items_stock'
      AND parent_object_id = OBJECT_ID('dbo.showcase_items')
)
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD CONSTRAINT CK_showcase_items_stock
        CHECK (stock_quantity >= 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_showcase_items_approval'
      AND parent_object_id = OBJECT_ID('dbo.showcase_items')
)
BEGIN
    ALTER TABLE dbo.showcase_items
    ADD CONSTRAINT CK_showcase_items_approval
        CHECK (approval_status IN ('draft', 'pending', 'approved', 'rejected'));
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.artisan_business_images table
-- =============================================================================
IF OBJECT_ID('dbo.artisan_business_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.artisan_business_images (
        id INT IDENTITY(1,1) NOT NULL,
        artisan_listing_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        caption NVARCHAR(255) NULL,
        display_order INT NOT NULL CONSTRAINT DF_artisan_business_images_order DEFAULT 0,
        is_primary BIT NOT NULL CONSTRAINT DF_artisan_business_images_primary DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_artisan_business_images_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_artisan_business_images PRIMARY KEY (id),

        CONSTRAINT FK_artisan_business_images_listing
            FOREIGN KEY (artisan_listing_id)
            REFERENCES dbo.artisan_listings(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_artisan_business_images_order
            CHECK (display_order >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_artisan_business_images_listing
        ON dbo.artisan_business_images(artisan_listing_id);

    PRINT 'artisan_business_images table created.';
END
ELSE
BEGIN
    PRINT 'artisan_business_images table already exists.';
END;
GO

-- =============================================================================
-- Batch 6: Create dbo.artisan_product_images table
-- =============================================================================
IF OBJECT_ID('dbo.artisan_product_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.artisan_product_images (
        id INT IDENTITY(1,1) NOT NULL,
        showcase_item_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        caption NVARCHAR(255) NULL,
        display_order INT NOT NULL CONSTRAINT DF_artisan_product_images_order DEFAULT 0,
        is_primary BIT NOT NULL CONSTRAINT DF_artisan_product_images_primary DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_artisan_product_images_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_artisan_product_images PRIMARY KEY (id),

        CONSTRAINT FK_artisan_product_images_item
            FOREIGN KEY (showcase_item_id)
            REFERENCES dbo.showcase_items(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_artisan_product_images_order
            CHECK (display_order >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_artisan_product_images_item
        ON dbo.artisan_product_images(showcase_item_id);

    PRINT 'artisan_product_images table created.';
END
ELSE
BEGIN
    PRINT 'artisan_product_images table already exists.';
END;
GO

-- =============================================================================
-- Batch 7: Create dbo.artisan_reservations table
-- =============================================================================
IF OBJECT_ID('dbo.artisan_reservations', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.artisan_reservations (
        id INT IDENTITY(1,1) NOT NULL,
        showcase_item_id INT NOT NULL,
        artisan_listing_id INT NOT NULL,
        traveler_id INT NOT NULL,
        quantity INT NOT NULL CONSTRAINT DF_artisan_reservations_quantity DEFAULT 1,
        total_price DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_artisan_reservations_currency DEFAULT 'LKR',
        reservation_status NVARCHAR(30) NOT NULL CONSTRAINT DF_artisan_reservations_status DEFAULT 'pending',
        pickup_date DATE NULL,
        special_notes NVARCHAR(1000) NULL,
        expires_at DATETIME2 NULL,
        cancelled_by INT NULL,
        cancellation_reason NVARCHAR(1000) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_artisan_reservations_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_artisan_reservations_updated DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_artisan_reservations PRIMARY KEY (id),

        CONSTRAINT FK_artisan_reservations_item
            FOREIGN KEY (showcase_item_id)
            REFERENCES dbo.showcase_items(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_artisan_reservations_listing
            FOREIGN KEY (artisan_listing_id)
            REFERENCES dbo.artisan_listings(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_artisan_reservations_traveler
            FOREIGN KEY (traveler_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_artisan_reservations_canceller
            FOREIGN KEY (cancelled_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_artisan_reservations_quantity
            CHECK (quantity > 0),

        CONSTRAINT CK_artisan_reservations_price
            CHECK (total_price >= 0),

        CONSTRAINT CK_artisan_reservations_status
            CHECK (reservation_status IN ('pending', 'confirmed', 'ready_for_pickup', 'completed', 'cancelled', 'expired'))
    );

    CREATE NONCLUSTERED INDEX IX_artisan_reservations_listing
        ON dbo.artisan_reservations(artisan_listing_id);

    CREATE NONCLUSTERED INDEX IX_artisan_reservations_traveler
        ON dbo.artisan_reservations(traveler_id);

    CREATE NONCLUSTERED INDEX IX_artisan_reservations_status
        ON dbo.artisan_reservations(reservation_status);

    PRINT 'artisan_reservations table created.';
END
ELSE
BEGIN
    PRINT 'artisan_reservations table already exists.';
END;
GO

-- =============================================================================
-- Batch 8: Create dbo.artisan_business_hours table
-- =============================================================================
IF OBJECT_ID('dbo.artisan_business_hours', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.artisan_business_hours (
        id INT IDENTITY(1,1) NOT NULL,
        artisan_listing_id INT NOT NULL,
        day_of_week INT NOT NULL,
        open_time TIME NULL,
        close_time TIME NULL,
        is_closed BIT NOT NULL CONSTRAINT DF_artisan_business_hours_closed DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_artisan_business_hours_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_artisan_business_hours PRIMARY KEY (id),

        CONSTRAINT FK_artisan_business_hours_listing
            FOREIGN KEY (artisan_listing_id)
            REFERENCES dbo.artisan_listings(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_artisan_business_hours_day
            UNIQUE (artisan_listing_id, day_of_week),

        CONSTRAINT CK_artisan_business_hours_day
            CHECK (day_of_week BETWEEN 0 AND 6),

        CONSTRAINT CK_artisan_business_hours_time
            CHECK (is_closed = 1 OR (open_time IS NOT NULL AND close_time IS NOT NULL AND close_time > open_time))
    );

    CREATE NONCLUSTERED INDEX IX_artisan_business_hours_listing
        ON dbo.artisan_business_hours(artisan_listing_id);

    PRINT 'artisan_business_hours table created.';
END
ELSE
BEGIN
    PRINT 'artisan_business_hours table already exists.';
END;
GO
