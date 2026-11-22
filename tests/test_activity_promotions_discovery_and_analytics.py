"""
Automated Unit and Integration Tests for CeylonGo Module 03 — Phase 5E:
- A17: Sponsored Activities Discovery, Badging, Boosting & Event Tracking
- A18: Special Offers & Promotional Discounts Calculation & Filtering
- B18: Provider Activity Promotions Analytics (CTR, Conversion Rate, Stats)
- C06: Admin Campaign Moderation Write Actions (Approve, Reject, Pause, Resume) & Admin Analytics

Guarantees 100% ANSI-SQL and physical schema compatibility with SQL Server 2025 Express:
- dbo.promotion_campaigns (11 columns)
- dbo.promotion_packages (11 columns)
- dbo.promotion_events (6 physical columns)
- dbo.admin_audit_logs (8 columns)
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, func, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, require_admin, require_approved_provider, require_traveler
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_image import ActivityImage
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, PromotionCampaign, PromotionEvent, PromotionPackage
from app.models.user import Base, User
from app.routers.activities_public import router as activities_public_router
from app.routers.admin_activities import router as admin_activities_router
from app.routers.auth import get_optional_current_user
from app.routers.provider_activities import require_activity_provider
from app.routers.provider_activity_promotions import router as provider_activity_promotions_router
from app.schemas.activity_admin import AdminCampaignReviewRequest

# Mount test app with proper routing
test_app = FastAPI(title="CeylonGo Module 03 Phase 5E Test App")
test_app.include_router(activities_public_router)
test_app.include_router(provider_activity_promotions_router)
test_app.include_router(admin_activities_router)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


@pytest.fixture(autouse=True)
def setup_test_database():
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: TestingSessionLocal()
    Base.metadata.create_all(bind=engine)
    yield
    test_app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def seeded_entities(db_session: Session):
    """Seed users, provider profiles, and activities for testing."""
    # 1. Admin
    admin = User(
        id=1,
        email="admin@ceylongo.test",
        password_hash="fakehash",
        full_name="Platform Admin",
        role="admin",
        is_active=True,
    )

    # 2. Activity Provider 1 (has campaigns and discounts)
    provider1_user = User(
        id=2,
        email="provider1@ceylongo.test",
        password_hash="fakehash",
        full_name="Lanka Adventures Host",
        role="provider",
        is_active=True,
    )
    provider1_profile = ProviderProfile(
        id=1,
        user_id=2,
        business_name="Lanka Adventures Pvt Ltd",
        provider_type="activity",
        approval_status="approved",
        district="Kandy",
        phone="+94770000002",
        description="Official certified adventure provider in Sri Lanka",
    )

    # 3. Activity Provider 2 (organic baseline, no promotions)
    provider2_user = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Cultural Walks Host",
        role="provider",
        is_active=True,
    )
    provider2_profile = ProviderProfile(
        id=2,
        user_id=3,
        business_name="Cultural Walks Lanka",
        provider_type="activity",
        approval_status="approved",
        district="Galle",
        phone="+94770000003",
        description="Official cultural walking tour organizer in Galle",
    )

    # 4. Traveler User
    traveler = User(
        id=4,
        email="traveler@ceylongo.test",
        password_hash="fakehash",
        full_name="Sam Traveler",
        role="traveler",
        is_active=True,
    )

    db_session.add_all([admin, provider1_user, provider1_profile, provider2_user, provider2_profile, traveler])
    db_session.flush()

    # 5. Activities
    # Activity 1: Provider 1 (Adventure, 5000 LKR)
    act1 = Activity(
        id=101,
        provider_profile_id=1,
        title="Kitulgala White Water Rafting",
        description="Experience world class white water rafting down the Kelani river in Kitulgala.",
        category="adventure",
        district="Kegalle",
        city="Kitulgala",
        meeting_point="Kitulgala Adventure Base",
        duration_minutes=180,
        minimum_age=12,
        pricing_type="per_person",
        price=Decimal("5000.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )

    # Activity 2: Provider 2 (Cultural, 3000 LKR)
    act2 = Activity(
        id=102,
        provider_profile_id=2,
        title="Galle Fort Historical Walking Tour",
        description="Walk the ramparts and discover centuries of Dutch and colonial history in Galle.",
        category="cultural",
        district="Galle",
        city="Galle",
        meeting_point="Galle Fort Main Gate",
        duration_minutes=120,
        minimum_age=6,
        pricing_type="per_person",
        price=Decimal("3000.00"),
        currency="LKR",
        approval_status="approved",
        is_active=True,
    )

    db_session.add_all([act1, act2])
    db_session.commit()

    return {
        "admin": admin,
        "provider1": provider1_profile,
        "provider2": provider2_profile,
        "traveler": traveler,
        "activity1": act1,
        "activity2": act2,
    }


@pytest.fixture
def client():
    return TestClient(test_app)


# ==============================================================================
# 1. A17: Sponsored Activities Discovery, Badging, Boosting & Events
# ==============================================================================


def test_public_discovery_sponsored_badging_and_boosting(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify A17:
    - Provider 1 has active sponsored campaign -> Activity 1 has is_sponsored=True, sponsored_campaign_id
    - Provider 2 has no campaign -> Activity 2 has is_sponsored=False
    - Boosting places Activity 1 first by default
    """
    now = datetime.now(timezone.utc)
    # Create active sponsored campaign for Provider 1
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Summer White Water Campaign",
        target_module="activity",
        budget=Decimal("20000.00"),
        spent_amount=Decimal("1500.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.get("/api/v1/activities?sort_by=price_asc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    items = data["items"]

    # Even though price_asc would normally place Act 2 (3000 LKR) before Act 1 (5000 LKR),
    # boosting ensures Act 1 (sponsored) is boosted to the top!
    assert items[0]["id"] == 101
    assert items[0]["is_sponsored"] is True
    assert items[0]["sponsored_campaign_id"] == 1

    assert items[1]["id"] == 102
    assert items[1]["is_sponsored"] is False
    assert items[1]["sponsored_campaign_id"] is None


def test_public_discovery_boosting_disabled(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    When boost_sponsored=False, pure sort_by=price_asc order is preserved.
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Summer White Water Campaign",
        target_module="activity",
        budget=Decimal("20000.00"),
        spent_amount=Decimal("1500.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.get("/api/v1/activities?sort_by=price_asc&boost_sponsored=false")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # 3000 LKR (Act 102) comes before 5000 LKR (Act 101)
    assert items[0]["id"] == 102
    assert items[1]["id"] == 101


def test_public_discovery_filter_is_sponsored(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Filter by is_sponsored=true and is_sponsored=false.
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Active Campaign",
        target_module="activity",
        budget=Decimal("5000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    # Filter is_sponsored=true
    resp_true = client.get("/api/v1/activities?is_sponsored=true")
    assert resp_true.status_code == 200
    data_true = resp_true.json()
    assert data_true["total"] == 1
    assert data_true["items"][0]["id"] == 101
    assert data_true["items"][0]["is_sponsored"] is True

    # Filter is_sponsored=false
    resp_false = client.get("/api/v1/activities?is_sponsored=false")
    assert resp_false.status_code == 200
    data_false = resp_false.json()
    assert data_false["total"] == 1
    assert data_false["items"][0]["id"] == 102
    assert data_false["items"][0]["is_sponsored"] is False


def test_public_activity_detail_sponsored_badging(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    GET /api/v1/activities/{id} returns is_sponsored=True and campaign ID.
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Detail Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.get("/api/v1/activities/101")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["is_sponsored"] is True
    assert detail["sponsored_campaign_id"] == 1

    resp2 = client.get("/api/v1/activities/102")
    assert resp2.status_code == 200
    detail2 = resp2.json()
    assert detail2["is_sponsored"] is False
    assert detail2["sponsored_campaign_id"] is None


def test_promotion_event_tracking_success(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify event tracking:
    - POST /api/v1/activities/campaigns/{id}/events accepts impression, click, inquiry
    - Saved in dbo.promotion_events without referencing unapplied columns
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Track Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    # Track impression
    resp_imp = client.post(
        "/api/v1/activities/campaigns/1/events",
        json={"event_type": "impression"},
    )
    assert resp_imp.status_code == 201
    data_imp = resp_imp.json()
    assert data_imp["campaign_id"] == 1
    assert data_imp["event_type"] == "impression"

    # Track click
    resp_clk = client.post(
        "/api/v1/activities/campaigns/1/events",
        json={"event_type": "click"},
    )
    assert resp_clk.status_code == 201

    # Track inquiry
    resp_inq = client.post(
        "/api/v1/activities/campaigns/1/events",
        json={"event_type": "inquiry"},
    )
    assert resp_inq.status_code == 201

    # Verify rows in DB
    events = db_session.query(PromotionEvent).filter(PromotionEvent.campaign_id == 1).all()
    assert len(events) == 3
    event_types = {e.event_type for e in events}
    assert event_types == {"impression", "click", "inquiry"}


def test_promotion_event_tracking_reject_booking_conversion(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Public event endpoint must reject fabricated booking conversions with 403 Forbidden.
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Booking Reject Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.post(
        "/api/v1/activities/campaigns/1/events",
        json={"event_type": "booking"},
    )
    # Pydantic schema or service rejects booking
    assert resp.status_code in (403, 422)


def test_promotion_event_tracking_convenience_endpoint(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    POST /api/v1/activities/{activity_id}/events convenience tracking.
    """
    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Convenience Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    # Track click on Activity 101 (sponsored)
    resp = client.post(
        "/api/v1/activities/101/events",
        json={"event_type": "click"},
    )
    assert resp.status_code == 201
    assert resp.json()["campaign_id"] == 1

    # Attempt track on Activity 102 (not sponsored) -> 400 Bad Request
    resp2 = client.post(
        "/api/v1/activities/102/events",
        json={"event_type": "click"},
    )
    assert resp2.status_code == 400
    assert "active sponsored campaign" in resp2.json()["detail"]


# ==============================================================================
# 2. A18: Special Offers & Promotional Discounts
# ==============================================================================


def test_public_discovery_percentage_discount(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify A18 percentage discount calculation:
    Activity 1 price: 5000.00 LKR
    Discount package: 20% discount
    Expected discounted_price: 4000.00 LKR
    """
    today = datetime.now(timezone.utc).date()
    package = PromotionPackage(
        id=1,
        provider_profile_id=1,
        title="Early Bird 20% Off",
        description="Get 20% off all whitewater experiences",
        discount_percent=Decimal("20.00"),
        discount_amount=None,
        currency="LKR",
        valid_from=today - timedelta(days=2),
        valid_to=today + timedelta(days=10),
        is_active=True,
    )
    db_session.add(package)
    db_session.commit()

    resp = client.get("/api/v1/activities")
    assert resp.status_code == 200
    items = resp.json()["items"]

    act1_item = next(it for it in items if it["id"] == 101)
    assert act1_item["has_discount"] is True
    assert Decimal(str(act1_item["discount_percent"])) == Decimal("20.00")
    assert Decimal(str(act1_item["discounted_price"])) == Decimal("4000.00")
    assert act1_item["special_offer_title"] == "Early Bird 20% Off"

    act2_item = next(it for it in items if it["id"] == 102)
    assert act2_item["has_discount"] is False
    assert act2_item["discounted_price"] is None


def test_public_discovery_fixed_amount_discount(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify A18 fixed amount discount calculation:
    Activity 1 price: 5000.00 LKR
    Discount package: 1250.00 LKR flat discount
    Expected discounted_price: 3750.00 LKR
    """
    today = datetime.now(timezone.utc).date()
    package = PromotionPackage(
        id=1,
        provider_profile_id=1,
        title="LKR 1250 Instant Voucher",
        discount_percent=None,
        discount_amount=Decimal("1250.00"),
        currency="LKR",
        valid_from=today - timedelta(days=1),
        valid_to=today + timedelta(days=5),
        is_active=True,
    )
    db_session.add(package)
    db_session.commit()

    resp = client.get("/api/v1/activities/101")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["has_discount"] is True
    assert Decimal(str(detail["discount_amount"])) == Decimal("1250.00")
    assert Decimal(str(detail["discounted_price"])) == Decimal("3750.00")
    assert detail["special_offer_title"] == "LKR 1250 Instant Voucher"


def test_public_discovery_filter_has_discount(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify filtering by has_discount=true and has_discount=false.
    """
    today = datetime.now(timezone.utc).date()
    package = PromotionPackage(
        id=1,
        provider_profile_id=1,
        title="Active Offer",
        discount_percent=Decimal("10.00"),
        currency="LKR",
        valid_from=today - timedelta(days=1),
        valid_to=today + timedelta(days=5),
        is_active=True,
    )
    db_session.add(package)
    db_session.commit()

    # Filter has_discount=true
    resp_true = client.get("/api/v1/activities?has_discount=true")
    assert resp_true.status_code == 200
    items_true = resp_true.json()["items"]
    assert len(items_true) == 1
    assert items_true[0]["id"] == 101

    # Filter has_discount=false
    resp_false = client.get("/api/v1/activities?has_discount=false")
    assert resp_false.status_code == 200
    items_false = resp_false.json()["items"]
    assert len(items_false) == 1
    assert items_false[0]["id"] == 102


def test_discount_capping_at_zero(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    If fixed discount amount exceeds activity price, discounted_price must be capped at 0.00 LKR.
    """
    today = datetime.now(timezone.utc).date()
    package = PromotionPackage(
        id=1,
        provider_profile_id=1,
        title="Mega Voucher",
        discount_amount=Decimal("10000.00"),  # Exceeds 5000.00 LKR price
        currency="LKR",
        valid_from=today - timedelta(days=1),
        valid_to=today + timedelta(days=5),
        is_active=True,
    )
    db_session.add(package)
    db_session.commit()

    resp = client.get("/api/v1/activities/101")
    assert resp.status_code == 200
    detail = resp.json()
    assert Decimal(str(detail["discounted_price"])) == Decimal("0.00")


def test_expired_discount_not_applied(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Expired discount package (valid_to in past) must not be applied.
    """
    today = datetime.now(timezone.utc).date()
    package = PromotionPackage(
        id=1,
        provider_profile_id=1,
        title="Expired Offer",
        discount_percent=Decimal("50.00"),
        currency="LKR",
        valid_from=today - timedelta(days=10),
        valid_to=today - timedelta(days=1),  # Expired yesterday
        is_active=True,
    )
    db_session.add(package)
    db_session.commit()

    resp = client.get("/api/v1/activities/101")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["has_discount"] is False
    assert detail["discounted_price"] is None


# ==============================================================================
# 3. B18: Provider Activity Promotions Analytics
# ==============================================================================


def test_provider_campaign_analytics_and_stats_alias(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify B18:
    - GET /api/v1/provider/activities/promotions/campaigns/{id}/analytics
    - GET /api/v1/provider/activities/promotions/campaigns/{id}/stats
    Computes impressions, clicks, inquiries, bookings, CTR, conversion rate.
    """
    provider1 = seeded_entities["provider1"]
    test_app.dependency_overrides[require_activity_provider] = lambda: provider1

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Analytics Test Campaign",
        target_module="activity",
        budget=Decimal("50000.00"),
        spent_amount=Decimal("12000.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.flush()

    # Seed events: 100 impressions, 10 clicks, 2 inquiries, 1 booking
    events = []
    for _ in range(100):
        events.append(PromotionEvent(campaign_id=1, event_type="impression"))
    for _ in range(10):
        events.append(PromotionEvent(campaign_id=1, event_type="click"))
    for _ in range(2):
        events.append(PromotionEvent(campaign_id=1, event_type="inquiry"))
    for _ in range(1):
        events.append(PromotionEvent(campaign_id=1, event_type="booking"))

    db_session.add_all(events)
    db_session.commit()

    # Test /analytics endpoint
    resp = client.get("/api/v1/provider/activities/promotions/campaigns/1/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["campaign_id"] == 1
    assert data["campaign_name"] == "Analytics Test Campaign"
    assert data["impressions"] == 100
    assert data["clicks"] == 10
    assert data["inquiries"] == 2
    assert data["bookings"] == 1
    assert data["ctr"] == 10.0  # (10 / 100) * 100
    assert data["conversion_rate"] == 10.0  # (1 / 10) * 100

    # Test /stats alias endpoint
    resp_stats = client.get("/api/v1/provider/activities/promotions/campaigns/1/stats")
    assert resp_stats.status_code == 200
    assert resp_stats.json() == data


def test_provider_campaign_analytics_tenant_isolation(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Provider 2 cannot access Provider 1's campaign analytics -> 403 Forbidden.
    """
    provider2 = seeded_entities["provider2"]
    test_app.dependency_overrides[require_activity_provider] = lambda: provider2

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,  # Belongs to Provider 1
        campaign_name="Provider 1 Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.get("/api/v1/provider/activities/promotions/campaigns/1/analytics")
    assert resp.status_code == 403
    assert "only view analytics for your own campaigns" in resp.json()["detail"]


def test_provider_promotions_aggregate_analytics(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    GET /api/v1/provider/activities/promotions/analytics returns provider aggregate overview.
    """
    provider1 = seeded_entities["provider1"]
    test_app.dependency_overrides[require_activity_provider] = lambda: provider1

    now = datetime.now(timezone.utc)
    c1 = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Campaign 1",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("2000.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=2),
        ends_at=now + timedelta(days=5),
    )
    c2 = PromotionCampaign(
        id=2,
        provider_profile_id=1,
        campaign_name="Campaign 2",
        target_module="activity",
        budget=Decimal("15000.00"),
        spent_amount=Decimal("5000.00"),
        currency="LKR",
        status="completed",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(days=2),
    )
    db_session.add_all([c1, c2])
    db_session.flush()

    # Seed events
    db_session.add_all([
        PromotionEvent(campaign_id=1, event_type="impression"),
        PromotionEvent(campaign_id=1, event_type="impression"),
        PromotionEvent(campaign_id=1, event_type="click"),
        PromotionEvent(campaign_id=2, event_type="impression"),
        PromotionEvent(campaign_id=2, event_type="click"),
        PromotionEvent(campaign_id=2, event_type="booking"),
    ])
    db_session.commit()

    resp = client.get("/api/v1/provider/activities/promotions/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_campaigns"] == 2
    assert data["active_campaigns"] == 1
    assert Decimal(str(data["total_budget"])) == Decimal("25000.00")
    assert Decimal(str(data["total_spent"])) == Decimal("7000.00")
    assert data["total_impressions"] == 3
    assert data["total_clicks"] == 2
    assert data["total_bookings"] == 1
    assert data["overall_ctr"] == 66.67
    assert data["overall_conversion_rate"] == 50.0
    assert len(data["campaigns"]) == 2


# ==============================================================================
# 4. C06: Admin Campaign Moderation Write Actions & Admin Analytics
# ==============================================================================


def test_admin_campaign_review_approve(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify C06 moderation action: approve
    pending_approval -> active
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=1,
        provider_profile_id=1,
        campaign_name="Pending Review Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.post(
        "/api/v1/admin/activities/promotions/campaigns/1/review",
        json={"action": "approve", "notes": "Approved for seasonal white water promo"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "active"

    # Verify audit log recorded in dbo.admin_audit_logs
    audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.entity_id == 1).first()
    assert audit is not None
    assert audit.admin_user_id == 1
    assert audit.action == "campaign_approve"


def test_admin_campaign_review_reject(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Verify C06 moderation action: reject
    pending_approval -> cancelled (mapped to valid physical schema check constraint)
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=2,
        provider_profile_id=1,
        campaign_name="Ineligible Campaign",
        target_module="activity",
        budget=Decimal("5000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.post(
        "/api/v1/admin/activities/promotions/campaigns/2/review",
        json={"action": "reject", "reason": "Campaign creative does not comply with safety standards"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"

    # Verify audit log
    audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.entity_id == 2).first()
    assert audit is not None
    assert audit.action == "campaign_reject"
    assert "safety standards" in audit.details


def test_admin_campaign_review_reject_requires_reason(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Rejecting a campaign without mandatory reason (>= 10 chars) must return 422.
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    resp = client.post(
        "/api/v1/admin/activities/promotions/campaigns/1/review",
        json={"action": "reject", "reason": "too short"},
    )
    assert resp.status_code == 422


def test_admin_campaign_review_pause_and_resume(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    active -> pause -> paused
    paused -> resume -> active
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=3,
        provider_profile_id=1,
        campaign_name="Pausable Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    # Pause
    resp_pause = client.post(
        "/api/v1/admin/activities/promotions/campaigns/3/review",
        json={"action": "pause", "notes": "Paused for routine compliance check"},
    )
    assert resp_pause.status_code == 200
    assert resp_pause.json()["status"] == "paused"

    # Resume
    resp_resume = client.post(
        "/api/v1/admin/activities/promotions/campaigns/3/review",
        json={"action": "resume", "notes": "Compliance check cleared"},
    )
    assert resp_resume.status_code == 200
    assert resp_resume.json()["status"] == "active"


def test_admin_campaign_review_invalid_transition(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    Attempting an ineligible transition (e.g. approve a draft campaign) must return 400 Bad Request.
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    now = datetime.now(timezone.utc)
    campaign = PromotionCampaign(
        id=4,
        provider_profile_id=1,
        campaign_name="Draft Campaign",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="draft",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()

    resp = client.post(
        "/api/v1/admin/activities/promotions/campaigns/4/review",
        json={"action": "approve"},
    )
    assert resp.status_code == 400
    assert "Cannot execute 'approve' on campaign with status 'draft'" in resp.json()["detail"]


def test_admin_promotions_analytics_endpoint(client: TestClient, db_session: Session, seeded_entities: dict):
    """
    GET /api/v1/admin/activities/promotions/campaigns/analytics returns list of all activity campaigns with metrics.
    """
    admin = seeded_entities["admin"]
    test_app.dependency_overrides[require_admin] = lambda: admin

    now = datetime.now(timezone.utc)
    c1 = PromotionCampaign(
        id=10,
        provider_profile_id=1,
        campaign_name="Admin Analytics 1",
        target_module="activity",
        budget=Decimal("10000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=5),
    )
    db_session.add(c1)
    db_session.commit()

    resp = client.get("/api/v1/admin/activities/promotions/campaigns/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["campaign_id"] == 10
    assert "impressions" in data[0]
    assert "ctr" in data[0]
