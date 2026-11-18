-- Migration: 027_create_shared_infrastructure.sql
-- Description: Centralized Shared Infrastructure Database Tables
-- Module: Payments, Refunds, Settlements, Reviews, Wishlists, Notifications, Audit & Promotions
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Create dbo.listing_references & dbo.booking_references
-- =============================================================================
IF OBJECT_ID('dbo.listing_references', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.listing_references (
        id INT IDENTITY(1,1) NOT NULL,
        module_type NVARCHAR(30) NOT NULL,
        target_id INT NOT NULL,
        provider_profile_id INT NOT NULL,
        title NVARCHAR(255) NOT NULL,
        district NVARCHAR(100) NOT NULL,
        city NVARCHAR(100) NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_listing_references_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_listing_references_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_listing_references PRIMARY KEY (id),

        CONSTRAINT FK_listing_references_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT UQ_listing_references
            UNIQUE (module_type, target_id),

        CONSTRAINT CK_listing_references_type
            CHECK (module_type IN ('homestay', 'tour_guide', 'activity', 'eco_experience', 'artisan', 'rental_vehicle'))
    );

    CREATE NONCLUSTERED INDEX IX_listing_references_provider
        ON dbo.listing_references(provider_profile_id);

    CREATE NONCLUSTERED INDEX IX_listing_references_location
        ON dbo.listing_references(district, city);

    PRINT 'listing_references table created.';
END;

IF OBJECT_ID('dbo.booking_references', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.booking_references (
        id INT IDENTITY(1,1) NOT NULL,
        booking_reference NVARCHAR(50) NOT NULL,
        module_type NVARCHAR(30) NOT NULL,
        target_booking_id INT NOT NULL,
        user_id INT NOT NULL,
        provider_profile_id INT NOT NULL,
        total_price DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_booking_references_currency DEFAULT 'LKR',
        booking_status NVARCHAR(30) NOT NULL,
        payment_status NVARCHAR(30) NOT NULL CONSTRAINT DF_booking_references_payment_status DEFAULT 'unpaid',
        created_at DATETIME2 NOT NULL CONSTRAINT DF_booking_references_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_booking_references PRIMARY KEY (id),

        CONSTRAINT UQ_booking_references_code
            UNIQUE (booking_reference),

        CONSTRAINT UQ_booking_references_target
            UNIQUE (module_type, target_booking_id),

        CONSTRAINT FK_booking_references_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_booking_references_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_booking_references_type
            CHECK (module_type IN ('homestay', 'tour_guide', 'activity', 'rental_vehicle', 'artisan_reservation')),

        CONSTRAINT CK_booking_references_price
            CHECK (total_price >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_booking_references_user
        ON dbo.booking_references(user_id);

    CREATE NONCLUSTERED INDEX IX_booking_references_provider
        ON dbo.booking_references(provider_profile_id);

    PRINT 'booking_references table created.';
END;
GO

-- =============================================================================
-- Batch 2: Create dbo.payment_transactions & dbo.payment_refunds
-- =============================================================================
IF OBJECT_ID('dbo.payment_transactions', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.payment_transactions (
        id INT IDENTITY(1,1) NOT NULL,
        booking_reference_id INT NOT NULL,
        user_id INT NOT NULL,
        gateway_name NVARCHAR(50) NOT NULL CONSTRAINT DF_payment_transactions_gateway DEFAULT 'payhere',
        gateway_transaction_id NVARCHAR(100) NULL,
        amount DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_payment_transactions_currency DEFAULT 'LKR',
        payment_status NVARCHAR(30) NOT NULL CONSTRAINT DF_payment_transactions_status DEFAULT 'pending',
        payment_method NVARCHAR(50) NOT NULL CONSTRAINT DF_payment_transactions_method DEFAULT 'visa_mastercard',
        gateway_response_payload NVARCHAR(MAX) NULL,
        paid_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_payment_transactions_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_payment_transactions PRIMARY KEY (id),

        CONSTRAINT FK_payment_transactions_booking
            FOREIGN KEY (booking_reference_id)
            REFERENCES dbo.booking_references(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_payment_transactions_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_payment_transactions_amount
            CHECK (amount >= 0),

        CONSTRAINT CK_payment_transactions_status
            CHECK (payment_status IN ('pending', 'completed', 'failed', 'refunded', 'cancelled'))
    );

    CREATE NONCLUSTERED INDEX IX_payment_transactions_booking
        ON dbo.payment_transactions(booking_reference_id);

    CREATE NONCLUSTERED INDEX IX_payment_transactions_user
        ON dbo.payment_transactions(user_id);

    PRINT 'payment_transactions table created.';
END;

IF OBJECT_ID('dbo.payment_refunds', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.payment_refunds (
        id INT IDENTITY(1,1) NOT NULL,
        payment_transaction_id INT NOT NULL,
        amount DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_payment_refunds_currency DEFAULT 'LKR',
        refund_reason NVARCHAR(1000) NOT NULL,
        refund_status NVARCHAR(30) NOT NULL CONSTRAINT DF_payment_refunds_status DEFAULT 'pending',
        processed_by INT NULL,
        processed_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_payment_refunds_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_payment_refunds PRIMARY KEY (id),

        CONSTRAINT FK_payment_refunds_transaction
            FOREIGN KEY (payment_transaction_id)
            REFERENCES dbo.payment_transactions(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_payment_refunds_processor
            FOREIGN KEY (processed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_payment_refunds_amount
            CHECK (amount >= 0),

        CONSTRAINT CK_payment_refunds_status
            CHECK (refund_status IN ('pending', 'processed', 'failed', 'rejected'))
    );

    CREATE NONCLUSTERED INDEX IX_payment_refunds_transaction
        ON dbo.payment_refunds(payment_transaction_id);

    PRINT 'payment_refunds table created.';
END;
GO

-- =============================================================================
-- Batch 3: Create dbo.provider_settlements & dbo.notifications
-- =============================================================================
IF OBJECT_ID('dbo.provider_settlements', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.provider_settlements (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,
        period_start DATE NOT NULL,
        period_end DATE NOT NULL,
        gross_amount DECIMAL(12,2) NOT NULL,
        platform_fee DECIMAL(12,2) NOT NULL CONSTRAINT DF_provider_settlements_fee DEFAULT 0,
        net_payable DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_provider_settlements_currency DEFAULT 'LKR',
        settlement_status NVARCHAR(30) NOT NULL CONSTRAINT DF_provider_settlements_status DEFAULT 'pending',
        payout_reference NVARCHAR(100) NULL,
        paid_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_provider_settlements_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_provider_settlements PRIMARY KEY (id),

        CONSTRAINT FK_provider_settlements_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_provider_settlements_dates
            CHECK (period_end >= period_start),

        CONSTRAINT CK_provider_settlements_gross
            CHECK (gross_amount >= 0),

        CONSTRAINT CK_provider_settlements_net
            CHECK (net_payable >= 0),

        CONSTRAINT CK_provider_settlements_status
            CHECK (settlement_status IN ('pending', 'approved', 'paid', 'on_hold'))
    );

    CREATE NONCLUSTERED INDEX IX_provider_settlements_provider
        ON dbo.provider_settlements(provider_profile_id, period_start);

    PRINT 'provider_settlements table created.';
END;

IF OBJECT_ID('dbo.notifications', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.notifications (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        title NVARCHAR(150) NOT NULL,
        message NVARCHAR(1000) NOT NULL,
        notification_type NVARCHAR(50) NOT NULL,
        link_url NVARCHAR(500) NULL,
        is_read BIT NOT NULL CONSTRAINT DF_notifications_read DEFAULT 0,
        read_at DATETIME2 NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_notifications_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_notifications PRIMARY KEY (id),

        CONSTRAINT FK_notifications_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_notifications_user
        ON dbo.notifications(user_id, is_read, created_at);

    PRINT 'notifications table created.';
END;
GO

-- =============================================================================
-- Batch 4: Create dbo.reviews & dbo.wishlists
-- =============================================================================
IF OBJECT_ID('dbo.reviews', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.reviews (
        id INT IDENTITY(1,1) NOT NULL,
        listing_reference_id INT NOT NULL,
        booking_reference_id INT NULL,
        user_id INT NOT NULL,
        rating DECIMAL(3,2) NOT NULL,
        title NVARCHAR(150) NULL,
        comment NVARCHAR(2000) NULL,
        is_verified_booking BIT NOT NULL CONSTRAINT DF_reviews_verified DEFAULT 0,
        is_published BIT NOT NULL CONSTRAINT DF_reviews_published DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_reviews_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_reviews_updated DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_reviews PRIMARY KEY (id),

        CONSTRAINT FK_reviews_listing
            FOREIGN KEY (listing_reference_id)
            REFERENCES dbo.listing_references(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_reviews_booking
            FOREIGN KEY (booking_reference_id)
            REFERENCES dbo.booking_references(id)
            ON DELETE SET NULL,

        CONSTRAINT FK_reviews_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_reviews_rating
            CHECK (rating BETWEEN 1.00 AND 5.00)
    );

    CREATE NONCLUSTERED INDEX IX_reviews_listing
        ON dbo.reviews(listing_reference_id, is_published);

    CREATE NONCLUSTERED INDEX IX_reviews_user
        ON dbo.reviews(user_id);

    PRINT 'reviews table created.';
END;

IF OBJECT_ID('dbo.wishlists', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.wishlists (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        listing_reference_id INT NOT NULL,
        notes NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_wishlists_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_wishlists PRIMARY KEY (id),

        CONSTRAINT FK_wishlists_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_wishlists_listing
            FOREIGN KEY (listing_reference_id)
            REFERENCES dbo.listing_references(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_wishlists_user_listing
            UNIQUE (user_id, listing_reference_id)
    );

    CREATE NONCLUSTERED INDEX IX_wishlists_user
        ON dbo.wishlists(user_id);

    PRINT 'wishlists table created.';
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.promotion_packages, promotion_campaigns & promotion_events
-- =============================================================================
IF OBJECT_ID('dbo.promotion_packages', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.promotion_packages (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,
        title NVARCHAR(150) NOT NULL,
        description NVARCHAR(2000) NULL,
        discount_percent DECIMAL(5,2) NULL,
        discount_amount DECIMAL(12,2) NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_promotion_packages_currency DEFAULT 'LKR',
        valid_from DATE NOT NULL,
        valid_to DATE NOT NULL,
        is_active BIT NOT NULL CONSTRAINT DF_promotion_packages_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_promotion_packages_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_promotion_packages PRIMARY KEY (id),

        CONSTRAINT FK_promotion_packages_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_promotion_packages_percent
            CHECK (discount_percent IS NULL OR (discount_percent >= 0 AND discount_percent <= 100)),

        CONSTRAINT CK_promotion_packages_amount
            CHECK (discount_amount IS NULL OR discount_amount >= 0),

        CONSTRAINT CK_promotion_packages_dates
            CHECK (valid_to >= valid_from)
    );

    CREATE NONCLUSTERED INDEX IX_promotion_packages_provider
        ON dbo.promotion_packages(provider_profile_id);

    PRINT 'promotion_packages table created.';
END;

IF OBJECT_ID('dbo.promotion_campaigns', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.promotion_campaigns (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,
        campaign_name NVARCHAR(150) NOT NULL,
        target_module NVARCHAR(30) NOT NULL,
        budget DECIMAL(12,2) NOT NULL,
        spent_amount DECIMAL(12,2) NOT NULL CONSTRAINT DF_promotion_campaigns_spent DEFAULT 0,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_promotion_campaigns_currency DEFAULT 'LKR',
        status NVARCHAR(30) NOT NULL CONSTRAINT DF_promotion_campaigns_status DEFAULT 'draft',
        starts_at DATETIME2 NOT NULL,
        ends_at DATETIME2 NOT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_promotion_campaigns_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_promotion_campaigns PRIMARY KEY (id),

        CONSTRAINT FK_promotion_campaigns_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_promotion_campaigns_budget
            CHECK (budget >= 0),

        CONSTRAINT CK_promotion_campaigns_spent
            CHECK (spent_amount >= 0),

        CONSTRAINT CK_promotion_campaigns_status
            CHECK (status IN ('draft', 'pending_approval', 'active', 'paused', 'completed', 'cancelled')),

        CONSTRAINT CK_promotion_campaigns_dates
            CHECK (ends_at > starts_at)
    );

    CREATE NONCLUSTERED INDEX IX_promotion_campaigns_provider
        ON dbo.promotion_campaigns(provider_profile_id);

    PRINT 'promotion_campaigns table created.';
END;

IF OBJECT_ID('dbo.promotion_events', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.promotion_events (
        id INT IDENTITY(1,1) NOT NULL,
        campaign_id INT NOT NULL,
        event_type NVARCHAR(30) NOT NULL,
        user_id INT NULL,
        ip_address NVARCHAR(45) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_promotion_events_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_promotion_events PRIMARY KEY (id),

        CONSTRAINT FK_promotion_events_campaign
            FOREIGN KEY (campaign_id)
            REFERENCES dbo.promotion_campaigns(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_promotion_events_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_promotion_events_type
            CHECK (event_type IN ('impression', 'click', 'booking', 'inquiry'))
    );

    CREATE NONCLUSTERED INDEX IX_promotion_events_campaign
        ON dbo.promotion_events(campaign_id, event_type);

    PRINT 'promotion_events table created.';
END;
GO

-- =============================================================================
-- Batch 6: Create dbo.booking_status_history & dbo.admin_audit_logs
-- =============================================================================
IF OBJECT_ID('dbo.booking_status_history', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.booking_status_history (
        id INT IDENTITY(1,1) NOT NULL,
        booking_reference_id INT NOT NULL,
        from_status NVARCHAR(30) NULL,
        to_status NVARCHAR(30) NOT NULL,
        changed_by INT NULL,
        notes NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_booking_history_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_booking_status_history PRIMARY KEY (id),

        CONSTRAINT FK_booking_status_history_booking
            FOREIGN KEY (booking_reference_id)
            REFERENCES dbo.booking_references(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_booking_status_history_user
            FOREIGN KEY (changed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION
    );

    CREATE NONCLUSTERED INDEX IX_booking_status_history_booking
        ON dbo.booking_status_history(booking_reference_id);

    PRINT 'booking_status_history table created.';
END;

IF OBJECT_ID('dbo.admin_audit_logs', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.admin_audit_logs (
        id INT IDENTITY(1,1) NOT NULL,
        admin_user_id INT NOT NULL,
        action_type NVARCHAR(50) NOT NULL,
        entity_type NVARCHAR(50) NOT NULL,
        entity_id INT NOT NULL,
        details NVARCHAR(MAX) NULL,
        ip_address NVARCHAR(45) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_admin_audit_logs_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_admin_audit_logs PRIMARY KEY (id),

        CONSTRAINT FK_admin_audit_logs_user
            FOREIGN KEY (admin_user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION
    );

    CREATE NONCLUSTERED INDEX IX_admin_audit_logs_admin
        ON dbo.admin_audit_logs(admin_user_id);

    CREATE NONCLUSTERED INDEX IX_admin_audit_logs_entity
        ON dbo.admin_audit_logs(entity_type, entity_id);

    PRINT 'admin_audit_logs table created.';
END;
GO
