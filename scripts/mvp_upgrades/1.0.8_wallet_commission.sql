-- Academic MVP 1.0.8. The runner owns the transaction and schema ledger.
SET ANSI_NULLS ON;
SET ANSI_PADDING ON;
SET ANSI_WARNINGS ON;
SET ARITHABORT ON;
SET CONCAT_NULL_YIELDS_NULL ON;
SET QUOTED_IDENTIFIER ON;
SET NUMERIC_ROUNDABORT OFF;

CREATE TABLE dbo.provider_wallets (
    id BIGINT NOT NULL IDENTITY(1,1),
    provider_id BIGINT NOT NULL,
    balance DECIMAL(18, 2) NOT NULL DEFAULT 0,
    free_bookings_used INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME2(3) NOT NULL,
    updated_at DATETIME2(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_provider_wallet_balance CHECK (balance >= 0),
    CONSTRAINT ck_wallet_free_used CHECK (free_bookings_used BETWEEN 0 AND 3),
    UNIQUE (provider_id),
    FOREIGN KEY(provider_id) REFERENCES dbo.provider_profiles (id)
);

CREATE TABLE dbo.wallet_transactions (
    id BIGINT NOT NULL IDENTITY(1,1),
    wallet_id BIGINT NOT NULL,
    booking_id BIGINT NULL,
    deduction_id BIGINT NULL,
    transaction_type VARCHAR(24) NOT NULL,
    amount DECIMAL(18, 2) NOT NULL,
    balance_before DECIMAL(18, 2) NOT NULL,
    balance_after DECIMAL(18, 2) NOT NULL,
    commission_rate DECIMAL(5, 4) NULL,
    booking_amount DECIMAL(12, 2) NULL,
    request_key UNIQUEIDENTIFIER NULL,
    simulate_outcome VARCHAR(7) NULL,
    description NVARCHAR(200) NOT NULL,
    created_at DATETIME2(3) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_wallet_transaction_amounts CHECK (amount >= 0 AND balance_before >= 0 AND balance_after >= 0),
    CONSTRAINT ck_wallet_transaction_shape CHECK ((transaction_type = 'top_up' AND booking_id IS NULL AND request_key IS NOT NULL
                AND amount > 0 AND booking_amount IS NULL AND commission_rate IS NULL
                AND deduction_id IS NULL AND simulate_outcome IS NOT NULL
                AND ((simulate_outcome = 'success' AND ROUND(balance_after - balance_before, 2) = amount)
                  OR (simulate_outcome = 'failure' AND balance_after = balance_before)))
            OR (booking_id IS NOT NULL AND request_key IS NULL AND booking_amount IS NOT NULL
                AND booking_amount >= 0 AND commission_rate IS NOT NULL
                AND simulate_outcome IS NULL AND (
                    (transaction_type = 'free_booking' AND amount = 0 AND commission_rate = 0
                        AND balance_before = balance_after AND deduction_id IS NULL)
                 OR (transaction_type = 'commission_deduction' AND commission_rate = 0.10
                        AND ROUND(balance_before - balance_after, 2) = amount AND deduction_id IS NULL)
                 OR (transaction_type = 'commission_refund' AND commission_rate = 0.10
                        AND ROUND(balance_after - balance_before, 2) = amount AND deduction_id IS NOT NULL)
                ))),
    FOREIGN KEY(wallet_id) REFERENCES dbo.provider_wallets (id),
    FOREIGN KEY(booking_id) REFERENCES dbo.bookings (id),
    FOREIGN KEY(deduction_id) REFERENCES dbo.wallet_transactions (id)
);

CREATE INDEX ix_wallet_tx_history ON dbo.wallet_transactions (wallet_id, created_at, id);

CREATE UNIQUE INDEX ux_wallet_tx_booking_type ON dbo.wallet_transactions (booking_id, transaction_type) WHERE booking_id IS NOT NULL;

CREATE UNIQUE INDEX ux_wallet_tx_refund_deduction ON dbo.wallet_transactions (deduction_id) WHERE deduction_id IS NOT NULL;

CREATE UNIQUE INDEX ux_wallet_tx_request ON dbo.wallet_transactions (wallet_id, request_key) WHERE request_key IS NOT NULL;

-- Count each historically confirmed booking once, even if subsequently cancelled.
-- Leave balances at zero and do not create historical ledger deductions.
INSERT INTO dbo.provider_wallets (provider_id, balance, free_bookings_used, created_at, updated_at)
SELECT p.id, 0, CASE WHEN history.used >= 3 THEN 3 ELSE history.used END,
       SYSUTCDATETIME(), SYSUTCDATETIME()
FROM dbo.provider_profiles AS p
JOIN dbo.users AS u ON u.id = p.user_id
CROSS APPLY (
    SELECT COUNT_BIG(*) AS used
    FROM dbo.bookings AS b
    JOIN dbo.listings AS l ON l.id = b.listing_id
    WHERE l.provider_id = p.id
      AND l.category IN ('homestay', 'adventure', 'guide', 'bike', 'bicycle')
      AND (b.status IN ('confirmed', 'completed') OR EXISTS (
          SELECT 1 FROM dbo.booking_status_history AS h
          WHERE h.booking_id = b.id AND h.to_status = 'confirmed'
      ))
) AS history
WHERE p.verification_status = 'approved' AND u.role = 'provider' AND u.is_active = 1;
