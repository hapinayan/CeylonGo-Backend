"""
Module 04 — Eco-Tourism Payment Persistence Contract.
======================================================
Defines typed protocols, transition plans, and contracts for future
database operations on ActivityBooking, BookingReference, and PaymentTransaction.

STRICT INTEGRITY RESTRICTIONS:
- ZERO physical database engines or SQLAlchemy sessions created here.
- Purely abstract protocols and immutable data structures.
- Does NOT claim real database locking or distributed idempotency.
- Designed for future dependency injection when enterprise governance approves Gate 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference, PaymentTransaction
from app.models.user import User


@dataclass(frozen=True)
class PaymentTransactionPlan:
    """
    Immutable plan for a proposed PaymentTransaction record to be persisted
    when a verified PayHere IPN callback is processed.
    """

    booking_reference_id: int
    booking_reference_code: str
    user_id: int
    gateway_name: str
    gateway_transaction_id: str
    amount: Decimal
    currency: str
    payment_status: str
    gateway_response_summary: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass(frozen=True)
class BookingPaymentStateTransitionPlan:
    """
    Immutable plan for proposed state transitions on an ActivityBooking and
    its associated BookingReference.
    """

    booking_id: int
    from_payment_status: str
    to_payment_status: str
    from_booking_status: str
    to_booking_status: str
    audit_note: str
    action_required: bool = True


@runtime_checkable
class EcoPaymentPersistenceAdapter(Protocol):
    """
    Abstract persistence adapter protocol for Module 04 payment operations.
    The real implementation will be injected after Gate 4 approval and will
    execute atomic row-level locked transactions on Microsoft SQL Server.
    """

    def get_booking_by_id(self, booking_id: int) -> Optional[ActivityBooking]:
        """Fetch ActivityBooking by primary key with read isolation."""
        ...

    def get_booking_reference(self, order_id: str) -> Optional[BookingReference]:
        """Fetch BookingReference by unique canonical reference code."""
        ...

    def get_latest_payment_transaction(
        self, booking_reference_id: int, gateway_name: str = "payhere"
    ) -> Optional[PaymentTransaction]:
        """Fetch latest transaction record for a booking reference."""
        ...

    def is_gateway_transaction_unique(
        self, gateway_name: str, gateway_tx_id: str
    ) -> bool:
        """Check whether a gateway transaction ID has already been recorded."""
        ...

    def execute_payment_confirmation_atomic(
        self,
        transition_plan: BookingPaymentStateTransitionPlan,
        tx_plan: PaymentTransactionPlan,
    ) -> bool:
        """
        Atomically transition booking and reference to 'paid' and insert
        payment transaction under exclusive row-level lock.
        """
        ...

    def record_payment_failure_atomic(
        self,
        booking_id: int,
        gateway_tx_id: Optional[str],
        reason_note: str,
        gateway_payload: Mapping[str, Any],
    ) -> bool:
        """Record a failed or cancelled payment attempt without marking paid."""
        ...
