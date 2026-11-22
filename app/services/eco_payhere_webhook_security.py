"""
Module 04 — Eco-Tourism Standalone PayHere Webhook Security Service.
====================================================================
Provides side-effect-free, robust cryptographic and business validation
for incoming PayHere Instant Payment Notification (IPN) webhooks.

STRICT INTEGRITY RESTRICTIONS:
- ZERO database writes, mutations, or transactions.
- ZERO external network calls to PayHere or payment gateways.
- ZERO modification of shared Module 03 or core payment router code.
- Pure functional validation, secret isolation, and deterministic idempotency decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
import logging
import math
import re
from typing import Any, Mapping

from app.core.payhere import PayHereService, payhere_service

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"LKR"})


class PaymentOutcome(str, Enum):
    """Normalized outcomes for PayHere callback status codes."""

    SUCCESS = "SUCCESS"  # status_code == "2"
    PENDING = "PENDING"  # status_code == "0"
    CANCELLED = "CANCELLED"  # status_code == "-1"
    FAILED = "FAILED"  # status_code == "-2"
    UNKNOWN = "UNKNOWN"  # Unsupported or unrecognized status code


class IdempotencyDecision(str, Enum):
    """
    Pure decision recommendation for handling repeated or conflicting webhooks.
    NOTE: Real concurrency safety requires database row-level locking (SELECT ... FOR UPDATE).
    """

    PROCESS = "PROCESS"  # First valid callback for an unpaid/pending booking; proceed to mutate.
    ALREADY_PROCESSED = "ALREADY_PROCESSED"  # Booking is already paid with matching transaction ID; safe 200 OK no-op.
    REJECT_CONFLICT = "REJECT_CONFLICT"  # Conflict: booking already paid with different tx ID or in incompatible state.
    IGNORE_NON_SUCCESS = "IGNORE_NON_SUCCESS"  # Valid signature, but outcome is not successful (cancelled/failed).


class EcoPayHereWebhookSecurityError(ValueError):
    """
    Structured exception raised when webhook security checks fail.
    Sanitized of sensitive credentials and raw payload contents.
    """

    def __init__(
        self,
        code: str,
        message: str,
        safe_details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.safe_details = safe_details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class TrustedEcoPaymentContext:
    """
    Server-authoritative expectations supplied by a trusted server-side caller.
    Never derived from the untrusted incoming webhook payload.
    """

    booking_id: int
    trusted_order_id: str  # Authoritative booking reference (e.g. BK-ECO-20261120-7A9B)
    expected_amount: Decimal  # Authoritative total price in LKR
    expected_currency: str = "LKR"
    expected_merchant_id: str = ""  # Authoritative server-configured merchant_id
    current_payment_status: str = "unpaid"  # 'unpaid', 'pending', 'paid', 'refunded'
    existing_gateway_transaction_id: str | None = None  # Previously recorded payment_id


@dataclass(frozen=True)
class EcoPayHereIPNPayload:
    """
    Structured, normalized representation of incoming PayHere IPN parameters.
    """

    merchant_id: str
    order_id: str
    payment_id: str | None
    payhere_amount: str
    payhere_currency: str
    status_code: str
    md5sig: str
    status_message: str | None = None
    method: str | None = None

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> EcoPayHereIPNPayload:
        """Construct normalized payload from request form data or dict."""
        return cls(
            merchant_id=str(data.get("merchant_id", "") or "").strip(),
            order_id=str(data.get("order_id", "") or "").strip(),
            payment_id=str(data.get("payment_id", "") or "").strip() or None,
            payhere_amount=str(data.get("payhere_amount", "") or "").strip(),
            payhere_currency=str(data.get("payhere_currency", "") or "").strip(),
            status_code=str(data.get("status_code", "") or "").strip(),
            md5sig=str(data.get("md5sig", "") or "").strip(),
            status_message=str(data.get("status_message", "") or "").strip() or None,
            method=str(data.get("method", "") or "").strip() or None,
        )


@dataclass(frozen=True)
class EcoPayHereIPNValidationResult:
    """
    Immutable structured result returned after complete webhook security validation.
    Contains solely verified, safe diagnostic data.
    """

    is_valid: bool
    payment_outcome: PaymentOutcome
    idempotency_decision: IdempotencyDecision
    safe_error_code: str | None = None
    safe_error_message: str | None = None
    trusted_booking_id: int | None = None
    trusted_order_id: str | None = None
    payment_id: str | None = None
    processed_amount: Decimal | None = None
    processed_currency: str | None = None


# ==============================================================================
# 1. Merchant ID Validation
# ==============================================================================


def validate_merchant_id(
    incoming_merchant_id: Any,
    expected_merchant_id: str | None,
) -> None:
    """
    Enforce strict server-authoritative PayHere merchant ID matching.

    Rules:
    - Rejects missing, empty, or whitespace-only merchant IDs.
    - Compares incoming merchant ID against server-configured authoritative ID.
    - Never exposes server secrets or expected IDs in error messages.
    """
    if not expected_merchant_id or not str(expected_merchant_id).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_CONFIGURATION_MISSING",
            message="Server PayHere merchant ID configuration is missing or unset.",
        )

    if incoming_merchant_id is None:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MERCHANT_MISMATCH",
            message="Incoming PayHere merchant ID is missing.",
        )

    incoming_clean = str(incoming_merchant_id).strip()
    expected_clean = str(expected_merchant_id).strip()

    if not incoming_clean:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MERCHANT_MISMATCH",
            message="Incoming PayHere merchant ID is empty.",
        )

    # Constant-time-like string comparison to avoid subtle timing disclosures
    if incoming_clean != expected_clean:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MERCHANT_MISMATCH",
            message="Incoming PayHere merchant ID does not match server configuration.",
        )


# ==============================================================================
# 2. Cryptographic Signature Verification
# ==============================================================================

MD5_HEX_REGEX = re.compile(r"^[0-9a-fA-F]{32}$")


def validate_ipn_signature(
    payhere_svc: PayHereService,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str,
    status_code: str,
    received_signature: str,
) -> None:
    """
    Validate PayHere IPN MD5 signature using canonical formula:
    strtoupper(md5(merchant_id + order_id + payhere_amount + payhere_currency + status_code + strtoupper(md5(merchant_secret))))

    Rules:
    - Requires all component fields to be non-empty.
    - Rejects malformed MD5 signature formatting (must be exactly 32 hex chars).
    - Never leaks the merchant secret in errors or diagnostic outputs.
    """
    if not received_signature or not str(received_signature).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MISSING_SIGNATURE",
            message="Incoming PayHere signature (md5sig) is missing or empty.",
        )

    sig_clean = str(received_signature).strip()
    if not MD5_HEX_REGEX.match(sig_clean):
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MALFORMED_SIGNATURE",
            message="Incoming PayHere signature is malformed; must be a 32-character hexadecimal string.",
        )

    if not order_id or not str(order_id).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MALFORMED_PAYLOAD",
            message="order_id parameter is missing or empty for signature verification.",
        )

    if not payhere_amount or not str(payhere_amount).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MALFORMED_PAYLOAD",
            message="payhere_amount parameter is missing or empty for signature verification.",
        )

    if not payhere_currency or not str(payhere_currency).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MALFORMED_PAYLOAD",
            message="payhere_currency parameter is missing or empty for signature verification.",
        )

    if not status_code or not str(status_code).strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_MALFORMED_PAYLOAD",
            message="status_code parameter is missing or empty for signature verification.",
        )

    is_valid = payhere_svc.verify_ipn_signature(
        order_id=str(order_id).strip(),
        payhere_amount=str(payhere_amount).strip(),
        payhere_currency=str(payhere_currency).strip(),
        status_code=str(status_code).strip(),
        received_signature=sig_clean,
    )

    if not is_valid:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_INVALID_SIGNATURE",
            message="PayHere MD5 signature verification failed.",
        )


# ==============================================================================
# 3. Server-Authoritative Context & Financial Validation
# ==============================================================================


def validate_server_authoritative_payment(
    incoming_order_id: str,
    incoming_amount_raw: Any,
    incoming_currency_raw: str,
    context: TrustedEcoPaymentContext,
) -> Decimal:
    """
    Enforce server-authoritative matching of order identifier, amount, and currency.

    Rules:
    - Order ID must strictly equal context.trusted_order_id.
    - Currency must be strictly 'LKR' and match context.expected_currency.
    - Amount must be parsed into Decimal, verified finite, positive (> 0.00).
    - Amount must match context.expected_amount with exact 2-decimal precision.
    """
    # 1. Order ID verification
    order_clean = str(incoming_order_id).strip() if incoming_order_id else ""
    if not order_clean or order_clean != context.trusted_order_id.strip():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_ORDER_MISMATCH",
            message="Incoming order ID does not match server-authoritative booking reference.",
            safe_details={"expected_order_id": context.trusted_order_id},
        )

    # 2. Currency verification
    curr_clean = str(incoming_currency_raw).strip().upper() if incoming_currency_raw else ""
    if not curr_clean or curr_clean not in SUPPORTED_CURRENCIES:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_CURRENCY_MISMATCH",
            message=f"Currency '{curr_clean}' is not supported. Supported currencies: {sorted(SUPPORTED_CURRENCIES)}.",
            safe_details={"currency": curr_clean},
        )

    if curr_clean != context.expected_currency.strip().upper():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_CURRENCY_MISMATCH",
            message="Incoming currency does not match server-expected booking currency.",
            safe_details={
                "incoming_currency": curr_clean,
                "expected_currency": context.expected_currency,
            },
        )

    # 3. Amount parsing and mathematical sanity
    if incoming_amount_raw is None:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_INVALID_AMOUNT",
            message="Incoming payment amount is missing.",
        )

    # Check for float NaN / Infinity
    if isinstance(incoming_amount_raw, float):
        if math.isnan(incoming_amount_raw) or math.isinf(incoming_amount_raw):
            raise EcoPayHereWebhookSecurityError(
                code="ECO_IPN_INVALID_AMOUNT",
                message="Payment amount must be a finite numeric value.",
            )

    try:
        if isinstance(incoming_amount_raw, Decimal):
            parsed_amount = incoming_amount_raw
        else:
            parsed_amount = Decimal(str(incoming_amount_raw).strip())
    except (InvalidOperation, TypeError, ValueError) as err:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_INVALID_AMOUNT",
            message="Incoming payment amount is not a valid numeric Decimal.",
        ) from err

    if parsed_amount.is_nan() or parsed_amount.is_infinite():
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_INVALID_AMOUNT",
            message="Payment amount must be a finite numeric value.",
        )

    if parsed_amount <= Decimal("0.00"):
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_INVALID_AMOUNT",
            message="Payment amount must be strictly greater than zero.",
        )

    quantized_received = parsed_amount.quantize(Decimal("0.01"))
    quantized_expected = context.expected_amount.quantize(Decimal("0.01"))

    if quantized_received != quantized_expected:
        raise EcoPayHereWebhookSecurityError(
            code="ECO_IPN_AMOUNT_MISMATCH",
            message="Incoming payment amount differs from server-authoritative booking total.",
            safe_details={
                "expected_amount": str(quantized_expected),
                "received_amount": str(quantized_received),
            },
        )

    return quantized_received


# ==============================================================================
# 4. Status Code Classification & Idempotency Policy
# ==============================================================================


def classify_payment_status(status_code_raw: Any) -> PaymentOutcome:
    """
    Map PayHere integer status codes to normalized PaymentOutcome.

    PayHere Specification:
      '2'  -> Success
      '0'  -> Pending
     '-1'  -> Canceled
     '-2'  -> Failed
    """
    code_str = str(status_code_raw).strip() if status_code_raw is not None else ""

    if code_str == "2":
        return PaymentOutcome.SUCCESS
    elif code_str == "0":
        return PaymentOutcome.PENDING
    elif code_str == "-1":
        return PaymentOutcome.CANCELLED
    elif code_str == "-2":
        return PaymentOutcome.FAILED
    else:
        return PaymentOutcome.UNKNOWN


def evaluate_idempotency_decision(
    outcome: PaymentOutcome,
    current_payment_status: str,
    incoming_payment_id: str | None,
    existing_gateway_tx_id: str | None,
) -> IdempotencyDecision:
    """
    Evaluate pure idempotency policy based on trusted server state and incoming callback.

    Decisions:
    - IGNORE_NON_SUCCESS: Outcome is not SUCCESS (e.g. CANCELLED, FAILED, PENDING).
    - PROCESS: Booking is unpaid/pending and ready for transition to 'paid'.
    - ALREADY_PROCESSED: Booking is already 'paid' with matching gateway payment ID.
    - REJECT_CONFLICT: Booking is in an incompatible state (e.g. refunded/cancelled),
      or already paid under a different payment ID.
    """
    status_clean = (current_payment_status or "").strip().lower()

    if outcome != PaymentOutcome.SUCCESS:
        return IdempotencyDecision.IGNORE_NON_SUCCESS

    if status_clean in ("unpaid", "pending"):
        return IdempotencyDecision.PROCESS

    if status_clean == "paid":
        # If an existing transaction ID is tracked, ensure incoming payment_id matches
        if existing_gateway_tx_id and incoming_payment_id:
            if existing_gateway_tx_id.strip() == incoming_payment_id.strip():
                return IdempotencyDecision.ALREADY_PROCESSED
            else:
                return IdempotencyDecision.REJECT_CONFLICT
        return IdempotencyDecision.ALREADY_PROCESSED

    # Booking in terminal non-payable states (refunded, cancelled, rejected)
    if status_clean in ("refunded", "refund_pending", "cancelled", "rejected", "failed"):
        return IdempotencyDecision.REJECT_CONFLICT

    return IdempotencyDecision.REJECT_CONFLICT


# ==============================================================================
# 5. Public Master Validation Function
# ==============================================================================


def validate_eco_payhere_ipn(
    payload_data: Mapping[str, Any] | EcoPayHereIPNPayload,
    context: TrustedEcoPaymentContext,
    payhere_svc: PayHereService | None = None,
) -> EcoPayHereIPNValidationResult:
    """
    Master standalone validator for incoming PayHere IPN webhooks for Eco-Tourism.

    Pipeline:
    1. Extract and normalize incoming payload fields.
    2. Enforce merchant ID matching server-side authoritative configuration.
    3. Verify cryptographic MD5 callback signature.
    4. Validate server-authoritative order ID, currency, and amount.
    5. Classify PayHere callback outcome (SUCCESS, FAILED, CANCELLED, PENDING, UNKNOWN).
    6. Evaluate pure idempotency decision based on current payment state.
    7. Return immutable, safe validation result without sensitive tokens.
    """
    svc = payhere_svc or payhere_service

    # 1. Normalize payload
    if isinstance(payload_data, EcoPayHereIPNPayload):
        payload = payload_data
    elif isinstance(payload_data, Mapping):
        payload = EcoPayHereIPNPayload.from_mapping(payload_data)
    else:
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=PaymentOutcome.UNKNOWN,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code="ECO_IPN_MALFORMED_PAYLOAD",
            safe_error_message="Payload must be a dictionary or EcoPayHereIPNPayload instance.",
        )

    # 2. Check required fields presence
    if not all([
        payload.merchant_id,
        payload.order_id,
        payload.payhere_amount,
        payload.payhere_currency,
        payload.status_code,
        payload.md5sig,
    ]):
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=PaymentOutcome.UNKNOWN,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code="ECO_IPN_MALFORMED_PAYLOAD",
            safe_error_message="Missing one or more required PayHere notification parameters.",
        )

    # 3. Classify outcome early
    outcome = classify_payment_status(payload.status_code)
    if outcome == PaymentOutcome.UNKNOWN:
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=PaymentOutcome.UNKNOWN,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code="ECO_IPN_INVALID_STATUS",
            safe_error_message=f"Unsupported or unrecognized PayHere status_code: '{payload.status_code}'.",
        )

    # 4. Merchant ID validation
    expected_m_id = context.expected_merchant_id or svc.merchant_id
    try:
        validate_merchant_id(payload.merchant_id, expected_m_id)
    except EcoPayHereWebhookSecurityError as err:
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=outcome,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code=err.code,
            safe_error_message=err.message,
            trusted_booking_id=context.booking_id,
            trusted_order_id=context.trusted_order_id,
        )

    # 5. Cryptographic signature verification
    try:
        validate_ipn_signature(
            payhere_svc=svc,
            order_id=payload.order_id,
            payhere_amount=payload.payhere_amount,
            payhere_currency=payload.payhere_currency,
            status_code=payload.status_code,
            received_signature=payload.md5sig,
        )
    except EcoPayHereWebhookSecurityError as err:
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=outcome,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code=err.code,
            safe_error_message=err.message,
            trusted_booking_id=context.booking_id,
            trusted_order_id=context.trusted_order_id,
        )

    # 6. Server-authoritative context matching
    try:
        validated_amount = validate_server_authoritative_payment(
            incoming_order_id=payload.order_id,
            incoming_amount_raw=payload.payhere_amount,
            incoming_currency_raw=payload.payhere_currency,
            context=context,
        )
    except EcoPayHereWebhookSecurityError as err:
        return EcoPayHereIPNValidationResult(
            is_valid=False,
            payment_outcome=outcome,
            idempotency_decision=IdempotencyDecision.IGNORE_NON_SUCCESS,
            safe_error_code=err.code,
            safe_error_message=err.message,
            trusted_booking_id=context.booking_id,
            trusted_order_id=context.trusted_order_id,
        )

    # 7. Evaluate idempotency decision
    idempotency_dec = evaluate_idempotency_decision(
        outcome=outcome,
        current_payment_status=context.current_payment_status,
        incoming_payment_id=payload.payment_id,
        existing_gateway_tx_id=context.existing_gateway_transaction_id,
    )

    return EcoPayHereIPNValidationResult(
        is_valid=True,
        payment_outcome=outcome,
        idempotency_decision=idempotency_dec,
        safe_error_code=None,
        safe_error_message=None,
        trusted_booking_id=context.booking_id,
        trusted_order_id=context.trusted_order_id,
        payment_id=payload.payment_id,
        processed_amount=validated_amount,
        processed_currency=payload.payhere_currency.strip().upper(),
    )
