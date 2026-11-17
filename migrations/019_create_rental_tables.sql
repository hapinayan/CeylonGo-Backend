
-- Module 09: Rental Vehicle and Booking Tables
-- SQL Server Migration

IF OBJECT_ID('dbo.rental_vehicles', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_vehicles (
        id INT IDENTITY(1,1) PRIMARY KEY,
        provider_id INT NOT NULL,
        vehicle_type NVARCHAR(30) NOT NULL,
        vehicle_name NVARCHAR(150) NOT NULL,
        description NVARCHAR(1000) NULL,
        price_per_day DECIMAL(12,2) NOT NULL,
        status NVARCHAR(30) NOT NULL DEFAULT 'available',
        created_at DATETIME2 NOT NULL
            DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_rental_vehicles_provider
            FOREIGN KEY (provider_id)
            REFERENCES dbo.provider_profiles(id),

        CONSTRAINT CK_rental_vehicle_type
            CHECK (vehicle_type IN ('bike', 'bicycle', 'tuk_tuk')),

        CONSTRAINT CK_rental_vehicle_price
            CHECK (price_per_day >= 0)
    );

    CREATE INDEX IX_rental_vehicles_provider_id
        ON dbo.rental_vehicles(provider_id);

    PRINT 'rental_vehicles table created.';
END
ELSE
BEGIN
    PRINT 'rental_vehicles table already exists.';
END
GO

IF OBJECT_ID('dbo.rental_bookings', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.rental_bookings (
        id INT IDENTITY(1,1) PRIMARY KEY,
        vehicle_id INT NOT NULL,
        user_id INT NOT NULL,
        pickup_at DATETIME2 NOT NULL,
        return_at DATETIME2 NOT NULL,
        total_price DECIMAL(12,2) NOT NULL,
        status NVARCHAR(30) NOT NULL DEFAULT 'pending',
        created_at DATETIME2 NOT NULL
            DEFAULT SYSUTCDATETIME(),

        CONSTRAINT FK_rental_bookings_vehicle
            FOREIGN KEY (vehicle_id)
            REFERENCES dbo.rental_vehicles(id),

        CONSTRAINT FK_rental_bookings_user
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id),

        CONSTRAINT CK_rental_booking_dates
            CHECK (return_at > pickup_at),

        CONSTRAINT CK_rental_booking_price
            CHECK (total_price >= 0)
    );

    CREATE INDEX IX_rental_bookings_vehicle_id
        ON dbo.rental_bookings(vehicle_id);

    CREATE INDEX IX_rental_bookings_user_id
        ON dbo.rental_bookings(user_id);

    PRINT 'rental_bookings table created.';
END
ELSE
BEGIN
    PRINT 'rental_bookings table already exists.';
END
GO
