"""Opt-in SQL Server checks requiring pre-applied 037/038 on the approved test DB."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.database_guard import (
    ARTISAN_INTEGRATION_ALIAS,
    ARTISAN_INTEGRATION_DATABASE,
    DatabaseSafetyError,
    normalize_server_name,
    verify_connected_server_identity,
    verify_safe_integration_target,
)
from app.database.connection import DATABASE_URL
from app.models.artisan import ArtisanProductFavorite, ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, BookingStatusHistory, Notification, PaymentTransaction
from app.models.showcase_item import ShowcaseItem
from app.models.user import User
from app.routers.artisan_wishlists import add_product_favorite, remove_product_favorite
from app.routers.provider_reservations import ArtisanReservationRequest
from app.services import provider_reservations
from app.services.artisan_reservation_expiry import expire_single_reservation
from app.services.provider_reservations import LOCAL, reject_artisan_reservation, reserve_artisan
from scripts.migrate import discover_migrations
from tests.test_provider_workspace_sqlserver import profile, traveler

OPT_IN = os.getenv("ARTISAN_PHASE03_SQLSERVER")
pytestmark = pytest.mark.skipif(
    OPT_IN != "1", reason="Requires explicit opt-in and a migrated isolated database"
)


@pytest.fixture
def engine():
    server = os.getenv("ARTISAN_PHASE03_TEST_SERVER")
    database = ARTISAN_INTEGRATION_DATABASE
    verify_safe_integration_target(server, database, OPT_IN)
    if normalize_server_name(server) != ARTISAN_INTEGRATION_ALIAS.lower():
        raise DatabaseSafetyError("Artisan Phase 01 requires the approved local test alias.")
    engine = create_engine(
        DATABASE_URL.set(host=server, database=database),
        connect_args={"timeout": 5},
        hide_parameters=True,
    )
    try:
        yield engine
    finally:
        engine.dispose()


def verify_migrated_connection(connection):
    verify_connected_server_identity(connection, server_alias=connection.engine.url.host)
    expected = [
        (migration.version, migration.filename, migration.checksum)
        for migration in discover_migrations(Path(__file__).resolve().parents[1] / "migrations")
        if migration.version <= "038"
    ]
    ledger = [
        tuple(row) for row in connection.execute(text(
            "SELECT version, filename, checksum FROM dbo.schema_migrations ORDER BY version"
        ))
    ]
    assert ledger == expected, "Artisan write tests require a compatible ledger through 038"
    connection.rollback()


@pytest.fixture
def transaction(engine):
    with engine.connect() as connection:
        verify_migrated_connection(connection)
        outer = connection.begin()
        try:
            with Session(
                bind=connection, join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            ) as db:
                yield db
        finally:
            assert outer.is_active
            outer.rollback()


def seed_product(db, title="Artisan workshop", stock=1):
    owner = profile(db, "artisan")
    listing = ArtisanListing(
        provider_profile_id=owner.id, title=title, craft_category="wood",
        city="Fort", district="Colombo", approval_status="approved", is_verified=True,
        is_active=True,
    )
    db.add(listing)
    db.flush()
    item = ShowcaseItem(
        artisan_listing_id=listing.id, title="Bowl", price=200, stock_quantity=stock,
    )
    db.add(item)
    db.flush()
    return owner, item


def seed_reservation(db, title="Artisan workshop", pickup_date=None):
    owner, item = seed_product(db, title)
    buyer = traveler(db)
    payload = ArtisanReservationRequest(
        item_id=item.id, quantity=1,
        pickup_date=pickup_date or datetime.now(LOCAL).date() + timedelta(days=2),
    )
    created = reserve_artisan(db, buyer, payload)
    return owner, buyer, item, created["id"]


def test_sqlserver_rejects_once_and_exercises_favorite_constraints(transaction):
    db = transaction
    owner, buyer, item, reservation_id = seed_reservation(db)
    row, ref = reject_artisan_reservation(db, reservation_id, owner, "Workshop closed")
    assert row.reservation_status == ref.booking_status == "rejected"
    assert row.rejected_by == owner.user_id and row.rejected_at is not None
    with pytest.raises(HTTPException) as duplicate:
        reject_artisan_reservation(db, reservation_id, owner, "Workshop closed")
    assert duplicate.value.status_code == 409
    db.refresh(item)
    assert item.stock_quantity == 1
    assert db.scalars(
        select(Notification).where(
            Notification.user_id == buyer.id,
            Notification.notification_type == "artisan_reservation_rejected",
        )
    ).one().message == "Workshop closed"
    first = add_product_favorite(item.id, buyer, db)
    assert add_product_favorite(item.id, buyer, db) == first
    assert db.scalars(
        select(ArtisanProductFavorite).where(ArtisanProductFavorite.user_id == buyer.id)
    ).one().product_id == item.id
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ArtisanProductFavorite(user_id=buyer.id, product_id=item.id))
        db.flush()
    remove_product_favorite(item.id, buyer, db)
    assert db.scalar(
        select(ArtisanProductFavorite.id).where(ArtisanProductFavorite.user_id == buyer.id)
    ) is None


def test_sqlserver_artisan_lock_contention_and_rollback_release(engine):
    resource = f"artisan_stock_test_{uuid4().hex}"
    with engine.connect() as first, engine.connect() as second:
        verify_connected_server_identity(first, server_alias=engine.url.host)
        verify_connected_server_identity(second, server_alias=engine.url.host)
        first.rollback()
        second.rollback()
        with Session(bind=first) as owner, Session(bind=second) as contender:
            lock_booking_resource(owner, resource)
            with pytest.raises(HTTPException) as blocked:
                lock_booking_resource(contender, resource)
            assert blocked.value.status_code == 503
            owner.rollback()
            lock_booking_resource(contender, resource)
            contender.commit()
            lock_booking_resource(owner, resource)
            owner.rollback()


def test_sqlserver_rejection_rolls_back_application_writes(transaction, monkeypatch):
    db = transaction
    owner, buyer, item, reservation_id = seed_reservation(db)
    product_id, buyer_id = item.id, buyer.id
    reference_id = db.scalar(
        select(BookingReference.id).where(
            BookingReference.module_type == "artisan_reservation",
            BookingReference.target_booking_id == reservation_id,
        )
    )

    def fail_after_flush(*args, **kwargs):
        db.flush()
        raise RuntimeError("Injected notification failure")

    monkeypatch.setattr(provider_reservations, "create_notification", fail_after_flush)
    with pytest.raises(RuntimeError, match="Injected notification failure"):
        reject_artisan_reservation(db, reservation_id, owner, "Workshop closed")

    assert db.get(ArtisanReservation, reservation_id).reservation_status == "pending"
    assert db.get(BookingReference, reference_id).booking_status == "pending"
    assert db.get(ShowcaseItem, product_id).stock_quantity == 0
    assert db.scalar(select(BookingStatusHistory.id).where(
        BookingStatusHistory.booking_reference_id == reference_id,
        BookingStatusHistory.to_status == "rejected",
    )) is None
    assert db.scalar(select(Notification.id).where(
        Notification.user_id == buyer_id,
        Notification.notification_type == "artisan_reservation_rejected",
    )) is None


def test_sqlserver_two_sessions_reject_the_same_reservation(engine, monkeypatch):
    # The two SQL Server sessions must see a committed seed. Keep the uniquely
    # marked run fixture to avoid deleting database records.
    label = f"Phase 01 rejection fixture {uuid4().hex}"
    with engine.connect() as connection:
        verify_migrated_connection(connection)
        with Session(bind=connection, expire_on_commit=False) as db:
            provider, buyer, product, reservation_id = seed_reservation(db, label)
            provider_id, buyer_id, product_id = provider.id, buyer.id, product.id
    print(f"Retained SQL Server fixture: {label}, reservation ID {reservation_id}")

    rendezvous = Barrier(2)

    def synchronize_before_product_lock(db, resource):
        rendezvous.wait(timeout=10)
        lock_booking_resource(db, resource)

    monkeypatch.setattr(
        provider_reservations, "lock_booking_resource", synchronize_before_product_lock
    )

    def reject():
        with engine.connect() as connection:
            verify_migrated_connection(connection)
            with Session(bind=connection) as db:
                provider = db.get(ProviderProfile, provider_id)
                try:
                    row, ref = reject_artisan_reservation(
                        db, reservation_id, provider, "Workshop closed"
                    )
                except HTTPException as error:
                    return error.status_code
                assert row.reservation_status == ref.booking_status == "rejected"
                return 200

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = [workers.submit(reject) for _ in range(2)]
        assert sorted(f.result(timeout=25) for f in results) == [200, 409]

    with engine.connect() as connection:
        verify_migrated_connection(connection)
        with Session(bind=connection) as db:
            assert db.get(ShowcaseItem, product_id).stock_quantity == 1
            notifications = db.scalars(select(Notification.id).where(
                Notification.user_id == buyer_id,
                Notification.notification_type == "artisan_reservation_rejected",
            )).all()
            assert len(notifications) == 1


def test_sqlserver_concurrent_hold_creation_has_one_winner(engine):
    # Actual independent transactions need a visible seed. Retain marked test rows.
    label = f"Phase 02 stock race {uuid4().hex}"
    with engine.connect() as connection:
        verify_migrated_connection(connection)
        with Session(bind=connection, expire_on_commit=False) as db:
            _, product = seed_product(db, label)
            buyer_ids = [traveler(db).id for _ in range(2)]
            product_id = product.id
            db.commit()
    pickup_date = datetime.now(LOCAL).date() + timedelta(days=2)
    start = Barrier(2)

    def attempt(buyer_id, detailed):
        with engine.connect() as connection:
            verify_migrated_connection(connection)
            with Session(bind=connection) as db:
                buyer = db.get(User, buyer_id)
                start.wait(timeout=10)
                try:
                    row, _ = provider_reservations.create_artisan_hold(
                        db, buyer, product_id, 1, pickup_date, detailed=detailed
                    )
                except HTTPException as exc:
                    assert exc.status_code in (400, 409)
                    return None
                return row.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(attempt, buyer_id, bool(n)) for n, buyer_id in enumerate(buyer_ids)]
        ids = [job.result(timeout=25) for job in jobs]
    assert sum(reservation_id is not None for reservation_id in ids) == 1
    with engine.connect() as connection:
        verify_migrated_connection(connection)
        with Session(bind=connection) as db:
            assert db.get(ShowcaseItem, product_id).stock_quantity == 0
            assert db.scalars(select(ArtisanReservation.id).where(
                ArtisanReservation.showcase_item_id == product_id
            )).one() in ids
    print(f"Retained SQL Server fixture: {label}, reservations {ids}")


def test_sqlserver_pickup_and_legacy_expiry_share_payment_and_stock_rules(transaction):
    db = transaction
    today = datetime.now(LOCAL).date()
    owner, buyer, product, reservation_id = seed_reservation(db, pickup_date=today)
    from app.services.provider_reservations import change_artisan_reservation

    with pytest.raises(HTTPException):
        change_artisan_reservation(db, reservation_id, owner.user, "ready")
    change_artisan_reservation(db, reservation_id, owner.user, "accept")
    change_artisan_reservation(db, reservation_id, owner.user, "ready")
    row, ref = change_artisan_reservation(
        db, reservation_id, owner.user, "complete", detailed=True
    )
    assert row.reservation_status == "completed" and ref.payment_status == "paid"
    assert db.scalars(select(PaymentTransaction).where(
        PaymentTransaction.booking_reference_id == ref.id
    )).one().amount == 200
    with pytest.raises(HTTPException):
        change_artisan_reservation(db, reservation_id, owner.user, "complete")
    assert db.get(ShowcaseItem, product.id).stock_quantity == 0

    owner, buyer, product, reservation_id = seed_reservation(db, pickup_date=today)
    legacy = db.get(ArtisanReservation, reservation_id)
    legacy.expires_at = None
    db.commit()
    cutoff = provider_reservations.artisan_pickup_expiry(today)
    assert not expire_single_reservation(db, reservation_id, now=cutoff - timedelta(seconds=1))[0]
    assert expire_single_reservation(db, reservation_id, now=cutoff)[0]
    assert db.get(ShowcaseItem, product.id).stock_quantity == 1
    assert not expire_single_reservation(db, reservation_id, now=cutoff)[0]
