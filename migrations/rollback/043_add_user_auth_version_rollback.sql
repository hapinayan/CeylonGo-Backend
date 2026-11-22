-- Stop application versions that depend on this column before rollback.
-- Coordinate invalidation of outstanding traveler JWTs before a later rollout.
SET XACT_ABORT ON;
BEGIN TRANSACTION;
IF COL_LENGTH(N'dbo.users', N'auth_version') IS NOT NULL
BEGIN
    ALTER TABLE dbo.users DROP CONSTRAINT DF_users_auth_version;
    ALTER TABLE dbo.users DROP COLUMN auth_version;
END;
COMMIT TRANSACTION;
GO
