"""
Isolated Unit & Integration Tests for Staged Module 04 Eco-Bookings Router.

Targets:
    app.routers.eco_bookings (POST /eco-bookings, GET /eco-bookings/my-bookings, POST /eco-bookings/{id}/cancel)

Tests Covered:
1. Traveler POST /api/v1/eco-bookings:
   - Authentication and traveler role checks (rejects provider / admin with 403).
   - Inactive traveler check (403).
   - Payment method restriction: strictly pay_at_location (online rejected with 400).
   - Slot and experience validation: non-existent slot (404), inactive slot (409), past slot (409).
   - Group size and capacity bounds (422).
   - Minimum-age verification (422).
   - Activation Guard: returns HTTP 503 on valid input, asserting zero database writes.
2. Traveler GET /api/v1/eco-bookings/my-bookings:
   - Role authorization check (rejects non-traveler with 403).
   - Returns empty list for traveler with no bookings.
   - Read-only retrieval of traveler's eco-tourism bookings.
   - Excludes non-eco bookings (where eco_experience_id IS NULL).
   - Excludes bookings of other travelers.
   - Formats canonical booking references (ECO-YYYYMMDD-XXXX).
   - Issue 1 Safety: asserts participants_list is empty list to avoid ORM column mismatch crash.
3. Traveler POST /api/v1/eco-bookings/{id}/cancel:
   - Role authorization and ownership check (rejects non-traveler with 403, non-owner with 404).
   - Non-existent booking check (404).
   - Rejection of already cancelled or completed bookings (409).
   - Cutoff validation: slot already started (409).
   - Activation Guard: returns HTTP 503 on valid input, asserting zero database writes.

STRICT ISOLATION:
- Runs against an in-memory SQLite database only.
- ZERO connections, reads, or writes to SQL Server.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import require_approved_eco_provider
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.eco_bookings import router as staged_eco_bookings_router
from app.services.eco_booking_service import format_eco_booking_reference

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
    with test_engine.connect() as conn:
        conn.execute(
            text(
                "ALTER TABLE activity_booking_participants ADD COLUMN emergency_contact_name NVARCHAR(150)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE activity_booking_participants ADD COLUMN emergency_contact_phone NVARCHAR(30)"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE activity_booking_participants ADD COLUMN waiver_signed BOOLEAN DEFAULT 0"
            )
        )
        conn.commit()
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

    # 6. Traveler User 1 (Active)
    traveler = User(
        full_name="Alice Traveler",
        email="traveler@ceylongo.test",
        password_hash="hashed_pw_traveler",
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(traveler)

    # 7. Traveler User 2 (Inactive)
    inactive_traveler = User(
        full_name="Inactive Bob",
        email="inactive@ceylongo.test",
        password_hash="hashed_pw_inactive",
        role="traveler",
        is_active=False,
        created_at=datetime.now(UTC),
    )
    db_session.add(inactive_traveler)

    # 8. Another Traveler User (for isolation checks)
    other_traveler = User(
        full_name="Charlie Other",
        email="charlie@ceylongo.test",
        password_hash="hashed_pw_other",
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(other_traveler)

    # 9. Second Approved Eco Provider (for cross-provider isolation)
    second_user_eco = User(
        full_name="Second Eco Guide",
        email="second_provider@ceylongo.test",
        password_hash="hashed_pw_eco2",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(second_user_eco)
    db_session.flush()

    second_provider_eco = ProviderProfile(
        user_id=second_user_eco.id,
        business_name="Knuckles Eco Treks",
        provider_type="eco_tourism",
        district="Matale",
        approval_status="approved",
        phone="+94770005566",
        identity_document_type="nic",
        created_at=datetime.now(UTC),
    )
    db_session.add(second_provider_eco)
    db_session.flush()

    second_experience = EcoExperience(
        provider_profile_id=second_provider_eco.id,
        title="Knuckles Mountain Range Cloud Forest Walk",
        eco_category="Forest & Nature Trails",
        description="High altitude trail walk.",
        duration_hours=Decimal("6.0"),
        district="Matale",
        city="Matale",
        meeting_point="Riverston Gap",
        pricing_type="per_person",
        base_price=Decimal("6000.00"),
        currency="LKR",
        min_participants=2,
        max_group_size=10,
        min_age=12,
        environmental_guidelines="Strict zero trace policy",
        wildlife_safety_summary="Do not feed animals",
        free_cancellation_hours=24,
        listing_status="approved",
        is_published=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(second_experience)
    db_session.flush()

    second_slot = ActivitySlot(
        activity_id=base_activity.id,
        eco_experience_id=second_experience.id,
        starts_at=future_start,
        ends_at=future_start + timedelta(hours=6),
        capacity=10,
        reserved_count=0,
        is_active=True,
    )
    db_session.add(second_slot)

    # 10. Unapproved Eco Provider (for RBAC testing)
    unapproved_user = User(
        full_name="Pending Provider",
        email="pending@ceylongo.test",
        password_hash="hashed_pw_pend",
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db_session.add(unapproved_user)
    db_session.flush()

    unapproved_profile = ProviderProfile(
        user_id=unapproved_user.id,
        business_name="Unapproved Tours",
        provider_type="eco_tourism",
        district="Galle",
        approval_status="pending",
        phone="+94770007788",
        identity_document_type="nic",
        created_at=datetime.now(UTC),
    )
    db_session.add(unapproved_profile)

    db_session.commit()

    return {
        "provider_user": user_eco,
        "provider_profile": provider_eco,
        "second_provider_user": second_user_eco,
        "second_provider_profile": second_provider_eco,
        "second_experience": second_experience,
        "second_slot": second_slot,
        "unapproved_provider_user": unapproved_user,
        "unapproved_provider_profile": unapproved_profile,
        "base_activity": base_activity,
        "experience": experience,
        "slot": slot,
        "traveler": traveler,
        "inactive_traveler": inactive_traveler,
        "other_traveler": other_traveler,
    }


def create_app_with_auth(
    db_session: Session,
    current_user: User,
    current_provider: ProviderProfile | None = None,
):
    app = FastAPI()
    app.include_router(staged_eco_bookings_router)

    def override_get_db():
        yield db_session

    def override_get_current_user():
        return current_user

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    if current_provider is not None:
        def override_require_approved_eco_provider():
            return current_provider
        app.dependency_overrides[require_approved_eco_provider] = override_require_approved_eco_provider
    return app


# ==============================================================================
# 1. Traveler Booking Creation (POST /api/v1/eco-bookings)
# ==============================================================================


class TestTravelerEcoBookingCreation:
    def test_rejects_non_traveler_role(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {
            "eco_slot_id": seeded_env["slot"].id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "traveler" in res.json()["detail"].lower()

    def test_rejects_inactive_traveler(self, db_session, seeded_env):
        inactive_user = seeded_env["inactive_traveler"]
        app = create_app_with_auth(db_session, inactive_user)
        client = TestClient(app)

        payload = {
            "eco_slot_id": seeded_env["slot"].id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "inactive" in res.json()["detail"].lower()

    def test_rejects_online_payment_method(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": seeded_env["slot"].id,
            "participants": 2,
            "payment_method": "online",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "pay_at_location" in res.json()["detail"]

    def test_rejects_non_existent_slot(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": 999999,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_rejects_inactive_slot(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        slot.is_active = False
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_409_CONFLICT

    def test_rejects_past_slot(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        slot.starts_at = datetime.now(UTC) - timedelta(hours=1)
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_409_CONFLICT

    def test_rejects_group_size_below_min(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 1,  # Experience minimum is 2
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_rejects_group_size_exceeding_capacity(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        slot.capacity = 5
        slot.reserved_count = 4  # Only 1 place available
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 3,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_rejects_underage_participant(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 2,
            "payment_method": "pay_at_location",
            "participants_roster": [
                {"full_name": "Adult Traveler", "age": 28},
                {"full_name": "Underage Child", "age": 5},  # min_age is 8
            ],
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_staged_activation_guard_returns_503_and_zero_writes(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 3,
            "payment_method": "pay_at_location",
            "special_requests": "Need walking sticks for trail.",
            "participants_roster": [
                {"full_name": "Alice Traveler", "age": 30},
                {"full_name": "Bob Guest", "age": 35},
                {"full_name": "Charlie Guest", "age": 12},
            ],
        }

        # Pre-condition: 0 bookings in database
        initial_booking_count = db_session.query(ActivityBooking).count()
        assert initial_booking_count == 0

        res = client.post("/api/v1/eco-bookings", json=payload)

        # STAGED ACTIVATION GUARD: HTTP 503
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = res.json()["detail"]
        assert "validation succeeded" in detail.lower()
        assert "staged and disabled" in detail.lower()
        assert "Issue 1" in detail
        assert "Issue 2" in detail
        assert "Zero database writes were executed" in detail

        # Post-condition: STILL 0 bookings in database (ZERO WRITES)
        post_booking_count = db_session.query(ActivityBooking).count()
        assert post_booking_count == 0

    def test_rejects_unpublished_or_draft_experience(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        exp = seeded_env["experience"]
        exp.is_published = False
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": seeded_env["slot"].id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "not approved or is currently unpublished" in res.json()["detail"]

    def test_rejects_roster_count_mismatch(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 3,
            "payment_method": "pay_at_location",
            "participants_roster": [
                {"full_name": "Alice Traveler", "age": 30},
                {"full_name": "Bob Guest", "age": 35},
            ],
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "does not match requested participant count" in str(res.json())

    def test_rejects_slot_experience_mismatch(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        slot.eco_experience_id = None
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 2,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "not associated with an Eco-Tourism experience" in res.json()["detail"]

    def test_rejects_group_size_exceeding_listing_max(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]
        exp.max_group_size = 4
        slot.capacity = 10
        db_session.flush()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {
            "eco_slot_id": slot.id,
            "participants": 5,
            "payment_method": "pay_at_location",
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "exceeds the maximum allowed group size" in res.json()["detail"]


# ==============================================================================
# 2. Traveler Booking History (GET /api/v1/eco-bookings/my-bookings)
# ==============================================================================


class TestTravelerEcoBookingHistory:
    def test_rejects_non_traveler_role(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_returns_empty_list_when_no_bookings(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == []

    def test_returns_only_travelers_eco_bookings(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        other_traveler = seeded_env["other_traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        # 1. Traveler's Eco Booking
        booking_eco = ActivityBooking(
            id=101,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking_eco)

        # 2. Traveler's NON-Eco Booking (Regular activity booking where eco_experience_id IS NULL)
        booking_non_eco = ActivityBooking(
            id=102,
            activity_slot_id=slot.id,
            eco_experience_id=None,
            user_id=traveler.id,
            participants=1,
            total_price=Decimal("4000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 11, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 11, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking_non_eco)

        # 3. Other Traveler's Eco Booking (different user)
        booking_other_user = ActivityBooking(
            id=103,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=other_traveler.id,
            participants=3,
            total_price=Decimal("13500.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking_other_user)
        db_session.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        item = data[0]
        assert item["id"] == 101
        assert item["eco_experience_id"] == exp.id
        assert item["participants"] == 2
        assert Decimal(item["total_price"]) == Decimal("9000.00")
        assert item["booking_status"] == "pending"
        assert item["payment_status"] == "unpaid"
        assert item["payment_method"] == "pay_at_location"

    def test_returns_persisted_booking_reference_from_shared_table(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        provider_profile = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=110,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking)
        db_session.flush()

        persisted_ref = BookingReference(
            id=1,
            booking_reference="BK-PERSISTED-EXACT-001",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add(persisted_ref)
        db_session.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        assert data[0]["booking_reference"] == "BK-PERSISTED-EXACT-001"

    def test_falls_back_to_formatted_reference_when_no_persisted_record(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        dt = datetime(2026, 11, 19, 14, 30, 0, tzinfo=UTC)
        booking = ActivityBooking(
            id=111,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=dt,
            updated_at=dt,
        )
        db_session.add(booking)
        db_session.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        expected_fallback = format_eco_booking_reference(111, dt)
        assert data[0]["booking_reference"] == expected_fallback
        assert data[0]["booking_reference"] == "ECO-20261119-0111"

    def test_deterministic_selection_with_duplicate_booking_references(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        provider_profile = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=112,
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
        db_session.flush()

        # Seed two references for the same target booking
        older_ref = BookingReference(
            id=50,
            booking_reference="BK-OLDER-REF-050",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        newer_ref = BookingReference(
            id=60,
            booking_reference="BK-NEWER-REF-060",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add_all([older_ref, newer_ref])
        db_session.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        # Deterministic selection picks the newest by ID
        assert data[0]["booking_reference"] == "BK-NEWER-REF-060"

    def test_prevents_cross_module_or_cross_user_reference_matching(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        other_traveler = seeded_env["other_traveler"]
        provider_profile = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=113,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking)
        db_session.flush()

        # 1. Homestay module reference for the same target_booking_id (should NOT match)
        homestay_ref = BookingReference(
            id=70,
            booking_reference="BK-HOMESTAY-CROSS-REF",
            module_type="homestay",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        # 2. Eco reference belonging to another user for the same target_booking_id (should NOT match)
        other_user_ref = BookingReference(
            id=71,
            booking_reference="BK-OTHER-USER-REF",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=other_traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add_all([homestay_ref, other_user_ref])
        db_session.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        # Should NOT use homestay or other user's reference, but safely fall back
        assert data[0]["booking_reference"] == "ECO-20261119-0113"

    def test_sourcing_persisted_booking_reference_with_canonical_activity_module_type(
        self, db_session, seeded_env
    ):
        """
        Regression test for Issue 2:
        Verifies that references persisted under module_type='activity' (the SQL Server
        CHECK constraint CK_booking_references_type convention) are correctly sourced
        across both traveler history and provider endpoints.
        """
        traveler = seeded_env["traveler"]
        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=1135,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking)
        db_session.flush()

        activity_ref = BookingReference(
            id=880,
            booking_reference="BK-ACT-CANONICAL-1135",
            module_type="activity",  # SQL Server schema constraint convention
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add(activity_ref)
        db_session.commit()

        # 1. Traveler history
        traveler_app = create_app_with_auth(db_session, traveler)
        t_client = TestClient(traveler_app)
        t_res = t_client.get("/api/v1/eco-bookings/my-bookings")
        assert t_res.status_code == status.HTTP_200_OK
        t_data = [b for b in t_res.json() if b["id"] == 1135]
        assert len(t_data) == 1
        assert t_data[0]["booking_reference"] == "BK-ACT-CANONICAL-1135"

        # 2. Provider listing
        provider_app = create_app_with_auth(db_session, provider_user)
        p_client = TestClient(provider_app)
        p_res = p_client.get("/api/v1/provider/eco-bookings")
        assert p_res.status_code == status.HTTP_200_OK
        p_data = [b for b in p_res.json()["items"] if b["id"] == 1135]
        assert len(p_data) == 1
        assert p_data[0]["booking_reference"] == "BK-ACT-CANONICAL-1135"

        # 3. Provider detail
        p_detail_res = p_client.get(f"/api/v1/provider/eco-bookings/{booking.id}")
        assert p_detail_res.status_code == status.HTTP_200_OK
        assert p_detail_res.json()["booking_reference"] == "BK-ACT-CANONICAL-1135"

    def test_cross_module_isolation_strictly_excludes_other_modules_for_provider(
        self, db_session, seeded_env
    ):
        """
        Regression test for Issue 2:
        Verifies that provider booking listing strictly isolates against cross-module references
        (homestay, tour_guide, rental_vehicle, artisan_reservation) sharing the same target_booking_id.
        """
        traveler = seeded_env["traveler"]
        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=1136,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        db_session.add(booking)
        db_session.flush()

        foreign_refs = [
            BookingReference(
                id=890,
                booking_reference="BK-HS-FORBIDDEN-1136",
                module_type="homestay",
                target_booking_id=booking.id,
                user_id=traveler.id,
                provider_profile_id=provider_profile.id,
                total_price=Decimal("9000.00"),
                currency="LKR",
                booking_status="pending",
                payment_status="unpaid",
                created_at=datetime.now(UTC),
            ),
            BookingReference(
                id=891,
                booking_reference="BK-TG-FORBIDDEN-1136",
                module_type="tour_guide",
                target_booking_id=booking.id,
                user_id=traveler.id,
                provider_profile_id=provider_profile.id,
                total_price=Decimal("9000.00"),
                currency="LKR",
                booking_status="pending",
                payment_status="unpaid",
                created_at=datetime.now(UTC),
            ),
            BookingReference(
                id=892,
                booking_reference="BK-RV-FORBIDDEN-1136",
                module_type="rental_vehicle",
                target_booking_id=booking.id,
                user_id=traveler.id,
                provider_profile_id=provider_profile.id,
                total_price=Decimal("9000.00"),
                currency="LKR",
                booking_status="pending",
                payment_status="unpaid",
                created_at=datetime.now(UTC),
            ),
        ]
        db_session.add_all(foreign_refs)
        db_session.commit()

        provider_app = create_app_with_auth(db_session, provider_user)
        client = TestClient(provider_app)

        res = client.get("/api/v1/provider/eco-bookings")
        assert res.status_code == status.HTTP_200_OK
        match = [b for b in res.json()["items"] if b["id"] == 1136][0]
        # Must NOT match any foreign reference; must fall back to deterministic format
        assert match["booking_reference"] == "ECO-20261119-1136"
        assert "FORBIDDEN" not in match["booking_reference"]

    def test_unrelated_module03_activity_booking_and_reference_strictly_isolated(
        self, db_session, seeded_env
    ):
        """
        Regression test for Task 2 / Issue 2:
        Verifies that when an unrelated Module 03 activity booking has a reference with
        module_type='activity' (for a standard activity where eco_experience_id IS NULL),
        Eco-Tourism booking queries (traveler history, provider listing, provider detail)
        strictly isolate and ignore the unrelated activity booking and its reference.
        """
        traveler = seeded_env["traveler"]
        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        base_act = seeded_env["base_activity"]
        eco_slot = seeded_env["slot"]
        eco_exp = seeded_env["experience"]

        # 1. Create a legitimate Eco booking
        eco_booking = ActivityBooking(
            id=1140,
            activity_slot_id=eco_slot.id,
            eco_experience_id=eco_exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 10, 0, 0, tzinfo=UTC),
        )
        eco_ref = BookingReference(
            id=910,
            booking_reference="ECO-ACT-REF-1140",
            module_type="activity",  # Standard SQL Server constraint type
            target_booking_id=1140,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )

        # 2. Create an unrelated Module 03 Activity slot and booking (eco_experience_id IS NULL)
        unrelated_slot = ActivitySlot(
            id=999,
            activity_id=base_act.id,
            starts_at=datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC),
            ends_at=datetime(2026, 11, 20, 11, 30, 0, tzinfo=UTC),
            capacity=10,
            reserved_count=1,
            is_active=True,
            created_at=datetime.now(UTC),
        )
        unrelated_booking = ActivityBooking(
            id=9999,
            activity_slot_id=unrelated_slot.id,
            eco_experience_id=None,  # Module 03 Activity booking
            user_id=traveler.id,     # Same traveler!
            participants=1,
            total_price=Decimal("4000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
            created_at=datetime(2026, 11, 19, 11, 0, 0, tzinfo=UTC),
            updated_at=datetime(2026, 11, 19, 11, 0, 0, tzinfo=UTC),
        )
        unrelated_ref = BookingReference(
            id=911,
            booking_reference="ACT-BOOKING-REF-9999",
            module_type="activity",
            target_booking_id=9999,
            user_id=traveler.id,
            provider_profile_id=base_act.provider_profile_id,
            total_price=Decimal("4000.00"),
            currency="LKR",
            booking_status="confirmed",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add_all([eco_booking, eco_ref, unrelated_slot, unrelated_booking, unrelated_ref])
        db_session.commit()

        # Check traveler history: only eco_booking is returned, unrelated_booking (id 9999) is excluded
        traveler_app = create_app_with_auth(db_session, traveler)
        t_client = TestClient(traveler_app)
        t_res = t_client.get("/api/v1/eco-bookings/my-bookings")
        assert t_res.status_code == status.HTTP_200_OK
        t_ids = [b["id"] for b in t_res.json()]
        assert 1140 in t_ids
        assert 9999 not in t_ids
        eco_item = [b for b in t_res.json() if b["id"] == 1140][0]
        assert eco_item["booking_reference"] == "ECO-ACT-REF-1140"

        # Check provider listing: unrelated_booking is excluded
        provider_app = create_app_with_auth(db_session, provider_user)
        p_client = TestClient(provider_app)
        p_res = p_client.get("/api/v1/provider/eco-bookings")
        assert p_res.status_code == status.HTTP_200_OK
        p_ids = [b["id"] for b in p_res.json()["items"]]
        assert 1140 in p_ids
        assert 9999 not in p_ids

        # Check provider detail for unrelated booking: 404 Not Found (strictly isolated)
        p_detail = p_client.get("/api/v1/provider/eco-bookings/9999")
        assert p_detail.status_code == status.HTTP_404_NOT_FOUND


    def test_returns_existing_participants_with_actual_emergency_and_waiver_values(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=114,
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

        # Seed 2 participants into physical activity_booking_participants table
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        with test_engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO activity_booking_participants "
                    "(id, activity_booking_id, full_name, age, emergency_contact_name, emergency_contact_phone, special_requirements, waiver_signed, created_at) "
                    "VALUES (:id, :b_id, :name, :age, :em_name, :em_phone, :reqs, :waiver, :created)"
                ),
                [
                    {
                        "id": 901,
                        "b_id": booking.id,
                        "name": "Sarah Connor",
                        "age": 35,
                        "em_name": "John Connor",
                        "em_phone": "+94770011223",
                        "reqs": "Vegetarian dietary plan",
                        "waiver": True,
                        "created": now_str,
                    },
                    {
                        "id": 902,
                        "b_id": booking.id,
                        "name": "Kyle Reese",
                        "age": 28,
                        "em_name": None,
                        "em_phone": None,
                        "reqs": None,
                        "waiver": False,
                        "created": now_str,
                    },
                ],
            )
            conn.commit()

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        item = data[0]
        participants = item["participants_list"]
        assert len(participants) == 2

        # Participant 1: actual physical values preserved
        p1 = participants[0]
        assert p1["id"] == 901
        assert p1["full_name"] == "Sarah Connor"
        assert p1["age"] == 35
        assert p1["emergency_contact_name"] == "John Connor"
        assert p1["emergency_contact_phone"] == "+94770011223"
        assert p1["special_requirements"] == "Vegetarian dietary plan"
        assert p1["waiver_signed"] is True

        # Participant 2: actual physical null / false values preserved
        p2 = participants[1]
        assert p2["id"] == 902
        assert p2["full_name"] == "Kyle Reese"
        assert p2["age"] == 28
        assert p2["emergency_contact_name"] is None
        assert p2["emergency_contact_phone"] is None
        assert p2["special_requirements"] is None
        assert p2["waiver_signed"] is False

    def test_returns_empty_participant_list_when_no_participants_exist(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=115,
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

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/eco-bookings/my-bookings")
        assert res.status_code == status.HTTP_200_OK

        data = res.json()
        assert len(data) == 1
        assert data[0]["participants_list"] == []


# ==============================================================================
# 3. Traveler Booking Cancellation (POST /api/v1/eco-bookings/{id}/cancel)
# ==============================================================================


class TestTravelerEcoBookingCancellation:
    def test_rejects_non_traveler_role(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"reason": "Changing travel plans."}
        res = client.post("/api/v1/eco-bookings/101/cancel", json=payload)
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_rejects_non_existent_booking(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"reason": "Valid reason for non-existent booking."}
        res = client.post("/api/v1/eco-bookings/999999/cancel", json=payload)
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_rejects_non_owner_traveler(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        other_traveler = seeded_env["other_traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        # Booking owned by other_traveler
        booking = ActivityBooking(
            id=202,
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

        # Traveler 1 attempts to cancel Traveler 2's booking
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"reason": "Attempting to cancel someone else's trip."}
        res = client.post(f"/api/v1/eco-bookings/{booking.id}/cancel", json=payload)
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_rejects_already_cancelled_booking(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=203,
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

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"reason": "Attempting to cancel an already cancelled booking."}
        res = client.post(f"/api/v1/eco-bookings/{booking.id}/cancel", json=payload)
        assert res.status_code == status.HTTP_409_CONFLICT
        assert "already cancelled" in res.json()["detail"].lower()

    def test_staged_cancellation_guard_returns_503_and_zero_writes(
        self, db_session, seeded_env
    ):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=204,
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

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"reason": "Family emergency, unable to attend trek."}
        res = client.post(f"/api/v1/eco-bookings/{booking.id}/cancel", json=payload)

        # STAGED ACTIVATION GUARD: HTTP 503
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = res.json()["detail"]
        assert "cancellation validation succeeded" in detail.lower()
        assert "staged and disabled" in detail.lower()
        assert "Zero database writes were executed" in detail

        # Assert status in database remains untouched (ZERO WRITES)
        refetched = db_session.query(ActivityBooking).filter(ActivityBooking.id == 204).one()
        assert refetched.booking_status == "pending"


# ==============================================================================
# 4. Provider Booking Listing (Phase 4B)
# ==============================================================================


class TestProviderEcoBookingListing:
    def test_provider_listing_rejects_traveler_role(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_provider_listing_rejects_unapproved_provider(self, db_session, seeded_env):
        unapproved = seeded_env["unapproved_provider_user"]
        app = create_app_with_auth(db_session, unapproved)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "pending approval" in res.json()["detail"].lower()

    def test_provider_listing_empty(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1
        assert data["page_size"] == 10

    def test_provider_listing_returns_owned_bookings_with_persisted_reference_and_participants(
        self, db_session, seeded_env
    ):
        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=301,
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
        db_session.flush()

        ref = BookingReference(
            booking_reference="ECO-20261119-0301",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add(ref)

        db_session.execute(
            text(
                "INSERT INTO activity_booking_participants "
                "(id, activity_booking_id, full_name, age, emergency_contact_name, emergency_contact_phone, special_requirements, waiver_signed, created_at) "
                "VALUES (301, 301, 'Jane Trekker', 28, 'David Trekker', '+94771122334', 'Vegan', 1, '2026-11-19 10:00:00')"
            )
        )
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["id"] == 301
        assert item["booking_reference"] == "ECO-20261119-0301"
        assert item["eco_experience_title"] == exp.title
        assert item["traveler_name"] == traveler.full_name
        assert item["traveler_email"] == traveler.email
        assert item["payment_method"] == "pay_at_location"
        assert item["payment_status"] == "unpaid"
        assert item["booking_status"] == "pending"
        assert len(item["participants_list"]) == 1
        p = item["participants_list"][0]
        assert p["full_name"] == "Jane Trekker"
        assert p["emergency_contact_name"] == "David Trekker"
        assert p["waiver_signed"] is True

    def test_provider_listing_cross_provider_isolation(self, db_session, seeded_env):
        provider_user_1 = seeded_env["provider_user"]
        exp_1 = seeded_env["experience"]
        slot_1 = seeded_env["slot"]

        second_provider_user = seeded_env["second_provider_user"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=302,
            activity_slot_id=slot_1.id,
            eco_experience_id=exp_1.id,
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

        # Provider 1 sees booking
        app_1 = create_app_with_auth(db_session, provider_user_1)
        client_1 = TestClient(app_1)
        res_1 = client_1.get("/api/v1/provider/eco-bookings")
        assert res_1.status_code == status.HTTP_200_OK
        assert res_1.json()["total"] == 1

        # Second provider sees ZERO bookings (strict isolation)
        app_2 = create_app_with_auth(db_session, second_provider_user)
        client_2 = TestClient(app_2)
        res_2 = client_2.get("/api/v1/provider/eco-bookings")
        assert res_2.status_code == status.HTTP_200_OK
        assert res_2.json()["total"] == 0
        assert res_2.json()["items"] == []

    def test_provider_listing_filter_by_status(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        b_pending = ActivityBooking(
            id=303,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
            created_at=datetime.now(UTC) - timedelta(hours=2),
            updated_at=datetime.now(UTC) - timedelta(hours=2),
        )
        b_confirmed = ActivityBooking(
            id=304,
            activity_slot_id=slot.id,
            eco_experience_id=exp.id,
            user_id=traveler.id,
            participants=2,
            total_price=Decimal("9000.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
            created_at=datetime.now(UTC) - timedelta(hours=1),
            updated_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add_all([b_pending, b_confirmed])
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res_pend = client.get("/api/v1/provider/eco-bookings?status=pending")
        assert res_pend.status_code == status.HTTP_200_OK
        data_pend = res_pend.json()
        assert data_pend["total"] == 1
        assert data_pend["items"][0]["id"] == 303

        res_conf = client.get("/api/v1/provider/eco-bookings?status=confirmed")
        assert res_conf.status_code == status.HTTP_200_OK
        data_conf = res_conf.json()
        assert data_conf["total"] == 1
        assert data_conf["items"][0]["id"] == 304

    def test_provider_listing_filter_by_status_invalid(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings?status=INVALID_STATUS")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "invalid booking status" in res.json()["detail"].lower()

    def test_provider_listing_filter_by_eco_experience_id_success(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        b = ActivityBooking(
            id=305,
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
        db_session.add(b)
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get(f"/api/v1/provider/eco-bookings?eco_experience_id={exp.id}")
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["total"] == 1

    def test_provider_listing_filter_by_eco_experience_id_unauthorized(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        second_exp = seeded_env["second_experience"]

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        # Provider 1 attempts to query bookings for Provider 2's experience
        res = client.get(f"/api/v1/provider/eco-bookings?eco_experience_id={second_exp.id}")
        assert res.status_code == status.HTTP_403_FORBIDDEN
        assert "permission" in res.json()["detail"].lower()

    def test_provider_listing_filter_by_eco_experience_id_not_found(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings?eco_experience_id=99999")
        assert res.status_code == status.HTTP_404_NOT_FOUND

    def test_provider_listing_pagination(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        for i in range(1, 4):
            b = ActivityBooking(
                id=310 + i,
                activity_slot_id=slot.id,
                eco_experience_id=exp.id,
                user_id=traveler.id,
                participants=2,
                total_price=Decimal("9000.00"),
                currency="LKR",
                payment_method="pay_at_location",
                payment_status="unpaid",
                booking_status="pending",
                created_at=datetime.now(UTC) - timedelta(minutes=10 * i),
                updated_at=datetime.now(UTC) - timedelta(minutes=10 * i),
            )
            db_session.add(b)
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings?page=1&page_size=2")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["total"] == 3
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["page_size"] == 2


# ==============================================================================
# 5. Experience-Specific Booking Listing (Phase 4B)
# ==============================================================================


class TestExperienceSpecificEcoBookingListing:
    def test_experience_bookings_owned_success(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        b = ActivityBooking(
            id=320,
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
        db_session.add(b)
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get(f"/api/v1/provider/eco-experiences/{exp.id}/bookings")
        assert res.status_code == status.HTTP_200_OK
        assert res.json()["total"] == 1
        assert res.json()["items"][0]["id"] == 320

    def test_experience_bookings_unowned_forbidden(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        second_exp = seeded_env["second_experience"]

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get(f"/api/v1/provider/eco-experiences/{second_exp.id}/bookings")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_experience_bookings_not_found(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-experiences/99999/bookings")
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================
# 6. Provider Single Booking Detail (Phase 4B)
# ==============================================================================


class TestProviderEcoBookingDetail:
    def test_get_booking_detail_success(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=330,
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
        db_session.flush()

        ref = BookingReference(
            booking_reference="ECO-20261119-0330",
            module_type="eco_tourism",
            target_booking_id=booking.id,
            user_id=traveler.id,
            provider_profile_id=provider_profile.id,
            total_price=Decimal("9000.00"),
            currency="LKR",
            booking_status="pending",
            payment_status="unpaid",
            created_at=datetime.now(UTC),
        )
        db_session.add(ref)

        db_session.execute(
            text(
                "INSERT INTO activity_booking_participants "
                "(id, activity_booking_id, full_name, age, emergency_contact_name, emergency_contact_phone, special_requirements, waiver_signed, created_at) "
                "VALUES (330, 330, 'Sam Nature', 34, 'Mary Nature', '+94773344556', 'None', 1, '2026-11-19 10:00:00')"
            )
        )
        db_session.commit()

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get(f"/api/v1/provider/eco-bookings/{booking.id}")
        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert data["id"] == 330
        assert data["booking_reference"] == "ECO-20261119-0330"
        assert data["eco_experience_title"] == exp.title
        assert data["traveler_name"] == traveler.full_name
        assert len(data["participants_list"]) == 1
        assert data["participants_list"][0]["emergency_contact_name"] == "Mary Nature"

    def test_get_booking_detail_unowned_forbidden(self, db_session, seeded_env):
        second_provider_user = seeded_env["second_provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=331,
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

        # Second provider queries booking belonging to Provider 1's experience
        app = create_app_with_auth(db_session, second_provider_user)
        client = TestClient(app)

        res = client.get(f"/api/v1/provider/eco-bookings/{booking.id}")
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_get_booking_detail_not_found(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        res = client.get("/api/v1/provider/eco-bookings/99999")
        assert res.status_code == status.HTTP_404_NOT_FOUND


# ==============================================================================
# 7. Provider Booking Decision & Staged Activation Guard (Phase 4B)
# ==============================================================================


class TestProviderEcoBookingDecision:
    def test_decision_rejects_traveler_role(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"action": "accept"}
        res = client.post("/api/v1/provider/eco-bookings/100/decision", json=payload)
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_decision_rejects_unowned_booking(self, db_session, seeded_env):
        second_provider_user = seeded_env["second_provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=340,
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

        app = create_app_with_auth(db_session, second_provider_user)
        client = TestClient(app)

        payload = {"action": "accept"}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)
        assert res.status_code == status.HTTP_403_FORBIDDEN

    def test_decision_accept_staged_guard_503_and_zero_writes(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=341,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "accept", "note": "Confirmed your trek."}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)

        # STAGED ACTIVATION GUARD: HTTP 503
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = res.json()["detail"]
        assert "validated successfully in read-only mode" in detail
        assert "target_booking_status='confirmed'" in detail
        assert "target_payment_status='unpaid'" in detail
        assert "Zero database writes were executed" in detail

        # Verify DB status is UNCHANGED (Zero writes)
        refetched = db_session.query(ActivityBooking).filter_by(id=booking.id).first()
        assert refetched.booking_status == "pending"

    def test_decision_accept_rejects_non_pending(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=342,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "accept"}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "only 'pending' bookings can be accepted" in res.json()["detail"].lower()

    def test_decision_reject_staged_guard_503_and_calculates_capacity_release(
        self, db_session, seeded_env
    ):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=343,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {
            "action": "reject",
            "note": "Severe rain alert issued by forestry department.",
        }
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)

        # STAGED ACTIVATION GUARD: HTTP 503
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = res.json()["detail"]
        assert "target_booking_status='rejected'" in detail
        assert "target_payment_status='unpaid'" in detail
        assert "target_payment_status='cancelled'" not in detail
        assert "release_capacity=4" in detail
        assert "Zero database writes were executed" in detail

        # Verify DB status is UNCHANGED (Zero writes)
        refetched = db_session.query(ActivityBooking).filter_by(id=booking.id).first()
        assert refetched.booking_status == "pending"

    def test_decision_reject_requires_valid_note(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=344,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        # Missing note
        payload_missing = {"action": "reject"}
        res_missing = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload_missing)
        assert res_missing.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

        # Too short note (< 5 characters)
        payload_short = {"action": "reject", "note": "bad"}
        res_short = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload_short)
        assert res_short.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    def test_decision_confirm_cash_staged_guard_503_and_zero_writes(
        self, db_session, seeded_env
    ):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=345,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "confirm_cash", "note": "Received 9000 LKR cash at trail entrance."}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)

        # STAGED ACTIVATION GUARD: HTTP 503
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        detail = res.json()["detail"]
        assert "target_booking_status='confirmed'" in detail  # Separate from completed!
        assert "target_payment_status='paid'" in detail
        assert "Zero database writes were executed" in detail

        # Verify DB status is UNCHANGED (Zero writes)
        refetched = db_session.query(ActivityBooking).filter_by(id=booking.id).first()
        assert refetched.payment_status == "unpaid"

    def test_decision_confirm_cash_rejects_online_method(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=346,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "confirm_cash"}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "only 'pay_at_location' bookings accept cash confirmation" in res.json()["detail"].lower()

    def test_decision_confirm_cash_rejects_already_paid(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=347,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "confirm_cash"}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "already been recorded as paid" in res.json()["detail"].lower()

    def test_decision_confirm_cash_rejects_pending_status(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=348,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        payload = {"action": "confirm_cash"}
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json=payload)
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "booking must be confirmed first" in res.json()["detail"].lower()


# ==============================================================================
# 7. Phase 4D: Activation Guards & Transaction Ownership Tests
# ==============================================================================


class TestPhase4DActivationGuardsAndTransactionOwnership:
    def test_public_creation_endpoint_strictly_503_and_zero_writes(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        initial_booking_count = db_session.query(ActivityBooking).count()
        payload = {
            "eco_slot_id": slot.id,
            "participants": 2,
            "payment_method": "pay_at_location",
            "participants_roster": [
                {"full_name": "Alice Tester", "age": 28, "emergency_contact_name": "Bob", "emergency_contact_phone": "0771234567"},
                {"full_name": "Bob Tester", "age": 30, "emergency_contact_name": "Alice", "emergency_contact_phone": "0771234568"},
            ],
        }
        res = client.post("/api/v1/eco-bookings", json=payload)
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Zero database writes were executed" in res.json()["detail"]
        assert db_session.query(ActivityBooking).count() == initial_booking_count

    def test_public_cancellation_endpoint_strictly_503_and_zero_writes(self, db_session, seeded_env):
        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        booking = ActivityBooking(
            id=401,
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

        app = create_app_with_auth(db_session, traveler)
        client = TestClient(app)

        payload = {"reason": "Plans changed unexpectedly."}
        res = client.post(f"/api/v1/eco-bookings/{booking.id}/cancel", json=payload)
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Zero database writes were executed" in res.json()["detail"]

        # Status in DB remains pending (ZERO WRITES)
        db_session.refresh(booking)
        assert booking.booking_status == "pending"

    def test_public_provider_decision_strictly_503_and_zero_writes(self, db_session, seeded_env):
        provider_user = seeded_env["provider_user"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=402,
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

        app = create_app_with_auth(db_session, provider_user)
        client = TestClient(app)

        # 1. Accept decision -> 503
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json={"action": "accept"})
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Zero database writes were executed" in res.json()["detail"]
        db_session.refresh(booking)
        assert booking.booking_status == "pending"

        # 2. Reject decision -> 503
        res = client.post(f"/api/v1/provider/eco-bookings/{booking.id}/decision", json={"action": "reject", "note": "Severe weather."})
        assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "Zero database writes were executed" in res.json()["detail"]
        db_session.refresh(booking)
        assert booking.booking_status == "pending"

    def test_router_booking_create_commits_and_rollback_on_commit_failure(self, db_session, seeded_env, monkeypatch):
        from app.routers.eco_bookings import _execute_booking_create_with_transaction
        from app.services.eco_booking_service import validate_booking_request_read_only
        from app.schemas.eco_booking import EcoBookingCreate, EcoParticipantCreate

        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        payload = EcoBookingCreate(
            eco_slot_id=slot.id,
            participants=2,
            payment_method="pay_at_location",
            participants_roster=[
                EcoParticipantCreate(full_name="Alice A", age=25),
                EcoParticipantCreate(full_name="Bob B", age=30),
            ],
        )
        plan = validate_booking_request_read_only(db_session, traveler, payload, datetime.now(UTC))

        # 1. Successful commit
        res = _execute_booking_create_with_transaction(db_session, plan)
        assert res.booking_status == "pending"
        persisted = db_session.query(ActivityBooking).filter_by(id=res.id).first()
        assert persisted is not None

        # 2. Failure during commit -> calls db.rollback() and raises 500
        plan2 = validate_booking_request_read_only(db_session, traveler, payload, datetime.now(UTC))
        def failing_commit():
            raise Exception("Simulated connection timeout during commit")
        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(HTTPException) as exc_info:
            _execute_booking_create_with_transaction(db_session, plan2)
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to complete booking creation" in exc_info.value.detail
        assert "Simulated connection timeout" not in exc_info.value.detail

    def test_router_cancellation_commits_and_rollback_on_commit_failure(self, db_session, seeded_env, monkeypatch):
        from app.routers.eco_bookings import _execute_booking_cancel_with_transaction
        from app.services.eco_booking_service import validate_booking_cancellation_read_only

        traveler = seeded_env["traveler"]
        slot = seeded_env["slot"]
        exp = seeded_env["experience"]

        slot.reserved_count = 4
        booking = ActivityBooking(
            id=405,
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

        cancel_plan = validate_booking_cancellation_read_only(db_session, traveler, booking.id, "Change of plans")

        # Failure during commit -> calls db.rollback() and raises 500
        def failing_commit():
            raise Exception("Simulated commit deadlock")
        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(HTTPException) as exc_info:
            _execute_booking_cancel_with_transaction(db_session, cancel_plan)
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to complete booking cancellation" in exc_info.value.detail
        assert "Simulated commit deadlock" not in exc_info.value.detail

    def test_router_provider_decision_commits_and_rollback_on_commit_failure(self, db_session, seeded_env, monkeypatch):
        from app.routers.eco_bookings import _execute_provider_decision_with_transaction
        from app.services.eco_booking_service import validate_provider_decision_read_only
        from app.schemas.eco_booking import EcoBookingProviderDecision

        provider_user = seeded_env["provider_user"]
        provider_profile = seeded_env["provider_profile"]
        exp = seeded_env["experience"]
        slot = seeded_env["slot"]
        traveler = seeded_env["traveler"]

        booking = ActivityBooking(
            id=406,
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
        booking2 = ActivityBooking(
            id=407,
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
        db_session.add_all([booking, booking2])
        db_session.commit()

        decision = EcoBookingProviderDecision(action="confirm_cash")
        plan = validate_provider_decision_read_only(db_session, provider_profile, booking.id, decision)

        # 1. Successful commit
        res = _execute_provider_decision_with_transaction(db_session, plan, provider_profile.id)
        assert res.payment_status == "paid"
        assert res.booking_status == "confirmed"

        # 2. Failure during commit on booking2
        plan2 = validate_provider_decision_read_only(db_session, provider_profile, booking2.id, decision)
        def failing_commit():
            raise Exception("Simulated commit error")
        monkeypatch.setattr(db_session, "commit", failing_commit)

        with pytest.raises(HTTPException) as exc_info:
            _execute_provider_decision_with_transaction(db_session, plan2, provider_profile.id)
        assert exc_info.value.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "Failed to process provider booking decision" in exc_info.value.detail
        assert "Simulated commit error" not in exc_info.value.detail

