# T09: Payment Security Review & Automated Test Coverage Audit

**Document Version**: 1.0  
**Date**: 20 November 2026  
**Project**: `CeylonGo-Backend` (`develop` branch)  
**Requirement**: **T09 — Eco-Tourism Online Payment via PayHere**  
**Audit Scope**: Gateway Cryptographic Security, Webhook Vulnerabilities, and Test Suite Gap Analysis  

---

## 1. PayHere Security Review (14 Core Dimensions)

A static security audit of `app/core/payhere.py` and `app/routers/payments.py` evaluated the implementation against standard e-commerce and financial gateway security controls:

| # | Security Dimension | Current Implementation | Risk / Vulnerability | Required Remediation |
|:---:|---|---|---|---|
| **1** | **Merchant ID Configuration** | Loaded via `os.getenv("PAYHERE_MERCHANT_ID")` in `PayHereService.__init__`. Defaults to `"TEST_MERCHANT_ID"`. | Low | Ensure production environment mandates non-empty, valid merchant ID. |
| **2** | **Merchant Secret Handling** | Loaded via `os.getenv("PAYHERE_MERCHANT_SECRET")`. Hashed with MD5 in memory. | Low | Never log or expose raw secret. Use secure secret management in production. |
| **3** | **Checkout Hash Generation** | Generates uppercase MD5 hash using canonical PayHere formula: `MD5(merchant_id + order_id + amount + currency + MD5(merchant_secret))`. | None | Correctly implemented in `payhere_service.generate_checkout_hash()`. |
| **4** | **Callback Signature Verification** | Validates incoming IPN signature against: `MD5(merchant_id + order_id + amount + currency + status_code + MD5(merchant_secret))`. | None | Correctly implemented in `payhere_service.verify_ipn_signature()`. |
| **5** | **Server-Side Amount & Currency Check** | Verifies `Decimal(payhere_amount) == booking.total_price` and `payhere_currency == booking.currency`. | Medium | Currently only verifies against Homestay `Booking`. Must be polymorphic to verify `ActivityBooking`. |
| **6** | **Merchant Identity Validation** | Form parameter `merchant_id` is extracted in `payments.py:181`. | **High (Vulnerability)** | `payments.py` does **NOT** verify that the incoming `merchant_id` matches `payhere_service.merchant_id`! An IPN signed with another merchant's account could spoof notifications if the secret matched. Must add `if merchant_id != payhere_service.merchant_id: raise HTTPException(400)`. |
| **7** | **Order ID Validation** | Looks up `dbo.booking_references` by `booking_reference == order_id`. | None | Correctly prevents processing arbitrary unseeded order IDs. |
| **8** | **Status Code Mapping** | Maps PayHere status codes: `"2"` (Success), `"0"` (Pending), `"-1"` (Canceled), `"-2"` (Failed). | Low | Status mapping is correct, but transition to `payment_transactions` must match SQL Server check constraint (`completed`). |
| **9** | **Replay & Duplicate Protection** | Checks `if booking.payment_status == 'paid': return {"status": "already_processed"}`. | Medium | Vulnerable to race conditions under concurrent webhook delivery without database locks. |
| **10** | **Transaction Isolation** | No lock (`sp_getapplock`) acquired during webhook execution in `payments.py`. | **High (Concurrency)** | Must acquire `sp_getapplock` on `booking_reference` or `activity_slot` during webhook processing to guarantee serial execution. |
| **11** | **Sensitive Credential Exposure** | `payments.py:259` executes `tx.gateway_response_payload = str(dict(form_data))`. | **Medium (Data Privacy)** | Storing raw form data might capture customer card tokens or sensitive PII. Payload must be sanitized prior to database persistence. |
| **12** | **Secure Logging** | No credentials logged in `payhere.py`. | None | Preserved. |
| **13** | **Failure & Rollback Behavior** | FastAPI/SQLAlchemy roll back uncommitted transactions on exception. | Low | Must use explicit `savepoint` or atomic nested transaction block for multi-table updates. |
| **14** | **Client-Side Spoofing Protection** | Payment status is **never** updated via client return redirect (`return_url`). Only updated via server-to-server IPN (`notify_url`). | None | Strong security design: client cannot self-mark bookings as paid. |

---

## 2. Automated Test Coverage Audit (Existing vs Missing Tests)

An audit of the `tests/` directory revealed **ZERO automated test coverage** for PayHere online payments on Eco-Tourism bookings.

### Test Coverage Matrix (14 Focus Scenarios)

| Scenario # | Test Scenario Description | Existing Test File | Current Status | Priority | Dependency |
|:---:|---|:---:|:---:|:---:|:---:|
| **1** | Eco online payment checkout initiation | None | **MISSING** | **High** | Gate 4 (Router) |
| **2** | PayHere checkout MD5 hash generation | None | **MISSING** | **High** | Unit / Mock |
| **3** | Valid PayHere IPN webhook signature verification | None | **MISSING** | **High** | Unit / Mock |
| **4** | Tampered IPN webhook signature rejection | None | **MISSING** | **High** | Unit / Mock |
| **5** | Mismatched merchant ID rejection in webhook | None | **MISSING** | **High** | Unit / Mock |
| **6** | Amount tampering rejection (paid < total_price) | None | **MISSING** | **High** | Unit / Mock |
| **7** | Currency tampering rejection (USD instead of LKR) | None | **MISSING** | **High** | Unit / Mock |
| **8** | Unknown / non-existent order ID rejection | None | **MISSING** | **Medium** | Unit / Mock |
| **9** | Idempotent handling of duplicate webhook delivery | None | **MISSING** | **High** | Unit / Mock |
| **10** | Concurrent webhook delivery race condition (`sp_getapplock`) | None | **MISSING** | **High** | Gate 3 (SQL Server) |
| **11** | Payment failure status handling (status_code="-2") | None | **MISSING** | **Medium** | Unit / Mock |
| **12** | Atomic state update across `ActivityBooking` & `PaymentTransaction` | None | **MISSING** | **High** | Gate 3 (SQL Server) |
| **13** | Traveler & provider notification dispatch on payment success | None | **MISSING** | **Medium** | T16 Notifications |
| **14** | Refund record generation and authorization bounds | `test_eco_phase_5d_weather_cancellation.py` | **PARTIAL** (Emergency only) | **Medium** | Gate 1 / Gate 2 |

---

## 3. Required Security Test Suite Design

To safely activate T09, a dedicated test file must be authored:
`tests/test_eco_payhere_integration.py`

This suite must use mock IPN payloads and isolated fixtures to verify:
1. `test_payhere_checkout_hash_generation`: Validates MD5 hash matches PayHere specification vectors.
2. `test_payhere_ipn_signature_verification_success_and_tamper`: Validates that modifying any of `order_id`, `amount`, `currency`, or `status_code` invalidates the signature.
3. `test_payhere_ipn_rejects_foreign_merchant_id`: Validates rejection if `merchant_id != configured_merchant_id`.
4. `test_payhere_ipn_amount_currency_mismatch_fails`: Validates HTTP 400 when amount or currency does not match `ActivityBooking`.
5. `test_payhere_ipn_updates_activity_booking_atomically`: Validates `ActivityBooking.payment_status` becomes `'paid'` and `PaymentTransaction.payment_status` becomes `'completed'`.
6. `test_payhere_ipn_duplicate_delivery_idempotent`: Validates that second delivery returns `status='already_processed'` with zero database duplicate rows.
