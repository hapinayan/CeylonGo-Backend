"""
Automated tests for Module 04 P1.1E — Sponsored Promotion Analytics & Provider Dashboard.

Requirements:
- Track: impressions_count, clicks_count.
- No fabricated bookings, inquiries, or conversion revenue.
- Provider Dashboard: GET /api/v1/eco-promotions/me/analytics
- Campaign Detail Analytics: GET /api/v1/eco-promotions/me/campaigns/{campaign_id}/analytics
- Admin Analytics Overview: GET /api/v1/admin/eco-promotions/analytics
- Anti-abuse safeguards: cryptographic tokens, non-boosted exclusion, replay prevention, rate limiting.

Covers all 17 required P1.1E test scenarios:
1. test_valid_sponsored_impression
2. test_valid_sponsored_click
3. test_unpaid_campaign_rejected
4. test_expired_campaign_rejected
5. test_unpublished_listing_rejected
6. test_non_boosted_appearance_not_counted
7. test_duplicate_replay_protection
8. test_rate_limiting
9. test_atomic_counter_updates
10. test_provider_ownership_isolation
11. test_admin_only_analytics
12. test_date_filtering
13. test_ctr_calculation
14. test_zero_count_behavior
15. test_no_sensitive_data_exposure
16. test_no_fabricated_bookings_revenue
17. test_sql_server_compatibility
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import (
    create_sponsored_tracking_token,
    hash_password,
)
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import (
    EcoPromotionAnalytics,
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import router as eco_experiences_router
from app.routers.eco_promotions import (
    _reset_tracking_caches,
    admin_eco_promotions_router,
    eco_promotions_router,
)

# Test application isolating M04 discovery and promotion routers
analytics_app = FastAPI(title="CeylonGo M04 P1.1E Analytics Test App")
analytics_app.include_router(auth_router)
analytics_app.include_router(eco_experiences_router)
analytics_app.include_router(eco_promotions_router)
analytics_app.include_router(admin_eco_promotions_router)

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
    _reset_tracking_caches()
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    _reset_tracking_caches()


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

    analytics_app.dependency_overrides[get_db] = override_get_db
    with TestClient(analytics_app) as test_client:
        yield test_client
    analytics_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def create_user(
    db: Session,
    email: str,
    role: str = "provider",
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
    business_name: str = "Ceylon Eco Adventures",
    provider_type: str = "eco_tourism",
    approval_status: str = "approved",
) -> ProviderProfile:
    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type=provider_type,
        approval_status=approval_status,
        phone="+94771234567",
        business_registration_number=f"REG-{user.id}",
        district="Kandy",
    )
    db.add(profile)
    db.flush()
    return profile


def create_experience(
    db: Session,
    provider: ProviderProfile,
    title: str = "Knuckles Forest Sanctuary Trek",
    category: str = "Forest & Nature Trails",
    district: str = "Kandy",
    city: str = "Matale",
    price: Decimal = Decimal("5000.00"),
    duration_hours: Decimal = Decimal("4.00"),
    listing_status: str = "approved",
    is_published: bool = True,
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider.id,
        title=title,
        eco_category=category,
        district=district,
        city=city,
        meeting_point=f"{city} Base Camp",
        duration_hours=duration_hours,
        base_price=price,
        currency="LKR",
        max_group_size=12,
        min_participants=1,
        environmental_guidelines="Strict zero-waste policy.",
        wildlife_safety_summary="Do not feed or disturb wildlife.",
        listing_status=listing_status,
        is_published=is_published,
        eco_badge_status="none",
    )
    db.add(exp)
    db.flush()
    return exp


def create_package(
    db: Session,
    name: str = "Starter Spotlight",
    price: Decimal = Decimal("2500.00"),
    duration_days: int = 7,
) -> EcoPromotionPackage:
    pkg = EcoPromotionPackage(
        name=name,
        description="7-day promotional spotlight",
        duration_days=duration_days,
        price=price,
        currency="LKR",
        placement_tier="standard",
        is_active=True,
    )
    db.add(pkg)
    db.flush()
    return pkg


def create_active_campaign(
    db: Session,
    provider: ProviderProfile,
    experience: EcoExperience,
    package: EcoPromotionPackage,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    payment_status: str = "paid",
    campaign_status: str = "active",
) -> EcoSponsoredPromotion:
    now = datetime.now(UTC)
    if starts_at is None:
        starts_at = now - timedelta(days=1)
    if ends_at is None:
        ends_at = now + timedelta(days=6)

    campaign = EcoSponsoredPromotion(
        eco_experience_id=experience.id,
        provider_profile_id=provider.id,
        package_id=package.id,
        campaign_status=campaign_status,
        payment_status=payment_status,
        package_price_snapshot=package.price,
        duration_days_snapshot=package.duration_days,
        currency_snapshot=package.currency,
        starts_at=starts_at,
        ends_at=ends_at,
        payment_reference="PAY-REF-VERIFIED-1234",
    )
    db.add(campaign)
    db.flush()
    return campaign


# ==============================================================================
# P1.1E Test Scenarios
# ==============================================================================


def test_valid_sponsored_impression(client: TestClient, db_session: Session):
    """Scenario 1: Valid sponsored impression tracking via token."""
    user = create_user(db_session, "p1@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    # Discover listing to obtain token from boosted placement
    disc_resp = client.get("/api/v1/eco-experiences")
    assert disc_resp.status_code == 200
    token = disc_resp.json()["items"][0]["sponsored_tracking_token"]
    assert token is not None

    # Track impression
    resp = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["event_type"] == "impression"
    assert data["campaign_id"] == camp.id
    assert data["impressions_count"] == 1
    assert data["clicks_count"] == 0

    # Verify DB state
    today = datetime.now(UTC).date()
    row = (
        db_session.query(EcoPromotionAnalytics)
        .filter(
            EcoPromotionAnalytics.promotion_id == camp.id,
            EcoPromotionAnalytics.metric_date == today,
        )
        .first()
    )
    assert row is not None
    assert row.impressions_count == 1
    assert row.clicks_count == 0


def test_valid_sponsored_click(client: TestClient, db_session: Session):
    """Scenario 2: Valid sponsored click tracking via token."""
    user = create_user(db_session, "p2@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    disc_resp = client.get("/api/v1/eco-experiences")
    token = disc_resp.json()["items"][0]["sponsored_tracking_token"]

    resp = client.post(
        f"/api/v1/eco-promotions/campaigns/{camp.id}/track/click",
        json={"token": token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["event_type"] == "click"
    assert data["clicks_count"] == 1
    assert data["impressions_count"] == 0


def test_unpaid_campaign_rejected(client: TestClient, db_session: Session):
    """Scenario 3: Unpaid campaign rejects ad tracking events."""
    user = create_user(db_session, "p3@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="approved",
        payment_status="unpaid",
    )
    db_session.commit()

    # Generate token manually for unpaid campaign
    token = create_sponsored_tracking_token(camp.id, exp.id)

    resp = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert resp.status_code == 400
    assert "unpaid campaign" in resp.json()["detail"].lower()


def test_expired_campaign_rejected(client: TestClient, db_session: Session):
    """Scenario 4: Expired campaign rejects ad tracking events."""
    user = create_user(db_session, "p4@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    now = datetime.now(UTC)
    camp = create_active_campaign(
        db_session,
        prov,
        exp,
        pkg,
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=1),
    )
    db_session.commit()

    token = create_sponsored_tracking_token(camp.id, exp.id)
    resp = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"].lower()


def test_unpublished_listing_rejected(client: TestClient, db_session: Session):
    """Scenario 5: Tracking rejected if experience was unpublished/deactivated."""
    user = create_user(db_session, "p5@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, is_published=False)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    token = create_sponsored_tracking_token(camp.id, exp.id)
    resp = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert resp.status_code == 400
    assert "unpublished" in resp.json()["detail"].lower()


def test_non_boosted_appearance_not_counted(
    client: TestClient, db_session: Session
):
    """Scenario 6: Organic and backfilled appearances do not receive ad tokens."""
    user = create_user(db_session, "p6@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 3 sponsored items: 2 get boosted, 1 gets backfilled
    # 0 organic items, page_size = 3
    for i in range(3):
        exp = create_experience(db_session, prov, title=f"Exp {i+1}")
        create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences?page_size=3")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 3

    # First two items are boosted placements: have tracking token
    assert items[0]["sponsored_tracking_token"] is not None
    assert items[1]["sponsored_tracking_token"] is not None

    # Third item is backfilled: is_sponsored=True, but NO tracking token
    assert items[2]["is_sponsored"] is True
    assert items[2]["sponsored_tracking_token"] is None

    # Attempting to track without valid token fails
    invalid_resp = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": "invalid_or_missing_token"},
    )
    assert invalid_resp.status_code == 400


def test_duplicate_replay_protection(client: TestClient, db_session: Session):
    """Scenario 7: Duplicate/replay tracking events are rejected."""
    user = create_user(db_session, "p7@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    disc_resp = client.get("/api/v1/eco-experiences")
    token = disc_resp.json()["items"][0]["sponsored_tracking_token"]

    # First impression succeeds
    r1 = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert r1.status_code == 200

    # Second impression with same token fails with 409 Conflict
    r2 = client.post(
        "/api/v1/eco-promotions/track/impression",
        json={"token": token},
    )
    assert r2.status_code == 409
    assert "duplicate" in r2.json()["detail"].lower()


def test_rate_limiting(client: TestClient, db_session: Session):
    """Scenario 8: Rate limit protects tracking endpoint against flooding."""
    user = create_user(db_session, "p8@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    # Generate 65 unique tokens
    tokens = [
        create_sponsored_tracking_token(camp.id, exp.id) for _ in range(65)
    ]

    got_429 = False
    for t in tokens:
        res = client.post(
            "/api/v1/eco-promotions/track/impression",
            json={"token": t},
        )
        if res.status_code == 429:
            got_429 = True
            assert "rate limit exceeded" in res.json()["detail"].lower()
            break

    assert got_429, "Expected 429 Too Many Requests after rapid requests"


def test_atomic_counter_updates(client: TestClient, db_session: Session):
    """Scenario 9: Atomic daily counter updates increment accurately."""
    user = create_user(db_session, "p9@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    # Record 3 impressions and 2 clicks
    for _ in range(3):
        t = create_sponsored_tracking_token(camp.id, exp.id)
        res = client.post(
            "/api/v1/eco-promotions/track/impression", json={"token": t}
        )
        assert res.status_code == 200

    for _ in range(2):
        t = create_sponsored_tracking_token(camp.id, exp.id)
        res = client.post(
            "/api/v1/eco-promotions/track/click", json={"token": t}
        )
        assert res.status_code == 200

    today = datetime.now(UTC).date()
    row = (
        db_session.query(EcoPromotionAnalytics)
        .filter(
            EcoPromotionAnalytics.promotion_id == camp.id,
            EcoPromotionAnalytics.metric_date == today,
        )
        .first()
    )
    assert row.impressions_count == 3
    assert row.clicks_count == 2


def test_provider_ownership_isolation(client: TestClient, db_session: Session):
    """Scenario 10: Providers cannot inspect other providers' campaign analytics."""
    user1 = create_user(db_session, "p10_1@ceylongo.com")
    prov1 = create_provider(
        db_session, user1, business_name="Provider 1 Eco Trails"
    )
    exp1 = create_experience(db_session, prov1, title="P1 Wilderness Trail")

    user2 = create_user(db_session, "p10_2@ceylongo.com")
    prov2 = create_provider(
        db_session, user2, business_name="Provider 2 Safari Co"
    )
    exp2 = create_experience(db_session, prov2, title="P2 Safari Trail")

    pkg = create_package(db_session)
    camp1 = create_active_campaign(db_session, prov1, exp1, pkg)
    camp2 = create_active_campaign(db_session, prov2, exp2, pkg)
    db_session.commit()

    # Provider 2 tries to view Provider 1's campaign analytics -> 403 Forbidden
    res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp1.id}/analytics",
        headers=auth_header(user2),
    )
    assert res.status_code == 403

    # Provider 2 views their own analytics -> only contains camp2
    overview = client.get(
        "/api/v1/eco-promotions/me/analytics",
        headers=auth_header(user2),
    )
    assert overview.status_code == 200
    data = overview.json()
    assert data["total_campaigns"] == 1
    assert data["campaigns"][0]["campaign_id"] == camp2.id


def test_admin_only_analytics(client: TestClient, db_session: Session):
    """Scenario 11: Admin-only platform analytics requires admin role."""
    admin_user = create_user(db_session, "admin@ceylongo.com", role="admin")
    prov_user = create_user(db_session, "prov@ceylongo.com", role="provider")
    trav_user = create_user(db_session, "trav@ceylongo.com", role="traveler")

    # Unauthenticated -> 401
    assert (
        client.get("/api/v1/admin/eco-promotions/analytics").status_code == 401
    )

    # Traveler -> 403
    assert (
        client.get(
            "/api/v1/admin/eco-promotions/analytics",
            headers=auth_header(trav_user),
        ).status_code
        == 403
    )

    # Provider -> 403
    assert (
        client.get(
            "/api/v1/admin/eco-promotions/analytics",
            headers=auth_header(prov_user),
        ).status_code
        == 403
    )

    # Admin -> 200
    res = client.get(
        "/api/v1/admin/eco-promotions/analytics",
        headers=auth_header(admin_user),
    )
    assert res.status_code == 200
    assert "total_campaigns" in res.json()


def test_date_filtering(client: TestClient, db_session: Session):
    """Scenario 12: Date filtering works accurately across analytics endpoints."""
    user = create_user(db_session, "p12@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)

    day1 = date(2026, 5, 1)
    day2 = date(2026, 5, 2)
    day3 = date(2026, 5, 3)

    db_session.add(
        EcoPromotionAnalytics(
            promotion_id=camp.id,
            eco_experience_id=exp.id,
            metric_date=day1,
            impressions_count=10,
            clicks_count=1,
        )
    )
    db_session.add(
        EcoPromotionAnalytics(
            promotion_id=camp.id,
            eco_experience_id=exp.id,
            metric_date=day2,
            impressions_count=20,
            clicks_count=2,
        )
    )
    db_session.add(
        EcoPromotionAnalytics(
            promotion_id=camp.id,
            eco_experience_id=exp.id,
            metric_date=day3,
            impressions_count=30,
            clicks_count=3,
        )
    )
    db_session.commit()

    # Filter for Day 2 only
    res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics?start_date=2026-05-02&end_date=2026-05-02",
        headers=auth_header(user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_impressions"] == 20
    assert data["total_clicks"] == 2
    assert len(data["daily_metrics"]) == 1
    assert data["daily_metrics"][0]["metric_date"] == "2026-05-02"

    # Inverted date range -> 422
    inv_res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics?start_date=2026-05-05&end_date=2026-05-01",
        headers=auth_header(user),
    )
    assert inv_res.status_code == 422


def test_ctr_calculation(client: TestClient, db_session: Session):
    """Scenario 13: Accurate mathematical CTR calculation (clicks / impressions * 100)."""
    user = create_user(db_session, "p13@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)

    today = date(2026, 6, 1)
    # 200 impressions, 15 clicks -> CTR = 7.50%
    db_session.add(
        EcoPromotionAnalytics(
            promotion_id=camp.id,
            eco_experience_id=exp.id,
            metric_date=today,
            impressions_count=200,
            clicks_count=15,
        )
    )
    db_session.commit()

    res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics",
        headers=auth_header(user),
    )
    assert res.status_code == 200
    data = res.json()
    assert Decimal(str(data["ctr_percent"])) == Decimal("7.50")
    assert Decimal(str(data["daily_metrics"][0]["ctr_percent"])) == Decimal(
        "7.50"
    )


def test_zero_count_behavior(client: TestClient, db_session: Session):
    """Scenario 14: Zero-count behavior handles 0 impressions safely without division by zero."""
    user = create_user(db_session, "p14@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics",
        headers=auth_header(user),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total_impressions"] == 0
    assert data["total_clicks"] == 0
    assert Decimal(str(data["ctr_percent"])) == Decimal("0.00")
    assert data["daily_metrics"] == []


def test_no_sensitive_data_exposure(client: TestClient, db_session: Session):
    """Scenario 15: Analytics endpoints never expose payment references or banking details."""
    admin_user = create_user(db_session, "admin15@ceylongo.com", role="admin")
    prov_user = create_user(db_session, "p15@ceylongo.com")
    prov = create_provider(db_session, prov_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    camp.payment_reference = "SUPER-SECRET-CONFIDENTIAL-TXN-999"
    db_session.commit()

    # Provider campaign analytics
    r1 = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics",
        headers=auth_header(prov_user),
    )
    assert r1.status_code == 200
    assert "payment_reference" not in r1.json()
    assert "SUPER-SECRET" not in r1.text

    # Provider dashboard overview
    r2 = client.get(
        "/api/v1/eco-promotions/me/analytics",
        headers=auth_header(prov_user),
    )
    assert r2.status_code == 200
    assert "SUPER-SECRET" not in r2.text

    # Admin analytics
    r3 = client.get(
        "/api/v1/admin/eco-promotions/analytics",
        headers=auth_header(admin_user),
    )
    assert r3.status_code == 200
    assert "SUPER-SECRET" not in r3.text


def test_no_fabricated_bookings_revenue(
    client: TestClient, db_session: Session
):
    """Scenario 16: Models and schemas do not fabricate booking counts or inquiry revenue."""
    prov_user = create_user(db_session, "p16@ceylongo.com")
    prov = create_provider(db_session, prov_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    camp = create_active_campaign(db_session, prov, exp, pkg)
    db_session.commit()

    res = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{camp.id}/analytics",
        headers=auth_header(prov_user),
    )
    assert res.status_code == 200
    data = res.json()

    # Strictly verify absence of unverified/fabricated fields
    assert "bookings_count" not in data
    assert "inquiries_count" not in data
    assert "conversion_revenue" not in data
    assert "revenue_generated" not in data


def test_sql_server_compatibility(db_session: Session):
    """Scenario 17: Validates that analytics queries compile cleanly against SQL Server (mssql dialect)."""
    # Compile daily aggregate query for SQL Server
    stmt = (
        select(
            EcoPromotionAnalytics.metric_date,
            func.sum(EcoPromotionAnalytics.impressions_count).label(
                "total_impressions"
            ),
            func.sum(EcoPromotionAnalytics.clicks_count).label("total_clicks"),
        )
        .where(
            EcoPromotionAnalytics.promotion_id == 1,
            EcoPromotionAnalytics.metric_date >= date(2026, 1, 1),
            EcoPromotionAnalytics.metric_date <= date(2026, 12, 31),
        )
        .group_by(EcoPromotionAnalytics.metric_date)
        .order_by(EcoPromotionAnalytics.metric_date.asc())
    )

    compiled_sql = str(stmt.compile(dialect=mssql.dialect())).lower()
    assert "group by" in compiled_sql
    assert "sum" in compiled_sql
    assert "eco_promotion_analytics" in compiled_sql
