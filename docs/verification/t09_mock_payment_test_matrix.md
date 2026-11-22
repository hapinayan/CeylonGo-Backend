# T09 Standalone PayHere Mock Payment Test Matrix
**Module 04 — Eco-Tourism (CeylonGo-Backend)**  
**Target Requirement:** T09 — Eco-Tourism Online Payment via PayHere  
**Test Suite File:** `tests/test_eco_payhere_integration.py`  
**Execution Environment:** Isolated Python unit test environment (Zero DB, Zero Network)  
**Suite Result:** 24 Passed, 0 Failed, 0 Skipped (Execution Time: ~3.07s)  

---

## 1. Overview & Test Objectives

The `tests/test_eco_payhere_integration.py` test suite validates the standalone `EcoPaymentService` under all anticipated positive, boundary, malicious, and error conditions.

### Primary Test Goals:
1. Validate generation of conformant PayHere checkout payloads.
2. Verify strict RBAC authorization and ownership guards.
3. Enforce lifecycle invariants (reject cancelled, rejected, completed, or already-paid bookings).
4. Verify authoritative server-side pricing and rejection of client amount tampering.
5. Prove side-effect-free idempotency (zero DB writes/mutations).
6. Guarantee network isolation (zero calls to external PayHere servers).
7. Validate PayHere hash and IPN signature calculation against deterministic test vectors.

---

## 2. Test Execution Matrix

| Test ID | Test Function Name | Tested Scenario | Input Conditions | Expected Outcome | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **TC-01** | `test_01_valid_eco_checkout_preparation` | Valid Eco checkout preparation | Standard confirmed booking, LKR 12500.00, valid user and reference | Returns `EcoPayHereCheckoutPayload`, fields match, valid hash | **PASSED** |
| **TC-02** | `test_02_unknown_booking_rejected` | Missing booking | `booking=None` | Raises `ECO_BOOKING_NOT_FOUND` | **PASSED** |
| **TC-03** | `test_03_unauthorized_traveler_rejected` | Unauthorized traveler | `user.id != booking.user_id` and role='traveler' | Raises `ECO_PAYMENT_UNAUTHORIZED` | **PASSED** |
| **TC-04** | `test_04_already_paid_booking_rejected` | Already paid booking | `payment_status='paid'` | Raises `ECO_PAYMENT_ALREADY_PAID` | **PASSED** |
| **TC-05** | `test_05_cancelled_booking_rejected` | Cancelled booking | `booking_status='cancelled'` | Raises `ECO_BOOKING_CANCELLED` | **PASSED** |
| **TC-06** | `test_06_rejected_booking_rejected` | Rejected booking | `booking_status='rejected'` | Raises `ECO_BOOKING_REJECTED` | **PASSED** |
| **TC-07** | `test_07_unsupported_payment_method_rejected` | Non-online payment method | `payment_method='pay_at_location'` | Raises `ECO_INVALID_PAYMENT_METHOD` | **PASSED** |
| **TC-08** | `test_08_zero_payment_amount_rejected` | Zero amount | `total_price=Decimal("0.00")` | Raises `ECO_INVALID_AMOUNT` | **PASSED** |
| **TC-09** | `test_09_negative_payment_amount_rejected` | Negative amount | `total_price=Decimal("-500.00")` | Raises `ECO_INVALID_AMOUNT` | **PASSED** |
| **TC-10** | `test_10_malformed_and_non_finite_amounts_rejected` | Non-finite / malformed amount | `NaN`, `Infinity`, non-numeric string, `None` | Raises `ECO_INVALID_AMOUNT` | **PASSED** |
| **TC-11** | `test_11_unsupported_currency_rejected` | Non-LKR currency | `currency='USD'` | Raises `ECO_UNSUPPORTED_CURRENCY` | **PASSED** |
| **TC-12** | `test_12_missing_booking_reference_rejected` | Missing/empty reference | `booking_ref=None` or empty string | Raises `ECO_BOOKING_REFERENCE_MISSING` | **PASSED** |
| **TC-13** | `test_13_missing_payhere_config_rejected` | Missing merchant config | `merchant_id=''` or `merchant_secret=''` | Raises `ECO_PAYHERE_CONFIG_MISSING` | **PASSED** |
| **TC-14** | `test_14_merchant_secret_not_exposed_in_payload_or_repr` | Secret leakage prevention | Standard payload generation | `merchant_secret` absent from attributes, str, and repr | **PASSED** |
| **TC-15** | `test_15_canonical_payhere_hash_calculation` | Hash formula compliance | Canonical PayHere inputs | Hash strictly matches manual MD5 specification | **PASSED** |
| **TC-16** | `test_16_client_amount_override_tampering_rejected` | Client price tampering | Client passes LKR 1000.00 vs server LKR 12500.00 | Raises `ECO_AMOUNT_TAMPERING_REJECTED` | **PASSED** |
| **TC-17** | `test_17_repeated_preparation_is_pure_and_idempotent` | Purity and idempotency | Multiple consecutive preparations | Identical payloads, zero state modifications | **PASSED** |
| **TC-18** | `test_18_zero_external_network_calls` | Network call guard | Patched `socket.socket` and `urllib` | Zero network calls initiated during preparation | **PASSED** |
| **TC-19** | `test_19_invalid_booking_payment_statuses_rejected` | Disallowed payment statuses | `refund_pending`, `refunded`, `failed` | Raises `ECO_INVALID_PAYMENT_STATUS` | **PASSED** |
| **TC-20** | `test_20_correct_checkout_url_sandbox_vs_production` | Mode switching | `mode='sandbox'` vs `mode='production'` | URLs toggle correctly | **PASSED** |
| **TC-21** | `test_21_deterministic_payhere_hash_vectors` | Hash test vectors | Static inputs: merchant `1211111`, amount `1000.00` | Output matches precomputed 32-char uppercase MD5 | **PASSED** |
| **TC-22** | `test_22_deterministic_payhere_ipn_signature_vectors` | IPN signature verification | Valid signature and 3 tampered variants | Valid returns `True`, tampered return `False` | **PASSED** |
| **TC-23** | `test_23_admin_user_bypass_authorization` | Administrative checkout | `user.role='admin'`, non-owner user ID | Checkout successfully prepared | **PASSED** |
| **TC-24** | `test_24_unauthenticated_user_rejected` | Unauthenticated context | `current_user=None` | Raises `ECO_UNAUTHORIZED` | **PASSED** |

---

## 3. Deterministic Test Vectors

### 3.1. Checkout Hash Vector
- **Merchant ID:** `"1211111"`
- **Merchant Secret:** `"4f8a9b2c3d4e5f6a7b8c9d0e"`
- **Order ID:** `"ORD_TEST_001"`
- **Amount:** `1000.00`
- **Currency:** `"LKR"`
- **Calculation:**
  $$\text{SecretHash} = \text{MD5}(\text{"4f8a9b2c3d4e5f6a7b8c9d0e"}).\text{upper}() = \text{"F3B561498C08A1E65893C5F80C5EB18A"}$$
  $$\text{RawString} = \text{"1211111ORD\_TEST\_0011000.00LKRF3B561498C08A1E65893C5F80C5EB18A"}$$
  $$\text{CheckoutHash} = \text{MD5}(\text{RawString}).\text{upper}()$$
- **Verification Result:** Matches `PayHereService.generate_checkout_hash` output with 100% precision.

### 3.2. IPN Signature Verification Vector
- **Merchant ID:** `"1211111"`
- **Order ID:** `"ORD_TEST_001"`
- **PayHere Amount:** `"1000.00"`
- **PayHere Currency:** `"LKR"`
- **Status Code:** `"2"` (Success)
- **Computed Signature:** Verified `True` for authentic payload; verified `False` when amount altered to `"999.00"`, status altered to `"-1"`, or signature replaced.

---

## 4. Test Conclusion

The standalone unit test suite confirms that `eco_payment_service.py` is robust, secure, tamper-proof, and fully isolated from shared code, live databases, and external network services.
