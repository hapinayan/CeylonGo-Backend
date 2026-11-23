-- CeylonGo Academic MVP — Unified Greenfield Schema (17 Application Tables + 1 Ledger)
-- Reference: FRESH_DB_02_MVP_SCHEMA_DESIGN.md

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- ============================================================================
-- 1. users
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'users' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.users (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_users PRIMARY KEY CLUSTERED,
        email NVARCHAR(254) COLLATE Latin1_General_CI_AS NOT NULL,
        full_name NVARCHAR(120) NOT NULL,
        phone NVARCHAR(32) NULL,
        hashed_password NVARCHAR(512) NOT NULL,
        role VARCHAR(12) NOT NULL,
        is_active BIT NOT NULL CONSTRAINT df_users_is_active DEFAULT 1,
        auth_version BIGINT NOT NULL CONSTRAINT df_users_auth_version DEFAULT 0,
        credential_version BIGINT NOT NULL CONSTRAINT df_users_credential_version DEFAULT 0,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT uq_users_email UNIQUE NONCLUSTERED (email),
        CONSTRAINT ck_users_role CHECK (role IN ('traveler', 'provider', 'admin')),
        CONSTRAINT ck_users_auth_version CHECK (auth_version >= 0),
        CONSTRAINT ck_users_credential_version CHECK (credential_version >= 0)
    );

    CREATE NONCLUSTERED INDEX ix_users_role_active ON dbo.users (role, is_active, id);
END;
GO

-- ============================================================================
-- 2. password_resets
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'password_resets' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.password_resets (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_password_resets PRIMARY KEY CLUSTERED,
        user_id BIGINT NOT NULL,
        token_hash BINARY(32) NOT NULL,
        expires_at DATETIME2(3) NOT NULL,
        used_at DATETIME2(3) NULL,
        created_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_password_resets_user FOREIGN KEY (user_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_password_resets_token_hash UNIQUE NONCLUSTERED (token_hash),
        CONSTRAINT ck_password_resets_expires CHECK (expires_at > created_at),
        CONSTRAINT ck_password_resets_used CHECK (used_at IS NULL OR (used_at >= created_at AND used_at <= expires_at))
    );

    CREATE NONCLUSTERED INDEX ix_password_resets_user_expires ON dbo.password_resets (user_id, expires_at);
END;
GO

-- ============================================================================
-- 3. provider_profiles
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'provider_profiles' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.provider_profiles (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_provider_profiles PRIMARY KEY CLUSTERED,
        user_id BIGINT NOT NULL,
        business_name NVARCHAR(160) NOT NULL,
        phone NVARCHAR(32) NOT NULL,
        district NVARCHAR(80) NOT NULL,
        verification_status VARCHAR(12) NOT NULL,
        submitted_at DATETIME2(3) NULL,
        decided_at DATETIME2(3) NULL,
        decided_by BIGINT NULL,
        decision_reason NVARCHAR(500) NULL,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_provider_profiles_user FOREIGN KEY (user_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_provider_profiles_decided_by FOREIGN KEY (decided_by) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_provider_profiles_user_id UNIQUE NONCLUSTERED (user_id),
        CONSTRAINT ck_provider_profiles_verification_status CHECK (verification_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended'))
    );

    CREATE NONCLUSTERED INDEX ix_provider_profiles_status_submitted ON dbo.provider_profiles (verification_status, submitted_at, id);
    CREATE NONCLUSTERED INDEX ix_provider_profiles_decided_by ON dbo.provider_profiles (decided_by);
END;
GO

-- ============================================================================
-- 4. listings
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'listings' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.listings (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_listings PRIMARY KEY CLUSTERED,
        provider_id BIGINT NOT NULL,
        category VARCHAR(16) NOT NULL,
        title NVARCHAR(160) NOT NULL,
        description NVARCHAR(2000) NOT NULL,
        district NVARCHAR(80) NOT NULL,
        locality NVARCHAR(100) NOT NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,
        price DECIMAL(12,2) NOT NULL,
        currency CHAR(3) NOT NULL CONSTRAINT df_listings_currency DEFAULT 'LKR',
        approval_status VARCHAR(12) NOT NULL,
        is_active BIT NOT NULL CONSTRAINT df_listings_is_active DEFAULT 0,
        submitted_at DATETIME2(3) NULL,
        decided_by BIGINT NULL,
        decided_at DATETIME2(3) NULL,
        decision_reason NVARCHAR(500) NULL,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_listings_provider FOREIGN KEY (provider_id) REFERENCES dbo.provider_profiles (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_listings_decided_by FOREIGN KEY (decided_by) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_listings_id_provider UNIQUE NONCLUSTERED (id, provider_id),
        CONSTRAINT ck_listings_category CHECK (category IN ('homestay', 'bike', 'bicycle', 'adventure', 'guide')),
        CONSTRAINT ck_listings_approval_status CHECK (approval_status IN ('draft', 'pending', 'approved', 'rejected')),
        CONSTRAINT ck_listings_active_requires_approved CHECK (is_active = 0 OR approval_status = 'approved'),
        CONSTRAINT ck_listings_price_nonnegative CHECK (price >= 0),
        CONSTRAINT ck_listings_currency CHECK (currency = 'LKR'),
        CONSTRAINT ck_listings_coordinates CHECK (
            (latitude IS NULL AND longitude IS NULL) OR 
            (latitude IS NOT NULL AND longitude IS NOT NULL AND latitude BETWEEN -90.000000 AND 90.000000 AND longitude BETWEEN -180.000000 AND 180.000000)
        )
    );

    CREATE NONCLUSTERED INDEX ix_listings_category_approval ON dbo.listings (category, approval_status, is_active, district, id);
    CREATE NONCLUSTERED INDEX ix_listings_provider_approval ON dbo.listings (provider_id, approval_status, id);
    CREATE NONCLUSTERED INDEX ix_listings_decided_by ON dbo.listings (decided_by);
END;
GO

-- ============================================================================
-- 5. listing_media
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'listing_media' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.listing_media (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_listing_media PRIMARY KEY CLUSTERED,
        listing_id BIGINT NOT NULL,
        storage_key NVARCHAR(450) NOT NULL,
        sort_order SMALLINT NOT NULL,
        alt_text NVARCHAR(160) NULL,
        created_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_listing_media_listing FOREIGN KEY (listing_id) REFERENCES dbo.listings (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_listing_media_storage_key UNIQUE NONCLUSTERED (storage_key),
        CONSTRAINT uq_listing_media_listing_sort UNIQUE NONCLUSTERED (listing_id, sort_order),
        CONSTRAINT ck_listing_media_sort_order CHECK (sort_order >= 0)
    );
END;
GO

-- ============================================================================
-- 6. inventory_units
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'inventory_units' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.inventory_units (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_inventory_units PRIMARY KEY CLUSTERED,
        listing_id BIGINT NOT NULL,
        provider_id BIGINT NOT NULL,
        label NVARCHAR(100) NOT NULL,
        capacity INT NOT NULL CONSTRAINT df_inventory_units_capacity DEFAULT 1,
        is_active BIT NOT NULL CONSTRAINT df_inventory_units_is_active DEFAULT 1,
        CONSTRAINT fk_inventory_units_listing_provider FOREIGN KEY (listing_id, provider_id) REFERENCES dbo.listings (id, provider_id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_inventory_units_listing_label UNIQUE NONCLUSTERED (listing_id, label),
        CONSTRAINT uq_inventory_units_id_listing UNIQUE NONCLUSTERED (id, listing_id),
        CONSTRAINT uq_inventory_units_id_provider UNIQUE NONCLUSTERED (id, provider_id),
        CONSTRAINT ck_inventory_units_capacity CHECK (capacity > 0)
    );

    CREATE NONCLUSTERED INDEX ix_inventory_units_listing_provider ON dbo.inventory_units (listing_id, provider_id);
END;
GO

-- ============================================================================
-- 7. provider_documents
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'provider_documents' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.provider_documents (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_provider_documents PRIMARY KEY CLUSTERED,
        provider_id BIGINT NOT NULL,
        unit_id BIGINT NULL,
        kind VARCHAR(32) NOT NULL,
        storage_key NVARCHAR(450) NOT NULL,
        sha256 BINARY(32) NOT NULL,
        status VARCHAR(12) NOT NULL,
        expires_on DATE NULL,
        reviewed_by BIGINT NULL,
        reviewed_at DATETIME2(3) NULL,
        created_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_provider_documents_provider FOREIGN KEY (provider_id) REFERENCES dbo.provider_profiles (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_provider_documents_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_provider_documents_unit_provider FOREIGN KEY (unit_id, provider_id) REFERENCES dbo.inventory_units (id, provider_id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_provider_documents_storage_key UNIQUE NONCLUSTERED (storage_key),
        CONSTRAINT ck_provider_documents_kind CHECK (kind IN ('identity', 'business', 'revenue_license', 'insurance')),
        CONSTRAINT ck_provider_documents_status CHECK (status IN ('pending', 'verified', 'rejected')),
        CONSTRAINT ck_provider_documents_unit_consistency CHECK (
            ((kind IN ('revenue_license', 'insurance') AND unit_id IS NOT NULL)) OR
            ((kind IN ('identity', 'business') AND unit_id IS NULL))
        )
    );

    CREATE NONCLUSTERED INDEX ix_provider_documents_lookup ON dbo.provider_documents (provider_id, unit_id, kind, status);
    CREATE NONCLUSTERED INDEX ix_provider_documents_unit_provider ON dbo.provider_documents (unit_id, provider_id);
    CREATE NONCLUSTERED INDEX ix_provider_documents_reviewed_by ON dbo.provider_documents (reviewed_by);
END;
GO

-- ============================================================================
-- 8. provider_email_challenges
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'provider_email_challenges' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.provider_email_challenges (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_provider_email_challenges PRIMARY KEY CLUSTERED,
        email NVARCHAR(254) NOT NULL,
        challenge_hash BINARY(32) NOT NULL,
        expires_at DATETIME2(3) NOT NULL,
        verified_at DATETIME2(3) NULL,
        consumed_at DATETIME2(3) NULL,
        attempts SMALLINT NOT NULL CONSTRAINT df_challenges_attempts DEFAULT 0,
        created_at DATETIME2(3) NOT NULL,
        CONSTRAINT ck_challenges_attempts CHECK (attempts BETWEEN 0 AND 10),
        CONSTRAINT ck_challenges_expires CHECK (expires_at > created_at),
        CONSTRAINT ck_challenges_verified CHECK (verified_at IS NULL OR (verified_at >= created_at AND verified_at <= expires_at)),
        CONSTRAINT ck_challenges_consumed CHECK (consumed_at IS NULL OR (verified_at IS NOT NULL AND consumed_at >= verified_at AND consumed_at <= expires_at))
    );

    CREATE NONCLUSTERED INDEX ix_challenges_email_expires ON dbo.provider_email_challenges (email, expires_at, id);
END;
GO

-- ============================================================================
-- 9. availability_slots
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'availability_slots' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.availability_slots (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_availability_slots PRIMARY KEY CLUSTERED,
        unit_id BIGINT NOT NULL,
        listing_id BIGINT NOT NULL,
        starts_at DATETIME2(3) NOT NULL,
        ends_at DATETIME2(3) NOT NULL,
        capacity INT NOT NULL,
        is_open BIT NOT NULL CONSTRAINT df_availability_slots_is_open DEFAULT 1,
        CONSTRAINT fk_availability_slots_unit_listing FOREIGN KEY (unit_id, listing_id) REFERENCES dbo.inventory_units (id, listing_id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_availability_slots_unit_starts UNIQUE NONCLUSTERED (unit_id, starts_at),
        CONSTRAINT uq_availability_slots_id_listing UNIQUE NONCLUSTERED (id, listing_id),
        CONSTRAINT ck_availability_slots_dates CHECK (ends_at > starts_at),
        CONSTRAINT ck_availability_slots_capacity CHECK (capacity > 0)
    );

    CREATE NONCLUSTERED INDEX ix_availability_slots_lookup ON dbo.availability_slots (listing_id, is_open, starts_at, id);
END;
GO

-- ============================================================================
-- 10. bookings
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'bookings' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.bookings (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_bookings PRIMARY KEY CLUSTERED,
        listing_id BIGINT NOT NULL,
        traveler_id BIGINT NOT NULL,
        request_key UNIQUEIDENTIFIER NOT NULL,
        request_hash BINARY(32) NOT NULL,
        starts_at DATETIME2(3) NOT NULL,
        ends_at DATETIME2(3) NOT NULL,
        quantity INT NOT NULL,
        total_amount DECIMAL(12,2) NOT NULL,
        currency CHAR(3) NOT NULL CONSTRAINT df_bookings_currency DEFAULT 'LKR',
        status VARCHAR(12) NOT NULL,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_bookings_listing FOREIGN KEY (listing_id) REFERENCES dbo.listings (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_bookings_traveler FOREIGN KEY (traveler_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_bookings_traveler_request UNIQUE NONCLUSTERED (traveler_id, request_key),
        CONSTRAINT uq_bookings_id_listing UNIQUE NONCLUSTERED (id, listing_id),
        CONSTRAINT ck_bookings_dates CHECK (ends_at > starts_at),
        CONSTRAINT ck_bookings_quantity CHECK (quantity > 0),
        CONSTRAINT ck_bookings_amount CHECK (total_amount >= 0),
        CONSTRAINT ck_bookings_currency CHECK (currency = 'LKR'),
        CONSTRAINT ck_bookings_status CHECK (status IN ('pending', 'confirmed', 'completed', 'rejected', 'cancelled'))
    );

    CREATE NONCLUSTERED INDEX ix_bookings_listing_status_starts ON dbo.bookings (listing_id, status, starts_at, id);
    CREATE NONCLUSTERED INDEX ix_bookings_traveler_created ON dbo.bookings (traveler_id, created_at, id);
END;
GO

-- ============================================================================
-- 11. booking_allocations
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'booking_allocations' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.booking_allocations (
        booking_id BIGINT NOT NULL,
        slot_id BIGINT NOT NULL,
        listing_id BIGINT NOT NULL,
        quantity INT NOT NULL,
        reserved_at DATETIME2(3) NOT NULL,
        released_at DATETIME2(3) NULL,
        CONSTRAINT pk_booking_allocations PRIMARY KEY CLUSTERED (booking_id, slot_id),
        CONSTRAINT fk_booking_allocations_booking_listing FOREIGN KEY (booking_id, listing_id) REFERENCES dbo.bookings (id, listing_id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_booking_allocations_slot_listing FOREIGN KEY (slot_id, listing_id) REFERENCES dbo.availability_slots (id, listing_id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT ck_booking_allocations_quantity CHECK (quantity > 0),
        CONSTRAINT ck_booking_allocations_released CHECK (released_at IS NULL OR released_at >= reserved_at)
    );

    CREATE NONCLUSTERED INDEX ix_booking_allocations_slot_unreleased ON dbo.booking_allocations (slot_id) INCLUDE (quantity, booking_id) WHERE released_at IS NULL;
    CREATE NONCLUSTERED INDEX ix_booking_allocations_slot_listing ON dbo.booking_allocations (slot_id, listing_id);
END;
GO

-- ============================================================================
-- 12. booking_status_history
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'booking_status_history' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.booking_status_history (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_booking_status_history PRIMARY KEY CLUSTERED,
        booking_id BIGINT NOT NULL,
        actor_user_id BIGINT NOT NULL,
        from_status VARCHAR(12) NULL,
        to_status VARCHAR(12) NOT NULL,
        reason NVARCHAR(500) NULL,
        occurred_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_booking_status_history_booking FOREIGN KEY (booking_id) REFERENCES dbo.bookings (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_booking_status_history_actor FOREIGN KEY (actor_user_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT ck_booking_status_history_transitions CHECK (
            ((from_status IS NULL AND to_status = 'pending')) OR
            ((from_status = 'pending' AND to_status IN ('confirmed', 'rejected', 'cancelled'))) OR
            ((from_status = 'confirmed' AND to_status IN ('completed', 'cancelled')))
        )
    );

    CREATE NONCLUSTERED INDEX ix_booking_status_history_booking ON dbo.booking_status_history (booking_id, occurred_at, id);
    CREATE NONCLUSTERED INDEX ix_booking_status_history_actor ON dbo.booking_status_history (actor_user_id);
END;
GO

-- ============================================================================
-- 13. booking_payments
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'booking_payments' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.booking_payments (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_booking_payments PRIMARY KEY CLUSTERED,
        booking_id BIGINT NOT NULL,
        request_key UNIQUEIDENTIFIER NOT NULL,
        request_hash BINARY(32) NOT NULL,
        attempt_no INT NOT NULL,
        method VARCHAR(16) NOT NULL,
        status VARCHAR(16) NOT NULL,
        display_amount DECIMAL(12,2) NOT NULL,
        currency CHAR(3) NOT NULL CONSTRAINT df_booking_payments_currency DEFAULT 'LKR',
        created_at DATETIME2(3) NOT NULL,
        resolved_at DATETIME2(3) NULL,
        CONSTRAINT fk_booking_payments_booking FOREIGN KEY (booking_id) REFERENCES dbo.bookings (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT uq_booking_payments_request_key UNIQUE NONCLUSTERED (booking_id, request_key),
        CONSTRAINT uq_booking_payments_attempt UNIQUE NONCLUSTERED (booking_id, attempt_no),
        CONSTRAINT ck_booking_payments_attempt CHECK (attempt_no > 0),
        CONSTRAINT ck_booking_payments_amount CHECK (display_amount >= 0),
        CONSTRAINT ck_booking_payments_currency CHECK (currency = 'LKR'),
        CONSTRAINT ck_booking_payments_method_status CHECK (
            ((method = 'simulated' AND status IN ('demo_pending', 'demo_failed', 'demo_succeeded'))) OR
            ((method = 'at_location' AND status = 'unpaid'))
        ),
        CONSTRAINT ck_booking_payments_resolution CHECK (
            ((status IN ('demo_succeeded', 'demo_failed') AND resolved_at IS NOT NULL)) OR
            ((status IN ('demo_pending', 'unpaid') AND resolved_at IS NULL))
        )
    );

    CREATE UNIQUE NONCLUSTERED INDEX uq_booking_payments_succeeded ON dbo.booking_payments (booking_id) WHERE status = 'demo_succeeded';
    CREATE UNIQUE NONCLUSTERED INDEX uq_booking_payments_unpaid ON dbo.booking_payments (booking_id) WHERE status = 'unpaid';
END;
GO

-- ============================================================================
-- 14. community_posts
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'community_posts' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.community_posts (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_community_posts PRIMARY KEY CLUSTERED,
        author_id BIGINT NOT NULL,
        body NVARCHAR(2000) NOT NULL,
        visibility VARCHAR(10) NOT NULL,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_community_posts_author FOREIGN KEY (author_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT ck_community_posts_body CHECK (LEN(TRIM(body)) > 0),
        CONSTRAINT ck_community_posts_visibility CHECK (visibility IN ('visible', 'hidden', 'deleted'))
    );

    CREATE NONCLUSTERED INDEX ix_community_posts_visibility ON dbo.community_posts (visibility, created_at, id);
    CREATE NONCLUSTERED INDEX ix_community_posts_author ON dbo.community_posts (author_id, created_at, id);
END;
GO

-- ============================================================================
-- 15. community_comments
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'community_comments' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.community_comments (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_community_comments PRIMARY KEY CLUSTERED,
        post_id BIGINT NOT NULL,
        author_id BIGINT NOT NULL,
        body NVARCHAR(1000) NOT NULL,
        visibility VARCHAR(10) NOT NULL,
        created_at DATETIME2(3) NOT NULL,
        updated_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_community_comments_post FOREIGN KEY (post_id) REFERENCES dbo.community_posts (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_community_comments_author FOREIGN KEY (author_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT ck_community_comments_body CHECK (LEN(TRIM(body)) > 0),
        CONSTRAINT ck_community_comments_visibility CHECK (visibility IN ('visible', 'hidden', 'deleted'))
    );

    CREATE NONCLUSTERED INDEX ix_community_comments_post ON dbo.community_comments (post_id, visibility, created_at, id);
    CREATE NONCLUSTERED INDEX ix_community_comments_author ON dbo.community_comments (author_id);
END;
GO

-- ============================================================================
-- 16. community_post_likes
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'community_post_likes' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.community_post_likes (
        post_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL,
        created_at DATETIME2(3) NOT NULL,
        CONSTRAINT pk_community_post_likes PRIMARY KEY CLUSTERED (post_id, user_id),
        CONSTRAINT fk_community_post_likes_post FOREIGN KEY (post_id) REFERENCES dbo.community_posts (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_community_post_likes_user FOREIGN KEY (user_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION
    );

    CREATE NONCLUSTERED INDEX ix_community_post_likes_user ON dbo.community_post_likes (user_id, created_at);
END;
GO

-- ============================================================================
-- 17. admin_audit_logs
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'admin_audit_logs' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.admin_audit_logs (
        id BIGINT IDENTITY(1,1) CONSTRAINT pk_admin_audit_logs PRIMARY KEY CLUSTERED,
        actor_id BIGINT NOT NULL,
        action VARCHAR(40) NOT NULL,
        subject_user_id BIGINT NULL,
        subject_provider_id BIGINT NULL,
        subject_listing_id BIGINT NULL,
        subject_post_id BIGINT NULL,
        reason NVARCHAR(500) NOT NULL,
        occurred_at DATETIME2(3) NOT NULL,
        CONSTRAINT fk_admin_audit_logs_actor FOREIGN KEY (actor_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_admin_audit_logs_subject_user FOREIGN KEY (subject_user_id) REFERENCES dbo.users (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_admin_audit_logs_subject_provider FOREIGN KEY (subject_provider_id) REFERENCES dbo.provider_profiles (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_admin_audit_logs_subject_listing FOREIGN KEY (subject_listing_id) REFERENCES dbo.listings (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT fk_admin_audit_logs_subject_post FOREIGN KEY (subject_post_id) REFERENCES dbo.community_posts (id) ON DELETE NO ACTION ON UPDATE NO ACTION,
        CONSTRAINT ck_admin_audit_logs_action CHECK (action IN ('user_status', 'provider_decision', 'listing_decision', 'post_moderation')),
        CONSTRAINT ck_admin_audit_logs_single_subject CHECK (
            ((CASE WHEN subject_user_id IS NOT NULL THEN 1 ELSE 0 END +
              CASE WHEN subject_provider_id IS NOT NULL THEN 1 ELSE 0 END +
              CASE WHEN subject_listing_id IS NOT NULL THEN 1 ELSE 0 END +
              CASE WHEN subject_post_id IS NOT NULL THEN 1 ELSE 0 END) = 1)
        )
    );

    CREATE NONCLUSTERED INDEX ix_admin_audit_logs_actor ON dbo.admin_audit_logs (actor_id, occurred_at, id);
    CREATE NONCLUSTERED INDEX ix_admin_audit_logs_subject_user ON dbo.admin_audit_logs (subject_user_id) WHERE subject_user_id IS NOT NULL;
    CREATE NONCLUSTERED INDEX ix_admin_audit_logs_subject_provider ON dbo.admin_audit_logs (subject_provider_id) WHERE subject_provider_id IS NOT NULL;
    CREATE NONCLUSTERED INDEX ix_admin_audit_logs_subject_listing ON dbo.admin_audit_logs (subject_listing_id) WHERE subject_listing_id IS NOT NULL;
    CREATE NONCLUSTERED INDEX ix_admin_audit_logs_subject_post ON dbo.admin_audit_logs (subject_post_id) WHERE subject_post_id IS NOT NULL;
END;
GO

-- ============================================================================
-- 18. mvp_schema_versions
-- ============================================================================
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'mvp_schema_versions' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.mvp_schema_versions (
        version VARCHAR(20) NOT NULL CONSTRAINT pk_mvp_schema_versions PRIMARY KEY CLUSTERED,
        artifact_name NVARCHAR(255) NOT NULL,
        sha256 CHAR(64) NOT NULL,
        applied_at DATETIME2(3) NOT NULL,
        applied_by NVARCHAR(128) NOT NULL,
        CONSTRAINT uq_mvp_schema_versions_artifact UNIQUE NONCLUSTERED (artifact_name)
    );
END;
GO
