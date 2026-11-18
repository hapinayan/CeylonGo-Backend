-- Migration: 026_extend_rental_module.sql
-- Description: Centralized Vehicle Rental Module Schema Extension
-- Module: Vehicle Rentals, Locations, Rate Plans, Inspections, Incidents & Deposits
-- Target Database: Microsoft SQL Server (CeylonGoDB)
-- Safe & Idempotent: Can be executed multiple times safely without data loss.

-- =============================================================================
-- Batch 1: Create dbo.rental_locations table
-- =============================================================================
IF OBJECT_ID('dbo.rental_locations', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_locations (
        id INT IDENTITY(1,1) NOT NULL,
        provider_id INT NOT NULL,
        location_name NVARCHAR(150) NOT NULL,
        district NVARCHAR(100) NOT NULL,
        city NVARCHAR(100) NOT NULL,
        address NVARCHAR(500) NULL,
        latitude DECIMAL(9,6) NULL,
        longitude DECIMAL(9,6) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_rental_locations_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_locations_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_locations PRIMARY KEY (id),

        CONSTRAINT FK_rental_locations_provider
            FOREIGN KEY (provider_id)
            REFERENCES dbo.provider_profiles(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_rental_locations_coords
            CHECK (
                (latitude IS NULL AND longitude IS NULL)
                OR
                (latitude BETWEEN -90.0 AND 90.0 AND longitude BETWEEN -180.0 AND 180.0)
            )
    );

    CREATE NONCLUSTERED INDEX IX_rental_locations_district
        ON dbo.rental_locations(district, city);

    PRINT 'rental_locations table created.';
END
ELSE
BEGIN
    PRINT 'rental_locations table already exists.';
END;
GO

-- =============================================================================
-- Batch 2: Extend dbo.rental_vehicles with operational, inspection, and location fields
-- =============================================================================
IF COL_LENGTH('dbo.rental_vehicles', 'model_year') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD model_year INT NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'license_plate') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD license_plate NVARCHAR(50) NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'seating_capacity') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD seating_capacity INT NOT NULL
        CONSTRAINT DF_rental_vehicles_seating DEFAULT 4;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'transmission') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD transmission NVARCHAR(20) NOT NULL
        CONSTRAINT DF_rental_vehicles_transmission DEFAULT 'manual';
END;

IF COL_LENGTH('dbo.rental_vehicles', 'fuel_type') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD fuel_type NVARCHAR(20) NOT NULL
        CONSTRAINT DF_rental_vehicles_fuel DEFAULT 'petrol';
END;

IF COL_LENGTH('dbo.rental_vehicles', 'district') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD district NVARCHAR(100) NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'city') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD city NVARCHAR(100) NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'location_id') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD location_id INT NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'approval_status') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD approval_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_rental_vehicles_approval DEFAULT 'approved';
END;

IF COL_LENGTH('dbo.rental_vehicles', 'verification_status') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD verification_status NVARCHAR(20) NOT NULL
        CONSTRAINT DF_rental_vehicles_verification DEFAULT 'unverified';
END;

IF COL_LENGTH('dbo.rental_vehicles', 'reviewed_by') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD reviewed_by INT NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'reviewed_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD reviewed_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'rejection_reason') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD rejection_reason NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'cancellation_policy') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD cancellation_policy NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'free_cancellation_hours') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD free_cancellation_hours INT NOT NULL
        CONSTRAINT DF_rental_vehicles_cancellation_hours DEFAULT 24;
END;

IF COL_LENGTH('dbo.rental_vehicles', 'updated_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD updated_at DATETIME2 NOT NULL
        CONSTRAINT DF_rental_vehicles_updated DEFAULT SYSUTCDATETIME();
END;
GO

-- =============================================================================
-- Batch 3: Add constraints and indexes to dbo.rental_vehicles
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_rental_vehicles_location'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT FK_rental_vehicles_location
        FOREIGN KEY (location_id)
        REFERENCES dbo.rental_locations(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_rental_vehicles_reviewer'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT FK_rental_vehicles_reviewer
        FOREIGN KEY (reviewed_by)
        REFERENCES dbo.users(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_rental_vehicles_seating'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_seating
        CHECK (seating_capacity > 0);
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_rental_vehicles_transmission'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_transmission
        CHECK (transmission IN ('manual', 'automatic'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_rental_vehicles_fuel'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_fuel
        CHECK (fuel_type IN ('petrol', 'diesel', 'hybrid', 'electric'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_rental_vehicles_approval'
      AND parent_object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    ALTER TABLE dbo.rental_vehicles
    ADD CONSTRAINT CK_rental_vehicles_approval
        CHECK (approval_status IN ('draft', 'pending', 'approved', 'rejected', 'suspended'));
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_rental_vehicles_discovery'
      AND object_id = OBJECT_ID('dbo.rental_vehicles')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_rental_vehicles_discovery
        ON dbo.rental_vehicles(approval_status, status, district);
END;
GO

-- =============================================================================
-- Batch 4: Create rate plans, vehicle images, documents, and availability blocks
-- =============================================================================
IF OBJECT_ID('dbo.rental_rate_plans', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_rate_plans (
        id INT IDENTITY(1,1) NOT NULL,
        vehicle_id INT NOT NULL,
        plan_name NVARCHAR(100) NOT NULL,
        duration_type NVARCHAR(30) NOT NULL,
        rate DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_rental_rate_plans_currency DEFAULT 'LKR',
        included_km_per_day INT NULL,
        extra_km_charge DECIMAL(10,2) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_rental_rate_plans_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_rate_plans_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_rate_plans PRIMARY KEY (id),

        CONSTRAINT FK_rental_rate_plans_vehicle
            FOREIGN KEY (vehicle_id)
            REFERENCES dbo.rental_vehicles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_rate_plans_type
            CHECK (duration_type IN ('hourly', 'daily', 'weekly', 'monthly')),

        CONSTRAINT CK_rental_rate_plans_rate
            CHECK (rate >= 0),

        CONSTRAINT CK_rental_rate_plans_extra_km
            CHECK (extra_km_charge IS NULL OR extra_km_charge >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_rental_rate_plans_vehicle
        ON dbo.rental_rate_plans(vehicle_id);

    PRINT 'rental_rate_plans table created.';
END;

IF OBJECT_ID('dbo.rental_vehicle_images', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_vehicle_images (
        id INT IDENTITY(1,1) NOT NULL,
        vehicle_id INT NOT NULL,
        image_url NVARCHAR(1000) NOT NULL,
        caption NVARCHAR(255) NULL,
        display_order INT NOT NULL CONSTRAINT DF_rental_vehicle_images_order DEFAULT 0,
        is_primary BIT NOT NULL CONSTRAINT DF_rental_vehicle_images_primary DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_vehicle_images_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_vehicle_images PRIMARY KEY (id),

        CONSTRAINT FK_rental_vehicle_images_vehicle
            FOREIGN KEY (vehicle_id)
            REFERENCES dbo.rental_vehicles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_vehicle_images_order
            CHECK (display_order >= 0)
    );

    CREATE NONCLUSTERED INDEX IX_rental_vehicle_images_vehicle
        ON dbo.rental_vehicle_images(vehicle_id);

    PRINT 'rental_vehicle_images table created.';
END;

IF OBJECT_ID('dbo.rental_vehicle_documents', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_vehicle_documents (
        id INT IDENTITY(1,1) NOT NULL,
        vehicle_id INT NOT NULL,
        document_type NVARCHAR(50) NOT NULL,
        document_number NVARCHAR(100) NULL,
        file_reference NVARCHAR(1000) NOT NULL,
        expiry_date DATE NULL,
        is_verified BIT NOT NULL CONSTRAINT DF_rental_vehicle_docs_verified DEFAULT 0,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_vehicle_docs_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_vehicle_documents PRIMARY KEY (id),

        CONSTRAINT FK_rental_vehicle_documents_vehicle
            FOREIGN KEY (vehicle_id)
            REFERENCES dbo.rental_vehicles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_vehicle_documents_type
            CHECK (document_type IN ('revenue_license', 'insurance_certificate', 'vcr', 'fitness_certificate', 'other'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_vehicle_documents_vehicle
        ON dbo.rental_vehicle_documents(vehicle_id);

    PRINT 'rental_vehicle_documents table created.';
END;

IF OBJECT_ID('dbo.rental_availability_blocks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_availability_blocks (
        id INT IDENTITY(1,1) NOT NULL,
        vehicle_id INT NOT NULL,
        start_datetime DATETIME2 NOT NULL,
        end_datetime DATETIME2 NOT NULL,
        block_type NVARCHAR(50) NOT NULL,
        reason NVARCHAR(500) NULL,
        is_active BIT NOT NULL CONSTRAINT DF_rental_blocks_active DEFAULT 1,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_blocks_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_availability_blocks PRIMARY KEY (id),

        CONSTRAINT FK_rental_availability_blocks_vehicle
            FOREIGN KEY (vehicle_id)
            REFERENCES dbo.rental_vehicles(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_availability_blocks_dates
            CHECK (end_datetime > start_datetime),

        CONSTRAINT CK_rental_availability_blocks_type
            CHECK (block_type IN ('maintenance', 'service', 'accident_repair', 'owner_unavailable', 'administrative'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_availability_blocks_vehicle
        ON dbo.rental_availability_blocks(vehicle_id, start_datetime, end_datetime);

    PRINT 'rental_availability_blocks table created.';
END;
GO

-- =============================================================================
-- Batch 5: Extend dbo.rental_bookings with lifecycle, location, and snapshot fields
-- =============================================================================
IF COL_LENGTH('dbo.rental_bookings', 'pickup_location_id') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD pickup_location_id INT NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'return_location_id') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD return_location_id INT NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'currency') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD currency NVARCHAR(3) NOT NULL
        CONSTRAINT DF_rental_bookings_currency DEFAULT 'LKR';
END;

IF COL_LENGTH('dbo.rental_bookings', 'payment_method') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD payment_method NVARCHAR(30) NOT NULL
        CONSTRAINT DF_rental_bookings_payment_method DEFAULT 'pay_at_location';
END;

IF COL_LENGTH('dbo.rental_bookings', 'payment_status') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD payment_status NVARCHAR(30) NOT NULL
        CONSTRAINT DF_rental_bookings_payment_status DEFAULT 'unpaid';
END;

IF COL_LENGTH('dbo.rental_bookings', 'expires_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD expires_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'cancellation_reason') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD cancellation_reason NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'cancelled_by') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD cancelled_by INT NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'cancelled_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD cancelled_at DATETIME2 NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'daily_rate_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD daily_rate_snapshot DECIMAL(12,2) NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'cancellation_policy_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD cancellation_policy_snapshot NVARCHAR(1000) NULL;
END;

IF COL_LENGTH('dbo.rental_bookings', 'free_cancellation_hours_snapshot') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD free_cancellation_hours_snapshot INT NOT NULL
        CONSTRAINT DF_rental_bookings_cancellation_snapshot DEFAULT 24;
END;

IF COL_LENGTH('dbo.rental_bookings', 'updated_at') IS NULL
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD updated_at DATETIME2 NOT NULL
        CONSTRAINT DF_rental_bookings_updated DEFAULT SYSUTCDATETIME();
END;
GO

-- =============================================================================
-- Batch 6: Add constraints and indexes to dbo.rental_bookings
-- =============================================================================
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_rental_bookings_pickup_loc'
      AND parent_object_id = OBJECT_ID('dbo.rental_bookings')
)
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD CONSTRAINT FK_rental_bookings_pickup_loc
        FOREIGN KEY (pickup_location_id)
        REFERENCES dbo.rental_locations(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_rental_bookings_return_loc'
      AND parent_object_id = OBJECT_ID('dbo.rental_bookings')
)
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD CONSTRAINT FK_rental_bookings_return_loc
        FOREIGN KEY (return_location_id)
        REFERENCES dbo.rental_locations(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_rental_bookings_cancelled_by'
      AND parent_object_id = OBJECT_ID('dbo.rental_bookings')
)
BEGIN
    ALTER TABLE dbo.rental_bookings
    ADD CONSTRAINT FK_rental_bookings_cancelled_by
        FOREIGN KEY (cancelled_by)
        REFERENCES dbo.users(id)
        ON DELETE NO ACTION;
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_rental_bookings_dates'
      AND object_id = OBJECT_ID('dbo.rental_bookings')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_rental_bookings_dates
        ON dbo.rental_bookings(vehicle_id, pickup_at, return_at);
END;
GO

-- =============================================================================
-- Batch 7: Create eligibility checks, inspections, condition evidence, incidents & deposits
-- =============================================================================
IF OBJECT_ID('dbo.rental_eligibility_checks', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_eligibility_checks (
        id INT IDENTITY(1,1) NOT NULL,
        user_id INT NOT NULL,
        driver_license_number NVARCHAR(100) NOT NULL,
        license_country NVARCHAR(100) NOT NULL CONSTRAINT DF_rental_eligibility_country DEFAULT 'Sri Lanka',
        license_expiry_date DATE NOT NULL,
        license_front_image NVARCHAR(1000) NULL,
        license_back_image NVARCHAR(1000) NULL,
        idp_number NVARCHAR(100) NULL,
        verification_status NVARCHAR(30) NOT NULL CONSTRAINT DF_rental_eligibility_status DEFAULT 'pending',
        reviewed_by INT NULL,
        reviewed_at DATETIME2 NULL,
        rejection_reason NVARCHAR(1000) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_eligibility_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_eligibility_checks PRIMARY KEY (id),

        CONSTRAINT FK_rental_eligibility_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT FK_rental_eligibility_reviewer
            FOREIGN KEY (reviewed_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_rental_eligibility_status
            CHECK (verification_status IN ('pending', 'approved', 'rejected', 'expired'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_eligibility_user
        ON dbo.rental_eligibility_checks(user_id);

    PRINT 'rental_eligibility_checks table created.';
END;

IF OBJECT_ID('dbo.rental_inspections', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_inspections (
        id INT IDENTITY(1,1) NOT NULL,
        booking_id INT NOT NULL,
        inspection_type NVARCHAR(30) NOT NULL,
        odometer_reading INT NOT NULL,
        fuel_level_percent INT NOT NULL,
        cleanliness_status NVARCHAR(30) NOT NULL CONSTRAINT DF_rental_inspections_clean DEFAULT 'clean',
        inspected_by INT NULL,
        customer_signature NVARCHAR(1000) NULL,
        notes NVARCHAR(1000) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_inspections_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_inspections PRIMARY KEY (id),

        CONSTRAINT FK_rental_inspections_booking
            FOREIGN KEY (booking_id)
            REFERENCES dbo.rental_bookings(id)
            ON DELETE CASCADE,

        CONSTRAINT FK_rental_inspections_inspector
            FOREIGN KEY (inspected_by)
            REFERENCES dbo.users(id)
            ON DELETE NO ACTION,

        CONSTRAINT CK_rental_inspections_type
            CHECK (inspection_type IN ('pickup', 'return')),

        CONSTRAINT CK_rental_inspections_odometer
            CHECK (odometer_reading >= 0),

        CONSTRAINT CK_rental_inspections_fuel
            CHECK (fuel_level_percent BETWEEN 0 AND 100)
    );

    CREATE NONCLUSTERED INDEX IX_rental_inspections_booking
        ON dbo.rental_inspections(booking_id);

    PRINT 'rental_inspections table created.';
END;

IF OBJECT_ID('dbo.rental_condition_evidence', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_condition_evidence (
        id INT IDENTITY(1,1) NOT NULL,
        inspection_id INT NOT NULL,
        evidence_type NVARCHAR(30) NOT NULL,
        file_url NVARCHAR(1000) NOT NULL,
        area_of_vehicle NVARCHAR(50) NOT NULL,
        description NVARCHAR(500) NULL,
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_evidence_created DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_condition_evidence PRIMARY KEY (id),

        CONSTRAINT FK_rental_condition_evidence_inspection
            FOREIGN KEY (inspection_id)
            REFERENCES dbo.rental_inspections(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_evidence_type
            CHECK (evidence_type IN ('photo', 'video', 'document'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_condition_evidence_inspection
        ON dbo.rental_condition_evidence(inspection_id);

    PRINT 'rental_condition_evidence table created.';
END;

IF OBJECT_ID('dbo.rental_incidents', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_incidents (
        id INT IDENTITY(1,1) NOT NULL,
        booking_id INT NOT NULL,
        incident_type NVARCHAR(50) NOT NULL,
        incident_datetime DATETIME2 NOT NULL,
        description NVARCHAR(2000) NOT NULL,
        estimated_damage_cost DECIMAL(12,2) NOT NULL CONSTRAINT DF_rental_incidents_est DEFAULT 0,
        actual_repair_cost DECIMAL(12,2) NULL,
        police_report_reference NVARCHAR(100) NULL,
        resolution_status NVARCHAR(30) NOT NULL CONSTRAINT DF_rental_incidents_status DEFAULT 'reported',
        created_at DATETIME2 NOT NULL CONSTRAINT DF_rental_incidents_created DEFAULT SYSUTCDATETIME(),
        updated_at DATETIME2 NOT NULL CONSTRAINT DF_rental_incidents_updated DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_rental_incidents PRIMARY KEY (id),

        CONSTRAINT FK_rental_incidents_booking
            FOREIGN KEY (booking_id)
            REFERENCES dbo.rental_bookings(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_incidents_type
            CHECK (incident_type IN ('scratch', 'dent', 'accident', 'mechanical_failure', 'theft', 'late_return', 'traffic_fine')),

        CONSTRAINT CK_rental_incidents_est_cost
            CHECK (estimated_damage_cost >= 0),

        CONSTRAINT CK_rental_incidents_act_cost
            CHECK (actual_repair_cost IS NULL OR actual_repair_cost >= 0),

        CONSTRAINT CK_rental_incidents_status
            CHECK (resolution_status IN ('reported', 'under_investigation', 'insurance_claimed', 'customer_billed', 'resolved', 'dismissed'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_incidents_booking
        ON dbo.rental_incidents(booking_id);

    PRINT 'rental_incidents table created.';
END;

IF OBJECT_ID('dbo.rental_deposits', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_deposits (
        id INT IDENTITY(1,1) NOT NULL,
        booking_id INT NOT NULL,
        deposit_amount DECIMAL(12,2) NOT NULL,
        currency NVARCHAR(3) NOT NULL CONSTRAINT DF_rental_deposits_currency DEFAULT 'LKR',
        deposit_status NVARCHAR(30) NOT NULL CONSTRAINT DF_rental_deposits_status DEFAULT 'held',
        deducted_amount DECIMAL(12,2) NOT NULL CONSTRAINT DF_rental_deposits_deducted DEFAULT 0,
        deduction_reason NVARCHAR(1000) NULL,
        refunded_amount DECIMAL(12,2) NOT NULL CONSTRAINT DF_rental_deposits_refunded DEFAULT 0,
        held_at DATETIME2 NOT NULL CONSTRAINT DF_rental_deposits_held DEFAULT SYSUTCDATETIME(),
        released_at DATETIME2 NULL,

        CONSTRAINT PK_rental_deposits PRIMARY KEY (id),

        CONSTRAINT FK_rental_deposits_booking
            FOREIGN KEY (booking_id)
            REFERENCES dbo.rental_bookings(id)
            ON DELETE CASCADE,

        CONSTRAINT CK_rental_deposits_amount
            CHECK (deposit_amount >= 0),

        CONSTRAINT CK_rental_deposits_deducted
            CHECK (deducted_amount >= 0),

        CONSTRAINT CK_rental_deposits_refunded
            CHECK (refunded_amount >= 0),

        CONSTRAINT CK_rental_deposits_status
            CHECK (deposit_status IN ('held', 'partially_refunded', 'fully_refunded', 'forfeited', 'deducted'))
    );

    CREATE NONCLUSTERED INDEX IX_rental_deposits_booking
        ON dbo.rental_deposits(booking_id);

    PRINT 'rental_deposits table created.';
END;
GO
