# Phase 03A Standalone Eco-Tourism Reservations

Eco-Tourism now uses its own availability slots and reservations. Each slot belongs directly to an Eco experience. Provider management uses the same authenticated booking queue, details, actions, history and dashboard as the other categories. Existing Activity booking tables and their Eco links retain their original behavior.

## API

All paths below are relative to `/api/v1`.

| Method | Path | Access and behavior |
| --- | --- | --- |
| GET | `/eco-experiences/{experience_id}/availability` | Public, bounded future availability for approved and published experiences. Optional `day` uses Sri Lankan local dates. |
| GET | `/provider/eco-experiences/{experience_id}/slots` | Approved Eco provider, owned schedule including closed and historical slots. |
| POST | `/provider/eco-experiences/{experience_id}/slots` | Approved Eco provider, create an owned slot from timezone-aware `starts_at` and `capacity`. Ends are calculated from the listing duration. |
| PATCH | `/provider/eco-experiences/{experience_id}/slots/{slot_id}/close` | Approved Eco provider, close a slot to new requests without displacing existing reservations. |
| POST | `/eco-reservations` | Traveler, submit `slot_id`, `participants` and `accepts_safety_rules`. Server calculates the amount. |
| GET | `/eco-reservations/me` | Traveler, paginated own reservation history. |
| GET | `/eco-reservations/{booking_id}` | Traveler, own reservation detail. |
| POST | `/eco-reservations/{booking_id}/cancel` | Traveler, cancel an eligible unpaid reservation. Optional `reason`. |
| GET | `/provider/bookings` and `/provider/bookings/{booking_id}` | Existing approved provider endpoints now include owned Eco records and history. |
| POST | `/provider/bookings/{booking_id}/action` | Existing endpoint now supports Eco `accept`, `reject`, `cancel`, `cash` and `complete`. |
| GET | `/provider/workspace`, `/provider/dashboard`, `/provider/dashboard/analytics`, `/provider/dashboard/recent-activity` | Existing endpoints now expose Eco booking capability, native metrics, verified recorded cash and booking lifecycle events. |

Provider and traveler lists accept `page` (1–1000) and `page_size` (1–50). Booking details omit other travelers' records and use `404` for absent or foreign IDs. Closed slots, overlapping windows, concurrent conflicts and invalid transitions use `409`. Request format, horizon and group-minimum validation use `422`. Existing authorization dependencies enforce role and approved account status.

### Examples

```http
POST /api/v1/provider/eco-experiences/42/slots
Content-Type: application/json
Authorization: Bearer <provider-token>

{"starts_at":"2027-01-20T09:00:00+05:30","capacity":8}
```

```http
POST /api/v1/eco-reservations
Content-Type: application/json
Authorization: Bearer <traveler-token>

{"slot_id":23,"participants":2,"accepts_safety_rules":true}
```

```json
{
  "id": 61,
  "module": "eco_experience",
  "reference": "RS-8febb97453784e69aacc011cfb3d14ed",
  "experience_id": 42,
  "slot_id": 23,
  "participants": 2,
  "status": "pending",
  "payment_status": "unpaid",
  "payment_method": "pay_at_location",
  "total": "3600.00",
  "currency": "LKR",
  "starts_at": "2027-01-20T03:30:00Z",
  "ends_at": "2027-01-20T05:30:00Z"
}
```

## Booking and receipt behavior

- The calendar uses Sri Lankan local dates for availability, booking horizon and seasonal restrictions. Persisted timestamps are UTC. Overlapping open or held slots on the same experience are rejected. Existing Activity eligibility continues using its prior calendar behavior.
- A pending reservation immediately holds places. For per-person prices the hold is the party size. For per-group prices the hold is the entire slot. The server validates eligibility, published participant limits and mandatory-safety acknowledgement.
- Acceptance rechecks publication and seasonal eligibility. Rejection and eligible unpaid cancellation release the stored hold in the same transaction as the state transition. Travelers cancelling a confirmed booking must also meet the listing's snapshotted free-cancellation cutoff.
- Cash received at the location can be confirmed on an accepted booking by its owning provider. The receipt, native payment status, shared reference and audit event commit together. A conditional state update and receipt check prevent duplicate confirmation. Completion requires the service to have ended and any positive charge to be recorded as paid.
- SQL Server serializes writers on each experience. Conditional updates enforce capacity and transition state, and a filtered unique index prevents duplicate live requests. The shared ledger and status history use the new `eco_experience` discriminator. The dashboard reports Eco cash separately from online revenue. Existing financial reconciliation remains scoped to Homestay booking IDs.

## Migration and verification

Migration `033_create_eco_reservations.sql` adds two tables and extends the trusted `booking_references` type constraint by preserving its existing predicate. Migrations 001–032 remain intact. There is no Activity data conversion. The transaction rollback probe injected a failure after executing the DDL and checked that both new tables disappeared, the original constraint was restored and the existing Activity booking count was unchanged.

The integration ledger recorded migrations 001–031 before the upgrade. Only the approved 033 ran on `CeylonGoDB_IntegrationTest`, through the repository's migration discovery and checksum logic with an explicit isolated-target transaction. The ledger checksum, both new tables and the unchanged Activity booking count were verified after commit. The integration ledger still has no 032 entry. Primary `CeylonGoDB` was outside the test scope.

| Check | Actual result |
| --- | --- |
| Focused local tests | 47 passed. Covers the Eco API, current workspace, Phase 03 bookings and existing Eco eligibility. |
| Isolated SQL Server | 19 passed. Includes two-session races for the last capacity, duplicate reservation, competing rejection and cancellation, and duplicate cash confirmation. Includes existing Activity capacity and provider workflow regressions. |
| Full offline backend suite | 886 passed, 22 failed, 19 skipped. The failures are in the older Homestay and promotion tests listed below. SQL Server connections were blocked for this run. The 19 opt-in SQL cases ran separately. |
| Migration validation | All 33 migration files parsed. Migration 033 passed the injected-failure rollback probe and the isolated apply check. |

The full-suite failures were in `test_homestay.py`, `test_homestay_owner_api.py`, `test_homestay_phase1_implementation.py`, `test_promotion_api.py` and `test_promotion_filter_integration.py`. The errors include SQLite fixtures missing the `provider_profiles` table, fixtures receiving `404` from existing publication rules and a mocked promotion response missing valid discount fields. All 22 failures reproduced when those five files were rerun with the new Eco router disabled. That rerun passed 49 other cases.

## Remaining work

The standalone Eco schema blocker is resolved and the approved Phase 03A scope is implemented. Phase 03 remains partial overall. Eco online payment, provider settlements and paid cancellation/refund execution remain pending. Paid cancellation requires reconciliation. Scheduled expiry, notification reminders and the broader Phase 03 gaps remain open. Deployment of the updated Eco endpoints requires migration 033 on the deployment target.

## Phase 03A files

Created `migrations/033_create_eco_reservations.sql`, `app/models/eco_reservation.py`, `app/schemas/eco_reservation.py`, `app/routers/eco_reservations.py`, `app/services/eco_reservations.py`, `tests/test_eco_reservations.py`, `tests/test_eco_reservations_sqlserver.py` and this report.

Modified `app/main.py`, `app/models/__init__.py`, `app/routers/payments.py`, `app/routers/provider_reservations.py`, `app/services/eco_experience_eligibility.py`, `app/services/provider_workspace.py`, `tests/test_provider_phase03.py`, `tests/test_provider_workspace.py` and the [provider development tracker](PROVIDER_DEVELOPMENT_TRACKER.md).
