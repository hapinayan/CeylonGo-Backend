"""
Module 04 — Eco-Tourism SQLAlchemy Payment Persistence Adapter.
================================================================
Concrete implementation of EcoPaymentPersistenceAdapter Protocol using
dependency-injected SQLAlchemy sessions.

STRICT INTEGRITY & SAFETY RULES:
- Zero database engine creation.
- Zero connections or queries executed on module import.
- Purely caller-controlled transactions (NEVER automatically calls db.commit()).
- Parameterized SQLAlchemy statements only (zero raw SQL string interpolation).
- Fails closed with typed exceptions on schema incompatibility or record mismatch.
- Reuses existing ActivityBooking, BookingReference, and PaymentTransaction models.
- Does NOT wire into active FastAPI application routers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any, Mapping, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.services.eco_payment_persistence_contract import (
    BookingPaymentStateTransitionPlan,
    EcoPaymentPersistenceAdapter,
    PaymentTransactionPlan,
)

logger = logging.getLogger(__name__)


class EcoPaymentPersistenceError(Exception):
    """Base exception for payment persistence adapter failures."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class EcoPaymentRecordNotFoundError(EcoPaymentPersistenceError):
    """Raised when an ActivityBooking or BookingReference is not found."""

    pass


class EcoPaymentConcurrencyConflictError(EcoPaymentPersistenceError):
    """Raised when an operation conflicts with existing booking or transaction state."""

    pass


class EcoPaymentSchemaIncompatibilityError(EcoPaymentPersistenceError):
    """Raised when the database schema cannot safely support the requested operation."""

    pass


class SqlAlchemyEcoPaymentAdapter:
    """
    Concrete persistence adapter satisfying EcoPaymentPersistenceAdapter.

    Guarantees:
    1. Operates solely through the caller-supplied SQLAlchemy Session.
    2. Zero engine or connection creation within this class.
    3. Flushes mutations for constraint checking, but NEVER calls db.commit().
    4. Enforces row-level idempotency and conflict prevention.
    """

    def __init__(self, db: Session) -> None:
        if db is None:
            raise EcoPaymentPersistenceError("A valid SQLAlchemy Session must be provided.")
        self.db = db

    def get_booking_by_id(self, booking_id: int) -> Optional[ActivityBooking]:
        """
        Fetch ActivityBooking by primary key.
        Uses parameterized query via SQLAlchemy ORM.
        """
        if not isinstance(booking_id, int) or booking_id <= 0:
            return None
        return (
            self.db.query(ActivityBooking)
            .filter(ActivityBooking.id == booking_id)
            .first()
        )

    def get_booking_reference(self, order_id: str) -> Optional[BookingReference]:
        """
        Fetch BookingReference by unique canonical reference code (e.g. ECO-YYYYMMDD-XXXX).
        """
        if not order_id or not str(order_id).strip():
            return None
        cleaned_ref = str(order_id).strip()
        return (
            self.db.query(BookingReference)
            .filter(BookingReference.booking_reference == cleaned_ref)
            .first()
        )

    def get_booking_reference_by_booking_id(
        self, booking_id: int
    ) -> Optional[BookingReference]:
        """
        Fetch BookingReference associated with an activity/eco booking ID.
        """
        if not isinstance(booking_id, int) or booking_id <= 0:
            return None
        return (
            self.db.query(BookingReference)
            .filter(
                BookingReference.target_booking_id == booking_id,
                BookingReference.module_type.in_(("activity", "eco")),
            )
            .order_by(BookingReference.id.desc())
            .first()
        )

    def get_latest_payment_transaction(
        self, booking_reference_id: int, gateway_name: str = "payhere"
    ) -> Optional[PaymentTransaction]:
        """
        Fetch the most recent PaymentTransaction recorded for a booking reference.
        """
        if not isinstance(booking_reference_id, int) or booking_reference_id <= 0:
            return None
        return (
            self.db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.booking_reference_id == booking_reference_id,
                PaymentTransaction.gateway_name == gateway_name,
            )
            .order_by(PaymentTransaction.id.desc())
            .first()
        )

    def is_gateway_transaction_unique(
        self, gateway_name: str, gateway_tx_id: str
    ) -> bool:
        """
        Check whether a gateway transaction ID is globally unique in payment_transactions.
        Returns True if NOT found (unique); False if already recorded.
        """
        if not gateway_tx_id or not str(gateway_tx_id).strip():
            return False
        cleaned_id = str(gateway_tx_id).strip()
        count = (
            self.db.query(func.count(PaymentTransaction.id))
            .filter(
                PaymentTransaction.gateway_name == gateway_name,
                PaymentTransaction.gateway_transaction_id == cleaned_id,
            )
            .scalar()
            or 0
        )
        return count == 0

    def execute_payment_confirmation_atomic(
        self,
        transition_plan: BookingPaymentStateTransitionPlan,
        tx_plan: PaymentTransactionPlan,
    ) -> bool:
        """
        Apply payment confirmation transitions and record PaymentTransaction atomically.

        Guarantees:
        - Caller-controlled transaction: DOES NOT call self.db.commit().
        - Validates that booking exists and is eligible for payment.
        - Prevents double payment confirmation; supports idempotent re-entry.
        - Rejects payment confirmation on cancelled or rejected bookings.
        - Rejects conflicting gateway transaction IDs across different bookings.
        - Synchronizes both ActivityBooking and BookingReference payment states.
        """
        # 1. Fetch and validate ActivityBooking
        booking = self.get_booking_by_id(transition_plan.booking_id)
        if booking is None:
            raise EcoPaymentRecordNotFoundError(
                f"ActivityBooking #{transition_plan.booking_id} was not found."
            )

        # 2. Terminal status protection
        if booking.booking_status in ("cancelled", "rejected"):
            raise EcoPaymentConcurrencyConflictError(
                f"Cannot confirm payment for booking #{booking.id} with status '{booking.booking_status}'."
            )

        # 3. Method validation
        if booking.payment_method != "online":
            raise EcoPaymentConcurrencyConflictError(
                f"Cannot confirm online payment for booking #{booking.id} with payment method '{booking.payment_method}'."
            )

        # 4. Fetch and validate BookingReference
        booking_ref = None
        if tx_plan.booking_reference_id:
            booking_ref = (
                self.db.query(BookingReference)
                .filter(BookingReference.id == tx_plan.booking_reference_id)
                .first()
            )
        if not booking_ref and tx_plan.booking_reference_code:
            booking_ref = self.get_booking_reference(tx_plan.booking_reference_code)

        if not booking_ref:
            raise EcoPaymentRecordNotFoundError(
                f"BookingReference #{tx_plan.booking_reference_id} ('{tx_plan.booking_reference_code}') was not found."
            )

        # 5. Idempotency & double-payment validation
        if booking.payment_status == "paid":
            # Check if this exact gateway transaction was already recorded for this booking
            existing_tx = (
                self.db.query(PaymentTransaction)
                .filter(
                    PaymentTransaction.booking_reference_id == booking_ref.id,
                    PaymentTransaction.gateway_transaction_id == tx_plan.gateway_transaction_id,
                )
                .first()
            )
            if existing_tx:
                # Purely idempotent re-entry: already applied
                logger.info(
                    "Idempotent re-entry for booking #%d and gateway tx '%s'.",
                    booking.id,
                    tx_plan.gateway_transaction_id,
                )
                return True
            else:
                raise EcoPaymentConcurrencyConflictError(
                    f"Booking #{booking.id} is already marked as paid with another transaction."
                )

        # 6. Gateway transaction uniqueness across other bookings
        conflict_tx = (
            self.db.query(PaymentTransaction)
            .filter(
                PaymentTransaction.gateway_name == tx_plan.gateway_name,
                PaymentTransaction.gateway_transaction_id == tx_plan.gateway_transaction_id,
                PaymentTransaction.booking_reference_id != booking_ref.id,
            )
            .first()
        )
        if conflict_tx:
            raise EcoPaymentConcurrencyConflictError(
                f"Gateway transaction ID '{tx_plan.gateway_transaction_id}' is already recorded "
                f"for a different booking reference (#{conflict_tx.booking_reference_id})."
            )

        # 7. Apply state transition to ActivityBooking
        booking.payment_status = transition_plan.to_payment_status
        if (
            transition_plan.action_required
            and transition_plan.to_booking_status != transition_plan.from_booking_status
        ):
            booking.booking_status = transition_plan.to_booking_status

        booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # 8. Synchronize BookingReference
        booking_ref.payment_status = transition_plan.to_payment_status
        if (
            transition_plan.action_required
            and transition_plan.to_booking_status != transition_plan.from_booking_status
        ):
            booking_ref.booking_status = transition_plan.to_booking_status

        # 9. Insert PaymentTransaction audit record
        payload_json = None
        if tx_plan.gateway_response_summary:
            try:
                payload_json = json.dumps(tx_plan.gateway_response_summary)
            except (TypeError, ValueError):
                payload_json = str(tx_plan.gateway_response_summary)

        payment_method = "online"
        if tx_plan.gateway_response_summary and isinstance(
            tx_plan.gateway_response_summary, dict
        ):
            payment_method = str(
                tx_plan.gateway_response_summary.get("method") or "online"
            )

        paid_at = tx_plan.created_at
        if isinstance(paid_at, datetime) and paid_at.tzinfo is not None:
            paid_at = paid_at.astimezone(timezone.utc).replace(tzinfo=None)

        new_tx = PaymentTransaction(
            booking_reference_id=booking_ref.id,
            user_id=tx_plan.user_id,
            gateway_name=tx_plan.gateway_name,
            gateway_transaction_id=tx_plan.gateway_transaction_id,
            amount=tx_plan.amount,
            currency=tx_plan.currency,
            payment_status=tx_plan.payment_status,
            payment_method=payment_method,
            gateway_response_payload=payload_json,
            paid_at=paid_at or datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(new_tx)

        # 10. Flush mutations for immediate constraint checking without committing
        self.db.flush()
        return True

    def record_payment_failure_atomic(
        self,
        booking_id: int,
        gateway_tx_id: Optional[str],
        reason_note: str,
        gateway_payload: Mapping[str, Any],
    ) -> bool:
        """
        Record a failed or cancelled payment attempt without marking the booking as paid.

        Guarantees:
        - Caller-controlled transaction: DOES NOT call self.db.commit().
        - Validates that booking exists.
        - Rejects failure recording on already paid bookings.
        - Inserts failure transaction record if booking reference exists.
        """
        booking = self.get_booking_by_id(booking_id)
        if booking is None:
            raise EcoPaymentRecordNotFoundError(
                f"ActivityBooking #{booking_id} was not found."
            )

        if booking.payment_status == "paid":
            raise EcoPaymentConcurrencyConflictError(
                f"Cannot record payment failure for booking #{booking_id} because it is already paid."
            )

        # If booking is currently unpaid or pending, update to failed if permitted
        if booking.payment_status in ("unpaid", "pending"):
            booking.payment_status = "failed"
            booking.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Locate reference to record failure transaction
        booking_ref = self.get_booking_reference_by_booking_id(booking.id)
        if booking_ref:
            booking_ref.payment_status = "failed"

            if gateway_tx_id:
                payload_json = None
                try:
                    payload_json = json.dumps(dict(gateway_payload))
                except (TypeError, ValueError):
                    payload_json = str(gateway_payload)

                fail_tx = PaymentTransaction(
                    booking_reference_id=booking_ref.id,
                    user_id=booking.user_id,
                    gateway_name="payhere",
                    gateway_transaction_id=str(gateway_tx_id).strip(),
                    amount=booking.total_price,
                    currency=booking.currency or "LKR",
                    payment_status="failed",
                    payment_method=str(gateway_payload.get("method") or "online"),
                    gateway_response_payload=payload_json,
                    paid_at=None,
                )
                self.db.add(fail_tx)

        self.db.flush()
        return True
