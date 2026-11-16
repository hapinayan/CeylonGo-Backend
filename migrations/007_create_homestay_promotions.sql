
-- M01.4: Homestay Promotions
-- SQL Server migration

IF OBJECT_ID(
    'dbo.homestay_promotions',
    'U'
) IS NULL
BEGIN
    CREATE TABLE dbo.homestay_promotions
    (
        id INT IDENTITY(1,1) NOT NULL,

        homestay_id INT NOT NULL,

        title NVARCHAR(150) NOT NULL,

        description NVARCHAR(1000) NULL,

        discount_percent DECIMAL(5,2) NOT NULL,

        start_date DATE NOT NULL,

        end_date DATE NOT NULL,

        is_active BIT NOT NULL
            CONSTRAINT DF_homestay_promotions_active
            DEFAULT (1),

        created_at DATETIME2 NOT NULL
            CONSTRAINT DF_homestay_promotions_created
            DEFAULT (SYSUTCDATETIME()),

        CONSTRAINT PK_homestay_promotions
            PRIMARY KEY (id),

        CONSTRAINT FK_homestay_promotions_homestay
            FOREIGN KEY (homestay_id)
            REFERENCES dbo.homestays(id),

        CONSTRAINT CK_homestay_promotions_discount
            CHECK (
                discount_percent > 0
                AND discount_percent <= 100
            ),

        CONSTRAINT CK_homestay_promotions_dates
            CHECK (end_date >= start_date)
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_homestay_promotions_homestay_id'
      AND object_id = OBJECT_ID('dbo.homestay_promotions')
)
BEGIN
    CREATE INDEX IX_homestay_promotions_homestay_id
    ON dbo.homestay_promotions(homestay_id);
END;
GO
