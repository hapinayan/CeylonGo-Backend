# CeylonGo Module 03 – Phase 5F-3: C05 Admin Payment Monitoring & Financial Reconciliation

## 1. Executive Summary

Requirement **C05 — Admin Payment Monitoring** provides platform administrators with comprehensive, server-authoritative financial monitoring, transaction auditing, and read-only financial anomaly detection for the Activities & Experiences module.

In this phase (Phase 5F-3), the missing application-layer functionality for C05 was fully implemented and integrated. It builds safely on top of the A09 PayHere payment foundation and B14 Provider Payouts infrastructure without modifying physical database schemas, executing live gateway calls, or mutating persisted data during reconciliation.

> **Status Notice on C05:**
> In accordance with CeylonGo system safety rules, Requirement C05 remains classified as **BLOCKED** at the platform governance level. Full production resolution requires live automated payment gateway reconciliation rails (e.g. automated PayHere settlement and dispute synchronization, verified merchant credentials, and physical settlement ledger tables).

---

## 2. Implemented Endpoints

All endpoints are mounted under the Admin Activities router (`/api/v1/admin/activities`) with strict admin authorization (`require_admin`). Unauthorized callers (e.g. travelers with role `traveler` or activity providers with role `service_provider`) receive `403 Forbidden`.

### 2.1 `GET /api/v1/admin/activities/payments/overview`

Exposes platform-wide activity financial aggregates derived from authoritative transaction, refund, and settlement records.

- **Query Parameters:**
  - `date_from` (`date`, optional): Start date filter (YYYY-MM-DD). Filtered against slot date.
  - `date_to` (`date`, optional): End date filter (YYYY-MM-DD). Filtered against slot date.
  - `provider_profile_id` (`int`, optional): Filter metrics for a specific activity provider.
  - `activity_id` (`int`, optional): Filter metrics for a specific activity.
  - `payment_status` (`str`, optional): Filter by payment transaction status (`paid`, `pending`, `failed`, `refunded`).
- **Validation:**
  - If `date_from > date_to`, returns `422 Unprocessable Content` (`"date_from must not be later than date_to"`).

#### The 14 Authoritative Server-Calculated Metrics

| # | Metric Field | Type | Description & Authoritative Formula |
|---|--------------|------|--------------------------------------|
| 1 | `captured_online_revenue` | `Decimal` | Sum of gross transaction amounts for online payments in `("paid", "completed")`. |
| 2 | `online_transaction_count` | `int` | Total count of captured online transactions. |
| 3 | `pay_at_location_unverified_value` | `Decimal` | Sum of booking totals for bookings marked `pay_at_location`. Strictly segregated from online revenue as unverified on-site cash collections. |
| 4 | `pay_at_location_booking_count` | `int` | Count of `pay_at_location` bookings. |
| 5 | `platform_commission_earned` | `Decimal` | Authoritative platform commission earned from captured transactions (using transaction `commission_amount` or `gross * rate`). |
| 6 | `commission_reversed` | `Decimal` | Platform commission reversed due to completed refunds (`refund_amount * commission_rate`). |
| 7 | `net_platform_commission` | `Decimal` | `max(0.00, platform_commission_earned - commission_reversed)`. |
| 8 | `provider_payable_amount` | `Decimal` | Total earnings owed to providers from completed bookings less provider refund share: `max(0.00, completed_provider_payable - provider_refund_reduction)`. |
| 9 | `total_settled` | `Decimal` | Total disbursements completed and paid out to providers (`settlement_status in ("completed", "paid")`). |
| 10 | `pending_settlement_liability` | `Decimal` | Payouts reserved or pending processing (`settlement_status in ("pending", "approved")`). |
| 11 | `completed_refunds_amount` / `count` | `Decimal` / `int` | Total amount and count of executed refunds (`refund_status in ("processed", "completed")`). |
| 12 | `pending_refund_obligations` / `count` | `Decimal` / `int` | Total amount and count of reserved/pending refund requests awaiting execution (`refund_status in ("pending", "approved")`). |
| 13 | `failed_payment_count` / `pending_payment_count` | `int` | Counts of payment transactions currently in `failed`/`cancelled` or `pending` status. |
| 14 | `net_platform_revenue` & `outstanding_payout_exposure` | `Decimal` | Net platform revenue (`net_platform_commission`) and total unpaid platform payout exposure (`provider_payable_amount - total_settled`). |

---

### 2.2 `GET /api/v1/admin/activities/payments/transactions`

Provides administrators with detailed, paginated payment transaction audit logs.

- **Query Parameters:**
  - `date_from` (`date`, optional): Filter transactions on or after date.
  - `date_to` (`date`, optional): Filter transactions on or before date.
  - `payment_status` (`str`, optional): Filter by payment transaction status.
  - `provider_profile_id` (`int`, optional): Filter by provider profile ID.
  - `activity_id` (`int`, optional): Filter by activity ID.
  - `page` (`int`, default: 1, ge: 1): Page index.
  - `page_size` (`int`, default: 20, ge: 1, le: 100): Page size.
- **Security & Data Sanitization:**
  - Strictly no gateway credentials, tokens, card details, or hash secrets exposed.
  - Includes `transaction_id`, `gateway_transaction_id`, `booking_id`, `booking_reference`, `activity_id`, `activity_title`, `provider_profile_id`, `provider_business_name`, `amount`, `gross_amount`, `commission_rate`, `commission_amount`, `provider_payable_amount`, `refund_status`, `refund_amount`, `paid_at`, and `created_at`.

---

### 2.3 `GET /api/v1/admin/activities/payments/reconciliation`

A completely read-only, offline financial reconciliation and discrepancy detection engine.

- **Guarantees:**
  - **Zero Database Writes:** Never calls `db.commit()` or `db.flush()`. Database record counts remain identical before and after invocation.
  - **Zero Live Gateway Calls:** Operates strictly on persisted ledger data; returns `is_live_gateway_checked: false`.
  - **Target Filtering:** Supports optional scoping via `provider_profile_id` and `activity_id`.

#### Detected Financial Anomalies

| Discrepancy Type | Detection Condition | Classification Reason |
|------------------|---------------------|-----------------------|
| **1. Unmatched Paid Bookings** | Booking has `payment_status == 'paid'`, but no corresponding `PaymentTransaction` with `payment_status in ('paid', 'completed')` exists. | `PAID_BOOKING_WITHOUT_CAPTURED_TRANSACTION` |
| **2. Inconsistent Status** | Payment transaction is `paid`/`completed`, but activity booking is marked `cancelled` or `failed`. | `CAPTURED_PAYMENT_ON_CANCELLED_BOOKING` |
| **3. Duplicate Gateway References** | Multiple payment transactions share the same non-null `gateway_transaction_id`. | `DUPLICATE_GATEWAY_REFERENCE` |
| **4. Amount / Currency Mismatches** | Transaction gross amount or currency differs from activity booking total price or currency. | `AMOUNT_OR_CURRENCY_MISMATCH` |
| **5. Unreviewed Refund Requests** | Refund records remaining in unexecuted states (`pending`, `approved`, `in_review`, `processing`). | `REFUND_AWAITING_EXECUTION` |
| **6. Excessive Settlements** | Total settled and pending commitments to a provider exceed their eligible net earnings (`outstanding_liability > 0.00`). | `SETTLEMENT_EXCEEDS_ELIGIBLE_EARNINGS` |
| **7. Pending Gateway Verifications** | Transaction has `payment_status == 'pending'` and age > 30 minutes. | Tagged with `status: EXTERNAL_VERIFICATION_PENDING` and `reason: PENDING_TRANSACTION_REQUIRING_GATEWAY_RECONCILIATION`. |

---

## 3. Strict Scope & Safety Boundaries

1. **Physical SQL Server Schema Intact:**
   - No migrations or table structure changes were introduced or assumed.
   - All calculations use existing columns in `booking_references`, `payment_transactions`, `payment_refunds`, and `provider_settlements`.
2. **Tenant & Cross-Module Isolation:**
   - All queries explicitly enforce `BookingReference.module_type == 'activity'`.
   - Tour guide bookings (Module 04) and homestay bookings (Module 05) are strictly excluded from activity payment totals and reconciliation checks.
3. **Pay-at-Location Cash Segregation:**
   - On-site cash collections are never treated as captured revenue. They are segregated into separate informational fields (`pay_at_location_unverified_value`, `pay_at_location_booking_count`).
4. **Offline Operation:**
   - No live PayHere API requests, bank payouts, or external email/SMS notifications are executed.

---

## 4. Verification and Test Results

The implementation was validated using targeted unit and integration tests, as well as the canonical regression suites:

- **Targeted C05 Tests:** `tests/test_admin_activity_payments_c05.py`
  - 8 passed in 10.03s (RBAC guardrails, 14 financial metrics calculation, cash segregation, date filtering/validation, pagination, cross-module isolation, 7-anomaly reconciliation detection, and strict read-only safety).
- **Payment & Payout Regressions:**
  - `tests/test_activity_payhere_payments.py` (A09): Passed.
  - `tests/test_activity_provider_payouts_b14.py` (B14): Passed.
  - `tests/test_tour_guide_phase4_shared_payments.py` (Module 04): Passed.
  - `tests/test_staging_safety.py`: Passed.
  - Total: 88 passed in 17.16s.
- **Module 03 Canonical Regression Suite:** 17 test files, 311 passed in 80.56s. Zero failures.
- **Module 04 / Module 05 Shared Payments & Settlement Suite:** 83 passed in 17.98s. Zero failures.

---

## 5. Canonical 48-Requirement Scorecard

```text
================================================================================
                    CEYLONGO MODULE 03 REQUIREMENT SCORECARD
================================================================================
Total Requirements: 48
COMPLETE:           41  (Includes C06 Admin Promotion Management)
PARTIAL:             4  (A12, A16, B15, B16)
MISSING:             0
BLOCKED:             3  (A09, B14, C05)
================================================================================

Canonical PARTIAL Requirements:
- A12: Automatic Card Refunds       --> PARTIAL (Policy cutoff & cash cancel active; live gateway card refund pending)
- A16: Notification Delivery        --> PARTIAL (Safe mock dispatch active; live external SMTP/SMS blocked)
- B15: Discount Packages            --> PARTIAL (Application validation active; DB migration 030 pending)
- B16: Sponsored Campaigns          --> PARTIAL (Application validation active; DB migration 041 pending)

Canonical BLOCKED Requirements:
- A09: PayHere Payment Integration  --> BLOCKED (Live merchant credentials & live IPN webhook ingress)
- B14: Provider Payouts             --> BLOCKED (Live banking CEFTS/SLIPS disbursement rail)
- C05: Admin Payment Monitoring     --> BLOCKED (Live automated gateway reconciliation rails)

Canonical COMPLETE Status (Selected Milestone Requirements):
- C06: Admin Promotion Management   --> COMPLETE (Moderation router, review workflows, audit logging committed in Phase 5E-1)
- C07: Platform Activity Reporting  --> COMPLETE (Executive reporting committed in Phase 5D-2A)
================================================================================
```
