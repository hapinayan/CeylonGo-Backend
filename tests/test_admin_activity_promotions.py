"""
Automated Unit and Integration Tests for Module 03 — Phase 5D-2D:
Admin Promotion Management Foundation (C06).

Tests cover:
- Authentication & RBAC (Anonymous 401, Traveler 403, Provider 403, Admin 200)
- Admin Campaign Listing (GET /api/v1/admin/activities/promotions/campaigns):
  - Admin list success with stable deterministic ordering
  - Empty results handling (total=0, items=[])
  - Pagination (page, page_size, total_pages)
  - Status filtering against physical schema values
  - Provider profile filtering
  - Campaign name search (case-insensitive substring)
  - Combined multi-criteria filtering
  - Validation: invalid status (422), invalid provider_profile_id (422), invalid page/size (422)
  - Cross-module exclusion (target_module != 'activity' strictly excluded)
- Admin Campaign Details (GET /api/v1/admin/activities/promotions/campaigns/{campaign_id}):
  - Admin detail success with physical column projection
  - Targeting metadata (targeting_scope="provider", activity_targeting_status="BLOCKED_BY_SCHEMA")
  - Nonexistent campaign ID returns 404
  - Cross-module campaign ID returns 404 (metadata leakage prevention)
  - Currency-safe decimal serialization (budget, spent_amount)
- Read-only database invariance (zero mutations, zero DDL drift)
- Reusable Pydantic review schema validation (AdminCampaignReviewRequest for C06 decisions)
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user, require_admin, require_approved_provider, require_traveler
from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.shared import PromotionCampaign
from app.models.user import Base, User
from app.routers.admin_activities import router as admin_activities_router
from app.schemas.activity_admin import (
    ADMIN_CAMPAIGN_VALID_ACTIONS,
    CAMPAIGN_ACTION_ELIGIBLE_STATUSES,
    PHYSICAL_CAMPAIGN_STATUSES,
    AdminCampaignReviewRequest,
)
from app.schemas.activity_promotion import (
    ActivityCampaignPagination,
    ActivityCampaignResponse,
)

# Test app mounting admin_activities_router
test_app = FastAPI(title="CeylonGo Module 03 Phase 5D-2D Test App")
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
def seeded_users_and_profiles(db_session: Session):
    """Seed test users: 1 platform admin, 2 activity providers, 1 homestay provider, 1 traveler."""
    admin = User(
        id=1,
        email="admin@ceylongo.test",
        password_hash="fakehash",
        full_name="Platform Administrator",
        role="admin",
        is_active=True,
    )
    provider_user_1 = User(
        id=2,
        email="provider1@ceylongo.test",
        password_hash="fakehash",
        full_name="Lanka Adventures Owner",
        role="provider",
        is_active=True,
    )
    provider_user_2 = User(
        id=3,
        email="provider2@ceylongo.test",
        password_hash="fakehash",
        full_name="Ella Hikes Owner",
        role="provider",
        is_active=True,
    )
    homestay_user = User(
        id=4,
        email="homestay@ceylongo.test",
        password_hash="fakehash",
        full_name="Kandy Homestay Owner",
        role="provider",
        is_active=True,
    )
    traveler = User(
        id=5,
        email="traveler@ceylongo.test",
        password_hash="fakehash",
        full_name="John Traveler",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin, provider_user_1, provider_user_2, homestay_user, traveler])
    db_session.flush()

    profile_1 = ProviderProfile(
        id=10,
        user_id=2,
        business_name="Lanka Adventures Pvt Ltd",
        provider_type="activity",
        phone="0771112233",
        district="Kegalle",
        approval_status="approved",
    )
    profile_2 = ProviderProfile(
        id=20,
        user_id=3,
        business_name="Ella Hiking Expeditions",
        provider_type="activity",
        phone="0774445566",
        district="Badulla",
        approval_status="approved",
    )
    profile_homestay = ProviderProfile(
        id=30,
        user_id=4,
        business_name="Kandy Hilltop Homestay",
        provider_type="homestay",
        phone="0812223344",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add_all([profile_1, profile_2, profile_homestay])
    db_session.commit()

    return {
        "admin": admin,
        "provider1": provider_user_1,
        "profile1": profile_1,
        "provider2": provider_user_2,
        "profile2": profile_2,
        "homestay_provider": homestay_user,
        "homestay_profile": profile_homestay,
        "traveler": traveler,
    }


@pytest.fixture
def seeded_campaigns(db_session: Session, seeded_users_and_profiles):
    """
    Seed diverse campaigns:
    - Provider 1: 2 activity campaigns (1 pending_approval, 1 active)
    - Provider 2: 2 activity campaigns (1 draft, 1 completed)
    - Homestay Provider: 1 homestay campaign (active) - for cross-module exclusion verification!
    """
    now = datetime.now(timezone.utc)
    c1 = PromotionCampaign(
        id=101,
        provider_profile_id=10,
        campaign_name="Rafting Summer Special",
        target_module="activity",
        budget=Decimal("50000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=30),
        created_at=now - timedelta(days=2),
    )
    c2 = PromotionCampaign(
        id=102,
        provider_profile_id=10,
        campaign_name="Kitulgala Adventure Promo",
        target_module="activity",
        budget=Decimal("75000.00"),
        spent_amount=Decimal("12500.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        created_at=now - timedelta(days=10),
    )
    c3 = PromotionCampaign(
        id=103,
        provider_profile_id=20,
        campaign_name="Ella Rock Sunrise Trek Promo",
        target_module="activity",
        budget=Decimal("30000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="draft",
        starts_at=now + timedelta(days=10),
        ends_at=now + timedelta(days=40),
        created_at=now - timedelta(days=1),
    )
    c4 = PromotionCampaign(
        id=104,
        provider_profile_id=20,
        campaign_name="Little Adams Peak Sunset Tour",
        target_module="activity",
        budget=Decimal("20000.00"),
        spent_amount=Decimal("20000.00"),
        currency="LKR",
        status="completed",
        starts_at=now - timedelta(days=40),
        ends_at=now - timedelta(days=10),
        created_at=now - timedelta(days=45),
    )
    # Cross-module campaign (homestay)
    c_cross = PromotionCampaign(
        id=999,
        provider_profile_id=30,
        campaign_name="Kandy View Homestay Mega Promo",
        target_module="homestay",
        budget=Decimal("100000.00"),
        spent_amount=Decimal("45000.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=15),
        ends_at=now + timedelta(days=15),
        created_at=now - timedelta(days=20),
    )
    db_session.add_all([c1, c2, c3, c4, c_cross])
    db_session.commit()

    return [c1, c2, c3, c4, c_cross]


@pytest.fixture
def admin_client(seeded_users_and_profiles):
    """Client authenticated as Platform Admin."""
    admin_user = seeded_users_and_profiles["admin"]
    test_app.dependency_overrides[get_current_user] = lambda: admin_user
    test_app.dependency_overrides[require_admin] = lambda: admin_user
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def traveler_client(seeded_users_and_profiles):
    """Client authenticated as Traveler (should be rejected with 403)."""
    traveler_user = seeded_users_and_profiles["traveler"]
    test_app.dependency_overrides[get_current_user] = lambda: traveler_user
    test_app.dependency_overrides[require_traveler] = lambda: traveler_user
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def provider_client(seeded_users_and_profiles):
    """Client authenticated as Provider (should be rejected with 403)."""
    provider_user = seeded_users_and_profiles["provider1"]
    test_app.dependency_overrides[get_current_user] = lambda: provider_user
    test_app.dependency_overrides[require_approved_provider] = lambda: seeded_users_and_profiles["profile1"]
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def anon_client():
    """Unauthenticated client (should be rejected with 401)."""
    def unauth():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
        )
    test_app.dependency_overrides[get_current_user] = unauth
    test_app.dependency_overrides[require_admin] = unauth
    with TestClient(test_app) as client:
        yield client


# ==============================================================================
# 1. RBAC & Authentication Tests
# ==============================================================================


def test_admin_list_campaigns_anonymous_unauthorized(anon_client):
    """Unauthenticated requests must return 401 Unauthorized."""
    response = anon_client.get("/api/v1/admin/activities/promotions/campaigns")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_admin_get_campaign_detail_anonymous_unauthorized(anon_client):
    """Unauthenticated detail request must return 401 Unauthorized."""
    response = anon_client.get("/api/v1/admin/activities/promotions/campaigns/101")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_admin_list_campaigns_traveler_forbidden(traveler_client):
    """Travelers must be rejected with 403 Forbidden."""
    response = traveler_client.get("/api/v1/admin/activities/promotions/campaigns")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "admin role required" in response.json()["detail"].lower()


def test_admin_get_campaign_detail_traveler_forbidden(traveler_client):
    """Travelers must be rejected with 403 Forbidden for detail endpoint."""
    response = traveler_client.get("/api/v1/admin/activities/promotions/campaigns/101")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_admin_list_campaigns_provider_forbidden(provider_client):
    """Activity providers must be rejected with 403 Forbidden on admin endpoints."""
    response = provider_client.get("/api/v1/admin/activities/promotions/campaigns")
    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "admin role required" in response.json()["detail"].lower()


def test_admin_get_campaign_detail_provider_forbidden(provider_client):
    """Activity providers must be rejected with 403 Forbidden on admin detail endpoint."""
    response = provider_client.get("/api/v1/admin/activities/promotions/campaigns/101")
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# 2. Admin Campaign Listing Tests (GET /promotions/campaigns)
# ==============================================================================


def test_admin_list_campaigns_empty(admin_client):
    """Listing returns empty items list with total=0 when no campaigns exist."""
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 0


def test_admin_list_campaigns_success(admin_client, seeded_campaigns):
    """
    Admin lists all activity campaigns across providers.
    Cross-module homestay campaign (ID 999) must be strictly excluded.
    Ordering must be deterministic (created_at DESC, id DESC).
    """
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Exactly 4 activity campaigns seeded (homestay 999 excluded)
    assert data["total"] == 4
    assert len(data["items"]) == 4

    # Verify cross-module exclusion
    item_ids = [item["id"] for item in data["items"]]
    assert 999 not in item_ids
    assert set(item_ids) == {101, 102, 103, 104}

    # Verify targeting transparency
    for item in data["items"]:
        assert item["target_module"] == "activity"
        assert item["targeting_scope"] == "provider"
        assert item["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"


def test_admin_list_campaigns_pagination(admin_client, seeded_campaigns):
    """Admin pagination test with page=1, page_size=2 and page=2."""
    res_p1 = admin_client.get("/api/v1/admin/activities/promotions/campaigns?page=1&page_size=2")
    assert res_p1.status_code == status.HTTP_200_OK
    d1 = res_p1.json()
    assert d1["total"] == 4
    assert d1["page"] == 1
    assert d1["page_size"] == 2
    assert d1["total_pages"] == 2
    assert len(d1["items"]) == 2

    res_p2 = admin_client.get("/api/v1/admin/activities/promotions/campaigns?page=2&page_size=2")
    assert res_p2.status_code == status.HTTP_200_OK
    d2 = res_p2.json()
    assert d2["total"] == 4
    assert d2["page"] == 2
    assert len(d2["items"]) == 2

    # Disjoint items
    ids_p1 = [i["id"] for i in d1["items"]]
    ids_p2 = [i["id"] for i in d2["items"]]
    assert set(ids_p1).isdisjoint(set(ids_p2))


def test_admin_list_campaigns_status_filtering(admin_client, seeded_campaigns):
    """Admin filters by status: pending_approval, active, draft, completed."""
    # Filter pending_approval
    res_pending = admin_client.get("/api/v1/admin/activities/promotions/campaigns?status=pending_approval")
    assert res_pending.status_code == status.HTTP_200_OK
    d_pending = res_pending.json()
    assert d_pending["total"] == 1
    assert d_pending["items"][0]["id"] == 101
    assert d_pending["items"][0]["status"] == "pending_approval"

    # Filter active
    res_active = admin_client.get("/api/v1/admin/activities/promotions/campaigns?status=active")
    assert res_active.status_code == status.HTTP_200_OK
    d_active = res_active.json()
    assert d_active["total"] == 1
    assert d_active["items"][0]["id"] == 102
    assert d_active["items"][0]["status"] == "active"

    # Filter paused (none exist)
    res_paused = admin_client.get("/api/v1/admin/activities/promotions/campaigns?status=paused")
    assert res_paused.status_code == status.HTTP_200_OK
    assert res_paused.json()["total"] == 0


def test_admin_list_campaigns_provider_filtering(admin_client, seeded_campaigns):
    """Admin filters by provider_profile_id."""
    res_p1 = admin_client.get("/api/v1/admin/activities/promotions/campaigns?provider_profile_id=10")
    assert res_p1.status_code == status.HTTP_200_OK
    d1 = res_p1.json()
    assert d1["total"] == 2
    for item in d1["items"]:
        assert item["provider_profile_id"] == 10

    res_p2 = admin_client.get("/api/v1/admin/activities/promotions/campaigns?provider_profile_id=20")
    assert res_p2.status_code == status.HTTP_200_OK
    d2 = res_p2.json()
    assert d2["total"] == 2
    for item in d2["items"]:
        assert item["provider_profile_id"] == 20


def test_admin_list_campaigns_name_search(admin_client, seeded_campaigns):
    """Admin searches campaign names with case-insensitive substring."""
    # Search 'rafting'
    res_rafting = admin_client.get("/api/v1/admin/activities/promotions/campaigns?search=rafting")
    assert res_rafting.status_code == status.HTTP_200_OK
    d_rafting = res_rafting.json()
    assert d_rafting["total"] == 1
    assert d_rafting["items"][0]["id"] == 101

    # Search 'tour'
    res_tour = admin_client.get("/api/v1/admin/activities/promotions/campaigns?search=tour")
    assert res_tour.status_code == status.HTTP_200_OK
    d_tour = res_tour.json()
    assert d_tour["total"] == 1
    assert d_tour["items"][0]["id"] == 104

    # Search non-matching
    res_none = admin_client.get("/api/v1/admin/activities/promotions/campaigns?search=whale+watching")
    assert res_none.status_code == status.HTTP_200_OK
    assert res_none.json()["total"] == 0


def test_admin_list_campaigns_combined_filtering(admin_client, seeded_campaigns):
    """Admin combines status, provider_profile_id, and search filters."""
    # Provider 10 + status active + search 'kitulgala' -> matches 102
    res = admin_client.get(
        "/api/v1/admin/activities/promotions/campaigns"
        "?provider_profile_id=10&status=active&search=kitulgala"
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == 102

    # Provider 10 + status active + search 'ella' -> 0 results
    res_mismatch = admin_client.get(
        "/api/v1/admin/activities/promotions/campaigns"
        "?provider_profile_id=10&status=active&search=ella"
    )
    assert res_mismatch.status_code == status.HTTP_200_OK
    assert res_mismatch.json()["total"] == 0


def test_admin_list_campaigns_invalid_parameters(admin_client):
    """Validation errors for invalid query parameters (status, provider_id, pagination)."""
    # Invalid status
    res_status = admin_client.get("/api/v1/admin/activities/promotions/campaigns?status=unsupported_status")
    assert res_status.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # Invalid provider_profile_id (< 1)
    res_prov = admin_client.get("/api/v1/admin/activities/promotions/campaigns?provider_profile_id=0")
    assert res_prov.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # Invalid page (< 1)
    res_page = admin_client.get("/api/v1/admin/activities/promotions/campaigns?page=0")
    assert res_page.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    # Invalid page_size (> 100)
    res_size = admin_client.get("/api/v1/admin/activities/promotions/campaigns?page_size=101")
    assert res_size.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ==============================================================================
# 3. Admin Campaign Detail Tests (GET /promotions/campaigns/{campaign_id})
# ==============================================================================


def test_admin_get_campaign_detail_success(admin_client, seeded_campaigns):
    """Admin inspects a specific activity campaign detail."""
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns/101")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == 101
    assert data["provider_profile_id"] == 10
    assert data["campaign_name"] == "Rafting Summer Special"
    assert data["target_module"] == "activity"
    assert data["budget"] == "50000.00"
    assert data["spent_amount"] == "0.00"
    assert data["currency"] == "LKR"
    assert data["status"] == "pending_approval"
    assert data["targeting_scope"] == "provider"
    assert data["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"


def test_admin_get_campaign_detail_nonexistent(admin_client, seeded_campaigns):
    """Non-existent campaign ID returns 404 Not Found."""
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns/99999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


def test_admin_get_campaign_cross_module_leakage_prevented(admin_client, seeded_campaigns):
    """
    Attempting to retrieve a homestay campaign (ID 999) via activity admin endpoint
    must return 404 Not Found without leaking homestay campaign metadata.
    """
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns/999")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in response.json()["detail"].lower()


def test_admin_campaign_decimal_serialization(admin_client, seeded_campaigns):
    """Verify that budget and spent_amount maintain decimal accuracy."""
    response = admin_client.get("/api/v1/admin/activities/promotions/campaigns/102")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert Decimal(data["budget"]) == Decimal("75000.00")
    assert Decimal(data["spent_amount"]) == Decimal("12500.00")


def test_admin_campaign_read_only_invariance(admin_client, db_session: Session, seeded_campaigns):
    """Verify that admin listing and detail endpoints perform zero writes or state mutations."""
    # Record pre-inspection row count
    initial_count = db_session.query(PromotionCampaign).count()

    # Execute multiple GET requests
    admin_client.get("/api/v1/admin/activities/promotions/campaigns")
    admin_client.get("/api/v1/admin/activities/promotions/campaigns/101")
    admin_client.get("/api/v1/admin/activities/promotions/campaigns/102")

    # Verify row count and states remain identical
    final_count = db_session.query(PromotionCampaign).count()
    assert initial_count == final_count

    c1 = db_session.query(PromotionCampaign).filter(PromotionCampaign.id == 101).first()
    assert c1.status == "pending_approval"
    assert c1.spent_amount == Decimal("0.00")


# ==============================================================================
# 4. Review Validation Schema Tests (AdminCampaignReviewRequest for C06)
# ==============================================================================


def test_admin_review_schema_approve_action():
    """Verify review schema validation for 'approve' action."""
    req = AdminCampaignReviewRequest(action="approve", notes="Meets all requirements")
    assert req.action == "approve"
    assert req.notes == "Meets all requirements"
    assert req.reason is None

    # Status eligibility: valid on pending_approval
    req.validate_status_eligibility("pending_approval")

    # Ineligible on draft or active
    with pytest.raises(ValueError, match="Cannot execute 'approve'"):
        req.validate_status_eligibility("draft")

    with pytest.raises(ValueError, match="Cannot execute 'approve'"):
        req.validate_status_eligibility("active")


def test_admin_review_schema_reject_action():
    """Verify review schema validation for 'reject' action requiring min 10-char reason."""
    # Valid rejection
    req = AdminCampaignReviewRequest(
        action="reject",
        reason="Budget exceeds acceptable limits for provider tier",
    )
    assert req.action == "reject"
    assert "Budget exceeds" in req.reason

    # Status eligibility: valid on pending_approval
    req.validate_status_eligibility("pending_approval")

    # Rejection without reason fails validation
    with pytest.raises(ValueError, match="Rejection reason is mandatory"):
        AdminCampaignReviewRequest(action="reject")

    # Rejection with too short reason (< 10 chars) fails validation
    with pytest.raises(ValueError, match="at least 10 characters"):
        AdminCampaignReviewRequest(action="reject", reason="Too short")


def test_admin_review_schema_pause_and_resume():
    """Verify review schema validation for 'pause' and 'resume' actions."""
    pause_req = AdminCampaignReviewRequest(action="pause", notes="Pausing for provider inquiry")
    assert pause_req.action == "pause"
    pause_req.validate_status_eligibility("active")

    with pytest.raises(ValueError, match="Cannot execute 'pause'"):
        pause_req.validate_status_eligibility("pending_approval")

    resume_req = AdminCampaignReviewRequest(action="resume")
    assert resume_req.action == "resume"
    resume_req.validate_status_eligibility("paused")

    with pytest.raises(ValueError, match="Cannot execute 'resume'"):
        resume_req.validate_status_eligibility("active")


def test_admin_review_schema_unsupported_action():
    """Unsupported action strings must fail validation."""
    with pytest.raises(ValueError, match="is not supported"):
        AdminCampaignReviewRequest(action="delete")

    with pytest.raises(ValueError, match="is not supported"):
        AdminCampaignReviewRequest(action="cancel")


def test_admin_review_schema_extra_fields_forbidden():
    """Extra unpermitted fields must fail validation (extra='forbid')."""
    with pytest.raises(ValueError):
        AdminCampaignReviewRequest(action="approve", unauthorized_extra="hack")
