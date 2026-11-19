"""Opt-in, two-session reservation races against the isolated SQL Server schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.eco_experience import EcoExperience
from app.models.eco_reservation import EcoAvailabilitySlot, EcoReservation
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentTransaction,
)
from app.models.user import User
from app.schemas.eco_reservation import EcoReservationCreate, EcoSlotCreate
from app.services import eco_reservations
from app.services.provider_reservations import LOCAL
from tests import test_provider_phase01_sqlserver, test_provider_workspace_sqlserver

engine = test_provider_phase01_sqlserver.engine
verify_target = test_provider_phase01_sqlserver.verify_target
make_profile = test_provider_workspace_sqlserver.profile
make_traveler = test_provider_workspace_sqlserver.traveler

pytestmark = pytest.mark.skipif(
    os.getenv("ECO_PHASE03A_SQLSERVER") != "1",
    reason="Enable explicitly for the migrated, isolated SQL Server test database",
)


@pytest.fixture
def inventory(engine):
    with Session(engine, expire_on_commit=False) as db:
        verify_target(db.connection())
        owner = make_profile(db, "eco_tourism")
        buyers = [make_traveler(db), make_traveler(db)]
        listing = EcoExperience(
            provider_profile_id=owner.id,
            title="Phase 03A isolated experience",
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
        day = datetime.now(LOCAL).date() + timedelta(days=2)
        slot = eco_reservations.create_slot(
            db,
            owner,
            listing.id,
            EcoSlotCreate(starts_at=datetime.combine(day, time(9), LOCAL), capacity=2),
        )
        ids = (
            slot["id"],
            listing.id,
            owner.id,
            owner.user_id,
            *(buyer.id for buyer in buyers),
        )
    try:
        yield ids
    finally:
        slot_id, listing_id, owner_id, *users = ids
        with Session(engine) as db:
            verify_target(db.connection())
            booking_ids = select(EcoReservation.id).where(
                EcoReservation.slot_id == slot_id
            )
            ref_ids = select(BookingReference.id).where(
                BookingReference.module_type == "eco_experience",
                BookingReference.provider_profile_id == owner_id,
                BookingReference.target_booking_id.in_(booking_ids),
            )
            db.execute(
                delete(BookingStatusHistory).where(
                    BookingStatusHistory.booking_reference_id.in_(ref_ids)
                )
            )
            db.execute(
                delete(PaymentTransaction).where(
                    PaymentTransaction.booking_reference_id.in_(ref_ids)
                )
            )
            db.execute(delete(BookingReference).where(BookingReference.id.in_(ref_ids)))
            db.execute(delete(Notification).where(Notification.user_id.in_(users)))
            db.execute(delete(EcoReservation).where(EcoReservation.slot_id == slot_id))
            db.execute(
                delete(EcoAvailabilitySlot).where(EcoAvailabilitySlot.id == slot_id)
            )
            db.execute(delete(EcoExperience).where(EcoExperience.id == listing_id))
            db.execute(delete(ProviderProfile).where(ProviderProfile.id == owner_id))
            db.execute(delete(User).where(User.id.in_(users)))
            db.commit()
        with Session(engine) as db:
            verify_target(db.connection())
            assert db.get(EcoAvailabilitySlot, slot_id) is None
            assert db.get(EcoExperience, listing_id) is None


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
        engine, lambda db: reserve(db, buyer_id), lambda db: reserve(db, buyer_id)
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
