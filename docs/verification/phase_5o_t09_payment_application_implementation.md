# CeylonGo Module 04 — Phase 5O Implementation Specification
## T09 Eco Payment Integration Service & Proposed Router Contract

**Project:** CeylonGo-Backend | **Branch:** `develop`  
**Module:** Module 04 — Eco-Tourism (BACKEND ONLY)  
**Target Requirement:** T09 — Eco-Tourism Online Payment via PayHere  
**Files Created:**
1. `app/services/eco_payment_persistence_contract.py`
2. `app/services/eco_payment_application.py`
3. `app/routers/eco_payments_proposed.py`
4. `tests/test_eco_payment_application.py` (21 tests, 100% passed)
5. `tests/test_eco_payments_proposed_router.py` (15 tests, 100% passed)

**Status:** IMPLEMENTED (STANDALONE / DECOUPLED / UNMOUNTED)  
**Governance State:** T09 remains strictly **BLOCKED** pending Gate 1–4 approvals, shared database migrations, and production activation.

---

## 1. Executive Summary & Design Scope

Phase 5O synthesizes the pure checkout preparation logic of Phase 5M (`app/services/eco_payment_service.py`) and the authoritative webhook security validation of Phase 5N (`app/services/eco_payhere_webhook_security.py`) into a complete, decoupled application architecture.

### Architectural Accomplishments:
1. **Decoupled Persistence Contract (`EcoPaymentPersistenceAdapter` Protocol):**
   - Implements `@runtime_checkable class EcoPaymentPersistenceAdapter(Protocol)` in `app/services/eco_payment_persistence_contract.py`.
   - Defines strict transactional contracts for querying bookings, reading booking references, checking transaction idempotency, and executing atomic state transitions without coupling to any physical database engine.
2. **Deterministic Mutation Planning Dataclasses:**
   - `PaymentTransactionPlan`: Frozen dataclass detailing gateway transaction audit record insertion (payment_id, order_id, amount, currency, method, status).
   - `BookingPaymentStateTransitionPlan`: Frozen dataclass defining exact target statuses (`to_payment_status="paid"`, `to_booking_status="confirmed"`, `action_required=True/False`).
3. **Application Orchestration Layer (`app/services/eco_payment_application.py`):**
   - `prepare_eco_checkout_application(...)`: Orchestrates booking state validation, RBAC enforcement, server-authoritative amount retrieval, and PayHere checkout payload generation with MD5 hash and zero secret exposure.
   - `evaluate_eco_payment_webhook(...)`: Orchestrates incoming IPN validation via Phase 5N security primitives, enforces gateway idempotency, generates deterministic state transition plans, and prepares audit transaction records without performing direct DB mutations.
4. **Proposed REST API Contract (`app/routers/eco_payments_proposed.py`):**
   - Declares `POST /api/v1/eco-bookings/{booking_id}/payhere-checkout`.
   - Fails closed with `HTTP 503 SERVICE UNAVAILABLE` when `adapter is None` (production default prior to Gate 4).
   - Enforces RBAC: booking owner or `admin` role required.
   - Strictly unmounted from `app/main.py`.

---

## 2. Component Details

### 2.1. `app/services/eco_payment_persistence_contract.py`
Defines the boundary between business logic and database persistence:

```python
@dataclass(frozen=True)
class PaymentTransactionPlan:
    booking_id: int
    booking_reference: str
    payhere_payment_id: str
    payhere_amount: Decimal
    payhere_currency: str
    payment_status: str
    payment_method: str | None = None
    status_message: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

@dataclass(frozen=True)
class BookingPaymentStateTransitionPlan:
    booking_id: int
    from_payment_status: str
    to_payment_status: str
    from_booking_status: str
    to_booking_status: str
    action_required: bool
    audit_note: str

@runtime_checkable
class EcoPaymentPersistenceAdapter(Protocol):
    def get_booking_by_id(self, booking_id: int) -> Optional[Any]: ...
    def get_booking_reference(self, reference_str: str) -> Optional[Any]: ...
    def get_payment_transaction_by_payhere_id(self, payhere_payment_id: str) -> Optional[Any]: ...
    def record_payment_transaction(self, plan: PaymentTransactionPlan) -> Any: ...
    def apply_booking_payment_transition(self, plan: BookingPaymentStateTransitionPlan) -> Any: ...
```

### 2.2. `app/services/eco_payment_application.py`
Connects domain services to persistence protocols:

- **`prepare_eco_checkout_application`**:
  - Revalidates that booking exists, is active, belongs to user or admin, is `pending`/`unpaid`, and has payment method `online`.
  - Derives authoritative amount directly from `booking.total_price`.
  - Rejects tampering and excludes secrets.
- **`evaluate_eco_payment_webhook`**:
  - Extracts and normalizes IPN payload.
  - Builds `TrustedEcoPaymentContext` from adapter queries.
  - Validates merchant ID, MD5 signature, order ID, amount, and currency via `validate_eco_payhere_ipn`.
  - Classifies idempotency policy:
    - `PROCESS`: Produces `BookingPaymentStateTransitionPlan` (`paid`, `confirmed`) and `PaymentTransactionPlan`.
    - `ALREADY_PROCESSED`: Produces no-op transition plan (`action_required=False`) and `transaction_plan=None`.
    - `REJECT_CONFLICT`: Rejects duplicate transaction with different payment ID.
    - `IGNORE_NON_SUCCESS`: Produces `action_required=False` with appropriate status note for `CANCELLED` or `FAILED`.

### 2.3. `app/routers/eco_payments_proposed.py`
Unmounted router implementing the traveler checkout endpoint:

- **Route:** `POST /api/v1/eco-bookings/{booking_id}/payhere-checkout`
- **Dependencies:**
  - `get_current_user`: Authentication and RBAC.
  - `get_persistence_adapter`: Injects `EcoPaymentPersistenceAdapter`. Fails closed (HTTP 503) if `None`.
  - `payhere_svc`: PayHere configuration and hash utilities.
- **Error Mapping:**
  - Missing adapter -> `HTTP 503 Service Unavailable`
  - Missing booking -> `HTTP 404 Not Found`
  - Ownership violation -> `HTTP 403 Forbidden`
  - Already paid / cancelled / invalid payment method -> `HTTP 409 Conflict`
  - Malformed request -> `HTTP 400 Bad Request`

---

## 3. Safety & Isolation Verification

| Safety Rule | Status | Verification Evidence |
| :--- | :--- | :--- |
| `app/main.py` Unmodified | Verified | Route `/api/v1/eco-bookings/{booking_id}/payhere-checkout` not in `main_app.routes` |
| Zero Direct DB Queries in Router | Verified | Router interacts strictly through abstract `EcoPaymentPersistenceAdapter` Protocol |
| Fail-Closed Staging Guard | Verified | HTTP 503 returned when `get_persistence_adapter()` returns `None` |
| Secret Non-Disclosure | Verified | Payload output verified; `merchant_secret` excluded from all schemas and responses |
| Zero External Network Requests | Verified | Offline deterministic mock testing; zero HTTP/socket connections |
| No SQL Server Connections | Verified | Test suites run entirely in-memory with dataclass mocks; zero DB touches |
