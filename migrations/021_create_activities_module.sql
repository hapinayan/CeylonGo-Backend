
-- Migration: 008_create_activities_module.sql
-- Module: M03 Activities & Experiences
-- Target: Microsoft SQL Server / dbo
-- Creates core P0 tables only.
-- Review before execution. Do not run against production without backup.

-- 1. ACTIVITIES
IF OBJECT_ID('dbo.activities', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.activities (
        id INT IDENTITY(1,1) NOT NULL,
        provider_profile_id INT NOT NULL,

        title NVARCHAR(150) NOT NULL,
        description NVARCHAR(4000) NOT NULL,
        category NVARCHAR(30) NOT NULL,

        district NVARCHAR(100) NOT NULL,
        city NVARCHAR(100) NOT NULL,
        meeting_point NVARCHAR(500) NOT NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,

        duration_minutes INT NOT NULL,
        itinerary NVARCHAR(MAX) NULL,
        requirements NVARCHAR(2000) NULL,
        safety_rules NVARCHAR(2000) NULL,
        required_equipment NVARCHAR(1000) NULL,

        minimum_age INT NOT NULL DEFAULT 0,

        pricing_type NVARCHAR(20) NOT NULL,
        price DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL DEFAULT 'LKR',

        approval_status NVARCHAR(20) NOT NULL DEFAULT 'draft',
        is_active BIT NOT NULL DEFAULT 0,

        reviewed_by INT NULL,
        reviewed_at DATETIME2 NULL,
        rejection_reason NVARCHAR(500) NULL,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_activities PRIMARY KEY (id),

        CONSTRAINT FK_activities_provider
            FOREIGN KEY (provider_profile_id)
            REFERENCES dbo.provider_profiles(id),

        CONSTRAINT FK_activities_reviewer
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users(id),

        CONSTRAINT CK_activities_category
            CHECK (category IN (
                'cultural',
                'adventure',
                'local',
                'community'
            )),

        CONSTRAINT CK_activities_pricing
            CHECK (pricing_type IN ('per_person', 'per_group')),

        CONSTRAINT CK_activities_price
            CHECK (price >= 0),

        CONSTRAINT CK_activities_duration
            CHECK (duration_minutes > 0),

        CONSTRAINT CK_activities_age
            CHECK (minimum_age BETWEEN 0 AND 120),

        CONSTRAINT CK_activities_status
            CHECK (approval_status IN (
                'draft',
                'pending',
                'approved',
                'rejected',
                'suspended'
            )),

        CONSTRAINT CK_activities_latitude
            CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),

        CONSTRAINT CK_activities_longitude
            CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
    );

    CREATE INDEX IX_activities_provider
        ON dbo.activities(provider_profile_id);

    CREATE INDEX IX_activities_discovery
        ON dbo.activities(approval_status, is_active, category, district);

    PRINT 'activities table created.';
END
ELSE
    PRINT 'activities table already exists.';
GO

-- 2. ACTIVITY IMAGES
IF OBJECT_ID('dbo.activity_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.activity_images (
        id INT IDENTITY(1,1) NOT NULL,
        activity_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        alt_text NVARCHAR(255) NULL,
        display_order INT NOT NULL DEFAULT 0,
        is_cover BIT NOT NULL DEFAULT 0,

        CONSTRAINT PK_activity_images PRIMARY KEY (id),

        CONSTRAINT FK_activity_images_activity
            FOREIGN KEY (activity_id)
            REFERENCES dbo.activities(id),

        CONSTRAINT CK_activity_images_order
            CHECK (display_order >= 0)
    );

    CREATE INDEX IX_activity_images_activity
        ON dbo.activity_images(activity_id);

    PRINT 'activity_images table created.';
END
ELSE
    PRINT 'activity_images table already exists.';
GO

-- 3. ACTIVITY SLOTS
IF OBJECT_ID('dbo.activity_slots', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.activity_slots (
        id INT IDENTITY(1,1) NOT NULL,
        activity_id INT NOT NULL,

        starts_at DATETIME2 NOT NULL,
        ends_at DATETIME2 NOT NULL,

        capacity INT NOT NULL,
        reserved_count INT NOT NULL DEFAULT 0,

        is_active BIT NOT NULL DEFAULT 1,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_activity_slots PRIMARY KEY (id),

        CONSTRAINT FK_activity_slots_activity
            FOREIGN KEY (activity_id)
            REFERENCES dbo.activities(id),

        CONSTRAINT CK_activity_slots_time
            CHECK (ends_at > starts_at),

        CONSTRAINT CK_activity_slots_capacity
            CHECK (capacity > 0),

        CONSTRAINT CK_activity_slots_reserved
            CHECK (reserved_count >= 0 AND reserved_count <= capacity)
    );

    CREATE INDEX IX_activity_slots_activity_time
        ON dbo.activity_slots(activity_id, starts_at);

    PRINT 'activity_slots table created.';
END
ELSE
    PRINT 'activity_slots table already exists.';
GO

-- 4. ACTIVITY BOOKINGS
IF OBJECT_ID('dbo.activity_bookings', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.activity_bookings (
        id INT IDENTITY(1,1) NOT NULL,
        activity_slot_id INT NOT NULL,
        user_id INT NOT NULL,

        participants INT NOT NULL,

        total_price DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL DEFAULT 'LKR',

        payment_method NVARCHAR(20) NOT NULL,
        payment_status NVARCHAR(20) NOT NULL DEFAULT 'unpaid',
        booking_status NVARCHAR(30) NOT NULL DEFAULT 'pending',

        cancellation_reason NVARCHAR(1000) NULL,
        cancelled_at DATETIME2 NULL,

        created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_activity_bookings PRIMARY KEY (id),

        CONSTRAINT FK_activity_bookings_slot
            FOREIGN KEY (activity_slot_id)
            REFERENCES dbo.activity_slots(id),

        CONSTRAINT FK_activity_bookings_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id),

        CONSTRAINT CK_activity_bookings_participants
            CHECK (participants > 0),

        CONSTRAINT CK_activity_bookings_price
            CHECK (total_price >= 0),

        CONSTRAINT CK_activity_bookings_method
            CHECK (payment_method IN ('online', 'pay_at_location')),

        CONSTRAINT CK_activity_bookings_payment
            CHECK (payment_status IN (
                'unpaid',
                'pending',
                'paid',
                'refund_pending',
                'refunded',
                'failed'
            )),

        CONSTRAINT CK_activity_bookings_status
            CHECK (booking_status IN (
                'pending',
                'confirmed',
                'rejected',
                'cancelled',
                'completed'
            ))
    );

    CREATE INDEX IX_activity_bookings_slot
        ON dbo.activity_bookings(activity_slot_id);

    CREATE INDEX IX_activity_bookings_user
        ON dbo.activity_bookings(user_id);

    CREATE INDEX IX_activity_bookings_status
        ON dbo.activity_bookings(booking_status);

    PRINT 'activity_bookings table created.';
END
ELSE
    PRINT 'activity_bookings table already exists.';
GO
