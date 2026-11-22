"""Opt-in reservation writes on the existing isolated integration database."""

import os
from datetime import UTC, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.booking_transactions import lock_booking_resource
from app.core.payhere import payhere_service
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.artisan_listing import ArtisanListing
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.shared import (
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
    Review,
)
from app.models.showcase_item import ShowcaseItem
from app.models.tour_guide import TourGuide, TourGuideAvailability, TourGuidePackage
from app.models.user import User
from app.routers.booking import cancel_booking, record_cash_payment
from app.routers.homestay_reviews import submit_homestay_review
from app.routers.payments import (
    calculate_provider_earnings_balances,
    handle_payhere_callback,
    initiate_booking_payment,
    process_refund,
)
from app.routers.provider_reservations import (
    ArtisanReservationRequest,
    GuideReservationRequest,
)
from app.schemas.booking import BookingCancelRequest, CashPaymentRecordRequest
from app.schemas.homestay_review import HomestayReviewCreateRequest
from app.schemas.payment import RefundProcessRequest
from app.schemas.provider_workspace import DashboardPeriod
from app.services.provider_reservations import (
    ARTISAN,
    GUIDE,
    LOCAL,
    change_activity_booking,
    change_reservation,
    reserve_artisan,
    reserve_guide,
)
from app.services.provider_workspace import WorkspaceReader
from tests import test_provider_phase01_sqlserver, test_provider_workspace_sqlserver

engine = test_provider_phase01_sqlserver.engine
transaction = test_provider_phase01_sqlserver.transaction
make_profile = test_provider_workspace_sqlserver.profile
make_traveler = test_provider_workspace_sqlserver.traveler
pytestmark = pytest.mark.skipif(
    os.getenv("PROVIDER_PHASE03_SQLSERVER") != "1",
    reason="Enable explicitly for the isolated SQL Server test database",
)


def test_sqlserver_artisan_stock_cash_and_dashboard(transaction):
    db = transaction
    owner, buyer = make_profile(db, "artisan"), make_traveler(db)
    listing = ArtisanListing(
        provider_profile_id=owner.id,
        title="Phase 03 shop",
        craft_category="wood",
        city="Fort",
        district="Colombo",
        approval_status="approved",
        is_verified=True,
        is_active=True,
    )
    db.add(listing)
    db.flush()
    item = ShowcaseItem(
        artisan_listing_id=listing.id, title="Bowl", price=200, stock_quantity=1
    )
    db.add(item)
    db.flush()
    payload = ArtisanReservationRequest(
        item_id=item.id,
        quantity=1,
        pickup_date=datetime.now(LOCAL).date() + timedelta(days=2),
    )
    row = reserve_artisan(db, buyer, payload)
    assert item.stock_quantity == 0
    with pytest.raises(HTTPException) as duplicate:
        reserve_artisan(db, buyer, payload)
    assert duplicate.value.status_code == 409
    change_reservation(db, ARTISAN, row["id"], owner.user, "accept", None, owner)
    receipt = change_reservation(
        db, ARTISAN, row["id"], owner.user, "cash", None, owner
    )
    assert receipt["payment_status"] == "paid"
    assert (
        db.scalar(
            select(PaymentTransaction.payment_status).where(
                PaymentTransaction.user_id == buyer.id
            )
        )
        == "completed"
    )
    dashboard = WorkspaceReader(db, owner).dashboard(DashboardPeriod(), 5, 5)
    assert dashboard.bookings.upcoming == 1 and dashboard.finance is None


def test_sqlserver_guide_reservation_respects_owned_availability(transaction):
    db = transaction
    owner, buyer = make_profile(db, "tour_guide"), make_traveler(db)
    guide = TourGuide(
        provider_profile_id=owner.id,
        bio="Phase 03 integration guide",
        base_city="Fort",
        base_district="Colombo",
        approval_status="approved",
        is_active=True,
        daily_rate=500,
    )
    db.add(guide)
    db.flush()
    package = TourGuidePackage(
        tour_guide_id=guide.id,
        title="Walk",
        description="Fort walk",
        duration_hours=2,
        price=100,
        max_people=3,
        meeting_point="Fort",
    )
    day = datetime.now(LOCAL).date() + timedelta(days=2)
    db.add_all(
        [
            package,
            TourGuideAvailability(
                tour_guide_id=guide.id,
                available_date=day,
                start_time=time(9),
                end_time=time(15),
            ),
        ]
    )
    db.flush()
    payload = GuideReservationRequest(
        guide_id=guide.id,
        package_id=package.id,
        people=2,
        starts_at=datetime.combine(day, time(10), LOCAL),
    )
    booking = reserve_guide(db, buyer, payload)
    with pytest.raises(HTTPException) as overlap:
        reserve_guide(db, buyer, payload)
    assert overlap.value.status_code == 409
    row = change_reservation(
        db, GUIDE, booking["id"], owner.user, "reject", None, owner
    )
    assert row["status"] == "rejected"
    assert reserve_guide(db, buyer, payload)["status"] == "pending"


def test_sqlserver_homestay_receipt_and_review_write(transaction):
    db = transaction
    owner, buyer = make_profile(db, "homestay"), make_traveler(db)
    stay = Homestay(
        owner_id=owner.user_id,
        provider_profile_id=owner.id,
        name="Phase 03 Homestay",
        location="Colombo",
        address="Fort",
        district="Colombo",
        city="Fort",
        approval_status="approved",
        is_active=True,
        price_per_night=100,
        max_guests=3,
    )
    db.add(stay)
    db.flush()
    today = datetime.now(UTC).date()
    booking = Booking(
        homestay_id=stay.id,
        user_id=buyer.id,
        check_in=today - timedelta(days=2),
        check_out=today - timedelta(days=1),
        guests=2,
        status="completed",
        total_price=200,
        payment_method="pay_at_location",
    )
    db.add(booking)
    db.flush()
    result = record_cash_payment(booking.id, CashPaymentRecordRequest(), db, owner.user)
    assert result.payment_status == "paid"
    assert calculate_provider_earnings_balances(db, owner.id)["total_gross_earnings"] == 0
    assert (
        db.scalar(
            select(PaymentTransaction.payment_status).where(
                PaymentTransaction.user_id == buyer.id
            )
        )
        == "completed"
    )
    submit_homestay_review(
        stay.id,
        HomestayReviewCreateRequest(booking_id=booking.id, rating=5),
        buyer,
        db,
    )
    assert (
        db.scalar(select(Review.updated_at).where(Review.user_id == buyer.id))
        is not None
    )


def test_sqlserver_activity_provider_cancellation_releases_capacity(transaction):
    db = transaction
    owner, buyer = make_profile(db, "activity"), make_traveler(db)
    activity = Activity(
        provider_profile_id=owner.id,
        title="Phase 03 walk",
        description="Fort walk",
        category="cultural",
        district="Colombo",
        city="Fort",
        meeting_point="Fort",
        duration_minutes=90,
        pricing_type="per_person",
        price=100,
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )
    db.add(activity)
    db.flush()
    start = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=2)
    slot = ActivitySlot(
        activity_id=activity.id,
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        capacity=3,
        reserved_count=2,
    )
    db.add(slot)
    db.flush()
    booking = ActivityBooking(
        activity_slot_id=slot.id,
        user_id=buyer.id,
        participants=2,
        total_price=200,
        payment_method="pay_at_location",
        booking_status="confirmed",
    )
    db.add(booking)
    db.flush()
    change_activity_booking(db, owner, booking.id, "cancel", "Fixture cancellation")
    db.refresh(slot)
    db.refresh(booking)
    assert slot.reserved_count == 0 and booking.booking_status == "cancelled"
    assert (
        db.scalar(
            select(BookingStatusHistory.to_status).where(
                BookingStatusHistory.changed_by == owner.user_id,
            )
        )
        == "cancelled"
    )


def test_sqlserver_capture_and_refund_sync(transaction, monkeypatch):
    db = transaction
    owner, buyer = make_profile(db, "homestay"), make_traveler(db)
    admin = test_provider_phase01_sqlserver.make_admin(db)
    stay = Homestay(
        owner_id=owner.user_id,
        provider_profile_id=owner.id,
        name="Phase 03 online",
        location="Colombo",
        address="Fort",
        district="Colombo",
        city="Fort",
        approval_status="approved",
        is_active=True,
        price_per_night=100,
        max_guests=3,
    )
    db.add(stay)
    db.flush()
    today = datetime.now(UTC).date()
    booking = Booking(
        homestay_id=stay.id,
        user_id=buyer.id,
        check_in=today + timedelta(days=6),
        check_out=today + timedelta(days=8),
        guests=2,
        status="awaiting_payment",
        total_price=200,
        payment_method="online",
    )
    db.add(booking)
    db.flush()
    checkout = initiate_booking_payment(booking.id, buyer, db)
    monkeypatch.setattr(payhere_service, "verify_ipn_signature", lambda **_: True)
    callback = {
        "merchant_id": payhere_service.merchant_id,
        "order_id": checkout.order_id,
        "payment_id": f"phase03-{booking.id}",
        "payhere_amount": "200.00",
        "payhere_currency": "LKR",
        "status_code": "2",
        "md5sig": "fixture",
    }
    with pytest.raises(HTTPException) as mismatch:
        handle_payhere_callback({**callback, "payhere_amount": "199.00"}, db)
    assert mismatch.value.status_code == 400
    assert handle_payhere_callback(callback, db)["status"] == "ok"
    assert handle_payhere_callback(callback, db)["status"] == "already_processed"
    assert booking.payment_status == "paid"
    assert calculate_provider_earnings_balances(db, owner.id)["total_gross_earnings"] == 200
    cancel_booking(
        booking_id=booking.id,
        payload=BookingCancelRequest(),
        db=db,
        current_user=buyer,
    )
    tx = db.scalar(
        select(PaymentTransaction).where(PaymentTransaction.user_id == buyer.id)
    )
    refund = db.scalar(
        select(PaymentRefund).where(PaymentRefund.payment_transaction_id == tx.id)
    )
    assert refund is not None and refund.refund_status == "pending"
    decision = RefundProcessRequest(status="completed", notes="Bank REF: PHASE03-123")
    process_refund(refund.id, decision, admin, db)
    assert refund.refund_status == "processed"
    assert booking.payment_status == "refunded"
    with pytest.raises(HTTPException) as replay:
        process_refund(refund.id, decision, admin, db)
    assert replay.value.status_code == 409


def test_sqlserver_reservation_lock_conflict_is_bounded(engine):
    with engine.connect() as first, engine.connect() as second:
        for connection in (first, second):
            test_provider_phase01_sqlserver.verify_target(connection)
            connection.commit()
        with Session(bind=first) as owner, Session(bind=second) as contender:
            key = "tour_guide_reservations_phase03_test"
            lock_booking_resource(owner, key)
            try:
                with pytest.raises(HTTPException) as conflict:
                    lock_booking_resource(contender, key)
                assert conflict.value.status_code == 503
            finally:
                owner.rollback()
            lock_booking_resource(contender, key)
            contender.rollback()
