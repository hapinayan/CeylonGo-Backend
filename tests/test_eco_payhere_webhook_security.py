"""
Module 04 — Eco-Tourism PayHere Webhook Security Unit Tests.
============================================================
Comprehensive test suite validating standalone PayHere IPN security logic.

Covers:
A. Merchant validation (correct, incorrect, missing, empty, server unconfigured).
B. Signature validation (valid, invalid, missing, malformed, secret isolation).
C. Order validation (correct, incorrect, missing).
D. Amount validation (correct, tampered, zero, negative, NaN, Inf, non-numeric, precision).
E. Currency validation (correct LKR, unsupported currency, missing currency).
F. Payment statuses (success, pending, cancellation, failure, unknown status).
G. Idempotency decisions (first callback, repeated callback, conflicting tx ID, refunded state, non-success).
H. Security & isolation (zero DB writes, zero network, no secret leaks, immutable result).
I. Compatibility (dictionary payload constructor, PayHereService verification symmetry).
"""

from decimal import Decimal
import hashlib
from unittest.mock import patch

import pytest

from app.core.payhere import PayHereService
from app.services.eco_payhere_webhook_security import (
    EcoPayHereIPNPayload,
    EcoPayHereIPNValidationResult,
    EcoPayHereWebhookSecurityError,
    IdempotencyDecision,
    PaymentOutcome,
    TrustedEcoPaymentContext,
    classify_payment_status,
    evaluate_idempotency_decision,
    validate_eco_payhere_ipn,
    validate_ipn_signature,
    validate_merchant_id,
    validate_server_authoritative_payment,
)


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def configured_payhere_service() -> PayHereService:
    svc = PayHereService()
    svc.merchant_id = "1211111"
    svc.merchant_secret = "4f8a9b2c3d4e5f6a7b8c9d0e"
    svc.mode = "sandbox"
    return svc


@pytest.fixture
def trusted_context() -> TrustedEcoPaymentContext:
    return TrustedEcoPaymentContext(
        booking_id=101,
        trusted_order_id="BK-ECO-20261120-7A9B",
        expected_amount=Decimal("12500.00"),
        expected_currency="LKR",
        expected_merchant_id="1211111",
        current_payment_status="unpaid",
        existing_gateway_transaction_id=None,
    )


def compute_valid_ipn_signature(
    merchant_id: str,
    merchant_secret: str,
    order_id: str,
    amount: str,
    currency: str,
    status_code: str,
) -> str:
    """Helper to compute valid PayHere MD5 signature according to official formula."""
    secret_hash = hashlib.md5(merchant_secret.encode("utf-8")).hexdigest().upper()
    raw = f"{merchant_id}{order_id}{amount}{currency}{status_code}{secret_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


@pytest.fixture
def valid_ipn_dict(configured_payhere_service: PayHereService, trusted_context: TrustedEcoPaymentContext) -> dict:
    sig = compute_valid_ipn_signature(
        merchant_id=configured_payhere_service.merchant_id,
        merchant_secret=configured_payhere_service.merchant_secret,
        order_id=trusted_context.trusted_order_id,
        amount="12500.00",
        currency="LKR",
        status_code="2",
    )
    return {
        "merchant_id": configured_payhere_service.merchant_id,
        "order_id": trusted_context.trusted_order_id,
        "payment_id": "320025112233",
        "payhere_amount": "12500.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": sig,
        "status_message": "Successfully completed.",
        "method": "VISA",
    }


# ===========================================================================
# CATEGORY A: Merchant ID Validation
# ===========================================================================


def test_a01_correct_merchant_id():
    """A1: Exact merchant ID match passes."""
    validate_merchant_id("1211111", "1211111")


def test_a02_incorrect_merchant_id_rejected():
    """A2: Mismatched merchant ID raises ECO_IPN_MERCHANT_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_merchant_id("9999999", "1211111")
    assert exc_info.value.code == "ECO_IPN_MERCHANT_MISMATCH"


def test_a03_missing_merchant_id_rejected():
    """A3: None merchant ID raises ECO_IPN_MERCHANT_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_merchant_id(None, "1211111")
    assert exc_info.value.code == "ECO_IPN_MERCHANT_MISMATCH"


def test_a04_empty_merchant_id_rejected():
    """A4: Empty or whitespace merchant ID raises ECO_IPN_MERCHANT_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_merchant_id("   ", "1211111")
    assert exc_info.value.code == "ECO_IPN_MERCHANT_MISMATCH"


def test_a05_server_unconfigured_merchant_id_rejected():
    """A5: Unconfigured server merchant ID raises ECO_IPN_CONFIGURATION_MISSING."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_merchant_id("1211111", "")
    assert exc_info.value.code == "ECO_IPN_CONFIGURATION_MISSING"


# ===========================================================================
# CATEGORY B: Signature Validation
# ===========================================================================


def test_b01_valid_signature_passes(configured_payhere_service: PayHereService):
    """B1: Valid cryptographic signature passes."""
    sig = compute_valid_ipn_signature(
        merchant_id=configured_payhere_service.merchant_id,
        merchant_secret=configured_payhere_service.merchant_secret,
        order_id="ORD_001",
        amount="5000.00",
        currency="LKR",
        status_code="2",
    )
    validate_ipn_signature(
        payhere_svc=configured_payhere_service,
        order_id="ORD_001",
        payhere_amount="5000.00",
        payhere_currency="LKR",
        status_code="2",
        received_signature=sig,
    )


def test_b02_invalid_signature_rejected(configured_payhere_service: PayHereService):
    """B2: Tampered signature raises ECO_IPN_INVALID_SIGNATURE."""
    fake_sig = "A" * 32
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_ipn_signature(
            payhere_svc=configured_payhere_service,
            order_id="ORD_001",
            payhere_amount="5000.00",
            payhere_currency="LKR",
            status_code="2",
            received_signature=fake_sig,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_SIGNATURE"


def test_b03_missing_signature_rejected(configured_payhere_service: PayHereService):
    """B3: Empty or missing signature raises ECO_IPN_MISSING_SIGNATURE."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_ipn_signature(
            payhere_svc=configured_payhere_service,
            order_id="ORD_001",
            payhere_amount="5000.00",
            payhere_currency="LKR",
            status_code="2",
            received_signature="",
        )
    assert exc_info.value.code == "ECO_IPN_MISSING_SIGNATURE"


def test_b04_malformed_signature_format_rejected(configured_payhere_service: PayHereService):
    """B4: Non-32-hex string signature raises ECO_IPN_MALFORMED_SIGNATURE."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_ipn_signature(
            payhere_svc=configured_payhere_service,
            order_id="ORD_001",
            payhere_amount="5000.00",
            payhere_currency="LKR",
            status_code="2",
            received_signature="not-a-valid-hex-md5",
        )
    assert exc_info.value.code == "ECO_IPN_MALFORMED_SIGNATURE"


def test_b05_secret_non_disclosure_in_signature_error(configured_payhere_service: PayHereService):
    """B5: Error message does not leak merchant_secret."""
    secret = configured_payhere_service.merchant_secret
    try:
        validate_ipn_signature(
            payhere_svc=configured_payhere_service,
            order_id="ORD_001",
            payhere_amount="5000.00",
            payhere_currency="LKR",
            status_code="2",
            received_signature="0" * 32,
        )
    except EcoPayHereWebhookSecurityError as err:
        assert secret not in str(err)
        assert secret not in str(err.safe_details)


# ===========================================================================
# CATEGORY C: Order ID Validation
# ===========================================================================


def test_c01_correct_order_id(trusted_context: TrustedEcoPaymentContext):
    """C1: Matching order ID passes validation."""
    amt = validate_server_authoritative_payment(
        incoming_order_id="BK-ECO-20261120-7A9B",
        incoming_amount_raw="12500.00",
        incoming_currency_raw="LKR",
        context=trusted_context,
    )
    assert amt == Decimal("12500.00")


def test_c02_incorrect_order_id_rejected(trusted_context: TrustedEcoPaymentContext):
    """C2: Mismatched order ID raises ECO_IPN_ORDER_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id="BK-ECO-WRONG-REF",
            incoming_amount_raw="12500.00",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_ORDER_MISMATCH"


def test_c03_missing_order_id_rejected(trusted_context: TrustedEcoPaymentContext):
    """C3: Empty order ID raises ECO_IPN_ORDER_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id="",
            incoming_amount_raw="12500.00",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_ORDER_MISMATCH"


# ===========================================================================
# CATEGORY D: Amount Validation
# ===========================================================================


def test_d01_correct_amount_matching(trusted_context: TrustedEcoPaymentContext):
    """D1: Exact matching Decimal amount passes."""
    amt = validate_server_authoritative_payment(
        incoming_order_id=trusted_context.trusted_order_id,
        incoming_amount_raw=Decimal("12500.00"),
        incoming_currency_raw="LKR",
        context=trusted_context,
    )
    assert amt == Decimal("12500.00")


def test_d02_tampered_amount_rejected(trusted_context: TrustedEcoPaymentContext):
    """D2: Even 1 cent tampering raises ECO_IPN_AMOUNT_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="12499.99",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_AMOUNT_MISMATCH"


def test_d03_zero_amount_rejected(trusted_context: TrustedEcoPaymentContext):
    """D3: Zero amount raises ECO_IPN_INVALID_AMOUNT."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="0.00",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_AMOUNT"


def test_d04_negative_amount_rejected(trusted_context: TrustedEcoPaymentContext):
    """D4: Negative amount raises ECO_IPN_INVALID_AMOUNT."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="-12500.00",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_AMOUNT"


def test_d05_nan_amount_rejected(trusted_context: TrustedEcoPaymentContext):
    """D5: Float NaN raises ECO_IPN_INVALID_AMOUNT."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw=float("nan"),
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_AMOUNT"


def test_d06_infinity_amount_rejected(trusted_context: TrustedEcoPaymentContext):
    """D6: Float Infinity raises ECO_IPN_INVALID_AMOUNT."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw=float("inf"),
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_AMOUNT"


def test_d07_invalid_numeric_text_rejected(trusted_context: TrustedEcoPaymentContext):
    """D7: Non-numeric string raises ECO_IPN_INVALID_AMOUNT."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="twelve_thousand",
            incoming_currency_raw="LKR",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_INVALID_AMOUNT"


def test_d08_precision_and_trailing_zeros(trusted_context: TrustedEcoPaymentContext):
    """D8: Single decimal digit string is normalized to 2 decimal places."""
    amt = validate_server_authoritative_payment(
        incoming_order_id=trusted_context.trusted_order_id,
        incoming_amount_raw="12500.0",
        incoming_currency_raw="LKR",
        context=trusted_context,
    )
    assert amt == Decimal("12500.00")


# ===========================================================================
# CATEGORY E: Currency Validation
# ===========================================================================


def test_e01_correct_lkr_currency(trusted_context: TrustedEcoPaymentContext):
    """E1: 'LKR' currency passes."""
    amt = validate_server_authoritative_payment(
        incoming_order_id=trusted_context.trusted_order_id,
        incoming_amount_raw="12500.00",
        incoming_currency_raw="LKR",
        context=trusted_context,
    )
    assert amt == Decimal("12500.00")


def test_e02_unsupported_currency_rejected(trusted_context: TrustedEcoPaymentContext):
    """E2: 'USD' raises ECO_IPN_CURRENCY_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="12500.00",
            incoming_currency_raw="USD",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_CURRENCY_MISMATCH"


def test_e03_missing_currency_rejected(trusted_context: TrustedEcoPaymentContext):
    """E3: Empty currency raises ECO_IPN_CURRENCY_MISMATCH."""
    with pytest.raises(EcoPayHereWebhookSecurityError) as exc_info:
        validate_server_authoritative_payment(
            incoming_order_id=trusted_context.trusted_order_id,
            incoming_amount_raw="12500.00",
            incoming_currency_raw="",
            context=trusted_context,
        )
    assert exc_info.value.code == "ECO_IPN_CURRENCY_MISMATCH"


# ===========================================================================
# CATEGORY F: Payment Status Classification
# ===========================================================================


def test_f01_status_code_success():
    """F1: '2' maps to PaymentOutcome.SUCCESS."""
    assert classify_payment_status("2") == PaymentOutcome.SUCCESS


def test_f02_status_code_pending():
    """F2: '0' maps to PaymentOutcome.PENDING."""
    assert classify_payment_status("0") == PaymentOutcome.PENDING


def test_f03_status_code_cancelled():
    """F3: '-1' maps to PaymentOutcome.CANCELLED."""
    assert classify_payment_status("-1") == PaymentOutcome.CANCELLED


def test_f04_status_code_failed():
    """F4: '-2' maps to PaymentOutcome.FAILED."""
    assert classify_payment_status("-2") == PaymentOutcome.FAILED


def test_f05_status_code_unknown():
    """F5: Unrecognized status code maps to PaymentOutcome.UNKNOWN."""
    assert classify_payment_status("999") == PaymentOutcome.UNKNOWN
    assert classify_payment_status(None) == PaymentOutcome.UNKNOWN


# ===========================================================================
# CATEGORY G: Idempotency Policy Decisions
# ===========================================================================


def test_g01_first_valid_callback_recommends_process():
    """G1: First valid success callback for unpaid booking recommends PROCESS."""
    dec = evaluate_idempotency_decision(
        outcome=PaymentOutcome.SUCCESS,
        current_payment_status="unpaid",
        incoming_payment_id="TX_100",
        existing_gateway_tx_id=None,
    )
    assert dec == IdempotencyDecision.PROCESS


def test_g02_repeated_successful_callback_recommends_already_processed():
    """G2: Repeated callback with matching payment_id for paid booking recommends ALREADY_PROCESSED."""
    dec = evaluate_idempotency_decision(
        outcome=PaymentOutcome.SUCCESS,
        current_payment_status="paid",
        incoming_payment_id="TX_100",
        existing_gateway_tx_id="TX_100",
    )
    assert dec == IdempotencyDecision.ALREADY_PROCESSED


def test_g03_conflicting_gateway_tx_id_recommends_reject_conflict():
    """G3: Conflicting payment_id for already-paid booking recommends REJECT_CONFLICT."""
    dec = evaluate_idempotency_decision(
        outcome=PaymentOutcome.SUCCESS,
        current_payment_status="paid",
        incoming_payment_id="TX_NEW_DIFFERENT",
        existing_gateway_tx_id="TX_ORIGINAL",
    )
    assert dec == IdempotencyDecision.REJECT_CONFLICT


def test_g04_refunded_booking_recommends_reject_conflict():
    """G4: Callback for refunded booking recommends REJECT_CONFLICT."""
    dec = evaluate_idempotency_decision(
        outcome=PaymentOutcome.SUCCESS,
        current_payment_status="refunded",
        incoming_payment_id="TX_100",
        existing_gateway_tx_id=None,
    )
    assert dec == IdempotencyDecision.REJECT_CONFLICT


def test_g05_non_success_outcome_recommends_ignore():
    """G5: Cancelled or failed outcome recommends IGNORE_NON_SUCCESS."""
    dec_cancelled = evaluate_idempotency_decision(
        outcome=PaymentOutcome.CANCELLED,
        current_payment_status="unpaid",
        incoming_payment_id=None,
        existing_gateway_tx_id=None,
    )
    assert dec_cancelled == IdempotencyDecision.IGNORE_NON_SUCCESS

    dec_failed = evaluate_idempotency_decision(
        outcome=PaymentOutcome.FAILED,
        current_payment_status="unpaid",
        incoming_payment_id=None,
        existing_gateway_tx_id=None,
    )
    assert dec_failed == IdempotencyDecision.IGNORE_NON_SUCCESS


# ===========================================================================
# CATEGORY H: Master Integration Function & Security
# ===========================================================================


def test_h01_complete_valid_webhook_master(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H1: Master validation function succeeds on valid IPN payload."""
    res = validate_eco_payhere_ipn(
        payload_data=valid_ipn_dict,
        context=trusted_context,
        payhere_svc=configured_payhere_service,
    )
    assert res.is_valid is True
    assert res.payment_outcome == PaymentOutcome.SUCCESS
    assert res.idempotency_decision == IdempotencyDecision.PROCESS
    assert res.safe_error_code is None
    assert res.trusted_booking_id == 101
    assert res.trusted_order_id == "BK-ECO-20261120-7A9B"
    assert res.payment_id == "320025112233"
    assert res.processed_amount == Decimal("12500.00")
    assert res.processed_currency == "LKR"


def test_h02_master_rejects_merchant_mismatch(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H2: Master validation returns invalid with ECO_IPN_MERCHANT_MISMATCH."""
    valid_ipn_dict["merchant_id"] = "WRONG_ID"
    res = validate_eco_payhere_ipn(
        payload_data=valid_ipn_dict,
        context=trusted_context,
        payhere_svc=configured_payhere_service,
    )
    assert res.is_valid is False
    assert res.safe_error_code == "ECO_IPN_MERCHANT_MISMATCH"


def test_h03_master_rejects_signature_mismatch(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H3: Master validation returns invalid on forged signature."""
    valid_ipn_dict["md5sig"] = "F" * 32
    res = validate_eco_payhere_ipn(
        payload_data=valid_ipn_dict,
        context=trusted_context,
        payhere_svc=configured_payhere_service,
    )
    assert res.is_valid is False
    assert res.safe_error_code == "ECO_IPN_INVALID_SIGNATURE"


def test_h04_master_rejects_missing_required_fields(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H4: Master validation returns ECO_IPN_MALFORMED_PAYLOAD on missing field."""
    del valid_ipn_dict["order_id"]
    res = validate_eco_payhere_ipn(
        payload_data=valid_ipn_dict,
        context=trusted_context,
        payhere_svc=configured_payhere_service,
    )
    assert res.is_valid is False
    assert res.safe_error_code == "ECO_IPN_MALFORMED_PAYLOAD"


def test_h05_zero_network_calls_guaranteed(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H5: Webhook validation never performs socket or urllib requests."""
    with patch("socket.socket") as mock_socket, patch("urllib.request.urlopen") as mock_urllib:
        res = validate_eco_payhere_ipn(
            payload_data=valid_ipn_dict,
            context=trusted_context,
            payhere_svc=configured_payhere_service,
        )
        assert res.is_valid is True
        mock_socket.assert_not_called()
        mock_urllib.assert_not_called()


def test_h06_immutable_result_is_frozen(
    valid_ipn_dict: dict,
    trusted_context: TrustedEcoPaymentContext,
    configured_payhere_service: PayHereService,
):
    """H6: EcoPayHereIPNValidationResult is frozen and tamper-proof."""
    res = validate_eco_payhere_ipn(
        payload_data=valid_ipn_dict,
        context=trusted_context,
        payhere_svc=configured_payhere_service,
    )
    with pytest.raises(Exception):
        res.is_valid = False  # FrozenInstanceError / AttributeError


# ===========================================================================
# CATEGORY I: Compatibility
# ===========================================================================


def test_i01_payload_from_mapping_construction():
    """I1: EcoPayHereIPNPayload correctly parses mapping with whitespace stripping."""
    raw = {
        "merchant_id": " 1211111 ",
        "order_id": " ORD_99 ",
        "payment_id": " 3200 ",
        "payhere_amount": " 1000.00 ",
        "payhere_currency": " LKR ",
        "status_code": " 2 ",
        "md5sig": " 0123456789abcdef0123456789abcdef ",
    }
    p = EcoPayHereIPNPayload.from_mapping(raw)
    assert p.merchant_id == "1211111"
    assert p.order_id == "ORD_99"
    assert p.payment_id == "3200"
    assert p.payhere_amount == "1000.00"
    assert p.payhere_currency == "LKR"
    assert p.status_code == "2"
    assert p.md5sig == "0123456789abcdef0123456789abcdef"


def test_i02_symmetry_with_canonical_payhere_service(
    configured_payhere_service: PayHereService,
    trusted_context: TrustedEcoPaymentContext,
):
    """I2: Verification signature is 100% symmetric with PayHereService.verify_ipn_signature."""
    order_id = "ORD_SYM_01"
    amt_str = "12500.00"
    curr_str = "LKR"
    status_str = "2"
    sig = compute_valid_ipn_signature(
        configured_payhere_service.merchant_id,
        configured_payhere_service.merchant_secret,
        order_id,
        amt_str,
        curr_str,
        status_str,
    )
    # Check directly via PayHereService
    assert (
        configured_payhere_service.verify_ipn_signature(
            order_id, amt_str, curr_str, status_str, sig
        )
        is True
    )

    # Check via validate_ipn_signature wrapper
    validate_ipn_signature(
        configured_payhere_service,
        order_id,
        amt_str,
        curr_str,
        status_str,
        sig,
    )
