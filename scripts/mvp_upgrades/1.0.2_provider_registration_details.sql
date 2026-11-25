IF OBJECT_ID(N'dbo.mvp_schema_versions', N'U') IS NULL
    THROW 51045, 'The Academic MVP ledger is required.', 1;

IF COL_LENGTH(N'dbo.provider_profiles', N'registration_details') IS NULL
    ALTER TABLE dbo.provider_profiles ADD registration_details NVARCHAR(MAX) NULL;
GO
