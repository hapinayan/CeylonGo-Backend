"""
Unit & Integration Tests for Module 04 Eco-Tourism Slot Scheduling Validation Service.

Target:
    app.services.eco_slot_validation

Features Covered:
1. Slot timing validity, minimum duration, future start, and max horizon checks.
2. Capacity boundaries against listing min/max and environmental ceiling (50).
3. Overlapping slot collision detection, including adjacent non-overlapping boundaries.
4. Active and inactive seasonal restriction evaluations.
5. Provider ownership, provider_type, and approval prerequisites.
6. Experience readiness (approved status, is_published flag).
7. Full read-only database-backed orchestrator (validate_eco_slot_creation_read_only).
8. Strict verification of zero database writes.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.schemas.eco_slot import EcoSlotCreate
from app.services.eco_slot_validation import (
    EcoSlotValidationError,
    EcoSlotValidationResult,
    check_seasonal_restrictions_in_memory,
    check_slot_collision_in_memory,
    validate_eco_experience_readiness_and_ownership,
    validate_eco_slot_creation_read_only,
    validate_provider_scheduling_prerequisites,
    validate_slot_capacity_against_listing,
    validate_slot_timing_bounds,
)


# ==============================================================================
# In-Memory SQLite Fixtures for Read-Only Orchestrator Testing
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


# Simple dummy class for mock slot and restriction objects in pure unit tests
class MockSlot:
    def __init__(self, starts_at: datetime, ends_at: datetime, is_active: bool = True, id: int = 1):
        self.starts_at = starts_at
        self.ends_at = ends_at
        self.is_active = is_active
        self.id = id


class MockRestriction:
    def __init__(self, start_date: date, end_date: date, is_active: bool = True, restriction_type="monsoon", reason="Rain"):
        self.start_date = start_date
        self.end_date = end_date
        self.is_active = is_active
        self.restriction_type = restriction_type
        self.reason = reason
        self.id = 1


class MockProfile:
    def __init__(self, id=1, provider_type="eco_tourism", approval_status="approved"):
        self.id = id
        self.provider_type = provider_type
        self.approval_status = approval_status


class MockExperience:
    def __init__(self, id=10, provider_profile_id=1, listing_status="approved", is_published=True, min_participants=2, max_group_size=12, title="Rainforest Trek"):
        self.id = id
        self.provider_profile_id = provider_profile_id
        self.listing_status = listing_status
        self.is_published = is_published
        self.min_participants = min_participants
        self.max_group_size = max_group_size
        self.title = title


# ==============================================================================
# 1. Slot Timing Bounds Tests
# ==============================================================================


class TestSlotTimingBounds:
    def test_valid_future_timing(self):
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now + timedelta(days=2)
        end = start + timedelta(hours=3)

        start_utc, end_utc = validate_slot_timing_bounds(start, end, now_utc=now)
        assert start_utc == start
        assert end_utc == end

    def test_past_start_time_rejected(self):
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=2)

        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_timing_bounds(start, end, now_utc=now)
        assert exc.value.code == "ECO_INVALID_SLOT_TIMING"

    def test_end_before_or_equal_start_rejected(self):
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now + timedelta(days=1)

        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_timing_bounds(start, start, now_utc=now)
        assert exc.value.code == "ECO_INVALID_SLOT_TIMING"

    def test_minimum_duration_boundary(self):
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now + timedelta(days=1)

        # 14 minutes -> too short (min is 15 mins)
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_timing_bounds(start, start + timedelta(minutes=14), now_utc=now, min_duration_minutes=15)
        assert exc.value.code == "ECO_SLOT_DURATION_TOO_SHORT"

        # 15 minutes -> passes
        s, e = validate_slot_timing_bounds(start, start + timedelta(minutes=15), now_utc=now, min_duration_minutes=15)
        assert (e - s).total_seconds() == 900

    def test_exceeds_scheduling_horizon(self):
        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now + timedelta(days=366)  # Horizon is 365 days
        end = start + timedelta(hours=2)

        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_timing_bounds(start, end, now_utc=now, max_horizon_days=365)
        assert exc.value.code == "ECO_SLOT_EXCEEDS_SCHEDULING_HORIZON"


# ==============================================================================
# 2. Slot Capacity Against Listing Tests
# ==============================================================================


class TestSlotCapacityAgainstListing:
    def test_valid_capacity(self):
        validate_slot_capacity_against_listing(capacity=6, listing_max_group_size=12, listing_min_participants=2)
        validate_slot_capacity_against_listing(capacity=2, listing_max_group_size=12, listing_min_participants=2)
        validate_slot_capacity_against_listing(capacity=12, listing_max_group_size=12, listing_min_participants=2)

    def test_capacity_exceeds_listing_max(self):
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_capacity_against_listing(capacity=15, listing_max_group_size=12, listing_min_participants=2)
        assert exc.value.code == "ECO_SLOT_CAPACITY_EXCEEDS_LISTING"
        assert exc.value.details["capacity"] == 15
        assert exc.value.details["listing_max_group_size"] == 12

    def test_capacity_below_listing_min(self):
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_capacity_against_listing(capacity=1, listing_max_group_size=12, listing_min_participants=3)
        assert exc.value.code == "ECO_SLOT_CAPACITY_BELOW_MINIMUM"
        assert exc.value.details["capacity"] == 1

    def test_capacity_exceeds_environmental_ceiling(self):
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_capacity_against_listing(capacity=55, listing_max_group_size=60, listing_min_participants=1)
        assert exc.value.code == "ECO_EXCEEDS_ENVIRONMENTAL_CEILING"

    @pytest.mark.parametrize("bad_cap", [0, -1, None, "10", 8.5, True])
    def test_invalid_capacity_types(self, bad_cap):
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_slot_capacity_against_listing(capacity=bad_cap, listing_max_group_size=12, listing_min_participants=1)  # type: ignore
        assert exc.value.code == "ECO_INVALID_CAPACITY"


# ==============================================================================
# 3. Slot Collision & Overlap Boundary Tests
# ==============================================================================


class TestSlotCollisionDetection:
    def test_no_existing_slots(self):
        start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        end = start + timedelta(hours=2)
        check_slot_collision_in_memory(start, end, existing_slots=[])

    def test_adjacent_slots_allowed(self):
        slot1_start = datetime(2026, 12, 10, 8, 0, tzinfo=UTC)
        slot1_end = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        existing = [MockSlot(slot1_start, slot1_end, is_active=True)]

        # New slot starts exactly when existing ends (10:00) -> Adjacent, Allowed!
        new_start = slot1_end
        new_end = new_start + timedelta(hours=2)
        check_slot_collision_in_memory(new_start, new_end, existing)

        # New slot ends exactly when existing starts (08:00) -> Adjacent, Allowed!
        earlier_start = datetime(2026, 12, 10, 6, 0, tzinfo=UTC)
        earlier_end = slot1_start
        check_slot_collision_in_memory(earlier_start, earlier_end, existing)

    def test_overlapping_scenarios(self):
        existing_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        existing_end = datetime(2026, 12, 10, 14, 0, tzinfo=UTC)
        existing = [MockSlot(existing_start, existing_end, is_active=True, id=99)]

        # Scenario 1: Inside existing slot (11:00 - 13:00)
        with pytest.raises(EcoSlotValidationError) as exc:
            check_slot_collision_in_memory(
                datetime(2026, 12, 10, 11, 0, tzinfo=UTC),
                datetime(2026, 12, 10, 13, 0, tzinfo=UTC),
                existing,
            )
        assert exc.value.code == "ECO_SLOT_OVERLAPPING"
        assert exc.value.details["overlapping_slot_id"] == 99

        # Scenario 2: Overlapping start (09:00 - 11:00)
        with pytest.raises(EcoSlotValidationError) as exc:
            check_slot_collision_in_memory(
                datetime(2026, 12, 10, 9, 0, tzinfo=UTC),
                datetime(2026, 12, 10, 11, 0, tzinfo=UTC),
                existing,
            )
        assert exc.value.code == "ECO_SLOT_OVERLAPPING"

        # Scenario 3: Overlapping end (13:00 - 15:00)
        with pytest.raises(EcoSlotValidationError) as exc:
            check_slot_collision_in_memory(
                datetime(2026, 12, 10, 13, 0, tzinfo=UTC),
                datetime(2026, 12, 10, 15, 0, tzinfo=UTC),
                existing,
            )
        assert exc.value.code == "ECO_SLOT_OVERLAPPING"

        # Scenario 4: Fully encompassing existing (08:00 - 16:00)
        with pytest.raises(EcoSlotValidationError) as exc:
            check_slot_collision_in_memory(
                datetime(2026, 12, 10, 8, 0, tzinfo=UTC),
                datetime(2026, 12, 10, 16, 0, tzinfo=UTC),
                existing,
            )
        assert exc.value.code == "ECO_SLOT_OVERLAPPING"

    def test_inactive_slot_overlap_ignored(self):
        # Existing slot is cancelled or deactivated (is_active=False)
        existing = [
            MockSlot(
                datetime(2026, 12, 10, 10, 0, tzinfo=UTC),
                datetime(2026, 12, 10, 14, 0, tzinfo=UTC),
                is_active=False,
            )
        ]
        # Overlapping window is allowed because the existing slot is inactive
        check_slot_collision_in_memory(
            datetime(2026, 12, 10, 11, 0, tzinfo=UTC),
            datetime(2026, 12, 10, 13, 0, tzinfo=UTC),
            existing,
        )


# ==============================================================================
# 4. Seasonal Restrictions Tests
# ==============================================================================


class TestSeasonalRestrictions:
    def test_no_restrictions(self):
        start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        end = start + timedelta(hours=3)
        check_seasonal_restrictions_in_memory(start, end, restrictions=[])

    def test_slot_outside_restriction(self):
        slot_start = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        slot_end = datetime(2026, 12, 1, 14, 0, tzinfo=UTC)
        # Restriction is in June
        restrictions = [MockRestriction(start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))]
        check_seasonal_restrictions_in_memory(slot_start, slot_end, restrictions)

    def test_slot_overlapping_active_restriction(self):
        slot_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        slot_end = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
        restrictions = [
            MockRestriction(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
                is_active=True,
                restriction_type="monsoon_season",
                reason="Heavy monsoon rains and trail closure",
            )
        ]
        with pytest.raises(EcoSlotValidationError) as exc:
            check_seasonal_restrictions_in_memory(slot_start, slot_end, restrictions)
        assert exc.value.code == "ECO_SEASON_RESTRICTED"
        assert exc.value.details["restriction_type"] == "monsoon_season"

    def test_inactive_restriction_ignored(self):
        slot_start = datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
        slot_end = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
        restrictions = [
            MockRestriction(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 30),
                is_active=False,  # Inactive restriction
            )
        ]
        check_seasonal_restrictions_in_memory(slot_start, slot_end, restrictions)


# ==============================================================================
# 5. Provider & Experience Prerequisites Tests
# ==============================================================================


class TestProviderAndExperiencePrerequisites:
    def test_valid_provider(self):
        p = MockProfile(provider_type="eco_tourism", approval_status="approved")
        validate_provider_scheduling_prerequisites(p)

    def test_wrong_provider_type_rejected(self):
        p = MockProfile(provider_type="activity", approval_status="approved")
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_provider_scheduling_prerequisites(p)
        assert exc.value.code == "ECO_PROVIDER_NOT_AUTHORIZED"

    def test_unapproved_provider_rejected(self):
        p = MockProfile(provider_type="eco_tourism", approval_status="pending")
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_provider_scheduling_prerequisites(p)
        assert exc.value.code == "ECO_PROVIDER_NOT_APPROVED"

    def test_valid_experience_ownership_and_readiness(self):
        exp = MockExperience(provider_profile_id=5, listing_status="approved", is_published=True)
        validate_eco_experience_readiness_and_ownership(exp, provider_profile_id=5)

    def test_not_found_experience(self):
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_experience_readiness_and_ownership(None, provider_profile_id=5)
        assert exc.value.code == "ECO_EXPERIENCE_NOT_FOUND"

    def test_unowned_experience(self):
        exp = MockExperience(provider_profile_id=5)
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_experience_readiness_and_ownership(exp, provider_profile_id=99)  # Mismatched ID
        assert exc.value.code == "ECO_EXPERIENCE_NOT_OWNED"

    def test_unapproved_experience(self):
        exp = MockExperience(provider_profile_id=5, listing_status="draft")
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_experience_readiness_and_ownership(exp, provider_profile_id=5)
        assert exc.value.code == "ECO_EXPERIENCE_NOT_APPROVED"

    def test_unpublished_experience(self):
        exp = MockExperience(provider_profile_id=5, listing_status="approved", is_published=False)
        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_experience_readiness_and_ownership(exp, provider_profile_id=5)
        assert exc.value.code == "ECO_EXPERIENCE_NOT_PUBLISHED"


# ==============================================================================
# 6. Read-Only Database-Backed Orchestrator Tests
# ==============================================================================


class TestDatabaseBackedOrchestrator:
    @pytest.fixture
    def seeded_environment(self, db_session: Session):
        # Create Provider User & Profile
        user = User(
            full_name="Eco Provider",
            email="eco@test.com",
            password_hash="hashed_pw",
            role="provider",
            is_active=True,
            created_at=datetime.now(UTC),
        )
        db_session.add(user)
        db_session.flush()

        provider = ProviderProfile(
            user_id=user.id,
            business_name="Knuckles Eco Treks",
            provider_type="eco_tourism",
            district="Kandy",
            approval_status="approved",
            phone="+94771234567",
            identity_document_type="nic",
            created_at=datetime.now(UTC),
        )
        db_session.add(provider)
        db_session.flush()

        from app.models.activity import Activity
        parent_act = Activity(
            provider_profile_id=provider.id,
            title="Base Activity",
            description="Base Activity for Foreign Key reference in test",
            category="adventure",
            district="Kandy",
            city="Teldeniya",
            meeting_point="Base Camp",
            duration_minutes=120,
            minimum_age=0,
            pricing_type="per_person",
            price=Decimal("1000.00"),
            currency="LKR",
            approval_status="approved",
            is_active=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(parent_act)
        db_session.flush()

        # Create Approved EcoExperience
        exp = EcoExperience(
            provider_profile_id=provider.id,
            title="Knuckles Mountain Forest Trail",
            eco_category="Forest & Nature Trails",
            description="Guided exploration of cloud forest flora.",
            duration_hours=Decimal("4.0"),
            district="Kandy",
            city="Teldeniya",
            meeting_point="Knuckles Base Camp",
            pricing_type="per_person",
            base_price=Decimal("3500.00"),
            currency="LKR",
            max_group_size=12,
            min_participants=2,
            environmental_guidelines="Strict zero-waste policy",
            wildlife_safety_summary="Do not feed endemic birds",
            listing_status="approved",
            is_published=True,
            eco_badge_status="granted",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(exp)
        db_session.flush()

        return {"provider": provider, "experience": exp, "activity": parent_act}

    def test_successful_validation_and_zero_writes(self, db_session: Session, seeded_environment):
        provider = seeded_environment["provider"]
        exp = seeded_environment["experience"]

        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        start = now + timedelta(days=5)
        end = start + timedelta(hours=4)

        payload = EcoSlotCreate(starts_at=start, ends_at=end, capacity=8)

        # Count records before
        slots_before = db_session.query(ActivitySlot).count()

        result = validate_eco_slot_creation_read_only(
            db=db_session,
            eco_experience_id=exp.id,
            provider_profile=provider,
            payload=payload,
            now_utc=now,
        )

        assert isinstance(result, EcoSlotValidationResult)
        assert result.is_valid is True
        assert result.eco_experience_id == exp.id
        assert result.capacity == 8
        assert result.duration_hours == 4.0

        # ZERO WRITES CONFIRMATION: Verify record count is strictly unchanged
        slots_after = db_session.query(ActivitySlot).count()
        assert slots_before == slots_after == 0

    def test_db_overlapping_slot_detection(self, db_session: Session, seeded_environment):
        provider = seeded_environment["provider"]
        exp = seeded_environment["experience"]
        act = seeded_environment["activity"]

        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
        existing_start = now + timedelta(days=2)
        existing_end = existing_start + timedelta(hours=3)

        # Seed an existing active slot in DB
        existing_slot = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=existing_start,
            ends_at=existing_end,
            capacity=10,
            reserved_count=2,
            is_active=True,
            created_at=now,
        )
        db_session.add(existing_slot)
        db_session.flush()

        # Attempt to validate a new overlapping slot (starts 1 hour before existing ends)
        new_start = existing_start + timedelta(hours=1)
        new_end = new_start + timedelta(hours=3)

        payload = EcoSlotCreate(starts_at=new_start, ends_at=new_end, capacity=6)

        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_slot_creation_read_only(
                db=db_session,
                eco_experience_id=exp.id,
                provider_profile=provider,
                payload=payload,
                now_utc=now,
            )
        assert exc.value.code == "ECO_SLOT_OVERLAPPING"
        assert exc.value.details["overlapping_slot_id"] == existing_slot.id

    def test_db_active_seasonal_restriction_rejection(self, db_session: Session, seeded_environment):
        provider = seeded_environment["provider"]
        exp = seeded_environment["experience"]

        now = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)

        # Seed active seasonal restriction for December 15 - 31
        restriction = EcoSeasonRestriction(
            eco_experience_id=exp.id,
            start_date=date(2026, 12, 15),
            end_date=date(2026, 12, 31),
            restriction_type="weather_alert",
            reason="High landslide risk during December monsoon",
            is_active=True,
            created_at=now,
        )
        db_session.add(restriction)
        db_session.flush()

        # Attempt slot in restricted window (December 20)
        slot_start = datetime(2026, 12, 20, 9, 0, tzinfo=UTC)
        slot_end = datetime(2026, 12, 20, 13, 0, tzinfo=UTC)

        payload = EcoSlotCreate(starts_at=slot_start, ends_at=slot_end, capacity=6)

        with pytest.raises(EcoSlotValidationError) as exc:
            validate_eco_slot_creation_read_only(
                db=db_session,
                eco_experience_id=exp.id,
                provider_profile=provider,
                payload=payload,
                now_utc=now,
            )
        assert exc.value.code == "ECO_SEASON_RESTRICTED"
        assert exc.value.details["restriction_type"] == "weather_alert"
