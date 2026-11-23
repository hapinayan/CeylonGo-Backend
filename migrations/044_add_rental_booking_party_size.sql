IF COL_LENGTH(N'dbo.rental_bookings', N'passenger_count') IS NULL
    ALTER TABLE dbo.rental_bookings ADD passenger_count INT NULL;
GO

IF COL_LENGTH(N'dbo.rental_bookings', N'self_drive_agreement_accepted') IS NULL
    ALTER TABLE dbo.rental_bookings ADD self_drive_agreement_accepted BIT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = N'CK_rental_bookings_passenger_count'
      AND parent_object_id = OBJECT_ID(N'dbo.rental_bookings')
)
    ALTER TABLE dbo.rental_bookings WITH CHECK
        ADD CONSTRAINT CK_rental_bookings_passenger_count
        CHECK (passenger_count IS NULL OR passenger_count > 0);
GO
