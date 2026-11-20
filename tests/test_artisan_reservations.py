"""
Automated tests for Artisan Product Reservation API (Phase P0 - Step 3).
Tests reservation creation, stock validation, price calculation, status transitions
(pending -> confirmed -> ready_for_pickup -> completed), cancellation, stock release,
provider and traveler authorization, and concurrency protection.
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
from app.models.artisan import ArtisanReservation
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


# ==============================================================================
# User & Entity Fixtures
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
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
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
        business_name="Nimal Woodcrafts",
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
        title="Nimal Kandy Mask Studio",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def second_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
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
        business_name="Kamala Batik Gallery",
        provider_type="artisan",
        phone="+94774445566",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Galle Fort Silk Batiks",
        craft_category="Batik & Textiles",
        district="Galle",
        city="Galle",
        approval_status="approved",
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    return user, profile, listing


@pytest.fixture
def reservable_product(db_session, approved_artisan_user) -> ShowcaseItem:
    _, _, listing = approved_artisan_user
    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Handcrafted Raksha Mask",
        item_type="product",
        description="Authentic carved Raksha demon mask.",
        price=Decimal("4500.00"),
        currency="LKR",
        stock_quantity=10,
        sku="MASK-RAK-01",
        is_available=True,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


# ==============================================================================
# 1. Reservation Creation Tests
# ==============================================================================

def test_create_reservation_success(client: TestClient, db_session, traveler_user, reservable_product):
    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 2,
        "pickup_date": tomorrow.isoformat(),
        "special_notes": "Will pick up around noon.",
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 201
    data = res.json()

    assert data["showcase_item_id"] == reservable_product.id
    assert data["traveler_id"] == traveler_user.id
    assert data["quantity"] == 2
    assert Decimal(str(data["total_price"])) == Decimal("9000.00")
    assert data["currency"] == "LKR"
    assert data["reservation_status"] == "pending"
    assert data["pickup_date"] == tomorrow.isoformat()
    assert data["special_notes"] == "Will pick up around noon."
    assert data["item_title"] == "Handcrafted Raksha Mask"
    assert data["listing_title"] == "Nimal Kandy Mask Studio"
    assert data["provider_business_name"] == "Nimal Woodcrafts"
    assert data["traveler_name"] == "Traveler Kasun"

    # Verify stock was deducted by 2 in DB (10 - 2 = 8)
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 8


def test_create_reservation_insufficient_stock(client: TestClient, traveler_user, reservable_product):
    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 15,  # only 10 available
        "pickup_date": tomorrow.isoformat(),
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 400
    assert "insufficient stock" in res.json()["detail"].lower()


def test_create_reservation_past_pickup_date_rejected(client: TestClient, traveler_user, reservable_product):
    yesterday = date.today() - timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 1,
        "pickup_date": yesterday.isoformat(),
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 422


def test_create_reservation_service_item_rejected(client: TestClient, db_session, traveler_user, approved_artisan_user):
    _, _, listing = approved_artisan_user
    service_item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="2-Hour Mask Painting Workshop",
        item_type="service",
        price=Decimal("3000.00"),
        is_available=True,
        is_active=True,
    )
    db_session.add(service_item)
    db_session.commit()
    db_session.refresh(service_item)

    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": service_item.id,
        "quantity": 1,
        "pickup_date": tomorrow.isoformat(),
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 400
    assert "only handcrafted products can be reserved" in res.json()["detail"].lower()


def test_create_reservation_unapproved_listing_rejected(client: TestClient, db_session, traveler_user, reservable_product):
    reservable_product.artisan_listing.approval_status = "pending"
    db_session.commit()

    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 1,
        "pickup_date": tomorrow.isoformat(),
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 400
    assert "not active or approved" in res.json()["detail"].lower()


def test_create_reservation_artisan_self_reservation_forbidden(client: TestClient, approved_artisan_user, reservable_product):
    user, _, _ = approved_artisan_user
    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 1,
        "pickup_date": tomorrow.isoformat(),
    }
    # Provider role rejected by require_traveler
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
        headers=auth_header(user),
    )
    assert res.status_code == 403
    assert "traveler role required" in res.json()["detail"].lower()


def test_create_reservation_unauthenticated_rejected(client: TestClient, reservable_product):
    tomorrow = date.today() + timedelta(days=1)
    payload = {
        "showcase_item_id": reservable_product.id,
        "quantity": 1,
        "pickup_date": tomorrow.isoformat(),
    }
    res = client.post(
        "/api/v1/artisan-reservations",
        json=payload,
    )
    assert res.status_code == 401


# ==============================================================================
# 2. Reservation Lookup & Access Control Tests
# ==============================================================================

def test_get_my_reservations(client: TestClient, traveler_user, second_traveler_user, reservable_product):
    tomorrow = date.today() + timedelta(days=1)

    # Traveler 1 creates a reservation
    client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )

    # Traveler 2 creates a reservation
    client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 2, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(second_traveler_user),
    )

    # Traveler 1 sees only their 1 reservation
    res1 = client.get("/api/v1/artisan-reservations/me", headers=auth_header(traveler_user))
    assert res1.status_code == 200
    data1 = res1.json()
    assert len(data1) == 1
    assert data1[0]["quantity"] == 1

    # Traveler 2 sees only their 1 reservation
    res2 = client.get("/api/v1/artisan-reservations/me", headers=auth_header(second_traveler_user))
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2) == 1
    assert data2[0]["quantity"] == 2


def test_get_provider_reservations(client: TestClient, approved_artisan_user, second_artisan_user, traveler_user, reservable_product):
    artisan1, _, _ = approved_artisan_user
    artisan2, _, _ = second_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    # Traveler reserves item from Artisan 1
    res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 201

    # Artisan 1 sees this reservation
    res_artisan1 = client.get("/api/v1/artisan-reservations/provider", headers=auth_header(artisan1))
    assert res_artisan1.status_code == 200
    assert len(res_artisan1.json()) == 1
    assert res_artisan1.json()[0]["showcase_item_id"] == reservable_product.id

    # Artisan 2 does not see Artisan 1's reservation
    res_artisan2 = client.get("/api/v1/artisan-reservations/provider", headers=auth_header(artisan2))
    assert res_artisan2.status_code == 200
    assert len(res_artisan2.json()) == 0


def test_get_single_reservation_authorization(client: TestClient, approved_artisan_user, second_artisan_user, traveler_user, second_traveler_user, reservable_product):
    artisan1, _, _ = approved_artisan_user
    artisan2, _, _ = second_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    res = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    res_id = res.json()["id"]

    # 1. Traveler owner can view
    r_traveler = client.get(f"/api/v1/artisan-reservations/{res_id}", headers=auth_header(traveler_user))
    assert r_traveler.status_code == 200

    # 2. Listing owner can view
    r_provider = client.get(f"/api/v1/artisan-reservations/{res_id}", headers=auth_header(artisan1))
    assert r_provider.status_code == 200

    # 3. Another traveler cannot view -> 403
    r_other_traveler = client.get(f"/api/v1/artisan-reservations/{res_id}", headers=auth_header(second_traveler_user))
    assert r_other_traveler.status_code == 403

    # 4. Another artisan cannot view -> 403
    r_other_artisan = client.get(f"/api/v1/artisan-reservations/{res_id}", headers=auth_header(artisan2))
    assert r_other_artisan.status_code == 403


# ==============================================================================
# 3. Status Transitions & Workflow Tests
# ==============================================================================

def test_full_reservation_lifecycle(client: TestClient, db_session, traveler_user, approved_artisan_user, reservable_product):
    artisan, _, _ = approved_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    # 1. Traveler creates reservation (pending)
    res_create = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 3, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res_create.status_code == 201
    res_id = res_create.json()["id"]
    assert res_create.json()["reservation_status"] == "pending"

    # Verify stock deducted: 10 - 3 = 7
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 7

    # 2. Provider confirms reservation (pending -> confirmed)
    res_confirm = client.patch(
        f"/api/v1/artisan-reservations/{res_id}/confirm",
        headers=auth_header(artisan),
    )
    assert res_confirm.status_code == 200
    assert res_confirm.json()["reservation_status"] == "confirmed"

    # Cannot confirm again (not in pending)
    res_confirm_again = client.patch(
        f"/api/v1/artisan-reservations/{res_id}/confirm",
        headers=auth_header(artisan),
    )
    assert res_confirm_again.status_code == 400

    # 3. Provider marks ready for pickup (confirmed -> ready_for_pickup)
    res_ready = client.patch(
        f"/api/v1/artisan-reservations/{res_id}/ready",
        headers=auth_header(artisan),
    )
    assert res_ready.status_code == 200
    assert res_ready.json()["reservation_status"] == "ready_for_pickup"

    # 4. Traveler arrives, pays cash, provider completes pickup (ready_for_pickup -> completed)
    res_complete = client.post(
        f"/api/v1/artisan-reservations/{res_id}/complete-pickup",
        headers=auth_header(artisan),
    )
    assert res_complete.status_code == 200
    assert res_complete.json()["reservation_status"] == "completed"

    # 5. Cannot complete an already completed reservation
    res_complete_again = client.post(
        f"/api/v1/artisan-reservations/{res_id}/complete-pickup",
        headers=auth_header(artisan),
    )
    assert res_complete_again.status_code == 400

    # Stock remains sold: 7
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 7


def test_transition_unauthorized_provider_forbidden(client: TestClient, second_artisan_user, traveler_user, reservable_product):
    artisan2, _, _ = second_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    res_create = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    res_id = res_create.json()["id"]

    # Artisan 2 attempts to confirm Artisan 1's reservation
    res = client.patch(f"/api/v1/artisan-reservations/{res_id}/confirm", headers=auth_header(artisan2))
    assert res.status_code == 403
    assert "do not own the artisan listing" in res.json()["detail"].lower()


# ==============================================================================
# 4. Cancellation & Stock Release Tests
# ==============================================================================

def test_traveler_cancel_reservation_releases_stock_once(client: TestClient, db_session, traveler_user, reservable_product):
    tomorrow = date.today() + timedelta(days=1)

    # 1. Create reservation of 4 items (stock: 10 -> 6)
    res_create = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 4, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    res_id = res_create.json()["id"]
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 6

    # 2. Traveler cancels reservation
    cancel_payload = {"cancellation_reason": "Change of travel dates."}
    res_cancel = client.post(
        f"/api/v1/artisan-reservations/{res_id}/cancel",
        json=cancel_payload,
        headers=auth_header(traveler_user),
    )
    assert res_cancel.status_code == 200
    data = res_cancel.json()
    assert data["reservation_status"] == "cancelled"
    assert data["cancelled_by"] == traveler_user.id
    assert data["cancellation_reason"] == "Change of travel dates."

    # Stock is restored exactly once: 6 + 4 = 10
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 10

    # 3. Duplicate cancellation attempt -> 400 Bad Request, stock untouched
    res_cancel_again = client.post(
        f"/api/v1/artisan-reservations/{res_id}/cancel",
        json=cancel_payload,
        headers=auth_header(traveler_user),
    )
    assert res_cancel_again.status_code == 400
    assert "already cancelled" in res_cancel_again.json()["detail"].lower()

    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 10


def test_provider_cancel_active_reservation_releases_stock(client: TestClient, db_session, approved_artisan_user, traveler_user, reservable_product):
    artisan, _, _ = approved_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    # 1. Create reservation (10 -> 8)
    res_create = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 2, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    res_id = res_create.json()["id"]

    # 2. Provider cancels reservation
    cancel_payload = {"cancellation_reason": "Workshop emergency closure."}
    res_cancel = client.post(
        f"/api/v1/artisan-reservations/{res_id}/cancel",
        json=cancel_payload,
        headers=auth_header(artisan),
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["reservation_status"] == "cancelled"
    assert res_cancel.json()["cancelled_by"] == artisan.id

    # Stock restored: 8 + 2 = 10
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 10


def test_cannot_cancel_completed_reservation(client: TestClient, db_session, approved_artisan_user, traveler_user, reservable_product):
    artisan, _, _ = approved_artisan_user
    tomorrow = date.today() + timedelta(days=1)

    # 1. Create & complete reservation
    res_create = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    res_id = res_create.json()["id"]

    client.patch(f"/api/v1/artisan-reservations/{res_id}/confirm", headers=auth_header(artisan))
    client.post(f"/api/v1/artisan-reservations/{res_id}/complete-pickup", headers=auth_header(artisan))

    # 2. Attempt cancellation on completed reservation
    res_cancel = client.post(
        f"/api/v1/artisan-reservations/{res_id}/cancel",
        json={"cancellation_reason": "Try to cancel"},
        headers=auth_header(traveler_user),
    )
    assert res_cancel.status_code == 400
    assert "completed reservations cannot be cancelled" in res_cancel.json()["detail"].lower()


# ==============================================================================
# 5. Stock Exhaustion & Sequential Locking Test
# ==============================================================================

def test_stock_exhaustion_sequential_reservations(client: TestClient, db_session, traveler_user, second_traveler_user, reservable_product):
    tomorrow = date.today() + timedelta(days=1)

    # Initial stock is 10
    # Traveler 1 reserves 7 items -> succeeds, remaining stock = 3
    res1 = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 7, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res1.status_code == 201
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 3

    # Traveler 2 attempts to reserve 4 items -> fails (only 3 available)
    res2 = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 4, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(second_traveler_user),
    )
    assert res2.status_code == 400
    assert "insufficient stock" in res2.json()["detail"].lower()

    # Traveler 2 attempts to reserve exactly 3 items -> succeeds, remaining stock = 0
    res3 = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 3, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(second_traveler_user),
    )
    assert res3.status_code == 201
    db_session.refresh(reservable_product)
    assert reservable_product.stock_quantity == 0

    # Next attempt fails since stock is 0
    res4 = client.post(
        "/api/v1/artisan-reservations",
        json={"showcase_item_id": reservable_product.id, "quantity": 1, "pickup_date": tomorrow.isoformat()},
        headers=auth_header(traveler_user),
    )
    assert res4.status_code == 400
    assert "insufficient stock" in res4.json()["detail"].lower()

