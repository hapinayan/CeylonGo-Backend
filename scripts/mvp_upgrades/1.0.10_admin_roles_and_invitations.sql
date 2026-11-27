-- ============================================================================
-- CeylonGo Academic MVP V1.0.10 Upgrade
-- Purpose:
--   1. Support 'super_admin' in dbo.users role CHECK constraint.
--   2. Create persistent dbo.admin_invitations table for Super Admin invitations.
-- ============================================================================

-- Step 1: Extend dbo.users role CHECK constraint to include 'super_admin'
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'ck_users_role' AND parent_object_id = OBJECT_ID('dbo.users')
)
BEGIN
    ALTER TABLE dbo.users DROP CONSTRAINT ck_users_role;
END;

ALTER TABLE dbo.users WITH CHECK ADD CONSTRAINT ck_users_role
    CHECK (role IN ('traveler', 'provider', 'admin', 'super_admin'));

-- Step 2: Create dbo.admin_invitations table
IF OBJECT_ID('dbo.admin_invitations', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.admin_invitations (
        id BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT pk_admin_invitations PRIMARY KEY CLUSTERED,
        email NVARCHAR(254) COLLATE Latin1_General_CI_AS NOT NULL,
        full_name NVARCHAR(150) NOT NULL,
        role VARCHAR(20) NOT NULL,
        department NVARCHAR(100) NULL,
        token_hash NVARCHAR(255) NOT NULL,
        status VARCHAR(20) NOT NULL CONSTRAINT df_admin_invitations_status DEFAULT 'pending',
        invitation_note NVARCHAR(500) NULL,
        invited_by_id BIGINT NULL CONSTRAINT fk_admin_invitations_invited_by REFERENCES dbo.users(id),
        invited_by_email NVARCHAR(254) NULL,
        expires_at DATETIME2(3) NOT NULL,
        accepted_at DATETIME2(3) NULL,
        revoked_at DATETIME2(3) NULL,
        created_at DATETIME2(3) NOT NULL CONSTRAINT df_admin_invitations_created_at DEFAULT SYSUTCDATETIME(),
        CONSTRAINT uq_admin_invitations_token_hash UNIQUE NONCLUSTERED (token_hash),
        CONSTRAINT ck_admin_invitations_role CHECK (role IN ('admin', 'super_admin')),
        CONSTRAINT ck_admin_invitations_status CHECK (status IN ('pending', 'accepted', 'revoked', 'expired'))
    );

    CREATE NONCLUSTERED INDEX ix_admin_invitations_email_status
        ON dbo.admin_invitations (email, status);
    CREATE NONCLUSTERED INDEX ix_admin_invitations_token_hash
        ON dbo.admin_invitations (token_hash);
END;
