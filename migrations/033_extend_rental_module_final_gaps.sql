-- Migration: 033_extend_rental_module_final_gaps.sql
-- Description: Centralized Vehicle Rental Module Final Gaps (AR-02, AR-03, AR-04, AR-05, AR-06, AR-11, AR-12)
-- Module: Module 09 (Vehicle Rentals, Eligibility, Inspections, Rate Plans & Insurance)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Extend dbo.rental_eligibility_checks (AR-02: DOB, AR-11: Foreign Permits)
-- =============================================================================
IF COL_LENGTH('dbo.rental_eligibility_checks', 'date_of_birth') IS NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks
    ADD date_of_birth DATE NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_eligibility_dob', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks
    ADD CONSTRAINT CK_rental_eligibility_dob
        CHECK (date_of_birth IS NULL OR date_of_birth <= CAST(SYSUTCDATETIME() AS DATE));
END;
GO

IF COL_LENGTH('dbo.rental_eligibility_checks', 'recognition_permit_number') IS NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks
    ADD recognition_permit_number NVARCHAR(100) NULL;
END;
GO

IF COL_LENGTH('dbo.rental_eligibility_checks', 'recognition_permit_document_url') IS NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks
    ADD recognition_permit_document_url NVARCHAR(1000) NULL;
END;
GO

-- =============================================================================
-- Batch 2: Extend dbo.rental_vehicles (AR-03: Helmets, AR-04: Deposit, AR-05: Fuel)
-- =============================================================================
IF COL_LENGTH('dbo.rental_vehicles', 'helmet_count') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD helmet_count INT NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicles_helmet_count', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_helmet_count
        CHECK (helmet_count IS NULL OR helmet_count >= 0);
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'helmets_included') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD helmets_included BIT NULL;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'helmet_sizes') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD helmet_sizes NVARCHAR(100) NULL;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'security_deposit') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD security_deposit DECIMAL(12,2) NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicles_security_deposit', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_security_deposit
        CHECK (security_deposit IS NULL OR security_deposit >= 0);
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'fuel_policy') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD fuel_policy NVARCHAR(30) NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicles_fuel_policy', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_fuel_policy
        CHECK (fuel_policy IS NULL OR fuel_policy IN ('same_to_same', 'full_to_full', 'not_applicable'));
END;
GO

-- =============================================================================
-- Batch 3: Extend dbo.rental_inspections (AR-06: Digital Acknowledgement Metadata)
-- =============================================================================
IF COL_LENGTH('dbo.rental_inspections', 'acknowledged_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_inspections
    ADD acknowledged_at DATETIME2 NULL;
END;
GO

IF COL_LENGTH('dbo.rental_inspections', 'acknowledgement_ip') IS NULL
BEGIN
    ALTER TABLE dbo.rental_inspections
    ADD acknowledgement_ip NVARCHAR(50) NULL;
END;
GO

-- =============================================================================
-- Batch 4: Extend dbo.rental_vehicle_documents (AR-12: Insurance Coverage Details)
-- =============================================================================
IF COL_LENGTH('dbo.rental_vehicle_documents', 'coverage_amount') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD coverage_amount DECIMAL(12,2) NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicle_docs_coverage', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD CONSTRAINT CK_rental_vehicle_docs_coverage
        CHECK (coverage_amount IS NULL OR coverage_amount >= 0);
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'deductible_amount') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD deductible_amount DECIMAL(12,2) NULL;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicle_docs_deductible', 'C') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD CONSTRAINT CK_rental_vehicle_docs_deductible
        CHECK (deductible_amount IS NULL OR deductible_amount >= 0);
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'insurance_provider') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD insurance_provider NVARCHAR(150) NULL;
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'insurance_policy_number') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents
    ADD insurance_policy_number NVARCHAR(100) NULL;
END;
GO

PRINT 'Migration 033_extend_rental_module_final_gaps applied successfully.';
GO
