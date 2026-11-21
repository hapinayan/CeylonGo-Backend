# Phase 4 Proposal: Shared PayHere Integration (`app/routers/payments.py`) & Migration 034

**Status:** Proposal Only — Awaiting Explicit Approval  
**Branch:** `feature/tour-guide-backend-complete`  
**Shared Files Modified So Far:** None (`app/routers/payments.py` and `migrations/001`–`033` are completely untouched)

---

## 1. Migration Version 034 Availability Check

- **Current repository state:** `migrations/001_create_password_resets.sql` through `migrations/033_extend_rental_module_final_gaps.sql`.
- **All local and remote-tracking refs checked:** `develop`, `feature/tour-guide-backend-complete`, and all `origin/*` refs contain zero `034_*.sql` files.
- **Result:** Version **`034` is currently available with zero conflicts** in the repository.
- **Drafted SQL files (stored safely outside `migrations/` so `scripts/migrate.py` cannot auto-execute them):**
  - Forward SQL: `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps.sql`
  - Rollback SQL: `docs/tour-guide/PROPOSED_034_extend_tour_guide_module_final_gaps_rollback.sql`

### Important SQL Server Schema Drift Advisory for DB Coordinator
During the shared payment audit of `migrations/027_create_shared_infrastructure.sql` vs `app/routers/payments.py`, we observed two pre-existing CHECK constraint mismatches on shared payment tables that also affect Homestay and Rental on SQL Server:
1. `dbo.payment_transactions` (`CK_payment_transactions_status`): Migration `027` allows `('pending', 'completed', 'failed', 'refunded', 'cancelled')`, whereas `app/routers/payments.py` (lines 208, 749, 1085, 1232, 1641) writes and filters `payment_status = 'paid'`.
2. `dbo.payment_refunds` (`CK_payment_refunds_status`): Migration `027` allows `('pending', 'processed', 'failed', 'rejected')`, whereas `app/routers/payments.py` (lines 1155, 1293, 1649) uses `'approved'` and `'completed'`.

**Recommendation:** Before enabling live SQL Server PayHere callbacks for any module, the DB coordinator should verify live `sys.check_constraints` on `dbo.payment_transactions` and `dbo.payment_refunds` (and align them in an approved shared migration if needed). We intentionally excluded shared-table alterations from `PROPOSED_034` to keep Migration 034 strictly scoped to Module 05 (`tour_guides`, `tour_guide_packages`, `tour_guide_bookings`).

---

## 2. Safest Integration Architecture (Zero Homestay / Rental Regression Risk)

All Tour Guide payment logic is already isolated and tested in dedicated Module 05 files:
- `app/services/tour_guide_payment.py`
- `app/routers/tour_guide_payments.py`

Because all business rules, constant-time IPN verification, idempotency, late-payment refund flagging, 10% commission calculation, and refund eligibility live inside `app/services/tour_guide_payment.py`, integrating with `app/routers/payments.py` requires **only thin delegation hooks** — zero edits to existing Homestay or Rental branches.

---

## 3. Exact Proposed Changes to `app/routers/payments.py` (Pending Approval)

| # | Function / Location in `app/routers/payments.py` | Current Behavior | Exact Minimal Change Proposed | Risk to Homestay / Rental |
| :-: | --- | --- | --- | --- |
| 1 | **Imports** (lines 22–52) | Imports Homestay (`Booking`, `Homestay`) and Rental (`RentalBooking`, `RentalVehicle`) models. | Import `TourGuideBooking` and thin service callables (`handle_tour_guide_payhere_callback`, `prepare_tour_guide_online_payment`, `sync_tour_guide_refund_completion`, `calculate_tour_guide_provider_earnings`) from `app.services.tour_guide_payment`. | None |
| 2 | **New Section 1C** (after line 573, optional alias) | Has `/bookings/{id}/initiate` (Homestay) and `/rental/bookings/{id}/initiate` (Rental). | Optionally add `POST /api/v1/payments/tour-guides/bookings/{booking_id}/initiate` delegating directly to `prepare_tour_guide_online_payment(db, booking_id=booking_id, current_user=current_user)`. | None |
| 3 | **`handle_payhere_callback(form_data, db)`** (lines 636–680) | Handles `module_type == "rental_vehicle"` and `"homestay"`, rejects all other `module_type` values with HTTP 400. | Add one branch right before `else:` — `elif booking_ref.module_type == "tour_guide": return handle_tour_guide_payhere_callback(db=db, form_data=dict(form_data), booking_ref=booking_ref, verify_signature=True)`. | None (Homestay & Rental branches untouched) |
| 4 | **`process_refund(refund_id, payload, admin, db)`** (lines 1142–1197) | Updates `PaymentRefund.refund_status` and notifies traveler, but does not sync `TourGuideBooking.payment_status` to `'refunded'`. | When `payload.status == "completed"` and `tx` exists, load `BookingReference`; if `booking_ref.module_type == "tour_guide"`, call `sync_tour_guide_refund_completion(db, booking_ref=booking_ref, refund=refund, admin_id=admin.id)`. | None (guarded by `module_type == "tour_guide"`) |
| 5 | **`calculate_provider_earnings_balances(db, provider_profile_id, period_start, period_end)`** (lines 1204–1352) | Queries only `BookingReference.module_type == "homestay"` joined to `Booking`. Called by `create_provider_settlement` and `admin_get_financial_summary`. | Add additive Tour Guide online contribution via `calculate_tour_guide_provider_earnings(...)` when `provider_profile.provider_type == "tour_guide"` (or unioning `tour_guide` online transactions for multi-service providers) so admin settlements and `total_provider_liabilities` include Tour Guide providers without changing Homestay numbers. | Minimal — verified by running `test_homestay_phase10_commission_settlements.py` |
| 6 | **`admin_get_reconciliation_queue(admin, db)`** (lines 1696–1737) | Step 2 joins only Homestay `Booking` to detect paid transactions on `expired`/`cancelled`/`rejected` bookings. | Add a parallel query joining `TourGuideBooking` for `BookingReference.module_type == "tour_guide"` with `PaymentTransaction.payment_status == "paid"` and `TourGuideBooking.booking_status.in_(["expired", "cancelled", "rejected"])`, adding their IDs to `flagged_tx_ids`. | None (additive set union) |

### Functions Confirmed Requiring **ZERO** Changes
- `initiate_booking_payment` (Homestay) — untouched
- `handle_advertising_payment_callback` — untouched
- `initiate_rental_booking_payment` (Rental) — untouched
- `payhere_webhook_listener` (`/payhere/notify`) — untouched (already delegates to `handle_payhere_callback`)
- `get_booking_payment_status` / `request_booking_refund` (Homestay) — untouched (Tour Guide uses its own routes in `app/routers/tour_guide_payments.py`)
- `list_all_payment_transactions`, `list_all_refunds`, `admin_get_financial_summary` (gross/commission/refund totals already aggregate across all `PaymentTransaction`/`PaymentRefund` rows)

---

## 4. Approval Checklist Before Next Step

Please confirm which items you approve:
1. Approve moving `PROPOSED_034` forward and rollback SQL into `migrations/` and `migrations/rollback/` (and whether to apply to dev DB).
2. Approve applying the 6 minimal hooks in `app/routers/payments.py` listed above.
3. Confirm whether the DB coordinator wants a separate shared-table CHECK constraint alignment check for `CK_payment_transactions_status` / `CK_payment_refunds_status`.
