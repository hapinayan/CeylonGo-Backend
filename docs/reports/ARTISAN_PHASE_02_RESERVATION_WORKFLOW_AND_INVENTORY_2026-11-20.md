# Artisan Reservation Workflow API and Migration Inventory

**Phase 02 approved rules** · 20 November 2026 · `feature/artisan-integration`

## Reservation and stock workflow

Both payloads on `POST /api/v1/artisan-reservations` call the same hold service. The selected quantity is deducted immediately under the product transaction lock using a conditional SQL update. The item must be an eligible, approved, priced product under an active, approved and verified listing with an approved Artisan provider. The server computes the amount and rejects sold-out or insufficient stock, duplicate open holds, unsupported prices or currency, and out-of-window pickup dates. The existing 90-day booking window remains in effect. A product's provider availability flag and its remaining stock are separate signals.

The hold deadline is the first instant after the selected pickup day in Sri Lanka. It is stored as naive UTC. Legacy holds with a pickup date and no saved deadline use the same cutoff without a backfill. An existing earlier stored deadline is honored. Holds with neither field have no derivable deadline and require operational review. The batch worker and CLI dry run use the same due predicate. Paid or captured holds are excluded from automatic stock release.

| Action | Actor and condition | Result |
| --- | --- | --- |
| Reserve | Authenticated traveler, eligible item, sufficient stock | Pending; hold, shared reference, history and provider notice commit together. |
| Accept | Approved owning provider, pending hold with a valid pickup day, before the deadline | Confirmed; existing held quantity remains allocated. No second deduction. |
| Ready | Approved owning provider, confirmed hold before the deadline | Ready for pickup. |
| Reject | Approved owning provider, pending and unpaid, with a reason | Rejected; existing rejection audit fields, stock restoration, history and traveler notice. |
| Cancel | Owning traveler, approved owning provider, or admin on the detailed route, for an active unpaid hold before its deadline | Cancelled; stock returned once, with reason, actor, status history and notification. |
| Expire | Admin batch or expiry CLI, active unpaid hold at or after its cutoff | Expired; stock returned once, with shared history and traveler notice. |
| Record cash | Approved owning provider, confirmed or ready, positive outstanding cash balance, active hold | Cash receipt recorded once. Reservation stays confirmed or ready. |
| Complete pickup | Approved owning provider, confirmed or ready, on the selected Sri Lanka pickup day | Completed; stock stays sold. Detailed pickup can record the outstanding cash and complete atomically. Shared completion uses the recorded cash step; zero-price pickups need no receipt. |

Completed, rejected, cancelled, and expired are terminal. Every write reevaluates transition eligibility and payment after obtaining the product lock. Release uses a conditional state change and an atomic stock increment. A missing or mismatched product aborts the transition. Cash, status, stock, history and notices participate in their enclosing transaction. A pre-reference native-only hold can still be cancelled; the reference is created in that transaction. A mismatched or financially ambiguous reference blocks release.

[Shared reservation service](../../app/services/provider_reservations.py) · [Expiry service](../../app/services/artisan_reservation_expiry.py) · [Expiry CLI](../../scripts/expire_artisan_reservations.py)

## API inventory for Phase 02

All paths below use the `/api/v1` prefix. Existing operation paths and existing payload shapes remain available. The detailed reservation response adds nullable `payment_status` and exposes the effective `expires_at`. The provider booking queue's action hints reflect the pickup rules. Legacy and detailed adapters retain their existing error-code conventions for conflicts such as insufficient stock and invalid transitions.

| Method and path | Role | Contract |
| --- | --- | --- |
| POST `/artisan-reservations` | Traveler | Legacy `item_id` or detailed `showcase_item_id`, with pickup date and quantity. The detailed shape supports notes. |
| GET `/artisan-reservations/me` | Traveler | Own detailed holds with native status, effective expiry, and payment status. |
| GET `/artisan-reservations/{id}` | Owning traveler, approved owning provider, or admin | Detailed read with reservation and payment state. |
| GET `/artisan-reservations/provider` | Approved Artisan provider | Own holds, optionally filtered by listing/status. |
| PATCH `/artisan-reservations/{id}/confirm` | Approved owning provider | Manual acceptance. |
| PATCH `/artisan-reservations/{id}/ready` | Approved owning provider | Confirmed-to-ready transition. |
| POST `/artisan-reservations/{id}/reject` | Approved owning provider | Pending unpaid rejection with reason and audit. |
| POST `/artisan-reservations/{id}/cancel` | Owning traveler, approved owning provider, or admin | Cancel an eligible unpaid hold. |
| POST `/artisan-reservations/{id}/complete-pickup` | Approved owning provider | Pickup-day completion, with atomic cash recording when needed. |
| GET `/reservations/artisan_reservation/me` | Traveler | Own legacy reservation summaries with shared payment status. |
| POST `/reservations/artisan_reservation/{booking_id}/cancel` | Owning traveler | Same eligibility and stock release as detailed cancellation. |
| GET `/provider/bookings`; GET `/provider/bookings/{booking_id}` | Approved owning provider | Shared queue and detail, including allowed action hints. |
| POST `/provider/bookings/{booking_id}/action` | Approved owning provider | Artisan `accept`, `ready`, `reject`, `cancel`, `cash`, or `complete`. |
| POST `/admin/artisan/reservations/process-expired` | Admin | Batch expiry using the common policy. |

The inventory above describes Phase 02 routes and shared dependencies. Other operations remain covered by the [complete audit](ARTISAN_COMPLETE_DEVELOPMENT_AUDIT_2026-11-20.md). [Dedicated router](../../app/routers/artisan_reservations.py) · [Shared router](../../app/routers/provider_reservations.py)

## Migration inventory

| Range or version | Phase 02 status |
| --- | --- |
| 001–023 | Existing repository and guarded integration history retained. |
| 024 | Existing stock nonnegative constraint and Artisan reservation persistence used. |
| 025–026 | Existing repository and guarded integration history retained. |
| 027 | Existing booking references, history, payment transactions and notifications used. |
| 028–036 | Existing repository and guarded integration history retained. |
| 037 | Existing rejection status check and audit fields used. |
| 038 | Existing favorites table and uniqueness retained. |
| 039 | Next available number. Phase 02 required no migration. |

The integration write-test fixture checked filenames and checksums **001–038** before service writes. No original migration was modified and no migration ran in Phase 02.
