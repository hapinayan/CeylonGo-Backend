"""
Module 04 — Eco-Tourism Proposed Payments Router Tests.
======================================================
Tests the proposed, unmounted PayHere checkout router contract:
`app/routers/eco_payments_proposed.py`

Key test areas:
1. Router remains unmounted in app/main.py.
2. Endpoint structure, path, and OpenAPI metadata.
3. Fail-closed HTTP 503 when no persistence adapter is configured.
4. Authentication requirement enforcement.
5. HTTP 404 when booking is not found by adapter.
6. HTTP 403 when authenticated user is not owner and not admin.
7. HTTP 200 with valid PayHere checkout payload for owner.
8. HTTP 200 with valid PayHere checkout payload for admin.
9. HTTP 409 Conflict when booking is already paid.
10. HTTP 409 Conflict when booking is cancelled.
11. HTTP 409 Conflict when booking payment method is pay_at_location.
12. Checksum validation: 32-character uppercase MD5 hash.
13. Security validation: merchant secret is strictly excluded from payload.
14. Adapter lifecycle cleanup and isolation via fixtures.
15. Dependency override isolation on test FastAPI instances.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient

from app.core.payhere import PayHereService
from app.main import app as main_app
from app.models.user import User
from app.routers import eco_payments_proposed
from app.routers.auth import get_current_user
from app.routers.eco_payments_proposed import (
    get_persistence_adapter,
    set_persistence_adapter_for_testing,
)
from app.services.eco_payment_persistence_contract import (
    EcoPaymentPersistenceAdapter,
    PaymentTransactionPlan,
    BookingPaymentStateTransitionPlan,
)


@dataclass
class MockBooking:
    id: int
    user_id: int
    booking_status: str
    payment_status: str
    payment_method: str
    total_price: Decimal
    currency: str = "LKR"
    booking_reference: str = "ECO-2026-000101"
    eco_experience_id: int = 1
    experience_id: int = 1
    slot_id: int = 1


@dataclass
class MockBookingReference:
    booking_reference: str
    total_amount: Decimal
    currency: str = "LKR"
    module_type: str = "eco"
    user_id: int = 1


class MockPersistenceAdapter:
    """Mock implementation of EcoPaymentPersistenceAdapter for router tests."""

    def __init__(self):
        self.bookings: Dict[int, MockBooking] = {}
        self.references: Dict[str, MockBookingReference] = {}
        self.recorded_transactions: List[PaymentTransactionPlan] = []
        self.applied_transitions: List[BookingPaymentStateTransitionPlan] = []

    def get_booking_by_id(self, booking_id: int) -> Optional[Any]:
        return self.bookings.get(booking_id)

    def get_booking_reference(self, reference_str: str) -> Optional[Any]:
        return self.references.get(reference_str)

    def get_payment_transaction_by_payhere_id(
        self, payhere_payment_id: str
    ) -> Optional[Any]:
        for tx in self.recorded_transactions:
            if tx.payhere_payment_id == payhere_payment_id:
                return tx
        return None

    def record_payment_transaction(self, plan: PaymentTransactionPlan) -> Any:
        self.recorded_transactions.append(plan)
        return plan

    def apply_booking_payment_transition(
        self, plan: BookingPaymentStateTransitionPlan
    ) -> Any:
        self.applied_transitions.append(plan)
        booking = self.bookings.get(plan.booking_id)
        if booking and plan.action_required:
            booking.payment_status = plan.to_payment_status
            booking.booking_status = plan.to_booking_status
        return booking


@pytest.fixture(autouse=True)
def reset_adapter_fixture():
    """Ensure persistence adapter is reset to None before and after each test."""
    set_persistence_adapter_for_testing(None)
    yield
    set_persistence_adapter_for_testing(None)


@pytest.fixture
def mock_adapter() -> MockPersistenceAdapter:
    adapter = MockPersistenceAdapter()
    # Pre-populate sample booking #101 owned by user #1
    b101 = MockBooking(
        id=101,
        user_id=1,
        booking_status="pending",
        payment_status="unpaid",
        payment_method="online",
        total_price=Decimal("15000.00"),
        currency="LKR",
        booking_reference="ECO-2026-000101",
    )
    r101 = MockBookingReference(
        booking_reference="ECO-2026-000101",
        total_amount=Decimal("15000.00"),
        currency="LKR",
        module_type="eco",
        user_id=1,
    )
    adapter.bookings[101] = b101
    adapter.references["ECO-2026-000101"] = r101
    return adapter


@pytest.fixture
def test_app() -> FastAPI:
    """Create isolated FastAPI instance mounting the proposed router."""
    app = FastAPI(title="Test Eco Payments Proposed Router")
    app.include_router(eco_payments_proposed.router)
    return app


@pytest.fixture
def traveler_user() -> User:
    return User(
        id=1,
        email="traveler@example.com",
        full_name="Kusal Perera",
        role="traveler",
        is_active=True,
    )


@pytest.fixture
def other_traveler_user() -> User:
    return User(
        id=2,
        email="other@example.com",
        full_name="Anura Kumara",
        role="traveler",
        is_active=True,
    )


@pytest.fixture
def admin_user() -> User:
    return User(
        id=99,
        email="admin@example.com",
        full_name="System Admin",
        role="admin",
        is_active=True,
    )


# ==============================================================================
# TESTS
# ==============================================================================


def test_router_01_strictly_unmounted_in_main_app():
    """Verify eco_payments_proposed router is NOT mounted in app.main.app."""
    registered_paths = [
        getattr(route, "path", None)
        for route in main_app.routes
        if hasattr(route, "path")
    ]
    target_path = "/api/v1/eco-bookings/{booking_id}/payhere-checkout"
    assert (
        target_path not in registered_paths
    ), f"Security violation: proposed payment checkout path {target_path} is mounted in app.main.app!"


def test_router_02_router_endpoint_structure():
    """Verify endpoint structure and metadata on the router itself."""
    assert len(eco_payments_proposed.router.routes) >= 1
    checkout_route = None
    for route in eco_payments_proposed.router.routes:
        if "/{booking_id}/payhere-checkout" in route.path:
            checkout_route = route
            break

    assert checkout_route is not None, "Checkout route not found on proposed router"
    assert "POST" in checkout_route.methods
    assert checkout_route.summary is not None


def test_router_03_fail_closed_503_when_no_adapter_configured(
    test_app: FastAPI, traveler_user: User
):
    """When persistence adapter is None, endpoint must return HTTP 503."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    data = response.json()
    assert "Gate 4 activation" in data["detail"]
    assert "No live database payment adapter" in data["detail"]


def test_router_04_requires_authentication(test_app: FastAPI):
    """When no valid auth credentials/override is provided, request is rejected."""
    # Do not override get_current_user; default behavior rejects without bearer token
    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    # FastAPI auth dependency raises 401 or 403 when unauthenticated
    assert response.status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )


def test_router_05_returns_404_when_booking_not_found(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When adapter cannot locate the booking ID, endpoint returns HTTP 404."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/999/payhere-checkout")

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


def test_router_06_returns_403_when_traveler_is_not_booking_owner(
    test_app: FastAPI, other_traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When authenticated traveler does not own the booking, endpoint returns HTTP 403."""
    test_app.dependency_overrides[get_current_user] = lambda: other_traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "not authorized" in response.json()["detail"].lower()


def test_router_07_returns_200_and_payload_when_caller_is_owner(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When booking owner requests checkout, endpoint returns HTTP 200 with valid PayHere payload."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()

    assert payload["order_id"] == "ECO-2026-000101"
    assert payload["amount"] == "15000.00"
    assert payload["currency"] == "LKR"
    assert len(payload["hash"]) == 32
    assert payload["hash"].isupper()
    assert payload["first_name"] == "Kusal"
    assert payload["last_name"] == "Perera"
    assert payload["email"] == "traveler@example.com"


def test_router_08_returns_200_when_caller_is_admin(
    test_app: FastAPI, admin_user: User, mock_adapter: MockPersistenceAdapter
):
    """Admin caller is permitted to generate checkout redirect for any booking."""
    test_app.dependency_overrides[get_current_user] = lambda: admin_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_200_OK
    payload = response.json()
    assert payload["order_id"] == "ECO-2026-000101"
    assert payload["amount"] == "15000.00"


def test_router_09_returns_409_when_booking_already_paid(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When booking is already paid, endpoint returns HTTP 409 Conflict."""
    mock_adapter.bookings[101].payment_status = "paid"
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "already been paid" in response.json()["detail"].lower()


def test_router_10_returns_409_when_booking_cancelled(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When booking is cancelled, endpoint returns HTTP 409 Conflict."""
    mock_adapter.bookings[101].booking_status = "cancelled"
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "cancelled" in response.json()["detail"].lower()


def test_router_11_returns_409_when_payment_method_is_pay_at_location(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """When booking specifies pay_at_location, online checkout cannot be initiated."""
    mock_adapter.bookings[101].payment_method = "pay_at_location"
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert "pay_at_location" in response.json()["detail"]


def test_router_12_returns_canonical_md5_hash_and_exact_structure(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """Verify checkout payload adheres strictly to PayHere format specification."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Exact expected fields in EcoPayHereCheckoutPayload
    required_keys = {
        "booking_id",
        "booking_reference",
        "merchant_id",
        "checkout_url",
        "order_id",
        "items",
        "amount",
        "currency",
        "hash",
        "first_name",
        "last_name",
        "email",
        "phone",
        "return_url",
        "cancel_url",
        "notify_url",
    }
    assert required_keys.issubset(data.keys())
    assert len(data["hash"]) == 32
    assert all(c in "0123456789ABCDEF" for c in data["hash"])


def test_router_13_never_exposes_merchant_secret(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """Ensure response body strictly avoids disclosing merchant secret."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    set_persistence_adapter_for_testing(mock_adapter)

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    raw_text = response.text
    assert "merchant_secret" not in raw_text
    assert "secret" not in raw_text.lower()


def test_router_14_fixture_ensures_adapter_cleanup_and_reset(
    test_app: FastAPI, traveler_user: User
):
    """Verify that after tests set adapter, fixture reset restores fail-closed 503 behavior."""
    # reset_adapter_fixture automatically runs, ensuring _configured_persistence_adapter is None
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert get_persistence_adapter() is None


def test_router_15_fastapi_dependency_override_isolation(
    test_app: FastAPI, traveler_user: User, mock_adapter: MockPersistenceAdapter
):
    """Test that dependency overriding get_persistence_adapter directly works as expected."""
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    test_app.dependency_overrides[get_persistence_adapter] = lambda: mock_adapter

    client = TestClient(test_app)
    response = client.post("/api/v1/eco-bookings/101/payhere-checkout")

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["order_id"] == "ECO-2026-000101"
