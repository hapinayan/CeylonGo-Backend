"""
Automated verification tests for CeylonGo Module 04 — Phase 5C:
T16 Booking Lifecycle Notifications.

Requirements Covered:
- T16: Booking Notifications
  * Booking creation (notifies traveler & provider)
  * Booking acceptance (notifies traveler)
  * Booking rejection (notifies traveler with reason)
  * Traveler cancellation (notifies traveler & provider)
  * Cash payment confirmation (notifies traveler)

Integrity & Safety Rules:
- Reuses existing notification infrastructure (create_notification, dbo.notifications).
- Strictly routes notifications to the intended traveler and provider.
- Deduplication prevents duplicate alerts from retries within 30 seconds.
- Transactional atomicity: notifications roll back if transaction fails.
- Fault tolerance: notification dispatch failure does not corrupt booking mutation.
- Privacy: no private contact details leaked across user roles.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.notification_service import create_notification
from app.core.security import hash_password
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, Notification
from app.models.user import Base, User
from app.services.eco_booking_pricing import EcoCancellationResult, EcoPricingResult
from app.services.eco_booking_service import (
    EcoBookingValidationPlan,
    EcoCancellationValidationPlan,
    EcoProviderDecisionValidationPlan,
    EcoWeatherEmergencyPlan,
    dispatch_booking_cancelled_notifications,
    dispatch_booking_created_notifications,
    dispatch_provider_decision_notifications,
    execute_provider_decision_atomic,
    execute_traveler_booking_cancel_atomic,
    execute_traveler_booking_create_atomic,
    execute_weather_emergency_cancellation_atomic,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, expire_on_commit=False
)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seed_data(db_session: Session):
    # 1. Users
    provider_user = User(
        email="provider@ceylongo.com",
        password_hash=hash_password("ProviderPass123!"),
        full_name="Sinharaja Nature Treks",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        email="traveler@ceylongo.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Emma Watson",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([provider_user, traveler_user])
    db_session.flush()

    # 2. Provider Profile
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Sinharaja Eco Adventures",
        business_registration_number="PV-ECO-NOTIF-01",
        provider_type="eco_tourism",
        phone="+94771234567",
        district="Ratnapura",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.flush()

    # 3. Base Activity
    base_activity = Activity(
        provider_profile_id=profile.id,
        title="Sinharaja Rainforest Expedition",
        description="Base activity for slot FK",
        category="adventure",
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kalawana Park Gate",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("6000.00"),
    )
    db_session.add(base_activity)
    db_session.flush()

    # 4. Eco Experience
    exp = EcoExperience(
        provider_profile_id=profile.id,
        title="Sinharaja Primary Forest Walk",
        eco_category="Forest & Nature Trails",
        description="Guided walk through UNESCO biosphere reserve.",
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kalawana Entrance Point",
        duration_hours=Decimal("4.00"),
        base_price=Decimal("6000.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=1,
        min_age=8,
        environmental_guidelines="Strict zero-waste policy.",
        wildlife_safety_summary="Do not feed or touch wildlife.",
        conservation_factual_contribution="5% of fees support local sapling planting.",
        conservation_marketing_statement="Protecting rainforest canopy.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="granted",
    )
    db_session.add(exp)
    db_session.flush()

    # 5. Activity Slot
    slot = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=exp.id,
        starts_at=datetime.now(UTC) + timedelta(days=3),
        ends_at=datetime.now(UTC) + timedelta(days=3, hours=4),
        capacity=10,
        reserved_count=0,
        is_active=True,
    )
    db_session.add(slot)
    db_session.commit()

    return {
        "provider_user": provider_user,
        "traveler_user": traveler_user,
        "profile": profile,
        "base_activity": base_activity,
        "exp": exp,
        "slot": slot,
    }


def test_booking_creation_generates_notifications_for_traveler_and_provider(
    db_session: Session, seed_data: dict
):
    """
    T16: Booking creation automatically creates notifications for:
    1. Traveler (confirmation of request submission).
    2. Provider (alert of incoming booking request).
    """
    traveler = seed_data["traveler_user"]
    provider = seed_data["provider_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    plan = EcoBookingValidationPlan(
        eco_experience_id=exp.id,
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=2,
        pricing=EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("6000.00"),
            participants=2,
            total_price=Decimal("12000.00"),
            currency="LKR",
        ),
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests="Need vegetarian lunch",
        participant_roster=[
            {"full_name": "Emma Watson", "age": 28},
            {"full_name": "John Doe", "age": 30},
        ],
        experience_title=exp.title,
        slot_starts_at=slot.starts_at,
        provider_profile_id=profile.id,
    )

    res = execute_traveler_booking_create_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["status"] == "pending"

    # 1. Verify Traveler Notification
    t_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_booking_created",
        )
        .first()
    )
    assert t_notif is not None
    assert exp.title in t_notif.title
    assert "awaiting provider confirmation" in t_notif.message
    assert t_notif.link_url == "/my-bookings"

    # 2. Verify Provider Notification
    p_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == provider.id,
            Notification.notification_type == "eco_booking_created",
        )
        .first()
    )
    assert p_notif is not None
    assert exp.title in p_notif.title
    assert "2 participant(s)" in p_notif.message
    assert p_notif.link_url == "/provider/eco-bookings"


def test_booking_acceptance_generates_notification_for_traveler(
    db_session: Session, seed_data: dict
):
    """
    T16: When provider accepts a pending booking, traveler receives confirmation notification.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    # Create pending booking
    booking = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
    )
    db_session.add(booking)
    db_session.commit()

    # Provider decision: accept
    plan = EcoProviderDecisionValidationPlan(
        booking_id=booking.id,
        provider_profile_id=profile.id,
        eco_experience_id=exp.id,
        action="accept",
        from_booking_status="pending",
        to_booking_status="confirmed",
        from_payment_status="unpaid",
        to_payment_status="unpaid",
        slot_id=slot.id,
        participants_to_release=0,
        note=None,
    )

    res = execute_provider_decision_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["booking_status"] == "confirmed"

    # Verify Traveler Notification
    notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_booking_accepted",
        )
        .first()
    )
    assert notif is not None
    assert "Confirmed" in notif.title
    assert exp.title in notif.title
    assert "has been confirmed" in notif.message


def test_booking_rejection_generates_notification_with_reason(
    db_session: Session, seed_data: dict
):
    """
    T16: When provider rejects a pending booking, traveler receives notification with rejection note.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]
    slot.reserved_count = 2
    db_session.commit()

    booking = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
    )
    db_session.add(booking)
    db_session.commit()

    # Provider decision: reject with reason
    rejection_reason = "Weather alert: Severe rainfall expected on the trail."
    plan = EcoProviderDecisionValidationPlan(
        booking_id=booking.id,
        provider_profile_id=profile.id,
        eco_experience_id=exp.id,
        action="reject",
        from_booking_status="pending",
        to_booking_status="rejected",
        from_payment_status="unpaid",
        to_payment_status="unpaid",
        slot_id=slot.id,
        participants_to_release=2,
        note=rejection_reason,
    )

    res = execute_provider_decision_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["booking_status"] == "rejected"

    # Verify Traveler Notification contains the reason
    notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_booking_rejected",
        )
        .first()
    )
    assert notif is not None
    assert "Declined" in notif.title
    assert rejection_reason in notif.message


def test_booking_cancellation_generates_notifications(
    db_session: Session, seed_data: dict
):
    """
    T16: When traveler cancels an eligible booking, both traveler and provider are notified.
    """
    traveler = seed_data["traveler_user"]
    provider = seed_data["provider_user"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]
    slot.reserved_count = 2
    db_session.commit()

    booking = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    plan = EcoCancellationValidationPlan(
        booking_id=booking.id,
        traveler_id=traveler.id,
        slot_id=slot.id,
        eco_experience_id=exp.id,
        participants_to_release=2,
        cancellation=EcoCancellationResult(
            is_free_cancellation=True,
            cancellation_deadline=slot.starts_at - timedelta(hours=24),
            cancellation_requested_at=datetime.now(UTC),
            slot_start=slot.starts_at,
            free_cancellation_hours=24,
            hours_until_slot=72.0,
            penalty_applicable=False,
        ),
        reason="Flight rescheduled",
    )

    res = execute_traveler_booking_cancel_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["status"] == "cancelled"

    # 1. Traveler Notification
    t_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_booking_cancelled",
        )
        .first()
    )
    assert t_notif is not None
    assert "Cancelled" in t_notif.title
    assert "successfully cancelled" in t_notif.message

    # 2. Provider Notification
    p_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == provider.id,
            Notification.notification_type == "eco_booking_cancelled",
        )
        .first()
    )
    assert p_notif is not None
    assert "Flight rescheduled" in p_notif.message


def test_confirm_cash_payment_generates_notification_for_traveler(
    db_session: Session, seed_data: dict
):
    """
    T16: When provider confirms cash receipt, traveler receives payment confirmation notification.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    booking = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    plan = EcoProviderDecisionValidationPlan(
        booking_id=booking.id,
        provider_profile_id=profile.id,
        eco_experience_id=exp.id,
        action="confirm_cash",
        from_booking_status="confirmed",
        to_booking_status="confirmed",
        from_payment_status="unpaid",
        to_payment_status="paid",
        slot_id=slot.id,
        participants_to_release=0,
        note=None,
    )

    res = execute_provider_decision_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["payment_status"] == "paid"

    notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_payment_confirmed",
        )
        .first()
    )
    assert notif is not None
    assert "Payment Recorded" in notif.title
    assert "12000" in notif.message


def test_transaction_rollback_does_not_persist_notifications(
    db_session: Session, seed_data: dict
):
    """
    T16 Integrity: Notifications are transactional and never persisted if transaction rolls back.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    # Capacity is 10; requesting 15 exceeds capacity and causes atomic savepoint rollback
    plan = EcoBookingValidationPlan(
        eco_experience_id=exp.id,
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=15,
        pricing=EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("6000.00"),
            participants=15,
            total_price=Decimal("90000.00"),
            currency="LKR",
        ),
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests=None,
        participant_roster=None,
        experience_title=exp.title,
        slot_starts_at=slot.starts_at,
        provider_profile_id=profile.id,
    )

    with pytest.raises(Exception):
        execute_traveler_booking_create_atomic(db=db_session, plan=plan)

    # Verify NO notification persisted because savepoint rolled back
    notifs = (
        db_session.query(Notification)
        .filter(Notification.user_id == traveler.id)
        .all()
    )
    assert len(notifs) == 0


def test_notification_dispatch_failure_does_not_corrupt_booking(
    db_session: Session, seed_data: dict
):
    """
    T16 Fault Tolerance: If notification creation fails, the booking mutation remains intact.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    plan = EcoBookingValidationPlan(
        eco_experience_id=exp.id,
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=1,
        pricing=EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("6000.00"),
            participants=1,
            total_price=Decimal("6000.00"),
            currency="LKR",
        ),
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests=None,
        participant_roster=None,
        experience_title=exp.title,
        slot_starts_at=slot.starts_at,
        provider_profile_id=profile.id,
    )

    with patch(
        "app.services.eco_booking_service.dispatch_booking_created_notifications",
        side_effect=Exception("Notification service network failure"),
    ):
        res = execute_traveler_booking_create_atomic(db=db_session, plan=plan)
        db_session.commit()

        # Booking creation succeeded despite notification failure
        assert res["status"] == "pending"
        booking_in_db = (
            db_session.query(ActivityBooking)
            .filter(ActivityBooking.id == res["booking_id"])
            .first()
        )
        assert booking_in_db is not None


def test_confirm_cash_payment_handles_usd_currency(
    db_session: Session, seed_data: dict
):
    """
    T16: When provider confirms cash receipt for a USD booking, the notification
    message correctly formats USD rather than hardcoded LKR.
    """
    traveler = seed_data["traveler_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    booking = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=1,
        total_price=Decimal("45.00"),
        currency="USD",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    plan = EcoProviderDecisionValidationPlan(
        booking_id=booking.id,
        provider_profile_id=profile.id,
        eco_experience_id=exp.id,
        action="confirm_cash",
        from_booking_status="confirmed",
        to_booking_status="confirmed",
        from_payment_status="unpaid",
        to_payment_status="paid",
        slot_id=slot.id,
        participants_to_release=0,
        note=None,
    )

    res = execute_provider_decision_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["payment_status"] == "paid"

    notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_payment_confirmed",
        )
        .first()
    )
    assert notif is not None
    assert "Payment Recorded" in notif.title
    assert "USD 45.00" in notif.message


def test_notification_deduplication_within_window(
    db_session: Session, seed_data: dict
):
    """
    T16 Idempotency: Duplicate notifications with the same user, type, and title
    within the 30-second deduplication window reuse the existing row.
    """
    traveler = seed_data["traveler_user"]

    notif1 = create_notification(
        db=db_session,
        user_id=traveler.id,
        title="Test Booking Alert",
        message="Your booking is confirmed.",
        notification_type="eco_booking_accepted",
        link_url="/my-bookings",
    )
    db_session.commit()
    assert notif1 is not None

    # Immediate duplicate request with same user, type, and title
    notif2 = create_notification(
        db=db_session,
        user_id=traveler.id,
        title="Test Booking Alert",
        message="Your booking is confirmed (retry).",
        notification_type="eco_booking_accepted",
        link_url="/my-bookings",
    )
    db_session.commit()

    # Reuses existing notification ID; no second row inserted
    assert notif2.id == notif1.id
    total_count = (
        db_session.query(Notification)
        .filter(Notification.user_id == traveler.id)
        .count()
    )
    assert total_count == 1


def test_cross_user_notification_isolation(
    db_session: Session, seed_data: dict
):
    """
    T16 Isolation: Notifications are strictly routed to the involved parties.
    Unrelated travelers and providers receive zero notifications.
    """
    traveler_a = seed_data["traveler_user"]
    provider_a = seed_data["provider_user"]
    profile_a = seed_data["profile"]
    exp_a = seed_data["exp"]
    slot_a = seed_data["slot"]

    # Create unrelated Traveler B and Provider B
    traveler_b = User(
        email="traveler_b@ceylongo.com",
        password_hash=hash_password("Pass123!"),
        full_name="Bob Traveler",
        role="traveler",
        is_active=True,
    )
    provider_b = User(
        email="provider_b@ceylongo.com",
        password_hash=hash_password("Pass123!"),
        full_name="Other Provider",
        role="provider",
        is_active=True,
    )
    db_session.add_all([traveler_b, provider_b])
    db_session.flush()

    # Traveler A books Experience A
    plan = EcoBookingValidationPlan(
        eco_experience_id=exp_a.id,
        slot_id=slot_a.id,
        traveler_id=traveler_a.id,
        participants=2,
        pricing=EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("6000.00"),
            participants=2,
            total_price=Decimal("12000.00"),
            currency="LKR",
        ),
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests=None,
        participant_roster=None,
        experience_title=exp_a.title,
        slot_starts_at=slot_a.starts_at,
        provider_profile_id=profile_a.id,
    )

    execute_traveler_booking_create_atomic(db=db_session, plan=plan)
    db_session.commit()

    # Verify Traveler A and Provider A received notifications
    notifs_a = db_session.query(Notification).filter(Notification.user_id == traveler_a.id).count()
    notifs_prov_a = db_session.query(Notification).filter(Notification.user_id == provider_a.id).count()
    assert notifs_a >= 1
    assert notifs_prov_a >= 1

    # Verify Traveler B and Provider B received NO notifications
    notifs_b = db_session.query(Notification).filter(Notification.user_id == traveler_b.id).count()
    notifs_prov_b = db_session.query(Notification).filter(Notification.user_id == provider_b.id).count()
    assert notifs_b == 0
    assert notifs_prov_b == 0


def test_weather_emergency_cancellation_notifications(
    db_session: Session, seed_data: dict
):
    """
    T16 & E06: Declaring a weather emergency cancels affected bookings and dispatches:
    1. Weather cancellation notifications to all affected travelers with full refund notice.
    2. Operational restriction notification to the provider.
    """
    provider = seed_data["provider_user"]
    profile = seed_data["profile"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    # Create second traveler
    traveler2 = User(
        email="traveler2@ceylongo.com",
        password_hash=hash_password("Pass123!"),
        full_name="David Attenborough",
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler2)
    db_session.flush()

    # Create bookings for both travelers on this slot
    b1 = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=seed_data["traveler_user"].id,
        participants=2,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
    )
    b2 = ActivityBooking(
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler2.id,
        participants=3,
        total_price=Decimal("18000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
    )
    db_session.add_all([b1, b2])
    slot.reserved_count = 5
    db_session.commit()

    # Plan weather emergency cancellation
    start_d = (datetime.now(UTC) + timedelta(days=2)).date()
    end_d = (datetime.now(UTC) + timedelta(days=5)).date()
    plan = EcoWeatherEmergencyPlan(
        eco_experience_id=exp.id,
        provider_profile_id=profile.id,
        provider_user_id=provider.id,
        experience_title=exp.title,
        hazard_type="weather_alert",
        reason="Severe monsoonal flooding and flash flood warning.",
        affected_slots=[(slot.id, slot.reserved_count, slot.starts_at, slot.ends_at)],
        start_date=start_d,
        end_date=end_d,
    )

    res = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["cancelled_bookings_count"] == 2

    # Verify Traveler 1 notification
    t1_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == seed_data["traveler_user"].id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .first()
    )
    assert t1_notif is not None
    assert "Weather Emergency: Booking Cancelled" in t1_notif.title
    assert "Severe monsoonal flooding" in t1_notif.message
    assert "100% full refund" in t1_notif.message

    # Verify Traveler 2 notification
    t2_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler2.id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .first()
    )
    assert t2_notif is not None
    assert "Weather Emergency: Booking Cancelled" in t2_notif.title
    assert "Severe monsoonal flooding" in t2_notif.message

    # Verify Provider notification
    p_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == provider.id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .first()
    )
    assert p_notif is not None
    assert "Weather Emergency Restrictions Applied" in p_notif.title
    assert "2 bookings cancelled" in p_notif.message


def test_notification_dispatch_handles_missing_provider_gracefully(
    db_session: Session, seed_data: dict
):
    """
    T16 Robustness: When the provider profile has no associated user,
    notification dispatch does not raise an unhandled exception.
    """
    traveler = seed_data["traveler_user"]
    exp = seed_data["exp"]
    slot = seed_data["slot"]

    plan = EcoBookingValidationPlan(
        eco_experience_id=exp.id,
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=1,
        pricing=EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("6000.00"),
            participants=1,
            total_price=Decimal("6000.00"),
            currency="LKR",
        ),
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests=None,
        participant_roster=None,
        experience_title=exp.title,
        slot_starts_at=slot.starts_at,
        provider_profile_id=999999,  # Nonexistent provider profile
    )

    # Dispatch should not raise an error despite nonexistent provider
    dispatch_booking_created_notifications(
        db=db_session,
        booking_id=123,
        booking_ref_code="ECO-20261120-0123",
        plan=plan,
    )
    db_session.commit()

    # Traveler still received notification
    t_notif = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler.id,
            Notification.notification_type == "eco_booking_created",
        )
        .first()
    )
    assert t_notif is not None
