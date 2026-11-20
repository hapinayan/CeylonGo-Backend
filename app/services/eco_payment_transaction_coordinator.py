"""
Module 04 — Eco-Tourism Payment Transaction Coordinator.
=========================================================
Coordinates payment transaction validation, idempotency evaluation,
and persistence execution through an injected persistence adapter.

STRICT INTEGRITY & SAFETY RULES:
- Zero database engine creation.
- Caller-controlled transactions only (DOES NOT call db.commit() or manage engine).
- Zero asynchronous notifications sent before commit.
- Zero gateway refunds or outbound financial transactions initiated.
- Preserves existing booking approval rules (does not auto-confirm bookings pending provider approval).
- Handles payment_status and booking_status independently.
- Rejects unverified, forged, tampered, or mismatched callbacks.
- Does NOT register into active FastAPI application routers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Mapping, Optional

from app.core.payhere import PayHereService, payhere_service
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.services.eco_payhere_webhook_security import (
    EcoPayHereIPNPayload,
    EcoPayHereIPNValidationResult,
    IdempotencyDecision,
    PaymentOutcome,
    TrustedEcoPaymentContext,
    validate_eco_payhere_ipn,
)
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    EcoPaymentPersistenceAdapter,
    PaymentTransactionPlan,
)
from app.services.eco_payment_sqlalchemy_adapter import (
    EcoPaymentConcurrencyConflictError,
    EcoPaymentPersistenceError,
    EcoPaymentRecordNotFoundError,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EcoPaymentCoordinationResult:
    """
    Sanitized outcome of payment transaction coordination.
    Safe for external logging and caller response formatting (excludes all secrets).
    """

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


class EcoPaymentTransactionCoordinator:
    """
    Coordinates safe, isolated payment processing for Eco-Tourism bookings.

    Guarantees:
    1. Operates solely through the injected persistence adapter.
    2. Validates cryptographic signatures and authoritative booking context.
    3. Rejects forged signatures, order mismatches, and tampered amounts.
    4. Enforces idempotency policies without double-confirming or double-inserting.
    5. Preserves provider approval rules: does NOT promote pending provider-approval bookings
       to 'confirmed' unless auto_confirm_booking is explicitly permitted.
    6. Does NOT commit transactions; caller retains 100% control over commit/rollback.
    """

    def __init__(self, adapter: EcoPaymentPersistenceAdapter) -> None:
        if adapter is None:
            raise ValueError("A valid EcoPaymentPersistenceAdapter must be provided.")
        self.adapter = adapter

    def coordinate_verified_callback(
        self,
        booking: ActivityBooking,
        booking_ref: BookingReference,
        val_result: EcoPayHereIPNValidationResult,
        auto_confirm_booking: bool = False,
        payment_method: str = "online",
    ) -> EcoPaymentCoordinationResult:
        """
        Coordinates persistence for an already verified PayHere IPN validation result.

        Rules:
        - Rejects if val_result is invalid.
        - Enforces idempotency decision.
        - Independently manages payment_status vs booking_status.
        - Calls adapter to execute atomic persistence within caller's transaction.
        """
        if not val_result.is_valid:
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                message=val_result.safe_error_message or "Webhook validation failed.",
                error_code=val_result.safe_error_code or "ECO_WEBHOOK_INVALID",
            )

        # 1. Terminal booking status check
        if booking.booking_status in ("cancelled", "rejected"):
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                message=f"Booking is in terminal status '{booking.booking_status}'; payment cannot be accepted.",
                error_code="ECO_BOOKING_TERMINAL_STATUS",
            )

        # 2. Payment method check
        if booking.payment_method != "online":
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                message=f"Booking payment method is '{booking.payment_method}'; online payment rejected.",
                error_code="ECO_INVALID_PAYMENT_METHOD",
            )

        # 3. Idempotency Evaluation
        if val_result.idempotency_decision == IdempotencyDecision.ALREADY_PROCESSED:
            return EcoPaymentCoordinationResult(
                success=True,
                status="already_processed",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                applied_transition=False,
                applied_transaction=False,
                from_payment_status=booking.payment_status,
                to_payment_status=booking.payment_status,
                from_booking_status=booking.booking_status,
                to_booking_status=booking.booking_status,
                message="Payment was already recorded and confirmed; duplicate IPN safely ignored.",
            )

        if val_result.idempotency_decision == IdempotencyDecision.REJECT_CONFLICT:
            return EcoPaymentCoordinationResult(
                success=False,
                status="conflict_rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                applied_transition=False,
                applied_transaction=False,
                message="Conflicting payment transaction ID detected for already paid booking.",
                error_code="ECO_PAYMENT_CONFLICT",
            )

        if val_result.idempotency_decision == IdempotencyDecision.IGNORE_NON_SUCCESS:
            # Payment failed or was cancelled at PayHere
            reason = f"PayHere payment outcome: {val_result.payment_outcome.value}"
            try:
                self.adapter.record_payment_failure_atomic(
                    booking_id=booking.id,
                    gateway_tx_id=val_result.payment_id,
                    reason_note=reason,
                    gateway_payload={
                        "payment_id": val_result.payment_id,
                        "payment_outcome": val_result.payment_outcome.value,
                    },
                )
            except EcoPaymentPersistenceError as p_err:
                logger.warning(
                    "Failure recording failed for booking #%d: %s", booking.id, p_err
                )

            return EcoPaymentCoordinationResult(
                success=True,
                status="non_success_recorded",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=val_result.payment_id,
                applied_transition=False,
                applied_transaction=True,
                from_payment_status=booking.payment_status,
                to_payment_status="failed",
                from_booking_status=booking.booking_status,
                to_booking_status=booking.booking_status,
                message=f"Non-success payment recorded ({val_result.payment_outcome.value}).",
            )

        # 4. Standard Processing (IdempotencyDecision.PROCESS)
        assert val_result.processed_amount is not None
        assert val_result.processed_currency is not None
        payment_id_str = val_result.payment_id or "UNKNOWN_PAYMENT_ID"

        # Global gateway uniqueness check across other bookings
        if not self.adapter.is_gateway_transaction_unique("payhere", payment_id_str):
            return EcoPaymentCoordinationResult(
                success=False,
                status="conflict_rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=payment_id_str,
                applied_transition=False,
                applied_transaction=False,
                message=f"Gateway transaction ID '{payment_id_str}' has already been processed for another booking.",
                error_code="ECO_GATEWAY_TX_DUPLICATE",
            )

        # Independent booking_status vs payment_status lifecycle:
        # Preserve existing booking approval rules:
        # If booking is pending provider approval, payment does NOT automatically approve it
        # unless auto_confirm_booking is explicitly allowed by caller/experience rules.
        from_booking_status = booking.booking_status
        if auto_confirm_booking:
            to_booking_status = "confirmed"
        else:
            # Maintain existing booking status (e.g. 'pending' stays 'pending', 'confirmed' stays 'confirmed')
            to_booking_status = from_booking_status

        transition_plan = BookingPaymentStateTransitionPlan(
            booking_id=booking.id,
            from_payment_status=booking.payment_status,
            to_payment_status="paid",
            from_booking_status=from_booking_status,
            to_booking_status=to_booking_status,
            audit_note=f"PayHere payment confirmed (Payment ID: {payment_id_str}).",
            action_required=True,
        )

        ipn_summary = {
            "payment_id": payment_id_str,
            "order_id": booking_ref.booking_reference,
            "payment_outcome": val_result.payment_outcome.value,
            "processed_amount": str(val_result.processed_amount),
            "currency": str(val_result.processed_currency),
            "method": payment_method,
        }

        tx_plan = PaymentTransactionPlan(
            booking_reference_id=booking_ref.id,
            booking_reference_code=booking_ref.booking_reference,
            user_id=booking.user_id,
            gateway_name="payhere",
            gateway_transaction_id=payment_id_str,
            amount=val_result.processed_amount,
            currency=val_result.processed_currency,
            payment_status="paid",
            gateway_response_summary=ipn_summary,
            created_at=datetime.now(timezone.utc),
        )

        try:
            self.adapter.execute_payment_confirmation_atomic(
                transition_plan=transition_plan,
                tx_plan=tx_plan,
            )
        except EcoPaymentConcurrencyConflictError as c_err:
            return EcoPaymentCoordinationResult(
                success=False,
                status="conflict_rejected",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=payment_id_str,
                message=str(c_err),
                error_code="ECO_PAYMENT_CONFLICT",
            )
        except EcoPaymentPersistenceError as p_err:
            return EcoPaymentCoordinationResult(
                success=False,
                status="failed",
                booking_id=booking.id,
                order_id=booking_ref.booking_reference,
                gateway_payment_id=payment_id_str,
                message=str(p_err),
                error_code="ECO_PERSISTENCE_FAILED",
            )

        return EcoPaymentCoordinationResult(
            success=True,
            status="processed",
            booking_id=booking.id,
            order_id=booking_ref.booking_reference,
            gateway_payment_id=payment_id_str,
            applied_transition=True,
            applied_transaction=True,
            from_payment_status=booking.payment_status,
            to_payment_status="paid",
            from_booking_status=from_booking_status,
            to_booking_status=to_booking_status,
            message="Payment processed and state transition plans applied successfully.",
        )

    def process_webhook_payload(
        self,
        payload_data: Mapping[str, Any] | EcoPayHereIPNPayload,
        payhere_svc: Optional[PayHereService] = None,
        auto_confirm_booking: bool = False,
    ) -> EcoPaymentCoordinationResult:
        """
        Complete end-to-end IPN webhook coordination pipeline:
        1. Extracts order_id.
        2. Queries BookingReference and ActivityBooking.
        3. Enforces ownership and module matching.
        4. Validates IPN cryptographic signature and amounts.
        5. Coordinates persistence transitions.
        """
        # 1. Extract order_id safely
        order_id = ""
        if isinstance(payload_data, EcoPayHereIPNPayload):
            order_id = payload_data.order_id
        elif isinstance(payload_data, Mapping):
            order_id = str(payload_data.get("order_id", "")).strip()

        if not order_id:
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                message="Incoming webhook payload is missing required 'order_id'.",
                error_code="ECO_ORDER_ID_MISSING",
            )

        # 2. Query BookingReference
        booking_ref = self.adapter.get_booking_reference(order_id)
        if not booking_ref:
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                order_id=order_id,
                message=f"Booking reference '{order_id}' was not found.",
                error_code="ECO_BOOKING_REF_NOT_FOUND",
            )

        # 3. Query ActivityBooking
        booking = self.adapter.get_booking_by_id(booking_ref.target_booking_id)
        if not booking:
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                order_id=order_id,
                message=f"ActivityBooking #{booking_ref.target_booking_id} was not found.",
                error_code="ECO_BOOKING_NOT_FOUND",
            )

        # 4. Enforce booking reference ownership integrity
        if booking_ref.user_id != booking.user_id:
            return EcoPaymentCoordinationResult(
                success=False,
                status="rejected",
                booking_id=booking.id,
                order_id=order_id,
                message="Integrity error: user_id mismatch between BookingReference and ActivityBooking.",
                error_code="ECO_INTEGRITY_MISMATCH",
            )

        # 5. Fetch latest transaction state
        latest_tx = self.adapter.get_latest_payment_transaction(
            booking_reference_id=booking_ref.id,
            gateway_name="payhere",
        )

        svc = payhere_svc or payhere_service
        trusted_context = TrustedEcoPaymentContext(
            booking_id=booking.id,
            trusted_order_id=booking_ref.booking_reference,
            expected_amount=booking.total_price,
            expected_currency=booking.currency or "LKR",
            expected_merchant_id=getattr(svc, "merchant_id", "") or "",
            current_payment_status=booking.payment_status,
            existing_gateway_transaction_id=(
                latest_tx.gateway_transaction_id if latest_tx else None
            ),
        )

        # 6. Execute Phase 5N Webhook Security Validation
        val_result = validate_eco_payhere_ipn(
            payload_data=payload_data,
            context=trusted_context,
            payhere_svc=svc,
        )

        method = "online"
        if isinstance(payload_data, Mapping):
            method = str(payload_data.get("method") or "online")
        elif isinstance(payload_data, EcoPayHereIPNPayload):
            method = str(payload_data.method or "online")

        # 7. Coordinate verified callback
        return self.coordinate_verified_callback(
            booking=booking,
            booking_ref=booking_ref,
            val_result=val_result,
            auto_confirm_booking=auto_confirm_booking,
            payment_method=method,
        )
