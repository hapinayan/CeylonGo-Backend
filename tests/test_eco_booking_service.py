"""
Unit Tests for Module 04 Traveler Eco-Tourism Booking Service.

Target:
    app.services.eco_booking_service

Coverage:
1. Traveler authorization and active status validation.
2. Canonical booking reference generation (ECO-YYYYMMDD-XXXX).
3. Read-only booking request validation:
   - Rejects non-traveler roles and inactive users.
   - Enforces pay_at_location restriction (rejects online payments in Phase 4).
   - Validates slot existence, active state, future start, and eco association.
   - Validates experience existence, approval, and publication status.
   - Enforces group size bounds and available capacity.
   - Validates minimum-age requirements for participants.
   - Accurately computes server-side pricing with Decimal precision.
   - Generates initial booking lifecycle status (pending, unpaid).
   - Confirms ZERO database writes.
4. Read-only cancellation validation:
   - Validates ownership, eco association, and current booking status.
   - Rejects already cancelled or invalid status bookings.
   - Validates cancellation reason length.
   - Evaluates cancellation cutoff against free_cancellation_hours.
   - Confirms ZERO database writes.

STRICT ISOLATION:
- In-memory SQLite only. Zero SQL Server access.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.models.user import Base, User
from app.schemas.eco_booking import (
    EcoBookingCreate,
    EcoBookingProviderDecision,
    EcoParticipantCreate,
)
from app.services.eco_booking_service import (
    EcoBookingServiceError,
    _handle_applock_result,
    acquire_slot_lock,
    execute_provider_decision_atomic,
    execute_traveler_booking_cancel_atomic,
    execute_traveler_booking_create_atomic,
    format_eco_booking_reference,
    validate_booking_cancellation_read_only,
    validate_booking_request_read_only,
    validate_provider_booking_ownership,
    validate_provider_decision_read_only,
    validate_traveler_authorization,
)

# ==============================================================================
# In-Memory SQLite Fixtures
# ==============================================================================

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
def seeded_env(db_session: Session):
    # 1. Approved Eco Provider User & Profile
    user_eco = User(
        full_name="Eco Guide Ranil",
        email="ranil@eco.com",
        password_hash="hashed_pw",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(user_eco)
    db_session.flush()

    provider_eco = ProviderProfile(
        user_id=user_eco.id,
        business_name="Sinharaja Eco Walkers",
        provider_type="eco_tourism",
        district="Ratnapura",
        approval_status="approved",
        phone="+94770001122",
        identity_document_type="nic",
        created_at=datetime.now(UTC),
    )
    db_session.add(provider_eco)
    db_session.flush()

    # 2. Activity Provider (Non-Eco) for Base Activity FK
    user_act = User(
        full_name="Surfing Trainer",
        email="surf@test.com",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(user_act)
    db_session.flush()

    provider_act = ProviderProfile(
        user_id=user_act.id,
        business_name="Surf Lanka",
        provider_type="activity",
        district="Matara",
        approval_status="approved",
        phone="+94770003344",
        identity_document_type="nic",
        created_at=datetime.now(UTC),
    )
    db_session.add(provider_act)
    db_session.flush()

    # 3. Base Activity (satisfies ActivitySlot FK constraint in SQLite)
    base_activity = Activity(
        provider_profile_id=provider_act.id,
        title="Surfing Lessons",
        description="Ocean surfing",
        category="adventure",
        district="Matara",
        city="Weligama",
        meeting_point="Beach Hut",
        duration_minutes=90,
        minimum_age=10,
        pricing_type="per_person",
        price=Decimal("4000.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(base_activity)
    db_session.flush()

    # 4. Approved Eco Experience
    experience = EcoExperience(
        provider_profile_id=provider_eco.id,
        title="Sinharaja Rainforest Conservation Trek",
        eco_category="Forest & Nature Trails",
        description="A guided conservation walk through primary rainforest.",
        duration_hours=Decimal("5.0"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kudawa Conservation Centre",
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        currency="LKR",
        min_participants=2,
        max_group_size=12,
        min_age=8,
        environmental_guidelines="Strict zero trace policy",
        wildlife_safety_summary="Do not feed or approach wild animals",
        free_cancellation_hours=24,
        listing_status="approved",
        is_published=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(experience)
    db_session.flush()

    # 5. Future Active Slot (48 hours ahead, capacity 10, reserved 0)
    future_start = datetime.now(UTC) + timedelta(hours=48)
    slot = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=experience.id,
        starts_at=future_start,
        ends_at=future_start + timedelta(hours=4),
        capacity=10,
        reserved_count=0,
        is_active=True,
    )
    db_session.add(slot)
    db_session.flush()

    # 6. Traveler User
    traveler = User(
        full_name="Alice Traveler",
        email="traveler@ceylongo.test",
        password_hash="hashed_pw_traveler",
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(traveler)
    db_session.commit()

    return {
        "provider_user": user_eco,
        "provider_profile": provider_eco,
        "base_activity": base_activity,
        "experience": experience,
        "slot": slot,
        "traveler": traveler,
    }


# ==============================================================================
# 1. Traveler Authorization & Reference Format Tests
# ==============================================================================


def test_format_eco_booking_reference():
    dt = datetime(2026, 11, 19, 14, 30, 0, tzinfo=UTC)
    ref = format_eco_booking_reference(booking_id=42, created_at=dt)
    assert ref == "ECO-20261119-0042"


def test_validate_traveler_authorization_success(seeded_env):
    traveler = seeded_env["traveler"]
    # Should not raise
    validate_traveler_authorization(traveler)


def test_validate_traveler_authorization_rejects_non_traveler(seeded_env):
    provider_user = seeded_env["provider_user"]
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_traveler_authorization(provider_user)
    assert exc_info.value.code == "ECO_USER_NOT_TRAVELER"


def test_validate_traveler_authorization_rejects_inactive(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    traveler.is_active = False
    db_session.flush()

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_traveler_authorization(traveler)
    assert exc_info.value.code == "ECO_USER_INACTIVE"


# ==============================================================================
# 2. Read-Only Booking Request Validation Tests
# ==============================================================================


def test_validate_booking_request_success(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=4,
        payment_method="pay_at_location",
        special_requests="Need bird-watching binoculars",
        participants_roster=[
            EcoParticipantCreate(full_name="Alice Smith", age=30),
            EcoParticipantCreate(full_name="Bob Smith", age=32),
            EcoParticipantCreate(full_name="Charlie Smith", age=10),
            EcoParticipantCreate(full_name="Diana Smith", age=9),
        ],
    )

    plan = validate_booking_request_read_only(
        db=db_session,
        traveler=traveler,
        payload=payload,
    )

    assert plan.is_valid is True
    assert plan.eco_experience_id == seeded_env["experience"].id
    assert plan.slot_id == slot.id
    assert plan.traveler_id == traveler.id
    assert plan.participants == 4
    assert plan.pricing.total_price == Decimal("18000.00")
    assert plan.payment_method == "pay_at_location"
    assert plan.booking_status == "pending"
    assert plan.payment_status == "unpaid"
    assert plan.special_requests == "Need bird-watching binoculars"
    assert len(plan.participant_roster) == 4

    # Assert ZERO database writes occurred
    booking_count = db_session.query(ActivityBooking).count()
    assert booking_count == 0


def test_validate_booking_rejects_online_payment(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=2,
        payment_method="online",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_ONLINE_PAYMENT_UNSUPPORTED"


def test_validate_booking_slot_not_found(seeded_env, db_session):
    traveler = seeded_env["traveler"]

    payload = EcoBookingCreate(
        eco_slot_id=99999,
        participants=2,
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_SLOT_NOT_FOUND"


def test_validate_booking_slot_inactive(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    slot.is_active = False
    db_session.flush()

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=2,
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_SLOT_NOT_ACTIVE"


def test_validate_booking_slot_in_past(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    slot.starts_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.flush()

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=2,
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_SLOT_IN_PAST"


def test_validate_booking_experience_not_approved(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    exp = seeded_env["experience"]
    exp.listing_status = "draft"
    db_session.flush()

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=2,
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_EXPERIENCE_NOT_ACTIVE"


def test_validate_booking_group_size_below_min(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=1,  # Experience min is 2
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_BELOW_MIN_PARTICIPANTS"


def test_validate_booking_group_size_exceeds_available_capacity(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    slot.capacity = 5
    slot.reserved_count = 3  # Only 2 left
    db_session.flush()

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=4,  # Wants 4, but only 2 available
        payment_method="pay_at_location",
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_EXCEEDS_SLOT_CAPACITY"


def test_validate_booking_underage_participant(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]

    payload = EcoBookingCreate(
        eco_slot_id=slot.id,
        participants=2,
        payment_method="pay_at_location",
        participants_roster=[
            EcoParticipantCreate(full_name="Adult", age=30),
            EcoParticipantCreate(full_name="Child", age=6),  # Min age is 8
        ],
    )

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
        )
    assert exc_info.value.code == "ECO_AGE_BELOW_MINIMUM"


# ==============================================================================
# 3. Read-Only Booking Cancellation Validation Tests
# ==============================================================================


def test_validate_cancellation_success(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    exp = seeded_env["experience"]

    # Create a confirmed booking in DB for testing cancellation validation
    booking = ActivityBooking(
        id=777,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=3,
        total_price=Decimal("13500.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    plan = validate_booking_cancellation_read_only(
        db=db_session,
        traveler=traveler,
        booking_id=booking.id,
        reason="Family emergency, cannot attend trek.",
    )

    assert plan.is_valid is True
    assert plan.booking_id == 777
    assert plan.traveler_id == traveler.id
    assert plan.slot_id == slot.id
    assert plan.participants_to_release == 3
    assert plan.cancellation.is_free_cancellation is True
    assert plan.reason == "Family emergency, cannot attend trek."

    # Assert booking status in DB is untouched
    refetched = db_session.query(ActivityBooking).filter(ActivityBooking.id == 777).one()
    assert refetched.booking_status == "pending"


def test_validate_cancellation_rejects_non_owner(seeded_env, db_session):
    slot = seeded_env["slot"]
    exp = seeded_env["experience"]

    other_traveler = User(
        id=333,
        full_name="Other Traveler",
        email="other@ceylongo.test",
        password_hash="hashed_pw",
        role="traveler",
        is_active=True,
    )
    db_session.add(other_traveler)

    booking = ActivityBooking(
        id=778,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=other_traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    # Original traveler attempts to cancel other traveler's booking
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_cancellation_read_only(
            db=db_session,
            traveler=seeded_env["traveler"],
            booking_id=booking.id,
            reason="Attempting to cancel someone else's booking",
        )
    assert exc_info.value.code == "ECO_BOOKING_NOT_FOUND"


def test_validate_cancellation_rejects_already_cancelled(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    exp = seeded_env["experience"]

    booking = ActivityBooking(
        id=779,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="cancelled",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking.id,
            reason="Already cancelled booking cancellation attempt",
        )
    assert exc_info.value.code == "ECO_BOOKING_ALREADY_CANCELLED"


def test_validate_cancellation_rejects_completed(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    slot = seeded_env["slot"]
    exp = seeded_env["experience"]

    booking = ActivityBooking(
        id=780,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="completed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking.id,
            reason="Attempting to cancel a completed tour",
        )
    assert exc_info.value.code == "ECO_BOOKING_CANNOT_BE_CANCELLED"


def test_validate_cancellation_rejects_past_slot(seeded_env, db_session):
    traveler = seeded_env["traveler"]
    exp = seeded_env["experience"]
    base_activity = seeded_env["base_activity"]

    past_start = datetime.now(UTC) - timedelta(hours=3)
    past_slot = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=exp.id,
        starts_at=past_start,
        ends_at=past_start + timedelta(hours=3),
        capacity=10,
        reserved_count=2,
        is_active=True,
    )
    db_session.add(past_slot)
    db_session.flush()

    booking = ActivityBooking(
        id=781,
        activity_slot_id=past_slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC) - timedelta(days=1),
        updated_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking.id,
            reason="Slot already started 3 hours ago",
        )
    assert exc_info.value.code == "ECO_SLOT_ALREADY_STARTED"


# ==============================================================================
# Phase 4B: Provider Decision Validation Unit Tests
# ==============================================================================


def test_validate_provider_booking_ownership_success(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=801,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    verified_exp = validate_provider_booking_ownership(
        db=db_session,
        booking=booking,
        provider_profile=provider,
    )
    assert verified_exp.id == exp.id


def test_validate_provider_booking_ownership_unauthorized(seeded_env, db_session):
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    # Different provider
    other_user = User(
        id=77,
        full_name="Other Provider",
        email="other_provider@example.com",
        role="provider",
        is_active=True,
    )
    other_provider = ProviderProfile(
        id=88,
        user_id=77,
        provider_type="eco_tourism",
        business_name="Other Eco Tours",
        district="Kandy",
        approval_status="approved",
        phone="+94770009988",
        identity_document_type="nic",
        created_at=datetime.now(UTC),
    )
    db_session.add_all([other_user, other_provider])
    db_session.flush()

    booking = ActivityBooking(
        id=802,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_booking_ownership(
            db=db_session,
            booking=booking,
            provider_profile=other_provider,
        )
    assert exc_info.value.code == "ECO_PROVIDER_NOT_AUTHORIZED"


def test_validate_decision_accept_success(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=803,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=3,
        total_price=Decimal("13500.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="accept", note="Looking forward to hosting you!")
    plan = validate_provider_decision_read_only(
        db=db_session,
        provider_profile=provider,
        booking_id=booking.id,
        payload=decision,
    )

    assert plan.is_valid is True
    assert plan.action == "accept"
    assert plan.from_booking_status == "pending"
    assert plan.to_booking_status == "confirmed"
    assert plan.to_payment_status == "unpaid"
    assert plan.participants_to_release == 0
    # Verify read-only: booking in db remains unchanged
    reloaded = db_session.query(ActivityBooking).filter_by(id=booking.id).first()
    assert reloaded.booking_status == "pending"


def test_validate_decision_accept_rejects_non_pending(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=804,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="accept")
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking.id,
            payload=decision,
        )
    assert exc_info.value.code == "ECO_INVALID_STATUS_TRANSITION"


def test_validate_decision_accept_rejects_expired_slot(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    base_activity = seeded_env["base_activity"]
    traveler = seeded_env["traveler"]

    past_slot = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=exp.id,
        starts_at=datetime.now(UTC) - timedelta(hours=2),
        ends_at=datetime.now(UTC) + timedelta(hours=1),
        capacity=10,
        reserved_count=2,
        is_active=True,
    )
    db_session.add(past_slot)
    db_session.flush()

    booking = ActivityBooking(
        id=805,
        activity_slot_id=past_slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC) - timedelta(days=1),
        updated_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="accept")
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking.id,
            payload=decision,
        )
    assert exc_info.value.code == "ECO_SLOT_EXPIRED"


def test_validate_decision_reject_success(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=806,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=4,
        total_price=Decimal("18000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(
        action="reject",
        note="Trail unavailable due to weather warning.",
    )
    plan = validate_provider_decision_read_only(
        db=db_session,
        provider_profile=provider,
        booking_id=booking.id,
        payload=decision,
    )

    assert plan.is_valid is True
    assert plan.action == "reject"
    assert plan.from_booking_status == "pending"
    assert plan.to_booking_status == "rejected"
    assert plan.to_payment_status == "unpaid"
    # Capacity calculation: 4 participants to be released
    assert plan.participants_to_release == 4
    # Zero release performed in read-only mode
    assert slot.reserved_count == 0  # fixture initial value unchanged
    reloaded = db_session.query(ActivityBooking).filter_by(id=booking.id).first()
    assert reloaded.booking_status == "pending"


def test_validate_decision_reject_requires_valid_note(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=807,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    # Pydantic schema catches missing or too short note:
    with pytest.raises(ValueError):
        EcoBookingProviderDecision(action="reject", note="no")

    with pytest.raises(ValueError):
        EcoBookingProviderDecision(action="reject", note="")


def test_validate_decision_confirm_cash_success(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=808,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(
        action="confirm_cash",
        note="Received 9000 LKR in cash on-site.",
    )
    plan = validate_provider_decision_read_only(
        db=db_session,
        provider_profile=provider,
        booking_id=booking.id,
        payload=decision,
    )

    assert plan.is_valid is True
    assert plan.action == "confirm_cash"
    assert plan.from_booking_status == "confirmed"
    assert plan.to_booking_status == "confirmed"  # Separated from activity completion!
    assert plan.from_payment_status == "unpaid"
    assert plan.to_payment_status == "paid"
    assert plan.participants_to_release == 0


def test_validate_decision_confirm_cash_rejects_online_method(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=809,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="online",
        payment_status="unpaid",
        booking_status="confirmed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="confirm_cash")
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking.id,
            payload=decision,
        )
    assert exc_info.value.code == "ECO_INVALID_PAYMENT_METHOD"


def test_validate_decision_confirm_cash_rejects_already_paid(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=810,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="confirmed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="confirm_cash")
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking.id,
            payload=decision,
        )
    assert exc_info.value.code == "ECO_ALREADY_PAID"


def test_validate_decision_confirm_cash_rejects_pending_or_cancelled(seeded_env, db_session):
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=811,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(action="confirm_cash")
    with pytest.raises(EcoBookingServiceError) as exc_info:
        validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking.id,
            payload=decision,
        )
    assert exc_info.value.code == "ECO_INVALID_STATUS_TRANSITION"


def test_validate_decision_reject_payment_status_strictly_valid(seeded_env, db_session):
    """
    Regression test for Issue 1:
    Verify that rejecting a booking sets booking_status='rejected' and preserves
    payment_status as 'unpaid' (strictly matching CK_activity_bookings_payment).
    Verifies that payment_status is NEVER set to 'cancelled', which is only a booking_status.
    """
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=820,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=3,
        total_price=Decimal("13500.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(
        action="reject",
        note="Weather warning issued for trail.",
    )
    plan = validate_provider_decision_read_only(
        db=db_session,
        provider_profile=provider,
        booking_id=booking.id,
        payload=decision,
    )

    # Allowed in CK_activity_bookings_payment: ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')
    valid_payment_statuses = {"unpaid", "pending", "paid", "refund_pending", "refunded", "failed"}

    assert plan.action == "reject"
    assert plan.to_booking_status == "rejected"
    assert plan.to_payment_status == "unpaid"
    assert plan.to_payment_status != "cancelled", "payment_status must NOT be 'cancelled'"
    assert plan.to_payment_status in valid_payment_statuses


def test_validate_decision_reject_paid_booking_transitions_to_refund_pending(seeded_env, db_session):
    """
    Regression test for Issue 1:
    If a pending booking was paid, rejecting it must transition payment_status to 'refund_pending',
    NOT 'cancelled', complying with the SQL Server payment lifecycle constraint.
    """
    provider = seeded_env["provider_profile"]
    exp = seeded_env["experience"]
    slot = seeded_env["slot"]
    traveler = seeded_env["traveler"]

    booking = ActivityBooking(
        id=821,
        activity_slot_id=slot.id,
        eco_experience_id=exp.id,
        user_id=traveler.id,
        participants=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="pending",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(booking)
    db_session.commit()

    decision = EcoBookingProviderDecision(
        action="reject",
        note="Provider injured, cannot guide tour.",
    )
    plan = validate_provider_decision_read_only(
        db=db_session,
        provider_profile=provider,
        booking_id=booking.id,
        payload=decision,
    )

    assert plan.action == "reject"
    assert plan.to_booking_status == "rejected"
    assert plan.to_payment_status == "refund_pending"
    assert plan.to_payment_status != "cancelled"
    assert plan.to_payment_status in {"unpaid", "pending", "paid", "refund_pending", "refunded", "failed"}


# ==============================================================================
# 5. Phase 4C: Atomic Transaction & Lock Management Tests
# ==============================================================================


class TestPhase4CAppLockHandling:
    def test_applock_success_codes(self):
        # 0 = synchronous grant, 1 = wait grant
        assert _handle_applock_result(0, 101) == 0
        assert _handle_applock_result(1, 101) == 1

    def test_applock_timeout_raises_eco_lock_timeout(self):
        with pytest.raises(EcoBookingServiceError) as exc_info:
            _handle_applock_result(-1, 101)
        assert exc_info.value.code == "ECO_LOCK_TIMEOUT"

    def test_applock_canceled_raises_eco_lock_canceled(self):
        with pytest.raises(EcoBookingServiceError) as exc_info:
            _handle_applock_result(-2, 101)
        assert exc_info.value.code == "ECO_LOCK_CANCELED"

    def test_applock_deadlock_raises_eco_lock_deadlock(self):
        with pytest.raises(EcoBookingServiceError) as exc_info:
            _handle_applock_result(-3, 101)
        assert exc_info.value.code == "ECO_LOCK_DEADLOCK"

    def test_applock_failure_codes_raise_eco_lock_failed(self):
        for code in (-999, -5, None):
            with pytest.raises(EcoBookingServiceError) as exc_info:
                _handle_applock_result(code, 101)
            assert exc_info.value.code == "ECO_LOCK_FAILED"


class TestPhase4CAtomicExecution:
    def test_execute_traveler_booking_create_atomic_success(self, seeded_env, db_session):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        initial_reserved = slot.reserved_count

        payload = EcoBookingCreate(
            eco_slot_id=slot.id,
            participants=2,
            payment_method="pay_at_location",
            participants_roster=[
                EcoParticipantCreate(
                    full_name="Alice Smith",
                    age=28,
                    emergency_contact_name="Bob",
                    emergency_contact_phone="0771234567",
                ),
                EcoParticipantCreate(
                    full_name="Bob Smith",
                    age=30,
                    emergency_contact_name="Alice",
                    emergency_contact_phone="0771234568",
                ),
            ],
        )
        plan = validate_booking_request_read_only(
            db=db_session,
            traveler=traveler,
            payload=payload,
            now_utc=datetime.now(UTC),
        )

        res = execute_traveler_booking_create_atomic(db_session, plan)
        assert res["status"] == "pending"
        assert res["payment_status"] == "unpaid"
        assert res["participants"] == 2

        # Verify slot capacity reserved atomically
        db_session.refresh(slot)
        assert slot.reserved_count == initial_reserved + 2

        # Verify ActivityBooking row created
        booking = db_session.query(ActivityBooking).filter_by(id=res["booking_id"]).one()
        assert booking.booking_status == "pending"
        assert booking.payment_status == "unpaid"
        assert booking.participants == 2

        # Verify BookingReference row created with module_type='activity' per SQL Server CK constraint
        ref = db_session.query(BookingReference).filter_by(target_booking_id=res["booking_id"]).one()
        assert ref.module_type == "activity"
        assert ref.booking_reference == res["booking_reference"]
        assert ref.booking_status == "pending"
        assert ref.payment_status == "unpaid"

    def test_execute_booking_create_atomic_rolls_back_on_capacity_exceeded(self, seeded_env, db_session):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        slot.capacity = 5
        slot.reserved_count = 4
        db_session.commit()

        # Manually construct plan to simulate race condition where capacity filled between validation & execution
        from app.services.eco_booking_pricing import EcoPricingResult
        pricing = EcoPricingResult(
            pricing_type="per_person",
            base_price=Decimal("4500.00"),
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
        )
        from app.services.eco_booking_service import EcoBookingValidationPlan
        plan = EcoBookingValidationPlan(
            eco_experience_id=seeded_env["experience"].id,
            slot_id=slot.id,
            traveler_id=traveler.id,
            participants=2,
            pricing=pricing,
            payment_method="pay_at_location",
            booking_status="pending",
            payment_status="unpaid",
            special_requests=None,
            participant_roster=None,
            experience_title="Rainforest Trek",
            slot_starts_at=slot.starts_at,
        )

        initial_booking_count = db_session.query(ActivityBooking).count()
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_traveler_booking_create_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_CAPACITY_EXCEEDED"

        # Verify atomic rollback: slot reserved_count unchanged and zero bookings inserted
        db_session.refresh(slot)
        assert slot.reserved_count == 4
        assert db_session.query(ActivityBooking).count() == initial_booking_count

    def test_execute_traveler_booking_cancel_atomic_success_and_prevents_double_cancel(self, seeded_env, db_session):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        slot.reserved_count = 3
        booking = ActivityBooking(
            id=910,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=3,
            total_price=Decimal("13500.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        cancel_plan = validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking.id,
            reason="Unable to attend due to illness.",
        )

        res = execute_traveler_booking_cancel_atomic(db_session, cancel_plan)
        assert res["status"] == "cancelled"
        assert res["released_capacity"] == 3

        # Verify capacity decrement
        db_session.refresh(slot)
        assert slot.reserved_count == 0

        # Attempt DOUBLE CANCELLATION with same plan
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_traveler_booking_cancel_atomic(db_session, cancel_plan)
        assert exc_info.value.code == "ECO_BOOKING_ALREADY_CANCELLED"

        # Verify capacity was NOT decremented into negative numbers
        db_session.refresh(slot)
        assert slot.reserved_count == 0

    def test_execute_provider_decision_atomic_accept(self, seeded_env, db_session):
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=911,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        decision = EcoBookingProviderDecision(action="accept")
        plan = validate_provider_decision_read_only(db_session, provider, booking.id, decision)

        res = execute_provider_decision_atomic(db_session, plan)
        assert res["booking_status"] == "confirmed"

        db_session.refresh(booking)
        assert booking.booking_status == "confirmed"

        # Attempt DOUBLE ACCEPT -> conflict
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_provider_decision_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_DECISION_CONFLICT"

    def test_execute_provider_decision_atomic_reject_and_capacity_release(self, seeded_env, db_session):
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        slot.reserved_count = 4
        booking = ActivityBooking(
            id=912,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=4,
            total_price=Decimal("18000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        decision = EcoBookingProviderDecision(action="reject", note="Heavy rain warning.")
        plan = validate_provider_decision_read_only(db_session, provider, booking.id, decision)

        res = execute_provider_decision_atomic(db_session, plan)
        assert res["booking_status"] == "rejected"
        assert res["released_capacity"] == 4

        # Verify capacity decremented
        db_session.refresh(slot)
        assert slot.reserved_count == 0

        # Attempt DOUBLE REJECT -> conflict
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_provider_decision_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_DECISION_CONFLICT"

    def test_execute_provider_decision_atomic_confirm_cash(self, seeded_env, db_session):
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=913,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        decision = EcoBookingProviderDecision(action="confirm_cash")
        plan = validate_provider_decision_read_only(db_session, provider, booking.id, decision)

        res = execute_provider_decision_atomic(db_session, plan)
        assert res["payment_status"] == "paid"
        assert res["booking_status"] == "confirmed"  # Confirms cash without marking completed!

        db_session.refresh(booking)
        assert booking.payment_status == "paid"
        assert booking.booking_status == "confirmed"

        # Attempt confirming already paid booking -> conflict
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_provider_decision_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_PAYMENT_CONFLICT"

    def test_cancellation_capacity_underflow_rolls_back_booking_status(self, seeded_env, db_session):
        """
        Regression test for Issue 2:
        Verifies that if slot reserved_count is insufficient for release (rowcount != 1),
        ECO_CAPACITY_UNDERFLOW is raised, the entire operation is rolled back,
        and booking_status is restored (NOT left as 'cancelled').
        """
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        # Slot only has 1 reserved, but booking has 3 participants (underflow condition)
        slot.reserved_count = 1
        booking = ActivityBooking(
            id=914,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=3,
            total_price=Decimal("13500.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        plan = validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking.id,
            reason="Change of plans",
        )

        # Simulate concurrent underflow: slot reserved_count reduced to 1 (< participants 3)
        slot.reserved_count = 1
        db_session.commit()

        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_traveler_booking_cancel_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_CAPACITY_UNDERFLOW"

        # Verify atomic rollback: booking remains 'confirmed', slot reserved_count remains 1
        db_session.refresh(booking)
        db_session.refresh(slot)
        assert booking.booking_status == "confirmed"
        assert slot.reserved_count == 1

    def test_provider_rejection_capacity_underflow_rolls_back_booking_status(self, seeded_env, db_session):
        """
        Regression test for Issue 4:
        Verifies that if slot capacity release underflows during provider rejection,
        ECO_CAPACITY_UNDERFLOW is raised, the transaction rolls back,
        and booking_status is restored to 'pending' (NOT left as 'rejected').
        """
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        # Slot has 0 reserved, but booking requests release of 2
        slot.reserved_count = 0
        booking = ActivityBooking(
            id=915,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.commit()

        from app.services.eco_booking_service import EcoProviderDecisionValidationPlan
        plan = EcoProviderDecisionValidationPlan(
            booking_id=booking.id,
            provider_profile_id=provider.id,
            eco_experience_id=exp.id,
            action="reject",
            from_booking_status="pending",
            to_booking_status="rejected",
            from_payment_status="unpaid",
            to_payment_status="unpaid",
            slot_id=slot.id,
            participants_to_release=2,
            note="Unforeseen closure",
            is_valid=True,
        )

        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_provider_decision_atomic(db_session, plan)
        assert exc_info.value.code == "ECO_CAPACITY_UNDERFLOW"

        # Verify atomic rollback: booking remains 'pending', slot reserved_count remains 0
        db_session.refresh(booking)
        db_session.refresh(slot)
        assert booking.booking_status == "pending"
        assert slot.reserved_count == 0

    def test_concurrent_cancellation_and_provider_rejection_race_prevention(self, seeded_env, db_session):
        """
        Regression test for Issue 4:
        Verifies mutual exclusion between traveler cancellation and provider rejection.
        - If cancellation succeeds first, provider rejection fails with ECO_DECISION_CONFLICT.
        - If provider rejection succeeds first, traveler cancellation fails with ECO_BOOKING_ALREADY_CANCELLED.
        - In both cases, capacity is released exactly once, preventing double release.
        """
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        # --- Scenario A: Cancel then Reject ---
        slot.reserved_count = 2
        booking_a = ActivityBooking(
            id=916,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking_a)
        db_session.commit()

        cancel_plan_a = validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking_a.id,
            reason="Traveler plans changed",
        )
        decision_plan_a = validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking_a.id,
            payload=EcoBookingProviderDecision(action="reject", note="Weather issues"),
        )

        # 1. Traveler cancels first
        execute_traveler_booking_cancel_atomic(db_session, cancel_plan_a)
        db_session.refresh(slot)
        assert slot.reserved_count == 0

        # 2. Provider tries to reject already cancelled booking -> CONFLICT
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_provider_decision_atomic(db_session, decision_plan_a)
        assert exc_info.value.code == "ECO_DECISION_CONFLICT"

        # Capacity remains 0 (no double release)
        db_session.refresh(slot)
        assert slot.reserved_count == 0

        # --- Scenario B: Reject then Cancel ---
        slot.reserved_count = 2
        booking_b = ActivityBooking(
            id=917,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking_b)
        db_session.commit()

        cancel_plan_b = validate_booking_cancellation_read_only(
            db=db_session,
            traveler=traveler,
            booking_id=booking_b.id,
            reason="Traveler plans changed",
        )
        decision_plan_b = validate_provider_decision_read_only(
            db=db_session,
            provider_profile=provider,
            booking_id=booking_b.id,
            payload=EcoBookingProviderDecision(action="reject", note="Provider unavailable"),
        )

        # 1. Provider rejects first
        execute_provider_decision_atomic(db_session, decision_plan_b)
        db_session.refresh(slot)
        assert slot.reserved_count == 0

        # 2. Traveler tries to cancel already rejected booking -> ALREADY CANCELLED
        with pytest.raises(EcoBookingServiceError) as exc_info:
            execute_traveler_booking_cancel_atomic(db_session, cancel_plan_b)
        assert exc_info.value.code == "ECO_BOOKING_ALREADY_CANCELLED"

        # Capacity remains 0 (no double release)
        db_session.refresh(slot)
        assert slot.reserved_count == 0

    def test_confirm_cash_payment_status_strictly_paid_not_completed_against_sql_server_constraints(
        self, seeded_env, db_session
    ):
        """
        Regression test for Issue 1:
        Validates against SQL Server check constraints:
        - CK_activity_bookings_payment: ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')
        - CK_activity_bookings_status: ('pending', 'confirmed', 'rejected', 'cancelled', 'completed')

        Verifies that confirm_cash:
        - Sets payment_status = 'paid' (NOT 'completed')
        - Keeps booking_status = 'confirmed' (NOT 'completed' or 'paid')
        - Syncs booking_references.payment_status = 'paid'
        """
        provider = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=918,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(booking)
        db_session.flush()

        ref = BookingReference(
            id=950,
            booking_reference="BK-ACT-918",
            module_type="activity",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="confirmed",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add(ref)
        db_session.commit()

        # SQL Server CHECK constraints definitions
        ck_allowed_payment_statuses = {"unpaid", "pending", "paid", "refund_pending", "refunded", "failed"}
        ck_allowed_booking_statuses = {"pending", "confirmed", "rejected", "cancelled", "completed"}

        decision = EcoBookingProviderDecision(action="confirm_cash")
        plan = validate_provider_decision_read_only(db_session, provider, booking.id, decision)

        # Plan validation assertions
        assert plan.to_payment_status == "paid"
        assert plan.to_payment_status != "completed", "payment_status cannot be 'completed' per CK_activity_bookings_payment"
        assert plan.to_payment_status in ck_allowed_payment_statuses
        assert plan.to_booking_status == "confirmed"
        assert plan.to_booking_status != "completed", "confirm_cash must not conflate payment with activity completion"
        assert plan.to_booking_status in ck_allowed_booking_statuses

        # Atomic execution assertions
        res = execute_provider_decision_atomic(db_session, plan)
        assert res["payment_status"] == "paid"
        assert res["payment_status"] != "completed"
        assert res["booking_status"] == "confirmed"
        assert res["booking_status"] != "completed"

        db_session.refresh(booking)
        db_session.refresh(ref)
        assert booking.payment_status == "paid"
        assert booking.payment_status in ck_allowed_payment_statuses
        assert booking.booking_status == "confirmed"
        assert booking.booking_status in ck_allowed_booking_statuses

        assert ref.payment_status == "paid"
        assert ref.booking_status == "confirmed"





