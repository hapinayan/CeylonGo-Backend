"""
Automated API and ORM tests for Module 03 — Phase 5C:
Availability Calendar (A07) & Provider Analytics (B01).

Requirements covered:
- A07: Traveler Activity Availability Calendar (GET /api/v1/activities/{id}/availability
  and date filters on GET /api/v1/activities/{id}/slots)
- B01: Provider Activity Analytics (GET /api/v1/provider/activities/analytics with
  activity overview, booking conversion, revenue breakdown, capacity utilization,
  and per-activity breakdown with ratings and reviews)
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI, HTTPException, status
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
from app.core.listing_sync import sync_activity_listing_reference
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference, Review
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_slots import router as activity_slots_router
from app.routers.provider_activities import (
    require_activity_provider,
    router as provider_activities_router,
)

# Test FastAPI App with proper route ordering to prevent collision
test_app = FastAPI(title="CeylonGo Module 03 Phase 5C Test App")
test_app.include_router(activities_public_router)
test_app.include_router(activity_slots_router)
test_app.include_router(provider_activities_router)

# In-memory SQLite engine
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
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: TestingSessionLocal()
    Base.metadata.create_all(bind=engine)
    yield
    test_app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(db_session):
    test_app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(test_app)


@pytest.fixture
def seeded_users(db_session):
    """Seed admin, two activity providers, one non-activity provider, and travelers."""
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
        full_name="Alice Outdoor",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Bob Watersports",
        role="provider",
        is_active=True,
    )
    hotel_prov_user = User(
        id=4,
        email="hotelprov@ceylongo.test",
        password_hash="fakehash",
        full_name="Hotel Manager",
        role="provider",
        is_active=True,
    )
    traveler1_user = User(
        id=5,
        email="traveler1@ceylongo.test",
        password_hash="fakehash",
        full_name="Traveler Charlie",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([
        admin_user,
        prov1_user,
        prov2_user,
        hotel_prov_user,
        traveler1_user,
    ])
    db_session.commit()

    prof1 = ProviderProfile(
        id=1,
        user_id=prov1_user.id,
        business_name="Alice Outdoor Adventures",
        provider_type="activity",
        approval_status="approved",
        district="Kandy",
        phone="0771112233",
        description="Outdoor activities specialist",
    )
    prof2 = ProviderProfile(
        id=2,
        user_id=prov2_user.id,
        business_name="Bob Watersports",
        provider_type="activity",
        approval_status="approved",
        district="Galle",
        phone="0774445566",
        description="Watersports specialist",
    )
    hotel_prof = ProviderProfile(
        id=3,
        user_id=hotel_prov_user.id,
        business_name="Grand Colombo Hotel",
        provider_type="hotel",
        approval_status="approved",
        district="Colombo",
        phone="0112223344",
        description="Luxury hotel",
    )
    db_session.add_all([prof1, prof2, hotel_prof])
    db_session.commit()

    return {
        "admin": admin_user,
        "prov1_user": prov1_user,
        "prov1_profile": prof1,
        "prov2_user": prov2_user,
        "prov2_profile": prof2,
        "hotel_user": hotel_prov_user,
        "hotel_profile": hotel_prof,
        "traveler1": traveler1_user,
    }


def create_activity_record(
    db: Session,
    provider_profile_id: int,
    title: str = "Knuckles Mountain Trek",
    approval_status: str = "approved",
    is_active: bool = True,
    price: Decimal = Decimal("5000.00"),
) -> Activity:
    act = Activity(
        provider_profile_id=provider_profile_id,
        title=title,
        description="High altitude scenic trek through Knuckles range.",
        category="adventure",
        district="Kandy",
        city="Teldeniya",
        meeting_point="Teldeniya Junction Clock Tower",
        latitude=Decimal("7.300000"),
        longitude=Decimal("80.760000"),
        duration_minutes=240,
        itinerary="Morning hike -> Summit view -> Waterfall bath -> Return",
        requirements="Good physical fitness required.",
        safety_rules="Follow guide instructions at all times.",
        required_equipment="Trekking boots, 2L water.",
        minimum_age=12,
        pricing_type="per_person",
        price=price,
        currency="LKR",
        approval_status=approval_status,
        is_active=is_active,
    )
    db.add(act)
    db.commit()
    db.refresh(act)
    return act


def create_slot_record(
    db: Session,
    activity_id: int,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    capacity: int = 10,
    reserved_count: int = 0,
    is_active: bool = True,
) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    st = starts_at or (now + timedelta(days=2))
    et = ends_at or (st + timedelta(hours=4))
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=st,
        ends_at=et,
        capacity=capacity,
        reserved_count=reserved_count,
        is_active=is_active,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def create_booking_record(
    db: Session,
    slot_id: int,
    user_id: int,
    participants: int = 2,
    booking_status: str = "confirmed",
    payment_status: str = "paid",
    total_price: Decimal = Decimal("10000.00"),
    created_at: datetime | None = None,
) -> ActivityBooking:
    now = datetime.now(UTC).replace(tzinfo=None)
    booking = ActivityBooking(
        activity_slot_id=slot_id,
        user_id=user_id,
        participants=participants,
        total_price=total_price,
        currency="LKR",
        payment_method="online",
        payment_status=payment_status,
        booking_status=booking_status,
        created_at=created_at or now,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    return booking


def set_auth(
    user: User | None = None,
    profile: ProviderProfile | None = None,
    db: Session | None = None,
):
    """Configures dependency overrides cleanly for authentication."""
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: db or TestingSessionLocal()
    if user is None:
        def fail_auth():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        test_app.dependency_overrides[get_current_user] = fail_auth
        test_app.dependency_overrides[require_traveler] = fail_auth
        test_app.dependency_overrides[require_approved_provider] = fail_auth
        test_app.dependency_overrides[require_activity_provider] = fail_auth
        test_app.dependency_overrides[require_admin] = fail_auth
        return

    test_app.dependency_overrides[get_current_user] = lambda: user

    if user.role == "traveler":
        test_app.dependency_overrides[require_traveler] = lambda: user
    else:
        def deny_traveler():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Traveler required")
        test_app.dependency_overrides[require_traveler] = deny_traveler

    if user.role == "admin":
        test_app.dependency_overrides[require_admin] = lambda: user
    else:
        def deny_admin():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
        test_app.dependency_overrides[require_admin] = deny_admin

    if profile is not None:
        test_app.dependency_overrides[require_approved_provider] = lambda: profile
        if profile.provider_type == "activity":
            test_app.dependency_overrides[require_activity_provider] = lambda: profile
        else:
            def deny_activity():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Activity provider access required",
                )
            test_app.dependency_overrides[require_activity_provider] = deny_activity
    else:
        def deny_prov():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Provider required")
        test_app.dependency_overrides[require_approved_provider] = deny_prov
        test_app.dependency_overrides[require_activity_provider] = deny_prov


# ==============================================================================
# A07 – Traveler Activity Availability Calendar Tests
# ==============================================================================


def test_activity_availability_calendar_defaults(db_session, seeded_users, client):
    """Public traveler gets activity availability calendar with default 30-day window (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    today = date.today()
    d1 = datetime.combine(today + timedelta(days=3), datetime.min.time().replace(hour=9))
    d2 = datetime.combine(today + timedelta(days=5), datetime.min.time().replace(hour=14))

    slot1 = create_slot_record(db_session, act.id, starts_at=d1, capacity=10, reserved_count=2)
    slot2 = create_slot_record(db_session, act.id, starts_at=d2, capacity=6, reserved_count=6)  # Sold out

    res = client.get(f"/api/v1/activities/{act.id}/availability")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["activity_id"] == act.id
    assert data["total_slots"] == 2
    assert data["available_slots"] == 1
    assert data["date_from"] == str(today)
    assert data["date_to"] == str(today + timedelta(days=30))

    # Check day summaries
    days = data["days"]
    assert len(days) == 2
    day_d1 = next((d for d in days if d["date"] == str(d1.date())), None)
    assert day_d1 is not None
    assert day_d1["total_slots"] == 1
    assert day_d1["available_slots"] == 1
    assert day_d1["total_remaining"] == 8
    assert day_d1["has_availability"] is True

    day_d2 = next((d for d in days if d["date"] == str(d2.date())), None)
    assert day_d2 is not None
    assert day_d2["total_slots"] == 1
    assert day_d2["available_slots"] == 0
    assert day_d2["total_remaining"] == 0
    assert day_d2["has_availability"] is False

    # Check slot items
    slots = data["slots"]
    assert len(slots) == 2
    s1 = next((s for s in slots if s["id"] == slot1.id), None)
    assert s1 is not None
    assert s1["remaining_capacity"] == 8
    assert s1["availability_status"] == "available"

    s2 = next((s for s in slots if s["id"] == slot2.id), None)
    assert s2 is not None
    assert s2["remaining_capacity"] == 0
    assert s2["availability_status"] == "sold_out"


def test_activity_availability_date_filtering(db_session, seeded_users, client):
    """Calendar respects date_from/date_to and start_date/end_date aliases (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    today = date.today()
    slot_early = create_slot_record(
        db_session, act.id, starts_at=datetime.combine(today + timedelta(days=2), datetime.min.time().replace(hour=8))
    )
    slot_mid = create_slot_record(
        db_session, act.id, starts_at=datetime.combine(today + timedelta(days=10), datetime.min.time().replace(hour=8))
    )
    slot_late = create_slot_record(
        db_session, act.id, starts_at=datetime.combine(today + timedelta(days=25), datetime.min.time().replace(hour=8))
    )

    # Filter with date_from and date_to covering only slot_mid
    f_from = str(today + timedelta(days=8))
    f_to = str(today + timedelta(days=15))
    res = client.get(f"/api/v1/activities/{act.id}/availability?date_from={f_from}&date_to={f_to}")
    assert res.status_code == 200
    data = res.json()
    assert data["total_slots"] == 1
    assert data["slots"][0]["id"] == slot_mid.id

    # Filter with start_date and end_date alias
    res_alias = client.get(f"/api/v1/activities/{act.id}/availability?start_date={f_from}&end_date={f_to}")
    assert res_alias.status_code == 200
    assert res_alias.json()["total_slots"] == 1


def test_activity_availability_inverted_dates_422(db_session, seeded_users, client):
    """Inverted date range returns 422 Unprocessable Entity (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    res = client.get(f"/api/v1/activities/{act.id}/availability?date_from=2026-06-15&date_to=2026-06-10")
    assert res.status_code == 422
    assert "start_date / date_from cannot be after end_date / date_to" in res.json()["detail"]


def test_activity_availability_exceeds_180_days_422(db_session, seeded_users, client):
    """Calendar query window exceeding 180 days returns 422 Unprocessable Entity (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    today = date.today()
    too_far = today + timedelta(days=181)
    res = client.get(f"/api/v1/activities/{act.id}/availability?date_from={today}&date_to={too_far}")
    assert res.status_code == 422
    assert "180 days" in res.json()["detail"]


def test_activity_availability_status_transitions(db_session, seeded_users, client):
    """Slot availability accurately identifies available, limited, and sold_out status (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    now = datetime.now(UTC).replace(tzinfo=None)
    # Available (10 cap, 2 res = 8 left)
    s_avail = create_slot_record(
        db_session, act.id, starts_at=now + timedelta(days=1), capacity=10, reserved_count=2
    )
    # Limited due to <= 2 remaining (5 cap, 3 res = 2 left)
    s_lim1 = create_slot_record(
        db_session, act.id, starts_at=now + timedelta(days=2), capacity=5, reserved_count=3
    )
    # Limited due to >= 80% occupancy (20 cap, 16 res = 4 left, 80% full)
    s_lim2 = create_slot_record(
        db_session, act.id, starts_at=now + timedelta(days=3), capacity=20, reserved_count=16
    )
    # Sold out (capacity fully reserved)
    s_sold = create_slot_record(
        db_session, act.id, starts_at=now + timedelta(days=4), capacity=8, reserved_count=8
    )
    # Inactive slot
    s_inact = create_slot_record(
        db_session, act.id, starts_at=now + timedelta(days=5), capacity=10, reserved_count=0, is_active=False
    )

    res = client.get(f"/api/v1/activities/{act.id}/availability?date_to={date.today() + timedelta(days=10)}")
    assert res.status_code == 200
    slot_map = {s["id"]: s for s in res.json()["slots"]}

    assert slot_map[s_avail.id]["availability_status"] == "available"
    assert slot_map[s_lim1.id]["availability_status"] == "limited"
    assert slot_map[s_lim2.id]["availability_status"] == "limited"
    assert slot_map[s_sold.id]["availability_status"] == "sold_out"
    # Inactive slot is filtered out of public calendar
    assert s_inact.id not in slot_map


def test_activity_availability_unapproved_or_inactive_404(db_session, seeded_users, client):
    """Calendar returns 404 for draft, suspended, rejected, or inactive activities (A07)."""
    prof = seeded_users["prov1_profile"]
    act_draft = create_activity_record(db_session, prof.id, title="Draft Trek", approval_status="draft")
    act_susp = create_activity_record(db_session, prof.id, title="Suspended Trek", approval_status="suspended")
    act_inact = create_activity_record(db_session, prof.id, title="Inactive Trek", approval_status="approved", is_active=False)

    # Draft -> 404
    assert client.get(f"/api/v1/activities/{act_draft.id}/availability").status_code == 404
    # Suspended -> 404
    assert client.get(f"/api/v1/activities/{act_susp.id}/availability").status_code == 404
    # Inactive -> 404
    assert client.get(f"/api/v1/activities/{act_inact.id}/availability").status_code == 404
    # Non-existent -> 404
    assert client.get("/api/v1/activities/99999/availability").status_code == 404


def test_activity_slots_public_listing_date_filters(db_session, seeded_users, client):
    """GET /api/v1/activities/{id}/slots supports date_from / date_to query parameters (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    today = date.today()
    s1 = create_slot_record(db_session, act.id, starts_at=datetime.combine(today + timedelta(days=2), datetime.min.time()))
    s2 = create_slot_record(db_session, act.id, starts_at=datetime.combine(today + timedelta(days=12), datetime.min.time()))

    # Inverted date returns 422
    inv_res = client.get(f"/api/v1/activities/{act.id}/slots?date_from=2026-07-20&date_to=2026-07-10")
    assert inv_res.status_code == 422

    # Filter with date_from matching only s2
    res = client.get(f"/api/v1/activities/{act.id}/slots?date_from={today + timedelta(days=10)}")
    assert res.status_code == 200
    slots = res.json()
    assert len(slots) == 1
    assert slots[0]["id"] == s2.id


# ==============================================================================
# B01 – Provider Activity Analytics Tests
# ==============================================================================


def test_provider_analytics_basic_success(db_session, seeded_users, client):
    """Approved activity provider retrieves complete analytics payload (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]

    act1 = create_activity_record(db_session, prof.id, title="Knuckles Trek", approval_status="approved", is_active=True)
    act2 = create_activity_record(db_session, prof.id, title="Ella Rock Climb", approval_status="pending", is_active=False)

    slot1 = create_slot_record(db_session, act1.id, capacity=10, reserved_count=4)
    create_booking_record(db_session, slot1.id, user_id=seeded_users["traveler1"].id, participants=4, booking_status="confirmed", payment_status="paid", total_price=Decimal("20000.00"))

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["provider_profile_id"] == prof.id
    assert data["business_name"] == prof.business_name
    assert data["activity_filter_id"] is None

    # Activity overview
    acts = data["activities"]
    assert acts["total_activities"] == 2
    assert acts["active_activities"] == 1
    assert acts["approved_activities"] == 1
    assert acts["pending_activities"] == 1

    # Bookings overview
    bk = data["bookings"]
    assert bk["total_bookings"] == 1
    assert bk["confirmed_bookings"] == 1
    assert bk["total_participants"] == 4
    assert bk["cancellation_rate"] == 0.0

    # Financials
    fin = data["financials"]
    assert Decimal(str(fin["total_gross_revenue"])) == Decimal("20000.00")
    assert Decimal(str(fin["paid_revenue"])) == Decimal("20000.00")
    assert Decimal(str(fin["unpaid_revenue"])) == Decimal("0.00")
    assert fin["currency"] == "LKR"

    # Capacity
    cap = data["capacity"]
    assert cap["total_slots_offered"] == 1
    assert cap["total_capacity_offered"] == 10
    assert cap["total_capacity_reserved"] == 4
    assert cap["occupancy_rate"] == 40.0

    # Breakdown
    assert len(data["breakdown"]) == 2
    b1 = next((b for b in data["breakdown"] if b["activity_id"] == act1.id), None)
    assert b1 is not None
    assert b1["total_bookings"] == 1
    assert b1["confirmed_bookings"] == 1
    assert Decimal(str(b1["gross_revenue"])) == Decimal("20000.00")


def test_provider_analytics_route_precedence_no_collision(db_session, seeded_users, client):
    """GET /api/v1/provider/activities/analytics does not collide with /{activity_id} (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]

    set_auth(user=user, profile=prof, db=db_session)

    # Analytics call must succeed (200), not throw 422 attempting to parse 'analytics' as int
    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200

    # Dynamic path with real int must also work
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)
    res_detail = client.get(f"/api/v1/provider/activities/{act.id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == act.id


def test_provider_analytics_scoped_to_activity_id(db_session, seeded_users, client):
    """Provider can filter analytics by a specific owned activity_id (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]

    act1 = create_activity_record(db_session, prof.id, title="Activity 1")
    act2 = create_activity_record(db_session, prof.id, title="Activity 2")

    slot1 = create_slot_record(db_session, act1.id, capacity=10, reserved_count=2)
    slot2 = create_slot_record(db_session, act2.id, capacity=20, reserved_count=10)

    create_booking_record(db_session, slot1.id, seeded_users["traveler1"].id, participants=2, total_price=Decimal("5000.00"))
    create_booking_record(db_session, slot2.id, seeded_users["traveler1"].id, participants=10, total_price=Decimal("25000.00"))

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get(f"/api/v1/provider/activities/analytics?activity_id={act1.id}")
    assert res.status_code == 200
    data = res.json()

    assert data["activity_filter_id"] == act1.id
    assert data["activities"]["total_activities"] == 1
    assert data["bookings"]["total_bookings"] == 1
    assert data["capacity"]["total_capacity_offered"] == 10
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("5000.00")
    assert len(data["breakdown"]) == 1
    assert data["breakdown"][0]["activity_id"] == act1.id


def test_provider_analytics_cross_provider_isolation(db_session, seeded_users, client):
    """Provider cannot view analytics for an activity owned by another provider (B01)."""
    prof1 = seeded_users["prov1_profile"]
    user1 = seeded_users["prov1_user"]
    prof2 = seeded_users["prov2_profile"]

    act_of_p2 = create_activity_record(db_session, prof2.id, title="Bob Surfing School")

    set_auth(user=user1, profile=prof1, db=db_session)

    # Provider 1 asks for analytics of Provider 2's activity -> 404
    res = client.get(f"/api/v1/provider/activities/analytics?activity_id={act_of_p2.id}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Activity not found"


def test_provider_analytics_auth_and_rbac_enforcement(db_session, seeded_users, client):
    """Only active activity providers can access analytics; unauthorized roles get 401/403 (B01)."""
    # 1. Unauthenticated -> 401
    set_auth(user=None, profile=None, db=db_session)
    assert client.get("/api/v1/provider/activities/analytics").status_code == 401

    # 2. Traveler -> 403
    set_auth(user=seeded_users["traveler1"], profile=None, db=db_session)
    assert client.get("/api/v1/provider/activities/analytics").status_code == 403

    # 3. Hotel Provider (non-activity) -> 403
    set_auth(user=seeded_users["hotel_user"], profile=seeded_users["hotel_profile"], db=db_session)
    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 403
    assert "Activity provider access required" in res.json()["detail"]


def test_provider_analytics_date_filtering_and_validation(db_session, seeded_users, client):
    """Analytics respects date_from/date_to filters and rejects inverted ranges (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]

    act = create_activity_record(db_session, prof.id, title="Sunrise Trek")
    now = datetime.now(UTC).replace(tzinfo=None)

    slot_past = create_slot_record(db_session, act.id, starts_at=now - timedelta(days=20), capacity=10, reserved_count=2)
    slot_curr = create_slot_record(db_session, act.id, starts_at=now + timedelta(days=2), capacity=10, reserved_count=3)

    b_past = create_booking_record(
        db_session, slot_past.id, seeded_users["traveler1"].id,
        created_at=now - timedelta(days=20), total_price=Decimal("10000.00"),
    )
    b_curr = create_booking_record(
        db_session, slot_curr.id, seeded_users["traveler1"].id,
        created_at=now, total_price=Decimal("15000.00"),
    )

    set_auth(user=user, profile=prof, db=db_session)

    # Inverted date range -> 422
    res_inv = client.get("/api/v1/provider/activities/analytics?date_from=2026-06-20&date_to=2026-06-10")
    assert res_inv.status_code == 422
    assert "start_date / date_from cannot be after end_date / date_to" in res_inv.json()["detail"]

    # Filter for current period only
    start_filter = str((now - timedelta(days=5)).date())
    end_filter = str((now + timedelta(days=10)).date())
    res = client.get(f"/api/v1/provider/activities/analytics?date_from={start_filter}&date_to={end_filter}")
    assert res.status_code == 200
    data = res.json()

    assert data["bookings"]["total_bookings"] == 1
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("15000.00")
    assert data["capacity"]["total_slots_offered"] == 1
    assert data["capacity"]["total_capacity_reserved"] == 3


def test_provider_analytics_metric_calculations_accuracy(db_session, seeded_users, client):
    """Detailed verification of cancellation rate, revenue split, and occupancy calculations (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    act = create_activity_record(db_session, prof.id, title="Rafting Expedition")

    now = datetime.now(UTC).replace(tzinfo=None)
    slot1 = create_slot_record(db_session, act.id, starts_at=now + timedelta(days=1), capacity=20, reserved_count=12)

    # Booking 1: Confirmed & Paid
    b1 = create_booking_record(
        db_session, slot1.id, seeded_users["traveler1"].id,
        participants=4, booking_status="confirmed", payment_status="paid", total_price=Decimal("20000.00")
    )
    # Booking 2: Confirmed & Unpaid (pay at location)
    b2 = create_booking_record(
        db_session, slot1.id, seeded_users["traveler1"].id,
        participants=4, booking_status="confirmed", payment_status="unpaid", total_price=Decimal("20000.00")
    )
    # Booking 3: Cancelled (does not add to gross/paid revenue)
    b3 = create_booking_record(
        db_session, slot1.id, seeded_users["traveler1"].id,
        participants=2, booking_status="cancelled", payment_status="unpaid", total_price=Decimal("10000.00")
    )
    # Booking 4: Rejected
    b4 = create_booking_record(
        db_session, slot1.id, seeded_users["traveler1"].id,
        participants=2, booking_status="rejected", payment_status="failed", total_price=Decimal("10000.00")
    )

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    # Bookings overview
    bk = data["bookings"]
    assert bk["total_bookings"] == 4
    assert bk["confirmed_bookings"] == 2
    assert bk["cancelled_bookings"] == 1
    assert bk["rejected_bookings"] == 1
    assert bk["total_participants"] == 12  # 4 + 4 + 2 + 2
    assert bk["confirmed_participants"] == 8  # 4 + 4
    # Cancellation rate: 1 cancelled out of 4 = 25.0%
    assert bk["cancellation_rate"] == 25.0

    # Financials: gross = paid (20k) + unpaid (20k) = 40k. Cancelled and rejected excluded!
    fin = data["financials"]
    assert Decimal(str(fin["total_gross_revenue"])) == Decimal("40000.00")
    assert Decimal(str(fin["paid_revenue"])) == Decimal("20000.00")
    assert Decimal(str(fin["unpaid_revenue"])) == Decimal("20000.00")
    assert Decimal(str(fin["total_gross_revenue"])) == Decimal(str(fin["paid_revenue"])) + Decimal(str(fin["unpaid_revenue"]))

    # Capacity: 12 reserved / 20 capacity = 60.0%
    cap = data["capacity"]
    assert cap["total_capacity_offered"] == 20
    assert cap["total_capacity_reserved"] == 12
    assert cap["occupancy_rate"] == 60.0


def test_provider_analytics_reviews_aggregated_breakdown(db_session, seeded_users, client):
    """Breakdown correctly reflects review stats aggregated without N+1 queries (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    traveler = seeded_users["traveler1"]

    act = create_activity_record(db_session, prof.id, title="Whale Watching Mirissa", approval_status="approved", is_active=True)
    # Sync listing reference
    sync_activity_listing_reference(db_session, act)
    db_session.commit()

    ref = db_session.query(ListingReference).filter(ListingReference.target_id == act.id, ListingReference.module_type == "activity").first()
    assert ref is not None

    # Add 2 reviews (rating 5.0 and 4.0 -> avg 4.5)
    r1 = Review(
        listing_reference_id=ref.id,
        user_id=traveler.id,
        rating=Decimal("5.00"),
        comment="Incredible experience! Saw 3 blue whales.",
        is_verified_booking=True,
        is_published=True,
    )
    r2 = Review(
        listing_reference_id=ref.id,
        user_id=traveler.id,
        rating=Decimal("4.00"),
        comment="Very good guide and safety briefing.",
        is_verified_booking=True,
        is_published=True,
    )
    # Unpublished review (should NOT be counted)
    r3 = Review(
        listing_reference_id=ref.id,
        user_id=traveler.id,
        rating=Decimal("1.00"),
        comment="Pending review",
        is_verified_booking=False,
        is_published=False,
    )
    db_session.add_all([r1, r2, r3])
    db_session.commit()

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    breakdown = data["breakdown"]
    assert len(breakdown) == 1
    metric = breakdown[0]
    assert metric["activity_id"] == act.id
    assert metric["total_reviews"] == 2
    assert metric["average_rating"] == 4.5


def test_provider_analytics_empty_state_no_division_by_zero(db_session, seeded_users, client):
    """Empty provider state returns 0s, 0.0 rates, and empty breakdown safely (B01)."""
    prof = seeded_users["prov2_profile"]  # Has 0 activities
    user = seeded_users["prov2_user"]

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    assert data["provider_profile_id"] == prof.id
    assert data["activities"]["total_activities"] == 0
    assert data["bookings"]["total_bookings"] == 0
    assert data["bookings"]["cancellation_rate"] == 0.0
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("0.00")
    assert data["capacity"]["total_slots_offered"] == 0
    assert data["capacity"]["occupancy_rate"] == 0.0
    assert data["breakdown"] == []


# ==============================================================================
# Phase 5C Safety Review & Edge Case Verification Tests
# ==============================================================================


def test_activity_availability_exact_boundaries_and_past_slots(db_session, seeded_users, client):
    """Verifies midnight/end-of-day boundary inclusion and past slot exclusion (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    target_date = date.today() + timedelta(days=5)
    # Slot 1: Starts exactly at midnight (00:00:00) of target date
    s_midnight = create_slot_record(
        db_session, act.id,
        starts_at=datetime.combine(target_date, datetime.min.time()),
        ends_at=datetime.combine(target_date, datetime.min.time()) + timedelta(hours=2),
        capacity=10, reserved_count=2,
    )
    # Slot 2: Starts at 23:50:00 of target date (close to end of day)
    s_eod = create_slot_record(
        db_session, act.id,
        starts_at=datetime.combine(target_date, datetime.max.time().replace(hour=23, minute=50, second=0)),
        ends_at=datetime.combine(target_date, datetime.max.time().replace(hour=23, minute=59, second=59)),
        capacity=8, reserved_count=1,
    )
    # Slot 3: Past slot (yesterday) - should be excluded
    s_past = create_slot_record(
        db_session, act.id,
        starts_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1),
        capacity=10, reserved_count=0,
    )

    # Query single day
    res = client.get(f"/api/v1/activities/{act.id}/availability?date_from={target_date}&date_to={target_date}")
    assert res.status_code == 200
    data = res.json()

    slot_ids = [s["id"] for s in data["slots"]]
    assert s_midnight.id in slot_ids
    assert s_eod.id in slot_ids
    assert s_past.id not in slot_ids
    assert data["total_slots"] == 2
    assert len(data["days"]) == 1
    assert data["days"][0]["date"] == str(target_date)


def test_activity_availability_overbooked_slot_no_negative(db_session, seeded_users, client):
    """Fully booked slot (reserved_count == capacity) returns 0 remaining capacity and 'sold_out' (A07)."""
    prof = seeded_users["prov1_profile"]
    act = create_activity_record(db_session, prof.id, approval_status="approved", is_active=True)

    future_dt = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=3)
    s_full = create_slot_record(db_session, act.id, starts_at=future_dt, capacity=10, reserved_count=10)

    res = client.get(f"/api/v1/activities/{act.id}/availability?date_to={date.today() + timedelta(days=10)}")
    assert res.status_code == 200
    data = res.json()

    slot_data = next((s for s in data["slots"] if s["id"] == s_full.id), None)
    assert slot_data is not None
    assert slot_data["remaining_capacity"] == 0
    assert slot_data["availability_status"] == "sold_out"
    assert slot_data["is_available"] is False


def test_provider_analytics_date_boundary_midnight_and_eod(db_session, seeded_users, client):
    """Provider analytics includes bookings at exact midnight and end-of-day boundaries (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    act = create_activity_record(db_session, prof.id, title="Boundary Trek")
    slot = create_slot_record(db_session, act.id, capacity=20)

    target_date = date.today() - timedelta(days=2)
    dt_midnight = datetime.combine(target_date, datetime.min.time())
    dt_eod = datetime.combine(target_date, datetime.max.time().replace(microsecond=999000))

    b_midnight = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        created_at=dt_midnight, total_price=Decimal("5000.00"), payment_status="paid",
    )
    b_eod = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        created_at=dt_eod, total_price=Decimal("7000.00"), payment_status="paid",
    )

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get(f"/api/v1/provider/activities/analytics?date_from={target_date}&date_to={target_date}")
    assert res.status_code == 200
    data = res.json()

    assert data["bookings"]["total_bookings"] == 2
    assert Decimal(str(data["financials"]["paid_revenue"])) == Decimal("12000.00")
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("12000.00")


def test_provider_analytics_multiple_currencies_and_refunds(db_session, seeded_users, client):
    """Analytics handles multiple currencies via by_currency and tracks refunds accurately (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    act = create_activity_record(db_session, prof.id, title="Multi Currency Safari")
    slot = create_slot_record(db_session, act.id, capacity=30)

    # 1. Paid in LKR
    b_lkr = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        total_price=Decimal("15000.00"), payment_status="paid",
    )
    b_lkr.currency = "LKR"

    # 2. Paid in USD
    b_usd = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        total_price=Decimal("50.00"), payment_status="paid",
    )
    b_usd.currency = "USD"

    # 3. Refunded in LKR
    b_ref = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        total_price=Decimal("5000.00"), payment_status="refunded", booking_status="cancelled",
    )
    b_ref.currency = "LKR"

    # 4. Failed in LKR
    b_failed = create_booking_record(
        db_session, slot.id, seeded_users["traveler1"].id,
        total_price=Decimal("8000.00"), payment_status="failed", booking_status="pending",
    )
    b_failed.currency = "LKR"
    db_session.commit()

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    fin = data["financials"]
    assert Decimal(str(fin["paid_revenue"])) == Decimal("15000.00")
    assert Decimal(str(fin["refunded_amount"])) == Decimal("5000.00")
    assert fin["currency"] == "LKR"

    # Check by_currency
    by_curr = {c["currency"]: c for c in fin["by_currency"]}
    assert "LKR" in by_curr
    assert "USD" in by_curr
    assert Decimal(str(by_curr["USD"]["paid_revenue"])) == Decimal("50.00")
    assert Decimal(str(by_curr["USD"]["total_gross_revenue"])) == Decimal("50.00")
    assert Decimal(str(by_curr["LKR"]["paid_revenue"])) == Decimal("15000.00")
    assert Decimal(str(by_curr["LKR"]["refunded_amount"])) == Decimal("5000.00")


def test_provider_analytics_cross_provider_reviews_and_bookings_isolation(db_session, seeded_users, client):
    """Strict tenant isolation: reviews and bookings of other providers never leak (B01)."""
    prof1 = seeded_users["prov1_profile"]
    user1 = seeded_users["prov1_user"]
    prof2 = seeded_users["prov2_profile"]
    user2 = seeded_users["prov2_user"]

    # Activity for P1
    act1 = create_activity_record(db_session, prof1.id, title="Alice Activity")
    sync_activity_listing_reference(db_session, act1)
    slot1 = create_slot_record(db_session, act1.id, capacity=10, reserved_count=2)
    create_booking_record(db_session, slot1.id, seeded_users["traveler1"].id, total_price=Decimal("10000.00"))

    # Activity for P2
    act2 = create_activity_record(db_session, prof2.id, title="Bob Activity")
    sync_activity_listing_reference(db_session, act2)
    slot2 = create_slot_record(db_session, act2.id, capacity=10, reserved_count=5)
    create_booking_record(db_session, slot2.id, seeded_users["traveler1"].id, total_price=Decimal("25000.00"))

    ref2 = db_session.query(ListingReference).filter(ListingReference.target_id == act2.id, ListingReference.module_type == "activity").first()
    r2 = Review(listing_reference_id=ref2.id, user_id=seeded_users["traveler1"].id, rating=Decimal("5.00"), comment="Great Bob!", is_published=True)
    db_session.add(r2)
    db_session.commit()

    # Query as Provider 1
    set_auth(user=user1, profile=prof1, db=db_session)
    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    assert data["provider_profile_id"] == prof1.id
    assert data["activities"]["total_activities"] == 1
    assert data["bookings"]["total_bookings"] == 1
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("10000.00")
    # P1 activity has 0 reviews
    assert data["breakdown"][0]["total_reviews"] == 0
    assert data["breakdown"][0]["average_rating"] is None


def test_provider_analytics_response_privacy(db_session, seeded_users, client):
    """Analytics response contains zero traveler PII (no emails, names, phones) (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    act = create_activity_record(db_session, prof.id, title="Privacy Trek")
    slot = create_slot_record(db_session, act.id)
    create_booking_record(db_session, slot.id, seeded_users["traveler1"].id)

    set_auth(user=user, profile=prof, db=db_session)
    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    raw_text = res.text

    assert "traveler1@ceylongo.test" not in raw_text
    assert "Traveler Charlie" not in raw_text
    assert "fakehash" not in raw_text


def test_provider_analytics_suspended_activity_historical_bookings(db_session, seeded_users, client):
    """Historical bookings for a suspended activity are preserved in provider analytics (B01)."""
    prof = seeded_users["prov1_profile"]
    user = seeded_users["prov1_user"]
    act_susp = create_activity_record(db_session, prof.id, title="Historical Trek", approval_status="suspended", is_active=False)
    slot = create_slot_record(db_session, act_susp.id, capacity=10, reserved_count=3)
    create_booking_record(db_session, slot.id, seeded_users["traveler1"].id, participants=3, total_price=Decimal("15000.00"))

    set_auth(user=user, profile=prof, db=db_session)

    res = client.get("/api/v1/provider/activities/analytics")
    assert res.status_code == 200
    data = res.json()

    assert data["activities"]["suspended_activities"] == 1
    assert data["bookings"]["total_bookings"] == 1
    assert data["bookings"]["total_participants"] == 3
    assert Decimal(str(data["financials"]["total_gross_revenue"])) == Decimal("15000.00")
    assert len(data["breakdown"]) == 1
    assert data["breakdown"][0]["approval_status"] == "suspended"
