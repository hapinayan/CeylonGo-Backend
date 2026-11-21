-- Proposal only. Requires approval and a transaction on an authorized target.
CREATE TABLE dbo.eco_sponsorship_captures (
    gateway_id NVARCHAR(100) NOT NULL PRIMARY KEY,
    promotion_id INT NOT NULL REFERENCES dbo.eco_sponsored_promotions(id),
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
    promotion_id INT NOT NULL REFERENCES dbo.eco_sponsored_promotions(id),
    CONSTRAINT PK_eco_promotion_event_receipts PRIMARY KEY (nonce, event_type),
    CONSTRAINT CK_eco_event_type CHECK (event_type IN (N'impression', N'click'))
);

CREATE TABLE dbo.eco_promotion_attributions (
    booking_reference_id INT NOT NULL PRIMARY KEY REFERENCES dbo.booking_references(id),
    nonce NVARCHAR(32) NOT NULL UNIQUE,
    promotion_id INT NOT NULL REFERENCES dbo.eco_sponsored_promotions(id),
    recorded_on DATE NOT NULL
);

CREATE INDEX IX_eco_attribution_promotion
    ON dbo.eco_promotion_attributions(promotion_id);
