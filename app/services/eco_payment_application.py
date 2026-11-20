"""
Module 04 — Eco-Tourism Payment Application Service.
====================================================
Coordinates checkout preparation, webhook security validation,
and payment state transition planning for Eco-Tourism bookings.

STRICT INTEGRITY RESTRICTIONS:
- ZERO physical database engines or SQLAlchemy sessions created here.
- Zero mutations or side-effects on persisted records.
- Dependency-injected components only.
- Decimal precision for all monetary values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Mapping, Optional

from app.core.payhere import PayHereService, payhere_service
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference
from app.models.user import User
from app.services.eco_payhere_webhook_security import (
    EcoPayHereIPNPayload,
    EcoPayHereIPNValidationResult,
    EcoPayHereWebhookSecurityError,
    IdempotencyDecision,
    PaymentOutcome,
    TrustedEcoPaymentContext,
    validate_eco_payhere_ipn,
)
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    PaymentTransactionPlan,
)
from app.services.eco_payment_service import (
    EcoPayHereCheckoutPayload,
    EcoPaymentServiceError,
    prepare_eco_payhere_checkout,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EcoPaymentWebhookApplicationResult:
    """
    Immutable result from evaluating an incoming PayHere IPN webhook
    at the application service layer.
    """

    validation_result: EcoPayHereIPNValidationResult
    transition_plan: Optional[BookingPaymentStateTransitionPlan] = None
    transaction_plan: Optional[PaymentTransactionPlan] = None
    should_confirm_payment: bool = False
    safe_response_message: str = "Webhook evaluated."


def prepare_eco_checkout_application(
    booking: Optional[ActivityBooking],
    current_user: Optional[User],
    booking_ref: Optional[BookingReference],
    experience_title: Optional[str] = None,
    payhere_svc: Optional[PayHereService] = None,
    client_amount_override: Any = None,
) -> EcoPayHereCheckoutPayload:
    """
    Application-layer entrypoint for preparing an Eco-Tourism PayHere checkout.

    Guarantees:
    - Verifies booking eligibility and online payment method.
    - Prevents price tampering by deriving amount strictly from booking.total_price.
    - Excludes merchant secret from output.
    - Reuses canonical prepare_eco_payhere_checkout service.
    """
    if booking is None:
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_NOT_FOUND",
            message="Booking record is missing or not found.",
        )

    if current_user is None:
        raise EcoPaymentServiceError(
            code="ECO_UNAUTHORIZED",
            message="Authentication is required to prepare payment checkout.",
        )

    # Delegate to core Phase 5M preparation service
    return prepare_eco_payhere_checkout(
        booking=booking,
        current_user=current_user,
        booking_ref=booking_ref,
        experience_title=experience_title,
        payhere_svc=payhere_svc or payhere_service,
        client_amount_override=client_amount_override,
    )


def evaluate_eco_payment_webhook(
    payload_data: Mapping[str, Any] | EcoPayHereIPNPayload,
    trusted_context: TrustedEcoPaymentContext,
    booking_reference_id: int,
    user_id: int,
    payhere_svc: Optional[PayHereService] = None,
) -> EcoPaymentWebhookApplicationResult:
    """
    Application-layer entrypoint for evaluating incoming PayHere IPN webhooks.

    Guarantees:
    - Cryptographic signature and merchant ID verification via Phase 5N.
    - Server-authoritative order reference, currency, and amount validation.
    - Classifies payment outcome and applies idempotency policy.
    - Generates immutable state transition and transaction plans.
    - ZERO database mutations executed.
    """
    # 1. Execute cryptographic and security validation
    val_result = validate_eco_payhere_ipn(
        payload_data=payload_data,
        context=trusted_context,
        payhere_svc=payhere_svc or payhere_service,
    )

    if not val_result.is_valid:
        return EcoPaymentWebhookApplicationResult(
            validation_result=val_result,
            transition_plan=None,
            transaction_plan=None,
            should_confirm_payment=False,
            safe_response_message=val_result.safe_error_message or "Webhook validation failed.",
        )

    # 2. Handle Idempotency Decisions
    if val_result.idempotency_decision == IdempotencyDecision.ALREADY_PROCESSED:
        return EcoPaymentWebhookApplicationResult(
            validation_result=val_result,
            transition_plan=BookingPaymentStateTransitionPlan(
                booking_id=trusted_context.booking_id,
                from_payment_status=trusted_context.current_payment_status,
                to_payment_status=trusted_context.current_payment_status,
                from_booking_status="confirmed",
                to_booking_status="confirmed",
                audit_note="Duplicate PayHere IPN received; payment was already recorded.",
                action_required=False,
            ),
            transaction_plan=None,
            should_confirm_payment=False,
            safe_response_message="Payment was already processed successfully.",
        )

    if val_result.idempotency_decision == IdempotencyDecision.REJECT_CONFLICT:
        return EcoPaymentWebhookApplicationResult(
            validation_result=val_result,
            transition_plan=None,
            transaction_plan=None,
            should_confirm_payment=False,
            safe_response_message="Conflicting payment state; callback rejected.",
        )

    if val_result.idempotency_decision == IdempotencyDecision.IGNORE_NON_SUCCESS:
        # Callback is cryptographically valid, but payment failed or was cancelled
        audit_note = (
            f"PayHere payment outcome is {val_result.payment_outcome.value} "
            f"(Payment ID: {val_result.payment_id or 'none'})."
        )
        return EcoPaymentWebhookApplicationResult(
            validation_result=val_result,
            transition_plan=BookingPaymentStateTransitionPlan(
                booking_id=trusted_context.booking_id,
                from_payment_status=trusted_context.current_payment_status,
                to_payment_status="failed" if val_result.payment_outcome == PaymentOutcome.FAILED else trusted_context.current_payment_status,
                from_booking_status="pending",
                to_booking_status="pending",
                audit_note=audit_note,
                action_required=False,
            ),
            transaction_plan=None,
            should_confirm_payment=False,
            safe_response_message=f"Non-success outcome: {val_result.payment_outcome.value}.",
        )

    # 3. Successful First-Time Processing (IdempotencyDecision.PROCESS)
    assert val_result.processed_amount is not None
    assert val_result.processed_currency is not None

    payment_id_str = val_result.payment_id or "UNKNOWN_GATEWAY_TX"

    tx_plan = PaymentTransactionPlan(
        booking_reference_id=booking_reference_id,
        booking_reference_code=trusted_context.trusted_order_id,
        user_id=user_id,
        gateway_name="payhere",
        gateway_transaction_id=payment_id_str,
        amount=val_result.processed_amount,
        currency=val_result.processed_currency,
        payment_status="paid",
        gateway_response_summary={
            "payment_id": payment_id_str,
            "status_code": "2",
            "order_id": trusted_context.trusted_order_id,
        },
        created_at=datetime.now(timezone.utc),
    )

    transition_plan = BookingPaymentStateTransitionPlan(
        booking_id=trusted_context.booking_id,
        from_payment_status=trusted_context.current_payment_status,
        to_payment_status="paid",
        from_booking_status="pending",
        to_booking_status="confirmed",
        audit_note=f"PayHere payment confirmed successfully (Payment ID: {payment_id_str}).",
        action_required=True,
    )

    return EcoPaymentWebhookApplicationResult(
        validation_result=val_result,
        transition_plan=transition_plan,
        transaction_plan=tx_plan,
        should_confirm_payment=True,
        safe_response_message="Payment verified successfully; ready for atomic persistence.",
    )
