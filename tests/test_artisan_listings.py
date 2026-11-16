"""
Automated tests for M05.1 – Local Business & Artisan Listings API endpoints.
Tests public discovery, search/filtering, pagination, provider authorization, ownership, and admin status updates.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

# In-memory SQLite engine for tests
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
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
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


# Helper fixtures for users and providers
@pytest.fixture
def traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Jane",
        email="traveler.jane@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def admin_user(db_session) -> User:
    user = User(
        full_name="Admin Boss",
        email="admin.boss@example.com",
        password_hash=hash_password("Password123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Master Artisan Sunil",
        email="artisan.sunil@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Sunil Traditional Woodcraft",
        provider_type="artisan",
        phone="+94771112233",
        district="Kandy",
        description="Authentic handmade Kandyan woodcraft and masks.",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def second_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Kamala",
        email="artisan.kamala@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Kamala Batik Studio",
        provider_type="artisan",
        phone="+94774445566",
        district="Galle",
        description="Traditional southern handmade silk batiks.",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def pending_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Pending Artisan",
        email="pending.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Pending Craft Shop",
        provider_type="artisan",
        phone="+94779998877",
        district="Matale",
        description="Pending artisan profile",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def homestay_provider_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Homestay Host Nimal",
        email="homestay.nimal@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Ella Hilltop Homestay",
        provider_type="homestay",
        phone="+94775556677",
        district="Badulla",
        description="Cozy homestay in Ella",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# Public Discovery & Search Tests
# ==============================================================================

def test_list_artisan_listings_empty(client: TestClient):
    response = client.get("/api/v1/artisan-listings")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["total_pages"] == 0


def test_list_and_filter_artisan_listings(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, profile1 = approved_artisan_user
    _, profile2 = second_artisan_user

    # Create active listings
    listing1 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Kandyan Brass and Woodcraft",
        craft_category="Woodcarving & Masks",
        description="Authentic handmade Kandyan dancing masks.",
        district="Kandy",
        city="Pilimathalawa",
        latitude=Decimal("7.250000"),
        longitude=Decimal("80.550000"),
        is_active=True,
        is_featured=True,
    )
    listing2 = ArtisanListing(
        provider_profile_id=profile2.id,
        title="Galle Fort Handmade Batiks",
        craft_category="Batik & Textiles",
        description="Eco-dyed cotton and silk batiks in Galle Fort.",
        district="Galle",
        city="Galle",
        latitude=Decimal("6.032800"),
        longitude=Decimal("80.217000"),
        is_active=True,
        is_featured=False,
    )
    # Inactive listing (should not appear in public lists)
    listing3 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Closed Wood Shop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=False,
    )

    db_session.add_all([listing1, listing2, listing3])
    db_session.commit()

    # 1. Default list should return both active listings
    res = client.get("/api/v1/artisan-listings")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # 2. Filter by district
    res_district = client.get("/api/v1/artisan-listings", params={"district": "Galle"})
    assert res_district.status_code == 200
    data_district = res_district.json()
    assert data_district["total"] == 1
    assert data_district["items"][0]["title"] == "Galle Fort Handmade Batiks"

    # 3. Filter by category
    res_cat = client.get("/api/v1/artisan-listings", params={"category": "Batik & Textiles"})
    assert res_cat.status_code == 200
    assert res_cat.json()["total"] == 1

    # 4. Search query
    res_search = client.get("/api/v1/artisan-listings", params={"search": "masks"})
    assert res_search.status_code == 200
    assert res_search.json()["total"] == 1
    assert res_search.json()["items"][0]["title"] == "Kandyan Brass and Woodcraft"

    # 5. Filter by featured
    res_featured = client.get("/api/v1/artisan-listings", params={"is_featured": "true"})
    assert res_featured.status_code == 200
    assert res_featured.json()["total"] == 1
    assert res_featured.json()["items"][0]["is_featured"] is True

    # 6. Pagination
    res_page = client.get("/api/v1/artisan-listings", params={"page": 1, "page_size": 1})
    assert res_page.status_code == 200
    page_data = res_page.json()
    assert len(page_data["items"]) == 1
    assert page_data["total"] == 2
    assert page_data["total_pages"] == 2


def test_get_craft_categories(client: TestClient, db_session, approved_artisan_user):
    _, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Mask Workshop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()

    res = client.get("/api/v1/artisan-listings/categories")
    assert res.status_code == 200
    categories = res.json()
    assert isinstance(categories, list)
    assert "Woodcarving & Masks" in categories
    assert "Batik & Textiles" in categories


def test_get_active_districts(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, profile1 = approved_artisan_user
    _, profile2 = second_artisan_user

    listing1 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Kandy Craft",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    listing2 = ArtisanListing(
        provider_profile_id=profile2.id,
        title="Galle Craft",
        craft_category="Batik & Textiles",
        district="Galle",
        city="Galle",
        is_active=True,
    )
    db_session.add_all([listing1, listing2])
    db_session.commit()

    res = client.get("/api/v1/artisan-listings/districts")
    assert res.status_code == 200
    districts = res.json()
    assert "Galle" in districts
    assert "Kandy" in districts


def test_get_artisan_listing_details_public(client: TestClient, db_session, approved_artisan_user):
    _, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Kandy Woodcarving Workshop",
        craft_category="Woodcarving & Masks",
        description="Authentic traditional carving.",
        district="Kandy",
        city="Embekke",
        phone="+94771234567",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    # Valid ID
    res = client.get(f"/api/v1/artisan-listings/{listing.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == listing.id
    assert data["title"] == "Kandy Woodcarving Workshop"
    assert data["provider_business_name"] == "Sunil Traditional Woodcraft"

    # Inactive listing returns 404 for public
    listing.is_active = False
    db_session.commit()
    res_inactive = client.get(f"/api/v1/artisan-listings/{listing.id}")
    assert res_inactive.status_code == 404

    # Nonexistent ID returns 404
    res_404 = client.get("/api/v1/artisan-listings/99999")
    assert res_404.status_code == 404


# ==============================================================================
# Provider Permissions & CRUD Tests
# ==============================================================================

def test_create_listing_requires_authentication(client: TestClient):
    payload = {
        "title": "Unauthenticated Studio",
        "craft_category": "Pottery & Ceramics",
        "district": "Colombo",
        "city": "Colombo",
    }
    response = client.post("/api/v1/artisan-listings", json=payload)
    assert response.status_code == 401


def test_create_listing_traveler_forbidden(client: TestClient, traveler_user):
    payload = {
        "title": "Traveler Trying to List",
        "craft_category": "Pottery & Ceramics",
        "district": "Colombo",
        "city": "Colombo",
    }
    response = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert response.status_code == 403


def test_create_listing_pending_provider_forbidden(client: TestClient, pending_artisan_user):
    user, _ = pending_artisan_user
    payload = {
        "title": "Pending Trying to List",
        "craft_category": "Pottery & Ceramics",
        "district": "Colombo",
        "city": "Colombo",
    }
    response = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(user),
    )
    assert response.status_code == 403
    assert "pending" in response.json()["detail"].lower()


def test_create_listing_non_artisan_provider_forbidden(client: TestClient, homestay_provider_user):
    user, _ = homestay_provider_user
    payload = {
        "title": "Homestay Trying Artisan Listing",
        "craft_category": "Batik & Textiles",
        "district": "Badulla",
        "city": "Ella",
    }
    response = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(user),
    )
    assert response.status_code == 403
    assert "only artisan service providers" in response.json()["detail"].lower()


def test_create_listing_approved_artisan_success(client: TestClient, approved_artisan_user):
    user, profile = approved_artisan_user
    payload = {
        "title": "Master Sunil Wooden Elephant Workshop",
        "craft_category": "Woodcarving & Masks",
        "description": "Family-run heritage workshop for over 40 years.",
        "district": "Kandy",
        "city": "Embekke",
        "address": "No. 12 Temple Road",
        "latitude": 7.218500,
        "longitude": 80.569400,
        "email": "sunilwood@example.com",
        "opening_hours": "Mon-Sat: 9:00 AM - 5:00 PM",
    }
    response = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(user),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Master Sunil Wooden Elephant Workshop"
    assert data["provider_profile_id"] == profile.id
    assert data["is_active"] is True
    assert data["is_verified"] is False
    assert data["is_featured"] is False
    assert data["provider_business_name"] == profile.business_name


def test_get_my_listings(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    user1, profile1 = approved_artisan_user
    user2, profile2 = second_artisan_user

    listing1 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Sunil Listing 1",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    listing2 = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Sunil Inactive Listing 2",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=False,
    )
    listing3 = ArtisanListing(
        provider_profile_id=profile2.id,
        title="Kamala Listing",
        craft_category="Batik & Textiles",
        district="Galle",
        city="Galle",
        is_active=True,
    )
    db_session.add_all([listing1, listing2, listing3])
    db_session.commit()

    # User 1 should see only their 2 listings (including the inactive one)
    res = client.get("/api/v1/artisan-listings/me/listings", headers=auth_header(user1))
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    titles = [item["title"] for item in data]
    assert "Sunil Listing 1" in titles
    assert "Sunil Inactive Listing 2" in titles
    assert "Kamala Listing" not in titles


def test_update_listing_owner_success(client: TestClient, db_session, approved_artisan_user):
    user, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Original Title",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    update_payload = {
        "title": "Updated Brand Title",
        "city": "Peradeniya",
        "description": "Updated workshop description.",
    }
    res = client.put(
        f"/api/v1/artisan-listings/{listing.id}",
        json=update_payload,
        headers=auth_header(user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Updated Brand Title"
    assert data["city"] == "Peradeniya"
    assert data["description"] == "Updated workshop description."


def test_update_listing_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, profile1 = approved_artisan_user
    user2, _ = second_artisan_user

    listing = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Sunil Original",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    res = client.put(
        f"/api/v1/artisan-listings/{listing.id}",
        json={"title": "Hacked Title"},
        headers=auth_header(user2),
    )
    assert res.status_code == 403
    assert "do not own this listing" in res.json()["detail"].lower()


def test_soft_delete_listing_owner(client: TestClient, db_session, approved_artisan_user):
    user, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Listing to Deactivate",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    res = client.delete(
        f"/api/v1/artisan-listings/{listing.id}",
        headers=auth_header(user),
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    # Verify state in DB
    db_session.refresh(listing)
    assert listing.is_active is False


def test_delete_listing_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, profile1 = approved_artisan_user
    user2, _ = second_artisan_user

    listing = ArtisanListing(
        provider_profile_id=profile1.id,
        title="Sunil Listing",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    res = client.delete(
        f"/api/v1/artisan-listings/{listing.id}",
        headers=auth_header(user2),
    )
    assert res.status_code == 403
    assert "do not own this listing" in res.json()["detail"].lower()


# ==============================================================================
# Admin Verification & Status Update Tests
# ==============================================================================

def test_admin_status_update_forbidden_for_non_admin(client: TestClient, db_session, approved_artisan_user):
    user, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Artisan Listing",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    res = client.patch(
        f"/api/v1/artisan-listings/{listing.id}/status",
        json={"is_verified": True},
        headers=auth_header(user),
    )
    assert res.status_code == 403


def test_admin_status_update_success(client: TestClient, db_session, admin_user, approved_artisan_user):
    _, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Traditional Wood Shop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=True,
        is_verified=False,
        is_featured=False,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    res = client.patch(
        f"/api/v1/artisan-listings/{listing.id}/status",
        json={"is_verified": True, "is_featured": True},
        headers=auth_header(admin_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["is_verified"] is True
    assert data["is_featured"] is True


# ==============================================================================
# Full End-to-End Sample Artisan Listing Lifecycle & Ownership Test
# ==============================================================================

def test_sample_artisan_listing_end_to_end_flow(client: TestClient, traveler_user, approved_artisan_user, second_artisan_user):
    """
    Validates complete end-to-end workflow for M05.1 with isolated test fixtures:
    1. Traveler 403 boundary check.
    2. Authenticated artisan provider creates sample listing.
    3. Public search, district, and category discovery.
    4. Detailed public listing view.
    5. Ownership protection against other providers (PUT/DELETE 403).
    6. Owner update (PUT 200).
    7. Owner soft delete (DELETE 200).
    8. Public 404 verification after deactivation while preserved in provider's own catalog.
    """
    artisan_user, artisan_profile = approved_artisan_user
    other_artisan_user, _ = second_artisan_user

    sample_listing_payload = {
        "title": "Ceylon Heritage Brass & Wood Workshop",
        "craft_category": "Woodcarving & Masks",
        "description": "Traditional handmade masks, wood carvings, and brass lamps from master craftsmen.",
        "district": "Kandy",
        "city": "Pilimathalawa",
        "address": "45 Temple Road, Pilimathalawa",
        "latitude": 7.258900,
        "longitude": 80.572100,
        "phone": "+94771234567",
        "email": "heritage.crafts@example.com",
        "website_url": "https://ceylonheritagecrafts.example.com",
        "opening_hours": "Mon-Sat: 8:30 AM - 6:00 PM",
        "cover_image_url": "https://images.example.com/crafts/kandy-workshop.jpg",
    }

    # 1. Traveler receives 403 Forbidden
    traveler_res = client.post(
        "/api/v1/artisan-listings",
        json=sample_listing_payload,
        headers=auth_header(traveler_user),
    )
    assert traveler_res.status_code == 403
    assert "provider role required" in traveler_res.json()["detail"].lower()

    # 2. Approved artisan provider creates sample listing (201 Created)
    create_res = client.post(
        "/api/v1/artisan-listings",
        json=sample_listing_payload,
        headers=auth_header(artisan_user),
    )
    assert create_res.status_code == 201
    created = create_res.json()
    listing_id = created["id"]
    assert created["title"] == "Ceylon Heritage Brass & Wood Workshop"
    assert created["provider_profile_id"] == artisan_profile.id
    assert created["provider_business_name"] == artisan_profile.business_name
    assert created["is_active"] is True
    assert created["is_verified"] is False
    assert created["is_featured"] is False

    # 3. Public search & discovery (GET /api/v1/artisan-listings)
    public_res = client.get("/api/v1/artisan-listings", params={"search": "Heritage"})
    assert public_res.status_code == 200
    search_data = public_res.json()
    assert search_data["total"] == 1
    assert search_data["items"][0]["id"] == listing_id

    # Filter by correct district (Kandy) -> found
    dist_res = client.get("/api/v1/artisan-listings", params={"district": "Kandy"})
    assert dist_res.status_code == 200
    assert dist_res.json()["total"] == 1

    # Filter by different district (Galle) -> 0 results
    diff_dist_res = client.get("/api/v1/artisan-listings", params={"district": "Galle"})
    assert diff_dist_res.status_code == 200
    assert diff_dist_res.json()["total"] == 0

    # Filter by craft category -> found
    cat_res = client.get("/api/v1/artisan-listings", params={"category": "Woodcarving & Masks"})
    assert cat_res.status_code == 200
    assert cat_res.json()["total"] == 1

    # 4. Public detailed view (GET /api/v1/artisan-listings/{id})
    detail_res = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == listing_id
    assert detail["address"] == "45 Temple Road, Pilimathalawa"
    assert detail["phone"] == "+94771234567"
    assert detail["provider_business_name"] == artisan_profile.business_name

    # 5. Ownership protection: Second provider cannot edit (403 Forbidden)
    forbidden_put = client.put(
        f"/api/v1/artisan-listings/{listing_id}",
        json={"title": "Hijacked Listing Title"},
        headers=auth_header(other_artisan_user),
    )
    assert forbidden_put.status_code == 403
    assert "do not own this listing" in forbidden_put.json()["detail"].lower()

    # 6. Authorized owner updates listing (200 OK)
    update_res = client.put(
        f"/api/v1/artisan-listings/{listing_id}",
        json={
            "title": "Ceylon Heritage Brass & Traditional Masks Studio",
            "opening_hours": "Mon-Sun: 8:00 AM - 7:00 PM",
        },
        headers=auth_header(artisan_user),
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["title"] == "Ceylon Heritage Brass & Traditional Masks Studio"
    assert updated["opening_hours"] == "Mon-Sun: 8:00 AM - 7:00 PM"

    # 7. Ownership protection: Second provider cannot delete (403 Forbidden)
    forbidden_del = client.delete(
        f"/api/v1/artisan-listings/{listing_id}",
        headers=auth_header(other_artisan_user),
    )
    assert forbidden_del.status_code == 403
    assert "do not own this listing" in forbidden_del.json()["detail"].lower()

    # 8. Authorized owner soft-deletes listing (200 OK)
    del_res = client.delete(
        f"/api/v1/artisan-listings/{listing_id}",
        headers=auth_header(artisan_user),
    )
    assert del_res.status_code == 200
    assert del_res.json()["is_active"] is False

    # 9. Public traveler can no longer view the deactivated listing (404)
    public_after_del = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert public_after_del.status_code == 404

    # Public catalog no longer shows the deactivated listing (total = 0)
    public_catalog_after_del = client.get("/api/v1/artisan-listings")
    assert public_catalog_after_del.json()["total"] == 0

    # 10. Owner can still see it in their private dashboard catalog (/me/listings)
    my_listings_res = client.get(
        "/api/v1/artisan-listings/me/listings",
        headers=auth_header(artisan_user),
    )
    assert my_listings_res.status_code == 200
    my_listings = my_listings_res.json()
    assert len(my_listings) == 1
    assert my_listings[0]["id"] == listing_id
    assert my_listings[0]["is_active"] is False
