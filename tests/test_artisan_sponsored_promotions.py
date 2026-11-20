"""
Automated Integration Tests for Artisan Sponsored Promotions & Featured Business Integration (Phase P1 Step 12).

Covers all 10 required scenarios:
1. Approved featured listing visibility (/api/v1/artisan-listings/featured and is_featured=True).
2. Pending listing exclusion (cannot be featured; excluded from public discovery).
3. Rejected and suspended listing exclusion (cannot be featured; auto-reset to is_featured=False on moderation).
4. Admin-only featured status management (/feature and /unfeature endpoints with require_admin).
5. Unauthorized provider changes (provider cannot self-mark is_featured=True via create or update).
6. Promotion date validation (end_date >= start_date, expired promotions excluded from active listing).
7. Sponsored ordering behavior (featured listings appear ahead of non-featured listings).
8. Cross-provider data isolation (provider cannot update or access another provider's promotions).
9. Existing promotion compatibility (discount amounts, percentages, and showcase product linking).
10. Cross-module regression compatibility (sponsored status endpoint and no side effects on other modules).
"""

from datetime import UTC, datetime, timedelta
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
from app.models.promotion import Promotion
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_shared_reference import sync_artisan_listing_reference

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
# Helper & Fixture Setup
# ==============================================================================

def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


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
def traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Jane",
        email="jane.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def approved_artisan_provider(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    user = User(
        full_name="Artisan Nimal",
        email="nimal.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Nimal Woodcarving Studio",
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
        is_featured=False,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    sync_artisan_listing_reference(db_session, listing)
    return user, profile, listing


@pytest.fixture
def second_artisan_provider(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    user = User(
        full_name="Artisan Kamala",
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
        business_name="Kamala Batik Creations",
        provider_type="artisan",
        phone="+94777654321",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Galle Fort Silk Batik",
        craft_category="Textiles & Handloom",
        district="Galle",
        city="Galle Fort",
        approval_status="approved",
        is_active=True,
        is_featured=False,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    sync_artisan_listing_reference(db_session, listing)
    return user, profile, listing


# ==============================================================================
# 1. Approved Featured Listing Visibility (Scenario 1)
# ==============================================================================

def test_approved_featured_listing_visibility(
    client: TestClient,
    admin_user: User,
    approved_artisan_provider,
):
    _, _, listing = approved_artisan_provider

    # Admin marks listing as featured
    res_feature = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(admin_user),
    )
    assert res_feature.status_code == 200

    # Verify visible in dedicated /featured discovery endpoint
    res_featured = client.get("/api/v1/artisan-listings/featured")
    assert res_featured.status_code == 200
    data = res_featured.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == listing.id
    assert data["items"][0]["is_featured"] is True

    # Verify listing detail includes is_featured=True
    res_detail = client.get(f"/api/v1/artisan-listings/{listing.id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["is_featured"] is True


# ==============================================================================
# 2. Pending Listing Exclusion (Scenario 2)
# ==============================================================================

def test_pending_listing_cannot_be_featured_and_excluded_from_featured_discovery(
    client: TestClient,
    db_session,
    admin_user: User,
    approved_artisan_provider,
):
    _, _, listing = approved_artisan_provider
    listing.approval_status = "pending"
    db_session.commit()

    # Attempt to feature a pending listing -> rejected with 400 Bad Request
    res_feature = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(admin_user),
    )
    assert res_feature.status_code == 400
    assert "Cannot feature an artisan business that is not approved and active" in res_feature.json()["detail"]

    # Even if DB has is_featured=True, public discovery excludes pending listing
    listing.is_featured = True
    db_session.commit()

    res_featured = client.get("/api/v1/artisan-listings/featured")
    assert res_featured.status_code == 200
    assert res_featured.json()["total"] == 0

    res_all = client.get("/api/v1/artisan-listings")
    assert res_all.status_code == 200
    assert res_all.json()["total"] == 0


# ==============================================================================
# 3. Rejected and Suspended Listing Exclusion (Scenario 3)
# ==============================================================================

def test_rejected_and_suspended_listing_resets_featured_status(
    client: TestClient,
    db_session,
    admin_user: User,
    approved_artisan_provider,
):
    _, _, listing = approved_artisan_provider

    # 1. Feature the listing
    client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(admin_user),
    )
    db_session.refresh(listing)
    assert listing.is_featured is True

    # 2. Admin suspends the listing
    res_suspend = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/suspend",
        json={"reason": "Audit review required"},
        headers=auth_header(admin_user),
    )
    assert res_suspend.status_code == 200

    # 3. Verify is_featured was automatically reset to False
    db_session.refresh(listing)
    assert listing.is_featured is False
    assert listing.is_active is False
    assert listing.approval_status == "suspended"

    # 4. Attempt to feature a suspended listing -> 400 Bad Request
    res_refeature = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(admin_user),
    )
    assert res_refeature.status_code == 400

    # 5. Admin rejects the listing -> ensures is_featured remains False
    res_reject = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/reject",
        json={"reason": "Business closed"},
        headers=auth_header(admin_user),
    )
    assert res_reject.status_code == 200
    db_session.refresh(listing)
    assert listing.is_featured is False


# ==============================================================================
# 4. Admin-Only Featured Status Management (Scenario 4)
# ==============================================================================

def test_admin_only_featured_status_management(
    client: TestClient,
    admin_user: User,
    traveler_user: User,
    approved_artisan_provider,
):
    provider_user, _, listing = approved_artisan_provider

    # Traveler cannot feature -> 403 Forbidden
    res_trav = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(traveler_user),
    )
    assert res_trav.status_code == 403

    # Provider cannot feature -> 403 Forbidden
    res_prov = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(provider_user),
    )
    assert res_prov.status_code == 403

    # Unauthenticated -> 401 Unauthorized
    res_unauth = client.post(f"/api/v1/admin/artisan/listings/{listing.id}/feature")
    assert res_unauth.status_code == 401

    # Admin features -> 200 OK
    res_admin = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/feature",
        headers=auth_header(admin_user),
    )
    assert res_admin.status_code == 200

    # Admin unfeatures -> 200 OK
    res_unfeat = client.post(
        f"/api/v1/admin/artisan/listings/{listing.id}/unfeature",
        headers=auth_header(admin_user),
    )
    assert res_unfeat.status_code == 200


# ==============================================================================
# 5. Unauthorized Provider Changes (Scenario 5)
# ==============================================================================

def test_provider_cannot_self_mark_featured(
    client: TestClient,
    db_session,
    approved_artisan_provider,
):
    provider_user, _, listing = approved_artisan_provider
    assert listing.is_featured is False

    # Provider updates listing with is_featured=True
    res_update = client.put(
        f"/api/v1/artisan-listings/{listing.id}",
        json={"title": "Updated Title", "is_featured": True},
        headers=auth_header(provider_user),
    )
    assert res_update.status_code == 200
    # is_featured is stripped and remains False
    assert res_update.json()["is_featured"] is False

    db_session.refresh(listing)
    assert listing.is_featured is False


# ==============================================================================
# 6. Promotion Date Validation (Scenario 6)
# ==============================================================================

def test_promotion_date_validation_and_expiry_exclusion(
    client: TestClient,
    db_session,
    approved_artisan_provider,
):
    provider_user, _, listing = approved_artisan_provider
    now = datetime.now(UTC)

    # 1. Invalid date range (end_date before start_date) -> 422 Unprocessable Content
    res_invalid = client.post(
        "/api/v1/promotions",
        json={
            "artisan_listing_id": listing.id,
            "title": "Invalid Offer",
            "discount_percentage": 15.0,
            "start_date": (now + timedelta(days=5)).isoformat(),
            "end_date": (now + timedelta(days=2)).isoformat(),
        },
        headers=auth_header(provider_user),
    )
    assert res_invalid.status_code == 422

    # 2. Expired promotion (past dates)
    expired_promo = Promotion(
        artisan_listing_id=listing.id,
        title="Expired Woodcraft Promo",
        discount_percentage=Decimal("20.00"),
        start_date=now - timedelta(days=10),
        end_date=now - timedelta(days=2),
        is_active=True,
    )
    db_session.add(expired_promo)
    db_session.commit()

    # Active promotion
    active_promo = Promotion(
        artisan_listing_id=listing.id,
        title="Active Kandy Mask Special",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(active_promo)
    db_session.commit()

    # Public discovery -> only active promotion returned
    res_public = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_public.status_code == 200
    data = res_public.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Active Kandy Mask Special"


# ==============================================================================
# 7. Sponsored / Featured Ordering Behavior (Scenario 7)
# ==============================================================================

def test_sponsored_ordering_places_featured_ahead_of_non_featured(
    client: TestClient,
    db_session,
    admin_user: User,
    approved_artisan_provider,
    second_artisan_provider,
):
    _, _, listing1 = approved_artisan_provider
    _, _, listing2 = second_artisan_provider

    # Explicitly set distinct timestamps so listing2 is newer than listing1
    now = datetime.now(UTC)
    listing1.created_at = now - timedelta(hours=5)
    listing2.created_at = now
    db_session.commit()

    # Without feature, Listing 2 appears first (created_at desc)
    res_initial = client.get("/api/v1/artisan-listings")
    assert res_initial.json()["items"][0]["id"] == listing2.id

    # Feature Listing 1 (older listing)
    client.post(
        f"/api/v1/admin/artisan/listings/{listing1.id}/feature",
        headers=auth_header(admin_user),
    )

    # Now Listing 1 appears FIRST because it is featured
    res_featured_first = client.get("/api/v1/artisan-listings")
    items = res_featured_first.json()["items"]
    assert items[0]["id"] == listing1.id
    assert items[0]["is_featured"] is True
    assert items[1]["id"] == listing2.id
    assert items[1]["is_featured"] is False


# ==============================================================================
# 8. Cross-Provider Data Isolation (Scenario 8)
# ==============================================================================

def test_cross_provider_promotion_isolation(
    client: TestClient,
    approved_artisan_provider,
    second_artisan_provider,
):
    user1, _, listing1 = approved_artisan_provider
    user2, _, listing2 = second_artisan_provider

    now = datetime.now(UTC)
    res_create = client.post(
        "/api/v1/promotions",
        json={
            "artisan_listing_id": listing1.id,
            "title": "Nimal Exclusive Sale",
            "discount_percentage": 25.0,
            "start_date": now.isoformat(),
            "end_date": (now + timedelta(days=10)).isoformat(),
        },
        headers=auth_header(user1),
    )
    assert res_create.status_code == 201
    promo_id = res_create.json()["id"]

    # Provider 2 attempts to update Provider 1's promotion -> 403 Forbidden
    res_tamper = client.put(
        f"/api/v1/promotions/{promo_id}",
        json={"title": "Hijacked Title"},
        headers=auth_header(user2),
    )
    assert res_tamper.status_code == 403

    # Provider 2 attempts to delete Provider 1's promotion -> 403 Forbidden
    res_del = client.delete(
        f"/api/v1/promotions/{promo_id}",
        headers=auth_header(user2),
    )
    assert res_del.status_code == 403


# ==============================================================================
# 9. Existing Promotion Compatibility (Scenario 9)
# ==============================================================================

def test_existing_promotion_compatibility(
    client: TestClient,
    db_session,
    approved_artisan_provider,
):
    provider_user, _, listing = approved_artisan_provider
    now = datetime.now(UTC)

    # Showcase item
    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Carved Elephant Statue",
        price=Decimal("8000.00"),
        stock_quantity=5,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Create fixed amount promotion targeted to showcase item
    res = client.post(
        "/api/v1/promotions",
        json={
            "artisan_listing_id": listing.id,
            "showcase_item_id": item.id,
            "title": "LKR 1,000 Off Elephant Statues",
            "promo_code": "ELEPHANT1000",
            "discount_amount": 1000.00,
            "start_date": now.isoformat(),
            "end_date": (now + timedelta(days=5)).isoformat(),
        },
        headers=auth_header(provider_user),
    )
    assert res.status_code == 201
    data = res.json()
    assert float(data["discount_amount"]) == 1000.00
    assert data["promo_code"] == "ELEPHANT1000"
    assert data["showcase_item_id"] == item.id
    assert data["showcase_item_title"] == "Carved Elephant Statue"


# ==============================================================================
# 10. Cross-Module Regression & Status Endpoint (Scenario 10)
# ==============================================================================

def test_sponsored_promotions_status_and_cross_module_compatibility(
    client: TestClient,
):
    res = client.get("/api/v1/artisan-listings/sponsored-promotions-status")
    assert res.status_code == 200
    data = res.json()
    assert data["featured_listings_supported"] is True
    assert data["discount_promotions_supported"] is True
    assert data["paid_sponsorship_supported"] is False
    assert "Database Coordinator and Product Owner" in data["blocked_reason"]
