-- Migration: 036_align_shared_payment_status_constraints.sql
-- Description: Align pre-existing shared payment table CHECK constraints from
--              Migration 027 with the status values written by shared
--              app/routers/payments.py across Homestay, Rental, and Tour Guide modules.
-- Module: Shared Infrastructure (Payments, Refunds & Provider Settlements)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Prerequisites: 027_create_shared_infrastructure.sql, 034_create_eco_reservations.sql,
--                035_extend_tour_guide_module_final_gaps.sql
-- Safe & Idempotent: Can be executed multiple times safely without data loss.
-- Existing Data Compatibility: Every Migration 027 value is preserved as a strict subset.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Align dbo.payment_transactions (CK_payment_transactions_status)
-- Adds 'paid' alongside Migration 027's ('pending','completed','failed','refunded','cancelled').
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_payment_transactions_status'
      AND parent_object_id = OBJECT_ID('dbo.payment_transactions')
)
BEGIN
    ALTER TABLE dbo.payment_transactions
    DROP CONSTRAINT CK_payment_transactions_status;
END;

ALTER TABLE dbo.payment_transactions
WITH CHECK ADD CONSTRAINT CK_payment_transactions_status
    CHECK (payment_status IN (
        'pending',
        'paid',
        'completed',
        'failed',
        'refunded',
        'cancelled'
    ));
GO

-- =============================================================================
-- Batch 2: Align dbo.payment_refunds (CK_payment_refunds_status)
-- Adds 'approved' and 'completed' alongside Migration 027's
-- ('pending','processed','failed','rejected').
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_payment_refunds_status'
      AND parent_object_id = OBJECT_ID('dbo.payment_refunds')
)
BEGIN
    ALTER TABLE dbo.payment_refunds
    DROP CONSTRAINT CK_payment_refunds_status;
END;

ALTER TABLE dbo.payment_refunds
WITH CHECK ADD CONSTRAINT CK_payment_refunds_status
    CHECK (refund_status IN (
        'pending',
        'approved',
        'processed',
        'completed',
        'failed',
        'rejected'
    ));
GO

-- =============================================================================
-- Batch 3: Align dbo.provider_settlements (CK_provider_settlements_status)
-- Adds 'completed' alongside Migration 027's ('pending','approved','paid','on_hold').
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_provider_settlements_status'
      AND parent_object_id = OBJECT_ID('dbo.provider_settlements')
)
BEGIN
    ALTER TABLE dbo.provider_settlements
    DROP CONSTRAINT CK_provider_settlements_status;
END;

ALTER TABLE dbo.provider_settlements
WITH CHECK ADD CONSTRAINT CK_provider_settlements_status
    CHECK (settlement_status IN (
        'pending',
        'approved',
        'paid',
        'completed',
        'on_hold'
    ));
GO

PRINT 'Migration 035_align_shared_payment_status_constraints applied successfully.';
GO
