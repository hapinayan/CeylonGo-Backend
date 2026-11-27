"""Provider wallet business rules and reporting."""

from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import case, exists, func, or_, select, update

from app.models.mvp import (
    MVPBooking,
    MVPBookingStatusHistory,
    MVPListing,
    MVPProviderProfile,
)
from app.models.mvp_wallet import ProviderWallet, WalletTransaction, utc_now

RATE = Decimal("0.10")
ZERO = Decimal("0.00")
FREE_LIMIT = 3
BOOKABLE = ("homestay", "adventure", "guide", "bike", "bicycle")


def lock_profile(db, provider_id):
    """Serialize wallet writes even while a provider has no wallet row."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(
            update(MVPProviderProfile)
            .where(
                MVPProviderProfile.id == provider_id,
            )
            .values(updated_at=MVPProviderProfile.updated_at)
        )
    return db.scalar(
        select(MVPProviderProfile)
        .where(
            MVPProviderProfile.id == provider_id,
        )
        .with_hint(
            MVPProviderProfile,
            "WITH (UPDLOCK, HOLDLOCK)",
            dialect_name="mssql",
        )
        .execution_options(populate_existing=True)
    )


def require_eligible(profile, actor):
    if (
        profile is None
        or actor.role != "provider"
        or not actor.is_active
        or profile.user_id != actor.id
        or profile.verification_status != "approved"
    ):
        raise HTTPException(403, "An active approved provider account is required")


def historical_usage(db, provider_id):
    confirmed_history = exists().where(
        MVPBookingStatusHistory.booking_id == MVPBooking.id,
        MVPBookingStatusHistory.to_status == "confirmed",
    )
    count = db.scalar(
        select(func.count())
        .select_from(MVPBooking)
        .join(MVPListing)
        .where(
            MVPListing.provider_id == provider_id,
            MVPListing.category.in_(BOOKABLE),
            or_(MVPBooking.status.in_(("confirmed", "completed")), confirmed_history),
        )
    )
    return min(count, FREE_LIMIT)


def get_wallet(db, provider_id, *, lock=False):
    query = select(ProviderWallet).where(ProviderWallet.provider_id == provider_id)
    if lock:
        query = query.with_hint(
            ProviderWallet,
            "WITH (UPDLOCK, HOLDLOCK)",
            dialect_name="mssql",
        ).execution_options(populate_existing=True)
    return db.scalar(query)


def initialize(db, provider_id):
    """Caller holds the provider profile lock. Changes belong to its transaction."""
    wallet = get_wallet(db, provider_id, lock=True)
    if wallet is None:
        wallet = ProviderWallet(
            provider_id=provider_id,
            balance=ZERO,
            free_bookings_used=historical_usage(db, provider_id),
        )
        db.add(wallet)
        db.flush()
    return wallet


def quote(db, booking, wallet):
    used = (
        wallet.free_bookings_used
        if wallet
        else historical_usage(
            db,
            booking.listing.provider_id,
        )
    )
    balance = wallet.balance if wallet else ZERO
    free = booking.status == "pending" and used < FREE_LIMIT
    commission = (
        ZERO
        if free
        else (booking.total_amount * RATE).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    )
    return {
        "booking_amount": booking.total_amount,
        "commission_rate": RATE,
        "required_commission": commission,
        "free_booking_eligible": free,
        "wallet_balance": balance,
        "shortfall": max(ZERO, commission - balance),
    }


def record(db, wallet, kind, amount, balance_after, description, **fields):
    entry = WalletTransaction(
        wallet_id=wallet.id,
        transaction_type=kind,
        amount=amount,
        balance_before=wallet.balance,
        balance_after=balance_after,
        description=description,
        **fields,
    )
    wallet.balance = balance_after
    if balance_after != entry.balance_before or kind == "free_booking":
        wallet.updated_at = utc_now()
    db.add(entry)
    return entry


def charge(db, booking, wallet):
    details = quote(db, booking, wallet)
    amount = details["required_commission"]
    fields = {"booking_id": booking.id, "booking_amount": booking.total_amount}
    if details["shortfall"] > ZERO:
        raise HTTPException(
            409,
            {
                "code": "insufficient_wallet_balance",
                "message": "Top up your wallet before confirming this booking.",
                "currency": "LKR",
                "current_balance": f"{wallet.balance:.2f}",
                "required_commission": str(amount),
                "shortfall": str(details["shortfall"]),
            },
        )
    if details["free_booking_eligible"]:
        wallet.free_bookings_used += 1
        record(
            db,
            wallet,
            "free_booking",
            ZERO,
            wallet.balance,
            "Commission-free booking confirmed",
            commission_rate=ZERO,
            **fields,
        )
        return "free_booking"
    record(
        db,
        wallet,
        "commission_deduction",
        amount,
        wallet.balance - amount,
        "Booking commission deducted",
        commission_rate=RATE,
        **fields,
    )
    return "commission_deduction"


def refund(db, booking):
    deduction = db.scalar(
        select(WalletTransaction).where(
            WalletTransaction.booking_id == booking.id,
            WalletTransaction.transaction_type == "commission_deduction",
        )
    )
    if deduction is None:
        return None
    lock_profile(db, booking.listing.provider_id)
    wallet = get_wallet(db, booking.listing.provider_id, lock=True)
    record(
        db,
        wallet,
        "commission_refund",
        deduction.amount,
        wallet.balance + deduction.amount,
        "Booking commission refunded",
        booking_id=booking.id,
        deduction_id=deduction.id,
        booking_amount=deduction.booking_amount,
        commission_rate=deduction.commission_rate,
    )
    return "commission_refund"


def top_up(db, wallet, amount, request_key, outcome):
    prior = db.scalar(
        select(WalletTransaction).where(
            WalletTransaction.wallet_id == wallet.id,
            WalletTransaction.request_key == request_key,
        )
    )
    if prior:
        if prior.amount != amount or prior.simulate_outcome != outcome:
            raise HTTPException(
                409, "Request key is already used for a different top-up"
            )
        return prior
    succeeded = outcome == "success"
    return record(
        db,
        wallet,
        "top_up",
        amount,
        wallet.balance + amount if succeeded else wallet.balance,
        "Simulated top-up succeeded" if succeeded else "Simulated top-up failed",
        request_key=request_key,
        simulate_outcome=outcome,
    )


def totals(db, wallet_ids=None):
    transaction = WalletTransaction
    kind = transaction.transaction_type
    kinds = {
        "total_top_ups": (kind == "top_up")
        & (transaction.simulate_outcome == "success"),
        "total_commission_deducted": kind == "commission_deduction",
        "total_commission_refunded": kind == "commission_refund",
    }
    query = select(
        *(
            func.coalesce(
                func.sum(
                    case(
                        (condition, transaction.amount),
                        else_=ZERO,
                    )
                ),
                ZERO,
            ).label(label)
            for label, condition in kinds.items()
        )
    )
    if wallet_ids is not None:
        query = (
            query.add_columns(transaction.wallet_id)
            .where(transaction.wallet_id.in_(wallet_ids))
            .group_by(transaction.wallet_id)
        )
        return {row.wallet_id: dict(row._mapping) for row in db.execute(query)}
    return dict(db.execute(query).one()._mapping)


def summary(wallet, amounts):
    topped_up = amounts.get("total_top_ups", ZERO)
    deducted = amounts.get("total_commission_deducted", ZERO)
    refunded = amounts.get("total_commission_refunded", ZERO)
    return {
        "provider_id": wallet.provider_id,
        "balance": wallet.balance,
        "currency": "LKR",
        "commission_rate": RATE,
        "free_bookings_used": wallet.free_bookings_used,
        "free_bookings_remaining": FREE_LIMIT - wallet.free_bookings_used,
        "total_top_ups": topped_up,
        "total_commission_deducted": deducted,
        "total_commission_refunded": refunded,
        "net_commission_paid": deducted - refunded,
    }
