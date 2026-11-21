from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, extract, func, select

from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.schemas.provider_workspace import FinanceTotals, SettlementTotals


def period_conditions(column, period, *, date_column=False):
    if period.start_date is None:
        return ()
    start, exclusive_end = period.start_date, period.end_date + timedelta(days=1)
    if not date_column:
        start = datetime.combine(start, datetime.min.time())
        exclusive_end = datetime.combine(exclusive_end, datetime.min.time())
    return column >= start, column < exclusive_end


def month_key(column, *, monthly):
    return (extract("year", column), extract("month", column)) if monthly else ()


def _commission(amount):
    return cast(func.round(amount * Decimal("0.10"), 2), Numeric(18, 2))


def _payment_scope(statement, provider_id, owned):
    tx = PaymentTransaction
    return (
        statement.join(
            BookingReference,
            BookingReference.id == tx.booking_reference_id,
        )
        .join(Booking, Booking.id == BookingReference.target_booking_id)
        .join(
            Homestay,
            Homestay.id == Booking.homestay_id,
        )
        .where(
            owned,
            BookingReference.module_type == "homestay",
            BookingReference.provider_profile_id == provider_id,
            BookingReference.user_id == Booking.user_id,
            tx.user_id == Booking.user_id,
            tx.payment_status.in_(("paid", "completed", "refunded")),
            tx.paid_at.is_not(None),
            tx.payment_method.in_(("online", "cash")),
        )
    )


def finance_totals(db, provider_id, owned, period, *, monthly=False):
    tx = PaymentTransaction
    completed_online = (tx.payment_method == "online") & (Booking.status == "completed")
    gross = func.coalesce(tx.gross_amount, tx.amount)
    commission = func.coalesce(tx.commission_amount, _commission(gross))
    keys = month_key(tx.paid_at, monthly=monthly)
    receipts = db.execute(
        _payment_scope(
            select(
                *keys,
                tx.currency,
                func.sum(case((tx.payment_method == "online", gross), else_=0)),
                func.sum(case((tx.payment_method == "cash", tx.amount), else_=0)),
                func.sum(case((completed_online, commission), else_=0)),
                func.sum(
                    case(
                        (
                            completed_online,
                            func.coalesce(
                                tx.provider_payable_amount, gross - commission
                            ),
                        ),
                        else_=0,
                    )
                ),
            ).select_from(tx),
            provider_id,
            owned,
        )
        .where(*period_conditions(tx.paid_at, period))
        .group_by(*keys, tx.currency)
    )

    groups = defaultdict(dict)
    for *month, currency, online, cash, commission_value, payable in receipts:
        groups[tuple(month)][currency] = FinanceTotals(
            currency=currency,
            verified_revenue=online,
            cash_collections=cash,
            commission=commission_value,
            provider_earnings=payable,
        )

    refund = PaymentRefund
    keys = month_key(refund.processed_at, monthly=monthly)
    reversal = _commission(refund.amount)
    refunds = db.execute(
        _payment_scope(
            select(
                *keys,
                tx.currency,
                func.sum(case((tx.payment_method == "online", refund.amount), else_=0)),
                func.sum(case((tx.payment_method == "cash", refund.amount), else_=0)),
                func.sum(case((completed_online, reversal), else_=0)),
                func.sum(case((completed_online, refund.amount - reversal), else_=0)),
            ).select_from(tx),
            provider_id,
            owned,
        )
        .join(refund, refund.payment_transaction_id == tx.id)
        .where(
            refund.refund_status.in_(("processed", "completed")),
            refund.processed_at.is_not(None),
            refund.currency == tx.currency,
            *period_conditions(refund.processed_at, period),
        )
        .group_by(*keys, tx.currency)
    )
    for *month, currency, online, cash, commission_value, payable in refunds:
        bucket = groups[tuple(month)].setdefault(
            currency, FinanceTotals(currency=currency)
        )
        bucket.verified_online_refunds = online
        bucket.verified_cash_refunds = cash
        bucket.commission -= commission_value
        bucket.provider_earnings -= payable

    return {
        key: [buckets[currency] for currency in sorted(buckets)]
        for key, buckets in groups.items()
    }


def settlement_totals(db, provider_id):
    model = ProviderSettlement
    pending = model.settlement_status.in_(("pending", "approved"))
    paid = model.settlement_status.in_(("paid", "completed"))
    rows = db.execute(
        select(
            model.currency,
            func.count(case((pending, 1))).label("pending_count"),
            func.sum(case((pending, model.net_payable), else_=0)).label(
                "pending_amount"
            ),
            func.count(case((paid, 1))).label("paid_count"),
            func.sum(case((paid, model.net_payable), else_=0)).label("paid_amount"),
        )
        .where(
            model.provider_profile_id == provider_id,
            pending | paid,
        )
        .group_by(model.currency)
        .order_by(model.currency)
    ).mappings()
    return [SettlementTotals(**row) for row in rows]
