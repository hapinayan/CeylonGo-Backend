"""
Automated API and ORM tests for Module 03 — Phase 5D-2A:
Admin Platform-Wide Activity Reporting (Canonical Requirement C07).

Covers:
- Admin RBAC authorization (require_admin):
  - Admin access returns 200 OK with full platform analytics
  - Unauthenticated access returns 401 Unauthorized
  - Traveler and Provider roles return 403 Forbidden
- Platform Aggregations & Inventory:
  - Total activities, breakdown by approval_status (approved, pending, draft, suspended, rejected)
  - Active published vs inactive activities
  - Canonical category distribution (cultural, adventure, local, community)
  - Regional distribution by district
- Booking Lifecycle & Performance:
  - Total bookings, status counts (confirmed, completed, pending, cancelled, rejected)
  - Total participants and confirmed participants
  - Booking conversion rate and platform cancellation rate arithmetic
- Capacity Utilization:
  - Total scheduled slots, active slots, total capacity, reserved capacity, occupancy rate
- Multi-Currency Financial Integrity:
  - Strict currency partitioning (LKR, USD) preventing cross-currency mixing
  - Gross booking volume, paid revenue (cash/online), unpaid holding revenue, refunded amounts
  - Cancelled and rejected bookings excluded from gross/paid totals
- Administrative Ranking:
  - Top activities and top providers by booking volume and gross revenue
- Date Range Filtering & Validation:
  - Bounded date filtering on booking creation and slot schedules
  - Support for date_from/date_to and start_date/end_date aliases
  - Rejection of invalid date range (date_from > date_to -> 422)
  - Rejection of excessive date window (> 5 years -> 422)
- Privacy & Cross-Module Isolation:
  - Zero traveler PII (names, emails, phones, medical/emergency notes) exposed
  - Strictly excludes Eco-Tourism legacy slots/bookings (eco_experience_id IS NOT NULL)
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    get_current_user,
    require_admin,
    require_approved_provider,
    require_traveler,
)
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import (
    ActivityBooking,
    ActivityBookingParticipant,
)
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_bookings import router as activity_bookings_router
from app.routers.activity_slots import router as activity_slots_router
from app.routers.admin_activities import router as admin_activities_router
from app.routers.provider_activities import (
    require_activity_provider,
    router as provider_activities_router,
)


test_app = FastAPI(title="CeylonGo Module 03 Reporting Test App")
test_app.include_router(activities_public_router)
test_app.include_router(provider_activities_router)
test_app.include_router(admin_activities_router)
test_app.include_router(activity_slots_router)
test_app.include_router(activity_bookings_router)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
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
    autocommit=False,
    autoflush=False,
    bind=engine,
)


@pytest.fixture(autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    test_app.dependency_overrides.clear()


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded_users(db_session: Session):
    admin_user = User(
        id=1,
        email="admin@ceylongo.test",
        password_hash="fakehash",
        full_name="Admin Chief",
        role="admin",
        is_active=True,
    )
    prov1_user = User(
        id=2,
        email="provider1@ceylongo.test",
        password_hash="fakehash",
        full_name="Silva Surfing Provider",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Perera Ella Treks Provider",
        role="provider",
        is_active=True,
    )
    traveler_user = User(
        id=4,
        email="traveler@ceylongo.test",
        password_hash="fakehash",
        full_name="Jane Doe Traveler",
        role="traveler",
        is_active=True,
    )

    db_session.add_all([admin_user, prov1_user, prov2_user, traveler_user])
    db_session.commit()

    p1_profile = ProviderProfile(
        id=1,
        user_id=2,
        business_name="Silva Surf Academy",
        provider_type="activity",
        phone="+94771112233",
        district="Matara",
        approval_status="approved",
    )
    p2_profile = ProviderProfile(
        id=2,
        user_id=3,
        business_name="Perera Trekking Tours",
        provider_type="activity",
        phone="+94772223344",
        district="Badulla",
        approval_status="approved",
    )
    db_session.add_all([p1_profile, p2_profile])
    db_session.commit()

    return {
        "admin": admin_user,
        "prov1": prov1_user,
        "prov2": prov2_user,
        "traveler": traveler_user,
        "p1_profile": p1_profile,
        "p2_profile": p2_profile,
    }


@pytest.fixture
def seeded_activities(db_session: Session, seeded_users):
    """Seed activities across canonical categories and approval statuses."""
    act1 = Activity(
        id=10,
        provider_profile_id=1,
        title="Weligama Beginner Surf Camp",
        description="Learn surfing in Weligama Bay.",
        category="adventure",
        district="Matara",
        city="Weligama",
        meeting_point="Weligama Beach Main Post",
        duration_minutes=120,
        minimum_age=10,
        pricing_type="per_person",
        price=Decimal("4000.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )
    act2 = Activity(
        id=20,
        provider_profile_id=2,
        title="Ella Rock Sunrise Hike",
        description="Guided hiking trek to Ella Rock.",
        category="adventure",
        district="Badulla",
        city="Ella",
        meeting_point="Ella Station Post",
        duration_minutes=240,
        minimum_age=12,
        pricing_type="per_person",
        price=Decimal("5000.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )
    act3 = Activity(
        id=30,
        provider_profile_id=1,
        title="Kandy Cultural Drumming Workshop",
        description="Traditional drum beats of central Sri Lanka.",
        category="cultural",
        district="Kandy",
        city="Kandy",
        meeting_point="Temple of Tooth West Gate",
        duration_minutes=90,
        minimum_age=8,
        pricing_type="per_person",
        price=Decimal("30.00"),
        currency="USD",
        approval_status="approved",
        is_active=True,
    )
    act4 = Activity(
        id=40,
        provider_profile_id=2,
        title="Unawatuna Cooking Masterclass",
        description="Authentic spicy curry preparation.",
        category="local",
        district="Galle",
        city="Unawatuna",
        meeting_point="Unawatuna Main Junction",
        duration_minutes=150,
        minimum_age=14,
        pricing_type="per_person",
        price=Decimal("3500.00"),
        currency="LKR",
        approval_status="pending",
        is_active=False,
    )
    act5 = Activity(
        id=50,
        provider_profile_id=1,
        title="Nuwara Eliya Tea Plucking Experience",
        description="Join community tea pluckers in the misty hills.",
        category="community",
        district="Nuwara Eliya",
        city="Nuwara Eliya",
        meeting_point="Pedro Tea Estate Office",
        duration_minutes=180,
        minimum_age=6,
        pricing_type="per_person",
        price=Decimal("2500.00"),
        currency="LKR",
        approval_status="draft",
        is_active=False,
    )
    act6 = Activity(
        id=60,
        provider_profile_id=2,
        title="Mirissa Night Fishing Expedition",
        description="Deep sea night fishing with local fishermen.",
        category="adventure",
        district="Matara",
        city="Mirissa",
        meeting_point="Mirissa Harbor Pier 3",
        duration_minutes=240,
        minimum_age=18,
        pricing_type="per_person",
        price=Decimal("8000.00"),
        currency="LKR",
        approval_status="suspended",
        is_active=False,
    )

    db_session.add_all([act1, act2, act3, act4, act5, act6])
    db_session.commit()

    return {
        "act1": act1,
        "act2": act2,
        "act3": act3,
        "act4": act4,
        "act5": act5,
        "act6": act6,
    }


@pytest.fixture
def seeded_slots_and_bookings(db_session: Session, seeded_users, seeded_activities):
    """Seed scheduled slots and bookings across statuses, dates, and currencies."""
    now = datetime.now(UTC).replace(tzinfo=None)

    # Seed parent eco experience for legacy eco slot reference
    eco_exp = EcoExperience(
        id=999,
        provider_profile_id=1,
        title="Sinharaja Rainforest Trek",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("4.00"),
        district="Ratnapura",
        city="Sinharaja",
        meeting_point="Sinharaja Main Gate",
        pricing_type="per_person",
        base_price=Decimal("5000.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=1,
        environmental_guidelines="Strict leave no trace guidelines enforced.",
        wildlife_safety_summary="Observe wildlife from a safe 10-meter distance.",
        listing_status="approved",
    )
    db_session.add(eco_exp)
    db_session.commit()

    # Slots
    s1 = ActivitySlot(
        id=101,
        activity_id=10,  # Surf Camp (LKR)
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=2, hours=2),
        capacity=10,
        reserved_count=5,
        is_active=True,
    )
    s2 = ActivitySlot(
        id=102,
        activity_id=20,  # Ella Hike (LKR)
        starts_at=now + timedelta(days=3),
        ends_at=now + timedelta(days=3, hours=4),
        capacity=8,
        reserved_count=4,
        is_active=True,
    )
    s3 = ActivitySlot(
        id=103,
        activity_id=30,  # Kandy Drumming (USD)
        starts_at=now + timedelta(days=4),
        ends_at=now + timedelta(days=4, hours=2),
        capacity=6,
        reserved_count=2,
        is_active=True,
    )
    s4_eco = ActivitySlot(
        id=104,
        activity_id=10,
        starts_at=now + timedelta(days=5),
        ends_at=now + timedelta(days=5, hours=2),
        capacity=10,
        reserved_count=3,
        is_active=True,
        eco_experience_id=999,  # Legacy eco slot (MUST be excluded)
    )
    db_session.add_all([s1, s2, s3, s4_eco])
    db_session.commit()

    # Bookings
    # 1. Confirmed cash booking for Surf Camp (LKR)
    b1 = ActivityBooking(
        id=501,
        activity_slot_id=101,
        user_id=4,
        participants=3,
        total_price=Decimal("12000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="unpaid",
        booking_status="confirmed",
        created_at=now - timedelta(days=1),
    )
    # 2. Completed paid cash booking for Surf Camp (LKR)
    b2 = ActivityBooking(
        id=502,
        activity_slot_id=101,
        user_id=4,
        participants=2,
        total_price=Decimal("8000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="completed",
        created_at=now - timedelta(days=2),
    )
    # 3. Confirmed booking for Ella Hike (LKR)
    b3 = ActivityBooking(
        id=503,
        activity_slot_id=102,
        user_id=4,
        participants=4,
        total_price=Decimal("20000.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="confirmed",
        created_at=now - timedelta(days=3),
    )
    # 4. Cancelled booking with refund for Kandy Drumming (USD)
    b4 = ActivityBooking(
        id=504,
        activity_slot_id=103,
        user_id=4,
        participants=2,
        total_price=Decimal("60.00"),
        currency="USD",
        payment_method="online",
        payment_status="refunded",
        booking_status="cancelled",
        cancellation_reason="Traveler flight delayed",
        cancelled_at=now - timedelta(hours=6),
        created_at=now - timedelta(days=4),
    )
    # 5. Legacy eco-booking (MUST be excluded from activity platform report)
    b5_eco = ActivityBooking(
        id=505,
        activity_slot_id=104,
        user_id=4,
        participants=3,
        total_price=Decimal("9999.00"),
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        booking_status="confirmed",
        created_at=now - timedelta(days=1),
        eco_experience_id=999,
    )
    db_session.add_all([b1, b2, b3, b4, b5_eco])
    db_session.commit()

    return {
        "s1": s1,
        "s2": s2,
        "s3": s3,
        "b1": b1,
        "b2": b2,
        "b3": b3,
        "b4": b4,
    }


def set_auth(user: User | None):
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: TestingSessionLocal()

    if user is not None:
        test_app.dependency_overrides[get_current_user] = lambda: user
        if user.role == "admin":
            test_app.dependency_overrides[require_admin] = lambda: user
        elif user.role == "provider":
            test_app.dependency_overrides[require_approved_provider] = lambda: user
        elif user.role == "traveler":
            test_app.dependency_overrides[require_traveler] = lambda: user


# ==============================================================================
# Security & RBAC Tests
# ==============================================================================


def test_unauthenticated_reports_access_rejected():
    """Unauthenticated access to platform reports is rejected with 401."""
    set_auth(None)
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 401


def test_traveler_reports_access_rejected(seeded_users):
    """Travelers cannot access platform administrative reports (403)."""
    set_auth(seeded_users["traveler"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 403


def test_provider_reports_access_rejected(seeded_users):
    """Providers cannot access platform administrative reports (403)."""
    set_auth(seeded_users["prov1"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 403


def test_admin_reports_access_granted(seeded_users):
    """Authorized admins can access platform administrative reports (200 OK)."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    data = response.json()
    assert "activity_inventory" in data
    assert "booking_overview" in data
    assert "capacity_utilization" in data
    assert "category_distribution" in data
    assert "regional_distribution" in data
    assert "financial_overview" in data
    assert "top_activities" in data
    assert "top_providers" in data


# ==============================================================================
# Empty State Tests
# ==============================================================================


def test_empty_reporting_state(seeded_users):
    """Empty database returns valid zeroed reporting response without 500 errors."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    data = response.json()

    inv = data["activity_inventory"]
    assert inv["total_activities"] == 0
    assert inv["approved_activities"] == 0
    assert inv["active_published_activities"] == 0

    book = data["booking_overview"]
    assert book["total_bookings"] == 0
    assert book["booking_conversion_rate"] == 0.0
    assert book["cancellation_rate"] == 0.0

    cap = data["capacity_utilization"]
    assert cap["total_slots"] == 0
    assert cap["total_capacity"] == 0
    assert cap["occupancy_rate"] == 0.0

    assert len(data["category_distribution"]) == 4  # All 4 canonical categories represented
    assert data["regional_distribution"] == []
    assert len(data["financial_overview"]) == 1
    assert data["financial_overview"][0]["total_gross_revenue"] == "0.00"


# ==============================================================================
# Aggregation & Metric Accuracy Tests
# ==============================================================================


def test_activity_inventory_aggregates(seeded_users, seeded_activities):
    """Verifies activity inventory breakdown across approval statuses and active flag."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    inv = response.json()["activity_inventory"]

    assert inv["total_activities"] == 6
    assert inv["approved_activities"] == 3  # act1, act2, act3
    assert inv["pending_activities"] == 1   # act4
    assert inv["draft_activities"] == 1     # act5
    assert inv["suspended_activities"] == 1 # act6
    assert inv["rejected_activities"] == 0
    assert inv["active_published_activities"] == 3
    assert inv["inactive_activities"] == 3


def test_booking_overview_and_conversion_rates(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies booking status counts, participants, conversion rate, and cancellation rate."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    book = response.json()["booking_overview"]

    # 4 activity bookings seeded (b1, b2, b3, b4); b5_eco excluded
    assert book["total_bookings"] == 4
    assert book["confirmed_bookings"] == 2  # b1, b3
    assert book["completed_bookings"] == 1  # b2
    assert book["cancelled_bookings"] == 1  # b4
    assert book["pending_bookings"] == 0
    assert book["rejected_bookings"] == 0

    assert book["total_participants"] == 11  # 3 + 2 + 4 + 2
    assert book["confirmed_participants"] == 9  # 3 + 2 + 4 (b1, b2, b3)

    # Conversion rate: (confirmed 2 + completed 1) / total 4 = 75.0%
    assert book["booking_conversion_rate"] == 75.0

    # Cancellation rate: cancelled 1 / total 4 = 25.0%
    assert book["cancellation_rate"] == 25.0


def test_capacity_utilization_metrics(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies scheduled slot capacity, reserved count, remaining, and occupancy rate."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    cap = response.json()["capacity_utilization"]

    # 3 active activity slots (s1=10, s2=8, s3=6); s4_eco excluded
    assert cap["total_slots"] == 3
    assert cap["active_slots"] == 3
    assert cap["total_capacity"] == 24       # 10 + 8 + 6
    assert cap["total_reserved"] == 11       # 5 + 4 + 2
    assert cap["total_remaining"] == 13      # 24 - 11

    # Occupancy rate: 11 / 24 * 100 = 45.83%
    assert cap["occupancy_rate"] == 45.83


def test_canonical_category_distribution(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies canonical category distribution (cultural, adventure, local, community)."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    cats = {c["category"]: c for c in response.json()["category_distribution"]}

    assert len(cats) == 4
    assert "adventure" in cats
    assert "cultural" in cats
    assert "local" in cats
    assert "community" in cats

    # Adventure: act1, act2, act6 -> 3 activities; b1, b2, b3 -> 3 bookings
    assert cats["adventure"]["activity_count"] == 3
    assert cats["adventure"]["active_activity_count"] == 2  # act1, act2 (act6 suspended)
    assert cats["adventure"]["booking_count"] == 3
    assert cats["adventure"]["total_participants"] == 9  # 3 + 2 + 4
    # Gross revenue: 12000 (b1) + 8000 (b2) + 20000 (b3) = 40000.00
    assert Decimal(cats["adventure"]["gross_revenue"]) == Decimal("40000.00")

    # Cultural: act3 (USD) -> 1 activity; b4 (cancelled) -> 1 booking
    assert cats["cultural"]["activity_count"] == 1
    assert cats["cultural"]["booking_count"] == 1

    # Local: act4 (pending) -> 1 activity; 0 bookings
    assert cats["local"]["activity_count"] == 1
    assert cats["local"]["booking_count"] == 0

    # Community: act5 (draft) -> 1 activity; 0 bookings
    assert cats["community"]["activity_count"] == 1
    assert cats["community"]["booking_count"] == 0


def test_regional_district_distribution(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies regional grouping across administrative districts."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    dists = {d["district"]: d for d in response.json()["regional_distribution"]}

    # Matara: act1 (surf), act6 (mirissa); b1, b2 (surf) -> 2 bookings
    assert dists["Matara"]["activity_count"] == 2
    assert dists["Matara"]["booking_count"] == 2

    # Badulla: act2 (ella hike); b3 -> 1 booking
    assert dists["Badulla"]["activity_count"] == 1
    assert dists["Badulla"]["booking_count"] == 1

    # Kandy: act3; b4 -> 1 booking
    assert dists["Kandy"]["activity_count"] == 1
    assert dists["Kandy"]["booking_count"] == 1


def test_multi_currency_financial_segregation(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies that monetary figures are strictly partitioned by currency (LKR and USD)."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    fin = {f["currency"]: f for f in response.json()["financial_overview"]}

    assert "LKR" in fin
    assert "USD" in fin

    # LKR: b1 (12000 confirmed unpaid), b2 (8000 completed paid), b3 (20000 confirmed paid)
    # Total gross = 12000 + 8000 + 20000 = 40000.00
    # Paid = 8000 + 20000 = 28000.00
    # Unpaid = 12000.00
    # Refunded = 0.00
    assert Decimal(fin["LKR"]["total_gross_revenue"]) == Decimal("40000.00")
    assert Decimal(fin["LKR"]["paid_revenue"]) == Decimal("28000.00")
    assert Decimal(fin["LKR"]["unpaid_revenue"]) == Decimal("12000.00")
    assert Decimal(fin["LKR"]["refunded_amount"]) == Decimal("0.00")

    # USD: b4 (60.00 cancelled refunded)
    # Gross = 0.00 (cancelled excluded)
    # Paid = 0.00
    # Unpaid = 0.00
    # Refunded = 60.00
    assert Decimal(fin["USD"]["total_gross_revenue"]) == Decimal("0.00")
    assert Decimal(fin["USD"]["paid_revenue"]) == Decimal("0.00")
    assert Decimal(fin["USD"]["unpaid_revenue"]) == Decimal("0.00")
    assert Decimal(fin["USD"]["refunded_amount"]) == Decimal("60.00")


def test_top_activities_and_top_providers(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies ranking of top activities and top providers by booking volume."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    data = response.json()

    top_acts = data["top_activities"]
    assert len(top_acts) > 0
    # Surf camp had 2 bookings (b1, b2); Ella hike had 1 booking (b3)
    assert top_acts[0]["activity_id"] == 10
    assert top_acts[0]["activity_title"] == "Weligama Beginner Surf Camp"
    assert top_acts[0]["booking_count"] == 2
    assert Decimal(top_acts[0]["gross_revenue"]) == Decimal("20000.00")

    top_provs = data["top_providers"]
    assert len(top_provs) > 0
    # Provider 1 (Silva Surf) had 3 bookings (b1, b2 on act10, b4 on act30); Provider 2 (Perera) had 1 booking (b3 on act20)
    assert top_provs[0]["provider_profile_id"] == 1
    assert top_provs[0]["business_name"] == "Silva Surf Academy"
    assert top_provs[0]["booking_count"] == 3

    assert top_provs[1]["provider_profile_id"] == 2
    assert top_provs[1]["business_name"] == "Perera Trekking Tours"
    assert top_provs[1]["booking_count"] == 1


# ==============================================================================
# Date Filtering & Validation Tests
# ==============================================================================


def test_date_range_filtering(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies that date_from and date_to filter booking counts accurately."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)

    now = datetime.now(UTC).date()
    # b1: now - 1 day, b2: now - 2 days, b3: now - 3 days, b4: now - 4 days
    date_from = now - timedelta(days=2)
    date_to = now

    response = client.get(
        f"/api/v1/admin/activities/reports?date_from={date_from}&date_to={date_to}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["date_from"] == str(date_from)
    assert data["date_to"] == str(date_to)

    # Within last 2 days: only b1 (1 day ago) and b2 (2 days ago)
    book = data["booking_overview"]
    assert book["total_bookings"] == 2
    assert book["confirmed_bookings"] == 1  # b1
    assert book["completed_bookings"] == 1  # b2


def test_date_alias_compatibility(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies that start_date and end_date work as aliases for date_from and date_to."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)

    now = datetime.now(UTC).date()
    start_date = now - timedelta(days=2)
    end_date = now

    response = client.get(
        f"/api/v1/admin/activities/reports?start_date={start_date}&end_date={end_date}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["date_from"] == str(start_date)
    assert data["date_to"] == str(end_date)
    assert data["booking_overview"]["total_bookings"] == 2


def test_invalid_date_range_rejected(seeded_users):
    """date_from > date_to is rejected with 422 Unprocessable Entity."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/admin/activities/reports?date_from=2026-12-10&date_to=2026-12-01"
    )
    assert response.status_code == 422
    assert "cannot be after" in response.json()["detail"]


def test_excessive_date_window_rejected(seeded_users):
    """Date window exceeding 5 years is rejected with 422 Unprocessable Entity."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/admin/activities/reports?date_from=2020-01-01&date_to=2026-01-01"
    )
    assert response.status_code == 422
    assert "exceeds maximum allowed window" in response.json()["detail"]


# ==============================================================================
# Privacy & Isolation Tests
# ==============================================================================


def test_privacy_protection_zero_pii(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies that report payloads strictly exclude traveler personal PII."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    text_content = response.text

    # Verify absence of traveler names and emergency contact details
    assert "Jane Doe" not in text_content
    assert "traveler@ceylongo.test" not in text_content
    assert "John Doe" not in text_content
    assert "+94770000000" not in text_content
    assert "Asthma inhaler" not in text_content


def test_cross_module_isolation_eco_excluded(
    seeded_users, seeded_activities, seeded_slots_and_bookings
):
    """Verifies that legacy eco slots/bookings are completely excluded from activity reports."""
    set_auth(seeded_users["admin"])
    client = TestClient(test_app)
    response = client.get("/api/v1/admin/activities/reports")
    assert response.status_code == 200
    data = response.json()

    # Eco slot s4_eco has capacity=10, reserved=3
    # Activity slots total = 24 capacity, 11 reserved (s4_eco excluded)
    assert data["capacity_utilization"]["total_capacity"] == 24
    assert data["capacity_utilization"]["total_reserved"] == 11

    # Eco booking b5_eco has price=9999.00
    # Activity bookings gross = 40000.00 (b5_eco excluded)
    fin_lkr = next(f for f in data["financial_overview"] if f["currency"] == "LKR")
    assert Decimal(fin_lkr["total_gross_revenue"]) == Decimal("40000.00")
