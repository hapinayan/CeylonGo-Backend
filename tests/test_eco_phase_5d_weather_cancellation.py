"""
Automated verification tests for CeylonGo Module 04 — Phase 5D:
E06 Weather & Seasonal Restrictions (Weather Emergency Cancellation Workflow).

Requirements Covered:
- E06: Weather & Seasonal Restrictions
  * Provider weather hazard declaration & authorization
  * Ownership and eligibility verification
  * Slot deactivation preventing new reservations
  * Safe cancellation of existing pending and confirmed bookings
  * Safe capacity release without underflow
  * Cancellation reason and audit recording
  * Refund tracking on approved payment infrastructure (dbo.payment_refunds)
  * Duplicate cancellation and duplicate refund prevention (idempotency)
  * Future reservation blocking via active EcoSeasonRestriction
  * In-app notification dispatch to travelers and provider
  * Transaction rollback on failure
  * Live mounted router endpoint (POST /api/v1/eco-experiences/{id}/weather-emergency)
  * Staged router endpoint (POST /api/v1/provider/eco-experiences/{id}/slots/{slot_id}/weather-cancel)

Safety Rules:
- No shared Module 03 model modifications.
- No schema migrations.
- In-memory SQLite with SQL Server sysutcdatetime function registration.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import require_approved_eco_provider
from app.core.security import hash_password
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, Notification, PaymentRefund, PaymentTransaction
from app.models.user import Base, User
from app.routers.eco_experiences import router as eco_experiences_router
from app.routers.eco_slots import get_public_eco_slots, router as staged_eco_slots_router
from app.schemas.eco_booking import EcoBookingCreate
from app.schemas.eco_experience import EcoWeatherEmergencyRequest
from app.schemas.eco_slot import EcoSlotWeatherCancelRequest
from app.services.eco_booking_service import (
    EcoBookingServiceError,
    EcoWeatherEmergencyPlan,
    execute_weather_emergency_cancellation_atomic,
    validate_booking_request_read_only,
    validate_weather_emergency_cancellation_read_only,
)
from app.services.eco_experience_eligibility import (
    EcoEligibilityException,
    validate_eco_booking_eligibility,
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
        email="eco_provider@ceylongo.com",
        password_hash=hash_password("ProviderPass123!"),
        full_name="Knuckles Eco Treks",
        role="provider",
        is_active=True,
    )
    unapproved_provider_user = User(
        email="unapproved@ceylongo.com",
        password_hash=hash_password("UnapprovedPass123!"),
        full_name="Unapproved Operator",
        role="provider",
        is_active=True,
    )
    other_provider_user = User(
        email="other@ceylongo.com",
        password_hash=hash_password("OtherPass123!"),
        full_name="Other Provider",
        role="provider",
        is_active=True,
    )
    non_eco_user = User(
        email="noneco@ceylongo.com",
        password_hash=hash_password("NonEcoPass123!"),
        full_name="Homestay Owner",
        role="provider",
        is_active=True,
    )
    traveler_user_1 = User(
        email="traveler1@ceylongo.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Kamal Perera",
        role="traveler",
        is_active=True,
    )
    traveler_user_2 = User(
        email="traveler2@ceylongo.com",
        password_hash=hash_password("TravelerPass123!"),
        full_name="Nimal Silva",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([
        provider_user,
        unapproved_provider_user,
        other_provider_user,
        non_eco_user,
        traveler_user_1,
        traveler_user_2,
    ])
    db_session.commit()

    # 2. Provider Profiles
    provider_profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Knuckles Wilderness Adventures",
        business_registration_number="PV123456",
        provider_type="eco_tourism",
        phone="+94771234567",
        district="Matale",
        approval_status="approved",
    )
    unapproved_provider_profile = ProviderProfile(
        user_id=unapproved_provider_user.id,
        business_name="Unapproved Wilderness",
        business_registration_number="PV654321",
        provider_type="eco_tourism",
        phone="+94771234568",
        district="Matale",
        approval_status="pending",
    )
    other_provider_profile = ProviderProfile(
        user_id=other_provider_user.id,
        business_name="Other Adventures",
        business_registration_number="PV999999",
        provider_type="eco_tourism",
        phone="+94771234569",
        district="Kandy",
        approval_status="approved",
    )
    non_eco_provider_profile = ProviderProfile(
        user_id=non_eco_user.id,
        business_name="Hotel Transylvania",
        business_registration_number="PV888888",
        provider_type="homestay",
        phone="+94771234560",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add_all([
        provider_profile,
        unapproved_provider_profile,
        other_provider_profile,
        non_eco_provider_profile,
    ])
    db_session.commit()

    # 3. Base Activity
    base_activity = Activity(
        provider_profile_id=provider_profile.id,
        title="Knuckles Trek Base Activity",
        description="Base activity for slot FK",
        category="adventure",
        district="Matale",
        city="Riverston",
        meeting_point="Riverston Gap",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("4500.00"),
    )
    db_session.add(base_activity)
    db_session.flush()

    # 4. Eco Experience
    experience = EcoExperience(
        provider_profile_id=provider_profile.id,
        title="Knuckles Cloud Forest Trail",
        eco_category="Forest & Nature Trails",
        description="A rigorous nature exploration following Leave No Trace principles.",
        district="Matale",
        city="Riverston",
        meeting_point="Riverston Gap",
        duration_hours=Decimal("4.00"),
        base_price=Decimal("4500.00"),
        currency="LKR",
        min_participants=1,
        max_group_size=15,
        min_age=8,
        environmental_guidelines="Strict zero-waste policy.",
        wildlife_safety_summary="Do not feed or touch wildlife.",
        conservation_factual_contribution="5% of fees support local tree planting.",
        conservation_marketing_statement="Protecting rainforest canopy.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="granted",
    )
    db_session.add(experience)
    db_session.flush()

    # 5. Scheduled Future Slots
    now = datetime.now(UTC)
    slot1_start = now + timedelta(days=3, hours=8)
    slot1_end = slot1_start + timedelta(hours=5)
    slot1 = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=experience.id,
        starts_at=slot1_start,
        ends_at=slot1_end,
        capacity=10,
        reserved_count=5,
        is_active=True,
    )

    slot2_start = now + timedelta(days=4, hours=8)
    slot2_end = slot2_start + timedelta(hours=5)
    slot2 = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=experience.id,
        starts_at=slot2_start,
        ends_at=slot2_end,
        capacity=10,
        reserved_count=0,
        is_active=True,
    )
    db_session.add_all([slot1, slot2])
    db_session.commit()

    # 5. Bookings on Slot 1
    # Booking 1: Confirmed with online payment and PaymentTransaction
    booking1 = ActivityBooking(
        activity_slot_id=slot1.id,
        eco_experience_id=experience.id,
        user_id=traveler_user_1.id,
        participants=3,
        total_price=Decimal("13500.00"),
        payment_method="online",
        payment_status="paid",
        booking_status="confirmed",
        created_at=now - timedelta(hours=12),
    )
    # Booking 2: Pending with cash on arrival (unpaid)
    booking2 = ActivityBooking(
        activity_slot_id=slot1.id,
        eco_experience_id=experience.id,
        user_id=traveler_user_2.id,
        participants=2,
        total_price=Decimal("9000.00"),
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=now - timedelta(hours=6),
    )
    db_session.add_all([booking1, booking2])
    db_session.commit()

    # 6. BookingReferences & PaymentTransaction
    ref1 = BookingReference(
        booking_reference="ECO-20261120-0001",
        module_type="eco_tourism",
        target_booking_id=booking1.id,
        user_id=traveler_user_1.id,
        provider_profile_id=provider_profile.id,
        total_price=Decimal("13500.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_status="paid",
    )
    ref2 = BookingReference(
        booking_reference="ECO-20261120-0002",
        module_type="eco_tourism",
        target_booking_id=booking2.id,
        user_id=traveler_user_2.id,
        provider_profile_id=provider_profile.id,
        total_price=Decimal("9000.00"),
        currency="LKR",
        booking_status="pending",
        payment_status="unpaid",
    )
    db_session.add_all([ref1, ref2])
    db_session.commit()

    tx1 = PaymentTransaction(
        booking_reference_id=ref1.id,
        user_id=traveler_user_1.id,
        gateway_name="payhere",
        gateway_transaction_id="TX-PAYHERE-987654",
        amount=Decimal("13500.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
        paid_at=now - timedelta(hours=11),
    )
    db_session.add(tx1)
    db_session.commit()

    return {
        "provider_user": provider_user,
        "provider_profile": provider_profile,
        "unapproved_provider_profile": unapproved_provider_profile,
        "other_provider_profile": other_provider_profile,
        "non_eco_provider_profile": non_eco_provider_profile,
        "traveler_user_1": traveler_user_1,
        "traveler_user_2": traveler_user_2,
        "experience": experience,
        "slot1": slot1,
        "slot2": slot2,
        "booking1": booking1,
        "booking2": booking2,
        "ref1": ref1,
        "ref2": ref2,
        "tx1": tx1,
    }


# ==============================================================================
# 1. Validation & Authorization Tests
# ==============================================================================


def test_weather_emergency_authorization_checks(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot = seed_data["slot1"]

    # 1. Non-eco provider rejected
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=seed_data["non_eco_provider_profile"],
            eco_experience_id=exp.id,
            reason="Heavy tropical storm warning",
            slot_id=slot.id,
        )
    assert exc_info.value.code == "ECO_PROVIDER_NOT_AUTHORIZED"

    # 2. Unapproved provider rejected
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=seed_data["unapproved_provider_profile"],
            eco_experience_id=exp.id,
            reason="Heavy tropical storm warning",
            slot_id=slot.id,
        )
    assert exc_info.value.code == "ECO_PROVIDER_NOT_APPROVED"

    # 3. Other provider (non-owner) rejected
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=seed_data["other_provider_profile"],
            eco_experience_id=exp.id,
            reason="Heavy tropical storm warning",
            slot_id=slot.id,
        )
    assert exc_info.value.code == "ECO_EXPERIENCE_NOT_OWNED"

    # 4. Non-existent experience
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=seed_data["provider_profile"],
            eco_experience_id=999999,
            reason="Heavy tropical storm warning",
            slot_id=slot.id,
        )
    assert exc_info.value.code == "ECO_EXPERIENCE_NOT_FOUND"


def test_weather_emergency_input_validation(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    provider = seed_data["provider_profile"]

    # 1. Invalid hazard type
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=provider,
            eco_experience_id=exp.id,
            reason="Severe flooding warning",
            hazard_type="alien_invasion",
            slot_id=seed_data["slot1"].id,
        )
    assert exc_info.value.code == "ECO_INVALID_HAZARD_TYPE"

    # 2. Reason too short (< 5 chars)
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=provider,
            eco_experience_id=exp.id,
            reason="Rain",
            slot_id=seed_data["slot1"].id,
        )
    assert exc_info.value.code == "ECO_INVALID_REASON"

    # 3. Missing target window and slot_id
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=provider,
            eco_experience_id=exp.id,
            reason="Severe weather hazard alert",
        )
    assert exc_info.value.code == "ECO_MISSING_TARGET_WINDOW"

    # 4. Invalid date range (end_date < start_date)
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_weather_emergency_cancellation_read_only(
            db=db_session,
            provider_profile=provider,
            eco_experience_id=exp.id,
            reason="Severe weather hazard alert",
            start_date=date(2026, 12, 10),
            end_date=date(2026, 12, 5),
        )
    assert exc_info.value.code == "ECO_INVALID_DATE_RANGE"


# ==============================================================================
# 2. Slot Deactivation, Booking Cancellation & Capacity Release
# ==============================================================================


def test_weather_emergency_cancels_slot_and_releases_capacity(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    b1 = seed_data["booking1"]
    b2 = seed_data["booking2"]
    ref1 = seed_data["ref1"]
    ref2 = seed_data["ref2"]
    provider = seed_data["provider_profile"]

    plan = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="DMC issued Red Alert for torrential rain and landslides",
        hazard_type="weather_alert",
        slot_id=slot1.id,
    )

    result = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
    db_session.commit()

    # 1. Result summary assertions
    assert result["status"] == "success"
    assert result["affected_slots_count"] == 1
    assert result["cancelled_bookings_count"] == 2
    assert result["released_capacity_total"] == 5  # 3 from b1 + 2 from b2
    assert b1.id in result["cancelled_booking_ids"]
    assert b2.id in result["cancelled_booking_ids"]

    # 2. Slot assertions
    db_session.refresh(slot1)
    assert slot1.is_active is False
    assert slot1.reserved_count == 0  # 5 - 5 = 0

    # 3. Booking assertions
    db_session.refresh(b1)
    db_session.refresh(b2)
    assert b1.booking_status == "cancelled"
    assert "Weather Emergency (weather_alert)" in b1.cancellation_reason
    assert b1.cancelled_at is not None
    assert b1.payment_status == "refund_pending"

    assert b2.booking_status == "cancelled"
    assert "Weather Emergency (weather_alert)" in b2.cancellation_reason
    assert b2.cancelled_at is not None
    assert b2.payment_status == "unpaid"  # was unpaid, stays unpaid

    # 4. BookingReference status sync assertions
    db_session.refresh(ref1)
    db_session.refresh(ref2)
    assert ref1.booking_status == "cancelled"
    assert ref1.payment_status == "refund_pending"
    assert ref2.booking_status == "cancelled"
    assert ref2.payment_status == "unpaid"


# ==============================================================================
# 3. Payment Refund Tracking & Idempotency
# ==============================================================================


def test_weather_emergency_refund_tracking_and_idempotency(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    tx1 = seed_data["tx1"]
    provider = seed_data["provider_profile"]

    plan = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Extreme monsoon flooding advisory",
        hazard_type="monsoon_season",
        slot_id=slot1.id,
    )

    # 1. First execution creates refund record
    res1 = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res1["refunds_initiated_count"] == 1
    assert Decimal(res1["total_refund_amount"]) == Decimal("13500.00")

    refund = (
        db_session.query(PaymentRefund)
        .filter(PaymentRefund.payment_transaction_id == tx1.id)
        .first()
    )
    assert refund is not None
    assert refund.amount == Decimal("13500.00")
    assert refund.currency == "LKR"
    assert refund.refund_status == "pending"
    assert "Weather Emergency (monsoon_season)" in refund.refund_reason

    # 2. Second execution on same slot (Idempotency)
    plan2 = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Extreme monsoon flooding advisory",
        hazard_type="monsoon_season",
        slot_id=slot1.id,
    )
    res2 = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan2)
    db_session.commit()

    # Bookings already cancelled -> 0 cancelled, 0 capacity double-released, 0 duplicate refunds
    assert res2["cancelled_bookings_count"] == 0
    assert res2["released_capacity_total"] == 0
    assert res2["refunds_initiated_count"] == 0
    assert Decimal(res2["total_refund_amount"]) == Decimal("0.00")

    # Confirm exactly ONE refund record exists in total
    refund_count = (
        db_session.query(PaymentRefund)
        .filter(PaymentRefund.payment_transaction_id == tx1.id)
        .count()
    )
    assert refund_count == 1


# ==============================================================================
# 4. Seasonal Restriction Creation & Future Blocking
# ==============================================================================


def test_weather_emergency_creates_season_restriction_and_blocks_new_slots(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    provider = seed_data["provider_profile"]

    hazard_start = date(2026, 12, 10)
    hazard_end = date(2026, 12, 15)

    plan = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Severe forest fire risk advisory",
        hazard_type="park_closure",
        start_date=hazard_start,
        end_date=hazard_end,
    )

    res = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
    db_session.commit()

    assert res["status"] == "success"
    assert res["restriction_id"] is not None

    # Check restriction persisted
    restriction = (
        db_session.query(EcoSeasonRestriction)
        .filter(EcoSeasonRestriction.id == res["restriction_id"])
        .first()
    )
    assert restriction is not None
    assert restriction.restriction_type == "park_closure"
    assert restriction.is_active is True
    assert restriction.start_date == hazard_start
    assert restriction.end_date == hazard_end

    # Verify that future slot booking during this window is blocked by eligibility service
    test_slot_start = datetime(2026, 12, 12, 9, 0, 0, tzinfo=UTC)
    test_slot_end = datetime(2026, 12, 12, 14, 0, 0, tzinfo=UTC)

    with pytest.raises(EcoEligibilityException) as exc_info:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=exp.id,
            participants=2,
            slot_start=test_slot_start,
            slot_end=test_slot_end,
        )
    assert exc_info.value.code == "ECO_SEASON_RESTRICTED"
    assert "overlaps with active seasonal restriction 'park_closure'" in exc_info.value.message


# ==============================================================================
# 5. In-App Notifications
# ==============================================================================


def test_weather_emergency_dispatches_notifications(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    traveler1 = seed_data["traveler_user_1"]
    traveler2 = seed_data["traveler_user_2"]
    provider_user = seed_data["provider_user"]
    provider = seed_data["provider_profile"]

    plan = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Heavy tropical storm warning",
        hazard_type="weather_alert",
        slot_id=slot1.id,
    )

    execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
    db_session.commit()

    # Verify traveler 1 notification
    t1_notifs = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler1.id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .all()
    )
    assert len(t1_notifs) == 1
    assert "Weather Emergency: Booking Cancelled" in t1_notifs[0].title
    assert "queued for 100% full refund" in t1_notifs[0].message

    # Verify traveler 2 notification
    t2_notifs = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == traveler2.id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .all()
    )
    assert len(t2_notifs) == 1
    assert "Weather Emergency: Booking Cancelled" in t2_notifs[0].title

    # Verify provider notification
    p_notifs = (
        db_session.query(Notification)
        .filter(
            Notification.user_id == provider_user.id,
            Notification.notification_type == "eco_weather_cancellation",
        )
        .all()
    )
    assert len(p_notifs) == 1
    assert "Weather Emergency Restrictions Applied" in p_notifs[0].title


# ==============================================================================
# 6. Transaction Rollback on Failure
# ==============================================================================


def test_weather_emergency_transaction_rollback_preserves_state(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    b1 = seed_data["booking1"]
    provider = seed_data["provider_profile"]

    plan = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Severe weather hazard",
        slot_id=slot1.id,
    )

    # Patch create_notification to raise an exception inside the atomic block
    with patch("app.services.eco_booking_service.acquire_slot_lock", side_effect=RuntimeError("Lock timeout")):
        with pytest.raises(RuntimeError) as exc_info:
            execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan)
        assert "Lock timeout" in str(exc_info.value)

    # State must be completely untouched due to savepoint rollback
    db_session.rollback()
    db_session.refresh(slot1)
    db_session.refresh(b1)
    assert slot1.is_active is True
    assert slot1.reserved_count == 5
    assert b1.booking_status == "confirmed"

    # Zero refund records created
    refunds = db_session.query(PaymentRefund).all()
    assert len(refunds) == 0


# ==============================================================================
# 7. Router Endpoints Integration Tests
# ==============================================================================


def test_mounted_router_declare_weather_emergency_endpoint(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    provider = seed_data["provider_profile"]

    app = FastAPI()
    app.include_router(eco_experiences_router)

    def override_get_db():
        yield db_session

    def override_require_provider():
        return provider

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_approved_eco_provider] = override_require_provider

    client = TestClient(app)

    payload = {
        "hazard_type": "weather_alert",
        "reason": "Severe gale force winds and fallen trees advisory",
        "slot_id": slot1.id,
    }

    response = client.post(
        f"/api/v1/eco-experiences/{exp.id}/weather-emergency",
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["eco_experience_id"] == exp.id
    assert data["hazard_type"] == "weather_alert"
    assert data["affected_slots_count"] == 1
    assert data["cancelled_bookings_count"] == 2
    assert data["refunds_initiated_count"] == 1


def test_staged_router_slot_weather_cancel_endpoint(db_session: Session, seed_data: dict):
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    provider = seed_data["provider_profile"]

    app = FastAPI()
    app.include_router(staged_eco_slots_router)

    def override_get_db():
        yield db_session

    def override_require_provider():
        return provider

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_approved_eco_provider] = override_require_provider

    client = TestClient(app)

    payload = {
        "hazard_type": "weather_alert",
        "reason": "Flash flood warning issued by local authorities",
    }

    response = client.post(
        f"/api/v1/provider/eco-experiences/{exp.id}/slots/{slot1.id}/weather-cancel",
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["slot_id"] == slot1.id
    assert data["is_active"] is False
    assert data["cancelled_bookings_count"] == 2
    assert data["refunds_initiated_count"] == 1


def test_booking_request_rejected_when_slot_falls_in_active_seasonal_restriction(
    db_session: Session, seed_data: dict
):
    """
    E06 & T08: Booking validation rejects any reservation attempt for a slot
    that falls within an active seasonal/environmental restriction.
    """
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    traveler = seed_data["traveler_user_1"]

    # Add an active seasonal restriction covering slot1's date
    slot_date = slot1.starts_at.date()
    restriction = EcoSeasonRestriction(
        eco_experience_id=exp.id,
        start_date=slot_date - timedelta(days=1),
        end_date=slot_date + timedelta(days=1),
        restriction_type="monsoon_season",
        reason="Heavy southwest monsoon alert: trails inaccessible.",
        is_active=True,
        created_by=seed_data["provider_user"].id,
    )
    db_session.add(restriction)
    db_session.commit()

    payload = EcoBookingCreate(
        eco_slot_id=slot1.id,
        participants=2,
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )

    assert exc_info.value.code == "ECO_SEASON_RESTRICTED"
    assert "monsoon_season" in exc_info.value.message
    assert "Heavy southwest monsoon alert" in exc_info.value.message


def test_t07_public_availability_excludes_slots_in_seasonal_restriction(
    db_session: Session, seed_data: dict
):
    """
    E06 & T07: Public availability calendar strictly excludes slots that fall
    within an active seasonal restriction, even if the slot itself is is_active=True.
    """
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    slot2 = seed_data["slot2"]

    # Slot1 has capacity available
    slot1.reserved_count = 0
    slot2.reserved_count = 0
    db_session.commit()

    # Before restriction, both slots appear in public availability
    public_slots_before = get_public_eco_slots(experience_id=exp.id, db=db_session)
    slot_ids_before = [s.id for s in public_slots_before]
    assert slot1.id in slot_ids_before
    assert slot2.id in slot_ids_before

    # Apply restriction covering ONLY slot1's date
    slot1_date = slot1.starts_at.date()
    restriction = EcoSeasonRestriction(
        eco_experience_id=exp.id,
        start_date=slot1_date,
        end_date=slot1_date,
        restriction_type="wildlife_breeding",
        reason="Annual elephant calving season: route strictly restricted.",
        is_active=True,
    )
    db_session.add(restriction)
    db_session.commit()

    # After restriction, slot1 is excluded; slot2 remains available
    public_slots_after = get_public_eco_slots(experience_id=exp.id, db=db_session)
    slot_ids_after = [s.id for s in public_slots_after]
    assert slot1.id not in slot_ids_after
    assert slot2.id in slot_ids_after


def test_seasonal_restriction_created_after_slots_exist(
    db_session: Session, seed_data: dict
):
    """
    E06 Edge Case: When seasonal restrictions are created after slots already exist,
    both availability discovery (T07) and booking creation (T08) immediately enforce the block.
    """
    exp = seed_data["experience"]
    slot2 = seed_data["slot2"]
    traveler = seed_data["traveler_user_2"]
    slot2.reserved_count = 0
    db_session.commit()

    # Declare park closure on slot2
    slot2_date = slot2.starts_at.date()
    restriction = EcoSeasonRestriction(
        eco_experience_id=exp.id,
        start_date=slot2_date - timedelta(days=2),
        end_date=slot2_date + timedelta(days=2),
        restriction_type="park_closure",
        reason="Department of Wildlife Conservation temporary sanctuary closure.",
        is_active=True,
    )
    db_session.add(restriction)
    db_session.commit()

    # 1. Availability hides it
    available = get_public_eco_slots(experience_id=exp.id, db=db_session)
    assert slot2.id not in [s.id for s in available]

    # 2. Direct booking attempt fails
    payload = EcoBookingCreate(
        eco_slot_id=slot2.id,
        participants=1,
        payment_method="pay_at_location",
    )
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_SEASON_RESTRICTED"


def test_weather_emergency_repeated_declaration_idempotent(
    db_session: Session, seed_data: dict
):
    """
    E06 Idempotency: Multiple sequential weather emergency declarations for the
    same period do not create duplicate refunds or duplicate active restrictions.
    """
    exp = seed_data["experience"]
    slot1 = seed_data["slot1"]
    provider = seed_data["provider_profile"]

    slot1_date = slot1.starts_at.date()

    plan1 = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Severe torrential rain and mudslide warning.",
        hazard_type="weather_alert",
        start_date=slot1_date,
        end_date=slot1_date,
    )
    res1 = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan1)
    db_session.commit()

    assert res1["cancelled_bookings_count"] == 2
    assert res1["refunds_initiated_count"] == 1

    # Second declaration for identical experience and date window
    plan2 = validate_weather_emergency_cancellation_read_only(
        db=db_session,
        provider_profile=provider,
        eco_experience_id=exp.id,
        reason="Severe torrential rain and mudslide warning.",
        hazard_type="weather_alert",
        start_date=slot1_date,
        end_date=slot1_date,
    )
    res2 = execute_weather_emergency_cancellation_atomic(db=db_session, plan=plan2)
    db_session.commit()

    # Zero additional bookings cancelled, zero additional refunds
    assert res2["cancelled_bookings_count"] == 0
    assert res2["refunds_initiated_count"] == 0

    # Total refunds for the transaction remains 1
    total_refunds = (
        db_session.query(PaymentRefund)
        .filter(PaymentRefund.payment_transaction_id == seed_data["tx1"].id)
        .count()
    )
    assert total_refunds == 1

    # Total active restrictions for the period remains 1
    total_restrictions = (
        db_session.query(EcoSeasonRestriction)
        .filter(
            EcoSeasonRestriction.eco_experience_id == exp.id,
            EcoSeasonRestriction.start_date == slot1_date,
            EcoSeasonRestriction.end_date == slot1_date,
            EcoSeasonRestriction.is_active.is_(True),
        )
        .count()
    )
    assert total_restrictions == 1
