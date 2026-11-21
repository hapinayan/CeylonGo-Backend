"""
Module 04 — Eco-Tourism Standalone PayHere Payment Preparation Service.
========================================================================
Provides side-effect-free, isolated preparation for PayHere online checkouts
targeting Eco-Tourism bookings (ActivityBooking).

STRICT INTEGRITY RESTRICTIONS:
- ZERO database writes, updates, or commits.
- ZERO external network requests to PayHere APIs.
- ZERO modifications to shared payment code or routers.
- Purely functional, dependency-injected validation and hash generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging
import math
from typing import Any

from app.core.payhere import PayHereService, payhere_service
from app.models.activity_booking import ActivityBooking
from app.models.shared import BookingReference
from app.models.user import User

logger = logging.getLogger(__name__)

SUPPORTED_CURRENCIES: frozenset[str] = frozenset({"LKR"})
ALLOWED_BOOKING_STATUSES: frozenset[str] = frozenset({"pending", "confirmed"})
ALLOWED_PAYMENT_STATUSES: frozenset[str] = frozenset({"unpaid", "pending"})


class EcoPaymentServiceError(ValueError):
    """
    Raised when payment validation or checkout preparation fails.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable error description.
        details: Optional diagnostic dictionary (sanitized of sensitive tokens).
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class EcoPayHereCheckoutPayload:
    """
    Immutable structured payload for initiating a PayHere checkout redirect.
    Matches PayHere HTML form and JavaScript SDK specifications.
    """

    booking_id: int
    booking_reference: str
    merchant_id: str
    checkout_url: str
    order_id: str
    items: str
    amount: Decimal
    currency: str
    hash: str
    first_name: str
    last_name: str
    email: str
    phone: str | None
    return_url: str
    cancel_url: str
    notify_url: str


def validate_checkout_amount(raw_amount: Any, raw_currency: str | None) -> Decimal:
    """
    Authoritative server-side validation for payment amounts and currency.
    - Enforces exact Decimal arithmetic.
    - Strictly rejects zero, negative, NaN, Infinity, and non-numeric amounts.
    - Enforces supported currency allowlist (strictly 'LKR').
    - Prevents floating-point rounding or precision truncation vulnerabilities.
    """
    # 1. Currency validation
    if not raw_currency or not str(raw_currency).strip():
        raise EcoPaymentServiceError(
            code="ECO_UNSUPPORTED_CURRENCY",
            message="Payment currency is unspecified or empty.",
        )

    norm_currency = str(raw_currency).strip().upper()
    if norm_currency not in SUPPORTED_CURRENCIES:
        raise EcoPaymentServiceError(
            code="ECO_UNSUPPORTED_CURRENCY",
            message=f"Currency '{norm_currency}' is not supported. Supported currencies: {sorted(SUPPORTED_CURRENCIES)}.",
            details={"currency": norm_currency},
        )

    # 2. Amount type and parsing
    if raw_amount is None:
        raise EcoPaymentServiceError(
            code="ECO_INVALID_AMOUNT",
            message="Payment amount is unspecified or None.",
        )

    # Reject float NaN / Infinity early
    if isinstance(raw_amount, float):
        if math.isnan(raw_amount) or math.isinf(raw_amount):
            raise EcoPaymentServiceError(
                code="ECO_INVALID_AMOUNT",
                message="Payment amount must be a finite numeric value.",
            )

    try:
        if isinstance(raw_amount, Decimal):
            amount_dec = raw_amount
        else:
            amount_dec = Decimal(str(raw_amount).strip())
    except (InvalidOperation, TypeError, ValueError) as parse_err:
        raise EcoPaymentServiceError(
            code="ECO_INVALID_AMOUNT",
            message=f"Payment amount '{raw_amount}' is not a valid numeric value.",
        ) from parse_err

    # Check for Decimal NaN or Infinity
    if amount_dec.is_nan() or amount_dec.is_infinite():
        raise EcoPaymentServiceError(
            code="ECO_INVALID_AMOUNT",
            message="Payment amount must be a finite numeric value.",
        )

    # 3. Positive amount validation
    if amount_dec <= Decimal("0.00"):
        raise EcoPaymentServiceError(
            code="ECO_INVALID_AMOUNT",
            message=f"Payment amount must be strictly greater than zero, received '{amount_dec}'.",
            details={"amount": str(amount_dec)},
        )

    return amount_dec.quantize(Decimal("0.01"))


def validate_booking_for_checkout(
    booking: ActivityBooking | None,
    current_user: User | None,
    booking_ref: BookingReference | None,
) -> None:
    """
    Side-effect-free validation of booking state, traveler authorization,
    payment method, and reference integrity.
    """
    # 1. Booking existence
    if booking is None:
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_NOT_FOUND",
            message="Booking record not found.",
        )

    # 2. Traveler authentication & ownership
    if current_user is None:
        raise EcoPaymentServiceError(
            code="ECO_UNAUTHORIZED",
            message="Authentication is required to initiate payment.",
        )

    user_role = getattr(current_user, "role", "traveler")
    user_id = getattr(current_user, "id", None)

    if booking.user_id != user_id and user_role != "admin":
        raise EcoPaymentServiceError(
            code="ECO_PAYMENT_UNAUTHORIZED",
            message="You are not authorized to initiate payment for this booking.",
            details={"booking_id": booking.id, "user_id": user_id},
        )

    # 3. Booking lifecycle status
    b_status = (booking.booking_status or "").lower()
    if b_status == "cancelled":
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_CANCELLED",
            message="Cannot initiate payment for a cancelled booking.",
            details={"booking_status": booking.booking_status},
        )

    if b_status == "rejected":
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_REJECTED",
            message="Cannot initiate payment for a rejected booking.",
            details={"booking_status": booking.booking_status},
        )

    if b_status not in ALLOWED_BOOKING_STATUSES:
        raise EcoPaymentServiceError(
            code="ECO_INVALID_BOOKING_STATUS",
            message=f"Cannot initiate payment for booking with status '{booking.booking_status}'.",
            details={"booking_status": booking.booking_status},
        )

    # 4. Payment method validation
    p_method = (booking.payment_method or "").lower()
    if p_method != "online":
        raise EcoPaymentServiceError(
            code="ECO_INVALID_PAYMENT_METHOD",
            message=(
                f"Online payment cannot be initiated for booking with payment method '{booking.payment_method}'. "
                "Only 'online' bookings support PayHere checkout."
            ),
            details={"payment_method": booking.payment_method},
        )

    # 5. Payment lifecycle status
    p_status = (booking.payment_status or "").lower()
    if p_status == "paid":
        raise EcoPaymentServiceError(
            code="ECO_PAYMENT_ALREADY_PAID",
            message="This booking has already been paid.",
            details={"payment_status": booking.payment_status},
        )

    if p_status not in ALLOWED_PAYMENT_STATUSES:
        raise EcoPaymentServiceError(
            code="ECO_INVALID_PAYMENT_STATUS",
            message=f"Cannot initiate payment for booking with payment status '{booking.payment_status}'.",
            details={"payment_status": booking.payment_status},
        )

    # 6. Booking reference integrity
    if booking_ref is None or not getattr(booking_ref, "booking_reference", None):
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_REFERENCE_MISSING",
            message="Authoritative booking reference record is missing.",
            details={"booking_id": booking.id},
        )

    ref_str = str(booking_ref.booking_reference).strip()
    if not ref_str:
        raise EcoPaymentServiceError(
            code="ECO_BOOKING_REFERENCE_MISSING",
            message="Authoritative booking reference code is empty.",
            details={"booking_id": booking.id},
        )


def prepare_eco_payhere_checkout(
    booking: ActivityBooking | None,
    current_user: User | None,
    booking_ref: BookingReference | None,
    experience_title: str | None = None,
    payhere_svc: PayHereService | None = None,
    client_amount_override: Any = None,  # Explicitly rejected if conflicting
) -> EcoPayHereCheckoutPayload:
    """
    Prepare a verified PayHere checkout payload for an Eco-Tourism booking.

    Guarantees:
    - Zero database mutations.
    - Authoritative server-side pricing from booking.total_price.
    - Explicit rejection of client-supplied amount overrides.
    - Validates PayHere merchant credentials existence without logging secrets.
    - Reuses canonical payhere_service for hash generation and URL endpoints.
    """
    # 1. Validate booking and permissions
    validate_booking_for_checkout(booking, current_user, booking_ref)

    # Assert non-null for type checkers after validation
    assert booking is not None
    assert current_user is not None
    assert booking_ref is not None

    # 2. Authoritative amount validation
    server_amount = validate_checkout_amount(booking.total_price, booking.currency)

    # Guard: Reject client tampering if an override was submitted
    if client_amount_override is not None:
        try:
            client_dec = Decimal(str(client_amount_override).strip()).quantize(Decimal("0.01"))
            if client_dec != server_amount:
                raise EcoPaymentServiceError(
                    code="ECO_AMOUNT_TAMPERING_REJECTED",
                    message="Client-submitted amount does not match authoritative booking price.",
                    details={
                        "server_amount": str(server_amount),
                        "client_amount": str(client_dec),
                    },
                )
        except (InvalidOperation, TypeError, ValueError) as err:
            raise EcoPaymentServiceError(
                code="ECO_AMOUNT_TAMPERING_REJECTED",
                message="Client-submitted amount is invalid.",
            ) from err

    # 3. PayHere service & configuration validation
    svc = payhere_svc or payhere_service
    merchant_id = getattr(svc, "merchant_id", None)
    merchant_secret = getattr(svc, "merchant_secret", None)

    if not merchant_id or not str(merchant_id).strip():
        raise EcoPaymentServiceError(
            code="ECO_PAYHERE_CONFIG_MISSING",
            message="PayHere merchant_id is not configured in environment.",
        )

    if not merchant_secret or not str(merchant_secret).strip():
        raise EcoPaymentServiceError(
            code="ECO_PAYHERE_CONFIG_MISSING",
            message="PayHere merchant_secret is not configured in environment.",
        )

    order_id = str(booking_ref.booking_reference).strip()
    currency = str(booking.currency).strip().upper()

    # 4. Canonical PayHere hash generation
    checkout_hash = svc.generate_checkout_hash(
        order_id=order_id,
        amount=server_amount,
        currency=currency,
    )

    # 5. Customer name resolution
    full_name = getattr(current_user, "full_name", "") or "Traveler"
    name_parts = full_name.strip().split(" ", 1)
    first_name = name_parts[0] if name_parts else "Traveler"
    last_name = name_parts[1] if len(name_parts) > 1 else "Traveler"

    # 6. Items description formatting
    title = experience_title or f"Eco Experience #{booking.eco_experience_id or booking.id}"
    items_desc = f"Eco Experience: {title}"

    return EcoPayHereCheckoutPayload(
        booking_id=booking.id,
        booking_reference=order_id,
        merchant_id=str(merchant_id).strip(),
        checkout_url=svc.checkout_url,
        order_id=order_id,
        items=items_desc,
        amount=server_amount,
        currency=currency,
        hash=checkout_hash,
        first_name=first_name,
        last_name=last_name,
        email=getattr(current_user, "email", ""),
        phone=getattr(current_user, "phone", None),
        return_url=svc.return_url,
        cancel_url=svc.cancel_url,
        notify_url=svc.notify_url,
    )
