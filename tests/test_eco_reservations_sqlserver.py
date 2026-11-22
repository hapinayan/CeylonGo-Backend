"""
Harden and Isolated Concurrency Integration Tests for Eco-Tourism Reservations.
================================================================================
Opt-in, two-session reservation races against the isolated SQL Server schema.

STRICT SAFETY RESTRICTIONS:
- Enforces fail-closed opt-in: RUN_SQLSERVER_INTEGRATION_TESTS=1 AND
  ECO_PHASE03A_SQLSERVER=1.
- Restricts target database strictly to 'ceylongodb_integrationtest' or an
  approved disposable test database URL.
- Live production database 'CeylonGoDB' is strictly rejected at all levels.
- Verifies actual connected server instance identity (machine + instance name).
- Scopes all write operations to unique test-generated records and ensures
  reliable, leaf-to-root cleanup across all 16 child and parent entity levels.
- Never mutates or deletes non-test database records.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
import os
from threading import Barrier
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.core.database_guard import (
    ARTISAN_INTEGRATION_ALIAS,
    ARTISAN_INTEGRATION_DATABASE,
    DatabaseSafetyError,
    attach_runtime_database_guard,
    create_safe_sqlserver_test_engine,
    normalize_server_name,
    validate_disposable_sqlserver_url,
    verify_connected_server_identity,
    verify_safe_integration_database,
    verify_safe_integration_target,
)
from app.database.connection import DATABASE_URL
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import EcoPromotionAttribution
from app.models.eco_reservation import (
    EcoAgeLimit,
    EcoAvailabilitySlot,
    EcoCancellationPolicy,
    EcoCancellationSnapshot,
    EcoRefundReconciliation,
    EcoReservation,
    EcoSlotClearance,
)
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.user import User
from app.schemas.eco_reservation import EcoReservationCreate, EcoSlotCreate
from app.services import eco_reservations
from app.services.provider_reservations import LOCAL

APPROVED_DB_NAME = "ceylongodb_integrationtest"
APPROVED_DATABASE_SET = frozenset({APPROVED_DB_NAME})


def is_sqlserver_opted_in() -> bool:
    """Fail-closed check requiring both global and eco-specific opt-ins."""
    return (
        os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS") == "1"
        and os.getenv("ECO_PHASE03A_SQLSERVER") == "1"
    )


pytestmark = pytest.mark.skipif(
    not is_sqlserver_opted_in(),
    reason=(
        "Requires explicit opt-in (RUN_SQLSERVER_INTEGRATION_TESTS=1 and "
        "ECO_PHASE03A_SQLSERVER=1) pointing to an isolated test database"
    ),
)


def verify_target(connection, expected_db: str | None = None) -> None:
    """
    Fail-closed runtime verification:
    - Verifies current DB name is strictly allowlisted.
    - Verifies live production CeylonGoDB is strictly rejected.
    - Verifies connected server instance identity (machine + instance name).
    - Sets lock timeout (6000ms) to prevent indefinite hangs.
    """
    target = (expected_db or APPROVED_DB_NAME).strip().lower()
    db_name = connection.scalar(text("SELECT DB_NAME()"))
    verify_safe_integration_database(
        db_name, approved_databases=frozenset({target})
    )
    verify_connected_server_identity(
        connection, server_alias=connection.engine.url.host
    )
    connection.exec_driver_sql("SET LOCK_TIMEOUT 6000")


@pytest.fixture
def engine():
    raw_disposable_url = os.getenv("TEST_DISPOSABLE_SQLSERVER_URL")
    if raw_disposable_url and raw_disposable_url.strip():
        # Dedicated disposable database strategy preferred if provided
        target_db = validate_disposable_sqlserver_url(
            raw_disposable_url, approved_databases=APPROVED_DATABASE_SET
        )
        db_engine = create_safe_sqlserver_test_engine(
            raw_disposable_url,
            approved_databases=APPROVED_DATABASE_SET,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        expected_db = target_db
    else:
        # Standard approved integration target CeylonGoDB_IntegrationTest
        server = os.getenv(
            "ECO_SQLSERVER_TEST_SERVER", ARTISAN_INTEGRATION_ALIAS
        )
        database = ARTISAN_INTEGRATION_DATABASE
        opt_in = os.getenv("RUN_SQLSERVER_INTEGRATION_TESTS")

        verify_safe_integration_target(server, database, opt_in)
        if normalize_server_name(server) != ARTISAN_INTEGRATION_ALIAS.lower():
            raise DatabaseSafetyError(
                "Eco SQL Server integration tests require the approved local "
                f"alias '{ARTISAN_INTEGRATION_ALIAS}'."
            )

        db_url = DATABASE_URL.set(host=server, database=database)
        db_engine = create_engine(
            db_url,
            connect_args={"timeout": 5},
            pool_pre_ping=True,
            hide_parameters=True,
        )
        attach_runtime_database_guard(
            db_engine,
            approved_databases=APPROVED_DATABASE_SET,
            expected_db=database,
            server_alias=server,
        )
        expected_db = database

    try:
        with db_engine.connect() as conn:
            verify_target(conn, expected_db=expected_db)
    except Exception:
        db_engine.dispose()
        raise

    try:
        yield db_engine
    finally:
        db_engine.dispose()


def make_eco_provider(db: Session, tag: str) -> ProviderProfile:
    """Create an isolated test provider with a unique tag."""
    user = User(
        full_name=f"Eco Test Provider {tag}",
        email=f"eco-provider-{tag}@example.test",
        is_active=True,
        role="provider",
    )
    db.add(user)
    db.flush()
    provider = ProviderProfile(
        user_id=user.id,
        provider_type="eco_tourism",
        approval_status="approved",
        business_name=f"Eco Provider Business {tag}",
        district="Jaffna",
        phone="0771234567",
    )
    db.add(provider)
    db.flush()
    return provider


def make_eco_buyer(db: Session, tag: str) -> User:
    """Create an isolated test traveler buyer with a unique tag."""
    user = User(
        full_name=f"Eco Test Buyer {tag}",
        email=f"eco-buyer-{tag}@example.test",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _perform_isolated_cleanup(engine, state: dict) -> None:
    """
    Safely and reliably deletes only the records created by this test,
    in strict leaf-to-root dependency order. Never modifies or deletes
    non-test records.
    """
    slot_id = state.get("slot_id")
    listing_id = state.get("listing_id")
    owner_id = state.get("owner_id")
    all_users = list(filter(None, [
        state.get("owner_user_id"),
        *state.get("buyer_ids", []),
    ]))

    with Session(engine) as db:
        verify_target(db.connection())

        # 1. Collect booking & reference IDs if slot existed
        if slot_id is not None:
            booking_ids = select(EcoReservation.id).where(
                EcoReservation.slot_id == slot_id
            )
            ref_ids = select(BookingReference.id).where(
                BookingReference.module_type == "eco_experience",
                BookingReference.target_booking_id.in_(booking_ids),
            )
            tx_ids = select(PaymentTransaction.id).where(
                PaymentTransaction.booking_reference_id.in_(ref_ids)
            )
            refund_ids = select(PaymentRefund.id).where(
                PaymentRefund.payment_transaction_id.in_(tx_ids)
            )

            # Delete payment refund reconciliations & refunds
            db.execute(delete(EcoRefundReconciliation).where(
                EcoRefundReconciliation.refund_id.in_(refund_ids)
            ))
            db.execute(delete(PaymentRefund).where(
                PaymentRefund.payment_transaction_id.in_(tx_ids)
            ))

            # Delete promotion attributions
            db.execute(delete(EcoPromotionAttribution).where(
                EcoPromotionAttribution.booking_reference_id.in_(ref_ids)
            ))

            # Delete booking histories, payments, references
            db.execute(delete(BookingStatusHistory).where(
                BookingStatusHistory.booking_reference_id.in_(ref_ids)
            ))
            db.execute(delete(PaymentTransaction).where(
                PaymentTransaction.booking_reference_id.in_(ref_ids)
            ))
            db.execute(delete(BookingReference).where(
                BookingReference.id.in_(ref_ids)
            ))

            # Delete reservation snapshots & reservations
            db.execute(delete(EcoCancellationSnapshot).where(
                EcoCancellationSnapshot.reservation_id.in_(booking_ids)
            ))
            db.execute(delete(EcoReservation).where(
                EcoReservation.slot_id == slot_id
            ))

            # Delete slot clearances & slot
            db.execute(delete(EcoSlotClearance).where(
                EcoSlotClearance.slot_id == slot_id
            ))
            db.execute(delete(EcoAvailabilitySlot).where(
                EcoAvailabilitySlot.id == slot_id
            ))

        # 2. Delete experience policies, age limits & experience
        if listing_id is not None:
            db.execute(delete(EcoCancellationPolicy).where(
                EcoCancellationPolicy.eco_experience_id == listing_id
            ))
            db.execute(delete(EcoAgeLimit).where(
                EcoAgeLimit.eco_experience_id == listing_id
            ))
            db.execute(delete(EcoExperience).where(
                EcoExperience.id == listing_id
            ))

        # 3. Delete notifications for all test users
        if all_users:
            db.execute(delete(Notification).where(
                Notification.user_id.in_(all_users)
            ))

        # 4. Delete provider profile
        if owner_id is not None:
            db.execute(delete(ProviderProfile).where(
                ProviderProfile.id == owner_id
            ))

        # 5. Delete all test users
        if all_users:
            db.execute(delete(User).where(
                User.id.in_(all_users)
            ))

        db.commit()

    # 6. Post-cleanup confirmation
    with Session(engine) as db:
        verify_target(db.connection())
        if slot_id is not None:
            assert db.get(EcoAvailabilitySlot, slot_id) is None
        if listing_id is not None:
            assert db.get(EcoExperience, listing_id) is None
        if owner_id is not None:
            assert db.get(ProviderProfile, owner_id) is None
        for u_id in all_users:
            assert db.get(User, u_id) is None


@pytest.fixture
def inventory(engine):
    tracked_state = {
        "slot_id": None,
        "listing_id": None,
        "owner_id": None,
        "owner_user_id": None,
        "buyer_ids": [],
    }

    try:
        with Session(engine, expire_on_commit=False) as db:
            verify_target(db.connection())
            tag = uuid4().hex[:12]
            owner = make_eco_provider(db, tag=tag)
            tracked_state["owner_id"] = owner.id
            tracked_state["owner_user_id"] = owner.user_id

            buyers = [
                make_eco_buyer(db, tag=f"{tag}-b1"),
                make_eco_buyer(db, tag=f"{tag}-b2"),
            ]
            tracked_state["buyer_ids"] = [b.id for b in buyers]

            listing = EcoExperience(
                provider_profile_id=owner.id,
                title=f"Phase 03A isolated experience {tag}",
                description="SQL Server Eco reservation fixture",
                eco_category="Wildlife & Birdwatching",
                meeting_point="Visitor entrance",
                duration_hours=2,
                district="Jaffna",
                city="Nallur",
                pricing_type="per_person",
                base_price=1800,
                max_group_size=2,
                environmental_guidelines="Stay on trails",
                wildlife_safety_summary="Maintain distance from wildlife",
                listing_status="approved",
                is_published=True,
            )
            db.add(listing)
            db.flush()
            tracked_state["listing_id"] = listing.id

            day = datetime.now(LOCAL).date() + timedelta(days=2)
            slot = eco_reservations.create_slot(
                db,
                owner,
                listing.id,
                EcoSlotCreate(
                    starts_at=datetime.combine(day, time(9), LOCAL),
                    capacity=2,
                ),
            )
            tracked_state["slot_id"] = slot["id"]

        ids = (
            tracked_state["slot_id"],
            tracked_state["listing_id"],
            tracked_state["owner_id"],
            tracked_state["owner_user_id"],
            *tracked_state["buyer_ids"],
        )
        yield ids

    finally:
        _perform_isolated_cleanup(engine, tracked_state)


def compete(engine, *operations):
    barrier = Barrier(len(operations), timeout=12)

    def run(operation):
        with Session(engine) as db:
            verify_target(db.connection())
            barrier.wait()
            try:
                return 200, operation(db)
            except HTTPException as exc:
                db.rollback()
                return exc.status_code, None

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        work = [pool.submit(run, operation) for operation in operations]
        results = [future.result(timeout=25) for future in work]
    assert sorted(status for status, _ in results) == [200, 409]
    return next(result for status, result in results if status == 200)


def test_concurrent_booking_release_and_cash_receipt(engine, inventory):
    slot_id, _, owner_id, owner_user_id, first_buyer, second_buyer = inventory
    request = EcoReservationCreate(slot_id=slot_id, participants=2)

    def reserve(db, buyer_id):
        return eco_reservations.reserve(db, db.get(User, buyer_id), request)

    winning = compete(
        engine,
        lambda db: reserve(db, first_buyer),
        lambda db: reserve(db, second_buyer),
    )
    booking_id = winning["id"]
    with Session(engine) as db:
        verify_target(db.connection())
        assert db.get(EcoAvailabilitySlot, slot_id).reserved_count == 2
        buyer_id = db.get(EcoReservation, booking_id).traveler_id

    def provider_action(db, reservation_id, action):
        return eco_reservations.change_reservation(
            db,
            db.get(User, owner_user_id),
            reservation_id,
            action,
            None,
            provider=db.get(ProviderProfile, owner_id),
        )

    compete(
        engine,
        lambda db: provider_action(db, booking_id, "reject"),
        lambda db: eco_reservations.change_reservation(
            db, db.get(User, buyer_id), booking_id, "cancel", None
        ),
    )
    with Session(engine) as db:
        verify_target(db.connection())
        assert db.get(EcoAvailabilitySlot, slot_id).reserved_count == 0

    duplicate = compete(
        engine,
        lambda db: reserve(db, buyer_id),
        lambda db: reserve(db, buyer_id),
    )
    new_id = duplicate["id"]
    with Session(engine) as db:
        verify_target(db.connection())
        provider_action(db, new_id, "accept")
    compete(
        engine,
        lambda db: provider_action(db, new_id, "cash"),
        lambda db: provider_action(db, new_id, "cash"),
    )
    with Session(engine) as db:
        verify_target(db.connection())
        assert db.get(EcoAvailabilitySlot, slot_id).reserved_count == 2
        assert db.get(EcoReservation, new_id).payment_status == "paid"
        receipts = db.scalar(
            select(func.count(PaymentTransaction.id))
            .join(BookingReference)
            .where(
                BookingReference.module_type == "eco_experience",
                BookingReference.target_booking_id == new_id,
                PaymentTransaction.payment_status == "completed",
            )
        )
        assert receipts == 1
