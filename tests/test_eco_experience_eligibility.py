"""
Automated Unit Tests for M04 Shared Eco Eligibility Service.

Target:
    `app.services.eco_experience_eligibility.validate_eco_booking_eligibility`

Covers:
1. EcoExperience not found -> ECO_NOT_FOUND.
2. Unapproved experiences (draft, pending_approval, rejected) -> ECO_NOT_APPROVED.
3. Unpublished experience (approved but is_published=False) -> ECO_NOT_PUBLISHED.
4. Deactivated experience -> ECO_NOT_APPROVED / ECO_NOT_PUBLISHED.
5. Invalid slot timing (end <= start) -> ECO_INVALID_SLOT_WINDOW.
6. Invalid participant counts (<=0, non-int) -> ECO_INVALID_PARTICIPANTS.
7. Maximum group-size enforcement (> max_group_size) -> ECO_GROUP_SIZE_EXCEEDED.
8. Active seasonal restriction overlap -> ECO_SEASON_RESTRICTED.
9. Inactive seasonal restriction overlap -> validation succeeds.
10. Successful validation with participants=None (slot creation / public availability).
11. Successful validation with positive participants (booking reservation).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.eco_experience import (
    EcoExperience,
    EcoSafetyRule,
    EcoSeasonRestriction,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.services.eco_experience_eligibility import (
    EcoEligibilityException,
    EcoEligibilityResult,
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
def base_eco_experience(db_session: Session) -> EcoExperience:
    user = User(
        full_name="Eco Provider",
        email="provider@test.com",
        password_hash="hashed_pw",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(user)
    db_session.flush()

    provider = ProviderProfile(
        user_id=user.id,
        business_name="Sinharaja Green Trails Ltd",
        provider_type="eco_tourism",
        district="Ratnapura",
        approval_status="approved",
        phone="+94771234567",
        created_at=datetime.now(UTC),
    )
    db_session.add(provider)
    db_session.flush()

    experience = EcoExperience(
        provider_profile_id=provider.id,
        title="Sinharaja Endemic Bird Trail",
        eco_category="Wildlife & Birdwatching",
        description="Guided birding trail in Sinharaja rainforest.",
        duration_hours=Decimal("4.00"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kudawa Conservation Center",
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        currency="LKR",
        max_group_size=12,
        min_participants=2,
        environmental_guidelines="Stay strictly on marked paths. No littering.",
        wildlife_safety_summary="No feeding of wildlife. Maintain silence.",
        free_cancellation_hours=24,
        listing_status="approved",
        is_published=True,
        eco_badge_status="none",
    )
    db_session.add(experience)
    db_session.flush()

    rule1 = EcoSafetyRule(
        eco_experience_id=experience.id,
        rule_type="wildlife_no_feeding",
        title="No Feeding of Birds",
        description="Feeding alters natural bird foraging behaviors.",
        is_mandatory=True,
    )
    rule2 = EcoSafetyRule(
        eco_experience_id=experience.id,
        rule_type="environmental_conduct",
        title="Stay on Marked Trail",
        description="Avoid sensitive sapling growth.",
        is_mandatory=True,
    )
    db_session.add_all([rule1, rule2])
    db_session.commit()
    db_session.refresh(experience)
    return experience


# ==============================================================================
# 1. Experience Existence & Approval Tests
# ==============================================================================


def test_experience_not_found(db_session: Session):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    with pytest.raises(EcoEligibilityException) as exc_info:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=99999,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_info.value.code == "ECO_NOT_FOUND"
    assert "99999" in exc_info.value.message


def test_unapproved_experience_statuses(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    # Test 'draft'
    base_eco_experience.listing_status = "draft"
    db_session.commit()
    with pytest.raises(EcoEligibilityException) as exc_draft:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_draft.value.code == "ECO_NOT_APPROVED"

    # Test 'pending_approval'
    base_eco_experience.listing_status = "pending_approval"
    db_session.commit()
    with pytest.raises(EcoEligibilityException) as exc_pending:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_pending.value.code == "ECO_NOT_APPROVED"

    # Test 'rejected'
    base_eco_experience.listing_status = "rejected"
    db_session.commit()
    with pytest.raises(EcoEligibilityException) as exc_rejected:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_rejected.value.code == "ECO_NOT_APPROVED"


def test_unpublished_and_deactivated_experience(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    # Approved but is_published = False
    base_eco_experience.listing_status = "approved"
    base_eco_experience.is_published = False
    db_session.commit()

    with pytest.raises(EcoEligibilityException) as exc_unpub:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_unpub.value.code == "ECO_NOT_PUBLISHED"

    # Deactivated status
    base_eco_experience.listing_status = "deactivated"
    base_eco_experience.is_published = False
    db_session.commit()

    with pytest.raises(EcoEligibilityException) as exc_deact:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_deact.value.code == "ECO_NOT_APPROVED"


# ==============================================================================
# 2. Slot Window Validation Tests
# ==============================================================================


def test_invalid_slot_window_timing(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)

    # End earlier than start
    start = now + timedelta(days=2)
    end = start - timedelta(hours=1)
    with pytest.raises(EcoEligibilityException) as exc_backwards:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_backwards.value.code == "ECO_INVALID_SLOT_WINDOW"

    # End equal to start
    with pytest.raises(EcoEligibilityException) as exc_equal:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=start,
        )
    assert exc_equal.value.code == "ECO_INVALID_SLOT_WINDOW"


# ==============================================================================
# 3. Participant Count & Group Size Tests
# ==============================================================================


def test_invalid_participant_counts(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    # Zero participants
    with pytest.raises(EcoEligibilityException) as exc_zero:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=0,
            slot_start=start,
            slot_end=end,
        )
    assert exc_zero.value.code == "ECO_INVALID_PARTICIPANTS"

    # Negative participants
    with pytest.raises(EcoEligibilityException) as exc_neg:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=-3,
            slot_start=start,
            slot_end=end,
        )
    assert exc_neg.value.code == "ECO_INVALID_PARTICIPANTS"

    # Boolean passed as participants (e.g. True)
    with pytest.raises(EcoEligibilityException) as exc_bool:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=True,  # type: ignore
            slot_start=start,
            slot_end=end,
        )
    assert exc_bool.value.code == "ECO_INVALID_PARTICIPANTS"


def test_group_size_exceeded(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    # Experience max_group_size is 12; request 13 participants
    with pytest.raises(EcoEligibilityException) as exc_group:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=13,
            slot_start=start,
            slot_end=end,
        )
    assert exc_group.value.code == "ECO_GROUP_SIZE_EXCEEDED"
    assert "13" in exc_group.value.message
    assert "12" in exc_group.value.message


# ==============================================================================
# 4. Seasonal Restrictions Tests
# ==============================================================================


def test_active_seasonal_restriction_blocks_slot(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    target_date = (now + timedelta(days=10)).date()

    restriction = EcoSeasonRestriction(
        eco_experience_id=base_eco_experience.id,
        start_date=target_date - timedelta(days=2),
        end_date=target_date + timedelta(days=3),
        restriction_type="monsoon_season",
        reason="Heavy Southwest Monsoon Flash Flood Warning",
        is_active=True,
    )
    db_session.add(restriction)
    db_session.commit()

    start = datetime.combine(target_date, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8)
    end = start + timedelta(hours=4)

    # Slot falls inside monsoon window -> ECO_SEASON_RESTRICTED
    with pytest.raises(EcoEligibilityException) as exc_season:
        validate_eco_booking_eligibility(
            db=db_session,
            eco_experience_id=base_eco_experience.id,
            participants=2,
            slot_start=start,
            slot_end=end,
        )
    assert exc_season.value.code == "ECO_SEASON_RESTRICTED"
    assert "monsoon_season" in exc_season.value.message
    assert "Flash Flood Warning" in exc_season.value.message

    # Deactivate the restriction -> now validation succeeds
    restriction.is_active = False
    db_session.commit()

    res = validate_eco_booking_eligibility(
        db=db_session,
        eco_experience_id=base_eco_experience.id,
        participants=2,
        slot_start=start,
        slot_end=end,
    )
    assert res.eligible is True


def test_non_overlapping_seasonal_restriction_allows_slot(
    db_session: Session, base_eco_experience: EcoExperience
):
    now = datetime.now(UTC)
    slot_date = (now + timedelta(days=5)).date()

    # Active restriction in day 20 to 30 (does not overlap day 5)
    restriction = EcoSeasonRestriction(
        eco_experience_id=base_eco_experience.id,
        start_date=slot_date + timedelta(days=15),
        end_date=slot_date + timedelta(days=25),
        restriction_type="wildlife_breeding",
        reason="Serendib Scops-Owl Breeding Nesting Period",
        is_active=True,
    )
    db_session.add(restriction)
    db_session.commit()

    start = datetime.combine(slot_date, datetime.min.time(), tzinfo=UTC) + timedelta(hours=7)
    end = start + timedelta(hours=3)

    result = validate_eco_booking_eligibility(
        db=db_session,
        eco_experience_id=base_eco_experience.id,
        participants=4,
        slot_start=start,
        slot_end=end,
    )
    assert result.eligible is True
    assert result.eco_experience_id == base_eco_experience.id


# ==============================================================================
# 5. Successful Validation (None & Positive Participants)
# ==============================================================================


def test_successful_validation_with_none_participants(
    db_session: Session, base_eco_experience: EcoExperience
):
    """
    Test scenario: Module 03 slot creation or public availability discovery
    where participants is None.
    """
    now = datetime.now(UTC)
    start = now + timedelta(days=3)
    end = start + timedelta(hours=4)

    result = validate_eco_booking_eligibility(
        db=db_session,
        eco_experience_id=base_eco_experience.id,
        participants=None,
        slot_start=start,
        slot_end=end,
    )

    assert isinstance(result, EcoEligibilityResult)
    assert result.eligible is True
    assert result.eco_experience_id == base_eco_experience.id
    assert result.max_group_size == 12
    assert result.min_participants == 2
    assert result.pricing_type == "per_person"
    assert result.base_price == Decimal("4500.00")
    assert result.currency == "LKR"
    assert result.free_cancellation_hours == 24
    assert result.meeting_point == "Kudawa Conservation Center"
    assert "Stay strictly on marked paths" in result.environmental_guidelines
    assert len(result.mandatory_safety_rules) == 2
    assert "No Feeding of Birds" in result.mandatory_safety_rules


def test_successful_validation_with_positive_participants(
    db_session: Session, base_eco_experience: EcoExperience
):
    """
    Test scenario: Module 03 booking creation where participants=4 (<= 12).
    """
    now = datetime.now(UTC)
    start = now + timedelta(days=4)
    end = start + timedelta(hours=4)

    result = validate_eco_booking_eligibility(
        db=db_session,
        eco_experience_id=base_eco_experience.id,
        participants=4,
        slot_start=start,
        slot_end=end,
    )

    assert result.eligible is True
    assert result.eco_experience_id == base_eco_experience.id
    assert result.max_group_size == 12
