from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.mssql import dialect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPAvailabilitySlot,
    MVPBase,
    MVPBooking,
    MVPBookingAllocation,
    MVPBookingPayment,
    MVPBookingStatusHistory,
    MVPInventoryUnit,
    MVPListing,
    MVPProviderProfile,
    MVPUser,
)
from app.models.mvp_wallet import ProviderWallet, WalletTransaction
from app.schemas.mvp import AdminProviderDecision
from app.services import mvp_account
from app.services.mvp_services import MVPAdminService
from scripts.bootstrap_mvp_schema import UPGRADES

BASE = "/api/v1"
WALLET = BASE + "/provider/wallet"


@pytest.fixture
def harness(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'wallet.sqlite'}")
    MVPBase.metadata.create_all(engine)
    sessions = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    app = create_mvp_app()

    def test_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_mvp_db] = test_db
    headers = {}
    profiles = {}
    with sessions() as db:
        for name, role in (
            ("provider", "provider"),
            ("other", "provider"),
            ("traveler", "traveler"),
            ("admin", "admin"),
            ("pending", "provider"),
        ):
            user = MVPUser(
                email=f"{name}@example.test",
                full_name=name,
                hashed_password="unused",
                role=role,
            )
            db.add(user)
            db.flush()
            token = create_mvp_access_token(
                user.id, user.email, user.role, user.auth_version
            )
            headers[name] = {"Authorization": f"Bearer {token}"}
            if role == "provider":
                profile = MVPProviderProfile(
                    user_id=user.id,
                    business_name=name,
                    phone="0771234567",
                    district="Kandy",
                    verification_status="pending" if name == "pending" else "approved",
                )
                db.add(profile)
                db.flush()
                profiles[name] = profile.id
        db.commit()
        traveler_id = db.scalar(select(MVPUser.id).where(MVPUser.role == "traveler"))

    def booking(
        category="adventure",
        *,
        method="at_location",
        state="pending",
        history=False,
        total="100.05",
    ):
        with sessions() as db:
            listing = MVPListing(
                provider_id=profiles["provider"],
                category=category,
                title="Listing",
                description="Listing description",
                district="Kandy",
                locality="Kandy",
                price=Decimal(total),
                approval_status="approved",
                is_active=True,
            )
            db.add(listing)
            db.flush()
            unit = MVPInventoryUnit(
                listing_id=listing.id,
                provider_id=listing.provider_id,
                label="Unit",
            )
            db.add(unit)
            db.flush()
            starts = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
                days=20
            )
            ends = starts + timedelta(days=1)
            slot = MVPAvailabilitySlot(
                unit_id=unit.id,
                listing_id=listing.id,
                starts_at=starts,
                ends_at=ends,
                capacity=1,
            )
            row = MVPBooking(
                listing_id=listing.id,
                traveler_id=traveler_id,
                quantity=1,
                starts_at=starts,
                ends_at=ends,
                total_amount=Decimal(total),
                status=state,
                request_key=str(uuid4()),
                request_hash=b"x" * 32,
            )
            db.add_all([slot, row])
            db.flush()
            db.add(
                MVPBookingAllocation(
                    booking_id=row.id,
                    slot_id=slot.id,
                    listing_id=listing.id,
                    quantity=1,
                )
            )
            if method:
                db.add(
                    MVPBookingPayment(
                        booking_id=row.id,
                        request_key=str(uuid4()),
                        request_hash=b"x" * 32,
                        method=method,
                        status="unpaid"
                        if method == "at_location"
                        else "demo_succeeded",
                        attempt_no=1,
                        display_amount=row.total_amount,
                        currency="LKR",
                    )
                )
            if history:
                db.add(
                    MVPBookingStatusHistory(
                        booking_id=row.id,
                        actor_user_id=traveler_id,
                        from_status="pending",
                        to_status="confirmed",
                    )
                )
            db.commit()
            return row.id

    with TestClient(app) as client:
        yield client, sessions, headers, profiles, booking
    engine.dispose()


def decision(client, headers, booking_id, target="confirmed"):
    return client.patch(
        f"{BASE}/bookings/{booking_id}/status",
        json={"to_status": target},
        headers=headers,
    )


def top_up(client, headers, key=None, *, amount="1000.00", outcome="success"):
    return client.post(
        WALLET + "/top-up",
        headers=headers,
        json={
            "request_key": str(key or uuid4()),
            "amount": amount,
            "simulate_outcome": outcome,
        },
    )


def test_history_and_approval_initialize_wallet_once(harness):
    client, sessions, headers, profiles, booking = harness
    booking("bike", state="confirmed", history=True)
    booking("guide", state="cancelled", history=True)
    booking("adventure", state="rejected")
    result = client.get(WALLET, headers=headers["provider"]).json()
    assert result["free_bookings_used"] == 2
    assert Decimal(result["balance"]) == 0
    assert (
        client.get(WALLET + "/transactions", headers=headers["provider"]).json()[
            "total"
        ]
        == 0
    )
    assert client.get(WALLET, headers=headers["provider"]).json() == result

    assert client.get(WALLET, headers=headers["pending"]).status_code == 403
    with sessions() as db:
        admin = db.scalar(select(MVPUser).where(MVPUser.role == "admin"))
        MVPAdminService.decide_provider(
            db,
            admin,
            profiles["pending"],
            AdminProviderDecision(verification_status="approved"),
        )
        wallet = db.scalar(
            select(ProviderWallet).filter_by(provider_id=profiles["pending"])
        )
        assert wallet.balance == wallet.free_bookings_used == 0


def test_free_bookings_shared_then_charge_or_reject_and_refund(harness):
    client, sessions, headers, profiles, booking = harness
    provider = headers["provider"]
    for category in ("homestay", "adventure", "guide"):
        booking_id = booking(category)
        accepted = decision(client, provider, booking_id)
        assert accepted.status_code == 200, accepted.text
        assert accepted.headers["X-Wallet-Event"] == "free_booking"
    assert decision(client, provider, booking_id, "cancelled").status_code == 200

    booking_id = booking("bike", method="simulated")
    preview_url = f"{BASE}/provider/bookings/{booking_id}/commission-preview"
    preview = client.get(preview_url, headers=provider).json()
    assert preview["required_commission"] == preview["shortfall"] == "10.01"
    assert not preview["confirmation_allowed"]
    denied = decision(client, provider, booking_id)
    assert denied.status_code == 409, denied.text
    assert denied.json()["detail"]["code"] == "insufficient_wallet_balance"
    with sessions() as db:
        row = db.get(MVPBooking, booking_id)
        assert row.status == "pending" and row.allocations[0].released_at is None
        assert row.payments[0].status == "demo_succeeded"
    assert top_up(client, provider).json()["success"]
    assert client.get(preview_url, headers=provider).json()["confirmation_allowed"]
    accepted = decision(client, provider, booking_id)
    assert accepted.status_code == 200, accepted.text
    assert accepted.headers["X-Wallet-Event"] == "commission_deduction"
    assert decision(client, provider, booking_id).status_code == 400
    assert client.get(WALLET, headers=provider).json()["balance"] == "989.99"

    cancelled = decision(client, headers["traveler"], booking_id, "cancelled")
    assert cancelled.status_code == 200, cancelled.text
    assert "X-Wallet-Event" not in cancelled.headers
    assert decision(client, provider, booking_id, "cancelled").status_code == 400
    refund = client.get(
        WALLET + "/transactions",
        headers=provider,
        params={
            "transaction_type": "commission_refund",
        },
    ).json()
    assert refund["total"] == 1 and refund["items"][0]["deduction_id"] is not None
    assert client.get(WALLET, headers=provider).json()["balance"] == "1000.00"
    assert (
        decision(client, provider, booking("bicycle"), "cancelled").status_code == 200
    )
    assert client.get(WALLET + "/transactions", headers=provider).json()["total"] == 6


def test_simulated_top_up_idempotency_and_admin_read_only(harness):
    client, _, headers, profiles, _ = harness
    provider, admin = headers["provider"], headers["admin"]
    failed_key, success_key = uuid4(), uuid4()
    failed = top_up(client, provider, failed_key, outcome="failure").json()
    assert not failed["success"] and failed["balance"] == "0.00"
    replay = top_up(client, provider, failed_key, outcome="failure").json()
    assert failed == replay
    assert top_up(client, provider, failed_key).status_code == 409
    successful = top_up(client, provider, success_key).json()
    assert successful["success"] and successful["transaction"]["request_key"] == str(
        success_key
    )
    assert top_up(client, provider, success_key).json() == successful
    assert top_up(client, provider, success_key, amount="2500.00").status_code == 409
    assert top_up(client, provider, amount="1.001").status_code == 422
    assert (
        top_up(client, provider, str(success_key).replace("-", "")).status_code == 422
    )
    admin_url = BASE + "/admin/wallets"
    assert client.get(admin_url, headers=provider).status_code == 403
    assert client.post(admin_url, headers=admin).status_code == 405
    assert (
        client.get(admin_url + "/summary", headers=admin).json()["total_top_ups"]
        == "1000.00"
    )
    assert (
        client.get(admin_url, headers=admin, params={"page_size": 1}).json()["total"]
        == 1
    )
    details = admin_url + f"/{profiles['provider']}"
    assert client.get(details, headers=admin).json()["balance"] == "1000.00"
    assert client.get(details + "/transactions", headers=admin).json()["total"] == 2
    assert (
        client.get(
            details + "/transactions",
            headers=admin,
            params={
                "date_from": "2099-01-01T00:00:00Z",
            },
        ).json()["total"]
        == 0
    )


def test_authorization_and_rollback(harness, monkeypatch):
    client, sessions, headers, profiles, booking = harness
    booking_id = booking()
    preview = f"{BASE}/provider/bookings/{booking_id}/commission-preview"
    assert client.get(WALLET, headers=headers["traveler"]).status_code == 403
    assert client.get(preview, headers=headers["other"]).status_code == 403
    assert decision(client, headers["other"], booking_id).status_code == 403
    assert decision(client, headers["admin"], booking_id).status_code == 403
    provider = headers["provider"]
    client.get(WALLET, headers=provider)

    def fail_after_flush(db, *_):
        db.flush()
        raise RuntimeError("injected booking notification failure")

    monkeypatch.setattr(mvp_account, "booking_event", fail_after_flush)
    with pytest.raises(RuntimeError, match="injected booking notification failure"):
        decision(client, provider, booking_id)
    with sessions() as db:
        assert db.get(MVPBooking, booking_id).status == "pending"
        assert db.scalar(select(ProviderWallet)).free_bookings_used == 0
        assert db.scalar(select(WalletTransaction)) is None
        profile = db.get(MVPProviderProfile, profiles["provider"])
        profile.verification_status = "suspended"
        db.commit()
    assert top_up(client, provider).status_code == 403
    assert decision(client, provider, booking_id).status_code == 403


def test_competing_confirmations_share_funds(harness):
    client, _, headers, _, booking = harness
    for category in ("adventure", "guide", "bicycle"):
        booking(category, state="completed")
    provider = headers["provider"]
    assert top_up(client, provider, amount="10.01").json()["success"]
    bookings = [booking("bike"), booking("guide")]
    barrier = Barrier(2, timeout=5)

    def confirm(booking_id):
        barrier.wait()
        return decision(client, provider, booking_id).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(confirm, bookings)) == [200, 409]
    assert client.get(WALLET, headers=provider).json()["balance"] == "0.00"
    assert (
        client.get(
            WALLET + "/transactions",
            headers=provider,
            params={
                "transaction_type": "commission_deduction",
            },
        ).json()["total"]
        == 1
    )


def test_mssql_decimal_uuid_and_filtered_index_contract():
    version = next(item for item in UPGRADES if item.version == "1.0.8")
    assert version.version == "1.0.8"
    sql = "\n".join(version.batches())
    table = WalletTransaction.__table__
    ddl = str(CreateTable(table).compile(dialect=dialect()))
    assert (
        "request_key UNIQUEIDENTIFIER" in ddl and "request_key UNIQUEIDENTIFIER" in sql
    )
    assert "NUMERIC(18, 2)" in ddl and "DECIMAL(18, 2)" in sql
    for index in table.indexes:
        if index.unique:
            compiled = str(CreateIndex(index).compile(dialect=dialect()))
            assert (
                compiled.replace(
                    " ON wallet_transactions ", " ON dbo.wallet_transactions "
                )
                in sql
            )
    assert "INSERT INTO dbo.provider_wallets" in sql
    assert "INSERT INTO dbo.wallet_transactions" not in sql
