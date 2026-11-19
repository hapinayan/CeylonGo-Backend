-- Run as one migration transaction. Existing Activity rows and constraints stay intact.
IF OBJECT_ID(N'dbo.eco_experiences', N'U') IS NULL
   OR OBJECT_ID(N'dbo.users', N'U') IS NULL
   OR NOT EXISTS (
       SELECT 1 FROM sys.check_constraints
       WHERE parent_object_id = OBJECT_ID(N'dbo.booking_references')
         AND name = N'CK_booking_references_type'
         AND is_disabled = 0 AND is_not_trusted = 0
   )
    THROW 50033, 'Eco reservations require the existing trusted booking schema.', 1;

IF OBJECT_ID(N'dbo.eco_availability_slots', N'U') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_reservations', N'U') IS NOT NULL
    THROW 50034, 'Unexpected pre-existing Eco reservation tables.', 1;

CREATE TABLE dbo.eco_availability_slots (
    id INT IDENTITY(1,1) CONSTRAINT PK_eco_availability_slots PRIMARY KEY,
    eco_experience_id INT NOT NULL REFERENCES dbo.eco_experiences(id),
    starts_at DATETIME2 NOT NULL,
    ends_at DATETIME2 NOT NULL,
    capacity INT NOT NULL,
    reserved_count INT NOT NULL CONSTRAINT DF_eco_slot_reserved DEFAULT (0),
    is_open BIT NOT NULL CONSTRAINT DF_eco_slot_open DEFAULT (1),
    created_at DATETIME2 NOT NULL CONSTRAINT DF_eco_slot_created DEFAULT SYSUTCDATETIME(),
    CONSTRAINT CK_eco_slot_times CHECK (ends_at > starts_at),
    CONSTRAINT CK_eco_slot_capacity CHECK (
        capacity BETWEEN 1 AND 50 AND reserved_count BETWEEN 0 AND capacity
    )
);
CREATE INDEX IX_eco_slot_schedule ON dbo.eco_availability_slots(eco_experience_id, starts_at);

CREATE TABLE dbo.eco_reservations (
    id INT IDENTITY(1,1) CONSTRAINT PK_eco_reservations PRIMARY KEY,
    slot_id INT NOT NULL REFERENCES dbo.eco_availability_slots(id),
    traveler_id INT NOT NULL REFERENCES dbo.users(id),
    participants INT NOT NULL,
    capacity_held INT NOT NULL,
    total_price DECIMAL(12,2) NOT NULL,
    currency NVARCHAR(3) NOT NULL,
    free_cancellation_hours INT NOT NULL CHECK (free_cancellation_hours >= 0),
    status NVARCHAR(30) NOT NULL CONSTRAINT DF_eco_reservation_status DEFAULT N'pending',
    payment_status NVARCHAR(30) NOT NULL CONSTRAINT DF_eco_reservation_payment DEFAULT N'unpaid',
    holds_capacity BIT NOT NULL CONSTRAINT DF_eco_reservation_hold DEFAULT (1),
    created_at DATETIME2 NOT NULL CONSTRAINT DF_eco_reservation_created DEFAULT SYSUTCDATETIME(),
    decided_at DATETIME2 NULL,
    cancelled_at DATETIME2 NULL,
    completed_at DATETIME2 NULL,
    CONSTRAINT CK_eco_reservation_amount CHECK (total_price >= 0),
    CONSTRAINT CK_eco_reservation_participants CHECK (
        participants BETWEEN 1 AND 50 AND capacity_held BETWEEN participants AND 50
    ),
    CONSTRAINT CK_eco_reservation_payment CHECK (
        payment_status = N'unpaid' OR (payment_status = N'paid' AND status IN (N'confirmed', N'completed'))
    ),
    CONSTRAINT CK_eco_reservation_state CHECK (
        (holds_capacity = 1 AND status IN (N'pending', N'confirmed', N'completed'))
        OR (holds_capacity = 0 AND status IN (N'rejected', N'cancelled'))
    )
);
CREATE UNIQUE INDEX UX_eco_reservation_active_traveler
    ON dbo.eco_reservations(slot_id, traveler_id) WHERE holds_capacity = 1;
CREATE INDEX IX_eco_reservation_traveler ON dbo.eco_reservations(traveler_id, id);

-- Preserve the installed predicate verbatim and extend it with the new module.
DECLARE @original NVARCHAR(MAX) = (
    SELECT definition FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'dbo.booking_references')
      AND name = N'CK_booking_references_type'
);
ALTER TABLE dbo.booking_references DROP CONSTRAINT CK_booking_references_type;
DECLARE @replacement NVARCHAR(MAX) =
    N'ALTER TABLE dbo.booking_references WITH CHECK ADD CONSTRAINT CK_booking_references_type CHECK ('
    + @original + N' OR [module_type] = N''eco_experience'');';
EXEC sys.sp_executesql @replacement;
