ALTER TABLE dbo.listings DROP CONSTRAINT ck_listings_category, ck_listings_approval_status;
ALTER TABLE dbo.listings WITH CHECK ADD
    CONSTRAINT ck_listings_category CHECK (category IN ('homestay','bike','bicycle','adventure','guide','artisan')),
    CONSTRAINT ck_listings_approval_status CHECK (approval_status IN ('draft','pending','evidence','approved','rejected','suspended'));
ALTER TABLE dbo.inventory_units ADD room_metadata NVARCHAR(MAX) NULL;
ALTER TABLE dbo.listing_media ADD is_visible BIT NOT NULL CONSTRAINT df_listing_media_visible DEFAULT 1;
CREATE TABLE dbo.listing_verification (
    listing_id BIGINT NOT NULL CONSTRAINT pk_listing_verification PRIMARY KEY
        CONSTRAINT fk_listing_verification_listing REFERENCES dbo.listings(id),
    details NVARCHAR(MAX) NOT NULL CONSTRAINT ck_listing_verification_details CHECK (ISJSON(details) = 1)
);
GO
