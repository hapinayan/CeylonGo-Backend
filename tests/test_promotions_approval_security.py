"""
Tests for Artisan Promotions Approval Security & Visibility (Phase P1 Step 6).
Verifies that public promotion discovery, detail, and nested listing endpoints
strictly filter by approval status and active flags:
1. Approved listing promotion visibility.
2. Pending listing promotions hidden.
3. Rejected listing promotions hidden.
4. Suspended listing promotions hidden.
5. Unapproved provider promotions hidden.
6. Unapproved product promotions hidden.
7. Inactive products and listings hidden.
8. Promotion creation blocked for unapproved targets.
9. Cross-provider ownership protection.
10. Provider update & delete access control.
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


def make_provider_bundle(
    db_session,
    name: str,
    provider_status: str = "approved",
    listing_status: str = "approved",
    listing_active: bool = True,
    product_status: str = "approved",
    product_active: bool = True,
):
    user = User(
        full_name=f"Artisan {name}",
        email=f"{name.lower()}@artisan.test",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name=f"{name} Workshop",
        provider_type="artisan",
        phone="+94770000001",
        district="Kandy",
        approval_status=provider_status,
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title=f"{name} Traditional Crafts",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        approval_status=listing_status,
        is_verified=True,
        is_active=listing_active,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title=f"{name} Wooden Carving",
        item_type="product",
        price=Decimal("5000.00"),
        approval_status=product_status,
        is_active=product_active,
        stock_quantity=10,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    return user, profile, listing, item


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Approved Listing & Product Promotion Visibility
# ==============================================================================

def test_approved_listing_promotion_visibility(client: TestClient, db_session):
    user, profile, listing, item = make_provider_bundle(db_session, "Approved")
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Approved Autumn Sale",
        promo_code="AUTUMN10",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    # 1. Appears in public list
    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == promo.id
    assert data["items"][0]["title"] == "Approved Autumn Sale"

    # 2. Appears in public detail
    res_detail = client.get(f"/api/v1/promotions/{promo.id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["id"] == promo.id

    # 3. Appears in nested listing endpoint
    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 200
    assert res_nested.json()["total"] == 1


# ==============================================================================
# 2. Pending Listing Promotions Hidden
# ==============================================================================

def test_pending_listing_promotions_hidden(client: TestClient, db_session):
    _, _, listing, item = make_provider_bundle(db_session, "PendingListing", listing_status="pending")
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Pending Listing Special",
        discount_percentage=Decimal("15.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    # Public list must exclude it
    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 0

    # Detail must return 404
    res_detail = client.get(f"/api/v1/promotions/{promo.id}")
    assert res_detail.status_code == 404

    # Nested listing must return 404
    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 404


# ==============================================================================
# 3. Rejected Listing Promotions Hidden
# ==============================================================================

def test_rejected_listing_promotions_hidden(client: TestClient, db_session):
    _, _, listing, _ = make_provider_bundle(db_session, "RejectedListing", listing_status="rejected")
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=None,
        title="Rejected Listing Storewide",
        discount_amount=Decimal("500.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 0

    res_detail = client.get(f"/api/v1/promotions/{promo.id}")
    assert res_detail.status_code == 404

    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 404


# ==============================================================================
# 4. Suspended Listing Promotions Hidden
# ==============================================================================

def test_suspended_listing_promotions_hidden(client: TestClient, db_session):
    _, _, listing, item = make_provider_bundle(db_session, "SuspendedListing", listing_status="suspended")
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Suspended Listing Promotion",
        discount_percentage=Decimal("20.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 0

    res_detail = client.get(f"/api/v1/promotions/{promo.id}")
    assert res_detail.status_code == 404

    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 404


# ==============================================================================
# 5. Unapproved Provider Promotions Hidden
# ==============================================================================

def test_unapproved_provider_promotions_hidden(client: TestClient, db_session):
    _, _, listing, item = make_provider_bundle(
        db_session, "PendingProvider", provider_status="pending", listing_status="approved"
    )
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Pending Provider Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 0

    res_detail = client.get(f"/api/v1/promotions/{promo.id}")
    assert res_detail.status_code == 404

    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 404


# ==============================================================================
# 6. Unapproved Product Promotions Hidden (Storewide Remains Visible)
# ==============================================================================

def test_unapproved_product_promotions_hidden(client: TestClient, db_session):
    user, profile, listing, item = make_provider_bundle(
        db_session, "ProductStatusTest", product_status="pending"
    )
    now = datetime.now(UTC)

    # Promotion 1: Linked to pending product -> MUST BE HIDDEN
    promo_pending_product = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Pending Item Special",
        discount_percentage=Decimal("25.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    # Promotion 2: Storewide on same approved listing -> MUST BE VISIBLE
    promo_storewide = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=None,
        title="Storewide Festival Discount",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add_all([promo_pending_product, promo_storewide])
    db_session.commit()
    db_session.refresh(promo_pending_product)
    db_session.refresh(promo_storewide)

    # Public list should only show the storewide promo
    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == promo_storewide.id

    # Detail for pending product promotion returns 404
    res_pending_detail = client.get(f"/api/v1/promotions/{promo_pending_product.id}")
    assert res_pending_detail.status_code == 404

    # Detail for storewide promotion returns 200
    res_storewide_detail = client.get(f"/api/v1/promotions/{promo_storewide.id}")
    assert res_storewide_detail.status_code == 200

    # Nested listing returns only storewide promo
    res_nested = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res_nested.status_code == 200
    assert res_nested.json()["total"] == 1
    assert res_nested.json()["items"][0]["id"] == promo_storewide.id


# ==============================================================================
# 7. Inactive Products and Listings Hidden
# ==============================================================================

def test_inactive_products_and_listings_hidden(client: TestClient, db_session):
    _, _, inactive_listing, item_on_inactive = make_provider_bundle(
        db_session, "InactiveListingTest", listing_active=False
    )
    _, _, active_listing, inactive_item = make_provider_bundle(
        db_session, "InactiveProductTest", product_active=False
    )
    now = datetime.now(UTC)

    promo_inactive_listing = Promotion(
        artisan_listing_id=inactive_listing.id,
        showcase_item_id=None,
        title="Inactive Listing Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    promo_inactive_product = Promotion(
        artisan_listing_id=active_listing.id,
        showcase_item_id=inactive_item.id,
        title="Inactive Item Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add_all([promo_inactive_listing, promo_inactive_product])
    db_session.commit()
    db_session.refresh(promo_inactive_listing)
    db_session.refresh(promo_inactive_product)

    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 0

    assert client.get(f"/api/v1/promotions/{promo_inactive_listing.id}").status_code == 404
    assert client.get(f"/api/v1/promotions/{promo_inactive_product.id}").status_code == 404


# ==============================================================================
# 8. Promotion Creation Blocked for Unapproved Targets
# ==============================================================================

def test_promotion_creation_blocked_for_unapproved_targets(client: TestClient, db_session):
    user, profile, listing, item = make_provider_bundle(db_session, "CreationSecurity")
    now = datetime.now(UTC)

    # 1. Blocked when listing is pending
    listing.approval_status = "pending"
    db_session.commit()

    payload_pending_listing = {
        "artisan_listing_id": listing.id,
        "title": "Pending Listing Attempt",
        "discount_percentage": 15.0,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload_pending_listing, headers=auth_header(user))
    assert res.status_code == 400
    assert "inactive or unapproved artisan listing" in res.json()["detail"].lower()

    # Restore listing to approved
    listing.approval_status = "approved"
    db_session.commit()

    # 2. Blocked when listing is inactive
    listing.is_active = False
    db_session.commit()

    res = client.post("/api/v1/promotions", json=payload_pending_listing, headers=auth_header(user))
    assert res.status_code == 400
    assert "inactive or unapproved artisan listing" in res.json()["detail"].lower()

    listing.is_active = True
    db_session.commit()

    # 3. Blocked when showcase product is pending
    item.approval_status = "pending"
    db_session.commit()

    payload_pending_item = {
        "artisan_listing_id": listing.id,
        "showcase_item_id": item.id,
        "title": "Pending Product Attempt",
        "discount_percentage": 20.0,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload_pending_item, headers=auth_header(user))
    assert res.status_code == 400
    assert "inactive or unapproved showcase product" in res.json()["detail"].lower()

    # 4. Blocked when showcase product is inactive
    item.approval_status = "approved"
    item.is_active = False
    db_session.commit()

    res = client.post("/api/v1/promotions", json=payload_pending_item, headers=auth_header(user))
    assert res.status_code == 400
    assert "inactive or unapproved showcase product" in res.json()["detail"].lower()


# ==============================================================================
# 9. Cross-Provider Ownership Protection
# ==============================================================================

def test_cross_provider_ownership_protection(client: TestClient, db_session):
    user1, profile1, listing1, item1 = make_provider_bundle(db_session, "OwnerOne")
    user2, profile2, listing2, item2 = make_provider_bundle(db_session, "OwnerTwo")
    now = datetime.now(UTC)

    # Provider 2 tries to create promotion for Listing 1
    cross_create_payload = {
        "artisan_listing_id": listing1.id,
        "title": "Cross Hijack Attempt",
        "discount_percentage": 10.0,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=cross_create_payload, headers=auth_header(user2))
    assert res.status_code == 403
    assert "do not own this artisan listing" in res.json()["detail"].lower()

    # Create legitimate promotion by Owner 1
    res_legit = client.post("/api/v1/promotions", json=cross_create_payload, headers=auth_header(user1))
    assert res_legit.status_code == 201
    promo_id = res_legit.json()["id"]

    # Provider 2 tries to update Owner 1's promotion
    res_update = client.put(
        f"/api/v1/promotions/{promo_id}",
        json={"title": "Hacked Title"},
        headers=auth_header(user2),
    )
    assert res_update.status_code == 403

    # Provider 2 tries to delete Owner 1's promotion
    res_delete = client.delete(f"/api/v1/promotions/{promo_id}", headers=auth_header(user2))
    assert res_delete.status_code == 403


# ==============================================================================
# 10. Provider Update Cannot Link to Unapproved Target
# ==============================================================================

def test_provider_update_cannot_link_to_unapproved_target(client: TestClient, db_session):
    user, profile, listing, item = make_provider_bundle(db_session, "UpdateSecurity")
    now = datetime.now(UTC)

    # Create valid storewide promotion
    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=None,
        title="Legitimate Storewide",
        discount_percentage=Decimal("10.00"),
        start_date=now,
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    # Unapprove item
    item.approval_status = "rejected"
    db_session.commit()

    # Update attempt linking to rejected product must be rejected
    update_payload = {"showcase_item_id": item.id}
    res = client.put(
        f"/api/v1/promotions/{promo.id}",
        json=update_payload,
        headers=auth_header(user),
    )
    assert res.status_code == 400
    assert "inactive or unapproved showcase product" in res.json()["detail"].lower()
