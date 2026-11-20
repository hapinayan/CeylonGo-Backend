-- Migration: 035_extend_tour_guide_module_final_gaps.sql
-- Description: Local Tour Guide Module 05 Extensions
--              (Online Payment Lifecycle, Commission Snapshots, Cash Receipt Timestamp,
--               Package Type/Meeting Instructions/Special Requirements,
--               WhatsApp Opt-In Contact & Admin-Verified Experience Years)
-- Module: Module 05 (Local Tour Guide Management)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Prerequisites: 022_create_local_tour_guide_module.sql, 027_create_shared_infrastructure.sql,
--                032_enforce_tour_guide_coordinate_pairs.sql, 033_extend_rental_module_final_gaps.sql,
--                034_create_eco_reservations.sql
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- =============================================================================
-- Batch 1: Extend dbo.tour_guide_bookings (Online Payment & Commission Snapshots)
-- Note: DECIMAL(18,2) matches dbo.tour_guide_bookings.total_price from Migration 022.
-- =============================================================================
IF COL_LENGTH('dbo.tour_guide_bookings', 'approved_at') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD approved_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'payment_expires_at') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD payment_expires_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'cash_received_at') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD cash_received_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'commission_rate') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD commission_rate DECIMAL(5, 2) NULL;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'commission_amount') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD commission_amount DECIMAL(18, 2) NULL;
END;

IF COL_LENGTH('dbo.tour_guide_bookings', 'provider_payable_amount') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    ADD provider_payable_amount DECIMAL(18, 2) NULL;
END;
GO

-- =============================================================================
-- Batch 2: Update dbo.tour_guide_bookings Status Constraint for awaiting_payment
-- Existing Migration 022 values are a strict subset of this list (100% compatible).
-- =============================================================================
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_status'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    DROP CONSTRAINT CK_tour_guide_bookings_status;
END;

ALTER TABLE dbo.tour_guide_bookings
WITH CHECK ADD CONSTRAINT CK_tour_guide_bookings_status
    CHECK (booking_status IN (
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
-- Batch 3: Add Financial Validation Constraints & Filtered Indexes on dbo.tour_guide_bookings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_commission_rate'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    WITH CHECK ADD CONSTRAINT CK_tour_guide_bookings_commission_rate
        CHECK (commission_rate IS NULL OR (commission_rate >= 0 AND commission_rate <= 100));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_commission_amount'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    WITH CHECK ADD CONSTRAINT CK_tour_guide_bookings_commission_amount
        CHECK (commission_amount IS NULL OR commission_amount >= 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_bookings_provider_payable'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    ALTER TABLE dbo.tour_guide_bookings
    WITH CHECK ADD CONSTRAINT CK_tour_guide_bookings_provider_payable
        CHECK (provider_payable_amount IS NULL OR provider_payable_amount >= 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_tour_guide_bookings_payment_expiry'
      AND object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_tour_guide_bookings_payment_expiry
        ON dbo.tour_guide_bookings(booking_status, payment_status, payment_expires_at)
        WHERE payment_expires_at IS NOT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_tour_guide_bookings_approved_at'
      AND object_id = OBJECT_ID('dbo.tour_guide_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_tour_guide_bookings_approved_at
        ON dbo.tour_guide_bookings(approved_at)
        WHERE approved_at IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 4: Extend dbo.tour_guide_packages (GS-03 Meeting Instructions, GS-06 Special Requirements)
-- =============================================================================
IF COL_LENGTH('dbo.tour_guide_packages', 'package_type') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages
    ADD package_type NVARCHAR(20) NULL;
END;

IF COL_LENGTH('dbo.tour_guide_packages', 'meeting_instructions') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages
    ADD meeting_instructions NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.tour_guide_packages', 'special_requirements') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guide_packages
    ADD special_requirements NVARCHAR(2000) NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guide_packages_type'
      AND parent_object_id = OBJECT_ID('dbo.tour_guide_packages')
)
BEGIN
    ALTER TABLE dbo.tour_guide_packages
    WITH CHECK ADD CONSTRAINT CK_tour_guide_packages_type
        CHECK (package_type IS NULL OR package_type IN ('half_day', 'full_day', 'custom'));
END;
GO

-- =============================================================================
-- Batch 5: Extend dbo.tour_guides (TR-18/PR-17 WhatsApp Opt-In, GS-01 Verified Experience)
-- Existing rows default to allow_whatsapp_contact = 0 and NULL verified_years_of_experience.
-- =============================================================================
IF COL_LENGTH('dbo.tour_guides', 'whatsapp_number') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guides
    ADD whatsapp_number NVARCHAR(30) NULL;
END;

IF COL_LENGTH('dbo.tour_guides', 'allow_whatsapp_contact') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guides
    ADD allow_whatsapp_contact BIT NOT NULL
        CONSTRAINT DF_tour_guides_allow_whatsapp DEFAULT 0;
END;

IF COL_LENGTH('dbo.tour_guides', 'verified_years_of_experience') IS NULL
BEGIN
    ALTER TABLE dbo.tour_guides
    ADD verified_years_of_experience INT NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guides_whatsapp_opt_in'
      AND parent_object_id = OBJECT_ID('dbo.tour_guides')
)
BEGIN
    ALTER TABLE dbo.tour_guides
    WITH CHECK ADD CONSTRAINT CK_tour_guides_whatsapp_opt_in
        CHECK (
            allow_whatsapp_contact = 0
            OR (whatsapp_number IS NOT NULL AND LEN(LTRIM(RTRIM(whatsapp_number))) >= 7)
        );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_tour_guides_verified_experience'
      AND parent_object_id = OBJECT_ID('dbo.tour_guides')
)
BEGIN
    ALTER TABLE dbo.tour_guides
    WITH CHECK ADD CONSTRAINT CK_tour_guides_verified_experience
        CHECK (
            verified_years_of_experience IS NULL
            OR (verified_years_of_experience >= 0 AND verified_years_of_experience <= 80)
        );
END;
GO

PRINT 'Migration 034_extend_tour_guide_module_final_gaps applied successfully.';
GO
