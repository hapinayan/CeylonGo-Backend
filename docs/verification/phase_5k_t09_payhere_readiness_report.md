# CeylonGo Module 04 — Phase 5K: T09 PayHere Readiness Audit Report

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Repository**: `CeylonGo-Backend` (`develop` branch)  
**Database**: Microsoft SQL Server 2025 (`CeylonGoDB`)  
**Scope**: Read-Only Technical Audit of T09 — Eco-Tourism Online Payment via PayHere  
**Final Status**: **BLOCKED**  

---

## 1. Existing PayHere Infrastructure Findings

1. **`app/core/payhere.py`**: Fully operational `PayHereService` with MD5 checkout hash generation (`generate_checkout_hash`) and IPN verification (`verify_ipn_signature`). Supports environment variables `PAYHERE_MERCHANT_ID`, `PAYHERE_MERCHANT_SECRET`, `PAYHERE_MODE`, and endpoint URLs.
2. **`app/routers/payments.py`**: Fully mounted at `/api/v1/payments`, but hardcoded exclusively to Module 02 Homestays (`db.query(Booking)`). Lacks support for `ActivityBooking` or Eco-Tourism bookings.
3. **`app/models/shared.py`**: Defines `PaymentTransaction`, `PaymentRefund`, `ProviderSettlement`, and `BookingReference`. Fully capable of handling polymorphic modules.
4. **`app/services/eco_booking_service.py`**: Explicitly rejects `payment_method != 'pay_at_location'` with `ECO_ONLINE_PAYMENT_UNSUPPORTED` (lines 181–189).

---

## 2. T09 Payment Lifecycle Readiness (14 Steps)

- **IMPLEMENTED (4 steps)**:
  - Step 2: Server-side Decimal pricing (`calculate_eco_booking_price`).
  - Step 3: Canonical booking reference formatting (`format_eco_booking_reference`).
  - Step 7: PayHere IPN webhook receiver endpoint (`POST /api/v1/payments/payhere/notify`).
  - Step 8: PayHere MD5 signature validation (`payhere_service.verify_ipn_signature`).
- **PARTIAL (4 steps)**:
  - Step 9: Amount and currency verification (implemented for Homestay, fails for Eco).
  - Step 10: Payment transaction recording (implemented for Homestay, fails for Eco).
  - Step 13: Webhook idempotency (checks `paid`, lacks concurrency locking).
  - Step 14: Refund reconciliation (pending records created on weather cancellation, gateway payout manual).
- **MISSING (4 steps)**:
  - Step 4: Initial pending `PaymentTransaction` creation for Eco bookings.
  - Step 5: PayHere checkout initiation endpoint for Eco bookings.
  - Step 11: Atomic update of `ActivityBooking.payment_status` on webhook receipt.
  - Step 12: Notification dispatch to Eco traveler and Eco provider on payment success.
- **BLOCKED (1 step)**:
  - Step 1: Traveler Eco booking creation with `payment_method='online'`.
- **NOT APPLICABLE (1 step)**:
  - Step 6: Traveler user interaction on PayHere hosted page.

---

## 3. Security Vulnerabilities & Missing Controls

1. **Missing Incoming Merchant ID Validation**: `payments.py:181` extracts `merchant_id` from the form but never checks `merchant_id == payhere_service.merchant_id`. An attacker could forge an IPN signed with another merchant ID if secrets collided.
2. **Missing Concurrency Protection in Webhook**: No distributed lock (`sp_getapplock`) is acquired during IPN processing in `payments.py`, creating a race condition window if duplicate webhooks arrive simultaneously.
3. **Raw Form Payload Storage**: `payments.py:259` stores `str(dict(form_data))` without sanitizing customer card tokens or sensitive data.
4. **Strong Invariants Confirmed**: Client return URL (`return_url`) never marks bookings as paid; only server-to-server IPN (`notify_url`) can update payment state.

---

## 4. Payment Database Compatibility

A read-only inspection of SQL Server (`CeylonGoDB`) revealed:
- `dbo.activity_bookings`: Check constraint `CK_activity_bookings_method` physically permits both `'pay_at_location'` and `'online'`. `CK_activity_bookings_payment` permits `'unpaid'`, `'pending'`, `'paid'`, `'refund_pending'`, `'refunded'`, `'failed'`.
- `dbo.booking_references`: Check constraint `CK_booking_references_type` physically permits `'activity'`.
- `dbo.payment_transactions`: Foreign key links to `dbo.booking_references.id`. Check constraint `CK_payment_transactions_status` permits `'pending'`, `'completed'`, `'failed'`, `'cancelled'`, `'refunded'`.
- `dbo.payment_refunds`: Foreign key links to `dbo.payment_transactions.id`.
- **Schema Verdict**: **100% physically compatible. Zero migrations or DDL changes required.**

---

## 5. Refund Lifecycle Readiness

- **Current Behavior**:
  - Traveler cancellation: transitions `payment_status` to `'refund_pending'` if paid.
  - Weather emergency cancellation: batch cancels paid bookings and automatically inserts `PaymentRefund` records with `refund_status='pending'`.
- **Critical Distinction**:
  - `PaymentRefund` with `refund_status='pending'` is an **internal accounting claim**, not money returned to traveler's card.
  - CeylonGo does not integrate PayHere's Automated Refund API; refunds must be executed manually by Admin via `POST /api/v1/payments/admin/refunds/{refund_id}/process` with bank transfer / gateway reference in notes.

---

## 6. Existing Test Coverage

- **Current Coverage**: **ZERO unit or integration tests** exist for PayHere Eco booking checkout initiation or IPN webhook handling.
- **Missing Coverage**: 14 test scenarios specified in `docs/verification/t09_payment_security_and_test_gaps.md` (hash generation, signature validation, merchant ID mismatch, tampered amounts, currency mismatch, duplicate webhook idempotency, concurrency, atomic state transitions).

---

## 7. Exact T09 Blockers

1. **Router Monomorphism**: `app/routers/payments.py` only handles Homestay bookings.
2. **Service Restriction**: `app/services/eco_booking_service.py` rejects `payment_method='online'`.
3. **Missing Eco Initiation Route**: No endpoint exists to generate PayHere checkout parameters for `ActivityBooking`.
4. **Unmounted Router**: `eco_bookings.router` remains unmounted in `app/main.py` under HTTP 503 guard.
5. **Prerequisite Gates**: Gates 1, 2, and 3 must complete before booking routers can be activated.

---

## 8. Files Created

1. **Infrastructure Audit**: [t09_payhere_infrastructure_audit.md](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/docs/verification/t09_payhere_infrastructure_audit.md)
2. **Payment Lifecycle Matrix**: [t09_payment_lifecycle_matrix.md](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/docs/verification/t09_payment_lifecycle_matrix.md)
3. **Security Review & Test Gaps**: [t09_payment_security_and_test_gaps.md](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/docs/verification/t09_payment_security_and_test_gaps.md)
4. **Unblocking Plan**: [t09_payhere_unblocking_plan.md](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/docs/proposals/t09_payhere_unblocking_plan.md)
5. **Readiness Report**: [phase_5k_t09_payhere_readiness_report.md](file:///c:/Users/komaruthan/Desktop/project%203/CeylonGo-Backend/docs/verification/phase_5k_t09_payhere_readiness_report.md)

---

## 9. Tests Actually Executed

- Read-only schema inspection on Microsoft SQL Server (`CeylonGoDB`).
- Static analysis and code inspection of all payment components.
- Regression test suite: **505 passed, 3 skipped, 0 failed** in previous phase; zero changes made to application logic.
- **Zero real or sandbox transactions or API calls executed.**

---

## 10. Recommended Implementation Sequence

1. **Prerequisite Sign-offs**: Complete Gate 1 (ORM model) and Gate 2 (Migration 030).
2. **Refactor Webhook Router**: Refactor `app/routers/payments.py:payhere_webhook_listener` to check `booking_ref.module_type` and handle `ActivityBooking`.
3. **Add Eco Checkout Route**: Add `POST /api/v1/eco-bookings/{id}/initiate-payment` in `eco_bookings.py`.
4. **Enable Service Online Payment**: Update `eco_booking_service.py` to allow `online` payment method.
5. **Implement Unit Test Suite**: Author `tests/test_eco_payhere_integration.py` (14 scenarios with mocks).
6. **Activate via Gate 4**: Mount router upon Project Lead authorization.

---

## 11. Tasks That Can Be Completed Without Team Leader Approval

- Authoring `app/services/eco_payment_service.py` (standalone helper to format PayHere checkout parameters).
- Drafting unit test cases for PayHere hash and signature verification in `tests/test_eco_payhere_integration.py`.
- Documentation and schema mapping verification.

*(Modifying shared `app/routers/payments.py` or lifting `ECO_ONLINE_PAYMENT_UNSUPPORTED` in `eco_booking_service.py` touches shared payment code and requires Team Leader sign-off).*

---

## 12. Final T09 Status

**BLOCKED**

T09 remains **BLOCKED** by architecture decisions restricting payments to `pay_at_location`, hardcoded homestay payment routing, unmounted booking endpoints, and prerequisite governance gates. A complete, safe unblocking roadmap is now formulated.
