-- Migration: 022_create_local_tour_guide_module.sql
-- Module: M02 Local Tour Guide Management
-- Target: Microsoft SQL Server (dbo)
-- Description: Centralized Local Tour Guide Foundation (9 tables)
-- Prerequisites: dbo.users, dbo.provider_profiles

-- =============================================================================
-- 1. TOUR GUIDES
-- =============================================================================
IF OBJECT_ID('dbo.tour_guides', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guides (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,

        -- Professional Details
        bio NVARCHAR(2000) NOT NULL,
        years_of_experience INT NOT NULL DEFAULT 0,
        specialties NVARCHAR(500) NULL,

        -- Licensing (SLTDA)
        license_number NVARCHAR(100) NULL,
        license_type NVARCHAR(50) NULL,
        license_expiry_date DATE NULL,

        -- Pricing & Currency
        hourly_rate DECIMAL(18,2) NULL,
        daily_rate DECIMAL(18,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL DEFAULT 'LKR',

        -- Base Location for Interactive Map
        base_city NVARCHAR(100) NOT NULL,
        base_district NVARCHAR(100) NOT NULL,
        base_latitude DECIMAL(9,6) NULL,
        base_longitude DECIMAL(9,6) NULL,

        -- Availability & Discovery Visibility
        is_available BIT NOT NULL DEFAULT 1,
        is_active BIT NOT NULL DEFAULT 0,

        -- Separate Listing Approval & Review Workflow
        approval_status NVARCHAR(20) NOT NULL DEFAULT 'draft',
        reviewed_by INT NULL,
        reviewed_at DATETIME2 NULL,
        rejection_reason NVARCHAR(1000) NULL,

        -- Guide Identity & Credential Verification State
        verification_status NVARCHAR(20) NOT NULL DEFAULT 'unverified',

        -- Ratings & Reviews
        rating_average DECIMAL(3,2) NOT NULL DEFAULT 0.00,
        review_count INT NOT NULL DEFAULT 0,

        -- Cancellation & Group Size Limits
        cancellation_policy NVARCHAR(1000) NULL,
        free_cancellation_hours INT NOT NULL DEFAULT 24,
        max_group_size INT NOT NULL DEFAULT 10,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guides PRIMARY KEY (id),

        CONSTRAINT UQ_tour_guides_provider_profile
            UNIQUE (provider_profile_id),

        CONSTRAINT FK_tour_guides_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_tour_guides_reviewer
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_tour_guides_experience
            CHECK (years_of_experience >= 0),

        CONSTRAINT CK_tour_guides_hourly_rate
            CHECK (hourly_rate IS NULL OR hourly_rate >= 0),

        CONSTRAINT CK_tour_guides_daily_rate
            CHECK (daily_rate >= 0),

        CONSTRAINT CK_tour_guides_coords
            CHECK (
                (base_latitude IS NULL AND base_longitude IS NULL)
                OR
                (base_latitude BETWEEN -90.0 AND 90.0 AND base_longitude BETWEEN -180.0 AND 180.0)
            ),

        CONSTRAINT CK_tour_guides_approval_status
            CHECK (approval_status IN (
                'draft',
                'pending',
                'approved',
                'rejected',
                'suspended'
            )),

        CONSTRAINT CK_tour_guides_verification_status
            CHECK (verification_status IN (
                'unverified',
                'pending',
                'verified',
                'rejected',
                'suspended'
            )),

        CONSTRAINT CK_tour_guides_license_type
            CHECK (license_type IS NULL OR license_type IN (
                'national',
                'chauffeur',
                'site',
                'area'
            )),

        CONSTRAINT CK_tour_guides_cancellation_hours
            CHECK (free_cancellation_hours >= 0),

        CONSTRAINT CK_tour_guides_group_size
            CHECK (max_group_size > 0),

        CONSTRAINT CK_tour_guides_rating
            CHECK (rating_average BETWEEN 0.00 AND 5.00),

        CONSTRAINT CK_tour_guides_review_count
            CHECK (review_count >= 0)
    );

    CREATE INDEX IX_tour_guides_provider
        ON dbo.tour_guides(provider_profile_id);

    CREATE INDEX IX_tour_guides_reviewer
        ON dbo.tour_guides(reviewed_by);

    CREATE INDEX IX_tour_guides_discovery
        ON dbo.tour_guides(approval_status, is_active, is_available, base_district);

    PRINT 'tour_guides table created.';
END
ELSE
BEGIN
    PRINT 'tour_guides table already exists.';
END;
GO

-- =============================================================================
-- 2. TOUR GUIDE LANGUAGES
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_languages', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_languages (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        language_code NVARCHAR(10) NOT NULL,
        language_name NVARCHAR(50) NOT NULL,
        proficiency_level NVARCHAR(20) NOT NULL DEFAULT 'fluent',
        is_primary BIT NOT NULL DEFAULT 0,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_languages PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_languages_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_tour_guide_languages_code
            UNIQUE (tour_guide_id, language_code),

        CONSTRAINT CK_tour_guide_languages_proficiency
            CHECK (proficiency_level IN (
                'basic',
                'conversational',
                'fluent',
                'native'
            ))
    );

    CREATE INDEX IX_tour_guide_languages_guide
        ON dbo.tour_guide_languages(tour_guide_id, is_primary);

    PRINT 'tour_guide_languages table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_languages table already exists.';
END;
GO

-- =============================================================================
-- 3. TOUR GUIDE SERVICE AREAS
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_service_areas', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_service_areas (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        province NVARCHAR(100) NOT NULL,
        district NVARCHAR(100) NOT NULL,
        city NVARCHAR(100) NOT NULL DEFAULT N'',
        is_primary BIT NOT NULL DEFAULT 0,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_service_areas PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_service_areas_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_tour_guide_service_areas
            UNIQUE (tour_guide_id, district, city)
    );

    CREATE INDEX IX_tour_guide_service_areas_guide
        ON dbo.tour_guide_service_areas(tour_guide_id);

    CREATE INDEX IX_tour_guide_service_areas_district
        ON dbo.tour_guide_service_areas(district, city);

    PRINT 'tour_guide_service_areas table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_service_areas table already exists.';
END;
GO

-- =============================================================================
-- 4. TOUR GUIDE CREDENTIALS
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_credentials', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_credentials (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        credential_type NVARCHAR(50) NOT NULL,
        title NVARCHAR(150) NOT NULL,
        issuing_authority NVARCHAR(150) NOT NULL,
        certificate_number NVARCHAR(100) NULL,
        issue_date DATE NULL,
        expiry_date DATE NULL,

        -- Protected internal storage identifier (never exposed in public API schemas)
        file_reference NVARCHAR(1000) NOT NULL,
        is_confidential BIT NOT NULL DEFAULT 1,

        verification_status NVARCHAR(20) NOT NULL DEFAULT 'pending',
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_credentials PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_credentials_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        -- Composite unique constraint to support guide-scoped foreign key references
        CONSTRAINT UQ_tour_guide_credentials_guide
            UNIQUE (id, tour_guide_id),

        CONSTRAINT CK_tour_guide_credentials_type
            CHECK (credential_type IN (
                'sltda_license',
                'first_aid',
                'degree',
                'wildlife_permit',
                'driving_license',
                'other'
            )),

        CONSTRAINT CK_tour_guide_credentials_status
            CHECK (verification_status IN (
                'pending',
                'approved',
                'rejected'
            )),

        CONSTRAINT CK_tour_guide_credentials_dates
            CHECK (expiry_date IS NULL OR issue_date IS NULL OR expiry_date >= issue_date)
    );

    CREATE INDEX IX_tour_guide_credentials_guide
        ON dbo.tour_guide_credentials(tour_guide_id);

    PRINT 'tour_guide_credentials table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_credentials table already exists.';
END;
GO

-- =============================================================================
-- 5. TOUR GUIDE IMAGES
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_images (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        caption NVARCHAR(255) NULL,
        display_order INT NOT NULL DEFAULT 0,
        is_profile BIT NOT NULL DEFAULT 0,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_images PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_images_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_tour_guide_images_order
            CHECK (display_order >= 0)
    );

    CREATE INDEX IX_tour_guide_images_guide
        ON dbo.tour_guide_images(tour_guide_id, display_order);

    -- Unique profile/primary image per guide via filtered unique index
    CREATE UNIQUE NONCLUSTERED INDEX UQ_tour_guide_images_profile
        ON dbo.tour_guide_images(tour_guide_id)
        WHERE is_profile = 1;

    PRINT 'tour_guide_images table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_images table already exists.';
END;
GO

-- =============================================================================
-- 6. TOUR GUIDE PACKAGES
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_packages', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_packages (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,

        title NVARCHAR(150) NOT NULL,
        description NVARCHAR(4000) NOT NULL,
        itinerary NVARCHAR(MAX) NULL,
        duration_hours DECIMAL(5,2) NOT NULL,

        pricing_type NVARCHAR(20) NOT NULL DEFAULT 'per_person',
        price DECIMAL(18,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL DEFAULT 'LKR',

        max_people INT NOT NULL DEFAULT 10,
        included_services NVARCHAR(2000) NULL,
        excluded_services NVARCHAR(2000) NULL,

        meeting_point NVARCHAR(500) NOT NULL,
        meeting_latitude DECIMAL(9,6) NULL,
        meeting_longitude DECIMAL(9,6) NULL,

        is_active BIT NOT NULL DEFAULT 1,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_packages PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_packages_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        -- Composite unique constraint to support guide-scoped foreign key references
        CONSTRAINT UQ_tour_guide_packages_guide
            UNIQUE (id, tour_guide_id),

        CONSTRAINT CK_tour_guide_packages_duration
            CHECK (duration_hours > 0),

        CONSTRAINT CK_tour_guide_packages_price
            CHECK (price >= 0),

        CONSTRAINT CK_tour_guide_packages_pricing_type
            CHECK (pricing_type IN ('per_person', 'per_group')),

        CONSTRAINT CK_tour_guide_packages_max_people
            CHECK (max_people > 0),

        CONSTRAINT CK_tour_guide_packages_coords
            CHECK (
                (meeting_latitude IS NULL AND meeting_longitude IS NULL)
                OR
                (meeting_latitude BETWEEN -90.0 AND 90.0 AND meeting_longitude BETWEEN -180.0 AND 180.0)
            )
    );

    CREATE INDEX IX_tour_guide_packages_guide
        ON dbo.tour_guide_packages(tour_guide_id, is_active);

    PRINT 'tour_guide_packages table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_packages table already exists.';
END;
GO

-- =============================================================================
-- 7. TOUR GUIDE AVAILABILITY
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_availability', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_availability (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        available_date DATE NOT NULL,
        start_time TIME NULL,
        end_time TIME NULL,
        is_available BIT NOT NULL DEFAULT 1,
        reason NVARCHAR(255) NULL,
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_availability PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_availability_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE CASCADE,

        CONSTRAINT UQ_tour_guide_availability_slot
            UNIQUE (tour_guide_id, available_date, start_time, end_time),

        -- Explicitly requires either both times NULL (full day) or both times NOT NULL with end_time > start_time
        CONSTRAINT CK_tour_guide_availability_times
            CHECK (
                (start_time IS NULL AND end_time IS NULL)
                OR
                (start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)
            )
    );

    CREATE INDEX IX_tour_guide_availability_guide
        ON dbo.tour_guide_availability(tour_guide_id, available_date);

    PRINT 'tour_guide_availability table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_availability table already exists.';
END;
GO

-- =============================================================================
-- 8. TOUR GUIDE BOOKINGS
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_bookings', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_bookings (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        traveler_id INT NOT NULL,
        package_id INT NULL,

        -- Unambiguous start and end timestamps (supports same-day & multi-day)
        booking_date DATE NOT NULL,
        start_datetime DATETIME2 NOT NULL,
        end_datetime DATETIME2 NOT NULL,

        number_of_people INT NOT NULL,
        pickup_location NVARCHAR(500) NULL,
        special_requests NVARCHAR(1000) NULL,

        total_price DECIMAL(18,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL DEFAULT 'LKR',

        booking_status NVARCHAR(30) NOT NULL DEFAULT 'pending',
        payment_method NVARCHAR(20) NOT NULL DEFAULT 'pay_at_location',
        payment_status NVARCHAR(20) NOT NULL DEFAULT 'unpaid',

        -- Expiration for pending bookings
        expires_at DATETIME2 NULL,

        -- Immutable policy snapshots at booking time
        cancellation_policy_snapshot NVARCHAR(1000) NULL,
        free_cancellation_hours_snapshot INT NOT NULL DEFAULT 24,

        -- Cancellation details
        cancellation_reason NVARCHAR(1000) NULL,
        cancelled_by INT NULL,
        cancelled_at DATETIME2 NULL,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_bookings PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_bookings_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_tour_guide_bookings_traveler
            FOREIGN KEY (traveler_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_tour_guide_bookings_cancelled_by
            FOREIGN KEY (cancelled_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        -- Ensures bookings cannot reference a package belonging to another guide
        CONSTRAINT FK_tour_guide_bookings_package_guide
            FOREIGN KEY (package_id, tour_guide_id)
            REFERENCES dbo.tour_guide_packages(id, tour_guide_id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_tour_guide_bookings_datetimes
            CHECK (end_datetime > start_datetime),

        CONSTRAINT CK_tour_guide_bookings_people
            CHECK (number_of_people > 0),

        CONSTRAINT CK_tour_guide_bookings_price
            CHECK (total_price >= 0),

        CONSTRAINT CK_tour_guide_bookings_cancellation_hours_snapshot
            CHECK (free_cancellation_hours_snapshot >= 0),

        CONSTRAINT CK_tour_guide_bookings_status
            CHECK (booking_status IN (
                'pending',
                'confirmed',
                'in_progress',
                'completed',
                'cancelled',
                'rejected',
                'expired'
            )),

        CONSTRAINT CK_tour_guide_bookings_payment_method
            CHECK (payment_method IN (
                'online',
                'pay_at_location'
            )),

        CONSTRAINT CK_tour_guide_bookings_payment_status
            CHECK (payment_status IN (
                'unpaid',
                'pending',
                'paid',
                'refund_pending',
                'refunded',
                'failed'
            ))
    );

    CREATE INDEX IX_tour_guide_bookings_guide
        ON dbo.tour_guide_bookings(tour_guide_id, start_datetime);

    CREATE INDEX IX_tour_guide_bookings_traveler
        ON dbo.tour_guide_bookings(traveler_id, start_datetime);

    CREATE INDEX IX_tour_guide_bookings_package
        ON dbo.tour_guide_bookings(package_id);

    CREATE INDEX IX_tour_guide_bookings_status
        ON dbo.tour_guide_bookings(booking_status);

    CREATE INDEX IX_tour_guide_bookings_expires
        ON dbo.tour_guide_bookings(expires_at)
        WHERE booking_status = 'pending';

    PRINT 'tour_guide_bookings table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_bookings table already exists.';
END;
GO

-- =============================================================================
-- 9. TOUR GUIDE VERIFICATIONS
-- =============================================================================
IF OBJECT_ID('dbo.tour_guide_verifications', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.tour_guide_verifications (
        id INT IDENTITY(1,1) NOT NULL,
        tour_guide_id INT NOT NULL,
        credential_id INT NOT NULL,
        reviewed_by INT NOT NULL,

        verification_type NVARCHAR(50) NOT NULL,
        status NVARCHAR(20) NOT NULL,
        notes NVARCHAR(2000) NULL,
        rejection_reason NVARCHAR(1000) NULL,

        verified_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_tour_guide_verifications PRIMARY KEY (id),

        CONSTRAINT FK_tour_guide_verifications_guide
            FOREIGN KEY (tour_guide_id)
            REFERENCES dbo.tour_guides(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_tour_guide_verifications_reviewer
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        -- Ensures verification records cannot reference credentials belonging to another guide
        CONSTRAINT FK_tour_guide_verifications_credential_guide
            FOREIGN KEY (credential_id, tour_guide_id)
            REFERENCES dbo.tour_guide_credentials(id, tour_guide_id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_tour_guide_verifications_status
            CHECK (status IN (
                'approved',
                'rejected',
                'requires_info'
            ))
    );

    CREATE INDEX IX_tour_guide_verifications_guide
        ON dbo.tour_guide_verifications(tour_guide_id);

    CREATE INDEX IX_tour_guide_verifications_credential
        ON dbo.tour_guide_verifications(credential_id);

    CREATE INDEX IX_tour_guide_verifications_reviewer
        ON dbo.tour_guide_verifications(reviewed_by);

    PRINT 'tour_guide_verifications table created.';
END
ELSE
BEGIN
    PRINT 'tour_guide_verifications table already exists.';
END;
GO
