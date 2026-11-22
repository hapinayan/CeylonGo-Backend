-- The migration runner owns the transaction and records the ledger row.
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

IF OBJECT_ID(N'dbo.schema_migrations', N'U') IS NULL
    THROW 51040, 'Eco migrations require the existing canonical migration ledger.', 1;

IF (
    SELECT COUNT(*) FROM dbo.schema_migrations
    WHERE version BETWEEN '001' AND '040' AND version LIKE '[0-9][0-9][0-9]'
) <> 40
    THROW 51041, 'Eco migrations require a verified ledger through 040.', 1;

IF OBJECT_ID(N'dbo.users', N'U') IS NULL
   OR OBJECT_ID(N'dbo.eco_experiences', N'U') IS NULL
   OR OBJECT_ID(N'dbo.eco_availability_slots', N'U') IS NULL
   OR OBJECT_ID(N'dbo.eco_reservations', N'U') IS NULL
   OR OBJECT_ID(N'dbo.payment_refunds', N'U') IS NULL
    THROW 51042, 'Eco native prerequisites are missing.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'dbo.payment_refunds', N'U')
      AND name = N'CK_payment_refunds_status'
      AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 51044, 'Expected a trusted shared refund status constraint.', 1;

IF OBJECT_ID(N'dbo.eco_cancellation_policies') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_cancellation_snapshots') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_age_limits') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_slot_clearances') IS NOT NULL
   OR OBJECT_ID(N'dbo.eco_refund_reconciliations') IS NOT NULL
    THROW 51047, 'Unexpected existing Eco migration object. Reconcile before upgrade.', 1;

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'dbo.eco_reservations')
      AND name = N'CK_eco_reservation_payment'
      AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 51001, 'Expected a trusted Eco reservation payment constraint.', 1;

-- Preflight compares the installed definition to 034 before authorizing this widening.
IF EXISTS (
    SELECT 1 FROM dbo.eco_reservations
    WHERE status IS NULL OR payment_status IS NULL
       OR NOT (
           payment_status = N'unpaid'
           OR (payment_status = N'paid' AND status IN (N'confirmed', N'completed'))
       )
)
    THROW 51043, 'Eco reservation payment data does not match the canonical 034 baseline.', 1;

ALTER TABLE dbo.eco_reservations DROP CONSTRAINT CK_eco_reservation_payment;
ALTER TABLE dbo.eco_reservations WITH CHECK
    ADD CONSTRAINT CK_eco_reservation_payment CHECK (
        payment_status = N'unpaid'
        OR (payment_status = N'paid' AND status IN (N'confirmed', N'completed'))
        OR (
            payment_status IN (
                N'refund_pending', N'refund_rejected', N'no_refund_due',
                N'refund_failed', N'refunded', N'partially_refunded'
            ) AND status = N'cancelled'
        )
    );

CREATE TABLE dbo.eco_cancellation_policies (
    eco_experience_id INT NOT NULL CONSTRAINT PK_eco_cancellation_policies PRIMARY KEY
        CONSTRAINT FK_eco_policy_experience REFERENCES dbo.eco_experiences(id),
    late_refund_percent INT NOT NULL CONSTRAINT CK_eco_policy_percent CHECK (
        late_refund_percent BETWEEN 0 AND 100
    )
);

CREATE TABLE dbo.eco_cancellation_snapshots (
    reservation_id INT NOT NULL CONSTRAINT PK_eco_cancellation_snapshots PRIMARY KEY
        CONSTRAINT FK_eco_snapshot_reservation REFERENCES dbo.eco_reservations(id),
    late_refund_percent INT NOT NULL CONSTRAINT CK_eco_policy_snapshot_percent CHECK (
        late_refund_percent BETWEEN 0 AND 100
    )
);

CREATE TABLE dbo.eco_age_limits (
    eco_experience_id INT NOT NULL CONSTRAINT PK_eco_age_limits PRIMARY KEY
        CONSTRAINT FK_eco_age_limit_experience REFERENCES dbo.eco_experiences(id),
    maximum_age INT NOT NULL CONSTRAINT CK_eco_maximum_age CHECK (maximum_age BETWEEN 0 AND 100)
);

CREATE TABLE dbo.eco_slot_clearances (
    slot_id INT NOT NULL CONSTRAINT FK_eco_clearance_slot
        REFERENCES dbo.eco_availability_slots(id),
    kind NVARCHAR(10) NOT NULL CONSTRAINT CK_eco_clearance_kind CHECK (
        kind IN (N'permit', N'guide')
    ),
    verified_by INT NOT NULL CONSTRAINT FK_eco_clearance_reviewer REFERENCES dbo.users(id),
    evidence_reference NVARCHAR(100) NOT NULL
        CONSTRAINT CK_eco_clearance_evidence CHECK (LTRIM(RTRIM(evidence_reference)) <> N''),
    valid_until DATETIME2 NOT NULL,
    revoked_at DATETIME2 NULL,
    CONSTRAINT PK_eco_slot_clearances PRIMARY KEY (slot_id, kind)
);

CREATE TABLE dbo.eco_refund_reconciliations (
    refund_id INT NOT NULL CONSTRAINT PK_eco_refund_reconciliations PRIMARY KEY
        CONSTRAINT FK_eco_reconciliation_refund REFERENCES dbo.payment_refunds(id),
    state NVARCHAR(30) NOT NULL CONSTRAINT CK_eco_refund_reconciliation_state CHECK (
        state IN (N'processing', N'succeeded', N'failed', N'reconciliation_required')
    ),
    gateway_refund_reference NVARCHAR(50) NULL
);

CREATE UNIQUE INDEX UX_eco_refund_gateway_reference
    ON dbo.eco_refund_reconciliations(gateway_refund_reference)
    WHERE gateway_refund_reference IS NOT NULL;
