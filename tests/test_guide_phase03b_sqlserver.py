import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuidePackage,
)
from app.models.user import User
from app.routers.provider_reservations import GuideReservationRequest
from app.services.guide_booking import LOCAL
from app.services.guide_payments import guide_checkout, handle_guide_callback
from app.services.provider_reservations import GUIDE, change_reservation, reserve_guide
from tests import test_provider_phase01_sqlserver, test_provider_workspace_sqlserver

engine = test_provider_phase01_sqlserver.engine
transaction = test_provider_phase01_sqlserver.transaction
verify_target = test_provider_phase01_sqlserver.verify_target
make_profile = test_provider_workspace_sqlserver.profile
make_traveler = test_provider_workspace_sqlserver.traveler
pytestmark = pytest.mark.skipif(
    os.getenv("GUIDE_PHASE03B_SQLSERVER") != "1",
    reason="Enable explicitly for the guarded isolated SQL Server database",
)


def seed_guide(db):
    owner = make_profile(db, GUIDE)
    buyers = [make_traveler(db), make_traveler(db)]
    guide = TourGuide(
        provider_profile_id=owner.id,
        bio="Isolated multi-day guide fixture",
        base_city="Fort",
        base_district="Colombo",
        approval_status="approved",
        is_active=True,
        daily_rate=100,
    )
    db.add(guide)
    db.flush()
    day = datetime.now(LOCAL).date() + timedelta(days=2)
    package = TourGuidePackage(
        tour_guide_id=guide.id,
        title="Multi-day fixture",
        description="Tour",
        duration_hours=30,
        price=500,
        meeting_point="Fort",
    )
    db.add_all(
        [
            package,
            TourGuideAvailability(tour_guide_id=guide.id, available_date=day),
            TourGuideAvailability(
                tour_guide_id=guide.id, available_date=day + timedelta(days=1)
            ),
        ]
    )
    db.flush()
    payload = GuideReservationRequest(
        guide_id=guide.id,
        package_id=package.id,
        people=2,
        starts_at=datetime.combine(day, time(7), LOCAL),
        payment_method="online",
    )
    return owner.id, owner.user_id, [buyer.id for buyer in buyers], payload


@pytest.fixture
def committed_inventory(engine):
    with Session(engine) as db:
        verify_target(db.connection())
        inventory = seed_guide(db)
        db.commit()
    try:
        yield inventory
    finally:
        owner_id, owner_user, buyers, payload = inventory
        with Session(engine) as db:
            verify_target(db.connection())
            bookings = select(TourGuideBooking.id).where(
                TourGuideBooking.tour_guide_id == payload.guide_id
            )
            refs = select(BookingReference.id).where(
                BookingReference.module_type == GUIDE,
                BookingReference.provider_profile_id == owner_id,
                BookingReference.target_booking_id.in_(bookings),
            )
            tx = select(PaymentTransaction.id).where(
                PaymentTransaction.booking_reference_id.in_(refs)
            )
            db.execute(
                delete(PaymentRefund).where(
                    PaymentRefund.payment_transaction_id.in_(tx)
                )
            )
            db.execute(
                delete(BookingStatusHistory).where(
                    BookingStatusHistory.booking_reference_id.in_(refs)
                )
            )
            db.execute(delete(PaymentTransaction).where(PaymentTransaction.id.in_(tx)))
            db.execute(delete(BookingReference).where(BookingReference.id.in_(refs)))
            db.execute(
                delete(Notification).where(
                    Notification.user_id.in_([owner_user, *buyers])
                )
            )
            db.execute(
                delete(TourGuideBooking).where(
                    TourGuideBooking.tour_guide_id == payload.guide_id
                )
            )
            db.execute(
                delete(TourGuideAvailability).where(
                    TourGuideAvailability.tour_guide_id == payload.guide_id
                )
            )
            db.execute(
                delete(TourGuidePackage).where(
                    TourGuidePackage.tour_guide_id == payload.guide_id
                )
            )
            db.execute(delete(TourGuide).where(TourGuide.id == payload.guide_id))
            db.execute(delete(ProviderProfile).where(ProviderProfile.id == owner_id))
            db.execute(delete(User).where(User.id.in_([owner_user, *buyers])))
            db.commit()
        with Session(engine) as db:
            verify_target(db.connection())
            assert db.get(TourGuide, payload.guide_id) is None


def race(engine, *operations):
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
        results = [pool.submit(run, op) for op in operations]
        return [result.result(timeout=25) for result in results]


def test_parallel_guide_reservation_and_capture(engine, committed_inventory):
    provider_id, provider_user, buyers, payload = committed_inventory
    overlapping = payload.model_copy(
        update={"starts_at": payload.starts_at + timedelta(hours=1)}
    )
    first = race(
        engine,
        lambda db: reserve_guide(db, db.get(User, buyers[0]), payload),
        lambda db: reserve_guide(db, db.get(User, buyers[1]), overlapping),
    )
    assert sorted(code for code, _ in first) == [200, 409]
    winner = next(value for code, value in first if code == 200)
    with Session(engine) as db:
        verify_target(db.connection())
        buyer_id = db.get(TourGuideBooking, winner["id"]).traveler_id
        accepted = change_reservation(
            db,
            GUIDE,
            winner["id"],
            db.get(User, provider_user),
            "accept",
            None,
            provider=db.get(ProviderProfile, provider_id),
        )
        assert accepted["status"] == "awaiting_payment"
        guide_checkout(db, winner["id"], db.get(User, buyer_id))

    def capture(db):
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.booking_reference == winner["reference"]
            )
        )
        return handle_guide_callback(
            db, ref, f"guide-race-{ref.id}", "1000.00", "LKR", "2"
        )

    receipts = race(engine, capture, capture)
    assert sorted(result["status"] for code, result in receipts if code == 200) == [
        "already_processed",
        "ok",
    ]
    with Session(engine) as db:
        verify_target(db.connection())
        assert db.get(TourGuideBooking, winner["id"]).booking_status == "confirmed"
        assert (
            db.scalar(
                select(func.count(PaymentTransaction.id))
                .join(BookingReference)
                .where(BookingReference.booking_reference == winner["reference"])
            )
            == 1
        )


def test_sqlserver_expired_checkout_and_late_capture(transaction, monkeypatch):
    db = transaction
    from app.services import guide_payments

    provider_id, provider_user, buyers, payload = seed_guide(db)
    row = reserve_guide(db, db.get(User, buyers[0]), payload)
    accepted = change_reservation(
        db,
        GUIDE,
        row["id"],
        db.get(User, provider_user),
        "accept",
        None,
        provider=db.get(ProviderProfile, provider_id),
    )
    guide_checkout(db, row["id"], db.get(User, buyers[0]))
    deadline = accepted["payment_expires_at"].replace(tzinfo=None)
    monkeypatch.setattr(guide_payments, "utc_now", lambda: deadline)
    ref = db.scalar(
        select(BookingReference).where(
            BookingReference.booking_reference == row["reference"]
        )
    )
    result = handle_guide_callback(
        db, ref, f"guide-expired-{ref.id}", "1000.00", "LKR", "2"
    )
    assert result["status"] == "requires_reconciliation"
    assert (ref.booking_status, ref.payment_status) == ("expired", "refund_pending")
    tx = db.scalar(
        select(PaymentTransaction).where(
            PaymentTransaction.booking_reference_id == ref.id
        )
    )
    assert tx.payment_status == "completed"
    assert (
        db.scalar(
            select(PaymentRefund.refund_status).where(
                PaymentRefund.payment_transaction_id == tx.id
            )
        )
        == "pending"
    )
