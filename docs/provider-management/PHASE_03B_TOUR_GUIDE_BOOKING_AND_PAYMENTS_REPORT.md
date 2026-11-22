# Phase 03B Tour Guide Booking and Payments

Tour Guides support same-day and multi-day package bookings, cash at location and PayHere checkout. Phase 03B reuses migration 022's booking, availability and payment columns and the shared ledger and status history. It requires no new database schema.

## API

Paths are relative to `/api/v1`.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/tour-guide-reservations` | Traveler creates a request with `guide_id`, `package_id`, `people`, timezone-aware `starts_at` and optional `payment_method` of `pay_at_location` (default) or `online`. The response includes the calculated end time, total and currency. |
| POST | `/tour-guide-reservations/{booking_id}/payments/initiate` | Owning traveler requests signed checkout fields for an approved, unexpired online hold. A retry uses the same order and deadline. |
| GET | `/tour-guide-reservations/{booking_id}/payments/status` | Owning traveler reads booking and payment status, deadline, receipt ID and capture timestamp when available. |
| POST | `/provider/bookings/{booking_id}/action` | Owning approved provider reviews, records eligible cash, cancels eligible unpaid bookings or completes the tour. Acceptance starts the online hold when payment is required. |
| GET | `/provider/bookings` and `/provider/bookings/{booking_id}` | Existing workspace exposes multi-day times, payment state and deadline. The `awaiting_payment` and `expired` filters support online holds. |
| POST | `/payments/payhere/notify` | Existing signed callback dispatches using the stored booking reference's module. Homestay and advertising keep their existing routes. |

An example reservation request is `{"guide_id":12,"package_id":91,"people":3,"starts_at":"2027-01-20T09:00:00+05:30","payment_method":"online"}`. The API derives the end from the package duration. The server charges the whole-package price once for `per_group` or once per traveler for `per_person`. The booking stores the computed amount and currency. Checkout signs that stored amount. Zero-total bookings can be accepted without checkout.

## Availability and payment states

- Every instant in the half-open booking interval requires availability. The service merges adjacent windows in the Sri Lankan local calendar and rejects overlapping blackouts. Full-day availability covers midnight. A booking ending exactly at midnight does not require coverage on the next day. Stored times are UTC.
- Creation, acceptance, expiry and payment confirmation serialize on the same SQL Server guide lock. Creation checks overlapping pending, confirmed and in-progress bookings under that lock. Provider acceptance and checkout recheck availability.
- A positive online booking remains natively pending after acceptance, with a payment deadline at the earlier of 30 minutes or tour start. The API exposes this as `awaiting_payment`. A failed attempt updates the shared transaction and booking payment states to `failed`. A retry returns them to `pending` and retains the original deadline.
- Before the Phase 03E scheduler, new requests for the same guide release expired checkout holds before checking overlaps. Guide actions, checkout, payment-status reads and callbacks also reconcile expired holds. A successful write persists expiry and history under the guide lock. Workspace reads display elapsed holds as expired even before that write. An expired hold cannot be extended or used to initiate checkout.
- Only a signed callback matching an existing checkout, merchant, reference, amount and currency can capture payment. Capture, booking/reference updates and history commit together. Repeated captures of the same gateway ID are acknowledged once. A late capture or an additional distinct capture creates a pending reconciliation record. An expired or cancelled booking stays closed. The original confirmed reservation and receipt remain intact when an additional charge needs reconciliation.
- Paid bookings cannot use ordinary cancellation. Late and duplicate captures enter the existing admin reconciliation queue. Automated refund execution, chargeback handling and Guide commissions or settlements remain open. Verification uses test fixtures instead of gateway or bank calls.

## Verification

- **68 focused tests passed**, including Guide payment failure and retry without deadline extension, paid cancellation protection, replay, availability boundaries and existing Homestay, advertising and Eco regressions.
- **915 tests passed in the full offline suite, with zero failures and 21 skipped.** All 21 opt-in cases ran separately on isolated SQL Server. This preserves the 908 passing offline tests and adds seven Guide cases.
- **21 SQL Server tests passed** on `CeylonGoDB_IntegrationTest`. The suite preserves the existing 19 cases and adds two Guide cases for concurrent overlap and callback replay, and late capture after the deadline. The race fixture cleaned up its own committed rows. The other test used rollback.
- `git diff --check` passed. No migration files were changed in Phase 03B.

## Phase 03B files

- Created `app/services/guide_booking.py`, `app/services/guide_payments.py`, `app/services/payhere_bookings.py`, `tests/test_guide_phase03b.py`, `tests/test_guide_phase03b_sqlserver.py` and this report.
- Updated `app/routers/payments.py`, `app/routers/provider_reservations.py`, `app/schemas/payment.py`, `app/services/provider_reservations.py`, `app/services/provider_workspace.py` and the [development tracker](PROVIDER_DEVELOPMENT_TRACKER.md).
