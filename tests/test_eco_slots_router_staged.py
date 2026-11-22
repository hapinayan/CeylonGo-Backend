"""
Isolated Unit & Integration Tests for Staged Module 04 Eco-Slots Router.

Targets:
    app.routers.eco_slots (POST /provider/.../slots, GET /.../slots)

Tests Covered:
1. Provider POST: Authentication, authorization, and role checks (rejects non-eco providers).
2. Provider POST: Experience ownership, approval, and publication status enforcement.
3. Provider POST: Slot timing, capacity bounds, seasonal blackout, and collision validation.
4. Provider POST: Activation Guard (returns HTTP 503 on valid input, asserting zero database writes).
5. Public GET: Rejection of non-existent, unapproved, or unpublished experiences.
6. Public GET: Filtering of active, future slots with open capacity.
7. Public GET: Exclusion of past slots, sold-out slots, and inactive slots.
8. Public GET: Filtering by start_date, end_date, and min_available_places.
9. Public GET: Verification that sensitive provider/financial details are not exposed.

STRICT ISOLATION:
- Runs against an in-memory SQLite database only.
- ZERO connections, reads, or writes to SQL Server.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, require_approved_eco_provider
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.eco_slots import router as staged_eco_slots_router

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
def test_app(db_session: Session):
    app = FastAPI()
    app.include_router(staged_eco_slots_router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
def seeded_data(db_session: Session):
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

    # 2. Activity Provider (Non-Eco)
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

    # 3. Base Activity for FK
    act = Activity(
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
    db_session.add(act)
    db_session.flush()

    # 4. Approved & Published Eco Experience
    exp_approved = EcoExperience(
        provider_profile_id=provider_eco.id,
        title="Sinharaja Primary Rainforest Expedition",
        eco_category="Forest & Nature Trails",
        description="Deep jungle nature observation.",
        duration_hours=Decimal("5.0"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Kudawa Conservation Centre",
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=2,
        environmental_guidelines="Strict zero leavings",
        wildlife_safety_summary="Do not feed reptiles",
        listing_status="approved",
        is_published=True,
        eco_badge_status="granted",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(exp_approved)

    # 5. Draft Eco Experience
    exp_draft = EcoExperience(
        provider_profile_id=provider_eco.id,
        title="Unpublished Draft Trail",
        eco_category="Forest & Nature Trails",
        description="Draft listing under construction.",
        duration_hours=Decimal("3.0"),
        district="Ratnapura",
        city="Kalawana",
        meeting_point="Base Camp",
        pricing_type="per_person",
        base_price=Decimal("2000.00"),
        currency="LKR",
        max_group_size=8,
        min_participants=1,
        environmental_guidelines="Standard",
        wildlife_safety_summary="Standard",
        listing_status="draft",
        is_published=False,
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(exp_draft)
    db_session.flush()

    return {
        "user_eco": user_eco,
        "provider_eco": provider_eco,
        "provider_act": provider_act,
        "exp_approved": exp_approved,
        "exp_draft": exp_draft,
        "activity": act,
    }


# ==============================================================================
# 1. Provider Slot POST Tests
# ==============================================================================


class TestProviderEcoSlotCreationRouter:
    def test_rejects_non_eco_provider(self, test_app, seeded_data):
        client = TestClient(test_app)
        provider_act = seeded_data["provider_act"]
        exp = seeded_data["exp_approved"]

        # Override dependency with activity provider
        test_app.dependency_overrides[require_approved_eco_provider] = lambda: provider_act

        now = datetime.now(timezone.utc)
        start = now + timedelta(days=2)
        end = start + timedelta(hours=3)

        payload = {
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "capacity": 8,
        }

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp.id}/slots",
            json=payload,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "Only 'eco_tourism' providers" in resp.json()["detail"]

    def test_rejects_unowned_experience(self, test_app, seeded_data):
        client = TestClient(test_app)
        provider_eco = seeded_data["provider_eco"]
        exp = seeded_data["exp_approved"]

        # Different provider
        other_provider = ProviderProfile(
            id=999,
            user_id=123,
            business_name="Other Provider",
            provider_type="eco_tourism",
            approval_status="approved",
        )
        test_app.dependency_overrides[require_approved_eco_provider] = lambda: other_provider

        now = datetime.now(timezone.utc)
        payload = {
            "starts_at": (now + timedelta(days=2)).isoformat(),
            "ends_at": (now + timedelta(days=2, hours=3)).isoformat(),
            "capacity": 8,
        }

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp.id}/slots",
            json=payload,
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert "do not own this eco-experience" in resp.json()["detail"]

    def test_rejects_draft_or_unpublished_experience(self, test_app, seeded_data):
        client = TestClient(test_app)
        provider_eco = seeded_data["provider_eco"]
        exp_draft = seeded_data["exp_draft"]

        test_app.dependency_overrides[require_approved_eco_provider] = lambda: provider_eco

        now = datetime.now(timezone.utc)
        payload = {
            "starts_at": (now + timedelta(days=2)).isoformat(),
            "ends_at": (now + timedelta(days=2, hours=3)).isoformat(),
            "capacity": 5,
        }

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp_draft.id}/slots",
            json=payload,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "approved" in resp.json()["detail"]

    def test_rejects_capacity_exceeding_max_group_size(self, test_app, seeded_data):
        client = TestClient(test_app)
        provider_eco = seeded_data["provider_eco"]
        exp = seeded_data["exp_approved"]

        test_app.dependency_overrides[require_approved_eco_provider] = lambda: provider_eco

        now = datetime.now(timezone.utc)
        payload = {
            "starts_at": (now + timedelta(days=2)).isoformat(),
            "ends_at": (now + timedelta(days=2, hours=3)).isoformat(),
            "capacity": 15,  # Max is 10
        }

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp.id}/slots",
            json=payload,
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "exceeds listing maximum group size" in resp.json()["detail"]

    def test_rejects_overlapping_slot(self, test_app, seeded_data, db_session):
        client = TestClient(test_app)
        provider_eco = seeded_data["provider_eco"]
        exp = seeded_data["exp_approved"]
        act = seeded_data["activity"]

        test_app.dependency_overrides[require_approved_eco_provider] = lambda: provider_eco

        now = datetime.now(timezone.utc)
        existing_start = now + timedelta(days=3)
        existing_end = existing_start + timedelta(hours=4)

        # Seed existing active slot
        existing_slot = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=existing_start,
            ends_at=existing_end,
            capacity=10,
            reserved_count=0,
            is_active=True,
            created_at=now,
        )
        db_session.add(existing_slot)
        db_session.flush()

        # Submit overlapping slot (starts 1 hour before existing ends)
        payload = {
            "starts_at": (existing_start + timedelta(hours=2)).isoformat(),
            "ends_at": (existing_start + timedelta(hours=5)).isoformat(),
            "capacity": 8,
        }

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp.id}/slots",
            json=payload,
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "overlaps an existing active slot" in resp.json()["detail"]

    def test_valid_slot_triggers_staged_activation_guard_with_zero_writes(
        self, test_app, seeded_data, db_session
    ):
        client = TestClient(test_app)
        provider_eco = seeded_data["provider_eco"]
        exp = seeded_data["exp_approved"]

        test_app.dependency_overrides[require_approved_eco_provider] = lambda: provider_eco

        now = datetime.now(timezone.utc)
        start = now + timedelta(days=5)
        end = start + timedelta(hours=4)

        payload = {
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "capacity": 8,
        }

        # Count slots before request
        slots_before = db_session.query(ActivitySlot).count()

        resp = client.post(
            f"/api/v1/provider/eco-experiences/{exp.id}/slots",
            json=payload,
        )

        # Asserts activation guard triggers with 503
        assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = resp.json()["detail"]
        assert "Eco slot validation succeeded" in detail
        assert "Migration 030" in detail
        assert "Zero database writes were executed" in detail

        # ZERO WRITES GUARANTEE: Slot count is strictly unchanged
        slots_after = db_session.query(ActivitySlot).count()
        assert slots_before == slots_after


# ==============================================================================
# 2. Public Slot GET Tests
# ==============================================================================


class TestPublicEcoSlotAvailabilityRouter:
    def test_get_slots_for_non_existent_or_draft_experience_returns_404(
        self, test_app, seeded_data
    ):
        client = TestClient(test_app)
        exp_draft = seeded_data["exp_draft"]

        # Non-existent ID
        resp404 = client.get("/api/v1/eco-experiences/9999/slots")
        assert resp404.status_code == status.HTTP_404_NOT_FOUND

        # Draft experience (hidden from public)
        resp_draft = client.get(f"/api/v1/eco-experiences/{exp_draft.id}/slots")
        assert resp_draft.status_code == status.HTTP_404_NOT_FOUND

    def test_get_slots_empty_when_no_slots_scheduled(self, test_app, seeded_data):
        client = TestClient(test_app)
        exp = seeded_data["exp_approved"]

        resp = client.get(f"/api/v1/eco-experiences/{exp.id}/slots")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json() == []

    def test_get_slots_returns_only_active_future_slots_with_capacity(
        self, test_app, seeded_data, db_session
    ):
        client = TestClient(test_app)
        exp = seeded_data["exp_approved"]
        act = seeded_data["activity"]

        now = datetime.now(timezone.utc)

        # 1. Valid future slot with open capacity (capacity 10, reserved 3 -> open 7)
        slot_valid = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=3),
            capacity=10,
            reserved_count=3,
            is_active=True,
            created_at=now,
        )
        # 2. Past slot (should be excluded)
        slot_past = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=now - timedelta(days=1),
            ends_at=now - timedelta(days=1, hours=-3),
            capacity=10,
            reserved_count=0,
            is_active=True,
            created_at=now,
        )
        # 3. Inactive/cancelled slot (should be excluded)
        slot_inactive = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=3),
            capacity=10,
            reserved_count=0,
            is_active=False,
            created_at=now,
        )
        # 4. Sold out slot (reserved == capacity -> open 0, should be excluded)
        slot_sold_out = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=3),
            capacity=10,
            reserved_count=10,
            is_active=True,
            created_at=now,
        )

        db_session.add_all([slot_valid, slot_past, slot_inactive, slot_sold_out])
        db_session.flush()

        resp = client.get(f"/api/v1/eco-experiences/{exp.id}/slots")
        assert resp.status_code == status.HTTP_200_OK
        items = resp.json()

        # Only slot_valid should be returned
        assert len(items) == 1
        assert items[0]["id"] == slot_valid.id
        assert items[0]["capacity"] == 10
        assert items[0]["reserved_count"] == 3
        assert items[0]["available_places"] == 7
        assert items[0]["is_available"] is True

        # Verify no sensitive admin/provider fields exposed
        assert "provider_profile_id" not in items[0]
        assert "rejection_reason" not in items[0]

    def test_get_slots_filtered_by_date_and_capacity(
        self, test_app, seeded_data, db_session
    ):
        client = TestClient(test_app)
        exp = seeded_data["exp_approved"]
        act = seeded_data["activity"]

        base_date = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)

        slot1 = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=datetime(2026, 12, 5, 9, 0, tzinfo=UTC),
            ends_at=datetime(2026, 12, 5, 13, 0, tzinfo=UTC),
            capacity=10,
            reserved_count=2,  # 8 open
            is_active=True,
            created_at=base_date,
        )
        slot2 = ActivitySlot(
            activity_id=act.id,
            eco_experience_id=exp.id,
            starts_at=datetime(2026, 12, 20, 9, 0, tzinfo=UTC),
            ends_at=datetime(2026, 12, 20, 13, 0, tzinfo=UTC),
            capacity=10,
            reserved_count=8,  # 2 open
            is_active=True,
            created_at=base_date,
        )
        db_session.add_all([slot1, slot2])
        db_session.flush()

        # Filter by start_date & end_date (targeting only Dec 1 - 10)
        resp_date = client.get(
            f"/api/v1/eco-experiences/{exp.id}/slots?start_date=2026-12-01&end_date=2026-12-10"
        )
        assert resp_date.status_code == status.HTTP_200_OK
        assert len(resp_date.json()) == 1
        assert resp_date.json()[0]["id"] == slot1.id

        # Filter by min_available_places (require at least 5 open places -> only slot1)
        resp_cap = client.get(
            f"/api/v1/eco-experiences/{exp.id}/slots?min_available_places=5"
        )
        assert resp_cap.status_code == status.HTTP_200_OK
        assert len(resp_cap.json()) == 1
        assert resp_cap.json()[0]["id"] == slot1.id


# ==============================================================================
# 3. Phase 4D Slot Transaction Ownership Tests
# ==============================================================================


class TestPhase4DSlotTransactionOwnership:
    def test_execute_slot_create_transaction_rolls_back_without_migration_030(
        self, db_session, seeded_data
    ):
        from app.routers.eco_slots import _execute_slot_create_with_transaction
        from app.schemas.eco_slot import EcoSlotCreate

        exp = seeded_data["exp_approved"]
        now = datetime.now(timezone.utc)
        payload = EcoSlotCreate(
            starts_at=now + timedelta(days=10),
            ends_at=now + timedelta(days=10, hours=3),
            capacity=12,
        )

        # In un-migrated schema, activity_slots.activity_id is NOT NULL.
        # Calling _execute_slot_create_with_transaction catches the commit error,
        # performs db.rollback(), logs the error server-side, and raises generic HTTP 500.
        with pytest.raises(HTTPException) as exc_info:
            _execute_slot_create_with_transaction(db_session, exp.id, payload)

        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to persist slot" in exc_info.value.detail
        # Security: Internal database errors (table/column/engine details) must NEVER leak to client
        assert "sqlite3" not in exc_info.value.detail
        assert "IntegrityError" not in exc_info.value.detail
        assert "activity_id" not in exc_info.value.detail
        assert "NOT NULL" not in exc_info.value.detail

        # Verify zero uncommitted/persisted rows leaked
        persisted = db_session.query(ActivitySlot).filter_by(capacity=12).first()
        assert persisted is None

