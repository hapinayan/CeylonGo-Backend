-- ============================================================================
-- CeylonGo Academic MVP — SQL Server Cutover & Cleanup Script
-- TARGET: CeylonGoDB on VISHNUGA-KAJARASH\SQLEXPRESS
-- PRE-CONDITION: Requires explicit approval. Do NOT run without authorization.
-- BACKUP SAFETY NET: CeylonGoDB_pre_mvp_20261123T084300Z.bak (SHA-256 verified)
-- ============================================================================

USE [master];
GO

-- 1. Instance and Safety Verification
IF (SERVERPROPERTY('ServerName') NOT LIKE '%SQLEXPRESS%')
BEGIN
    RAISERROR('SAFETY HALT: Not connected to the dedicated SQLEXPRESS instance.', 16, 1);
    RETURN;
END;
GO

-- 2. Drop existing foreign keys and legacy 90 user tables in CeylonGoDB
USE [CeylonGoDB];
GO

DECLARE @sql NVARCHAR(MAX) = N'';

-- Drop all foreign keys
SELECT @sql += N'ALTER TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + 
               N' DROP CONSTRAINT ' + QUOTENAME(f.name) + N';' + CHAR(13)
FROM sys.foreign_keys f
JOIN sys.tables t ON f.parent_object_id = t.object_id
JOIN sys.schemas s ON t.schema_id = s.schema_id;

EXEC sp_executesql @sql;
SET @sql = N'';

-- Drop all user tables
SELECT @sql += N'DROP TABLE ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N';' + CHAR(13)
FROM sys.tables t
JOIN sys.schemas s ON t.schema_id = s.schema_id
WHERE t.is_ms_shipped = 0;

EXEC sp_executesql @sql;
GO

PRINT 'Legacy tables dropped successfully from CeylonGoDB.';
GO

-- 3. Apply Unified Fresh MVP Schema (17 Application Tables + 1 Ledger)
-- Runs scripts/bootstrap_mvp_schema.sql into [CeylonGoDB]
PRINT 'Ready to execute scripts/bootstrap_mvp_schema.sql into CeylonGoDB.';
GO

-- ============================================================================
-- OPTIONAL POST-CUTOVER CLEANUP (Run only after Gate 6 Go-Live Acceptance)
-- ============================================================================
-- DROP DATABASE [CeylonGoDB_MVP_Rehearsal];
-- DROP DATABASE [CeylonGoDB_IntegrationTest];
-- DROP DATABASE [CeylonGoDB_JWT_Test];
-- DROP DATABASE [CeylonGoDB_MergeTest];
-- DROP DATABASE [CeylonGoDB_Rehearsal];
-- DROP DATABASE [CeylonGoDB_Rehearsal_V2];
-- ============================================================================
