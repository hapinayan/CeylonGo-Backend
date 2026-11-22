# CeylonGo Module 04 — Phase 5N Webhook Security Test Matrix
**Target Requirement:** T09 — Eco-Tourism Online Payment via PayHere  
**Test Suite File:** `tests/test_eco_payhere_webhook_security.py`  
**Execution Environment:** Isolated Python in-memory test environment (Zero DB, Zero Network)  
**Total Tests:** **42 Tests**  
**Execution Result:** **42 Passed, 0 Failed, 0 Skipped** (Time: ~0.50s)  

---

## 1. Test Suite Category Summary

| Category | Scope | Test Count | Pass Count | Pass Rate |
| :--- | :--- | :---: | :---: | :---: |
| **Category A** | Merchant ID Validation | 5 | 5 | 100% |
| **Category B** | Cryptographic Signature Validation | 5 | 5 | 100% |
| **Category C** | Order ID & Reference Validation | 3 | 3 | 100% |
| **Category D** | Amount Parsing & Precision Validation | 8 | 8 | 100% |
| **Category E** | Currency Allowlist Validation | 3 | 3 | 100% |
| **Category F** | Payment Status Classification | 5 | 5 | 100% |
| **Category G** | Pure Idempotency Policy Decisions | 5 | 5 | 100% |
| **Category H** | Master Integration & Security Isolation | 6 | 6 | 100% |
| **Category I** | Compatibility & Data Normalization | 2 | 2 | 100% |
| **TOTAL** | **Phase 5N Webhook Security Suite** | **42** | **42** | **100%** |

---

## 2. Exhaustive Test Case Execution Matrix

| Test ID | Function Name | Scenario Description | Inputs / Fixtures | Expected Outcome | Result |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **A-01** | `test_a01_correct_merchant_id` | Matching merchant ID | `merchant_id="1211111"`, `expected="1211111"` | Passes validation without exception | **PASSED** |
| **A-02** | `test_a02_incorrect_merchant_id_rejected` | Mismatched merchant ID | `merchant_id="9999999"`, `expected="1211111"` | Raises `ECO_IPN_MERCHANT_MISMATCH` | **PASSED** |
| **A-03** | `test_a03_missing_merchant_id_rejected` | Missing merchant ID | `merchant_id=None` | Raises `ECO_IPN_MERCHANT_MISMATCH` | **PASSED** |
| **A-04** | `test_a04_empty_merchant_id_rejected` | Whitespace merchant ID | `merchant_id="   "` | Raises `ECO_IPN_MERCHANT_MISMATCH` | **PASSED** |
| **A-05** | `test_a05_server_unconfigured_merchant_id_rejected` | Server config missing ID | `expected=""` | Raises `ECO_IPN_CONFIGURATION_MISSING` | **PASSED** |
| **B-01** | `test_b01_valid_signature_passes` | Valid MD5 signature | Canonical PayHere hash inputs | Passes signature validation | **PASSED** |
| **B-02** | `test_b02_invalid_signature_rejected` | Forged MD5 signature | `md5sig="A" * 32` | Raises `ECO_IPN_INVALID_SIGNATURE` | **PASSED** |
| **B-03** | `test_b03_missing_signature_rejected` | Missing MD5 signature | `md5sig=""` | Raises `ECO_IPN_MISSING_SIGNATURE` | **PASSED** |
| **B-04** | `test_b04_malformed_signature_format_rejected` | Malformed non-hex string | `md5sig="not-a-valid-hex-md5"` | Raises `ECO_IPN_MALFORMED_SIGNATURE` | **PASSED** |
| **B-05** | `test_b05_secret_non_disclosure_in_signature_error` | Secret containment check | Forged signature execution | `merchant_secret` absent in error string & details | **PASSED** |
| **C-01** | `test_c01_correct_order_id` | Matching booking reference | `order_id="BK-ECO-20261120-7A9B"` | Returns `Decimal("12500.00")` | **PASSED** |
| **C-02** | `test_c02_incorrect_order_id_rejected` | Order reference mismatch | `order_id="BK-ECO-WRONG-REF"` | Raises `ECO_IPN_ORDER_MISMATCH` | **PASSED** |
| **C-03** | `test_c03_missing_order_id_rejected` | Empty order ID | `order_id=""` | Raises `ECO_IPN_ORDER_MISMATCH` | **PASSED** |
| **D-01** | `test_d01_correct_amount_matching` | Exact matching Decimal | `raw_amount=Decimal("12500.00")` | Returns `Decimal("12500.00")` | **PASSED** |
| **D-02** | `test_d02_tampered_amount_rejected` | 1-cent tampered amount | `raw_amount="12499.99"` | Raises `ECO_IPN_AMOUNT_MISMATCH` | **PASSED** |
| **D-03** | `test_d03_zero_amount_rejected` | Zero amount payment | `raw_amount="0.00"` | Raises `ECO_IPN_INVALID_AMOUNT` | **PASSED** |
| **D-04** | `test_d04_negative_amount_rejected` | Negative amount payment | `raw_amount="-12500.00"` | Raises `ECO_IPN_INVALID_AMOUNT` | **PASSED** |
| **D-05** | `test_d05_nan_amount_rejected` | Float NaN amount | `raw_amount=float("nan")` | Raises `ECO_IPN_INVALID_AMOUNT` | **PASSED** |
| **D-06** | `test_d06_infinity_amount_rejected` | Float Infinity amount | `raw_amount=float("inf")` | Raises `ECO_IPN_INVALID_AMOUNT` | **PASSED** |
| **D-07** | `test_d07_invalid_numeric_text_rejected` | Non-numeric string | `raw_amount="twelve_thousand"` | Raises `ECO_IPN_INVALID_AMOUNT` | **PASSED** |
| **D-08** | `test_d08_precision_and_trailing_zeros` | Precision normalization | `raw_amount="12500.0"` | Normalizes to `Decimal("12500.00")` | **PASSED** |
| **E-01** | `test_e01_correct_lkr_currency` | Supported LKR currency | `currency="LKR"` | Passes currency validation | **PASSED** |
| **E-02** | `test_e02_unsupported_currency_rejected` | Unsupported USD currency | `currency="USD"` | Raises `ECO_IPN_CURRENCY_MISMATCH` | **PASSED** |
| **E-03** | `test_e03_missing_currency_rejected` | Empty currency string | `currency=""` | Raises `ECO_IPN_CURRENCY_MISMATCH` | **PASSED** |
| **F-01** | `test_f01_status_code_success` | PayHere status 2 | `status_code="2"` | Returns `PaymentOutcome.SUCCESS` | **PASSED** |
| **F-02** | `test_f02_status_code_pending` | PayHere status 0 | `status_code="0"` | Returns `PaymentOutcome.PENDING` | **PASSED** |
| **F-03** | `test_f03_status_code_cancelled` | PayHere status -1 | `status_code="-1"` | Returns `PaymentOutcome.CANCELLED` | **PASSED** |
| **F-04** | `test_f04_status_code_failed` | PayHere status -2 | `status_code="-2"` | Returns `PaymentOutcome.FAILED` | **PASSED** |
| **F-05** | `test_f05_status_code_unknown` | Unrecognized status code | `status_code="999"`, `None` | Returns `PaymentOutcome.UNKNOWN` | **PASSED** |
| **G-01** | `test_g01_first_valid_callback_recommends_process` | First valid callback | Status `unpaid`, Outcome `SUCCESS` | Returns `IdempotencyDecision.PROCESS` | **PASSED** |
| **G-02** | `test_g02_repeated_successful_callback_recommends_already_processed` | Repeated callback | Status `paid`, Matching `payment_id` | Returns `IdempotencyDecision.ALREADY_PROCESSED` | **PASSED** |
| **G-03** | `test_g03_conflicting_gateway_tx_id_recommends_reject_conflict` | Conflicting payment ID | Status `paid`, Different `payment_id` | Returns `IdempotencyDecision.REJECT_CONFLICT` | **PASSED** |
| **G-04** | `test_g04_refunded_booking_recommends_reject_conflict` | Callback for refunded booking | Status `refunded`, Outcome `SUCCESS` | Returns `IdempotencyDecision.REJECT_CONFLICT` | **PASSED** |
| **G-05** | `test_g05_non_success_outcome_recommends_ignore` | Non-success callbacks | Outcome `CANCELLED` / `FAILED` | Returns `IdempotencyDecision.IGNORE_NON_SUCCESS` | **PASSED** |
| **H-01** | `test_h01_complete_valid_webhook_master` | End-to-end master pipeline | Complete valid dictionary payload | Returns `is_valid=True`, `decision=PROCESS` | **PASSED** |
| **H-02** | `test_h02_master_rejects_merchant_mismatch` | Master rejects merchant mismatch | `merchant_id="WRONG_ID"` | Returns `is_valid=False`, code `ECO_IPN_MERCHANT_MISMATCH` | **PASSED** |
| **H-03** | `test_h03_master_rejects_signature_mismatch` | Master rejects bad signature | `md5sig="F" * 32` | Returns `is_valid=False`, code `ECO_IPN_INVALID_SIGNATURE` | **PASSED** |
| **H-04** | `test_h04_master_rejects_missing_required_fields` | Master rejects missing fields | Missing `order_id` | Returns `is_valid=False`, code `ECO_IPN_MALFORMED_PAYLOAD` | **PASSED** |
| **H-05** | `test_h05_zero_network_calls_guaranteed` | Network call guard | Patched `socket` and `urllib` | Zero socket or urllib requests initiated | **PASSED** |
| **H-06** | `test_h06_immutable_result_is_frozen` | Immutability enforcement | Mutating attribute on result | Raises `FrozenInstanceError` | **PASSED** |
| **I-01** | `test_i01_payload_from_mapping_construction` | Payload normalization | Dictionary with whitespace padding | Attributes cleanly stripped and normalized | **PASSED** |
| **I-02** | `test_i02_symmetry_with_canonical_payhere_service` | Shared service symmetry | PayHereService canonical verification | Dual verification passes symmetrically | **PASSED** |

---

## 3. Regression Suite Verification

- `tests/test_eco_payhere_webhook_security.py`: **42 passed** (0.50s)
- `tests/test_eco_payhere_integration.py`: **24 passed** (0.45s)
- `tests/test_eco_sqlserver_test_safety.py`: **18 passed** (1.17s)
- `pytest -k "eco"` (Full Eco Suite): **571 passed, 3 skipped, 494 deselected, 0 failed** in 167.61s.
