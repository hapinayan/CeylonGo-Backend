-- Proposal A: Add audited provider rejection without changing existing reservations.
ALTER TABLE dbo.artisan_reservations
ADD rejected_by INT NULL,
    rejected_at DATETIME2 NULL,
    rejection_reason NVARCHAR(1000) NULL;
GO

ALTER TABLE dbo.artisan_reservations WITH CHECK
ADD CONSTRAINT FK_artisan_reservations_rejector
    FOREIGN KEY (rejected_by) REFERENCES dbo.users(id);
GO

ALTER TABLE dbo.artisan_reservations
DROP CONSTRAINT CK_artisan_reservations_status;
GO

ALTER TABLE dbo.artisan_reservations WITH CHECK
ADD CONSTRAINT CK_artisan_reservations_status CHECK (
    reservation_status IN (
        'pending', 'confirmed', 'ready_for_pickup', 'completed',
        'cancelled', 'expired', 'rejected'
    )
),
CONSTRAINT CK_artisan_reservations_rejection_audit CHECK (
    (reservation_status = 'rejected'
     AND rejected_by IS NOT NULL
     AND rejected_at IS NOT NULL
     AND rejection_reason IS NOT NULL
     AND LEN(LTRIM(RTRIM(rejection_reason))) >= 3)
    OR
    (reservation_status <> 'rejected'
     AND rejected_by IS NULL
     AND rejected_at IS NULL
     AND rejection_reason IS NULL)
);
GO
