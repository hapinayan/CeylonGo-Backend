-- The migration runner owns the transaction and records the ledger row.
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
    THROW 51040, 'Eco migrations require the existing canonical migration ledger.', 1;

IF (
    SELECT COUNT(*) FROM dbo.schema_migrations
    WHERE version BETWEEN '001' AND '040' AND version LIKE '[0-9][0-9][0-9]'
) <> 40
    THROW 51041, 'Eco migrations require a verified ledger through 040.', 1;

IF NOT EXISTS (
    SELECT 1 FROM dbo.schema_migrations WHERE version = '041'
)
    THROW 51045, 'Eco sponsorship requires the canonical ledger through 041.', 1;

IF OBJECT_ID(N'dbo.eco_sponsored_promotions', N'U') IS NULL
   OR OBJECT_ID(N'dbo.eco_promotion_analytics', N'U') IS NULL
   OR OBJECT_ID(N'dbo.booking_references', N'U') IS NULL
    THROW 51046, 'Eco sponsorship prerequisites are missing.', 1;

IF OBJECT_ID(N'dbo.eco_sponsorship_captures') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_promotion_event_receipts') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_promotion_attributions') IS NOT NULL
    THROW 51048, 'Unexpected existing Eco migration object. Reconcile before upgrade.', 1;

CREATE TABLE dbo.eco_sponsorship_captures (
    gateway_id NVARCHAR(100) NOT NULL CONSTRAINT PK_eco_sponsorship_captures PRIMARY KEY,
    promotion_id INT NOT NULL CONSTRAINT FK_eco_capture_promotion
        REFERENCES dbo.eco_sponsored_promotions(id),
    amount DECIMAL(12,2) NOT NULL,
    currency NVARCHAR(3) NOT NULL,
    applied BIT NOT NULL,
    captured_at DATETIME NOT NULL
);

CREATE INDEX IX_eco_sponsor_capture_promotion
    ON dbo.eco_sponsorship_captures(promotion_id);
CREATE UNIQUE INDEX UX_eco_sponsor_applied
    ON dbo.eco_sponsorship_captures(promotion_id) WHERE applied = 1;

CREATE TABLE dbo.eco_promotion_event_receipts (
    nonce NVARCHAR(32) NOT NULL,
    event_type NVARCHAR(10) NOT NULL,
    promotion_id INT NOT NULL CONSTRAINT FK_eco_event_receipt_promotion
        REFERENCES dbo.eco_sponsored_promotions(id),
    CONSTRAINT PK_eco_promotion_event_receipts PRIMARY KEY (nonce, event_type),
    CONSTRAINT CK_eco_event_type CHECK (event_type IN (N'impression', N'click'))
);

CREATE TABLE dbo.eco_promotion_attributions (
    booking_reference_id INT NOT NULL CONSTRAINT PK_eco_promotion_attributions PRIMARY KEY
        CONSTRAINT FK_eco_attribution_booking REFERENCES dbo.booking_references(id),
    nonce NVARCHAR(32) NOT NULL CONSTRAINT UQ_eco_attribution_nonce UNIQUE,
    promotion_id INT NOT NULL CONSTRAINT FK_eco_attribution_promotion
        REFERENCES dbo.eco_sponsored_promotions(id),
    recorded_on DATE NOT NULL
);

CREATE INDEX IX_eco_attribution_promotion
    ON dbo.eco_promotion_attributions(promotion_id);
