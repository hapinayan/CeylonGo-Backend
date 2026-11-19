-- Migration: 031_extend_promotion_booking_attribution.sql
-- Description: Phase 09 - Promotion Booking Attribution & Campaign Completion
-- Module: Reliable Promotion Booking Conversion Attribution & Campaign Status Expansion
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Add booking_reference_id Column to dbo.promotion_events
-- =============================================================================
IF COL_LENGTH('dbo.promotion_events', 'booking_reference_id') IS NULL
BEGIN
    ALTER TABLE dbo.promotion_events
    ADD booking_reference_id INT NULL;
END;
GO

-- =============================================================================
-- Batch 2: Add Foreign Key Constraint to dbo.promotion_events
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_promotion_events_booking_reference'
      AND parent_object_id = OBJECT_ID('dbo.promotion_events')
)
BEGIN
    ALTER TABLE dbo.promotion_events
    ADD CONSTRAINT FK_promotion_events_booking_reference
        FOREIGN KEY (booking_reference_id)
        REFERENCES dbo.booking_references(id)
        ON DELETE SET NULL;
END;
GO

-- =============================================================================
-- Batch 3: Create Filtered Unique Index on (campaign_id, booking_reference_id)
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_promotion_events_campaign_booking'
      AND object_id = OBJECT_ID('dbo.promotion_events')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX UQ_promotion_events_campaign_booking
        ON dbo.promotion_events(campaign_id, booking_reference_id)
        WHERE booking_reference_id IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 4: Non-clustered Index on booking_reference_id
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_promotion_events_booking_reference'
      AND object_id = OBJECT_ID('dbo.promotion_events')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_promotion_events_booking_reference
        ON dbo.promotion_events(booking_reference_id)
        WHERE booking_reference_id IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 5: Update CK_promotion_campaigns_status Constraint to Include 'approved' & 'rejected'
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_promotion_campaigns_status'
      AND parent_object_id = OBJECT_ID('dbo.promotion_campaigns')
)
BEGIN
    ALTER TABLE dbo.promotion_campaigns
    DROP CONSTRAINT CK_promotion_campaigns_status;
END;
GO

ALTER TABLE dbo.promotion_campaigns
ADD CONSTRAINT CK_promotion_campaigns_status
    CHECK (status IN (
        'draft',
        'pending_approval',
        'approved',
        'active',
        'paused',
        'completed',
        'cancelled',
        'rejected'
    ));
GO
