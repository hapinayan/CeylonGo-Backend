"""Read-only reporting over canonical native Eco booking references."""

import os
from decimal import Decimal

from sqlalchemy import case, false, func, or_, select

from app.core.booking_transactions import CAPTURED, EXECUTED_REFUNDS
from app.models.eco_reservation import (
    EcoAvailabilitySlot, EcoRefundReconciliation, EcoReservation,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentRefund, PaymentTransaction, ProviderSettlement
from app.services.eco_reservations import MODULE, online_attempt_exists, reservation_query

ZERO = Decimal("0.00")
UNAPPLIED_CAPTURE_REASON = "Late or conflicting Eco capture"


def scope(
    *, provider_id=None, status=None, payment_status=None, payment_method=None,
    start=None, end=None,
):
    query = reservation_query(provider_id=provider_id, admin=provider_id is None)
    if status:
        query = query.where(EcoReservation.status == status)
    if payment_status:
        query = query.where(BookingReference.payment_status == payment_status)
    if payment_method:
        if payment_method == "online":
            query = query.where(online_attempt_exists(BookingReference.id))
        elif payment_method == "pay_at_location":
            query = query.where(~online_attempt_exists(BookingReference.id))
        else:
            query = query.where(false())
    if start:
        query = query.where(EcoReservation.created_at >= start)
    if end:
        query = query.where(EcoReservation.created_at <= end)
    return query


def empty_amounts():
    return dict.fromkeys((
        "captured_online", "unapplied_captures", "pending_refunds",
        "verified_refunds", "verified_applied_refunds", "cash_receipts",
        "recorded_online_commission",
    ), ZERO)


def ledger(db, references, *, include_states=False):
    """Aggregate per reference with one applied online capture and cash receipt."""
    results = {}
    candidates = db.execute(
        select(PaymentTransaction, BookingReference)
        .join(BookingReference, PaymentTransaction.booking_reference_id == BookingReference.id)
        .join(EcoReservation, EcoReservation.id == BookingReference.target_booking_id)
        .where(
            BookingReference.id.in_(references),
            BookingReference.module_type == MODULE,
            PaymentTransaction.user_id == BookingReference.user_id,
            PaymentTransaction.currency == EcoReservation.currency,
            BookingReference.currency == EcoReservation.currency,
            PaymentTransaction.amount == EcoReservation.total_price,
            BookingReference.total_price == EcoReservation.total_price,
            PaymentTransaction.payment_status.in_((*CAPTURED, "refunded")),
            PaymentTransaction.gateway_transaction_id.is_not(None),
            PaymentTransaction.paid_at.is_not(None),
        )
        .order_by(PaymentTransaction.id)
    ).all()
    # Load refunds separately so multiple refund rows cannot multiply capture volume.
    refund_query = (
        select(PaymentRefund)
        .join(PaymentTransaction, PaymentRefund.payment_transaction_id == PaymentTransaction.id)
        .where(PaymentTransaction.booking_reference_id.in_(references))
        .order_by(PaymentRefund.id)
    )
    refunds = {}
    for refund in db.scalars(refund_query):
        refunds.setdefault(refund.payment_transaction_id, []).append(refund)
    states = {}
    if include_states and os.getenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED") == "1":
        refund_ids = refund_query.with_only_columns(PaymentRefund.id).order_by(None)
        states = dict(db.execute(select(
            EcoRefundReconciliation.refund_id, EcoRefundReconciliation.state,
        ).where(EcoRefundReconciliation.refund_id.in_(refund_ids))).all())
    seen_captures = set()
    for tx, ref in candidates:
        amounts, details = results.setdefault(ref.id, (empty_amounts(), []))
        if tx.gateway_name == "cash_on_arrival" and tx.payment_method == "cash":
            if not amounts["cash_receipts"] and ref.payment_status == "paid":
                amounts["cash_receipts"] = tx.amount
            continue
        if tx.gateway_name != "payhere" or tx.payment_method != "online":
            continue
        capture_key = (ref.id, tx.gateway_transaction_id.casefold())
        if capture_key in seen_captures:
            continue
        seen_captures.add(capture_key)
        rows = [r for r in refunds.get(tx.id, ()) if r.currency == tx.currency and r.amount > ZERO]
        unapplied = any(r.refund_reason == UNAPPLIED_CAPTURE_REASON for r in rows)
        if unapplied or amounts["captured_online"]:
            amounts["unapplied_captures"] += tx.amount
            unapplied = True
        else:
            amounts["captured_online"] += tx.amount
            amounts["recorded_online_commission"] += tx.commission_amount or ZERO
        verified = min(tx.amount, sum(
            (r.amount for r in rows if r.refund_status in EXECUTED_REFUNDS), ZERO
        ))
        pending = min(tx.amount - verified, sum(
            (r.amount for r in rows if r.refund_status in ("pending", "approved")), ZERO
        ))
        amounts["verified_refunds"] += verified
        amounts["pending_refunds"] += pending
        if not unapplied:
            amounts["verified_applied_refunds"] += verified
        if include_states:
            details.extend({
                "id": r.id, "amount": r.amount,
                "state": states.get(
                    r.id,
                    "reconciliation_required"
                    if unapplied and r.refund_status == "pending" else r.refund_status,
                ),
            } for r in rows)
    return results


def totals(db, query):
    scoped = query.with_only_columns(
        BookingReference.id, BookingReference.currency,
        maintain_column_froms=True,
    ).subquery()
    results = ledger(db, select(scoped.c.id))
    currencies = {}
    for ref_id, currency in db.execute(select(scoped)):
        group = currencies.setdefault(currency, {
            "currency": currency, "bookings": 0, "paid_bookings": 0, **empty_amounts(),
        })
        group["bookings"] += 1
        if ref_id in results:
            amounts, _ = results[ref_id]
            if amounts["captured_online"] or amounts["cash_receipts"]:
                group["paid_bookings"] += 1
            for key, value in amounts.items():
                group[key] += value
    return [currencies[currency] for currency in sorted(currencies)]


def settlement_totals(db, provider_id=None):
    """Provider-level settlement balances grouped by currency and ledger state."""
    entry = ProviderSettlement
    paid = entry.settlement_status.in_(("paid", "completed")) & or_(
        entry.paid_at.is_not(None),
        func.nullif(func.trim(entry.payout_reference), "").is_not(None),
    )
    query = (
        select(
            entry.currency,
            func.sum(case((paid, entry.net_payable), else_=ZERO)).label("disbursed"),
            func.sum(case(
                (entry.settlement_status.in_(("pending", "approved")), entry.net_payable),
                else_=ZERO,
            )).label("pending"),
            func.sum(case(
                (entry.settlement_status == "on_hold", entry.net_payable), else_=ZERO,
            )).label("on_hold"),
            func.sum(entry.platform_fee).label("recorded_platform_fees"),
        )
        .join(ProviderProfile, ProviderProfile.id == entry.provider_profile_id)
        .where(ProviderProfile.provider_type == "eco_tourism")
        .group_by(entry.currency)
        .order_by(entry.currency)
    )
    if provider_id is not None:
        query = query.where(entry.provider_profile_id == provider_id)
    return list(db.execute(query).mappings())


def page(db, query, page_number, page_size):
    count = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(
        EcoReservation.created_at.desc(), EcoReservation.id.desc(),
    ).offset((page_number - 1) * page_size).limit(page_size)).all()
    entries = ledger(db, [ref.id for _, _, ref, _ in rows], include_states=True)
    items = []
    for reservation, slot, ref, online in rows:
        amounts, refunds = entries.get(ref.id, (empty_amounts(), []))
        items.append({
            "id": reservation.id, "booking_reference": ref.booking_reference,
            "experience_id": slot.eco_experience_id, "provider_id": ref.provider_profile_id,
            "traveler_id": reservation.traveler_id, "status": reservation.status,
            "payment_status": ref.payment_status, "currency": ref.currency,
            "payment_method": "online" if online else "pay_at_location",
            "created_at": reservation.created_at, "cancelled_at": reservation.cancelled_at,
            "total_price": reservation.total_price, "finance": amounts, "refunds": refunds,
        })
    return {"items": items, "total": count, "page": page_number, "page_size": page_size}
