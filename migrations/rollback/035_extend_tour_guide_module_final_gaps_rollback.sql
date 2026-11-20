-- Rollback Script: 035_extend_tour_guide_module_final_gaps_rollback.sql
-- Description: Reverses all schema changes applied by 035_extend_tour_guide_module_final_gaps.sql
-- Module: Module 05 (Local Tour Guide Management)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without error.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Rollback dbo.tour_guides changes
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guides_verified_experience'
      AND parent_object_id = OBJECT_ID('dbo.tour_guides')
)
BEGIN
    ALTER TABLE dbo.tour_guides DROP CONSTRAINT CK_tour_guides_verified_experience;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guides_whatsapp_opt_in'
      AND parent_object_id = OBJECT_ID('dbo.tour_guides')
)
BEGIN
    ALTER TABLE dbo.tour_guides DROP CONSTRAINT CK_tour_guides_whatsapp_opt_in;
END;
GO

IF COL_LENGTH('dbo.tour_guides', 'verified_years_of_experience') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guides DROP COLUMN verified_years_of_experience;
END;
GO

IF OBJECT_ID('dbo.DF_tour_guides_allow_whatsapp', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guides DROP CONSTRAINT DF_tour_guides_allow_whatsapp;
END;
GO

IF COL_LENGTH('dbo.tour_guides', 'allow_whatsapp_contact') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guides DROP COLUMN allow_whatsapp_contact;
END;
GO

IF COL_LENGTH('dbo.tour_guides', 'whatsapp_number') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guides DROP COLUMN whatsapp_number;
END;
GO

-- =============================================================================
-- Batch 2: Rollback dbo.tour_guide_packages changes
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_packages_type'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_packages')
)
BEGIN
    ALTER TABLE dbo.tour_guide_packages DROP CONSTRAINT CK_tour_guide_packages_type;
END;
GO

IF COL_LENGTH('dbo.tour_guide_packages', 'special_requirements') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages DROP COLUMN special_requirements;
END;
GO

IF COL_LENGTH('dbo.tour_guide_packages', 'meeting_instructions') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages DROP COLUMN meeting_instructions;
END;
GO

IF COL_LENGTH('dbo.tour_guide_packages', 'package_type') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages DROP COLUMN package_type;
END;
GO

-- =============================================================================
-- Batch 3: Rollback dbo.tour_guide_bookings indexes, constraints & columns
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_tour_guide_bookings_approved_at'
      AND object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    DROP INDEX IX_tour_guide_bookings_approved_at ON dbo.tour_guide_bookings;
END;

IF EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_tour_guide_bookings_payment_expiry'
      AND object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    DROP INDEX IX_tour_guide_bookings_payment_expiry ON dbo.tour_guide_bookings;
END;
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_provider_payable'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP CONSTRAINT CK_tour_guide_bookings_provider_payable;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_commission_amount'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP CONSTRAINT CK_tour_guide_bookings_commission_amount;
END;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_commission_rate'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP CONSTRAINT CK_tour_guide_bookings_commission_rate;
END;
GO

-- Preflight guard before tightening CK_tour_guide_bookings_status back to Migration 022
IF EXISTS (
    SELECT 1 FROM dbo.tour_guide_bookings
    WHERE booking_status = 'awaiting_payment'
)
    THROW 50340, 'Cannot rollback CK_tour_guide_bookings_status while awaiting_payment rows exist. Transition or archive them first.', 1;

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_status'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP CONSTRAINT CK_tour_guide_bookings_status;
END;

ALTER TABLE dbo.tour_guide_bookings
WITH CHECK ADD CONSTRAINT CK_tour_guide_bookings_status
    CHECK (booking_status IN (
        'pending',
        'confirmed',
        'in_progress',
        'completed',
        'cancelled',
        'rejected',
        'expired'
    ));
GO

IF COL_LENGTH('dbo.tour_guide_bookings', 'provider_payable_amount') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN provider_payable_amount;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'commission_amount') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN commission_amount;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'commission_rate') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN commission_rate;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'cash_received_at') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN cash_received_at;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'payment_expires_at') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN payment_expires_at;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'approved_at') IS NOT NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings DROP COLUMN approved_at;
END;
GO

PRINT 'Rollback 034_extend_tour_guide_module_final_gaps_rollback completed successfully.';
GO
