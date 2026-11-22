"""
Automated tests for Module 04 P1.1B — Admin Sponsored Promotion Package Management.

Requirements:
- P16: Provider Sponsored Promotions (Catalog browsing)
- A07: Admin Promotion Management (Package configuration)

Covers:
1. test_admin_creates_valid_package: Admin creates valid package with proper defaults.
2. test_admin_create_package_duplicate_name_rejected: Case-insensitive unique name enforcement (409 Conflict).
3. test_admin_create_package_validation_failures: Negative price, <= 0 duration, invalid tier/currency/extra fields (422).
4. test_admin_lists_packages_pagination_and_filtering: Pagination (page, page_size) and is_active filtering.
5. test_admin_gets_package_detail: 200 OK for existing, 404 Not Found for non-existent.
6. test_admin_updates_package: Configurable fields updated, duplicate name collision rejected (409).
7. test_admin_disables_package_via_toggle: Invert and explicit is_active=False.
8. test_admin_re_enables_package_via_toggle: Invert and explicit is_active=True.
9. test_traveler_and_provider_access_rejected: 403 Forbidden for traveler and provider callers.
10. test_unauthenticated_access_rejected: 401 Unauthorized for callers without token.
11. test_public_catalog_excludes_inactive_packages_and_deterministic_order: Active-only, price/duration ASC order.
12. test_historical_campaign_snapshots_remain_unchanged_after_package_update: Campaign price snapshot immutability.
13. test_no_campaign_becomes_active_through_package_management: Status and payment immutability during package operations.
14. test_hard_delete_prohibited: DELETE method not allowed (405).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import (
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_promotions import (
    admin_eco_promotions_router,
    eco_promotions_router,
)

# Test application isolating M04 promotion routers
eco_p11b_app = FastAPI(title="CeylonGo M04 P1.1B Promotions Test App")
eco_p11b_app.include_router(auth_router)
eco_p11b_app.include_router(eco_promotions_router)
eco_p11b_app.include_router(admin_eco_promotions_router)

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
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_p11b_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p11b_app) as test_client:
        yield test_client
    eco_p11b_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def create_user(
    db: Session,
    email: str,
    role: str = "admin",
    is_active: bool = True,
) -> User:
    user = User(
        full_name=f"User {email}",
        email=email,
        password_hash=hash_password("Pass123!"),
        role=role,
        is_active=is_active,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()
    return user


def create_provider(
    db: Session,
    user: User,
    business_name: str = "Ella Eco Tours",
) -> ProviderProfile:
    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94771234567",
        business_registration_number=f"REG-{user.id}",
        district="Badulla",
    )
    db.add(profile)
    db.flush()
    return profile


def create_experience(
    db: Session,
    provider: ProviderProfile,
    title: str = "Ella Ridge Trek",
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider.id,
        title=title,
        eco_category="Forest & Nature Trails",
        district="Badulla",
        city="Ella",
        meeting_point="Ella Railway Station",
        duration_hours=Decimal("4.00"),
        base_price=Decimal("4500.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=1,
        environmental_guidelines="Strict zero-waste policy.",
        wildlife_safety_summary="Observe wildlife from safe distances.",
        listing_status="approved",
        is_published=True,
    )
    db.add(exp)
    db.flush()
    return exp


# ==============================================================================
# Unit & Integration Tests
# ==============================================================================


def test_admin_creates_valid_package(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    payload = {
        "name": "Summer Boost",
        "description": "High visibility spotlight for summer season.",
        "duration_days": 21,
        "price": "6500.00",
        "currency": "LKR",
        "placement_tier": "featured",
        "is_active": True,
    }

    res = client.post(
        "/api/v1/admin/eco-promotions/packages",
        json=payload,
        headers=auth_header(admin),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Summer Boost"
    assert data["duration_days"] == 21
    assert Decimal(data["price"]) == Decimal("6500.00")
    assert data["currency"] == "LKR"
    assert data["placement_tier"] == "featured"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


def test_admin_create_package_duplicate_name_rejected(
    client: TestClient, db_session: Session
):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg = EcoPromotionPackage(
        name="Starter Spotlight",
        duration_days=7,
        price=Decimal("2500.00"),
        currency="LKR",
        placement_tier="standard",
    )
    db_session.add(pkg)
    db_session.commit()

    payload = {
        "name": "  starter spotlight  ",  # Case-insensitive duplicate with whitespace
        "duration_days": 10,
        "price": "3000.00",
        "currency": "LKR",
        "placement_tier": "standard",
    }

    res = client.post(
        "/api/v1/admin/eco-promotions/packages",
        json=payload,
        headers=auth_header(admin),
    )
    assert res.status_code == 409
    assert "already exists" in res.json()["detail"]


@pytest.mark.parametrize(
    "invalid_data,expected_field",
    [
        ({"name": "Test", "duration_days": 0, "price": "100.00"}, "duration_days"),
        ({"name": "Test", "duration_days": -5, "price": "100.00"}, "duration_days"),
        ({"name": "Test", "duration_days": 7, "price": "-50.00"}, "price"),
        ({"name": "Test", "duration_days": 7, "price": "100.00", "placement_tier": "ultra"}, "placement_tier"),
        ({"name": "   ", "duration_days": 7, "price": "100.00"}, "name"),
        ({"name": "Test", "duration_days": 7, "price": "100.00", "currency": "USD"}, "currency"),
        ({"name": "Test", "duration_days": 7, "price": "100.00", "unexpected_field": "injected"}, "unexpected_field"),
    ],
)
def test_admin_create_package_validation_failures(
    client: TestClient, db_session: Session, invalid_data: dict, expected_field: str
):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    res = client.post(
        "/api/v1/admin/eco-promotions/packages",
        json=invalid_data,
        headers=auth_header(admin),
    )
    assert res.status_code == 422


def test_admin_lists_packages_pagination_and_filtering(
    client: TestClient, db_session: Session
):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    p1 = EcoPromotionPackage(name="Pkg 1", duration_days=5, price=Decimal("1000.00"), is_active=True)
    p2 = EcoPromotionPackage(name="Pkg 2", duration_days=10, price=Decimal("2000.00"), is_active=True)
    p3 = EcoPromotionPackage(name="Pkg 3", duration_days=15, price=Decimal("3000.00"), is_active=True)
    p4 = EcoPromotionPackage(name="Pkg 4", duration_days=20, price=Decimal("4000.00"), is_active=False)
    db_session.add_all([p1, p2, p3, p4])
    db_session.commit()

    # Pagination test (page 1, size 2)
    res = client.get(
        "/api/v1/admin/eco-promotions/packages?page=1&page_size=2",
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 4
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total_pages"] == 2
    assert len(data["items"]) == 2

    # Filter active only
    res_active = client.get(
        "/api/v1/admin/eco-promotions/packages?is_active=true",
        headers=auth_header(admin),
    )
    assert res_active.status_code == 200
    data_active = res_active.json()
    assert data_active["total"] == 3
    assert all(item["is_active"] is True for item in data_active["items"])

    # Filter inactive only
    res_inactive = client.get(
        "/api/v1/admin/eco-promotions/packages?is_active=false",
        headers=auth_header(admin),
    )
    assert res_inactive.status_code == 200
    data_inactive = res_inactive.json()
    assert data_inactive["total"] == 1
    assert data_inactive["items"][0]["name"] == "Pkg 4"


def test_admin_gets_package_detail(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg = EcoPromotionPackage(
        name="Single Pkg",
        description="Detail test description.",
        duration_days=7,
        price=Decimal("1500.00"),
        placement_tier="standard",
    )
    db_session.add(pkg)
    db_session.commit()

    # 200 OK
    res = client.get(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}",
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Single Pkg"
    assert res.json()["description"] == "Detail test description."

    # 404 Not Found
    res_404 = client.get(
        "/api/v1/admin/eco-promotions/packages/99999",
        headers=auth_header(admin),
    )
    assert res_404.status_code == 404


def test_admin_updates_package(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg1 = EcoPromotionPackage(name="Pkg Alpha", duration_days=7, price=Decimal("2000.00"))
    pkg2 = EcoPromotionPackage(name="Pkg Beta", duration_days=14, price=Decimal("4000.00"))
    db_session.add_all([pkg1, pkg2])
    db_session.commit()

    # Successful update
    update_payload = {
        "name": "Pkg Alpha Updated",
        "description": "Brand new description",
        "price": "2200.00",
        "placement_tier": "featured",
    }
    res = client.put(
        f"/api/v1/admin/eco-promotions/packages/{pkg1.id}",
        json=update_payload,
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["name"] == "Pkg Alpha Updated"
    assert Decimal(data["price"]) == Decimal("2200.00")
    assert data["placement_tier"] == "featured"

    # Duplicate name collision with pkg2
    collision_payload = {"name": "Pkg Beta"}
    res_col = client.put(
        f"/api/v1/admin/eco-promotions/packages/{pkg1.id}",
        json=collision_payload,
        headers=auth_header(admin),
    )
    assert res_col.status_code == 409

    # 404 Not Found on update
    res_404 = client.put(
        "/api/v1/admin/eco-promotions/packages/88888",
        json={"name": "Does Not Matter"},
        headers=auth_header(admin),
    )
    assert res_404.status_code == 404


def test_admin_disables_package_via_toggle(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg = EcoPromotionPackage(name="Toggle Pkg", duration_days=7, price=Decimal("1500.00"), is_active=True)
    db_session.add(pkg)
    db_session.commit()

    # Invert toggle (True -> False)
    res = client.patch(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle",
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is False

    db_session.refresh(pkg)
    assert pkg.is_active is False

    # Explicit toggle payload
    res_explicit = client.patch(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle",
        json={"is_active": False},
        headers=auth_header(admin),
    )
    assert res_explicit.status_code == 200
    assert res_explicit.json()["is_active"] is False


def test_admin_re_enables_package_via_toggle(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg = EcoPromotionPackage(name="Disabled Pkg", duration_days=7, price=Decimal("1500.00"), is_active=False)
    db_session.add(pkg)
    db_session.commit()

    # Invert toggle (False -> True)
    res = client.patch(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle",
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["is_active"] is True

    db_session.refresh(pkg)
    assert pkg.is_active is True

    # 404 for non-existent ID
    res_404 = client.patch(
        "/api/v1/admin/eco-promotions/packages/77777/toggle",
        headers=auth_header(admin),
    )
    assert res_404.status_code == 404


def test_traveler_and_provider_access_rejected(client: TestClient, db_session: Session):
    traveler = create_user(db_session, "traveler@ceylongo.com", role="traveler")
    provider_user = create_user(db_session, "provider@ceylongo.com", role="provider")
    create_provider(db_session, provider_user)

    pkg = EcoPromotionPackage(name="Restricted Pkg", duration_days=7, price=Decimal("1500.00"))
    db_session.add(pkg)
    db_session.commit()

    # Traveler rejected (403)
    assert client.get("/api/v1/admin/eco-promotions/packages", headers=auth_header(traveler)).status_code == 403
    assert client.post("/api/v1/admin/eco-promotions/packages", json={"name": "T"}, headers=auth_header(traveler)).status_code == 403
    assert client.put(f"/api/v1/admin/eco-promotions/packages/{pkg.id}", json={"name": "T"}, headers=auth_header(traveler)).status_code == 403
    assert client.patch(f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle", headers=auth_header(traveler)).status_code == 403

    # Provider rejected (403)
    assert client.get("/api/v1/admin/eco-promotions/packages", headers=auth_header(provider_user)).status_code == 403
    assert client.post("/api/v1/admin/eco-promotions/packages", json={"name": "P"}, headers=auth_header(provider_user)).status_code == 403
    assert client.put(f"/api/v1/admin/eco-promotions/packages/{pkg.id}", json={"name": "P"}, headers=auth_header(provider_user)).status_code == 403
    assert client.patch(f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle", headers=auth_header(provider_user)).status_code == 403


def test_unauthenticated_access_rejected(client: TestClient, db_session: Session):
    pkg = EcoPromotionPackage(name="Public Check Pkg", duration_days=7, price=Decimal("1500.00"))
    db_session.add(pkg)
    db_session.commit()

    assert client.get("/api/v1/admin/eco-promotions/packages").status_code == 401
    assert client.post("/api/v1/admin/eco-promotions/packages", json={}).status_code == 401
    assert client.put(f"/api/v1/admin/eco-promotions/packages/{pkg.id}", json={}).status_code == 401
    assert client.patch(f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle").status_code == 401


def test_public_catalog_excludes_inactive_packages_and_deterministic_order(
    client: TestClient, db_session: Session
):
    # Seed out-of-order packages with active & inactive states
    p_high = EcoPromotionPackage(name="High Tier", duration_days=30, price=Decimal("8500.00"), is_active=True)
    p_inactive = EcoPromotionPackage(name="Inactive Deal", duration_days=5, price=Decimal("1000.00"), is_active=False)
    p_low = EcoPromotionPackage(name="Low Tier", duration_days=7, price=Decimal("2500.00"), is_active=True)
    p_mid = EcoPromotionPackage(name="Mid Tier", duration_days=14, price=Decimal("4500.00"), is_active=True)

    db_session.add_all([p_high, p_inactive, p_low, p_mid])
    db_session.commit()

    # Public catalog endpoint requires NO authentication
    res = client.get("/api/v1/eco-promotions/packages")
    assert res.status_code == 200
    items = res.json()

    # Inactive package must be excluded
    names = [i["name"] for i in items]
    assert "Inactive Deal" not in names
    assert len(items) == 3

    # Deterministic order by price ASC, duration ASC, id ASC
    assert items[0]["name"] == "Low Tier"
    assert items[1]["name"] == "Mid Tier"
    assert items[2]["name"] == "High Tier"


def test_historical_campaign_snapshots_remain_unchanged_after_package_update(
    client: TestClient, db_session: Session
):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "provider@ceylongo.com", role="provider")
    provider = create_provider(db_session, provider_user)
    exp = create_experience(db_session, provider)

    pkg = EcoPromotionPackage(
        name="Snapshot Anchor Package",
        duration_days=7,
        price=Decimal("2500.00"),
        currency="LKR",
        placement_tier="standard",
    )
    db_session.add(pkg)
    db_session.commit()

    # Create campaign snapshotting package price and duration
    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=provider.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
        payment_status="unpaid",
    )
    db_session.add(campaign)
    db_session.commit()

    # Admin updates package price to 3500.00 and duration to 14
    res = client.put(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}",
        json={"price": "3500.00", "duration_days": 14},
        headers=auth_header(admin),
    )
    assert res.status_code == 200

    # Verify campaign snapshot remains completely unchanged
    db_session.refresh(campaign)
    assert campaign.package_price_snapshot == Decimal("2500.00")
    assert campaign.duration_days_snapshot == 7
    assert campaign.currency_snapshot == "LKR"


def test_no_campaign_becomes_active_through_package_management(
    client: TestClient, db_session: Session
):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "provider@ceylongo.com", role="provider")
    provider = create_provider(db_session, provider_user)
    exp = create_experience(db_session, provider)

    pkg = EcoPromotionPackage(name="Safety Test Package", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=provider.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
        payment_status="unpaid",
    )
    db_session.add(campaign)
    db_session.commit()

    # Update package price and description
    client.put(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}",
        json={"price": "2999.00"},
        headers=auth_header(admin),
    )
    db_session.refresh(campaign)
    assert campaign.campaign_status == "pending_approval"
    assert campaign.payment_status == "unpaid"

    # Toggle package inactive
    client.patch(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle",
        headers=auth_header(admin),
    )
    db_session.refresh(campaign)
    assert campaign.campaign_status == "pending_approval"
    assert campaign.payment_status == "unpaid"

    # Toggle package active again
    client.patch(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}/toggle",
        headers=auth_header(admin),
    )
    db_session.refresh(campaign)
    assert campaign.campaign_status == "pending_approval"
    assert campaign.payment_status == "unpaid"


def test_hard_delete_prohibited(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")

    pkg = EcoPromotionPackage(name="No Delete Package", duration_days=7, price=Decimal("2500.00"))
    db_session.add(pkg)
    db_session.commit()

    res = client.delete(
        f"/api/v1/admin/eco-promotions/packages/{pkg.id}",
        headers=auth_header(admin),
    )
    assert res.status_code == 405  # Method Not Allowed
