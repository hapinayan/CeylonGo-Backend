IF OBJECT_ID(N'dbo.mvp_schema_versions', N'U') IS NULL
    THROW 51046, 'The Academic MVP ledger is required.', 1;

IF COL_LENGTH(N'dbo.listings', N'homestay_metadata') IS NULL
    ALTER TABLE dbo.listings ADD homestay_metadata NVARCHAR(MAX) NULL;
GO

IF OBJECT_ID(N'dbo.ck_listings_homestay_metadata', N'C') IS NULL
    ALTER TABLE dbo.listings WITH CHECK
        ADD CONSTRAINT ck_listings_homestay_metadata CHECK (
            homestay_metadata IS NULL
            OR (category = 'homestay' AND ISJSON(homestay_metadata) = 1)
        );

IF OBJECT_ID(N'dbo.homestay_wishlist', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestay_wishlist (
        traveler_id BIGINT NOT NULL,
        listing_id BIGINT NOT NULL,
        is_saved BIT NOT NULL CONSTRAINT df_homestay_wishlist_saved DEFAULT 1,
        CONSTRAINT pk_homestay_wishlist PRIMARY KEY (traveler_id, listing_id),
        CONSTRAINT fk_homestay_wishlist_traveler FOREIGN KEY (traveler_id) REFERENCES dbo.users(id),
        CONSTRAINT fk_homestay_wishlist_listing FOREIGN KEY (listing_id) REFERENCES dbo.listings(id)
    );
END;
GO
