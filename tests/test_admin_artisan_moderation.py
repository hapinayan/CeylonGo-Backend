"""
Automated tests for Phase P1 Step 1: Admin Artisan & Showcase Product Moderation.
Tests:
1. New listings and showcase products enter 'pending' approval status by default.
2. Admin can list pending listings and products.
3. Admin can approve listings and products, transitioning them to 'approved' and publishing them.
4. Admin can reject listings with a stated reason; rejection reason is stored and returned to owner.
5. Admin can suspend active listings, immediately revoking public visibility.
6. Admin can reject or hide showcase products.
7. Role-based access control: Non-admins (travelers, providers) cannot access moderation endpoints (403).
8. Providers cannot self-approve listings or products via update payloads.
9. Reservations cannot be made on unapproved (pending or rejected) products.
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
def admin_user(db_session) -> User:
    admin = User(
        full_name="Platform Admin",
        email="admin.artisan@ceylongo.com",
        password_hash=hash_password("AdminSecurePass123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


@pytest.fixture
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Silva",
        email="silva.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Silva Traditional Woodcrafts",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def traveler_user(db_session) -> User:
    traveler = User(
        full_name="Traveler Kasun",
        email="kasun.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    return traveler


# ==============================================================================
# 1. Listing Creation Defaults & Moderation Tests
# ==============================================================================

def test_new_artisan_listing_defaults_to_pending(client: TestClient, db_session, approved_artisan_user):
    user, profile = approved_artisan_user

    payload = {
        "title": "Silva Newly Created Workshop",
        "craft_category": "Woodcarving & Masks",
        "description": "Hand-carved masks and timber handicrafts.",
        "district": "Kandy",
        "city": "Pilimathalawa",
        "phone": "+94771234567",
    }
    res = client.post(
        "/api/v1/artisan-listings",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 201
    data = res.json()
    listing_id = data["id"]
    assert data["approval_status"] == "pending"
    assert data["verification_status"] == "unverified"

    # Verify state in database
    db_item = db_session.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    assert db_item is not None
    assert db_item.approval_status == "pending"

    # Public discovery MUST NOT expose pending listing
    res_public = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert res_public.status_code == 404

    res_list = client.get("/api/v1/artisan-listings", params={"search": "Silva Newly"})
    assert res_list.status_code == 200
    assert res_list.json()["total"] == 0

    # Provider MUST be able to view their pending listing
    res_me = client.get("/api/v1/artisan-listings/me/listings", headers=auth_header(user))
    assert res_me.status_code == 200
    matched = [item for item in res_me.json() if item["id"] == listing_id]
    assert len(matched) == 1
    assert matched[0]["approval_status"] == "pending"


def test_admin_approve_artisan_listing(client: TestClient, db_session, approved_artisan_user, admin_user):
    user, profile = approved_artisan_user

    # Provider creates listing (pending)
    res_create = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Pending Mask Workshop", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_create.json()["id"]

    # Admin approves listing
    res_approve = client.post(
        f"/api/v1/admin/artisan/listings/{listing_id}/approve",
        headers=auth_header(admin_user),
    )
    assert res_approve.status_code == 200
    approve_data = res_approve.json()
    assert approve_data["success"] is True
    assert approve_data["approval_status"] == "approved"
    assert approve_data["id"] == listing_id

    # Verify in DB
    db_item = db_session.query(ArtisanListing).filter(ArtisanListing.id == listing_id).first()
    assert db_item.approval_status == "approved"
    assert db_item.reviewed_by == admin_user.id
    assert db_item.reviewed_at is not None

    # Public discovery now exposes the approved listing
    res_public = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert res_public.status_code == 200
    assert res_public.json()["title"] == "Pending Mask Workshop"

    # Cannot approve already approved listing
    res_dup = client.post(
        f"/api/v1/admin/artisan/listings/{listing_id}/approve",
        headers=auth_header(admin_user),
    )
    assert res_dup.status_code == 400


def test_admin_reject_artisan_listing_with_reason(client: TestClient, db_session, approved_artisan_user, admin_user):
    user, profile = approved_artisan_user

    # Provider creates listing (pending)
    res_create = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Workshop Lacking Registration", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_create.json()["id"]

    # Admin rejects listing with reason
    rejection_payload = {"reason": "Business registration document is illegible. Please re-upload clearer copy."}
    res_reject = client.post(
        f"/api/v1/admin/artisan/listings/{listing_id}/reject",
        json=rejection_payload,
        headers=auth_header(admin_user),
    )
    assert res_reject.status_code == 200
    reject_data = res_reject.json()
    assert reject_data["success"] is True
    assert reject_data["approval_status"] == "rejected"
    assert reject_data["rejection_reason"] == rejection_payload["reason"]

    # Public discovery hides rejected listing
    res_public = client.get(f"/api/v1/artisan-listings/{listing_id}")
    assert res_public.status_code == 404

    # Provider can view rejection reason in /me
    res_me = client.get("/api/v1/artisan-listings/me/listings", headers=auth_header(user))
    assert res_me.status_code == 200
    matched = [item for item in res_me.json() if item["id"] == listing_id]
    assert len(matched) == 1
    assert matched[0]["approval_status"] == "rejected"
    assert matched[0]["rejection_reason"] == rejection_payload["reason"]


def test_admin_suspend_artisan_listing(client: TestClient, db_session, approved_artisan_user, admin_user):
    user, profile = approved_artisan_user

    # Provider creates listing and admin approves it
    res_create = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Active Workshop To Suspend", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_create.json()["id"]
    client.post(f"/api/v1/admin/artisan/listings/{listing_id}/approve", headers=auth_header(admin_user))

    # Admin suspends listing
    res_suspend = client.post(
        f"/api/v1/admin/artisan/listings/{listing_id}/suspend",
        json={"reason": "Customer fraud complaints under formal investigation."},
        headers=auth_header(admin_user),
    )
    assert res_suspend.status_code == 200
    assert res_suspend.json()["approval_status"] == "suspended"

    # Immediately hidden from public
    assert client.get(f"/api/v1/artisan-listings/{listing_id}").status_code == 404


def test_admin_list_listings_filtering(client: TestClient, approved_artisan_user, admin_user):
    user, _ = approved_artisan_user

    # Create 2 listings
    client.post(
        "/api/v1/artisan-listings",
        json={"title": "Pending Listing One", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    client.post(
        "/api/v1/artisan-listings",
        json={"title": "Pending Listing Two", "craft_category": "Batik & Textiles", "district": "Galle", "city": "Galle"},
        headers=auth_header(user),
    )

    # Admin lists pending listings
    res = client.get("/api/v1/admin/artisan/listings?status=pending", headers=auth_header(admin_user))
    assert res.status_code == 200
    listings = res.json()
    titles = [l["title"] for l in listings]
    assert "Pending Listing One" in titles
    assert "Pending Listing Two" in titles


# ==============================================================================
# 2. Product Creation Defaults & Moderation Tests
# ==============================================================================

def test_new_showcase_product_defaults_to_pending(client: TestClient, db_session, approved_artisan_user, admin_user):
    user, _ = approved_artisan_user

    # 1. Create and approve parent listing
    res_listing = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Approved Studio for Products", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_listing.json()["id"]
    client.post(f"/api/v1/admin/artisan/listings/{listing_id}/approve", headers=auth_header(admin_user))

    # 2. Provider creates product
    prod_payload = {
        "artisan_listing_id": listing_id,
        "title": "Silva Handcarved Gara Mask",
        "item_type": "product",
        "price": 8500.00,
        "stock_quantity": 4,
    }
    res_prod = client.post(
        "/api/v1/showcase-items",
        json=prod_payload,
        headers=auth_header(user),
    )
    assert res_prod.status_code == 201
    prod_data = res_prod.json()
    item_id = prod_data["id"]
    assert prod_data["approval_status"] == "pending"

    # Public catalog must NOT expose pending product
    assert client.get(f"/api/v1/showcase-items/{item_id}").status_code == 404
    res_catalog = client.get("/api/v1/showcase-items", params={"search": "Gara Mask"})
    assert res_catalog.status_code == 200
    assert res_catalog.json()["total"] == 0

    # Provider can view their own pending product
    res_my_items = client.get("/api/v1/showcase-items/me/items", headers=auth_header(user))
    assert res_my_items.status_code == 200
    matched = [i for i in res_my_items.json() if i["id"] == item_id]
    assert len(matched) == 1
    assert matched[0]["approval_status"] == "pending"


def test_admin_approve_and_reject_showcase_product(client: TestClient, approved_artisan_user, admin_user):
    user, _ = approved_artisan_user

    # Setup approved listing
    res_listing = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Studio For Moderation Tests", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_listing.json()["id"]
    client.post(f"/api/v1/admin/artisan/listings/{listing_id}/approve", headers=auth_header(admin_user))

    # Provider creates 2 products
    res1 = client.post(
        "/api/v1/showcase-items",
        json={"artisan_listing_id": listing_id, "title": "Product To Approve", "item_type": "product", "price": 3000.00},
        headers=auth_header(user),
    )
    item1_id = res1.json()["id"]

    res2 = client.post(
        "/api/v1/showcase-items",
        json={"artisan_listing_id": listing_id, "title": "Product To Reject", "item_type": "product", "price": 4000.00},
        headers=auth_header(user),
    )
    item2_id = res2.json()["id"]

    # 1. Admin approves item 1
    res_app = client.post(f"/api/v1/admin/artisan/products/{item1_id}/approve", headers=auth_header(admin_user))
    assert res_app.status_code == 200
    assert res_app.json()["approval_status"] == "approved"
    assert client.get(f"/api/v1/showcase-items/{item1_id}").status_code == 200

    # 2. Admin rejects item 2
    res_rej = client.post(
        f"/api/v1/admin/artisan/products/{item2_id}/reject",
        json={"reason": "Prohibited timber material specified."},
        headers=auth_header(admin_user),
    )
    assert res_rej.status_code == 200
    assert res_rej.json()["approval_status"] == "rejected"
    assert client.get(f"/api/v1/showcase-items/{item2_id}").status_code == 404

    # 3. Admin hides approved item 1
    res_hide = client.post(f"/api/v1/admin/artisan/products/{item1_id}/hide", headers=auth_header(admin_user))
    assert res_hide.status_code == 200
    assert client.get(f"/api/v1/showcase-items/{item1_id}").status_code == 404


# ==============================================================================
# 3. Security, Authorization & Reservation Boundary Tests
# ==============================================================================

def test_non_admin_cannot_access_admin_endpoints(client: TestClient, approved_artisan_user, traveler_user):
    user, _ = approved_artisan_user

    # Traveler 403
    assert client.get("/api/v1/admin/artisan/listings", headers=auth_header(traveler_user)).status_code == 403
    assert client.post("/api/v1/admin/artisan/listings/1/approve", headers=auth_header(traveler_user)).status_code == 403

    # Provider 403
    assert client.get("/api/v1/admin/artisan/listings", headers=auth_header(user)).status_code == 403
    assert client.post("/api/v1/admin/artisan/listings/1/approve", headers=auth_header(user)).status_code == 403
    assert client.post("/api/v1/admin/artisan/products/1/approve", headers=auth_header(user)).status_code == 403

    # Unauthenticated 401
    assert client.get("/api/v1/admin/artisan/listings").status_code == 401


def test_provider_cannot_self_approve_via_update(client: TestClient, approved_artisan_user):
    user, _ = approved_artisan_user

    # Create listing (pending)
    res_listing = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Pending Listing Bypass Test", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_listing.json()["id"]

    # Provider attempts to update approval_status to "approved"
    res_update = client.put(
        f"/api/v1/artisan-listings/{listing_id}",
        json={"title": "Updated Title", "approval_status": "approved"},
        headers=auth_header(user),
    )
    assert res_update.status_code == 200
    # Field must remain pending
    assert res_update.json()["approval_status"] == "pending"


def test_reservation_creation_blocked_for_pending_product(client: TestClient, approved_artisan_user, admin_user, traveler_user):
    user, _ = approved_artisan_user

    # Setup approved listing
    res_listing = client.post(
        "/api/v1/artisan-listings",
        json={"title": "Approved Studio For Res", "craft_category": "Woodcarving & Masks", "district": "Kandy", "city": "Kandy"},
        headers=auth_header(user),
    )
    listing_id = res_listing.json()["id"]
    client.post(f"/api/v1/admin/artisan/listings/{listing_id}/approve", headers=auth_header(admin_user))

    # Product is pending
    res_prod = client.post(
        "/api/v1/showcase-items",
        json={"artisan_listing_id": listing_id, "title": "Pending Mask Item", "item_type": "product", "price": 5000.00, "stock_quantity": 5},
        headers=auth_header(user),
    )
    prod_id = res_prod.json()["id"]

    # Traveler attempts reservation -> blocked (400)
    tomorrow = date.today() + timedelta(days=1)
    res_res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": prod_id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res_res.status_code == 400
    assert "not approved for reservation" in res_res.json()["detail"].lower()

