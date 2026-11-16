
-- M01.2: Homestay Amenities

IF OBJECT_ID(N'dbo.homestay_amenities', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestay_amenities (
        id INT IDENTITY(1,1) NOT NULL
            PRIMARY KEY,

        homestay_id INT NOT NULL,

        name NVARCHAR(100) NOT NULL,

        CONSTRAINT FK_homestay_amenities_homestay
            FOREIGN KEY (homestay_id)
            REFERENCES dbo.homestays(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_homestay_amenities_name
            UNIQUE (homestay_id, name)
    );

    CREATE INDEX IX_homestay_amenities_homestay_id
        ON dbo.homestay_amenities(homestay_id);
END;
