# Wallet and Commission API

All amounts are simulated and denominated in LKR. Currency amounts and rates in wallet responses are decimal strings. A rate of `"0.10"` means 10%.

## Booking and refund rules

- The first three confirmed bookings per approved provider use a shared allowance across homestays, adventures, guides, bikes, and bicycles. Planner-generated bookings use the same booking records and confirmation endpoint. Artisan showcases are excluded.
- Both simulated online payment and pay at location require provider confirmation. Booking creation and payment selection do not charge the wallet. Starting at confirmation four, the provider pays 10% of the stored booking total, rounded to two decimal places with Decimal half-up rounding.
- The booking decision, wallet balance, ledger entry, and existing booking event commit together. Existing payment states and inventory checks still apply.
- An authorized cancellation of a confirmed booking releases inventory through the existing workflow and refunds its original commission once. A refund references the original deduction. Pending bookings have no commission to refund, terminal transitions retain their existing restrictions, and a used free slot remains used after cancellation. Wallet refunds have no effect on traveler payments.
- New eligible providers get a zero-balance wallet on approval. The migration backfills active approved providers. Initialization counts each booking once if its current status is confirmed or completed, or if its status history shows a transition to confirmed, including later cancellations. The allowance counter caps at three. A historical cancellation without confirmation history cannot be inferred. Historical activity produces no retroactive commission or ledger entries.

## Database schema

| Table | Contents |
|---|---|
| `provider_wallets` | BIGINT identity, unique FK to `provider_profiles.id`, DECIMAL(18,2) balance, free bookings used from 0 to 3, UTC created and updated timestamps |
| `wallet_transactions` | BIGINT identity, wallet FK, optional booking FK and original deduction FK, type, DECIMAL amount and before/after balances, optional commission rate and booking amount, optional UNIQUEIDENTIFIER request key, simulation outcome, description, UTC creation time |

The ledger supports `top_up`, `free_booking`, `commission_deduction`, and `commission_refund`. Amounts and balances are nonnegative. Indexed uniqueness covers wallet/request-key pairs, booking/type pairs, and original deductions referenced by refunds. A failed simulated top-up records the attempted amount and an unchanged balance. Aggregates include successful top-ups only.

## Authentication and endpoints

Send the existing MVP bearer token in `Authorization: Bearer <token>`. Provider routes require an active approved provider and derive ownership from that token. Administrative routes use the existing MVP `admin` role, the single administrative role in this runtime. The legacy Super Admin login is outside the MVP authentication flow.

All paths below start with `/api/v1`.

| Method and path | Response |
|---|---|
| GET `/provider/wallet` | Balance, commission rate, free bookings used and remaining, total top-ups, deducted and refunded commission, net commission paid |
| POST `/provider/wallet/top-up` | Simulated outcome, current balance, message, and ledger transaction |
| GET `/provider/wallet/transactions` | Paginated provider ledger |
| GET `/provider/bookings/{booking_id}/commission-preview` | Booking amount, rate, required commission, free-booking eligibility, balance, shortfall, and whether confirmation can currently proceed |
| GET `/admin/wallets/summary` | Wallet count, combined balances, top-ups, gross commission, commission refunds, net commission revenue, and free bookings used including capped historical allowances |
| GET `/admin/wallets` | Paginated wallet summaries |
| GET `/admin/wallets/{provider_id}` | Wallet summary for one provider |
| GET `/admin/wallets/{provider_id}/transactions` | Paginated ledger for one provider |

Paginated results return `page`, `page_size`, `total`, and `items`. Page numbers start at 1 and page size defaults to 25 with a maximum of 100. Both ledger routes accept optional `transaction_type`, `date_from` (inclusive), and `date_to` (exclusive). Use ISO 8601 timestamps with offsets, such as `2026-11-27T00:00:00+05:30`. Newest ledger entries appear first. Administrative endpoints are read-only.

### Wallet and commission preview examples

A provider with its allowance used and LKR 250.00 in the wallet gets this summary:

```json
{
  "provider_id": 7,
  "balance": "250.00",
  "currency": "LKR",
  "commission_rate": "0.10",
  "free_bookings_used": 3,
  "free_bookings_remaining": 0,
  "total_top_ups": "1000.00",
  "total_commission_deducted": "800.00",
  "total_commission_refunded": "50.00",
  "net_commission_paid": "750.00"
}
```

For a pending LKR 5,000.00 booking, the preview returns:

```json
{
  "booking_amount": "5000.00",
  "commission_rate": "0.10",
  "required_commission": "500.00",
  "free_booking_eligible": false,
  "wallet_balance": "250.00",
  "shortfall": "250.00",
  "confirmation_allowed": false,
  "currency": "LKR"
}
```

### Top-up example

```json
{
  "amount": "2500.00",
  "request_key": "34b8cab3-9167-4b91-a718-fad99b20b419",
  "simulate_outcome": "success"
}
```

Suggested amounts are LKR 1,000, 2,500, 5,000 and 10,000. Custom amounts range from LKR 0.01 through 1,000,000.00, with at most two decimal places. A result contains `success`, `currency`, `balance`, `message`, and `transaction`. The transaction contains `id`, `booking_id`, `deduction_id`, `transaction_type`, `amount`, `balance_before`, `balance_after`, `commission_rate`, `booking_amount`, `request_key`, `simulate_outcome`, `description`, and `created_at`. An HTTP 200 result with `success: false` represents a simulated failure and credits LKR 0.00.

Retries must reuse the same lowercase, hyphenated UUID and payload. A replay returns the original transaction and current balance. Changing the amount or simulation outcome under a used key returns HTTP 409.

### Booking confirmation example

Fetch the preview for a pending booking. Continue to use `PATCH /api/v1/bookings/{booking_id}/status` with `{"to_status":"confirmed"}`. The server recalculates the fee and revalidates the booking upon confirmation. If funding is insufficient, HTTP 409 returns:

```json
{
  "detail": {
    "code": "insufficient_wallet_balance",
    "message": "Top up your wallet before confirming this booking.",
    "currency": "LKR",
    "current_balance": "250.00",
    "required_commission": "500.00",
    "shortfall": "250.00"
  }
}
```

The booking remains pending and its inventory allocation stays as it was. Successful provider-initiated decisions include an `X-Wallet-Event` response header for free confirmations, deductions, or refunds. Active MVP notifications serve travelers. Providers can display top-up messages and insufficient-balance errors directly, use the event header for their own decisions, and surface refunds from the ledger when another authorized actor cancels.

## Angular and Flutter integration

1. Use the existing provider session to fetch the summary and ledger when the wallet opens. Render values from the server using a decimal or fixed-point representation.
2. Label the top-up screen **Simulated payment**. Reuse the same UUID after transport errors and generate a new UUID for a new attempt. Check the `success` field before displaying a credit confirmation.
3. Fetch the booking preview when the provider opens a pending request. Treat it as advisory. On structured HTTP 409, show the shortfall and offer a top-up. Refresh the preview and let the provider retry the existing confirmation action.
4. Refresh wallet balance and history after top-up or a booking decision. Display refunds from the history, including those initiated by another actor. Angular callers can read `X-Wallet-Event` with `observe: 'response'`. Flutter callers can read it from response headers.
5. Use an MVP admin token for the read-only monitoring screens. Label commission revenue as simulated.

## Backup and upgrade

MVP ledger version `1.0.8` adds two tables to the existing database. The expected count is the verified pre-upgrade count plus two. An MVP-only schema assembled from the repository through 1.0.7 has 29 tables and reaches 31. A read-only CeylonGoDB preflight on 2026-11-27 found 29 user tables at 1.0.7, with only 1.0.8 pending. The expected CeylonGoDB count is 31.

Run from the backend root. First verify that the dry run lists only `1.0.8` as pending. The runner refuses the wallet upgrade on CeylonGoDB without the 1.0.7 predecessor.

```powershell
.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server '.\SQLEXPRESS' --target 1.0.8 --dry-run
```

Before an approved deployment, pause application writes and take a copy-only backup to the SQL Server service's writable backup directory. The following commands use the instance backup directory recorded in the project backup report.

```powershell
$walletBackup = "C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup\CeylonGoDB_pre_wallet_$(Get-Date -Format yyyyMMdd_HHmmss).bak"
sqlcmd -S '.\SQLEXPRESS' -E -C -b -Q "BACKUP DATABASE [CeylonGoDB] TO DISK = N'$walletBackup' WITH COPY_ONLY, CHECKSUM, STATS = 10;"
if ($LASTEXITCODE -ne 0) { throw 'Wallet deployment backup failed' }
sqlcmd -S '.\SQLEXPRESS' -E -C -b -Q "RESTORE VERIFYONLY FROM DISK = N'$walletBackup' WITH CHECKSUM;"
if ($LASTEXITCODE -ne 0) { throw 'Wallet deployment backup verification failed' }
```

After explicit approval to apply the schema:

```powershell
.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB upgrade --server '.\SQLEXPRESS' --target 1.0.8 --yes
.venv\Scripts\python.exe scripts/bootstrap_mvp_schema.py CeylonGoDB status --server '.\SQLEXPRESS'
```

Deploy the backend against the upgraded schema before resuming application writes. The runner commits table creation, provider backfill and the MVP ledger update together. Applying version 1.0.8 requires no legacy migrations or additional database.
