"""Provider wallet and read-only admin monitoring routes."""

from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.mvp_security import require_approved_provider, require_mvp_role
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPBooking, MVPUser
from app.models.mvp_wallet import ProviderWallet, WalletTransaction
from app.schemas.mvp_wallet import (
    AdminWalletTotals,
    CommissionPreview,
    TopUpRequest,
    TopUpResult,
    TransactionPage,
    TransactionType,
    WalletPage,
    WalletSummary,
)
from app.services import mvp_wallet as service
from app.services.mvp_services import validate_booking_confirmation

router = APIRouter(tags=["MVP Wallet"])
DB = Annotated[Session, Depends(get_mvp_db)]
Provider = Annotated[MVPUser, Depends(require_mvp_role("provider"))]
Admin = Annotated[MVPUser, Depends(require_mvp_role("admin"))]


class Pagination:
    def __init__(
        self,
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(25, ge=1, le=100),
    ):
        self.page = page
        self.page_size = page_size

    def apply(self, query):
        return query.offset((self.page - 1) * self.page_size).limit(self.page_size)

    def result(self, total, items):
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total": total,
            "items": items,
        }


Page = Annotated[Pagination, Depends()]


class TransactionFilters:
    def __init__(
        self,
        transaction_type: TransactionType | None = None,
        date_from: AwareDatetime | None = None,
        date_to: AwareDatetime | None = None,
    ):
        if date_from and date_to and date_from >= date_to:
            raise HTTPException(422, "date_from must precede date_to")
        self.conditions = []
        if transaction_type:
            self.conditions.append(
                WalletTransaction.transaction_type == transaction_type
            )
        if date_from:
            self.conditions.append(
                WalletTransaction.created_at >= self.utc_naive(date_from)
            )
        if date_to:
            self.conditions.append(
                WalletTransaction.created_at < self.utc_naive(date_to)
            )

    @staticmethod
    def utc_naive(value):
        return value.astimezone(timezone.utc).replace(tzinfo=None)


Filters = Annotated[TransactionFilters, Depends()]


def _provider_wallet(db, user):
    profile = require_approved_provider(db, user)
    profile = service.lock_profile(db, profile.id)
    service.require_eligible(profile, user)
    return service.initialize(db, profile.id)


def _wallet_summary(db, wallet):
    return service.summary(wallet, service.totals(db, [wallet.id]).get(wallet.id, {}))


def _admin_wallet(db, provider_id):
    wallet = service.get_wallet(db, provider_id)
    if wallet is None:
        raise HTTPException(404, "Provider wallet not found")
    return wallet


def _transactions(db, wallet, page, filters):
    conditions = [WalletTransaction.wallet_id == wallet.id, *filters.conditions]
    total = db.scalar(
        select(func.count()).select_from(WalletTransaction).where(*conditions)
    )
    query = (
        select(WalletTransaction)
        .where(*conditions)
        .order_by(
            WalletTransaction.created_at.desc(),
            WalletTransaction.id.desc(),
        )
    )
    return page.result(total, db.scalars(page.apply(query)).all())


@router.get("/provider/wallet", response_model=WalletSummary)
def provider_wallet(db: DB, user: Provider):
    result = _wallet_summary(db, _provider_wallet(db, user))
    db.commit()
    return result


@router.post("/provider/wallet/top-up", response_model=TopUpResult)
def top_up(payload: TopUpRequest, db: DB, user: Provider):
    wallet = _provider_wallet(db, user)
    entry = service.top_up(
        db,
        wallet,
        payload.amount,
        payload.request_key,
        payload.simulate_outcome,
    )
    db.commit()
    return {
        "success": entry.simulate_outcome == "success",
        "currency": "LKR",
        "balance": wallet.balance,
        "message": entry.description,
        "transaction": entry,
    }


@router.get("/provider/wallet/transactions", response_model=TransactionPage)
def provider_transactions(db: DB, user: Provider, page: Page, filters: Filters):
    result = _transactions(db, _provider_wallet(db, user), page, filters)
    db.commit()
    return result


@router.get(
    "/provider/bookings/{booking_id}/commission-preview",
    response_model=CommissionPreview,
)
def commission_preview(booking_id: int, db: DB, user: Provider):
    profile = require_approved_provider(db, user)
    service.require_eligible(profile, user)
    booking = db.get(MVPBooking, booking_id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    if booking.listing.provider_id != profile.id:
        raise HTTPException(403, "Only the owning provider can view this booking")
    if booking.listing.category not in service.BOOKABLE:
        raise HTTPException(422, "This service category does not support commission")
    result = service.quote(db, booking, service.get_wallet(db, profile.id))
    allowed = booking.status == "pending" and result["shortfall"] == service.ZERO
    if allowed:
        try:
            validate_booking_confirmation(db, booking, lock=False)
        except HTTPException:
            allowed = False
    return {**result, "confirmation_allowed": allowed, "currency": "LKR"}


@router.get("/admin/wallets/summary", response_model=AdminWalletTotals)
def admin_summary(db: DB, user: Admin):
    count, balances, free_used = db.execute(
        select(
            func.count(ProviderWallet.id),
            func.coalesce(func.sum(ProviderWallet.balance), service.ZERO),
            func.coalesce(func.sum(ProviderWallet.free_bookings_used), 0),
        )
    ).one()
    totals = service.totals(db)
    gross = totals["total_commission_deducted"]
    refunds = totals["total_commission_refunded"]
    return {
        "currency": "LKR",
        "total_provider_wallets": count,
        "total_wallet_balances": balances,
        "total_top_ups": totals["total_top_ups"],
        "gross_commission_collected": gross,
        "commission_refunds": refunds,
        "net_commission_revenue": gross - refunds,
        "total_commission_free_bookings": free_used,
    }


@router.get("/admin/wallets", response_model=WalletPage)
def admin_wallets(db: DB, user: Admin, page: Page):
    total = db.scalar(select(func.count(ProviderWallet.id)))
    wallets = db.scalars(
        page.apply(
            select(ProviderWallet).order_by(
                ProviderWallet.provider_id,
            )
        )
    ).all()
    amounts = service.totals(db, [wallet.id for wallet in wallets]) if wallets else {}
    return page.result(
        total,
        [service.summary(wallet, amounts.get(wallet.id, {})) for wallet in wallets],
    )


@router.get("/admin/wallets/{provider_id}", response_model=WalletSummary)
def admin_wallet(provider_id: int, db: DB, user: Admin):
    return _wallet_summary(db, _admin_wallet(db, provider_id))


@router.get("/admin/wallets/{provider_id}/transactions", response_model=TransactionPage)
def admin_transactions(
    provider_id: int, db: DB, user: Admin, page: Page, filters: Filters
):
    return _transactions(db, _admin_wallet(db, provider_id), page, filters)
