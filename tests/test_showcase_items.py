"""
Automated tests for M05.2 – Products & Services Showcase API endpoints.
Tests public showcase discovery, listing showcase catalog, item details,
approved artisan creation, ownership validation, updates, soft delete, and exclusion rules.
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


# Helper fixtures
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
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    user = User(
        full_name="Master Sunil",
        email="sunil.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Sunil Heritage Crafts",
        provider_type="artisan",
        phone="+94771112233",
        district="Kandy",
        description="Authentic traditional handicrafts.",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        is_verified=True,
        provider_profile_id=profile.id,
        title="Sunil Kandy Mask Workshop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def second_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    user = User(
        full_name="Kamala Batik",
        email="kamala.batik@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Kamala Batik Gallery",
        provider_type="artisan",
        phone="+94774445566",
        district="Galle",
        description="Southern silk batiks.",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        is_verified=True,
        provider_profile_id=profile.id,
        title="Galle Fort Batik Studio",
        craft_category="Batik & Textiles",
        district="Galle",
        city="Galle",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def homestay_provider_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Nimal Host",
        email="nimal.homestay@example.com",
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
        business_name="Pending Woodcraft",
        provider_type="artisan",
        phone="+94779998877",
        district="Matale",
        approval_status="pending",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# Public Showcase Tests
# ==============================================================================

def test_list_showcase_items_empty(client: TestClient):
    res = client.get("/api/v1/showcase-items")
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_showcase_items_filtering_and_exclusion(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1 = approved_artisan_user
    _, _, listing2 = second_artisan_user

    # Inactive listing to test exclusion requirement
    inactive_listing = ArtisanListing(
        provider_profile_id=listing1.provider_profile_id,
        title="Inactive Workshop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=False,
    )
    db_session.add(inactive_listing)
    db_session.commit()
    db_session.refresh(inactive_listing)

    # 1. Active product on active listing
    item1 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Handcarved Kaduru Peacock Mask",
        item_type="product",
        description="Authentic traditional peacock mask with natural lacquer.",
        price=Decimal("4500.00"),
        materials="Kaduru Wood, Natural Lacquer",
        is_available=True,
        is_featured=True,
        is_active=True,
    )
    # 2. Active service on active listing
    item2 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Traditional Woodcarving Demonstration",
        item_type="service",
        description="Live demonstration of ancient chisel techniques.",
        price=Decimal("2000.00"),
        duration_minutes=60,
        is_available=True,
        is_featured=False,
        is_active=True,
    )
    # 3. Active product on second active listing
    item3 = ShowcaseItem(
        artisan_listing_id=listing2.id,
        title="Galle Fort Silk Wall Hanging",
        item_type="product",
        description="Intricate hand-waxed silk batik wall hanging.",
        price=Decimal("8500.00"),
        materials="Pure Silk, Organic Dyes",
        is_available=True,
        is_featured=False,
        is_active=True,
    )
    # 4. Inactive item on active listing (must be excluded from public results)
    item_inactive = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Discontinued Brass Lamp",
        item_type="product",
        is_active=False,
    )
    # 5. Active item on INACTIVE listing (must be excluded from public results)
    item_on_inactive_listing = ShowcaseItem(
        artisan_listing_id=inactive_listing.id,
        title="Mask on Closed Listing",
        item_type="product",
        is_active=True,
    )

    db_session.add_all([item1, item2, item3, item_inactive, item_on_inactive_listing])
    db_session.commit()

    # Default list: should return only the 3 active items on active listings
    res = client.get("/api/v1/showcase-items")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3

    # Filter by item_type = "service"
    res_service = client.get("/api/v1/showcase-items", params={"item_type": "service"})
    assert res_service.status_code == 200
    assert res_service.json()["total"] == 1
    assert res_service.json()["items"][0]["title"] == "Traditional Woodcarving Demonstration"

    # Filter by item_type = "product"
    res_prod = client.get("/api/v1/showcase-items", params={"item_type": "product"})
    assert res_prod.status_code == 200
    assert res_prod.json()["total"] == 2

    # Filter by search
    res_search = client.get("/api/v1/showcase-items", params={"search": "Peacock"})
    assert res_search.status_code == 200
    assert res_search.json()["total"] == 1
    assert res_search.json()["items"][0]["title"] == "Handcarved Kaduru Peacock Mask"

    # Filter by artisan_listing_id
    res_listing = client.get("/api/v1/showcase-items", params={"artisan_listing_id": listing2.id})
    assert res_listing.status_code == 200
    assert res_listing.json()["total"] == 1
    assert res_listing.json()["items"][0]["title"] == "Galle Fort Silk Wall Hanging"

    # Filter by is_featured
    res_featured = client.get("/api/v1/showcase-items", params={"is_featured": "true"})
    assert res_featured.status_code == 200
    assert res_featured.json()["total"] == 1
    assert res_featured.json()["items"][0]["is_featured"] is True

    # Pagination
    res_page = client.get("/api/v1/showcase-items", params={"page": 1, "page_size": 2})
    assert res_page.status_code == 200
    assert len(res_page.json()["items"]) == 2
    assert res_page.json()["total"] == 3
    assert res_page.json()["total_pages"] == 2


def test_get_showcase_item_details_public(client: TestClient, db_session, approved_artisan_user):
    _, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Carved Wooden Elephant",
        item_type="product",
        description="Detailed figurine with ivory-style tusks.",
        price=Decimal("3500.00"),
        materials="Teak wood",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Active item returns 200
    res = client.get(f"/api/v1/showcase-items/{item.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == item.id
    assert data["title"] == "Carved Wooden Elephant"
    assert data["listing_title"] == listing.title

    # Inactive item returns 404
    item.is_active = False
    db_session.commit()
    res_inactive = client.get(f"/api/v1/showcase-items/{item.id}")
    assert res_inactive.status_code == 404

    # Nonexistent id returns 404
    res_404 = client.get("/api/v1/showcase-items/99999")
    assert res_404.status_code == 404


def test_get_listing_showcase_items_public(client: TestClient, db_session, approved_artisan_user):
    _, _, listing = approved_artisan_user

    item1 = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Workshop Product 1",
        item_type="product",
        is_active=True,
    )
    item2 = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Workshop Service 1",
        item_type="service",
        duration_minutes=45,
        is_active=True,
    )
    db_session.add_all([item1, item2])
    db_session.commit()

    # Query items for this listing
    res = client.get(f"/api/v1/artisan-listings/{listing.id}/showcase-items")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2

    # Query for nonexistent listing returns 404
    res_404 = client.get("/api/v1/artisan-listings/99999/showcase-items")
    assert res_404.status_code == 404


# ==============================================================================
# Protected Endpoints & Authorization Tests
# ==============================================================================

def test_create_showcase_item_unauthenticated(client: TestClient, approved_artisan_user):
    _, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Unauthenticated Mask",
        "item_type": "product",
    }
    res = client.post("/api/v1/showcase-items", json=payload)
    assert res.status_code == 401


def test_create_showcase_item_traveler_forbidden(client: TestClient, traveler_user, approved_artisan_user):
    _, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Traveler Showcase Item",
        "item_type": "product",
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 403
    assert "provider role required" in res.json()["detail"].lower()


def test_create_showcase_item_pending_provider_forbidden(client: TestClient, pending_artisan_user, approved_artisan_user):
    user, _ = pending_artisan_user
    _, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Pending Showcase Item",
        "item_type": "product",
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 403
    assert "pending" in res.json()["detail"].lower()


def test_create_showcase_item_non_artisan_provider_forbidden(client: TestClient, homestay_provider_user, approved_artisan_user):
    user, _ = homestay_provider_user
    _, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Homestay Showcase Item",
        "item_type": "product",
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 403
    assert "only artisan service providers" in res.json()["detail"].lower()


def test_create_showcase_item_other_artisan_listing_forbidden(client: TestClient, approved_artisan_user, second_artisan_user):
    _, _, listing1 = approved_artisan_user
    user2, _, _ = second_artisan_user

    # Artisan 2 attempts to add an item to Artisan 1's listing
    payload = {
        "artisan_listing_id": listing1.id,
        "title": "Hijacked Showcase Item",
        "item_type": "product",
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(user2),
    )
    assert res.status_code == 403
    assert "do not own this artisan listing" in res.json()["detail"].lower()


def test_create_showcase_item_owner_success(client: TestClient, approved_artisan_user):
    user, _, listing = approved_artisan_user

    # 1. Create a product item
    prod_payload = {
        "artisan_listing_id": listing.id,
        "title": "Master Sunil Demon Mask (Gara Yaka)",
        "item_type": "product",
        "description": "Authentic wood carving of Gara Yaka.",
        "price": 6500.00,
        "materials": "Kaduru wood, natural lacquer",
        "is_available": True,
    }
    prod_res = client.post(
        "/api/v1/showcase-items",
        json=prod_payload,
        headers=auth_header(user),
    )
    assert prod_res.status_code == 201
    prod_data = prod_res.json()
    assert prod_data["title"] == "Master Sunil Demon Mask (Gara Yaka)"
    assert prod_data["item_type"] == "product"
    assert prod_data["is_active"] is True
    assert prod_data["listing_title"] == listing.title

    # 2. Create a service/workshop item
    serv_payload = {
        "artisan_listing_id": listing.id,
        "title": "Mask Carving & Painting Workshop",
        "item_type": "service",
        "description": "2-hour hands-on carving and painting workshop.",
        "price": 4000.00,
        "duration_minutes": 120,
        "is_available": True,
    }
    serv_res = client.post(
        "/api/v1/showcase-items",
        json=serv_payload,
        headers=auth_header(user),
    )
    assert serv_res.status_code == 201
    serv_data = serv_res.json()
    assert serv_data["item_type"] == "service"
    assert serv_data["duration_minutes"] == 120


def test_get_my_showcase_items(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    user1, _, listing1 = approved_artisan_user
    user2, _, listing2 = second_artisan_user

    item1 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Sunil Item 1",
        item_type="product",
        is_active=True,
    )
    item2 = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Sunil Inactive Item 2",
        item_type="product",
        is_active=False,
    )
    item3 = ShowcaseItem(
        artisan_listing_id=listing2.id,
        title="Kamala Item",
        item_type="product",
        is_active=True,
    )
    db_session.add_all([item1, item2, item3])
    db_session.commit()

    # User 1 should see only their items (including inactive)
    res = client.get("/api/v1/showcase-items/me/items", headers=auth_header(user1))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    titles = [i["title"] for i in items]
    assert "Sunil Item 1" in titles
    assert "Sunil Inactive Item 2" in titles
    assert "Kamala Item" not in titles


def test_update_showcase_item_owner_success(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Original Title",
        item_type="product",
        price=Decimal("1000.00"),
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    update_payload = {
        "title": "Updated Showcase Title",
        "price": 1500.00,
        "description": "Updated story and background.",
    }
    res = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json=update_payload,
        headers=auth_header(user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Updated Showcase Title"
    assert float(data["price"]) == 1500.00
    assert data["description"] == "Updated story and background."


def test_update_showcase_item_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1 = approved_artisan_user
    user2, _, _ = second_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Sunil Product",
        item_type="product",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    res = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"title": "Hacked Title"},
        headers=auth_header(user2),
    )
    assert res.status_code == 403
    assert "do not own this showcase item" in res.json()["detail"].lower()


def test_soft_delete_showcase_item_owner_success(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Item to Delete",
        item_type="product",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    res = client.delete(
        f"/api/v1/showcase-items/{item.id}",
        headers=auth_header(user),
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    # Verify state in DB
    db_session.refresh(item)
    assert item.is_active is False

    # Verify public lookup returns 404
    public_res = client.get(f"/api/v1/showcase-items/{item.id}")
    assert public_res.status_code == 404


def test_soft_delete_showcase_item_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1 = approved_artisan_user
    user2, _, _ = second_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Sunil Item",
        item_type="product",
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    res = client.delete(
        f"/api/v1/showcase-items/{item.id}",
        headers=auth_header(user2),
    )
    assert res.status_code == 403
    assert "do not own this showcase item" in res.json()["detail"].lower()


# ==============================================================================
# Phase P0 Step 2: Product Stock & Inventory API Tests
# ==============================================================================

def test_create_showcase_item_with_inventory_fields_defaults(client: TestClient, approved_artisan_user):
    user, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Default Inventory Product",
        "item_type": "product",
        "price": 2500.00,
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["stock_quantity"] == 0
    assert data["sku"] is None
    assert data["is_custom_order_available"] is False


def test_create_showcase_item_with_explicit_inventory_fields(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Master Mask With Full Inventory",
        "item_type": "product",
        "price": 12500.00,
        "stock_quantity": 35,
        "sku": "MASK-SUNIL-001",
        "is_custom_order_available": True,
    }
    res = client.post(
        "/api/v1/showcase-items",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["stock_quantity"] == 35
    assert data["sku"] == "MASK-SUNIL-001"
    assert data["is_custom_order_available"] is True

    # Verify directly in DB
    item_in_db = db_session.query(ShowcaseItem).filter(ShowcaseItem.id == data["id"]).first()
    assert item_in_db is not None
    assert item_in_db.stock_quantity == 35
    assert item_in_db.sku == "MASK-SUNIL-001"
    assert item_in_db.is_custom_order_available is True


def test_create_showcase_item_invalid_stock_quantity(client: TestClient, approved_artisan_user):
    user, _, listing = approved_artisan_user

    # Negative stock
    res_neg = client.post(
        "/api/v1/showcase-items",
        json={
            "artisan_listing_id": listing.id,
            "title": "Negative Stock Item",
            "stock_quantity": -5,
        },
        headers=auth_header(user),
    )
    assert res_neg.status_code == 422

    # Float stock with fraction
    res_float = client.post(
        "/api/v1/showcase-items",
        json={
            "artisan_listing_id": listing.id,
            "title": "Float Stock Item",
            "stock_quantity": 4.5,
        },
        headers=auth_header(user),
    )
    assert res_float.status_code == 422

    # Boolean stock
    res_bool = client.post(
        "/api/v1/showcase-items",
        json={
            "artisan_listing_id": listing.id,
            "title": "Bool Stock Item",
            "stock_quantity": True,
        },
        headers=auth_header(user),
    )
    assert res_bool.status_code == 422

    # String stock
    res_str = client.post(
        "/api/v1/showcase-items",
        json={
            "artisan_listing_id": listing.id,
            "title": "String Stock Item",
            "stock_quantity": "ten",
        },
        headers=auth_header(user),
    )
    assert res_str.status_code == 422


def test_update_showcase_item_partial_inventory_preserves_other_fields(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Original Preserved Mask",
        item_type="product",
        price=Decimal("8000.00"),
        currency="LKR",
        stock_quantity=20,
        sku="ORIG-SKU-99",
        is_custom_order_available=False,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # 1. Partial update: Only update stock_quantity
    res1 = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": 12},
        headers=auth_header(user),
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["stock_quantity"] == 12
    assert data1["title"] == "Original Preserved Mask"
    assert float(data1["price"]) == 8000.00
    assert data1["sku"] == "ORIG-SKU-99"
    assert data1["is_custom_order_available"] is False

    # 2. Partial update: Only update is_custom_order_available
    res2 = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"is_custom_order_available": True},
        headers=auth_header(user),
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["is_custom_order_available"] is True
    assert data2["stock_quantity"] == 12
    assert data2["sku"] == "ORIG-SKU-99"

    # 3. Partial update: Only update sku
    res3 = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"sku": "NEW-UPDATED-SKU"},
        headers=auth_header(user),
    )
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["sku"] == "NEW-UPDATED-SKU"
    assert data3["stock_quantity"] == 12
    assert data3["is_custom_order_available"] is True


def test_update_showcase_item_stock_to_zero_allowed(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Item Soon Out of Stock",
        item_type="product",
        stock_quantity=5,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    res = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": 0},
        headers=auth_header(user),
    )
    assert res.status_code == 200
    assert res.json()["stock_quantity"] == 0


def test_update_showcase_item_invalid_inventory_values(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Validation Item",
        item_type="product",
        stock_quantity=10,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Negative stock
    res_neg = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": -1},
        headers=auth_header(user),
    )
    assert res_neg.status_code == 422

    # Null stock (cannot be null)
    res_null = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": None},
        headers=auth_header(user),
    )
    assert res_null.status_code == 422

    # Boolean stock
    res_bool = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": True},
        headers=auth_header(user),
    )
    assert res_bool.status_code == 422

    # Null is_custom_order_available (cannot be null)
    res_null_custom = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"is_custom_order_available": None},
        headers=auth_header(user),
    )
    assert res_null_custom.status_code == 422


def test_showcase_item_inventory_in_retrieval_and_discovery(client: TestClient, db_session, approved_artisan_user):
    user, _, listing = approved_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Showcase Mask with Inventory",
        item_type="product",
        price=Decimal("7500.00"),
        currency="LKR",
        stock_quantity=18,
        sku="DISC-MASK-18",
        is_custom_order_available=True,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # 1. Detail endpoint
    res_detail = client.get(f"/api/v1/showcase-items/{item.id}")
    assert res_detail.status_code == 200
    data_detail = res_detail.json()
    assert data_detail["stock_quantity"] == 18
    assert data_detail["sku"] == "DISC-MASK-18"
    assert data_detail["is_custom_order_available"] is True

    # 2. Authenticated provider's items endpoint
    res_me = client.get("/api/v1/showcase-items/me/items", headers=auth_header(user))
    assert res_me.status_code == 200
    items_me = res_me.json()
    matched_me = [i for i in items_me if i["id"] == item.id]
    assert len(matched_me) == 1
    assert matched_me[0]["stock_quantity"] == 18
    assert matched_me[0]["sku"] == "DISC-MASK-18"
    assert matched_me[0]["is_custom_order_available"] is True

    # 3. Public catalog list endpoint
    res_list = client.get("/api/v1/showcase-items")
    assert res_list.status_code == 200
    items_list = res_list.json()["items"]
    matched_list = [i for i in items_list if i["id"] == item.id]
    assert len(matched_list) == 1
    assert matched_list[0]["stock_quantity"] == 18
    assert matched_list[0]["sku"] == "DISC-MASK-18"
    assert matched_list[0]["is_custom_order_available"] is True

    # 4. Listing specific showcase endpoint
    res_listing_items = client.get(f"/api/v1/artisan-listings/{listing.id}/showcase-items")
    assert res_listing_items.status_code == 200
    listing_items = res_listing_items.json()["items"]
    matched_listing = [i for i in listing_items if i["id"] == item.id]
    assert len(matched_listing) == 1
    assert matched_listing[0]["stock_quantity"] == 18
    assert matched_listing[0]["sku"] == "DISC-MASK-18"
    assert matched_listing[0]["is_custom_order_available"] is True


def test_update_inventory_authorization(client: TestClient, db_session, approved_artisan_user, second_artisan_user, traveler_user):
    user1, _, listing1 = approved_artisan_user
    user2, _, _ = second_artisan_user

    item = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Sunil Protected Item",
        item_type="product",
        stock_quantity=50,
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # 1. Unauthenticated update attempt -> 401
    res_unauth = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": 0},
    )
    assert res_unauth.status_code == 401

    # 2. Traveler update attempt -> 403
    res_traveler = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": 0},
        headers=auth_header(traveler_user),
    )
    assert res_traveler.status_code == 403

    # 3. Other artisan update attempt -> 403
    res_other = client.put(
        f"/api/v1/showcase-items/{item.id}",
        json={"stock_quantity": 0},
        headers=auth_header(user2),
    )
    assert res_other.status_code == 403
    assert "do not own this showcase item" in res_other.json()["detail"].lower()

    # Verify stock remained untouched in DB
    db_session.refresh(item)
    assert item.stock_quantity == 50

