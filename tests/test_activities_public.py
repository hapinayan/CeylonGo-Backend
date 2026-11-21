"""
Automated API tests for Module 03 — Public Activity Discovery, Search, Categories, Details & Photo Gallery.

Covers:
- A01: Activity Discovery (browse approved & published experiences)
- A02: Search & Filters (location, category, price range, duration range, keyword search)
- A03: Activity Categories (cultural, adventure, local, community taxonomy)
- A04: Activity Details (itinerary, requirements, safety rules, equipment, organizer info)
- A05: Photo Gallery (public photo list sorted by display order)
- A06: Location & Maps (meeting point and coordinates)
- A15: Contact Provider (organizer contact information without sensitive credentials)
- D02: Safety Instructions (public retrieval of safety rules and required equipment)
- D03: Meeting Point (public retrieval of meeting instructions and coordinates)
- Route collision safety: /categories vs /{activity_id} vs /{activity_id}/slots
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.activity_slots import router as activity_slots_router

# Build isolated test FastAPI app
test_app = FastAPI(title="CeylonGo Module 03 Test App")
test_app.include_router(activities_public_router)
test_app.include_router(activity_slots_router)

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
    bind=engine, autoflush=False, expire_on_commit=False
)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    test_app.dependency_overrides[get_db] = override_get_db
    yield TestClient(test_app)
    test_app.dependency_overrides.clear()


def create_provider_user(
    db,
    email="provider@example.test",
    approval_status="approved",
    provider_type="activity",
    business_name="Ceylon Adventures Ltd",
    phone="0771234567",
    is_active=True,
):
    user = User(
        full_name="Test Provider User",
        email=email,
        role="provider",
        is_active=is_active,
    )
    db.add(user)
    db.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type=provider_type,
        approval_status=approval_status,
        district="Colombo",
        phone=phone,
        secondary_phone="0112345678",
        description="Official certified adventure provider in Sri Lanka",
    )
    db.add(profile)
    db.flush()
    return user, profile


def create_activity(
    db,
    provider_profile_id,
    title="Sigiriya Heritage Experience",
    description="Explore the ancient rock fortress with an experienced local guide.",
    category="cultural",
    district="Matale",
    city="Sigiriya",
    meeting_point="Sigiriya Main Entrance Ticket Counter",
    latitude=Decimal("7.957000"),
    longitude=Decimal("80.760300"),
    duration_minutes=180,
    itinerary="08:00 Meet at entrance -> 08:30 Ascend rock -> 11:00 Descend & summary",
    requirements="Moderate physical fitness required. Wear comfortable walking shoes.",
    safety_rules="Follow designated pathways at all times. Do not disturb wild wasps.",
    required_equipment="Sun protection, hat, at least 1L of drinking water.",
    minimum_age=8,
    pricing_type="per_person",
    price=Decimal("4500.00"),
    currency="LKR",
    approval_status="approved",
    is_active=True,
):
    activity = Activity(
        provider_profile_id=provider_profile_id,
        title=title,
        description=description,
        category=category,
        district=district,
        city=city,
        meeting_point=meeting_point,
        latitude=latitude,
        longitude=longitude,
        duration_minutes=duration_minutes,
        itinerary=itinerary,
        requirements=requirements,
        safety_rules=safety_rules,
        required_equipment=required_equipment,
        minimum_age=minimum_age,
        pricing_type=pricing_type,
        price=price,
        currency=currency,
        approval_status=approval_status,
        is_active=is_active,
    )
    db.add(activity)
    db.flush()
    return activity


# ==============================================================================
# 1. Category Taxonomy Tests (A03)
# ==============================================================================


def test_get_activity_categories(client):
    """Verify category taxonomy endpoint returns all 4 approved categories."""
    response = client.get("/api/v1/activities/categories")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 4

    slugs = [c["slug"] for c in data]
    assert slugs == ["cultural", "adventure", "local", "community"]

    for cat in data:
        assert "slug" in cat
        assert "name" in cat
        assert "description" in cat
        assert isinstance(cat["examples"], list)
        assert len(cat["examples"]) > 0


# ==============================================================================
# 2. Public Activity Discovery & Publication Invariants (A01, A02)
# ==============================================================================


def test_public_discovery_empty(client):
    """Empty listing returns valid pagination structure."""
    response = client.get("/api/v1/activities")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 12
    assert data["total_pages"] == 0


def test_public_discovery_excludes_unpublished_and_unapproved(client, db_session):
    """Ensure draft, pending, rejected, suspended, and inactive activities are completely hidden."""
    _, approved_provider = create_provider_user(db_session, email="p1@test.com")
    _, pending_provider = create_provider_user(
        db_session, email="p2@test.com", approval_status="pending"
    )

    # 1. Published and approved (MUST appear)
    act_published = create_activity(
        db_session,
        approved_provider.id,
        title="Published Activity",
        approval_status="approved",
        is_active=True,
    )

    # 2. Draft (MUST be hidden)
    create_activity(
        db_session,
        approved_provider.id,
        title="Draft Activity",
        approval_status="draft",
        is_active=False,
    )

    # 3. Pending admin review (MUST be hidden)
    create_activity(
        db_session,
        approved_provider.id,
        title="Pending Activity",
        approval_status="pending",
        is_active=False,
    )

    # 4. Rejected (MUST be hidden)
    create_activity(
        db_session,
        approved_provider.id,
        title="Rejected Activity",
        approval_status="rejected",
        is_active=False,
    )

    # 5. Suspended (MUST be hidden)
    create_activity(
        db_session,
        approved_provider.id,
        title="Suspended Activity",
        approval_status="suspended",
        is_active=False,
    )

    # 6. Approved but deactivated by provider (MUST be hidden)
    create_activity(
        db_session,
        approved_provider.id,
        title="Inactive Activity",
        approval_status="approved",
        is_active=False,
    )

    # 7. Approved activity under a pending provider (MUST be hidden)
    create_activity(
        db_session,
        pending_provider.id,
        title="Pending Provider Activity",
        approval_status="approved",
        is_active=True,
    )

    db_session.commit()

    response = client.get("/api/v1/activities")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == act_published.id
    assert data["items"][0]["title"] == "Published Activity"


def test_public_discovery_search_and_filters(client, db_session):
    """Test keyword search, category, district, city, price range, and duration filters."""
    _, provider = create_provider_user(db_session)

    # Seed 4 diverse published activities
    act_a = create_activity(
        db_session,
        provider.id,
        title="Sigiriya Heritage Walk",
        description="Ancient citadel cultural walking tour",
        category="cultural",
        district="Matale",
        city="Sigiriya",
        price=Decimal("2500.00"),
        duration_minutes=120,
    )
    act_b = create_activity(
        db_session,
        provider.id,
        title="Kitulgala White Water Rafting",
        description="Thrilling adventure on Kelani River rapids",
        category="adventure",
        district="Kegalle",
        city="Kitulgala",
        price=Decimal("7500.00"),
        duration_minutes=180,
    )
    act_c = create_activity(
        db_session,
        provider.id,
        title="Traditional Cooking Masterclass",
        description="Authentic spice grinding and village culinary session",
        category="local",
        district="Galle",
        city="Galle Fort",
        price=Decimal("4000.00"),
        duration_minutes=90,
    )
    act_d = create_activity(
        db_session,
        provider.id,
        title="Ella Tea Pluckers Experience",
        description="Community tea plantation tour and tasting",
        category="community",
        district="Badulla",
        city="Ella",
        price=Decimal("3000.00"),
        duration_minutes=150,
    )
    db_session.commit()

    # 1. Keyword search (case-insensitive substring)
    res = client.get("/api/v1/activities?search=rafting")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == act_b.id

    # 2. Category filter
    res = client.get("/api/v1/activities?category=local")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == act_c.id

    # 3. District filter
    res = client.get("/api/v1/activities?district=Matale")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == act_a.id

    # 4. City filter
    res = client.get("/api/v1/activities?city=Ella")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == act_d.id

    # 5. Price bounds filter
    res = client.get("/api/v1/activities?min_price=3500&max_price=8000")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    ids = {item["id"] for item in data["items"]}
    assert ids == {act_b.id, act_c.id}

    # 6. Duration bounds filter
    res = client.get("/api/v1/activities?min_duration=100&max_duration=160")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    ids = {item["id"] for item in data["items"]}
    assert ids == {act_a.id, act_d.id}


def test_public_discovery_invalid_ranges(client):
    """Test validation errors on inverted price and duration bounds."""
    # min_price > max_price
    res = client.get("/api/v1/activities?min_price=5000&max_price=2000")
    assert res.status_code == 422
    assert "min_price cannot exceed max_price" in res.json()["detail"]

    # min_duration > max_duration
    res = client.get("/api/v1/activities?min_duration=180&max_duration=60")
    assert res.status_code == 422
    assert "min_duration cannot exceed max_duration" in res.json()["detail"]


def test_public_discovery_pagination_and_sorting(client, db_session):
    """Test pagination math and deterministic ordering."""
    _, provider = create_provider_user(db_session)

    # Create 5 activities with distinct prices
    for i in range(1, 6):
        create_activity(
            db_session,
            provider.id,
            title=f"Activity {i}",
            price=Decimal(f"{i * 1000}.00"),
            duration_minutes=i * 30,
        )
    db_session.commit()

    # Pagination: page 1 with page_size 2
    res = client.get("/api/v1/activities?page=1&page_size=2")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 5
    assert data["total_pages"] == 3
    assert len(data["items"]) == 2

    # Pagination: page 3 with page_size 2
    res = client.get("/api/v1/activities?page=3&page_size=2")
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) == 1

    # Sorting: price_asc
    res = client.get("/api/v1/activities?sort_by=price_asc&page_size=10")
    data = res.json()
    prices = [float(item["price"]) for item in data["items"]]
    assert prices == sorted(prices)

    # Sorting: price_desc
    res = client.get("/api/v1/activities?sort_by=price_desc&page_size=10")
    data = res.json()
    prices = [float(item["price"]) for item in data["items"]]
    assert prices == sorted(prices, reverse=True)

    # Sorting: duration_asc
    res = client.get("/api/v1/activities?sort_by=duration_asc&page_size=10")
    data = res.json()
    durations = [item["duration_minutes"] for item in data["items"]]
    assert durations == sorted(durations)


def test_public_discovery_cover_image_resolution(client, db_session):
    """Test cover image resolution: is_cover flag takes precedence, else lowest display_order."""
    _, provider = create_provider_user(db_session)

    act1 = create_activity(db_session, provider.id, title="With Cover Flag")
    img1 = ActivityImage(
        activity_id=act1.id,
        image_url="https://images.example.test/img1.jpg",
        display_order=10,
        is_cover=False,
    )
    img2 = ActivityImage(
        activity_id=act1.id,
        image_url="https://images.example.test/cover.jpg",
        display_order=20,
        is_cover=True,  # Designated cover
    )
    db_session.add_all([img1, img2])

    act2 = create_activity(db_session, provider.id, title="Without Cover Flag")
    img3 = ActivityImage(
        activity_id=act2.id,
        image_url="https://images.example.test/display_10.jpg",
        display_order=10,
        is_cover=False,
    )
    img4 = ActivityImage(
        activity_id=act2.id,
        image_url="https://images.example.test/display_2.jpg",
        display_order=2,  # Lowest display_order
        is_cover=False,
    )
    db_session.add_all([img3, img4])

    act3 = create_activity(db_session, provider.id, title="No Images")
    db_session.commit()

    res = client.get("/api/v1/activities?page_size=10")
    assert res.status_code == 200
    data = res.json()
    items_map = {item["id"]: item for item in data["items"]}

    assert items_map[act1.id]["cover_image_url"] == "https://images.example.test/cover.jpg"
    assert items_map[act2.id]["cover_image_url"] == "https://images.example.test/display_2.jpg"
    assert items_map[act3.id]["cover_image_url"] is None


# ==============================================================================
# 3. Public Activity Details (A04, A06, A15, D02, D03)
# ==============================================================================


def test_public_activity_details_success(client, db_session):
    """Verify detailed activity view returns all required metadata and safe organizer info."""
    _, provider = create_provider_user(
        db_session,
        business_name="Lanka Safari Experts",
        phone="0779998877",
    )
    act = create_activity(
        db_session,
        provider.id,
        title="Yala National Park Safari",
        description="Full day jeep safari through Yala block 1.",
        category="adventure",
        district="Hambantota",
        city="Yala",
        meeting_point="Yala Palatupana Park Gate Entrance",
        latitude=Decimal("6.368600"),
        longitude=Decimal("81.520400"),
        duration_minutes=360,
        itinerary="06:00 Gate entry -> 10:00 Wildlife track -> 12:00 Exit",
        requirements="Passports required for gate entry. Carry hat & sunscreen.",
        safety_rules="Remain inside vehicle at all times. Do not feed wildlife.",
        required_equipment="Binoculars, zoom camera, 2L water.",
        minimum_age=5,
        price=Decimal("15000.00"),
    )
    img = ActivityImage(
        activity_id=act.id,
        image_url="https://images.example.test/yala1.jpg",
        alt_text="Leopard on rock",
        display_order=1,
        is_cover=True,
    )
    db_session.add(img)
    db_session.commit()

    res = client.get(f"/api/v1/activities/{act.id}")
    assert res.status_code == 200
    data = res.json()

    # Core metadata
    assert data["id"] == act.id
    assert data["title"] == "Yala National Park Safari"
    assert data["category"] == "adventure"
    assert data["duration_minutes"] == 360
    assert float(data["price"]) == 15000.0

    # Safety instructions (D02)
    assert "Remain inside vehicle" in data["safety_rules"]
    assert "Binoculars" in data["required_equipment"]
    assert "Passports required" in data["requirements"]
    assert data["minimum_age"] == 5

    # Meeting point & coordinates (A06, D03)
    assert data["meeting_point"] == "Yala Palatupana Park Gate Entrance"
    assert float(data["latitude"]) == 6.3686
    assert float(data["longitude"]) == 81.5204

    # Safe organizer contact channels (A15)
    assert data["organizer"] is not None
    assert data["organizer"]["business_name"] == "Lanka Safari Experts"
    assert data["organizer"]["phone"] == "0779998877"
    # Verify no private data leaked
    assert "nic_number" not in data["organizer"]
    assert "date_of_birth" not in data["organizer"]
    assert "selfie_image_path" not in data["organizer"]

    # Images gallery embedded (A05)
    assert len(data["images"]) == 1
    assert data["images"][0]["image_url"] == "https://images.example.test/yala1.jpg"
    assert data["images"][0]["alt_text"] == "Leopard on rock"


def test_public_activity_details_404_when_unpublished_or_missing(client, db_session):
    """Ensure unpublished activities or non-existent IDs return HTTP 404."""
    _, provider = create_provider_user(db_session)
    act_draft = create_activity(
        db_session,
        provider.id,
        title="Draft Activity",
        approval_status="draft",
        is_active=False,
    )
    act_suspended = create_activity(
        db_session,
        provider.id,
        title="Suspended Activity",
        approval_status="suspended",
        is_active=False,
    )
    db_session.commit()

    # Non-existent
    assert client.get("/api/v1/activities/99999").status_code == 404
    # Draft
    assert client.get(f"/api/v1/activities/{act_draft.id}").status_code == 404
    # Suspended
    assert client.get(f"/api/v1/activities/{act_suspended.id}").status_code == 404


# ==============================================================================
# 4. Public Photo Gallery (A05)
# ==============================================================================


def test_public_photo_gallery(client, db_session):
    """Verify /images endpoint returns images ordered by display_order."""
    _, provider = create_provider_user(db_session)
    act = create_activity(db_session, provider.id, title="Gallery Activity")

    img1 = ActivityImage(
        activity_id=act.id,
        image_url="https://images.example.test/second.jpg",
        display_order=2,
    )
    img2 = ActivityImage(
        activity_id=act.id,
        image_url="https://images.example.test/first.jpg",
        display_order=1,
    )
    db_session.add_all([img1, img2])
    db_session.commit()

    res = client.get(f"/api/v1/activities/{act.id}/images")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert data[0]["image_url"] == "https://images.example.test/first.jpg"
    assert data[1]["image_url"] == "https://images.example.test/second.jpg"


def test_public_photo_gallery_empty_and_404(client, db_session):
    """Activity with no images returns [], unpublished activity returns 404."""
    _, provider = create_provider_user(db_session)
    act_published = create_activity(db_session, provider.id, title="No Images")
    act_draft = create_activity(
        db_session,
        provider.id,
        title="Draft",
        approval_status="draft",
        is_active=False,
    )
    db_session.commit()

    # Published activity without images -> 200, []
    res = client.get(f"/api/v1/activities/{act_published.id}/images")
    assert res.status_code == 200
    assert res.json() == []

    # Unpublished activity -> 404
    res = client.get(f"/api/v1/activities/{act_draft.id}/images")
    assert res.status_code == 404


# ==============================================================================
# 5. Route Non-Conflict Safety
# ==============================================================================


def test_route_collision_safety(client, db_session):
    """Verify /categories does not collide with /{activity_id}."""
    res = client.get("/api/v1/activities/categories")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
