-- Rollback Script: 036_align_shared_payment_status_constraints_rollback.sql
-- Description: Restores the original Migration 027 CHECK constraints on
--              dbo.payment_transactions, dbo.payment_refunds, and dbo.provider_settlements
--              after preflight checks confirm no incompatible status rows exist.
-- Module: Shared Infrastructure (Payments, Refunds & Provider Settlements)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Refuses unsafe tightening via explicit THROW guards.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Rollback dbo.provider_settlements (CK_provider_settlements_status)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.provider_settlements
    WHERE settlement_status = 'completed'
)
    THROW 50353, 'Cannot rollback CK_provider_settlements_status while settlement_status = ''completed'' rows exist. Migrate them to ''paid'' first.', 1;

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
        'on_hold'
    ));
GO

-- =============================================================================
-- Batch 2: Rollback dbo.payment_refunds (CK_payment_refunds_status)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.payment_refunds
    WHERE refund_status IN ('approved', 'completed')
)
    THROW 50352, 'Cannot rollback CK_payment_refunds_status while ''approved'' or ''completed'' rows exist. Migrate them to ''processed''/''pending'' first.', 1;

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
        'processed',
        'failed',
        'rejected'
    ));
GO

-- =============================================================================
-- Batch 3: Rollback dbo.payment_transactions (CK_payment_transactions_status)
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM dbo.payment_transactions
    WHERE payment_status = 'paid'
)
    THROW 50351, 'Cannot rollback CK_payment_transactions_status while payment_status = ''paid'' rows exist. Migrate them to ''completed'' first.', 1;

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
        'completed',
        'failed',
        'refunded',
        'cancelled'
    ));
GO

PRINT 'Rollback 035_align_shared_payment_status_constraints_rollback completed successfully.';
GO
