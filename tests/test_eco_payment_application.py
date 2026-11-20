"""
Module 04 — Eco-Tourism Payment Application Layer Unit Tests.
=============================================================
Tests application service orchestration, checkout preparation,
webhook state transition planning, and persistence contracts.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from typing import Any, Mapping, Optional
from unittest.mock import MagicMock, patch

import pytest

from app.core.payhere import PayHereService
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import User
from app.services.eco_payhere_webhook_security import (
    IdempotencyDecision,
    PaymentOutcome,
    TrustedEcoPaymentContext,
)
from app.services.eco_payment_application import (
    EcoPaymentWebhookApplicationResult,
    evaluate_eco_payment_webhook,
    prepare_eco_checkout_application,
)
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    EcoPaymentPersistenceAdapter,
    PaymentTransactionPlan,
)
from app.services.eco_payment_service import (
    EcoPayHereCheckoutPayload,
    EcoPaymentServiceError,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Mocks
# ---------------------------------------------------------------------------


@dataclass
class MockUser:
    id: int = 42
    full_name: str = "Sunil Perera"
    email: str = "sunil.perera@example.com"
    phone: str = "+94771234567"
    role: str = "traveler"


@dataclass
class MockBookingRef:
    booking_reference: str = "BK-ECO-20261120-7A9B"
    booking_type: str = "activity"
    booking_id: int = 101


@dataclass
class MockActivityBooking:
    id: int = 101
    user_id: int = 42
    eco_experience_id: int = 5
    booking_status: str = "confirmed"
    payment_status: str = "unpaid"
    payment_method: str = "online"
    total_price: Decimal = Decimal("12500.00")
    currency: str = "LKR"


@pytest.fixture
def mock_user() -> MockUser:
    return MockUser()


@pytest.fixture
def mock_booking_ref() -> MockBookingRef:
    return MockBookingRef()


@pytest.fixture
def mock_booking() -> MockActivityBooking:
    return MockActivityBooking()


@pytest.fixture
def configured_payhere_service() -> PayHereService:
    svc = PayHereService()
    svc.merchant_id = "1211111"
    svc.merchant_secret = "SecretTestKey999!"
    svc.mode = "sandbox"
    return svc


@pytest.fixture
def trusted_payment_context() -> TrustedEcoPaymentContext:
    return TrustedEcoPaymentContext(
        booking_id=101,
        trusted_order_id="BK-ECO-20261120-7A9B",
        expected_amount=Decimal("12500.00"),
        expected_currency="LKR",
        expected_merchant_id="1211111",
        current_payment_status="unpaid",
        existing_gateway_transaction_id=None,
    )


def compute_ipn_sig(merchant_id: str, secret: str, order_id: str, amt: str, curr: str, status: str) -> str:
    sec_hash = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
    raw = f"{merchant_id}{order_id}{amt}{curr}{status}{sec_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


# ---------------------------------------------------------------------------
# Checkout Application Flow Tests
# ---------------------------------------------------------------------------


def test_app_01_checkout_preparation_success(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-01: Valid checkout application generates trusted payload."""
    payload = prepare_eco_checkout_application(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        experience_title="Sinharaja Rainforest Trek",
        payhere_svc=configured_payhere_service,
    )
    assert isinstance(payload, EcoPayHereCheckoutPayload)
    assert payload.booking_id == 101
    assert payload.order_id == "BK-ECO-20261120-7A9B"
    assert payload.amount == Decimal("12500.00")
    assert payload.currency == "LKR"
    assert len(payload.hash) == 32


def test_app_02_checkout_rejects_missing_booking(
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
):
    """App-02: Missing booking raises ECO_BOOKING_NOT_FOUND."""
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=None,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
        )
    assert exc_info.value.code == "ECO_BOOKING_NOT_FOUND"


def test_app_03_checkout_rejects_missing_user(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
):
    """App-03: Missing user raises ECO_UNAUTHORIZED."""
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=None,
            booking_ref=mock_booking_ref,
        )
    assert exc_info.value.code == "ECO_UNAUTHORIZED"


def test_app_04_checkout_rejects_unauthorized_traveler(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-04: Non-owner traveler raises ECO_PAYMENT_UNAUTHORIZED."""
    stranger = MockUser(id=999, role="traveler")
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=stranger,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_PAYMENT_UNAUTHORIZED"


def test_app_05_checkout_permits_admin_role(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-05: Admin user can prepare checkout for any booking."""
    admin = MockUser(id=999, role="admin")
    payload = prepare_eco_checkout_application(
        booking=mock_booking,
        current_user=admin,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )
    assert payload.booking_id == 101


def test_app_06_checkout_rejects_cancelled_booking(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-06: Cancelled booking raises ECO_BOOKING_CANCELLED."""
    mock_booking.booking_status = "cancelled"
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_BOOKING_CANCELLED"


def test_app_07_checkout_rejects_already_paid_booking(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-07: Already-paid booking raises ECO_PAYMENT_ALREADY_PAID."""
    mock_booking.payment_status = "paid"
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_PAYMENT_ALREADY_PAID"


def test_app_08_checkout_rejects_pay_at_location_method(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-08: Pay-at-location method raises ECO_INVALID_PAYMENT_METHOD."""
    mock_booking.payment_method = "pay_at_location"
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_INVALID_PAYMENT_METHOD"


def test_app_09_checkout_rejects_client_amount_tampering(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-09: Overridden client amount raises ECO_AMOUNT_TAMPERING_REJECTED."""
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_checkout_application(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
            client_amount_override=Decimal("100.00"),
        )
    assert exc_info.value.code == "ECO_AMOUNT_TAMPERING_REJECTED"


def test_app_10_checkout_secret_not_disclosed(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """App-10: Payload and string representations strictly exclude merchant secret."""
    secret = configured_payhere_service.merchant_secret
    payload = prepare_eco_checkout_application(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )
    assert not hasattr(payload, "merchant_secret")
    assert secret not in str(payload)
    assert secret not in repr(payload)


# ---------------------------------------------------------------------------
# Webhook Application Flow Tests
# ---------------------------------------------------------------------------


def test_app_11_webhook_evaluation_first_success(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-11: First valid success IPN generates plans for paid transition and transaction."""
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        trusted_payment_context.trusted_order_id,
        "12500.00",
        "LKR",
        "2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_payment_context.trusted_order_id,
        "payment_id": "TX_GATEWAY_1001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is True
    assert result.should_confirm_payment is True
    assert result.transition_plan is not None
    assert result.transition_plan.to_payment_status == "paid"
    assert result.transition_plan.to_booking_status == "confirmed"
    assert result.transition_plan.action_required is True

    assert result.transaction_plan is not None
    assert result.transaction_plan.gateway_transaction_id == "TX_GATEWAY_1001"
    assert result.transaction_plan.amount == Decimal("12500.00")
    assert result.transaction_plan.currency == "LKR"


def test_app_12_webhook_evaluation_already_processed(
    configured_payhere_service: PayHereService,
):
    """App-12: Repeated IPN for already-paid booking returns ALREADY_PROCESSED and no action."""
    context_paid = TrustedEcoPaymentContext(
        booking_id=101,
        trusted_order_id="BK-ECO-20261120-7A9B",
        expected_amount=Decimal("12500.00"),
        expected_currency="LKR",
        expected_merchant_id=configured_payhere_service.merchant_id,
        current_payment_status="paid",
        existing_gateway_transaction_id="TX_GATEWAY_1001",
    )
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        context_paid.trusted_order_id,
        "12500.00",
        "LKR",
        "2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": context_paid.trusted_order_id,
        "payment_id": "TX_GATEWAY_1001",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=context_paid,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is True
    assert result.should_confirm_payment is False
    assert result.validation_result.idempotency_decision == IdempotencyDecision.ALREADY_PROCESSED
    assert result.transaction_plan is None
    assert result.transition_plan is not None
    assert result.transition_plan.action_required is False


def test_app_13_webhook_evaluation_rejects_conflicting_tx_id(
    configured_payhere_service: PayHereService,
):
    """App-13: Conflicting payment ID for already-paid booking yields REJECT_CONFLICT."""
    context_paid = TrustedEcoPaymentContext(
        booking_id=101,
        trusted_order_id="BK-ECO-20261120-7A9B",
        expected_amount=Decimal("12500.00"),
        expected_currency="LKR",
        expected_merchant_id=configured_payhere_service.merchant_id,
        current_payment_status="paid",
        existing_gateway_transaction_id="TX_ORIGINAL_ID",
    )
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        context_paid.trusted_order_id,
        "12500.00",
        "LKR",
        "2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": context_paid.trusted_order_id,
        "payment_id": "TX_DIFFERENT_CONFLICTING_ID",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=context_paid,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is True
    assert result.should_confirm_payment is False
    assert result.validation_result.idempotency_decision == IdempotencyDecision.REJECT_CONFLICT
    assert result.transaction_plan is None


def test_app_14_webhook_evaluation_handles_cancelled_callback(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-14: Cancelled callback ('-1') returns IGNORE_NON_SUCCESS and no confirmation."""
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        trusted_payment_context.trusted_order_id,
        "12500.00",
        "LKR",
        "-1",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_payment_context.trusted_order_id,
        "payment_id": "TX_CANCELLED",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "-1",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is True
    assert result.should_confirm_payment is False
    assert result.validation_result.payment_outcome == PaymentOutcome.CANCELLED
    assert result.validation_result.idempotency_decision == IdempotencyDecision.IGNORE_NON_SUCCESS
    assert result.transaction_plan is None


def test_app_15_webhook_evaluation_handles_failed_callback(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-15: Failed callback ('-2') records failure note without paid transition."""
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        trusted_payment_context.trusted_order_id,
        "12500.00",
        "LKR",
        "-2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_payment_context.trusted_order_id,
        "payment_id": "TX_FAILED",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "-2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is True
    assert result.should_confirm_payment is False
    assert result.validation_result.payment_outcome == PaymentOutcome.FAILED
    assert result.transition_plan is not None
    assert result.transition_plan.to_payment_status == "failed"
    assert result.transition_plan.action_required is False


def test_app_16_webhook_rejects_forged_signature(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-16: Forged signature returns is_valid=False and no execution plans."""
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_payment_context.trusted_order_id,
        "payment_id": "TX_FORGED",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": "0" * 32,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is False
    assert result.should_confirm_payment is False
    assert result.validation_result.safe_error_code == "ECO_IPN_INVALID_SIGNATURE"
    assert result.transition_plan is None
    assert result.transaction_plan is None


def test_app_17_webhook_rejects_order_id_mismatch(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-17: Order ID mismatch returns is_valid=False."""
    wrong_order = "BK-ECO-WRONG"
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        wrong_order,
        "12500.00",
        "LKR",
        "2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": wrong_order,
        "payment_id": "TX_ORDER_MISMATCH",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is False
    assert result.validation_result.safe_error_code == "ECO_IPN_ORDER_MISMATCH"


def test_app_18_webhook_rejects_amount_mismatch(
    trusted_payment_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """App-18: Tampered amount returns is_valid=False."""
    tampered_amt = "999.00"
    sig = compute_ipn_sig(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        trusted_payment_context.trusted_order_id,
        tampered_amt,
        "LKR",
        "2",
    )
    payload = {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_payment_context.trusted_order_id,
        "payment_id": "TX_AMT_MISMATCH",
        "payhere_amount": tampered_amt,
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
    }

    result = evaluate_eco_payment_webhook(
        payload_data=payload,
        trusted_context=trusted_payment_context,
        booking_reference_id=50,
        user_id=42,
        payhere_svc=configured_payhere_service,
    )

    assert result.validation_result.is_valid is False
    assert result.validation_result.safe_error_code == "ECO_IPN_AMOUNT_MISMATCH"


# ---------------------------------------------------------------------------
# Persistence Contract Protocol Compliance Tests
# ---------------------------------------------------------------------------


class MockEcoPaymentPersistenceAdapter:
    """Mock implementation verifying protocol adherence."""

    def __init__(self):
        self.bookings = {}
        self.references = {}
        self.transactions = []

    def get_booking_by_id(self, booking_id: int):
        return self.bookings.get(booking_id)

    def get_booking_reference(self, order_id: str):
        return self.references.get(order_id)

    def get_latest_payment_transaction(self, booking_reference_id: int, gateway_name: str = "payhere"):
        for tx in reversed(self.transactions):
            if tx.get("booking_reference_id") == booking_reference_id and tx.get("gateway_name") == gateway_name:
                return tx
        return None

    def is_gateway_transaction_unique(self, gateway_name: str, gateway_tx_id: str) -> bool:
        for tx in self.transactions:
            if tx.get("gateway_name") == gateway_name and tx.get("gateway_transaction_id") == gateway_tx_id:
                return False
        return True

    def execute_payment_confirmation_atomic(self, transition_plan, tx_plan) -> bool:
        return True

    def record_payment_failure_atomic(self, booking_id, gateway_tx_id, reason_note, gateway_payload) -> bool:
        return True


def test_app_19_persistence_adapter_protocol_compliance():
    """App-19: Mock adapter satisfies EcoPaymentPersistenceAdapter runtime check."""
    mock_adapter = MockEcoPaymentPersistenceAdapter()
    assert isinstance(mock_adapter, EcoPaymentPersistenceAdapter)


def test_app_20_payment_transaction_plan_structure():
    """App-20: PaymentTransactionPlan is immutable and correctly initialized."""
    plan = PaymentTransactionPlan(
        booking_reference_id=1,
        booking_reference_code="BK-001",
        user_id=42,
        gateway_name="payhere",
        gateway_transaction_id="TX_100",
        amount=Decimal("1500.00"),
        currency="LKR",
        payment_status="paid",
    )
    assert plan.gateway_transaction_id == "TX_100"
    assert plan.amount == Decimal("1500.00")
    with pytest.raises(Exception):
        plan.amount = Decimal("2000.00")  # FrozenInstanceError


def test_app_21_state_transition_plan_structure():
    """App-21: BookingPaymentStateTransitionPlan is immutable and correctly initialized."""
    plan = BookingPaymentStateTransitionPlan(
        booking_id=10,
        from_payment_status="unpaid",
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note="Payment confirmed.",
        action_required=True,
    )
    assert plan.booking_id == 10
    assert plan.to_payment_status == "paid"
    assert plan.action_required is True
    with pytest.raises(Exception):
        plan.to_payment_status = "failed"  # FrozenInstanceError
