-- Rollback Script: 033_extend_rental_module_final_gaps_rollback.sql
-- Description: Reverses all schema changes applied by 033_extend_rental_module_final_gaps.sql
-- Module: Module 09 (Vehicle Rentals)
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without error.

-- =============================================================================
-- Batch 1: Rollback dbo.rental_vehicle_documents changes
-- =============================================================================
IF OBJECT_ID('dbo.CK_rental_vehicle_docs_coverage', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP CONSTRAINT CK_rental_vehicle_docs_coverage;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicle_docs_deductible', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP CONSTRAINT CK_rental_vehicle_docs_deductible;
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'insurance_policy_number') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP COLUMN insurance_policy_number;
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'insurance_provider') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP COLUMN insurance_provider;
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'deductible_amount') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP COLUMN deductible_amount;
END;
GO

IF COL_LENGTH('dbo.rental_vehicle_documents', 'coverage_amount') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicle_documents DROP COLUMN coverage_amount;
END;
GO

-- =============================================================================
-- Batch 2: Rollback dbo.rental_inspections changes
-- =============================================================================
IF COL_LENGTH('dbo.rental_inspections', 'acknowledgement_ip') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_inspections DROP COLUMN acknowledgement_ip;
END;
GO

IF COL_LENGTH('dbo.rental_inspections', 'acknowledged_at') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_inspections DROP COLUMN acknowledged_at;
END;
GO

-- =============================================================================
-- Batch 3: Rollback dbo.rental_vehicles changes
-- =============================================================================
IF OBJECT_ID('dbo.CK_rental_vehicles_fuel_policy', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT CK_rental_vehicles_fuel_policy;
END;
GO

IF OBJECT_ID('dbo.DF_rental_vehicles_fuel_policy', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT DF_rental_vehicles_fuel_policy;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'fuel_policy') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP COLUMN fuel_policy;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicles_security_deposit', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT CK_rental_vehicles_security_deposit;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'security_deposit') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP COLUMN security_deposit;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'helmet_sizes') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP COLUMN helmet_sizes;
END;
GO

IF OBJECT_ID('dbo.DF_rental_vehicles_helmets_included', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT DF_rental_vehicles_helmets_included;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'helmets_included') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP COLUMN helmets_included;
END;
GO

IF OBJECT_ID('dbo.CK_rental_vehicles_helmet_count', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT CK_rental_vehicles_helmet_count;
END;
GO

IF OBJECT_ID('dbo.DF_rental_vehicles_helmet_count', 'D') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP CONSTRAINT DF_rental_vehicles_helmet_count;
END;
GO

IF COL_LENGTH('dbo.rental_vehicles', 'helmet_count') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles DROP COLUMN helmet_count;
END;
GO

-- =============================================================================
-- Batch 4: Rollback dbo.rental_eligibility_checks changes
-- =============================================================================
IF COL_LENGTH('dbo.rental_eligibility_checks', 'recognition_permit_document_url') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks DROP COLUMN recognition_permit_document_url;
END;
GO

IF COL_LENGTH('dbo.rental_eligibility_checks', 'recognition_permit_number') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks DROP COLUMN recognition_permit_number;
END;
GO

IF OBJECT_ID('dbo.CK_rental_eligibility_dob', 'C') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks DROP CONSTRAINT CK_rental_eligibility_dob;
END;
GO

IF COL_LENGTH('dbo.rental_eligibility_checks', 'date_of_birth') IS NOT NULL
BEGIN
    ALTER TABLE dbo.rental_eligibility_checks DROP COLUMN date_of_birth;
END;
GO

PRINT 'Rollback of migration 033_extend_rental_module_final_gaps completed successfully.';
GO
