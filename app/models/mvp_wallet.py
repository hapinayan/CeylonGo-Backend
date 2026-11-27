"""Wallet and transaction ledger for the Academic MVP."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Unicode,
    Uuid,
    text,
)
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mvp import MVPBase, PK_TYPE

UTC_TIMESTAMP = DateTime().with_variant(DATETIME2(precision=3), "mssql")
MONEY = Numeric(18, 2)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProviderWallet(MVPBase):
    __tablename__ = "provider_wallets"
    __table_args__ = (
        CheckConstraint("balance >= 0", name="ck_provider_wallet_balance"),
        CheckConstraint(
            "free_bookings_used BETWEEN 0 AND 3", name="ck_wallet_free_used"
        ),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("provider_profiles.id"),
        unique=True,
    )
    balance: Mapped[Decimal] = mapped_column(MONEY, server_default=text("0"))
    free_bookings_used: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(UTC_TIMESTAMP, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTC_TIMESTAMP,
        default=utc_now,
        onupdate=utc_now,
    )


class WalletTransaction(MVPBase):
    __tablename__ = "wallet_transactions"
    __table_args__ = (
        CheckConstraint(
            "amount >= 0 AND balance_before >= 0 AND balance_after >= 0",
            name="ck_wallet_transaction_amounts",
        ),
        CheckConstraint(
            """(transaction_type = 'top_up' AND booking_id IS NULL
                AND request_key IS NOT NULL
                AND amount > 0 AND booking_amount IS NULL AND commission_rate IS NULL
                AND deduction_id IS NULL AND simulate_outcome IS NOT NULL
                AND ((simulate_outcome = 'success'
                      AND ROUND(balance_after - balance_before, 2) = amount)
                  OR (simulate_outcome = 'failure' AND balance_after = balance_before)))
            OR (booking_id IS NOT NULL AND request_key IS NULL
                AND booking_amount IS NOT NULL
                AND booking_amount >= 0 AND commission_rate IS NOT NULL
                AND simulate_outcome IS NULL AND (
                    (transaction_type = 'free_booking' AND amount = 0
                        AND commission_rate = 0
                        AND balance_before = balance_after AND deduction_id IS NULL)
                 OR (transaction_type = 'commission_deduction'
                        AND commission_rate = 0.10
                        AND ROUND(balance_before - balance_after, 2) = amount
                        AND deduction_id IS NULL)
                 OR (transaction_type = 'commission_refund' AND commission_rate = 0.10
                        AND ROUND(balance_after - balance_before, 2) = amount
                        AND deduction_id IS NOT NULL)
                ))""",
            name="ck_wallet_transaction_shape",
        ),
        Index("ix_wallet_tx_history", "wallet_id", "created_at", "id"),
        Index(
            "ux_wallet_tx_request",
            "wallet_id",
            "request_key",
            unique=True,
            mssql_where=text("request_key IS NOT NULL"),
            sqlite_where=text("request_key IS NOT NULL"),
        ),
        Index(
            "ux_wallet_tx_booking_type",
            "booking_id",
            "transaction_type",
            unique=True,
            mssql_where=text("booking_id IS NOT NULL"),
            sqlite_where=text("booking_id IS NOT NULL"),
        ),
        Index(
            "ux_wallet_tx_refund_deduction",
            "deduction_id",
            unique=True,
            mssql_where=text("deduction_id IS NOT NULL"),
            sqlite_where=text("deduction_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(PK_TYPE, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("provider_wallets.id")
    )
    booking_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("bookings.id")
    )
    deduction_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wallet_transactions.id"),
    )
    transaction_type: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    balance_before: Mapped[Decimal] = mapped_column(MONEY)
    balance_after: Mapped[Decimal] = mapped_column(MONEY)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    booking_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    request_key: Mapped[UUID | None] = mapped_column(Uuid())
    simulate_outcome: Mapped[str | None] = mapped_column(String(7))
    description: Mapped[str] = mapped_column(Unicode(200))
    created_at: Mapped[datetime] = mapped_column(UTC_TIMESTAMP, default=utc_now)
