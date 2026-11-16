
-- Migration: 004_create_homestay_bookings.sql
-- Module: M01 Homestay Booking & Availability
-- Target: Microsoft SQL Server

IF OBJECT_ID('dbo.homestay_bookings', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.homestay_bookings (
        id INT IDENTITY(1,1) NOT NULL,
        homestay_id INT NOT NULL,
        user_id INT NOT NULL,
        check_in DATE NOT NULL,
        check_out DATE NOT NULL,
        guests INT NOT NULL,
        total_price DECIMAL(12,2) NOT NULL,
        status NVARCHAR(30) NOT NULL
            DEFAULT 'confirmed',
        created_at DATETIME2 NOT NULL
            DEFAULT SYSUTCDATETIME(),

        CONSTRAINT PK_homestay_bookings
            PRIMARY KEY (id),

        CONSTRAINT FK_bookings_homestays
            FOREIGN KEY (homestay_id)
            REFERENCES dbo.homestays(id),

        CONSTRAINT FK_bookings_users
            FOREIGN KEY (user_id)
            REFERENCES dbo.users(id),

        CONSTRAINT CK_bookings_dates
            CHECK (check_out > check_in),

        CONSTRAINT CK_bookings_guests
            CHECK (guests > 0),

        CONSTRAINT CK_bookings_total_price
            CHECK (total_price >= 0),

        CONSTRAINT CK_bookings_status
            CHECK (status IN (
                'confirmed',
                'cancelled'
            ))
    );

    CREATE INDEX IX_homestay_bookings_homestay_id
        ON dbo.homestay_bookings(homestay_id);

    CREATE INDEX IX_homestay_bookings_user_id
        ON dbo.homestay_bookings(user_id);

    PRINT 'homestay_bookings table created.';
END
ELSE
BEGIN
    PRINT 'homestay_bookings table already exists.';
END
GO
