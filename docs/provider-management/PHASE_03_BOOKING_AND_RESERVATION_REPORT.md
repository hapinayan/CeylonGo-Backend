# Phase 03 Booking and Reservation Report

Phase 03 extends the approved provider workspace with native booking queues, activates Guide and Artisan reservations using existing tables, and corrects Homestay transaction writes. Eco-Tourism remains blocked on a native reservation model. The working branch is `feature/provider-management`.

## Pre-development audit

The Phase 01 and Phase 02 reports and the 42-requirement tracker identified gaps in Homestay receipts, refunds and reviews. The implementation checked booking routers, SQLAlchemy mappings and the corresponding applied migration files. Homestay uses its native bookings and shared reference, payments, refunds, settlement and review tables. Activity owns slots and transactional capacity. Rental owns vehicle booking intervals and existing confirmation and completion actions. Guide has packages, availability and bookings in migration 022. The Artisan reservation model had drifted from migration 024 and needed an ORM mapping correction. Eco season restrictions and safety records exist, but Activity bookings still require an Activity-owned slot through a non-null foreign key, even when an optional Eco reference is present.

| Category | Phase 03 behavior |
| --- | --- |
| Homestay | Existing APIs, verified online receipts, cash receipts, locked refund decisions, review fixes, joined workspace history |
| Culture, Adventure and Activities | Existing auto-confirmed traveler flow and transactional capacity, owned provider cancellation and completion, new workspace reads and shared history |
| Rental | Existing traveler and provider flow, new workspace reads and shared history for newly written events |
| Local Tour Guide | Public packages and calendar windows, same-day traveler requests, group validation, accept/reject/cancel, cash confirmation, completion and history |
| Artisan and Small Business | Product pickup requests, quantity and stock reservation, accept/reject/cancel, ready-for-pickup, cash confirmation, completion and history |
| Eco-Tourism | Booking operations report unavailable. Existing listings and sponsored campaign metrics continue |

## New endpoints

| Method | Endpoint | Authorization and purpose |
| --- | --- | --- |
| GET | `/api/v1/provider/bookings` | Approved provider, native category queue with bounded pagination, native or computed state, UTC creation dates and listing title or booking ID search |
| GET | `/api/v1/provider/bookings/{booking_id}` | Approved provider, own booking details and paginated, sanitized recorded status history |
| POST | `/api/v1/provider/bookings/{booking_id}/action` | Approved Guide, Artisan or Activity provider, owned reservation action |
| GET | `/api/v1/tour-guide-reservations/guides/{guide_id}/packages` | Public, paginated active packages on approved and active guides |
| GET | `/api/v1/tour-guide-reservations/guides/{guide_id}/availability` | Public, bounded Sri Lankan local date, published schedule windows |
| POST | `/api/v1/tour-guide-reservations` | Traveler, package request with timezone-aware start and group size |
| POST | `/api/v1/artisan-reservations` | Traveler, product quantity and pickup date |
| GET | `/api/v1/reservations/{module}/me` | Traveler, paginated own Guide or Artisan requests |
| POST | `/api/v1/reservations/{module}/{booking_id}/cancel` | Traveler, own eligible unpaid request |

The `module` parameter is `tour_guide` or `artisan_reservation`. A Guide request uses `guide_id`, `package_id`, `people` and `starts_at`. An Artisan request uses `item_id`, `quantity` and `pickup_date`. A provider action uses `action` with one of `accept`, `reject`, `cancel`, `ready`, `cash` or `complete`, and an optional `reason`. Native booking IDs are scoped by the authenticated provider category. The queue and detail endpoints omit personal customer data, free-text history notes and payment credentials. The date filter applies to booking creation dates. Scheduled availability is advisory until the reservation transaction revalidates it.

## State and money rules

- Guide requests require a currently active package, a same-day availability window without a conflicting blackout, and a group within the Guide and package limits. Requests hold the interval until they are resolved. Provider rejection releases the interval. Traveler cancellation of a confirmed Guide booking uses the stored free-cancellation cutoff. The existing schema supplies the meeting point, price and cancellation snapshot.
- Artisan requests use reserve-and-pay-at-shop. The server prices the quantity from the product and atomically deducts stock from available inventory. Unpaid cancellation or rejection restores stock. The existing schema stores the pickup date. A provider can mark a confirmed order ready, confirm cash received and mark pickup complete once the pickup date is reached. There is no online checkout.
- Activity keeps auto-confirmed traveler bookings. A provider can cancel an owned unpaid pay-at-location booking before the slot starts or complete a confirmed booking once the slot ends. Cancellation changes booking state and returns capacity in the same transaction.
- Guide and Artisan cash receipts are kept apart from booking states and grouped by currency in the dashboard. Unsupported online revenue and settlement metrics remain unavailable for these categories. The dashboard counts native rows once. The shared reference links audit, payment and notification data.
- Homestay cash confirmation uses the positive outstanding balance from captured cash receipts and permits pay-at-location bookings only. Verified callbacks validate the merchant, signature, booking reference, buyer, amount and currency. SQL Server serializes callbacks and booking changes. A late capture flags refund reconciliation without reopening the reservation. SQL Server uses the installed ledger status values `completed` and `processed` for receipts and executed refunds. Existing API payment and refund statuses are translated where necessary.
- Homestay refunds reserve one refundable balance. Final administrator decisions require an external execution reference to be recorded as completed. Refund status is synchronized back to the booking and shared reference, with audit and notification records. No bank or gateway refund is initiated by this API.
- Provider settlement requests serialize overlapping decisions. The settlement API calculates LKR balances from verified online receipts on owned bookings. It excludes cash, other currencies and cross-provider ledger links. Paid settlements require an administrator-supplied external payout reference. Automatic transfer and independent payout evidence verification are outside this implementation.
- Homestay reviews require an owned completed booking. The booking lock protects duplicate review checks and the writer supplies the timestamp required by SQL Server.

Existing Homestay booking, payment, refund, settlement and review routes retain their paths. Existing Activity and Rental booking routes retain their paths. New writes create status history and notifications within the booking transaction. Dashboard recent activity skips the duplicate initial history event and omits notes.

### Existing endpoints affected

All paths in this table are relative to `/api/v1`.

| Method | Paths | Change |
| --- | --- | --- |
| POST | `/bookings`, `/bookings/{booking_id}/accept`, `/bookings/{booking_id}/reject`, `/bookings/{booking_id}/complete`, `/bookings/{booking_id}/cancel`, `/bookings/{booking_id}/record-cash-payment` | Shared booking transaction lock, cancellation authorization and receipt matching, and cash balance and audit corrections |
| POST | `/payments/bookings/{booking_id}/initiate`, `/payments/payhere/notify`, `/payments/bookings/{booking_id}/request-refund`, `/payments/admin/refunds/{refund_id}/process`, `/payments/admin/settlements` | Capture, refund and payout synchronization and audit checks |
| GET | `/payments/bookings/{booking_id}/status`, `/payments/provider/earnings`, `/payments/admin/refunds` | Existing authorization, ledger and status response corrections |
| POST | `/homestays/{homestay_id}/reviews` | Completed booking eligibility, concurrency and mapped review timestamp |
| POST | `/activity-bookings`, `/activity-bookings/{booking_id}/cancel`, `/rental-bookings` | Native booking events and notifications |
| PATCH | `/rental-bookings/{booking_id}/cancel`, `/rental-bookings/{booking_id}/status` | Native status history and notifications |
| GET | `/provider/workspace`, `/provider/dashboard`, `/provider/dashboard/analytics`, `/provider/dashboard/recent-activity` | Category booking capabilities, native metrics, cash receipts and lifecycle events |

## Database and remaining work

The implementation reuses the existing 88-table baseline. No schema or migration changes were applied. Primary `CeylonGoDB` was outside verification.

The standalone Eco reservation workflow requires approval for a new native reservation model, or an approved redesign of Activity slots with mutually exclusive Activity and Eco ownership. It needs unambiguous listing ownership, availability and capacity enforcement, status and payment fields, and a shared reference module discriminator backed by the installed CHECK constraint. The optional Eco foreign key on an Activity booking is insufficient. An approved design must also specify effective seasonal and environmental restrictions for reservation dates.

Migration 027 has no separate `approved` refund ledger state. The administrator can record rejection or externally confirmed execution, while the standalone approval decision returns a conflict on SQL Server. Independent external refund and payout checks, chargeback and independently charged duplicate capture reconciliation, and Guide online checkout are open. Guide packages and availability are read from the existing inventory. Provider inventory-management CRUD, multi-day Guide requests, exact time-of-day Artisan pickup, reservation expiry automation, and scheduled reminders remain open. Older Activity and Rental bookings show only previously recorded history. Complete lifecycle parity across all six categories is pending.

## Verification

The guarded SQL Server run on `CeylonGoDB_IntegrationTest` passed **14 tests**. Six Phase 03 cases exercised Artisan stock and cash, Guide availability and overlaps, Homestay cash and review writes, Activity cancellation and capacity release, Homestay callback replay and refund ledger synchronization, and a two-connection application-lock conflict and release. Eight Phase 02 SQL Server regressions verified the dashboard and fixture rollback. The refund and capture cases used fixture payment verification. They made no gateway or bank calls. The SQL suites opt in with `PROVIDER_PHASE03_SQLSERVER=1` and `PROVIDER_WORKSPACE_SQLSERVER=1` and assert the actual database identity before fixture writes.

The four Phase 01 SQL Server regressions also passed on the isolated database. Two of the Phase 03 SQL Server cases were rerun successfully with added assertions for LKR ledger totals and cash exclusion. The bounded local suite passed **306 tests**, including 12 Phase 03 tests, the Phase 01 and Phase 02 local regressions, and the affected Homestay payment, expiration and workflow suites. A targeted financial and Phase 03 selection reran 40 of those tests successfully. The settlement workflow regression verifies that an unrelated provider's reference cannot make a booking eligible for payout.

The [development tracker](PROVIDER_DEVELOPMENT_TRACKER.md) records the status of all 42 requirements.

## Phase 03 files

Created `app/core/booking_transactions.py`, `app/routers/provider_reservations.py`, `app/services/provider_reservations.py`, `tests/test_provider_phase03.py`, `tests/test_provider_phase03_sqlserver.py` and this report.

Modified `app/main.py`, `app/core/listing_sync.py`, `app/models/artisan.py`, `app/routers/activity_bookings.py`, `app/routers/booking.py`, `app/routers/homestay_reviews.py`, `app/routers/payments.py`, `app/routers/rental_bookings.py`, `app/schemas/payment.py`, `app/schemas/provider_workspace.py`, `app/services/provider_workspace.py`, `tests/test_homestay_phase10_commission_settlements.py`, `tests/test_homestay_phase11_e2e_integration.py`, `tests/test_provider_workspace.py` and `docs/provider-management/PROVIDER_DEVELOPMENT_TRACKER.md`. This list covers Phase 03 work. The branch retains unstaged changes from prior phases.
