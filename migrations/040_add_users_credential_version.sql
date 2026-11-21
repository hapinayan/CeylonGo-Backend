-- Add a per-user version for invalidating access JWTs after password reset.
ALTER TABLE dbo.users
    ADD credential_version BIGINT NOT NULL
        CONSTRAINT DF_users_credential_version DEFAULT (0) WITH VALUES;
GO

ALTER TABLE dbo.users WITH CHECK
    ADD CONSTRAINT CK_users_credential_version_nonnegative
        CHECK (credential_version >= 0);
GO
