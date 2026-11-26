IF OBJECT_ID(N'dbo.traveler_email_challenges', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.traveler_email_challenges (
        email NVARCHAR(254) COLLATE Latin1_General_100_BIN2 NOT NULL,
        registration_hash VARCHAR(64) NULL,
        otp_hash VARCHAR(64) NULL,
        full_name NVARCHAR(150) NULL,
        phone NVARCHAR(32) NULL,
        password_hash NVARCHAR(512) NULL,
        sent_at DATETIME2 NOT NULL,
        attempts SMALLINT NOT NULL,
        CONSTRAINT PK_traveler_email_challenges PRIMARY KEY (email),
        CONSTRAINT CK_traveler_email_challenges_attempts CHECK (attempts BETWEEN 0 AND 5)
    );
END;
GO
