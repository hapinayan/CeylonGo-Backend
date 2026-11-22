-- PROPOSAL ONLY. Outside the executable migration inventory.
-- Requires separate approval, a verified canonical 034 constraint, and a runner transaction.
IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID(N'dbo.eco_reservations')
      AND name = N'CK_eco_reservation_payment'
      AND is_disabled = 0 AND is_not_trusted = 0
)
    THROW 51001, 'Expected a trusted Eco reservation payment constraint.', 1;

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
    eco_experience_id INT PRIMARY KEY REFERENCES dbo.eco_experiences(id),
    late_refund_percent INT NOT NULL CONSTRAINT CK_eco_policy_percent CHECK (
        late_refund_percent BETWEEN 0 AND 100
    )
);

CREATE TABLE dbo.eco_cancellation_snapshots (
    reservation_id INT PRIMARY KEY REFERENCES dbo.eco_reservations(id),
    late_refund_percent INT NOT NULL CONSTRAINT CK_eco_policy_snapshot_percent CHECK (
        late_refund_percent BETWEEN 0 AND 100
    )
);

CREATE TABLE dbo.eco_age_limits (
    eco_experience_id INT PRIMARY KEY REFERENCES dbo.eco_experiences(id),
    maximum_age INT NOT NULL CONSTRAINT CK_eco_maximum_age CHECK (maximum_age BETWEEN 0 AND 100)
);

CREATE TABLE dbo.eco_slot_clearances (
    slot_id INT NOT NULL REFERENCES dbo.eco_availability_slots(id),
    kind NVARCHAR(10) NOT NULL CONSTRAINT CK_eco_clearance_kind CHECK (
        kind IN (N'permit', N'guide')
    ),
    verified_by INT NOT NULL REFERENCES dbo.users(id),
    evidence_reference NVARCHAR(100) NOT NULL,
    valid_until DATETIME2 NOT NULL,
    revoked_at DATETIME2 NULL,
    PRIMARY KEY (slot_id, kind)
);

CREATE TABLE dbo.eco_refund_reconciliations (
    refund_id INT PRIMARY KEY REFERENCES dbo.payment_refunds(id),
    state NVARCHAR(30) NOT NULL CONSTRAINT CK_eco_refund_reconciliation_state CHECK (
        state IN (N'processing', N'succeeded', N'failed', N'reconciliation_required')
    ),
    gateway_refund_reference NVARCHAR(50) NULL
);

CREATE UNIQUE INDEX UX_eco_refund_gateway_reference
    ON dbo.eco_refund_reconciliations(gateway_refund_reference)
    WHERE gateway_refund_reference IS NOT NULL;
