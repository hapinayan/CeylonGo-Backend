
-- Migration: 003_create_homestay_tables.sql
-- Module: M01 Homestay Discovery
-- Target: Microsoft SQL Server (CeylonGoDB)

IF OBJECT_ID('dbo.homestays', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestays (
        id INT IDENTITY(1,1) NOT NULL,
        name NVARCHAR(150) NOT NULL,
        description NVARCHAR(MAX) NULL,
        location NVARCHAR(255) NOT NULL,
        price_per_night DECIMAL(12,2) NOT NULL,
        max_guests INT NOT NULL,
        is_active BIT NOT NULL DEFAULT 1,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_homestays PRIMARY KEY (id),
        CONSTRAINT CK_homestays_price
            CHECK (price_per_night >= 0),
        CONSTRAINT CK_homestays_guests
            CHECK (max_guests > 0)
    );

    PRINT 'homestays table created.';
END
ELSE
BEGIN
    PRINT 'homestays table already exists.';
END
GO

IF OBJECT_ID('dbo.homestay_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestay_images (
        id INT IDENTITY(1,1) NOT NULL,
        homestay_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        is_primary BIT NOT NULL DEFAULT 0,

        CONSTRAINT PK_homestay_images PRIMARY KEY (id),
        CONSTRAINT FK_homestay_images_homestays
            FOREIGN KEY (homestay_id)
            REFERENCES dbo.homestays(id)
            ON DELETE CASCADE
    );

    CREATE INDEX IX_homestay_images_homestay_id
        ON dbo.homestay_images(homestay_id);

    PRINT 'homestay_images table created.';
END
ELSE
BEGIN
    PRINT 'homestay_images table already exists.';
END
GO
