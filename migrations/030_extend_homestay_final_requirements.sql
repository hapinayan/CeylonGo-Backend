-- Migration: 030_extend_homestay_final_requirements.sql
-- Description: Phase 06 - Homestay Database Infrastructure Extensions
-- Module: Booking Payment Expiration, Advertising Payments, Listing-Specific Campaigns, Fixed Discounts & Financial Snapshots
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Add Payment Expiration & Commission Columns to dbo.homestay_bookings
-- =============================================================================
IF COL_LENGTH('dbo.homestay_bookings', 'approved_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD approved_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'payment_expires_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD payment_expires_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'expired_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD expired_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'commission_rate') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD commission_rate DECIMAL(5, 2) NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'commission_amount') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD commission_amount DECIMAL(12, 2) NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'provider_payable_amount') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD provider_payable_amount DECIMAL(12, 2) NULL;
END;
GO

-- =============================================================================
-- Batch 2: Update dbo.homestay_bookings Status Constraint for awaiting_payment
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_bookings_status'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    DROP CONSTRAINT CK_bookings_status;
END;

ALTER TABLE dbo.homestay_bookings
ADD CONSTRAINT CK_bookings_status
    CHECK (status IN (
        'pending',
        'awaiting_payment',
        'confirmed',
        'in_progress',
        'completed',
        'cancelled',
        'rejected',
        'expired'
    ));
GO

-- =============================================================================
-- Batch 3: Add Commission Validation Constraint to dbo.homestay_bookings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestay_bookings_commission'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD CONSTRAINT CK_homestay_bookings_commission
        CHECK (commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 100));
END;
GO

-- =============================================================================
-- Batch 4: Create Indexes on Newly Added Columns of dbo.homestay_bookings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestay_bookings_payment_expiry'
      AND object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestay_bookings_payment_expiry
        ON dbo.homestay_bookings(status, payment_status, payment_expires_at)
        WHERE payment_expires_at IS NOT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestay_bookings_approved_at'
      AND object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestay_bookings_approved_at
        ON dbo.homestay_bookings(approved_at)
        WHERE approved_at IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.advertising_payments Table
-- =============================================================================
IF OBJECT_ID('dbo.advertising_payments', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.advertising_payments (
        id INT IDENTITY(1,1) NOT NULL,
        campaign_id INT NOT NULL,
        provider_profile_id INT NOT NULL,
        order_reference NVARCHAR(100) NOT NULL,
        gateway_transaction_id NVARCHAR(100) NULL,
        amount DECIMAL(12, 2) NOT NULL,
        currency NVARCHAR(3) NOT NULL
            CONSTRAINT DF_advertising_payments_currency DEFAULT 'LKR',
        gateway_name NVARCHAR(50) NOT NULL
            CONSTRAINT DF_advertising_payments_gateway DEFAULT 'payhere',
        payment_status NVARCHAR(30) NOT NULL
            CONSTRAINT DF_advertising_payments_status DEFAULT 'pending',
        initiated_at DATETIME2 NOT NULL
            CONSTRAINT DF_advertising_payments_initiated DEFAULT SYSUTCDATETIME(),
        verified_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL
            CONSTRAINT DF_advertising_payments_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL
            CONSTRAINT DF_advertising_payments_updated DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_advertising_payments PRIMARY KEY (id),

        CONSTRAINT FK_advertising_payments_campaign
            FOREIGN KEY (campaign_id)
            REFERENCES dbo.promotion_campaigns(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_advertising_payments_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT UQ_advertising_payments_order
            UNIQUE (order_reference),

        CONSTRAINT CK_advertising_payments_amount
            CHECK (amount > 0),

        CONSTRAINT CK_advertising_payments_status
            CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded', 'cancelled'))
    );
END;
GO

-- =============================================================================
-- Batch 6: Create Indexes on dbo.advertising_payments
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_advertising_payments_gateway_tx'
      AND object_id = OBJECT_ID('dbo.advertising_payments')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UQ_advertising_payments_gateway_tx
        ON dbo.advertising_payments(gateway_transaction_id)
        WHERE gateway_transaction_id IS NOT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_advertising_payments_campaign'
      AND object_id = OBJECT_ID('dbo.advertising_payments')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_advertising_payments_campaign
        ON dbo.advertising_payments(campaign_id);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_advertising_payments_provider'
      AND object_id = OBJECT_ID('dbo.advertising_payments')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_advertising_payments_provider
        ON dbo.advertising_payments(provider_profile_id);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_advertising_payments_status'
      AND object_id = OBJECT_ID('dbo.advertising_payments')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_advertising_payments_status
        ON dbo.advertising_payments(payment_status);
END;
GO

-- =============================================================================
-- Batch 7: Add listing_reference_id Column to dbo.promotion_campaigns
-- =============================================================================
IF COL_LENGTH('dbo.promotion_campaigns', 'listing_reference_id') IS NULL
BEGIN
    ALTER TABLE dbo.promotion_campaigns
    ADD listing_reference_id INT NULL;
END;
GO

-- =============================================================================
-- Batch 8: Add Foreign Key for listing_reference_id on dbo.promotion_campaigns
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_promotion_campaigns_listing_reference'
      AND parent_object_id = OBJECT_ID('dbo.promotion_campaigns')
)
BEGIN
    ALTER TABLE dbo.promotion_campaigns
    ADD CONSTRAINT FK_promotion_campaigns_listing_reference
        FOREIGN KEY (listing_reference_id)
        REFERENCES dbo.listing_references(id)
        ON DELETE SET NULL;
END;
GO

-- =============================================================================
-- Batch 9: Create Filtered Index on dbo.promotion_campaigns(listing_reference_id)
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_promotion_campaigns_listing_active'
      AND object_id = OBJECT_ID('dbo.promotion_campaigns')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_promotion_campaigns_listing_active
        ON dbo.promotion_campaigns(listing_reference_id, status, starts_at, ends_at)
        WHERE listing_reference_id IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 10: Add discount_type and discount_amount to dbo.homestay_promotions
-- =============================================================================
IF COL_LENGTH('dbo.homestay_promotions', 'discount_type') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_promotions
    ADD discount_type NVARCHAR(20) NOT NULL
        CONSTRAINT DF_homestay_promotions_discount_type DEFAULT 'percentage';
END;

IF COL_LENGTH('dbo.homestay_promotions', 'discount_amount') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_promotions
    ADD discount_amount DECIMAL(12, 2) NULL;
END;
GO

-- =============================================================================
-- Batch 11: Alter discount_percent to NULL in dbo.homestay_promotions
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestay_promotions_discount'
      AND parent_object_id = OBJECT_ID('dbo.homestay_promotions')
)
BEGIN
    ALTER TABLE dbo.homestay_promotions
    DROP CONSTRAINT CK_homestay_promotions_discount;
END;

ALTER TABLE dbo.homestay_promotions
ALTER COLUMN discount_percent DECIMAL(5, 2) NULL;
GO

-- =============================================================================
-- Batch 12: Add Discount Type Constraint to dbo.homestay_promotions
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestay_promotions_type'
      AND parent_object_id = OBJECT_ID('dbo.homestay_promotions')
)
BEGIN
    ALTER TABLE dbo.homestay_promotions
    ADD CONSTRAINT CK_homestay_promotions_type
        CHECK (discount_type IN ('percentage', 'fixed'));
END;
GO

-- =============================================================================
-- Batch 13: Add Validation Constraint for Discount Mutually Exclusive Values
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestay_promotions_discount_valid'
      AND parent_object_id = OBJECT_ID('dbo.homestay_promotions')
)
BEGIN
    ALTER TABLE dbo.homestay_promotions
    ADD CONSTRAINT CK_homestay_promotions_discount_valid
        CHECK (
            (discount_type = 'percentage' AND discount_percent IS NOT NULL AND discount_percent > 0 AND discount_percent <= 100 AND discount_amount IS NULL)
            OR
            (discount_type = 'fixed' AND discount_amount IS NOT NULL AND discount_amount > 0 AND discount_percent IS NULL)
        );
END;
GO

-- =============================================================================
-- Batch 14: Add Commission Snapshot Columns to dbo.payment_transactions
-- =============================================================================
IF COL_LENGTH('dbo.payment_transactions', 'gross_amount') IS NULL
BEGIN
    ALTER TABLE dbo.payment_transactions
    ADD gross_amount DECIMAL(12, 2) NULL;
END;

IF COL_LENGTH('dbo.payment_transactions', 'commission_rate') IS NULL
BEGIN
    ALTER TABLE dbo.payment_transactions
    ADD commission_rate DECIMAL(5, 2) NULL;
END;

IF COL_LENGTH('dbo.payment_transactions', 'commission_amount') IS NULL
BEGIN
    ALTER TABLE dbo.payment_transactions
    ADD commission_amount DECIMAL(12, 2) NULL;
END;

IF COL_LENGTH('dbo.payment_transactions', 'provider_payable_amount') IS NULL
BEGIN
    ALTER TABLE dbo.payment_transactions
    ADD provider_payable_amount DECIMAL(12, 2) NULL;
END;
GO
