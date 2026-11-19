"""
Automated tests for M05.3 – Business Promotions & Management.
Tests SQLAlchemy model mapping, relationships, Pydantic v2 schema validations,
public discovery, date/status exclusions, approved artisan creation, co-ownership checks,
updates, and soft deletes.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
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
from app.schemas.promotion import (
    PromotionCreate,
    PromotionResponse,
    PromotionSummary,
    PromotionUpdate,
)

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
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing, ShowcaseItem]:
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

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Kaduru Peacock Mask",
        item_type="product",
        price=Decimal("4500.00"),
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    return user, profile, listing, item


@pytest.fixture
def second_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing, ShowcaseItem]:
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

    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Silk Wall Hanging",
        item_type="product",
        price=Decimal("8500.00"),
        is_active=True,
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    return user, profile, listing, item


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
# Model & Schema Unit Tests
# ==============================================================================

def test_promotion_model_db_roundtrip(db_session, approved_artisan_user):
    _, _, listing, item = approved_artisan_user
    start = datetime.now(UTC)
    end = start + timedelta(days=7)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Esala Festival 15% Off",
        description="Exclusive 15% discount on traditional carved masks.",
        promo_code="PERAHERA15",
        discount_percentage=Decimal("15.00"),
        discount_amount=None,
        start_date=start,
        end_date=end,
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    assert promo.id is not None
    assert promo.title == "Esala Festival 15% Off"
    assert promo.promo_code == "PERAHERA15"
    assert promo.discount_percentage == Decimal("15.00")
    assert promo.discount_amount is None
    assert promo.is_active is True
    assert promo.artisan_listing.id == listing.id
    assert promo.listing_title == "Sunil Kandy Mask Workshop"
    assert promo.showcase_item.id == item.id
    assert promo.showcase_item_title == "Kaduru Peacock Mask"


def test_listing_level_promotion_without_showcase_item(db_session, approved_artisan_user):
    _, _, listing, _ = approved_artisan_user
    start = datetime.now(UTC)
    end = start + timedelta(days=14)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=None,
        title="Storewide LKR 500 Discount",
        promo_code="FLAT500",
        discount_percentage=None,
        discount_amount=Decimal("500.00"),
        start_date=start,
        end_date=end,
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    assert promo.showcase_item_id is None
    assert promo.showcase_item is None
    assert promo.showcase_item_title is None
    assert promo.listing_title == listing.title


def test_promotion_create_valid_percentage():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    data = {
        "artisan_listing_id": 1,
        "title": "Summer Craft Sale",
        "description": "Special summer offer",
        "promo_code": "SUMMER10",
        "discount_percentage": Decimal("10.00"),
        "discount_amount": None,
        "start_date": start,
        "end_date": end,
    }
    schema = PromotionCreate(**data)
    assert schema.title == "Summer Craft Sale"
    assert schema.discount_percentage == Decimal("10.00")
    assert schema.discount_amount is None


def test_promotion_create_valid_fixed_amount():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    data = {
        "artisan_listing_id": 1,
        "title": "Fixed Discount",
        "discount_percentage": None,
        "discount_amount": Decimal("1000.00"),
        "start_date": start,
        "end_date": end,
    }
    schema = PromotionCreate(**data)
    assert schema.discount_amount == Decimal("1000.00")
    assert schema.discount_percentage is None


def test_promotion_create_valid_general_announcement():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    data = {
        "artisan_listing_id": 1,
        "title": "Complimentary Tea & Demo",
        "description": "Free tea and carving demonstration with every visit.",
        "discount_percentage": None,
        "discount_amount": None,
        "start_date": start,
        "end_date": end,
    }
    schema = PromotionCreate(**data)
    assert schema.discount_percentage is None
    assert schema.discount_amount is None


def test_promotion_rejects_simultaneous_percentage_and_amount():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    data = {
        "artisan_listing_id": 1,
        "title": "Conflicting Discounts",
        "discount_percentage": Decimal("15.00"),
        "discount_amount": Decimal("500.00"),
        "start_date": start,
        "end_date": end,
    }
    with pytest.raises(ValidationError) as exc_info:
        PromotionCreate(**data)
    assert "cannot have both discount_percentage and discount_amount" in str(exc_info.value)


def test_promotion_rejects_invalid_date_order():
    start = datetime.now(UTC)
    end = start - timedelta(days=1)

    data = {
        "artisan_listing_id": 1,
        "title": "Invalid Dates Promotion",
        "discount_percentage": Decimal("10.00"),
        "start_date": start,
        "end_date": end,
    }
    with pytest.raises(ValidationError) as exc_info:
        PromotionCreate(**data)
    assert "end_date must be greater than or equal to start_date" in str(exc_info.value)


def test_promotion_rejects_percentage_out_of_range():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    with pytest.raises(ValidationError):
        PromotionCreate(
            artisan_listing_id=1,
            title="Over 100 Percent",
            discount_percentage=Decimal("105.00"),
            start_date=start,
            end_date=end,
        )

    with pytest.raises(ValidationError):
        PromotionCreate(
            artisan_listing_id=1,
            title="Negative Percent",
            discount_percentage=Decimal("-5.00"),
            start_date=start,
            end_date=end,
        )


def test_promotion_rejects_negative_amount():
    start = datetime.now(UTC)
    end = start + timedelta(days=5)

    with pytest.raises(ValidationError):
        PromotionCreate(
            artisan_listing_id=1,
            title="Negative Amount",
            discount_amount=Decimal("-250.00"),
            start_date=start,
            end_date=end,
        )


def test_promotion_update_schema():
    update = PromotionUpdate(title="Updated Title", discount_percentage=Decimal("20.00"))
    assert update.title == "Updated Title"
    assert update.discount_percentage == Decimal("20.00")

    with pytest.raises(ValidationError):
        PromotionUpdate(
            discount_percentage=Decimal("20.00"),
            discount_amount=Decimal("500.00"),
        )

    start = datetime.now(UTC)
    end = start - timedelta(days=2)
    with pytest.raises(ValidationError):
        PromotionUpdate(start_date=start, end_date=end)


def test_promotion_serialization_to_summary_and_response(db_session, approved_artisan_user):
    listing, item = approved_artisan_user[2], approved_artisan_user[3]
    start = datetime.now(UTC)
    end = start + timedelta(days=10)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Special Exhibition Offer",
        promo_code="EXHIBIT20",
        discount_percentage=Decimal("20.00"),
        start_date=start,
        end_date=end,
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    summary = PromotionSummary.model_validate(promo)
    assert summary.id == promo.id
    assert summary.title == "Special Exhibition Offer"
    assert summary.promo_code == "EXHIBIT20"
    assert summary.discount_percentage == Decimal("20.00")

    response = PromotionResponse.model_validate(promo)
    assert response.id == promo.id
    assert response.listing_title == listing.title
    assert response.showcase_item_title == item.title
    assert response.is_active is True


# ==============================================================================
# Public API Endpoint Tests
# ==============================================================================

def test_list_promotions_empty(client: TestClient):
    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_promotions_filtering_and_exclusion(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1, item1 = approved_artisan_user
    _, _, listing2, item2 = second_artisan_user

    now = datetime.now(UTC)

    # Inactive listing to test exclusion
    inactive_listing = ArtisanListing(
        provider_profile_id=listing1.provider_profile_id,
        title="Closed Workshop",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Kandy",
        is_active=False,
    )
    db_session.add(inactive_listing)
    db_session.commit()
    db_session.refresh(inactive_listing)

    # Inactive showcase item to test exclusion
    inactive_item = ShowcaseItem(
        artisan_listing_id=listing1.id,
        title="Inactive Mask",
        item_type="product",
        is_active=False,
    )
    db_session.add(inactive_item)
    db_session.commit()
    db_session.refresh(inactive_item)

    # 1. Active currently running storewide promotion on active listing 1
    promo1 = Promotion(
        artisan_listing_id=listing1.id,
        showcase_item_id=None,
        title="Kandy Craft Week Special",
        promo_code="KANDY15",
        discount_percentage=Decimal("15.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    # 2. Active currently running item-specific promotion on active listing 2
    promo2 = Promotion(
        artisan_listing_id=listing2.id,
        showcase_item_id=item2.id,
        title="Galle Silk Batik Flat Discount",
        promo_code="GALLE500",
        discount_amount=Decimal("500.00"),
        start_date=now - timedelta(days=2),
        end_date=now + timedelta(days=4),
        is_active=True,
    )
    # 3. EXCLUDED: Inactive promotion (is_active = False)
    promo_inactive = Promotion(
        artisan_listing_id=listing1.id,
        title="Deactivated Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=False,
    )
    # 4. EXCLUDED: Expired promotion (end_date < now)
    promo_expired = Promotion(
        artisan_listing_id=listing1.id,
        title="Past New Year Promo",
        discount_percentage=Decimal("20.00"),
        start_date=now - timedelta(days=15),
        end_date=now - timedelta(days=2),
        is_active=True,
    )
    # 5. EXCLUDED: Future/unstarted promotion (start_date > now)
    promo_future = Promotion(
        artisan_listing_id=listing1.id,
        title="Upcoming Christmas Promo",
        discount_percentage=Decimal("25.00"),
        start_date=now + timedelta(days=10),
        end_date=now + timedelta(days=20),
        is_active=True,
    )
    # 6. EXCLUDED: Active promo attached to an INACTIVE listing
    promo_inactive_listing = Promotion(
        artisan_listing_id=inactive_listing.id,
        title="Promo on Closed Shop",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    # 7. EXCLUDED: Active promo attached to an INACTIVE showcase item
    promo_inactive_item = Promotion(
        artisan_listing_id=listing1.id,
        showcase_item_id=inactive_item.id,
        title="Promo on Inactive Item",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )

    db_session.add_all([
        promo1, promo2, promo_inactive, promo_expired, promo_future,
        promo_inactive_listing, promo_inactive_item
    ])
    db_session.commit()

    # Default list should return ONLY promo1 and promo2
    res = client.get("/api/v1/promotions")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Filter by search
    res_search = client.get("/api/v1/promotions", params={"search": "Kandy"})
    assert res_search.status_code == 200
    assert res_search.json()["total"] == 1
    assert res_search.json()["items"][0]["title"] == "Kandy Craft Week Special"

    # Filter by district
    res_dist = client.get("/api/v1/promotions", params={"district": "Galle"})
    assert res_dist.status_code == 200
    assert res_dist.json()["total"] == 1
    assert res_dist.json()["items"][0]["title"] == "Galle Silk Batik Flat Discount"

    # Filter by craft_category
    res_craft = client.get("/api/v1/promotions", params={"craft_category": "Woodcarving & Masks"})
    assert res_craft.status_code == 200
    assert res_craft.json()["total"] == 1

    # Filter by artisan_listing_id
    res_listing = client.get("/api/v1/promotions", params={"artisan_listing_id": listing2.id})
    assert res_listing.status_code == 200
    assert res_listing.json()["total"] == 1

    # Filter by showcase_item_id
    res_item = client.get("/api/v1/promotions", params={"showcase_item_id": item2.id})
    assert res_item.status_code == 200
    assert res_item.json()["total"] == 1

    # Pagination
    res_page = client.get("/api/v1/promotions", params={"page": 1, "page_size": 1})
    assert res_page.status_code == 200
    assert len(res_page.json()["items"]) == 1
    assert res_page.json()["total"] == 2
    assert res_page.json()["total_pages"] == 2


def test_get_promotion_details_public(client: TestClient, db_session, approved_artisan_user):
    _, _, listing, item = approved_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        showcase_item_id=item.id,
        title="Authentic Mask 10% Off",
        promo_code="MASK10",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    # Active running promotion returns 200
    res = client.get(f"/api/v1/promotions/{promo.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == promo.id
    assert data["title"] == "Authentic Mask 10% Off"
    assert data["listing_title"] == listing.title
    assert data["showcase_item_title"] == item.title

    # Inactive promotion returns 404
    promo.is_active = False
    db_session.commit()
    assert client.get(f"/api/v1/promotions/{promo.id}").status_code == 404

    # Expired promotion returns 404
    promo.is_active = True
    promo.end_date = now - timedelta(days=1)
    db_session.commit()
    assert client.get(f"/api/v1/promotions/{promo.id}").status_code == 404

    # Nonexistent promotion returns 404
    assert client.get("/api/v1/promotions/99999").status_code == 404


def test_get_listing_promotions_public(client: TestClient, db_session, approved_artisan_user):
    _, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        title="Listing Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()

    res = client.get(f"/api/v1/artisan-listings/{listing.id}/promotions")
    assert res.status_code == 200
    assert res.json()["total"] == 1

    # Nonexistent listing returns 404
    assert client.get("/api/v1/artisan-listings/99999/promotions").status_code == 404


# ==============================================================================
# Protected API Endpoint Tests
# ==============================================================================

def test_create_promotion_unauthenticated(client: TestClient, approved_artisan_user):
    _, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Unauthenticated Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    assert client.post("/api/v1/promotions", json=payload).status_code == 401


def test_create_promotion_traveler_forbidden(client: TestClient, traveler_user, approved_artisan_user):
    _, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Traveler Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload, headers=auth_header(traveler_user))
    assert res.status_code == 403
    assert "provider role required" in res.json()["detail"].lower()


def test_create_promotion_pending_provider_forbidden(client: TestClient, pending_artisan_user, approved_artisan_user):
    user, _ = pending_artisan_user
    _, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Pending Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload, headers=auth_header(user))
    assert res.status_code == 403
    assert "pending" in res.json()["detail"].lower()


def test_create_promotion_non_artisan_provider_forbidden(client: TestClient, homestay_provider_user, approved_artisan_user):
    user, _ = homestay_provider_user
    _, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)
    payload = {
        "artisan_listing_id": listing.id,
        "title": "Homestay Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload, headers=auth_header(user))
    assert res.status_code == 403
    assert "only artisan service providers" in res.json()["detail"].lower()


def test_create_promotion_other_artisan_listing_forbidden(client: TestClient, approved_artisan_user, second_artisan_user):
    _, _, listing1, _ = approved_artisan_user
    user2, _, _, _ = second_artisan_user
    now = datetime.now(UTC)

    payload = {
        "artisan_listing_id": listing1.id,
        "title": "Hijacked Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload, headers=auth_header(user2))
    assert res.status_code == 403
    assert "do not own this artisan listing" in res.json()["detail"].lower()


def test_create_promotion_mismatched_showcase_item_bad_request(client: TestClient, approved_artisan_user, second_artisan_user):
    user1, _, listing1, _ = approved_artisan_user
    _, _, _, item2 = second_artisan_user
    now = datetime.now(UTC)

    # Trying to attach an item from listing2 to a promotion for listing1
    payload = {
        "artisan_listing_id": listing1.id,
        "showcase_item_id": item2.id,
        "title": "Mismatched Item Promo",
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/promotions", json=payload, headers=auth_header(user1))
    assert res.status_code == 400
    assert "does not belong to the specified artisan listing" in res.json()["detail"]


def test_create_promotion_owner_success(client: TestClient, approved_artisan_user):
    user, _, listing, item = approved_artisan_user
    now = datetime.now(UTC)

    # 1. Create a percentage storewide promotion
    payload1 = {
        "artisan_listing_id": listing.id,
        "title": "Seasonal Storewide Sale",
        "description": "15% off all handicrafts this week",
        "promo_code": "SEASON15",
        "discount_percentage": 15.00,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=7)).isoformat(),
    }
    res1 = client.post("/api/v1/promotions", json=payload1, headers=auth_header(user))
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["title"] == "Seasonal Storewide Sale"
    assert float(data1["discount_percentage"]) == 15.00
    assert data1["showcase_item_id"] is None
    assert data1["listing_title"] == listing.title
    assert data1["is_active"] is True

    # 2. Create an item-specific fixed amount promotion
    payload2 = {
        "artisan_listing_id": listing.id,
        "showcase_item_id": item.id,
        "title": "Special Mask Voucher",
        "description": "500 LKR discount on peacock masks",
        "promo_code": "MASK500",
        "discount_amount": 500.00,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=7)).isoformat(),
    }
    res2 = client.post("/api/v1/promotions", json=payload2, headers=auth_header(user))
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["title"] == "Special Mask Voucher"
    assert float(data2["discount_amount"]) == 500.00
    assert data2["showcase_item_id"] == item.id
    assert data2["showcase_item_title"] == item.title


def test_get_my_promotions(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    user1, _, listing1, _ = approved_artisan_user
    user2, _, listing2, _ = second_artisan_user
    now = datetime.now(UTC)

    promo1 = Promotion(
        artisan_listing_id=listing1.id,
        title="Sunil Active Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    promo2 = Promotion(
        artisan_listing_id=listing1.id,
        title="Sunil Inactive Promo",
        discount_percentage=Decimal("20.00"),
        start_date=now - timedelta(days=10),
        end_date=now - timedelta(days=2),
        is_active=False,
    )
    promo3 = Promotion(
        artisan_listing_id=listing2.id,
        title="Kamala Promo",
        discount_percentage=Decimal("15.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=5),
        is_active=True,
    )
    db_session.add_all([promo1, promo2, promo3])
    db_session.commit()

    # User 1 calls /promotions/me/items
    res = client.get("/api/v1/promotions/me/items", headers=auth_header(user1))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    titles = [p["title"] for p in items]
    assert "Sunil Active Promo" in titles
    assert "Sunil Inactive Promo" in titles
    assert "Kamala Promo" not in titles


def test_update_promotion_owner_success(client: TestClient, db_session, approved_artisan_user):
    user, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        title="Original Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now,
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    update_payload = {
        "title": "Supercharged 20% Off",
        "discount_percentage": 20.00,
        "promo_code": "SUPER20",
    }
    res = client.put(f"/api/v1/promotions/{promo.id}", json=update_payload, headers=auth_header(user))
    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Supercharged 20% Off"
    assert float(data["discount_percentage"]) == 20.00
    assert data["promo_code"] == "SUPER20"


def test_update_promotion_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1, _ = approved_artisan_user
    user2, _, _, _ = second_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing1.id,
        title="Sunil Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now,
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.put(f"/api/v1/promotions/{promo.id}", json={"title": "Hacked Title"}, headers=auth_header(user2))
    assert res.status_code == 403
    assert "do not own this promotion" in res.json()["detail"].lower()


def test_update_promotion_mismatched_showcase_item_bad_request(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    user1, _, listing1, _ = approved_artisan_user
    _, _, _, item2 = second_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing1.id,
        title="Sunil Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now,
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.put(
        f"/api/v1/promotions/{promo.id}",
        json={"showcase_item_id": item2.id},
        headers=auth_header(user1),
    )
    assert res.status_code == 400
    assert "does not belong to the specified artisan listing" in res.json()["detail"]


def test_soft_delete_promotion_owner_success(client: TestClient, db_session, approved_artisan_user):
    user, _, listing, _ = approved_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing.id,
        title="Promo To Deactivate",
        discount_percentage=Decimal("10.00"),
        start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.delete(f"/api/v1/promotions/{promo.id}", headers=auth_header(user))
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    # Verify DB state
    db_session.refresh(promo)
    assert promo.is_active is False

    # Public detail endpoint returns 404
    assert client.get(f"/api/v1/promotions/{promo.id}").status_code == 404


def test_soft_delete_promotion_other_provider_forbidden(client: TestClient, db_session, approved_artisan_user, second_artisan_user):
    _, _, listing1, _ = approved_artisan_user
    user2, _, _, _ = second_artisan_user
    now = datetime.now(UTC)

    promo = Promotion(
        artisan_listing_id=listing1.id,
        title="Sunil Promo",
        discount_percentage=Decimal("10.00"),
        start_date=now,
        end_date=now + timedelta(days=7),
        is_active=True,
    )
    db_session.add(promo)
    db_session.commit()
    db_session.refresh(promo)

    res = client.delete(f"/api/v1/promotions/{promo.id}", headers=auth_header(user2))
    assert res.status_code == 403
    assert "do not own this promotion" in res.json()["detail"].lower()
