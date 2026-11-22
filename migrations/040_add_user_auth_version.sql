IF COL_LENGTH(N'dbo.users', N'auth_version') IS NULL
BEGIN
    ALTER TABLE dbo.users ADD auth_version BIGINT NOT NULL
        CONSTRAINT DF_users_auth_version DEFAULT (0);
END;
GO
