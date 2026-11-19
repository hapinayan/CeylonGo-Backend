from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import select

from app.core.booking_transactions import CAPTURED, lock_booking_resource
from app.core.payhere import payhere_service
from app.models.shared import PaymentTransaction
from app.schemas.payment import PaymentInitiateResponse


def capture_amount(value):
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(400, "Invalid payment amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise HTTPException(400, "Invalid payment amount")
    return amount


def lock_capture(db, payment_id, reference_id):
    """Serialize captured gateway IDs across all booking modules."""
    if not payment_id or not payment_id.strip() or len(payment_id) > 100:
        raise HTTPException(
            400, "Verified payment must include a transaction identifier"
        )
    canonical = payment_id.casefold()
    lock_booking_resource(db, f"payhere_capture_{canonical}")
    if (
        db.scalar(
            select(PaymentTransaction.id)
            .where(
                PaymentTransaction.gateway_name == "payhere",
                PaymentTransaction.gateway_transaction_id == payment_id,
                PaymentTransaction.booking_reference_id != reference_id,
                PaymentTransaction.payment_status.in_(CAPTURED),
            )
            .limit(1)
        )
        is not None
    ):
        raise HTTPException(409, "Payment identifier belongs to another order")


def checkout_response(booking_id, order_id, items, amount, currency, traveler):
    names = traveler.full_name.split(" ", 1)
    return PaymentInitiateResponse(
        booking_id=booking_id,
        booking_reference=order_id,
        merchant_id=payhere_service.merchant_id,
        checkout_url=payhere_service.checkout_url,
        order_id=order_id,
        items=items,
        amount=amount,
        currency=currency,
        hash=payhere_service.generate_checkout_hash(
            order_id=order_id,
            amount=amount,
            currency=currency,
        ),
        first_name=names[0],
        last_name=names[1] if len(names) > 1 else "Traveler",
        email=traveler.email,
        phone=None,
        return_url=payhere_service.return_url,
        cancel_url=payhere_service.cancel_url,
        notify_url=payhere_service.notify_url,
    )
