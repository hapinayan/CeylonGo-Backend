-- Migration: 025_extend_activities_eco_module.sql
-- Description: Unify Activities & Eco-Tourism Bookings & Participants
-- Module: Activity & Eco-Experience Unified Booking Infrastructure
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Extend dbo.activity_slots with optional eco_experience_id reference
-- =============================================================================
IF COL_LENGTH('dbo.activity_slots', 'eco_experience_id') IS NULL
BEGIN
    ALTER TABLE dbo.activity_slots
    ADD eco_experience_id INT NULL;
END;
GO

-- =============================================================================
-- Batch 2: Add FK & index for eco_experience_id on dbo.activity_slots
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_activity_slots_eco_experience'
      AND parent_object_id = OBJECT_ID('dbo.activity_slots')
)
BEGIN
    ALTER TABLE dbo.activity_slots
    ADD CONSTRAINT FK_activity_slots_eco_experience
        FOREIGN KEY (eco_experience_id)
        REFERENCES dbo.eco_experiences(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_activity_slots_eco_experience'
      AND object_id = OBJECT_ID('dbo.activity_slots')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_activity_slots_eco_experience
        ON dbo.activity_slots(eco_experience_id, starts_at)
        WHERE eco_experience_id IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 3: Extend dbo.activity_bookings with optional eco_experience_id reference
-- =============================================================================
IF COL_LENGTH('dbo.activity_bookings', 'eco_experience_id') IS NULL
BEGIN
    ALTER TABLE dbo.activity_bookings
    ADD eco_experience_id INT NULL;
END;
GO

-- =============================================================================
-- Batch 4: Add FK & index for eco_experience_id on dbo.activity_bookings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_activity_bookings_eco_experience'
      AND parent_object_id = OBJECT_ID('dbo.activity_bookings')
)
BEGIN
    ALTER TABLE dbo.activity_bookings
    ADD CONSTRAINT FK_activity_bookings_eco_experience
        FOREIGN KEY (eco_experience_id)
        REFERENCES dbo.eco_experiences(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_activity_bookings_eco_experience'
      AND object_id = OBJECT_ID('dbo.activity_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_activity_bookings_eco_experience
        ON dbo.activity_bookings(eco_experience_id)
        WHERE eco_experience_id IS NOT NULL;
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.activity_booking_participants table
-- =============================================================================
IF OBJECT_ID('dbo.activity_booking_participants', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.activity_booking_participants (
        id INT IDENTITY(1,1) NOT NULL,
        activity_booking_id INT NOT NULL,
        full_name NVARCHAR(150) NOT NULL,
        age INT NULL,
        emergency_contact_name NVARCHAR(150) NULL,
        emergency_contact_phone NVARCHAR(30) NULL,
        special_requirements NVARCHAR(1000) NULL,
        waiver_signed BIT NOT NULL CONSTRAINT DF_activity_participants_waiver DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_activity_participants_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_activity_booking_participants PRIMARY KEY (id),

        CONSTRAINT FK_activity_booking_participants_booking
            FOREIGN KEY (activity_booking_id)
            REFERENCES dbo.activity_bookings(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_activity_booking_participants_age
            CHECK (age IS NULL OR age >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_activity_booking_participants_booking
        ON dbo.activity_booking_participants(activity_booking_id);

    PRINT 'activity_booking_participants table created.';
END
ELSE
BEGIN
    PRINT 'activity_booking_participants table already exists.';
END;
GO
