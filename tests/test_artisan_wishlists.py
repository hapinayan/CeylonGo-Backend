"""
Automated Integration Tests for Artisan Wishlist & Favorites API (Phase P1 Step 11).

Covers all 12 required scenarios:
1. Add Artisan Business to Wishlist (HTTP 201 Created, rich listing metadata).
2. Duplicate prevention (HTTP 409 Conflict, database unique constraint).
3. Remove saved Business (HTTP 204 No Content, check returns is_saved=False).
4. List authenticated Traveler's Wishlist (HTTP 200 OK, paginated, ordered by created_at DESC).
5. Cross-user ownership isolation (Traveler A cannot see or delete Traveler B's wishlist items).
6. Unauthorized & unauthenticated access (401 Unauthorized for guests, 403 Forbidden for providers/admins).
7. Pending or rejected listing visibility (HTTP 404 Not Found, never leaks unapproved businesses).
8. Inactive listing visibility & dynamic deactivation handling (400 on add; is_available=False on list).
9. Shared listing reference integration (reusing dbo.listing_references and dbo.wishlists from Migration 027).
10. Cross-module wishlist compatibility (artisan wishlists do not collide with eco or homestay wishlists).
11. Empty Wishlist behavior (empty items list, total=0).
12. Product Favorite support and documented limitation (compatibility endpoint & rejection of product favorite).
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
from app.models.shared import (
    ListingReference,
    Review,
    Wishlist,
)
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_shared_reference import (
    ARTISAN_MODULE_TYPE,
    sync_artisan_listing_reference,
)

# In-memory SQLite engine for tests
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
        session.rollback()
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


# ==============================================================================
# User Fixtures
# ==============================================================================

def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


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


@pytest.fixture
def second_traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Sarah",
        email="sarah.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def provider_user(db_session) -> User:
    user = User(
        full_name="Provider Nimal",
        email="nimal.provider@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
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
def approved_artisan_listing(db_session, provider_user) -> ArtisanListing:
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Nimal Handcrafts",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Kandy Traditional Masks",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        approval_status="approved",
        is_active=True,
        cover_image_url="https://images.ceylongo.com/masks.jpg",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    sync_artisan_listing_reference(db_session, listing)
    return listing


@pytest.fixture
def second_artisan_listing(db_session, provider_user) -> ArtisanListing:
    profile = (
        db_session.query(ProviderProfile)
        .filter(ProviderProfile.user_id == provider_user.id)
        .first()
    )
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Galle Lace & Textiles",
        craft_category="Textiles & Handloom",
        district="Galle",
        city="Galle Fort",
        approval_status="approved",
        is_active=True,
        cover_image_url="https://images.ceylongo.com/lace.jpg",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    sync_artisan_listing_reference(db_session, listing)
    return listing


# ==============================================================================
# 1. Add Artisan Business to Wishlist (Scenario 1)
# ==============================================================================

def test_add_artisan_listing_to_wishlist_success(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # Add a sample review to verify rating enrichment in wishlist
    listing_ref = sync_artisan_listing_reference(db_session, approved_artisan_listing)
    review = Review(
        listing_reference_id=listing_ref.id,
        user_id=traveler_user.id,
        rating=Decimal("4.80"),
        title="Great artisan shop",
        is_verified_booking=True,
        is_published=True,
    )
    db_session.add(review)
    db_session.commit()

    payload = {"notes": "Must visit when in Kandy!"}
    res = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        json=payload,
        headers=auth_header(traveler_user),
    )

    assert res.status_code == 201
    data = res.json()
    assert data["id"] is not None
    assert data["user_id"] == traveler_user.id
    assert data["artisan_listing_id"] == approved_artisan_listing.id
    assert data["notes"] == "Must visit when in Kandy!"
    assert data["is_available"] is True
    assert data["listing"] is not None
    assert data["listing"]["title"] == "Kandy Traditional Masks"
    assert data["listing"]["district"] == "Kandy"
    assert float(data["listing"]["average_rating"]) == 4.80
    assert data["listing"]["total_reviews"] == 1

    # Verify check endpoint
    check_res = client.get(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist/check",
        headers=auth_header(traveler_user),
    )
    assert check_res.status_code == 200
    assert check_res.json()["is_saved"] is True
    assert check_res.json()["wishlist_id"] == data["id"]


# ==============================================================================
# 2. Duplicate Prevention (Scenario 2)
# ==============================================================================

def test_add_artisan_listing_to_wishlist_duplicate_prevention(
    client: TestClient,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # First save -> 201
    res1 = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        json={"notes": "First attempt"},
        headers=auth_header(traveler_user),
    )
    assert res1.status_code == 201

    # Second save -> 409 Conflict
    res2 = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        json={"notes": "Second attempt"},
        headers=auth_header(traveler_user),
    )
    assert res2.status_code == 409
    assert "Artisan business is already in your wishlist" in res2.json()["detail"]


# ==============================================================================
# 3. Remove Saved Business (Scenario 3)
# ==============================================================================

def test_remove_artisan_listing_from_wishlist_success(
    client: TestClient,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # Save listing
    client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )

    # Delete listing -> 204 No Content
    del_res = client.delete(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )
    assert del_res.status_code == 204

    # Check status -> is_saved == False
    check_res = client.get(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist/check",
        headers=auth_header(traveler_user),
    )
    assert check_res.status_code == 200
    assert check_res.json()["is_saved"] is False

    # Deleting again -> 404 Not Found
    del_res_again = client.delete(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )
    assert del_res_again.status_code == 404


# ==============================================================================
# 4. List Authenticated Traveler's Wishlist (Scenario 4)
# ==============================================================================

def test_list_authenticated_traveler_wishlist(
    client: TestClient,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
    second_artisan_listing: ArtisanListing,
):
    client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        json={"notes": "Kandy stop"},
        headers=auth_header(traveler_user),
    )
    client.post(
        f"/api/v1/artisan-listings/{second_artisan_listing.id}/wishlist",
        json={"notes": "Galle stop"},
        headers=auth_header(traveler_user),
    )

    res = client.get(
        "/api/v1/artisan-wishlists/me?page=1&page_size=10",
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    # Ordered by created_at DESC (most recent first)
    assert data["items"][0]["artisan_listing_id"] == second_artisan_listing.id
    assert data["items"][0]["notes"] == "Galle stop"
    assert data["items"][1]["artisan_listing_id"] == approved_artisan_listing.id
    assert data["items"][1]["notes"] == "Kandy stop"


# ==============================================================================
# 5. Cross-User Ownership Isolation (Scenario 5)
# ==============================================================================

def test_cross_user_wishlist_isolation(
    client: TestClient,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_listing: ArtisanListing,
    second_artisan_listing: ArtisanListing,
):
    # Traveler Kasun saves Kandy listing
    client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )

    # Traveler Sarah saves Galle listing
    client.post(
        f"/api/v1/artisan-listings/{second_artisan_listing.id}/wishlist",
        headers=auth_header(second_traveler_user),
    )

    # Kasun only sees Kandy listing
    kasun_res = client.get("/api/v1/artisan-wishlists/me", headers=auth_header(traveler_user))
    assert kasun_res.status_code == 200
    assert kasun_res.json()["total"] == 1
    assert kasun_res.json()["items"][0]["artisan_listing_id"] == approved_artisan_listing.id

    # Sarah only sees Galle listing
    sarah_res = client.get("/api/v1/artisan-wishlists/me", headers=auth_header(second_traveler_user))
    assert sarah_res.status_code == 200
    assert sarah_res.json()["total"] == 1
    assert sarah_res.json()["items"][0]["artisan_listing_id"] == second_artisan_listing.id

    # Sarah cannot delete Kasun's saved Kandy listing
    del_res = client.delete(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(second_traveler_user),
    )
    assert del_res.status_code == 404


# ==============================================================================
# 6. Unauthorized and Unauthenticated Access (Scenario 6)
# ==============================================================================

def test_unauthorized_and_unauthenticated_wishlist_access(
    client: TestClient,
    provider_user: User,
    admin_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # Unauthenticated -> 401
    unauth_post = client.post(f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist")
    assert unauth_post.status_code == 401

    unauth_get = client.get("/api/v1/artisan-wishlists/me")
    assert unauth_get.status_code == 401

    # Provider role -> 403 Forbidden
    prov_res = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(provider_user),
    )
    assert prov_res.status_code == 403
    assert "traveler role required" in prov_res.json()["detail"]

    # Admin role -> 403 Forbidden
    admin_res = client.get("/api/v1/artisan-wishlists/me", headers=auth_header(admin_user))
    assert admin_res.status_code == 403


# ==============================================================================
# 7. Pending or Rejected Listing Visibility (Scenario 7)
# ==============================================================================

@pytest.mark.parametrize("status_name", ["pending", "rejected", "suspended"])
def test_cannot_add_unapproved_artisan_listing_to_wishlist(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
    status_name: str,
):
    approved_artisan_listing.approval_status = status_name
    db_session.commit()

    res = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 404
    assert "Artisan listing not found" in res.json()["detail"]


# ==============================================================================
# 8. Inactive Listing & Dynamic Deactivation Handling (Scenario 8)
# ==============================================================================

def test_inactive_listing_addition_and_dynamic_deactivation_handling(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # Case 1: Cannot add an inactive listing
    approved_artisan_listing.is_active = False
    db_session.commit()

    res_inactive = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )
    assert res_inactive.status_code == 400
    assert "This artisan business is currently inactive" in res_inactive.json()["detail"]

    # Case 2: Listing is saved when active, then deactivated later
    approved_artisan_listing.is_active = True
    db_session.commit()

    client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )

    # Provider deactivates listing
    approved_artisan_listing.is_active = False
    db_session.commit()

    # Query with available_only=False -> included with is_available=False, listing=None
    list_all = client.get(
        "/api/v1/artisan-wishlists/me?available_only=false",
        headers=auth_header(traveler_user),
    )
    assert list_all.status_code == 200
    assert list_all.json()["total"] == 1
    assert list_all.json()["items"][0]["is_available"] is False
    assert list_all.json()["items"][0]["listing"] is None

    # Query with available_only=True -> filtered out cleanly
    list_available = client.get(
        "/api/v1/artisan-wishlists/me?available_only=true",
        headers=auth_header(traveler_user),
    )
    assert list_available.status_code == 200
    assert list_available.json()["total"] == 0
    assert len(list_available.json()["items"]) == 0


# ==============================================================================
# 9. Shared Listing Reference Integration (Scenario 9)
# ==============================================================================

def test_shared_listing_reference_integration(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    res = client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 201
    wish_id = res.json()["id"]

    # Verify db record directly references listing_reference
    db_wish = db_session.query(Wishlist).filter(Wishlist.id == wish_id).first()
    assert db_wish is not None
    assert db_wish.listing_reference is not None
    assert db_wish.listing_reference.module_type == ARTISAN_MODULE_TYPE
    assert db_wish.listing_reference.target_id == approved_artisan_listing.id


# ==============================================================================
# 10. Cross-Module Wishlist Compatibility (Scenario 10)
# ==============================================================================

def test_cross_module_wishlist_isolation_in_shared_infrastructure(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_listing: ArtisanListing,
):
    # 1. Save an Artisan listing
    client.post(
        f"/api/v1/artisan-listings/{approved_artisan_listing.id}/wishlist",
        headers=auth_header(traveler_user),
    )

    # 2. Simulate another module (e.g., homestay) creating a listing reference and wishlist entry
    homestay_ref = ListingReference(
        module_type="homestay",
        target_id=999,
        provider_profile_id=1,
        title="Ella Mountain View Homestay",
        district="Badulla",
        city="Ella",
        is_active=True,
    )
    db_session.add(homestay_ref)
    db_session.commit()
    db_session.refresh(homestay_ref)

    homestay_wish = Wishlist(
        user_id=traveler_user.id,
        listing_reference_id=homestay_ref.id,
        notes="Homestay note",
    )
    db_session.add(homestay_wish)
    db_session.commit()

    # 3. Call artisan wishlist endpoint -> only returns artisan business
    res = client.get("/api/v1/artisan-wishlists/me", headers=auth_header(traveler_user))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["artisan_listing_id"] == approved_artisan_listing.id


# ==============================================================================
# 11. Empty Wishlist Behavior (Scenario 11)
# ==============================================================================

def test_empty_wishlist_behavior(
    client: TestClient,
    traveler_user: User,
):
    res = client.get("/api/v1/artisan-wishlists/me", headers=auth_header(traveler_user))
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["total_pages"] == 0


# ==============================================================================
# 12. Product Favorite Support and Documented Limitation (Scenario 12)
# ==============================================================================

def test_product_favorite_architectural_limitation_and_rejection(
    client: TestClient,
    traveler_user: User,
):
    # 1. Query compatibility documentation
    compat_res = client.get("/api/v1/artisan-wishlists/product-favorites-compatibility")
    assert compat_res.status_code == 200
    compat_data = compat_res.json()
    assert compat_data["supported"] is False
    assert "CK_listing_references_type" in compat_data["reason"]
    assert "parent Artisan Business" in compat_data["recommended_alternative"]

    # 2. Attempt product-level favorite -> HTTP 400 Bad Request
    prod_res = client.post(
        "/api/v1/artisan-products/123/favorite",
        headers=auth_header(traveler_user),
    )
    assert prod_res.status_code == 400
    assert "Product-level favorites are not supported by the shared database schema" in prod_res.json()["detail"]
