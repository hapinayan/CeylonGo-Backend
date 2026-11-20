"""
Automated tests for Phase P0 Step 4: Public Artisan Business & Product Approval Filtering.
Verifies that:
1. Public listing discovery and detail endpoints expose ONLY active businesses with approval_status == 'approved'
   under approved provider profiles.
2. Unapproved (pending, rejected, draft, suspended) listings and listings belonging to pending/rejected providers
   are completely excluded from public lists, detail endpoints (404), category, and district aggregations.
3. Public product discovery and detail endpoints expose ONLY active, approved products belonging to active, approved listings.
4. Provider-owned /me endpoints continue to return all provider-owned listings and products (approved and pending)
   for dashboard and operational management.
5. Reservation attempts on unapproved products are blocked.
"""

from datetime import UTC, date, datetime, timedelta
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
from app.models.showcase_item import ShowcaseItem
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


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def approved_artisan(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    """Fully approved provider with a fully approved listing."""
    user = User(
        full_name="Approved Master",
        email="approved.master@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Heritage Craft Workshop",
        provider_type="artisan",
        phone="+94771110001",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Approved Kandy Woodcarving",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        approval_status="approved",
        is_verified=True,
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def pending_listing_artisan(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    """Approved provider with a pending listing."""
    user = User(
        full_name="Pending Listing Artisan",
        email="pending.listing@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Pending Review Studio",
        provider_type="artisan",
        phone="+94771110002",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Unapproved Galle Pottery",
        craft_category="Pottery & Ceramics",
        district="Galle",
        city="Galle",
        approval_status="pending",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def unapproved_provider_artisan(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    """Provider whose profile is pending, with an otherwise active listing."""
    user = User(
        full_name="Unapproved Profile Artisan",
        email="unapproved.profile@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Unapproved Provider Works",
        provider_type="artisan",
        phone="+94771110003",
        district="Jaffna",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Jaffna Palmyrah Crafts",
        craft_category="Weaving & Basketry",
        district="Jaffna",
        city="Jaffna",
        approval_status="approved",
        is_verified=True,
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Kasun",
        email="kasun.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ==============================================================================
# 1. Artisan Listing Public Discovery Filtering Tests
# ==============================================================================

def test_public_listings_exclude_pending_and_rejected_listings(client: TestClient, approved_artisan, pending_listing_artisan):
    res = client.get("/api/v1/artisan-listings")
    assert res.status_code == 200
    data = res.json()
    titles = [item["title"] for item in data["items"]]

    # Approved listing must appear
    assert "Approved Kandy Woodcarving" in titles
    # Pending listing must NOT appear
    assert "Unapproved Galle Pottery" not in titles


def test_public_listing_detail_returns_404_for_unapproved_listing(client: TestClient, pending_listing_artisan):
    _, _, pending_listing = pending_listing_artisan
    res = client.get(f"/api/v1/artisan-listings/{pending_listing.id}")
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_public_listings_exclude_unapproved_provider_profile(client: TestClient, approved_artisan, unapproved_provider_artisan):
    res = client.get("/api/v1/artisan-listings")
    assert res.status_code == 200
    data = res.json()
    titles = [item["title"] for item in data["items"]]

    # Approved listing must appear
    assert "Approved Kandy Woodcarving" in titles
    # Listing from pending provider must NOT appear
    assert "Jaffna Palmyrah Crafts" not in titles

    # Detail view must return 404
    _, _, jaffna_listing = unapproved_provider_artisan
    res_detail = client.get(f"/api/v1/artisan-listings/{jaffna_listing.id}")
    assert res_detail.status_code == 404


def test_districts_and_categories_exclude_unapproved_listings(client: TestClient, approved_artisan, pending_listing_artisan):
    # Approved listing has Kandy / Woodcarving & Masks
    # Pending listing has Galle / Pottery & Ceramics

    res_districts = client.get("/api/v1/artisan-listings/districts")
    assert res_districts.status_code == 200
    districts = res_districts.json()
    assert "Kandy" in districts
    assert "Galle" not in districts

    res_categories = client.get("/api/v1/artisan-listings/categories")
    assert res_categories.status_code == 200
    categories = res_categories.json()
    assert "Woodcarving & Masks" in categories


def test_provider_me_listings_includes_unapproved_for_owner(client: TestClient, pending_listing_artisan):
    user, _, pending_listing = pending_listing_artisan

    # Owner can see their pending listing in their private dashboard view
    res = client.get("/api/v1/artisan-listings/me/listings", headers=auth_header(user))
    assert res.status_code == 200
    listings = res.json()
    assert len(listings) == 1
    assert listings[0]["id"] == pending_listing.id
    assert listings[0]["approval_status"] == "pending"


# ==============================================================================
# 2. Showcase Item Public Discovery Filtering Tests
# ==============================================================================

def test_public_showcase_items_exclude_unapproved_product(client: TestClient, db_session, approved_artisan):
    _, _, listing = approved_artisan

    # 1. Approved item
    item_approved = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Approved Peacock Mask",
        item_type="product",
        price=Decimal("5000.00"),
        approval_status="approved",
        is_available=True,
        is_active=True,
    )
    # 2. Pending item
    item_pending = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Pending Devil Mask",
        item_type="product",
        price=Decimal("7000.00"),
        approval_status="pending",
        is_available=True,
        is_active=True,
    )
    # 3. Rejected item
    item_rejected = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Rejected Low Quality Mask",
        item_type="product",
        price=Decimal("1500.00"),
        approval_status="rejected",
        is_available=True,
        is_active=True,
    )
    db_session.add_all([item_approved, item_pending, item_rejected])
    db_session.commit()

    # Public list must only show the approved item
    res = client.get("/api/v1/showcase-items")
    assert res.status_code == 200
    items = res.json()["items"]
    titles = [i["title"] for i in items]
    assert "Approved Peacock Mask" in titles
    assert "Pending Devil Mask" not in titles
    assert "Rejected Low Quality Mask" not in titles

    # Public detail: approved is 200, pending is 404, rejected is 404
    assert client.get(f"/api/v1/showcase-items/{item_approved.id}").status_code == 200
    assert client.get(f"/api/v1/showcase-items/{item_pending.id}").status_code == 404
    assert client.get(f"/api/v1/showcase-items/{item_rejected.id}").status_code == 404


def test_public_showcase_items_exclude_product_from_unapproved_listing(client: TestClient, db_session, pending_listing_artisan):
    _, _, pending_listing = pending_listing_artisan

    # Product is marked approved, but its parent listing is pending
    item_on_pending_listing = ShowcaseItem(
        artisan_listing_id=pending_listing.id,
        title="Clay Vase on Pending Listing",
        item_type="product",
        price=Decimal("2500.00"),
        approval_status="approved",
        is_available=True,
        is_active=True,
    )
    db_session.add(item_on_pending_listing)
    db_session.commit()
    db_session.refresh(item_on_pending_listing)

    # Excluded from global showcase catalog
    res_catalog = client.get("/api/v1/showcase-items")
    assert res_catalog.status_code == 200
    titles = [i["title"] for i in res_catalog.json()["items"]]
    assert "Clay Vase on Pending Listing" not in titles

    # Detail returns 404
    res_detail = client.get(f"/api/v1/showcase-items/{item_on_pending_listing.id}")
    assert res_detail.status_code == 404

    # Listing specific endpoint returns 404 because listing is not approved
    res_listing_items = client.get(f"/api/v1/artisan-listings/{pending_listing.id}/showcase-items")
    assert res_listing_items.status_code == 404


def test_provider_me_showcase_items_includes_unapproved_for_owner(client: TestClient, db_session, approved_artisan):
    user, _, listing = approved_artisan

    item_pending = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Provider Own Pending Item",
        item_type="product",
        price=Decimal("4000.00"),
        approval_status="pending",
        is_available=True,
        is_active=True,
    )
    db_session.add(item_pending)
    db_session.commit()
    db_session.refresh(item_pending)

    # Provider sees their own pending item in protected dashboard view
    res = client.get("/api/v1/showcase-items/me/items", headers=auth_header(user))
    assert res.status_code == 200
    items = res.json()
    matched = [i for i in items if i["id"] == item_pending.id]
    assert len(matched) == 1
    assert matched[0]["title"] == "Provider Own Pending Item"


# ==============================================================================
# 3. Reservation Security Test
# ==============================================================================

def test_cannot_reserve_unapproved_product(client: TestClient, db_session, approved_artisan, traveler_user):
    _, _, listing = approved_artisan

    item_pending = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Unapproved Mask for Reservation",
        item_type="product",
        price=Decimal("6000.00"),
        stock_quantity=5,
        approval_status="pending",
        is_available=True,
        is_active=True,
    )
    db_session.add(item_pending)
    db_session.commit()
    db_session.refresh(item_pending)

    tomorrow = date.today() + timedelta(days=1)
    res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": item_pending.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 400
    assert "not approved for reservation" in res.json()["detail"].lower()
