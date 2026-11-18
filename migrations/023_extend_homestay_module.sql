-- Migration: 023_extend_homestay_module.sql
-- Description: Phase 02 - Homestay Database Enhancements
-- Module: M01 Homestay Lifecycle, Location & Availability Blocks
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Extend dbo.homestays with location, approval, policy, and timestamps
-- =============================================================================
IF COL_LENGTH('dbo.homestays', 'province') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD province NVARCHAR(100) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'district') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD district NVARCHAR(100) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'city') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD city NVARCHAR(100) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'address') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD address NVARCHAR(500) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'latitude') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD latitude DECIMAL(9, 6) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'longitude') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD longitude DECIMAL(9, 6) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'check_in_time') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD check_in_time TIME NOT NULL
        CONSTRAINT DF_homestays_check_in_time DEFAULT '14:00:00';
END;

IF COL_LENGTH('dbo.homestays', 'check_out_time') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD check_out_time TIME NOT NULL
        CONSTRAINT DF_homestays_check_out_time DEFAULT '11:00:00';
END;

IF COL_LENGTH('dbo.homestays', 'approval_status') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD approval_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_homestays_approval_status DEFAULT 'approved';
END;

IF COL_LENGTH('dbo.homestays', 'verification_status') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD verification_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_homestays_verification_status DEFAULT 'unverified';
END;

IF COL_LENGTH('dbo.homestays', 'reviewed_by') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD reviewed_by INT NULL;
END;

IF COL_LENGTH('dbo.homestays', 'reviewed_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD reviewed_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestays', 'rejection_reason') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD rejection_reason NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'cancellation_policy') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD cancellation_policy NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.homestays', 'free_cancellation_hours') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD free_cancellation_hours INT NOT NULL
        CONSTRAINT DF_homestays_free_cancellation_hours DEFAULT 24;
END;

IF COL_LENGTH('dbo.homestays', 'updated_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestays
    ADD updated_at DATETIME2 NOT NULL
        CONSTRAINT DF_homestays_updated_at DEFAULT SYSUTCDATETIME();
END;
GO

-- =============================================================================
-- Batch 2: Add constraints and indexes to dbo.homestays
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_homestays_reviewer'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT FK_homestays_reviewer
        FOREIGN KEY (reviewed_by)
        REFERENCES dbo.users(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestays_coords'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT CK_homestays_coords
        CHECK (
            (latitude IS NULL AND longitude IS NULL)
            OR
            (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0)
        );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestays_approval_status'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT CK_homestays_approval_status
        CHECK (approval_status IN (
            'draft',
            'pending',
            'approved',
            'rejected',
            'suspended'
        ));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestays_verification_status'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT CK_homestays_verification_status
        CHECK (verification_status IN (
            'unverified',
            'pending',
            'verified',
            'rejected',
            'suspended'
        ));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_homestays_cancellation_hours'
      AND parent_object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    ALTER TABLE dbo.homestays
    ADD CONSTRAINT CK_homestays_cancellation_hours
        CHECK (free_cancellation_hours >= 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestays_reviewer'
      AND object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestays_reviewer
        ON dbo.homestays(reviewed_by)
        WHERE reviewed_by IS NOT NULL;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestays_district'
      AND object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestays_district
        ON dbo.homestays(district, city);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestays_discovery'
      AND object_id = OBJECT_ID('dbo.homestays')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestays_discovery
        ON dbo.homestays(approval_status, is_active, district);
END;
GO

-- =============================================================================
-- Batch 3: Extend dbo.homestay_bookings with financial, lifecycle, and snapshot fields
-- =============================================================================
IF COL_LENGTH('dbo.homestay_bookings', 'currency') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD currency NVARCHAR(3) NOT NULL
        CONSTRAINT DF_homestay_bookings_currency DEFAULT 'LKR';
END;

IF COL_LENGTH('dbo.homestay_bookings', 'payment_method') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD payment_method NVARCHAR(30) NOT NULL
        CONSTRAINT DF_homestay_bookings_payment_method DEFAULT 'pay_at_location';
END;

IF COL_LENGTH('dbo.homestay_bookings', 'payment_status') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD payment_status NVARCHAR(30) NOT NULL
        CONSTRAINT DF_homestay_bookings_payment_status DEFAULT 'unpaid';
END;

IF COL_LENGTH('dbo.homestay_bookings', 'expires_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD expires_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'cancellation_reason') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD cancellation_reason NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'cancelled_by') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD cancelled_by INT NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'cancelled_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD cancelled_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'nightly_rate_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD nightly_rate_snapshot DECIMAL(12, 2) NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'cancellation_policy_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD cancellation_policy_snapshot NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'free_cancellation_hours_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD free_cancellation_hours_snapshot INT NOT NULL
        CONSTRAINT DF_homestay_bookings_cancellation_hours_snapshot DEFAULT 24;
END;

IF COL_LENGTH('dbo.homestay_bookings', 'updated_at') IS NULL
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD updated_at DATETIME2 NOT NULL
        CONSTRAINT DF_homestay_bookings_updated_at DEFAULT SYSUTCDATETIME();
END;
GO

-- =============================================================================
-- Batch 4: Update booking constraints, foreign keys, and indexes
-- =============================================================================
-- Safely replace CK_bookings_status constraint to support full lifecycle
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_bookings_status'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    DROP CONSTRAINT CK_bookings_status;
END;

ALTER TABLE dbo.homestay_bookings
ADD CONSTRAINT CK_bookings_status
    CHECK (status IN (
        'pending',
        'confirmed',
        'in_progress',
        'completed',
        'cancelled',
        'rejected',
        'expired'
    ));

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_bookings_payment_method'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD CONSTRAINT CK_bookings_payment_method
        CHECK (payment_method IN ('online', 'pay_at_location'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_bookings_payment_status'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD CONSTRAINT CK_bookings_payment_status
        CHECK (payment_status IN ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_bookings_cancellation_hours_snapshot'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD CONSTRAINT CK_bookings_cancellation_hours_snapshot
        CHECK (free_cancellation_hours_snapshot >= 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_homestay_bookings_cancelled_by'
      AND parent_object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    ALTER TABLE dbo.homestay_bookings
    ADD CONSTRAINT FK_homestay_bookings_cancelled_by
        FOREIGN KEY (cancelled_by)
        REFERENCES dbo.users(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestay_bookings_status'
      AND object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestay_bookings_status
        ON dbo.homestay_bookings(status);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestay_bookings_dates'
      AND object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestay_bookings_dates
        ON dbo.homestay_bookings(homestay_id, check_in, check_out);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_homestay_bookings_expires'
      AND object_id = OBJECT_ID('dbo.homestay_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_homestay_bookings_expires
        ON dbo.homestay_bookings(expires_at)
        WHERE status = 'pending';
END;
GO

-- =============================================================================
-- Batch 5: Create dbo.homestay_availability_blocks table
-- =============================================================================
IF OBJECT_ID('dbo.homestay_availability_blocks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestay_availability_blocks (
        id INT IDENTITY(1,1) NOT NULL,
        homestay_id INT NOT NULL,
        start_date DATE NOT NULL,
        end_date DATE NOT NULL,
        block_type NVARCHAR(50) NOT NULL,
        reason NVARCHAR(500) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_homestay_availability_blocks_is_active DEFAULT 1,
        created_by INT NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_homestay_availability_blocks_created_at DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_homestay_availability_blocks_updated_at DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_homestay_availability_blocks PRIMARY KEY (id),

        CONSTRAINT FK_homestay_availability_blocks_homestay
            FOREIGN KEY (homestay_id)
            REFERENCES dbo.homestays(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_homestay_availability_blocks_creator
            FOREIGN KEY (created_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_homestay_availability_blocks_dates
            CHECK (end_date > start_date),

        CONSTRAINT CK_homestay_availability_blocks_type
            CHECK (block_type IN (
                'maintenance',
                'owner_unavailable',
                'admin_block',
                'renovation',
                'seasonal_closure',
                'other'
            ))
    );

    CREATE NONCLUSTERED INDEX IX_homestay_availability_blocks_homestay
        ON dbo.homestay_availability_blocks(homestay_id, start_date, end_date);

    CREATE NONCLUSTERED INDEX IX_homestay_availability_blocks_active
        ON dbo.homestay_availability_blocks(is_active);

    PRINT 'homestay_availability_blocks table created.';
END
ELSE
BEGIN
    PRINT 'homestay_availability_blocks table already exists.';
END;
GO
