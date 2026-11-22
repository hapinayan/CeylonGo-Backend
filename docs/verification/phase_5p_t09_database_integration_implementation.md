# CeylonGo Module 04 — Phase 5P Implementation Specification
## T09 Eco Payment Database Integration & Activation Readiness

**Project:** CeylonGo-Backend | **Branch:** `develop`  
**Module:** Module 04 — Eco-Tourism (BACKEND ONLY)  
**Target Requirement:** T09 — Eco-Tourism Online Payment via PayHere  
**Files Created:**
1. `app/services/eco_payment_sqlalchemy_adapter.py`
2. `app/services/eco_payment_transaction_coordinator.py`
3. `tests/test_eco_payment_sqlalchemy_adapter.py` (20 tests, 100% passed)
4. `tests/test_eco_payment_transaction_coordinator.py` (20 tests, 100% passed)

**Status:** IMPLEMENTED (ISOLATED / DECOUPLED / UNWIRED)  
**Governance State:** T09 remains strictly **BLOCKED** pending Gates 1–4 approvals.

---

## 1. Executive Summary & Design Scope

Phase 5P implements the concrete database integration and transaction coordination components for **T09 — Eco-Tourism Online Payment via PayHere**.

By delivering an isolated SQLAlchemy persistence adapter and transaction coordinator without modifying shared files, creating physical database engines, or mounting endpoints in production routers, Phase 5P achieves complete technical readiness for database-backed payment confirmation.

### Key Architectural Accomplishments:
1. **Concrete SQLAlchemy Persistence Adapter (`SqlAlchemyEcoPaymentAdapter`):**
   - Satisfies the runtime protocol `EcoPaymentPersistenceAdapter`.
   - Requires dependency-injected `Session` (zero internal engine creation).
   - Zero operations executed on module import; zero raw SQL string interpolation.
   - Parameterized queries across `ActivityBooking`, `BookingReference`, and `PaymentTransaction`.
   - **Caller-Controlled Transactions:** Executes `db.flush()` for immediate constraint and row-level checks, but **NEVER** calls `db.commit()`. The calling service or transaction block retains full authority over commit/rollback.
   - Enforces idempotent re-entry: duplicate callbacks for the same payment ID on an already paid booking return success without re-inserting or mutating state.
   - Guards against conflict: duplicate gateway transaction IDs across different bookings fail closed with `EcoPaymentConcurrencyConflictError`.
2. **Transaction Safety & Lifecycle Separation:**
   - Separates `payment_status` from `booking_status`.
   - **Booking Approval Preservation:** Receiving an online payment transitions `payment_status` to `"paid"`. If the booking is currently `"pending"` provider approval, `booking_status` remains `"pending"`, strictly preserving the existing business rule requiring provider review.
   - Terminal booking protection: payments on `"cancelled"` or `"rejected"` bookings are strictly rejected.
   - Cash method protection: bookings created with `payment_method="pay_at_location"` cannot be confirmed via online payment gateway.
3. **Reusable Integration Coordinator (`EcoPaymentTransactionCoordinator`):**
   - Accepts an injected `EcoPaymentPersistenceAdapter`.
   - Coordinates end-to-end IPN validation using Phase 5N security rules (merchant ID matching, MD5 cryptographic signature, order ID matching, currency validation, amount matching).
   - Classifies idempotency decisions (`PROCESS`, `ALREADY_PROCESSED`, `REJECT_CONFLICT`, `IGNORE_NON_SUCCESS`).
   - Handles cancelled or failed callbacks safely by recording failure transactions without marking the booking as paid.
   - Does NOT trigger notifications before commit.
   - Does NOT trigger gateway refunds.
   - Returns a sanitized, secret-free `EcoPaymentCoordinationResult`.

---

## 2. Component Specifications

### 2.1. `SqlAlchemyEcoPaymentAdapter`
Defined in `app/services/eco_payment_sqlalchemy_adapter.py`:

```python
class SqlAlchemyEcoPaymentAdapter:
    def __init__(self, db: Session) -> None: ...
    def get_booking_by_id(self, booking_id: int) -> Optional[ActivityBooking]: ...
    def get_booking_reference(self, order_id: str) -> Optional[BookingReference]: ...
    def get_booking_reference_by_booking_id(self, booking_id: int) -> Optional[BookingReference]: ...
    def get_latest_payment_transaction(self, booking_reference_id: int, gateway_name: str = "payhere") -> Optional[PaymentTransaction]: ...
    def is_gateway_transaction_unique(self, gateway_name: str, gateway_tx_id: str) -> bool: ...
    def execute_payment_confirmation_atomic(self, transition_plan: BookingPaymentStateTransitionPlan, tx_plan: PaymentTransactionPlan) -> bool: ...
    def record_payment_failure_atomic(self, booking_id: int, gateway_tx_id: Optional[str], reason_note: str, gateway_payload: Mapping[str, Any]) -> bool: ...
```

#### Typed Error Hierarchy:
- `EcoPaymentPersistenceError(Exception)`: Root persistence failure.
- `EcoPaymentRecordNotFoundError`: Booking or BookingReference not found.
- `EcoPaymentConcurrencyConflictError`: Terminal state, cash method, already paid with another tx, or duplicate tx across bookings.
- `EcoPaymentSchemaIncompatibilityError`: Schema does not safely support the requested operation.

### 2.2. `EcoPaymentTransactionCoordinator`
Defined in `app/services/eco_payment_transaction_coordinator.py`:

```python
@dataclass(frozen=True)
class EcoPaymentCoordinationResult:
    success: bool
    status: str  # 'processed', 'already_processed', 'rejected', 'conflict_rejected', 'failed', 'non_success_recorded'
    booking_id: Optional[int] = None
    order_id: Optional[str] = None
    gateway_payment_id: Optional[str] = None
    applied_transition: bool = False
    applied_transaction: bool = False
    from_payment_status: Optional[str] = None
    to_payment_status: Optional[str] = None
    from_booking_status: Optional[str] = None
    to_booking_status: Optional[str] = None
    message: str = ""
    error_code: Optional[str] = None
```

---

## 3. Safety & Isolation Verification

| Safety Constraint | Status | Verification Evidence |
| :--- | :--- | :--- |
| Zero Engine Creation | Verified | Adapter constructor accepts `Session`; coordinator accepts `adapter`; zero `create_engine()` calls |
| Zero Auto-Commit | Verified | Tests confirm `session.rollback()` completely reverts all changes; zero calls to `db.commit()` |
| Shared ORM Untouched | Verified | Reuses existing `ActivityBooking`, `BookingReference`, `PaymentTransaction` without edits |
| Unmounted Routers | Verified | Proposed router remains unmounted in `app/main.py`; coordinator is not registered in routes |
| Zero External Network Requests | Verified | Offline deterministic tests; zero socket or HTTP requests to PayHere |
| Zero Real Database Access | Verified | Test suite runs purely in-memory via SQLite with `StaticPool`; zero SQL Server connections |
