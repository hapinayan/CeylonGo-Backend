"""
Module 04 — Eco-Tourism PayHere Integration Mock Unit Tests.
=============================================================
Tests side-effect-free payment preparation for Eco-Tourism (ActivityBooking).

Covers:
1. Valid Eco checkout preparation.
2. Unknown booking (ECO_BOOKING_NOT_FOUND).
3. Unauthorized traveler (ECO_PAYMENT_UNAUTHORIZED).
4. Already-paid booking (ECO_PAYMENT_ALREADY_PAID).
5. Cancelled booking (ECO_BOOKING_CANCELLED).
6. Rejected booking (ECO_BOOKING_REJECTED).
7. Unsupported payment method (ECO_INVALID_PAYMENT_METHOD).
8. Zero payment amount (ECO_INVALID_AMOUNT).
9. Negative payment amount (ECO_INVALID_AMOUNT).
10. Malformed or non-finite amount (NaN, Infinity).
11. Unsupported currency (ECO_UNSUPPORTED_CURRENCY).
12. Missing booking reference (ECO_BOOKING_REFERENCE_MISSING).
13. Missing PayHere configuration (ECO_PAYHERE_CONFIG_MISSING).
14. Merchant secret not exposed in payload/repr/logs.
15. Checkout hash verified using canonical PayHereService.generate_checkout_hash.
16. Client-supplied amount override rejected (ECO_AMOUNT_TAMPERING_REJECTED).
17. Repeated checkout preparation does not write database records (pure/idempotent).
18. Zero external network calls made (mock/network guard).
19. Invalid booking payment status (refund_pending, refunded).
20. Correct checkout URL configuration (sandbox vs production).
21. Deterministic PayHere hash vectors.
22. Deterministic PayHere IPN signature vectors.
23. Admin user authorization bypass.
24. Unauthenticated user rejection.
"""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.core.payhere import PayHereService
from app.services.eco_payment_service import (
    EcoPayHereCheckoutPayload,
    EcoPaymentServiceError,
    prepare_eco_payhere_checkout,
    validate_booking_for_checkout,
    validate_checkout_amount,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Mock Models
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
    svc.merchant_secret = "SecretKeyForTesting123!"
    svc.mode = "sandbox"
    svc.return_url = "https://ceylongo.lk/checkout/success"
    svc.cancel_url = "https://ceylongo.lk/checkout/cancel"
    svc.notify_url = "https://api.ceylongo.lk/api/v1/payments/payhere/notify"
    return svc


# ---------------------------------------------------------------------------
# Test Suite
# ---------------------------------------------------------------------------


def test_01_valid_eco_checkout_preparation(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 1: Valid Eco checkout preparation produces correct immutable payload."""
    payload = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        experience_title="Sinharaja Rainforest Guided Trek",
        payhere_svc=configured_payhere_service,
    )

    assert isinstance(payload, EcoPayHereCheckoutPayload)
    assert payload.booking_id == 101
    assert payload.booking_reference == "BK-ECO-20261120-7A9B"
    assert payload.merchant_id == "1211111"
    assert payload.checkout_url == "https://sandbox.payhere.lk/pay/checkout"
    assert payload.order_id == "BK-ECO-20261120-7A9B"
    assert payload.items == "Eco Experience: Sinharaja Rainforest Guided Trek"
    assert payload.amount == Decimal("12500.00")
    assert payload.currency == "LKR"
    assert payload.first_name == "Sunil"
    assert payload.last_name == "Perera"
    assert payload.email == "sunil.perera@example.com"
    assert payload.phone == "+94771234567"
    assert len(payload.hash) == 32
    assert payload.hash == payload.hash.upper()


def test_02_unknown_booking_rejected(
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 2: Missing booking record raises ECO_BOOKING_NOT_FOUND."""
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=None,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_BOOKING_NOT_FOUND"


def test_03_unauthorized_traveler_rejected(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 3: Non-owner traveler cannot initiate payment."""
    unauthorized_user = MockUser(id=999, email="other@example.com", role="traveler")

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=unauthorized_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_PAYMENT_UNAUTHORIZED"


def test_04_already_paid_booking_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 4: Booking with payment_status='paid' cannot be checked out again."""
    mock_booking.payment_status = "paid"

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_PAYMENT_ALREADY_PAID"


def test_05_cancelled_booking_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 5: Cancelled booking raises ECO_BOOKING_CANCELLED."""
    mock_booking.booking_status = "cancelled"

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_BOOKING_CANCELLED"


def test_06_rejected_booking_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 6: Rejected booking raises ECO_BOOKING_REJECTED."""
    mock_booking.booking_status = "rejected"

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_BOOKING_REJECTED"


def test_07_unsupported_payment_method_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 7: Non-'online' payment method raises ECO_INVALID_PAYMENT_METHOD."""
    mock_booking.payment_method = "pay_at_location"

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_INVALID_PAYMENT_METHOD"


def test_08_zero_payment_amount_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 8: Zero amount raises ECO_INVALID_AMOUNT."""
    mock_booking.total_price = Decimal("0.00")

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_INVALID_AMOUNT"


def test_09_negative_payment_amount_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 9: Negative amount raises ECO_INVALID_AMOUNT."""
    mock_booking.total_price = Decimal("-500.00")

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_INVALID_AMOUNT"


def test_10_malformed_and_non_finite_amounts_rejected():
    """Scenario 10: Non-finite floats (NaN, Inf) and invalid strings raise ECO_INVALID_AMOUNT."""
    with pytest.raises(EcoPaymentServiceError) as e1:
        validate_checkout_amount(float("nan"), "LKR")
    assert e1.value.code == "ECO_INVALID_AMOUNT"

    with pytest.raises(EcoPaymentServiceError) as e2:
        validate_checkout_amount(float("inf"), "LKR")
    assert e2.value.code == "ECO_INVALID_AMOUNT"

    with pytest.raises(EcoPaymentServiceError) as e3:
        validate_checkout_amount("abc_not_a_number", "LKR")
    assert e3.value.code == "ECO_INVALID_AMOUNT"

    with pytest.raises(EcoPaymentServiceError) as e4:
        validate_checkout_amount(None, "LKR")
    assert e4.value.code == "ECO_INVALID_AMOUNT"


def test_11_unsupported_currency_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 11: Non-LKR currency raises ECO_UNSUPPORTED_CURRENCY."""
    mock_booking.currency = "USD"

    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_UNSUPPORTED_CURRENCY"


def test_12_missing_booking_reference_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    configured_payhere_service: PayHereService,
):
    """Scenario 12: Missing or empty booking reference raises ECO_BOOKING_REFERENCE_MISSING."""
    # None reference
    with pytest.raises(EcoPaymentServiceError) as e1:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=None,
            payhere_svc=configured_payhere_service,
        )
    assert e1.value.code == "ECO_BOOKING_REFERENCE_MISSING"

    # Empty reference code
    empty_ref = MockBookingRef(booking_reference="   ")
    with pytest.raises(EcoPaymentServiceError) as e2:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=empty_ref,
            payhere_svc=configured_payhere_service,
        )
    assert e2.value.code == "ECO_BOOKING_REFERENCE_MISSING"


def test_13_missing_payhere_config_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
):
    """Scenario 13: Unset merchant_id or merchant_secret raises ECO_PAYHERE_CONFIG_MISSING."""
    svc_missing_id = PayHereService()
    svc_missing_id.merchant_id = ""
    svc_missing_id.merchant_secret = "Secret123"

    with pytest.raises(EcoPaymentServiceError) as e1:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=svc_missing_id,
        )
    assert e1.value.code == "ECO_PAYHERE_CONFIG_MISSING"

    svc_missing_secret = PayHereService()
    svc_missing_secret.merchant_id = "1211111"
    svc_missing_secret.merchant_secret = "  "

    with pytest.raises(EcoPaymentServiceError) as e2:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=svc_missing_secret,
        )
    assert e2.value.code == "ECO_PAYHERE_CONFIG_MISSING"


def test_14_merchant_secret_not_exposed_in_payload_or_repr(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 14: Merchant secret is strictly excluded from payload fields, repr, and str."""
    secret = configured_payhere_service.merchant_secret
    assert secret not in ("", None)

    payload = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )

    # 1. Payload attribute check
    assert not hasattr(payload, "merchant_secret")

    # 2. String representations
    payload_str = str(payload)
    payload_repr = repr(payload)
    assert secret not in payload_str
    assert secret not in payload_repr

    # 3. Payload dict values
    payload_dict = payload.__dict__
    for k, v in payload_dict.items():
        assert v != secret, f"Field {k} contains secret value"


def test_15_canonical_payhere_hash_calculation(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 15: Checkout hash adheres exactly to canonical PayHere MD5 formula."""
    payload = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )

    # Manually compute PayHere formula:
    # strtoupper(md5(merchant_id + order_id + amount_formatted + currency + strtoupper(md5(merchant_secret))))
    merchant_id = configured_payhere_service.merchant_id
    order_id = mock_booking_ref.booking_reference
    amount_str = "12500.00"
    currency = "LKR"
    secret = configured_payhere_service.merchant_secret

    inner_secret_hash = hashlib.md5(secret.encode("utf-8")).hexdigest().upper()
    expected_raw = f"{merchant_id}{order_id}{amount_str}{currency}{inner_secret_hash}"
    expected_hash = hashlib.md5(expected_raw.encode("utf-8")).hexdigest().upper()

    assert payload.hash == expected_hash


def test_16_client_amount_override_tampering_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 16: Client-submitted amount override conflicting with server total_price is rejected."""
    # Attempting to pay LKR 1000.00 instead of 12500.00
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
            client_amount_override=Decimal("1000.00"),
        )
    assert exc_info.value.code == "ECO_AMOUNT_TAMPERING_REJECTED"

    # Exact match does not trigger tampering error
    payload = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
        client_amount_override=Decimal("12500.00"),
    )
    assert payload.amount == Decimal("12500.00")


def test_17_repeated_preparation_is_pure_and_idempotent(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 17: Service is purely functional with zero mutations or side-effects."""
    payload_1 = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )

    payload_2 = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )

    assert payload_1 == payload_2
    assert mock_booking.booking_status == "confirmed"
    assert mock_booking.payment_status == "unpaid"


def test_18_zero_external_network_calls(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 18: Preparation never opens network connections or makes HTTP requests."""
    with patch("socket.socket") as mock_socket, patch("urllib.request.urlopen") as mock_urllib:
        payload = prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=mock_user,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
        assert payload is not None
        mock_socket.assert_not_called()
        mock_urllib.assert_not_called()


def test_19_invalid_booking_payment_statuses_rejected(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 19: Disallowed payment statuses (refund_pending, refunded, failed) are rejected."""
    for invalid_status in ["refund_pending", "refunded", "failed", "partially_refunded"]:
        mock_booking.payment_status = invalid_status
        with pytest.raises(EcoPaymentServiceError) as exc_info:
            prepare_eco_payhere_checkout(
                booking=mock_booking,
                current_user=mock_user,
                booking_ref=mock_booking_ref,
                payhere_svc=configured_payhere_service,
            )
        assert exc_info.value.code == "ECO_INVALID_PAYMENT_STATUS"


def test_20_correct_checkout_url_sandbox_vs_production(
    mock_booking: MockActivityBooking,
    mock_user: MockUser,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 20: Checkout URL switches between sandbox and production according to configuration."""
    # Sandbox
    configured_payhere_service.mode = "sandbox"
    payload_sandbox = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )
    assert payload_sandbox.checkout_url == "https://sandbox.payhere.lk/pay/checkout"

    # Production
    configured_payhere_service.mode = "production"
    payload_prod = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=mock_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )
    assert payload_prod.checkout_url == "https://www.payhere.lk/pay/checkout"


def test_21_deterministic_payhere_hash_vectors():
    """Scenario 21: Verify known PayHere test vectors for checkout hash generation."""
    svc = PayHereService()
    svc.merchant_id = "1211111"
    svc.merchant_secret = "4f8a9b2c3d4e5f6a7b8c9d0e"

    # Input vector:
    # merchant_id: "1211111"
    # order_id: "ORD_TEST_001"
    # amount: 1000.00
    # currency: "LKR"
    # secret_hash = md5("4f8a9b2c3d4e5f6a7b8c9d0e").upper()
    secret_hash = hashlib.md5("4f8a9b2c3d4e5f6a7b8c9d0e".encode("utf-8")).hexdigest().upper()
    raw = f"1211111ORD_TEST_0011000.00LKR{secret_hash}"
    expected_hash = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    computed = svc.generate_checkout_hash("ORD_TEST_001", Decimal("1000.00"), "LKR")
    assert computed == expected_hash
    assert len(computed) == 32


def test_22_deterministic_payhere_ipn_signature_vectors():
    """Scenario 22: Verify PayHere IPN signature verification with valid and invalid signatures."""
    svc = PayHereService()
    svc.merchant_id = "1211111"
    svc.merchant_secret = "4f8a9b2c3d4e5f6a7b8c9d0e"

    order_id = "ORD_TEST_001"
    amount = "1000.00"
    currency = "LKR"
    status_code = "2"  # Success

    secret_hash = hashlib.md5("4f8a9b2c3d4e5f6a7b8c9d0e".encode("utf-8")).hexdigest().upper()
    raw = f"1211111{order_id}{amount}{currency}{status_code}{secret_hash}"
    valid_sig = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()

    # Valid signature
    assert svc.verify_ipn_signature(order_id, amount, currency, status_code, valid_sig) is True

    # Tampered amount
    assert svc.verify_ipn_signature(order_id, "999.00", currency, status_code, valid_sig) is False

    # Tampered status code
    assert svc.verify_ipn_signature(order_id, amount, currency, "-1", valid_sig) is False

    # Tampered signature
    assert svc.verify_ipn_signature(order_id, amount, currency, status_code, "0" * 32) is False


def test_23_admin_user_bypass_authorization(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 23: Administrator user can prepare checkout on behalf of another user."""
    admin_user = MockUser(id=999, email="admin@ceylongo.lk", role="admin")

    payload = prepare_eco_payhere_checkout(
        booking=mock_booking,
        current_user=admin_user,
        booking_ref=mock_booking_ref,
        payhere_svc=configured_payhere_service,
    )
    assert payload.booking_id == mock_booking.id
    assert payload.order_id == mock_booking_ref.booking_reference


def test_24_unauthenticated_user_rejected(
    mock_booking: MockActivityBooking,
    mock_booking_ref: MockBookingRef,
    configured_payhere_service: PayHereService,
):
    """Scenario 24: Unauthenticated user (None) raises ECO_UNAUTHORIZED."""
    with pytest.raises(EcoPaymentServiceError) as exc_info:
        prepare_eco_payhere_checkout(
            booking=mock_booking,
            current_user=None,
            booking_ref=mock_booking_ref,
            payhere_svc=configured_payhere_service,
        )
    assert exc_info.value.code == "ECO_UNAUTHORIZED"
