"""
Comprehensive Unit & Integration Tests for Module 03 Requirements B15 & B16 (Safety Review):
- B15: Activity-Specific Discount Packages
  * Preview simulations (non-persistent pricing calculation, nonnegative pricing rule, boundaries)
  * Fail-closed on persistent targeting attempts: activity_id is NEVER falsely saved or silently lost
  * Conflict checks and date-window validation
  * Cross-provider tenant isolation
- B16: Activity-Specific Sponsored Campaigns
  * Reach & budget burn rate simulations with explicit disclaimer notices
  * Fail-closed on persistent targeting attempts: listing_reference_id is NEVER falsely saved
  * Strict anti-self-activation rules (403 Forbidden)
  * Cross-provider tenant isolation
- Activity-Specific List Endpoints:
  * Never falsely return provider-wide promotions as activity-linked
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.main import app as test_app
from app.models.activity import Activity
from app.models.provider_profile import ProviderProfile
from app.models.shared import PromotionCampaign, PromotionPackage
from app.models.user import Base, User
from app.routers.provider_activities import require_activity_provider

# Setup Isolated In-Memory SQLite Engine
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
def seeded_data(db_session: Session):
    """Seed test providers, users, and activities."""
    user1 = User(id=1, email="prov1@ceylongo.test", password_hash="h", full_name="Provider One", role="provider", is_active=True)
    user2 = User(id=2, email="prov2@ceylongo.test", password_hash="h", full_name="Provider Two", role="provider", is_active=True)
    traveler = User(id=3, email="trav@ceylongo.test", password_hash="h", full_name="Traveler Charlie", role="traveler", is_active=True)
    db_session.add_all([user1, user2, traveler])
    db_session.commit()

    prof1 = ProviderProfile(id=1, user_id=1, business_name="Surf Paradise", provider_type="activity", phone="0771112233", district="Matara", approval_status="approved")
    prof2 = ProviderProfile(id=2, user_id=2, business_name="Ella Hikes", provider_type="activity", phone="0774445566", district="Badulla", approval_status="approved")
    db_session.add_all([prof1, prof2])
    db_session.commit()

    act_approved = Activity(
        id=101,
        provider_profile_id=1,
        title="Weligama Beginner Surf Lesson",
        description="Learn to surf with experienced instructors.",
        category="adventure",
        district="Matara",
        city="Weligama",
        meeting_point="Weligama Beach Main Stall",
        duration_minutes=120,
        price=Decimal("8000.00"),
        pricing_type="per_person",
        currency="LKR",
        minimum_age=8,
        is_active=True,
        approval_status="approved",
    )
    act_other_prov = Activity(
        id=102,
        provider_profile_id=2,
        title="Ella Rock Guided Sunrise Hike",
        description="Scenic sunrise hike up Ella Rock.",
        category="adventure",
        district="Badulla",
        city="Ella",
        meeting_point="Ella Railway Station",
        duration_minutes=240,
        price=Decimal("6000.00"),
        pricing_type="per_person",
        currency="LKR",
        minimum_age=12,
        is_active=True,
        approval_status="approved",
    )
    act_unapproved = Activity(
        id=103,
        provider_profile_id=1,
        title="Secret Cave Exploration",
        description="Unapproved draft activity.",
        category="adventure",
        district="Matara",
        city="Mirissa",
        meeting_point="Mirissa Harbor",
        duration_minutes=90,
        price=Decimal("5000.00"),
        pricing_type="per_person",
        currency="LKR",
        minimum_age=16,
        is_active=True,
        approval_status="pending",
    )
    act_inactive = Activity(
        id=104,
        provider_profile_id=1,
        title="Seasonal Whale Watching",
        description="Whale watching off-season inactive.",
        category="adventure",
        district="Matara",
        city="Mirissa",
        meeting_point="Mirissa Fishery Port",
        duration_minutes=180,
        price=Decimal("12000.00"),
        pricing_type="per_person",
        currency="LKR",
        minimum_age=5,
        is_active=False,
        approval_status="approved",
    )
    db_session.add_all([act_approved, act_other_prov, act_unapproved, act_inactive])
    db_session.commit()

    return {
        "user1": user1,
        "prof1": prof1,
        "user2": user2,
        "prof2": prof2,
        "traveler": traveler,
        "act_approved": act_approved,
        "act_other_prov": act_other_prov,
        "act_unapproved": act_unapproved,
        "act_inactive": act_inactive,
    }


def set_auth(user: User, profile: ProviderProfile):
    test_app.dependency_overrides[get_current_user] = lambda: user
    test_app.dependency_overrides[require_activity_provider] = lambda: profile


# ==============================================================================
# B15: Activity-Specific Discount Packages Tests
# ==============================================================================


def test_activity_discount_preview_percentage(seeded_data):
    """Valid preview calculates correct percentage discount and discounted price with simulation notice."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 101,
        "discount_percent": "20.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=14)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["activity_id"] == 101
    assert data["activity_title"] == "Weligama Beginner Surf Lesson"
    assert Decimal(str(data["original_price"])) == Decimal("8000.00")
    # 20% off 8000 = 6400
    assert Decimal(str(data["discounted_price"])) == Decimal("6400.00")
    assert data["is_eligible"] is True
    assert data["targeting_scope"] == "activity"
    assert data["activity_targeting_status"] == "PREVIEW_ONLY_MIGRATION_PENDING"
    assert data["is_simulation"] is True
    assert "preview simulation" in data["simulation_notice"]


def test_activity_discount_preview_fixed_amount(seeded_data):
    """Valid preview calculates correct fixed amount discount and discounted price."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 101,
        "discount_amount": "1500.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 200
    data = res.json()
    # 8000 - 1500 = 6500
    assert Decimal(str(data["discounted_price"])) == Decimal("6500.00")
    assert data["is_eligible"] is True
    assert data["activity_targeting_status"] == "PREVIEW_ONLY_MIGRATION_PENDING"


def test_activity_discount_nonnegative_pricing_rule(seeded_data):
    """Fixed discount exceeding base activity price must be rejected with 422."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 101,
        "discount_amount": "9500.00",  # Activity price is 8000.00
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 422
    assert "cannot exceed activity price" in res.json()["detail"]


def test_activity_discount_percent_over_100_rejected(seeded_data):
    """Discount percentage over 100% must be rejected."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 101,
        "discount_percent": "150.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 422


def test_activity_discount_cross_provider_tenant_isolation(seeded_data):
    """Provider 1 attempting to target Provider 2's activity must receive 403 Forbidden."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 102,  # Owned by Provider 2
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 403
    assert "does not belong to this provider" in res.json()["detail"]


def test_activity_discount_nonexistent_activity(seeded_data):
    """Nonexistent activity ID must return 404."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 99999,
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 404


def test_activity_discount_unapproved_activity_rejected(seeded_data):
    """Discount on draft or pending activity must be rejected with 422."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 103,  # Status: pending
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 422
    assert "not eligible for promotions" in res.json()["detail"]


def test_activity_discount_inactive_activity_rejected(seeded_data):
    """Discount on inactive activity must be rejected with 422."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 104,  # is_active: False
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 422
    assert "inactive" in res.json()["detail"]


def test_activity_discount_invalid_date_window(seeded_data):
    """valid_to before valid_from or in the past must be rejected."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "activity_id": 101,
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today - timedelta(days=1)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/discounts/preview", json=payload)
    assert res.status_code == 422


def test_create_activity_targeted_discount_rejected_migration_pending(seeded_data):
    """
    CRITICAL TARGETING SAFETY:
    Supplying activity_id to persistent POST /discounts MUST be rejected with a clear
    migration-pending error (422). It must NEVER be falsely saved or silently discarded.
    """
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "title": "Summer Surf Kickoff",
        "discount_percent": "25.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=30)).isoformat(),
        "is_active": True,
        "activity_id": 101,
    }
    res = client.post("/api/v1/provider/activities/discounts", json=payload)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "Persistent activity-specific discount targeting is unavailable until Migration 041 is applied" in detail
    assert "Use POST /api/v1/provider/activities/discounts/preview" in detail


def test_update_activity_discount_with_activity_id_rejected(seeded_data):
    """Supplying activity_id on discount PATCH must be rejected with migration-pending error."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    # Create valid provider-wide discount
    create_res = client.post("/api/v1/provider/activities/discounts", json={
        "title": "Valid Provider Discount",
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=10)).isoformat(),
    })
    disc_id = create_res.json()["id"]

    patch_res = client.patch(f"/api/v1/provider/activities/discounts/{disc_id}", json={
        "activity_id": 101,
    })
    assert patch_res.status_code == 422
    assert "Persistent activity-specific discount targeting is unavailable until Migration 041 is applied" in patch_res.json()["detail"]


def test_create_provider_wide_discount_duplicate_conflict_check(seeded_data):
    """Overlapping active provider-wide discount with the same title must be rejected as conflicting."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "title": "Unique Monsoonal Discount",
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=20)).isoformat(),
        "is_active": True,
    }
    res1 = client.post("/api/v1/provider/activities/discounts", json=payload)
    assert res1.status_code == 201

    res2 = client.post("/api/v1/provider/activities/discounts", json=payload)
    assert res2.status_code == 422
    assert "already overlaps" in res2.json()["detail"]


def test_list_activity_discounts_empty_pending_migration(seeded_data):
    """
    GET /activities/{activity_id}/discounts must return an empty list.
    It must NEVER falsely return provider-wide promotions as if they are uniquely linked to this activity.
    """
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    # Create provider-wide discount
    client.post("/api/v1/provider/activities/discounts", json={
        "title": "Provider Wide Discount A",
        "discount_percent": "15.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=10)).isoformat(),
        "is_active": True,
    })

    res = client.get("/api/v1/provider/activities/activities/101/discounts")
    assert res.status_code == 200
    items = res.json()
    # Must be empty list: no physical activity-to-discount association exists in schema
    assert items == []


# ==============================================================================
# B16: Activity-Specific Sponsored Campaigns Tests
# ==============================================================================


def test_activity_campaign_preview_metrics(seeded_data):
    """Valid campaign preview calculates daily budget, duration, reach, and explicit simulation disclaimers."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "activity_id": 101,
        "budget": "15000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=11)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns/preview", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["activity_id"] == 101
    assert data["activity_title"] == "Weligama Beginner Surf Lesson"
    assert data["duration_days"] == 10.0
    assert Decimal(str(data["daily_budget"])) == Decimal("1500.00")
    assert data["estimated_impressions"] == 150000
    assert data["estimated_clicks"] == 4500
    assert data["is_eligible"] is True
    assert data["targeting_scope"] == "activity"
    assert data["activity_targeting_status"] == "PREVIEW_ONLY_MIGRATION_PENDING"
    assert data["is_simulation"] is True
    assert "simulated estimates" in data["simulation_notice"]


def test_activity_campaign_preview_budget_too_low(seeded_data):
    """Budget below LKR 500.00 must be rejected."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "activity_id": 101,
        "budget": "200.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns/preview", json=payload)
    assert res.status_code == 422
    assert "at least LKR 500.00" in res.json()["detail"]


def test_activity_campaign_preview_negative_budget(seeded_data):
    """Negative budget must fail schema validation."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "activity_id": 101,
        "budget": "-5000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns/preview", json=payload)
    assert res.status_code == 422


def test_activity_campaign_preview_invalid_dates(seeded_data):
    """Campaign ends_at must be strictly after starts_at and in the future."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "activity_id": 101,
        "budget": "5000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=5)).isoformat(),
        "ends_at": (now + timedelta(days=2)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns/preview", json=payload)
    assert res.status_code == 422
    assert "ends_at must be strictly after starts_at" in res.json()["detail"]


def test_activity_campaign_wrong_provider_access(seeded_data):
    """Provider 1 cannot preview a campaign for Provider 2's activity."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "activity_id": 102,
        "budget": "10000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=7)).isoformat(),
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns/preview", json=payload)
    assert res.status_code == 403
    assert "does not belong to this provider" in res.json()["detail"]


def test_create_activity_targeted_campaign_rejected_migration_pending(seeded_data):
    """
    CRITICAL TARGETING SAFETY:
    Supplying activity_id to persistent POST /promotions/campaigns MUST be rejected with a clear
    migration-pending error (422). It must NEVER be falsely saved or silently discarded.
    """
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    payload = {
        "campaign_name": "Peak Season Surf Promotion",
        "budget": "20000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=15)).isoformat(),
        "submit_for_review": True,
        "activity_id": 101,
    }
    res = client.post("/api/v1/provider/activities/promotions/campaigns", json=payload)
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "Persistent activity-specific campaign targeting is unavailable until Migration 030/041 is applied" in detail
    assert "Use POST /api/v1/provider/activities/promotions/campaigns/preview" in detail


def test_update_activity_campaign_with_activity_id_rejected(seeded_data):
    """Supplying activity_id on campaign PATCH must be rejected with migration-pending error."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    # Create valid provider-wide draft campaign
    create_res = client.post("/api/v1/provider/activities/promotions/campaigns", json={
        "campaign_name": "Provider Wide Campaign",
        "budget": "5000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
        "submit_for_review": False,
    })
    camp_id = create_res.json()["id"]

    patch_res = client.patch(f"/api/v1/provider/activities/promotions/campaigns/{camp_id}", json={
        "activity_id": 101,
    })
    assert patch_res.status_code == 422
    assert "Persistent activity-specific campaign targeting is unavailable until Migration 030/041 is applied" in patch_res.json()["detail"]


def test_create_activity_campaign_self_activation_rejected(seeded_data):
    """Provider cannot set status='active' directly."""
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    create_res = client.post("/api/v1/provider/activities/promotions/campaigns", json={
        "campaign_name": "Draft Camp",
        "budget": "5000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
        "submit_for_review": False,
    })
    camp_id = create_res.json()["id"]

    patch_res = client.patch(f"/api/v1/provider/activities/promotions/campaigns/{camp_id}", json={
        "status": "active",
    })
    assert patch_res.status_code == 403
    assert "Self-activation or self-approval is not permitted" in patch_res.json()["detail"]


def test_list_activity_campaigns_empty_pending_migration(seeded_data):
    """
    GET /activities/{activity_id}/promotions/campaigns must return an empty list.
    It must NEVER falsely return provider-wide campaigns as if they are uniquely linked to this activity.
    """
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    client.post("/api/v1/provider/activities/promotions/campaigns", json={
        "campaign_name": "Provider Wide Camp",
        "budget": "6000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=10)).isoformat(),
        "submit_for_review": True,
    })

    res = client.get("/api/v1/provider/activities/activities/101/promotions/campaigns")
    assert res.status_code == 200
    items = res.json()
    # Must be empty list: no physical listing_reference_id exists in schema
    assert items == []


def test_provider_wide_promotion_persistence_and_scope_distinction(seeded_data):
    """
    CRITICAL DISTINCTION:
    Confirms provider-wide promotions persist successfully with targeting_scope='provider'
    and activity_targeting_status='BLOCKED_BY_SCHEMA', cleanly distinct from preview simulations.
    """
    set_auth(seeded_data["user1"], seeded_data["prof1"])
    client = TestClient(test_app)

    today = date.today()
    # 1. Provider-wide discount
    disc_res = client.post("/api/v1/provider/activities/discounts", json={
        "title": "General Provider Discount",
        "discount_percent": "10.00",
        "currency": "LKR",
        "valid_from": today.isoformat(),
        "valid_to": (today + timedelta(days=15)).isoformat(),
        "is_active": True,
    })
    assert disc_res.status_code == 201
    disc_data = disc_res.json()
    assert disc_data["targeting_scope"] == "provider"
    assert disc_data["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"
    assert disc_data["activity_id"] is None

    # 2. Provider-wide campaign
    now = datetime.now(timezone.utc)
    camp_res = client.post("/api/v1/provider/activities/promotions/campaigns", json={
        "campaign_name": "General Provider Campaign",
        "budget": "10000.00",
        "currency": "LKR",
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=10)).isoformat(),
        "submit_for_review": True,
    })
    assert camp_res.status_code == 201
    camp_data = camp_res.json()
    assert camp_data["targeting_scope"] == "provider"
    assert camp_data["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"
    assert camp_data["activity_id"] is None
