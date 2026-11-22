# CeylonGo Module 04 — Phase 5N Webhook Security Handoff Package
**Target Requirement:** T09 — Eco-Tourism Online Payment via PayHere  
**Target Status:** **BLOCKED** (Remains blocked pending Gate 1–4 approvals and shared router unification)  
**Deliverables Completed:**
- `app/services/eco_payhere_webhook_security.py` (Standalone Webhook Security Service)
- `tests/test_eco_payhere_webhook_security.py` (42 Unit Tests, 100% Passed)
- `docs/verification/phase_5n_t09_webhook_security_implementation.md` (Implementation Specification)
- `docs/verification/phase_5n_t09_webhook_security_test_matrix.md` (Test Execution Matrix)

---

## 1. Technical Boundary & Operational Status

The standalone IPN webhook security service implemented in Phase 5N provides complete cryptographic verification, server-authoritative context matching, PayHere status mapping, sanitized error handling, and deterministic idempotency recommendations.

However, **this service is not yet wired to a live HTTP endpoint or database session**.
In accordance with enterprise governance:
- Zero shared files were modified (`app/routers/payments.py` remains untouched).
- Zero database writes or schema modifications were performed.
- Zero network requests were made to PayHere sandbox or production servers.
- The official requirement status of **T09 remains BLOCKED**.

---

## 2. Explicit Limitations of Pure Idempotency Checks

The function `evaluate_idempotency_decision()` provides pure, in-memory decision recommendations based on input parameters. It operates under the following explicit constraints:
1. **No In-Memory Process Locking:**  
   In-memory locks (e.g. Python `threading.Lock`) cannot provide mutual exclusion across multiple ASGI worker processes (e.g. `uvicorn --workers 4`) or multi-instance deployments.
2. **Distributed Race Conditions:**  
   If PayHere delivers identical webhook callbacks simultaneously to two separate server processes, both processes could evaluate `current_payment_status == 'unpaid'` simultaneously before either writes to the database.
3. **Requirement for SQL Server Transaction Locking:**  
   Real idempotency and concurrency protection must be enforced at the database transaction layer using exclusive row-level locking (`WITH (UPDLOCK, ROWLOCK)`) on `dbo.activity_bookings` and unique constraints on `dbo.payment_transactions(booking_reference_id, gateway_transaction_id)`.

---

## 3. Required Shared Router Changes After Approval

To make online payments and webhooks operational for Module 04, the following shared code refactoring must be authorized by the **Backend Team Leader**:

### 3.1. Polymorphic Booking Lookup in `app/routers/payments.py`
Currently, `payments.py` lines 64, 221, 334, and 399 hardcode queries against the Homestay `Booking` model:
```python
# CURRENT HOMESTAY COUPLING:
booking = db.query(Booking).filter(Booking.id == booking_ref.target_booking_id).first()
```

**Required Polymorphic Implementation:**
```python
from app.models.activity_booking import ActivityBooking
from app.services.eco_payhere_webhook_security import (
    TrustedEcoPaymentContext,
    validate_eco_payhere_ipn,
    IdempotencyDecision,
    PaymentOutcome,
)

if booking_ref.module_type in ("activity", "eco_tourism"):
    booking = db.query(ActivityBooking).filter(ActivityBooking.id == booking_ref.target_booking_id).first()
    is_eco_booking = True
else:
    booking = db.query(Booking).filter(Booking.id == booking_ref.target_booking_id).first()
    is_eco_booking = False
```

### 3.2. Integration with `validate_eco_payhere_ipn`
When `is_eco_booking` is True, dispatch the incoming webhook through the Phase 5N security service:
```python
trusted_context = TrustedEcoPaymentContext(
    booking_id=booking.id,
    trusted_order_id=booking_ref.booking_reference,
    expected_amount=booking.total_price,
    expected_currency=booking.currency or "LKR",
    expected_merchant_id=payhere_service.merchant_id,
    current_payment_status=booking.payment_status or "unpaid",
    existing_gateway_transaction_id=existing_tx.gateway_transaction_id if existing_tx else None,
)

result = validate_eco_payhere_ipn(
    payload_data=form_data,
    context=trusted_context,
    payhere_svc=payhere_service,
)

if not result.is_valid:
    raise HTTPException(status_code=400, detail=result.safe_error_message)

if result.idempotency_decision == IdempotencyDecision.ALREADY_PROCESSED:
    return {"status": "already_processed"}

if result.idempotency_decision == IdempotencyDecision.REJECT_CONFLICT:
    raise HTTPException(status_code=409, detail="Payment state conflict.")

if result.payment_outcome == PaymentOutcome.SUCCESS and result.idempotency_decision == IdempotencyDecision.PROCESS:
    booking.payment_status = "paid"
    booking.payment_method = "online"
    booking_ref.payment_status = "paid"
    # Persist PaymentTransaction record...
```

---

## 4. Compatibility Findings with Existing `PayHereService`

- **Checkout Hash Generation (`generate_checkout_hash`):**
  Uses formula `strtoupper(md5(merchant_id + order_id + amount_formatted + currency + strtoupper(md5(merchant_secret))))`. Symmetrically compatible with `app/services/eco_payment_service.py` (Phase 5M).
- **IPN Signature Verification (`verify_ipn_signature`):**
  Uses formula `strtoupper(md5(merchant_id + order_id + payhere_amount + payhere_currency + status_code + strtoupper(md5(merchant_secret))))`. Phase 5N delegates cryptographic verification directly to this canonical function. Symmetrically verified across all valid and tampered vectors.
- **Precision Convention:**
  `PayHereService` requires formatted string `f"{amount:.2f}"`. Phase 5N guarantees exact 2-decimal place quantization (`Decimal("0.01")`).

---

## 5. Next Steps to Unblock T09

1. Obtain Backend Team Leader approval for Gate 1 (ORM patch) and shared payment router refactoring.
2. Obtain DBA execution of Migration 030 (Gate 2).
3. Execute Gate 3 disposable integration testing.
4. Mount `eco_bookings.router` in `app/main.py` (Gate 4).
5. Apply the polymorphic dispatch refactor to `app/routers/payments.py` and mount `POST /api/v1/eco-bookings/{id}/payhere-checkout`.
6. Run live end-to-end sandbox IPN callback verification.
7. Transition T09 from **BLOCKED** to **COMPLETE**.
