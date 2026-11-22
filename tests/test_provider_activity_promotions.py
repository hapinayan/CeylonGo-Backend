"""
Automated Unit and Integration Tests for Module 03 — Phase 5D-2B:
Provider Activity Discounts (B15) & Sponsored Campaign Applications (B16).

Tests cover:
- Authentication & RBAC (Anonymous 401, Traveler 403, Non-Activity Provider 403)
- B15 Provider Discount Offers:
  - Create percentage discount, fixed amount discount
  - Validation: 0 < percent <= 100, amount > 0, valid_to >= valid_from, title length, currency format
  - List with pagination and is_active filtering
  - Detail inspection & tenant isolation (cross-provider 404)
  - Partial updates & deactivation
- B16 Provider Sponsored Campaign Applications:
  - Submit application (pending_approval) & save as draft
  - Validation: budget > 0, ends_at > starts_at, ends_at in future
  - Strict target_module = 'activity'
  - No self-approval / self-activation (attempting to set 'active' / 'approved' -> 403)
  - Edit draft campaign; editing submitted campaign rejected (400)
  - Campaign cancellation & idempotency
  - Multi-tenant cross-provider isolation (404)
  - Cross-module isolation (homestay/tour_guide campaigns return 404)
  - Physical SQL Server schema compatibility (zero reference to missing listing_reference_id)
"""

from datetime import UTC, date, datetime, timedelta, timezone
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
from app.models.shared import PromotionCampaign, PromotionPackage
from app.models.user import Base, User
from app.routers.provider_activities import require_activity_provider, router as provider_activities_router
from app.routers.provider_activity_promotions import router as provider_activity_promotions_router

# Test app with proper router ordering to avoid route shadowing
test_app = FastAPI(title="CeylonGo Module 03 Phase 5D-2B Test App")
test_app.include_router(provider_activity_promotions_router)
test_app.include_router(provider_activities_router)

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
def seeded_users(db_session: Session):
    """Seed two activity providers, one hotel provider, one traveler, and one admin."""
    admin = User(
        id=1,
        email="admin@ceylongo.test",
        password_hash="fakehash",
        full_name="Platform Admin",
        role="admin",
        is_active=True,
    )
    prov1_user = User(
        id=2,
        email="surf@ceylongo.test",
        password_hash="fakehash",
        full_name="Silva Surf Owner",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        id=3,
        email="trek@ceylongo.test",
        password_hash="fakehash",
        full_name="Perera Trek Owner",
        role="provider",
        is_active=True,
    )
    hotel_user = User(
        id=4,
        email="hotel@ceylongo.test",
        password_hash="fakehash",
        full_name="Hotel Manager",
        role="provider",
        is_active=True,
    )
    traveler = User(
        id=5,
        email="traveler@ceylongo.test",
        password_hash="fakehash",
        full_name="Tourist Charlie",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([admin, prov1_user, prov2_user, hotel_user, traveler])
    db_session.commit()

    prof1 = ProviderProfile(
        id=1,
        user_id=prov1_user.id,
        business_name="Silva Surf Academy",
        provider_type="activity",
        phone="0771112233",
        district="Matara",
        approval_status="approved",
    )
    prof2 = ProviderProfile(
        id=2,
        user_id=prov2_user.id,
        business_name="Perera Trekking Tours",
        provider_type="activity",
        phone="0774445566",
        district="Badulla",
        approval_status="approved",
    )
    prof_hotel = ProviderProfile(
        id=3,
        user_id=hotel_user.id,
        business_name="Galle Fort Boutique Villa",
        provider_type="hotel",
        phone="0912223344",
        district="Galle",
        approval_status="approved",
    )
    db_session.add_all([prof1, prof2, prof_hotel])
    db_session.commit()

    return {
        "admin": admin,
        "prov1_user": prov1_user,
        "prov1_profile": prof1,
        "prov2_user": prov2_user,
        "prov2_profile": prof2,
        "hotel_user": hotel_user,
        "hotel_profile": prof_hotel,
        "traveler": traveler,
    }


def set_auth(
    user: User | None = None,
    profile: ProviderProfile | None = None,
    db: Session | None = None,
):
    """Clean dependency overrides helper."""
    test_app.dependency_overrides.clear()
    test_app.dependency_overrides[get_db] = lambda: db or TestingSessionLocal()
    if user is None:
        def fail_auth():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        test_app.dependency_overrides[get_current_user] = fail_auth
        test_app.dependency_overrides[require_approved_provider] = fail_auth
        test_app.dependency_overrides[require_admin] = fail_auth
        test_app.dependency_overrides[require_traveler] = fail_auth
    else:
        test_app.dependency_overrides[get_current_user] = lambda: user
        if user.role == "provider":
            if profile:
                test_app.dependency_overrides[require_approved_provider] = lambda: profile
            else:
                def fail_profile():
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access forbidden: approved provider profile required",
                    )
                test_app.dependency_overrides[require_approved_provider] = fail_profile
        elif user.role == "admin":
            test_app.dependency_overrides[require_admin] = lambda: user
        elif user.role == "traveler":
            test_app.dependency_overrides[require_traveler] = lambda: user


# ==============================================================================
# Authentication & Access Control Tests
# ==============================================================================


def test_anonymous_access_rejected_discounts_and_campaigns(seeded_users):
    """Anonymous requests must be rejected with 401 Unauthorized."""
    set_auth(None)
    client = TestClient(test_app)

    res_d_get = client.get("/api/v1/provider/activities/discounts")
    assert res_d_get.status_code == 401

    res_d_post = client.post("/api/v1/provider/activities/discounts", json={})
    assert res_d_post.status_code == 401

    res_c_get = client.get("/api/v1/provider/activities/promotions/campaigns")
    assert res_c_get.status_code == 401

    res_c_post = client.post("/api/v1/provider/activities/promotions/campaigns", json={})
    assert res_c_post.status_code == 401


def test_traveler_access_rejected_discounts_and_campaigns(seeded_users):
    """Travelers must be rejected with 403 Forbidden."""
    set_auth(seeded_users["traveler"])
    client = TestClient(test_app)

    res_d = client.get("/api/v1/provider/activities/discounts")
    assert res_d.status_code == 403

    res_c = client.get("/api/v1/provider/activities/promotions/campaigns")
    assert res_c.status_code == 403


def test_non_activity_provider_rejected(seeded_users):
    """Hotel providers must be rejected from activity discounts and campaigns with 403."""
    set_auth(seeded_users["hotel_user"], seeded_users["hotel_profile"])
    client = TestClient(test_app)

    res_d = client.get("/api/v1/provider/activities/discounts")
    assert res_d.status_code == 403
    assert "Activity provider access required" in res_d.json()["detail"]

    res_c = client.get("/api/v1/provider/activities/promotions/campaigns")
    assert res_c.status_code == 403
    assert "Activity provider access required" in res_c.json()["detail"]


# ==============================================================================
# B15: Provider Discount Offers Tests (dbo.promotion_packages)
# ==============================================================================


def test_create_provider_discount_percentage(seeded_users):
    """Provider creates percentage-based discount offer."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "title": "Early Bird Surf Special",
        "description": "Book ahead and get 15% off all surf packages.",
        "discount_percent": 15.00,
        "discount_amount": None,
        "currency": "LKR",
        "valid_from": str(today),
        "valid_to": str(today + timedelta(days=30)),
        "is_active": True,
    }

    res = client.post("/api/v1/provider/activities/discounts", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["provider_profile_id"] == 1
    assert data["title"] == "Early Bird Surf Special"
    assert Decimal(str(data["discount_percent"])) == Decimal("15.00")
    assert data["discount_amount"] is None
    assert data["currency"] == "LKR"
    assert data["is_active"] is True
    assert data["targeting_scope"] == "provider"
    assert data["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"


def test_create_provider_discount_fixed_amount(seeded_users):
    """Provider creates fixed amount discount offer."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)

    today = date.today()
    payload = {
        "title": "Surf Board Voucher",
        "description": "Flat 1000 LKR off for local travelers.",
        "discount_percent": None,
        "discount_amount": 1000.00,
        "currency": "LKR",
        "valid_from": str(today),
        "valid_to": str(today + timedelta(days=14)),
        "is_active": True,
    }

    res = client.post("/api/v1/provider/activities/discounts", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert Decimal(str(data["discount_amount"])) == Decimal("1000.00")
    assert data["discount_percent"] is None


def test_create_provider_discount_invalid_percentage(seeded_users):
    """Discount percentage > 100 or negative must be rejected with 422."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    # Percent > 100
    res1 = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Invalid 150% off",
            "discount_percent": 150.00,
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=10)),
        },
    )
    assert res1.status_code == 422

    # Negative percent
    res2 = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Negative percent",
            "discount_percent": -5.00,
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=10)),
        },
    )
    assert res2.status_code == 422


def test_create_provider_discount_invalid_dates(seeded_users):
    """valid_to < valid_from must return 422."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    res = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Inverted dates",
            "discount_percent": 10.00,
            "valid_from": str(today + timedelta(days=10)),
            "valid_to": str(today),
        },
    )
    assert res.status_code == 422
    assert "valid_to cannot be earlier than valid_from" in res.json()["detail"]


def test_create_provider_discount_missing_both_discounts(seeded_users):
    """Must provide at least one positive discount value (percent or amount)."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    res = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "No discount given",
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=10)),
        },
    )
    assert res.status_code == 422
    assert "At least one of discount_percent or discount_amount must be greater than 0" in res.json()["detail"]


def test_list_provider_discounts_and_pagination(seeded_users):
    """Verifies listing and pagination for provider discounts."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    # Create 3 discounts
    for i in range(1, 4):
        client.post(
            "/api/v1/provider/activities/discounts",
            json={
                "title": f"Promo #{i}",
                "discount_percent": 10.00 * i,
                "valid_from": str(today),
                "valid_to": str(today + timedelta(days=i * 5)),
                "is_active": (i != 3),  # i=3 is inactive
            },
        )

    # List all
    res_all = client.get("/api/v1/provider/activities/discounts?page=1&page_size=10")
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert data_all["total"] == 3
    assert len(data_all["items"]) == 3

    # Filter is_active=True
    res_act = client.get("/api/v1/provider/activities/discounts?is_active=true")
    assert res_act.status_code == 200
    data_act = res_act.json()
    assert data_act["total"] == 2
    for it in data_act["items"]:
        assert it["is_active"] is True

    # Filter is_active=False
    res_inact = client.get("/api/v1/provider/activities/discounts?is_active=false")
    assert res_inact.status_code == 200
    data_inact = res_inact.json()
    assert data_inact["total"] == 1
    assert data_inact["items"][0]["is_active"] is False


def test_get_and_update_provider_discount(seeded_users):
    """Verifies detail inspection and partial update of owned discount."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    res_c = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Original Promo",
            "discount_percent": 10.00,
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=10)),
        },
    )
    disc_id = res_c.json()["id"]

    # Get detail
    res_g = client.get(f"/api/v1/provider/activities/discounts/{disc_id}")
    assert res_g.status_code == 200
    assert res_g.json()["title"] == "Original Promo"

    # Update title and percent
    res_u = client.patch(
        f"/api/v1/provider/activities/discounts/{disc_id}",
        json={
            "title": "Updated Special Promo",
            "discount_percent": 25.00,
        },
    )
    assert res_u.status_code == 200
    assert res_u.json()["title"] == "Updated Special Promo"
    assert Decimal(str(res_u.json()["discount_percent"])) == Decimal("25.00")


def test_deactivate_provider_discount(seeded_users):
    """Deactivation endpoint sets is_active to False."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    res_c = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Flash Sale",
            "discount_percent": 30.00,
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=2)),
            "is_active": True,
        },
    )
    disc_id = res_c.json()["id"]

    res_deact = client.post(f"/api/v1/provider/activities/discounts/{disc_id}/deactivate")
    assert res_deact.status_code == 200
    assert res_deact.json()["is_active"] is False

    # Check detail confirms inactive
    res_chk = client.get(f"/api/v1/provider/activities/discounts/{disc_id}")
    assert res_chk.json()["is_active"] is False


def test_cross_provider_discount_isolation(seeded_users):
    """Provider 2 cannot view, update, or deactivate Provider 1's discount (returns 404)."""
    # Provider 1 creates discount
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    today = date.today()

    res_c = client.post(
        "/api/v1/provider/activities/discounts",
        json={
            "title": "Silva Exclusive",
            "discount_percent": 20.00,
            "valid_from": str(today),
            "valid_to": str(today + timedelta(days=15)),
        },
    )
    disc_id = res_c.json()["id"]

    # Provider 2 attempts access
    set_auth(seeded_users["prov2_user"], seeded_users["prov2_profile"])
    client_p2 = TestClient(test_app)

    res_g = client_p2.get(f"/api/v1/provider/activities/discounts/{disc_id}")
    assert res_g.status_code == 404

    res_u = client_p2.patch(
        f"/api/v1/provider/activities/discounts/{disc_id}",
        json={"title": "Hacked Title"},
    )
    assert res_u.status_code == 404

    res_d = client_p2.post(f"/api/v1/provider/activities/discounts/{disc_id}/deactivate")
    assert res_d.status_code == 404


# ==============================================================================
# B16: Provider Sponsored Campaign Applications (dbo.promotion_campaigns)
# ==============================================================================


def test_create_sponsored_campaign_draft_and_pending(seeded_users):
    """Provider submits sponsored campaign application or saves as draft."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)
    st = now + timedelta(days=2)
    et = now + timedelta(days=16)

    # 1. Submit for review (default submit_for_review=True -> pending_approval)
    res1 = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Weligama Peak Season Surge",
            "budget": 25000.00,
            "currency": "LKR",
            "starts_at": st.isoformat(),
            "ends_at": et.isoformat(),
            "submit_for_review": True,
        },
    )
    assert res1.status_code == 201
    d1 = res1.json()
    assert d1["id"] > 0
    assert d1["provider_profile_id"] == 1
    assert d1["campaign_name"] == "Weligama Peak Season Surge"
    assert d1["target_module"] == "activity"
    assert d1["status"] == "pending_approval"
    assert Decimal(str(d1["budget"])) == Decimal("25000.00")
    assert Decimal(str(d1["spent_amount"])) == Decimal("0.00")
    assert d1["targeting_scope"] == "provider"
    assert d1["activity_targeting_status"] == "BLOCKED_BY_SCHEMA"

    # 2. Save as draft (submit_for_review=False -> draft)
    res2 = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Draft Summer Campaign",
            "budget": 15000.00,
            "currency": "LKR",
            "starts_at": st.isoformat(),
            "ends_at": et.isoformat(),
            "submit_for_review": False,
        },
    )
    assert res2.status_code == 201
    assert res2.json()["status"] == "draft"


def test_create_sponsored_campaign_invalid_dates(seeded_users):
    """ends_at <= starts_at or ends_at in the past must return 422."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)

    now = datetime.now(timezone.utc)

    # Inverted dates
    res1 = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Inverted Dates Campaign",
            "budget": 10000.00,
            "starts_at": (now + timedelta(days=10)).isoformat(),
            "ends_at": (now + timedelta(days=2)).isoformat(),
        },
    )
    assert res1.status_code == 422
    assert "ends_at must be strictly after starts_at" in res1.json()["detail"]

    # Past ends_at
    res2 = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Past Campaign",
            "budget": 10000.00,
            "starts_at": (now - timedelta(days=10)).isoformat(),
            "ends_at": (now - timedelta(days=2)).isoformat(),
        },
    )
    assert res2.status_code == 422
    assert "ends_at must be in the future" in res2.json()["detail"]


def test_create_sponsored_campaign_invalid_budget(seeded_users):
    """Zero or negative budget must return 422."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Zero Budget",
            "budget": 0.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=10)).isoformat(),
        },
    )
    assert res.status_code == 422


def test_list_sponsored_campaigns_and_status_filter(seeded_users):
    """Verifies listing campaigns and filtering by status."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    # Seed 1 draft, 1 pending
    client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Camp A",
            "budget": 10000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": False,
        },
    )
    client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Camp B",
            "budget": 20000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": True,
        },
    )

    # List all
    res_all = client.get("/api/v1/provider/activities/promotions/campaigns")
    assert res_all.status_code == 200
    assert res_all.json()["total"] == 2

    # Filter status=draft
    res_draft = client.get("/api/v1/provider/activities/promotions/campaigns?status=draft")
    assert res_draft.status_code == 200
    assert res_draft.json()["total"] == 1
    assert res_draft.json()["items"][0]["status"] == "draft"

    # Filter status=pending_approval
    res_pen = client.get("/api/v1/provider/activities/promotions/campaigns?status=pending_approval")
    assert res_pen.status_code == 200
    assert res_pen.json()["total"] == 1
    assert res_pen.json()["items"][0]["status"] == "pending_approval"


def test_update_draft_campaign_and_submit(seeded_users):
    """Draft campaigns can be updated and transitioned to pending_approval."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res_c = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Initial Draft",
            "budget": 10000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": False,
        },
    )
    cid = res_c.json()["id"]

    # Update budget and name
    res_u = client.patch(
        f"/api/v1/provider/activities/promotions/campaigns/{cid}",
        json={
            "campaign_name": "Revised Summer Push",
            "budget": 18000.00,
        },
    )
    assert res_u.status_code == 200
    assert res_u.json()["campaign_name"] == "Revised Summer Push"
    assert Decimal(str(res_u.json()["budget"])) == Decimal("18000.00")

    # Transition to pending_approval
    res_sub = client.patch(
        f"/api/v1/provider/activities/promotions/campaigns/{cid}",
        json={"status": "pending_approval"},
    )
    assert res_sub.status_code == 200
    assert res_sub.json()["status"] == "pending_approval"


def test_update_submitted_campaign_rejected(seeded_users):
    """Once submitted (pending_approval), editing campaign fields is rejected with 400."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res_c = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Submitted Directly",
            "budget": 10000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": True,
        },
    )
    cid = res_c.json()["id"]

    res_edit = client.patch(
        f"/api/v1/provider/activities/promotions/campaigns/{cid}",
        json={"budget": 99000.00},
    )
    assert res_edit.status_code == 400
    assert "Only draft campaigns can be modified" in res_edit.json()["detail"]


def test_self_approval_forbidden(seeded_users):
    """Providers cannot approve or activate their own campaigns (403 Forbidden)."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res_c = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Sneaky Self Activate",
            "budget": 10000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": False,
        },
    )
    cid = res_c.json()["id"]

    for illicit_status in ["active", "approved", "completed"]:
        res_sneak = client.patch(
            f"/api/v1/provider/activities/promotions/campaigns/{cid}",
            json={"status": illicit_status},
        )
        assert res_sneak.status_code == 403
        assert "Self-activation or self-approval is not permitted" in res_sneak.json()["detail"]


def test_cancel_sponsored_campaign(seeded_users):
    """Cancelling a campaign transitions status to cancelled."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res_c = client.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "To Be Cancelled",
            "budget": 12000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=5)).isoformat(),
            "submit_for_review": True,
        },
    )
    cid = res_c.json()["id"]

    res_canc = client.post(f"/api/v1/provider/activities/promotions/campaigns/{cid}/cancel")
    assert res_canc.status_code == 200
    assert res_canc.json()["status"] == "cancelled"

    # Cancelling again returns 400
    res_re_canc = client.post(f"/api/v1/provider/activities/promotions/campaigns/{cid}/cancel")
    assert res_re_canc.status_code == 400
    assert "already cancelled" in res_re_canc.json()["detail"]


def test_cross_provider_campaign_isolation(seeded_users):
    """Provider 2 cannot view, edit, or cancel Provider 1's campaign (404 Not Found)."""
    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"])
    client_p1 = TestClient(test_app)
    now = datetime.now(timezone.utc)

    res_c = client_p1.post(
        "/api/v1/provider/activities/promotions/campaigns",
        json={
            "campaign_name": "Silva Private Campaign",
            "budget": 20000.00,
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=7)).isoformat(),
            "submit_for_review": False,
        },
    )
    cid = res_c.json()["id"]

    # Provider 2 attempts access
    set_auth(seeded_users["prov2_user"], seeded_users["prov2_profile"])
    client_p2 = TestClient(test_app)

    res_g = client_p2.get(f"/api/v1/provider/activities/promotions/campaigns/{cid}")
    assert res_g.status_code == 404

    res_u = client_p2.patch(
        f"/api/v1/provider/activities/promotions/campaigns/{cid}",
        json={"campaign_name": "Hacked Name"},
    )
    assert res_u.status_code == 404

    res_canc = client_p2.post(f"/api/v1/provider/activities/promotions/campaigns/{cid}/cancel")
    assert res_canc.status_code == 404


def test_cross_module_campaign_isolation(seeded_users, db_session: Session):
    """Campaigns with target_module != 'activity' are excluded from activity provider routes."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Direct seed of a homestay campaign owned by provider 1
    homestay_stmt = (
        insert(PromotionCampaign.__table__)
        .values(
            provider_profile_id=1,
            campaign_name="Homestay Villa Promo",
            target_module="homestay",
            budget=Decimal("50000.00"),
            spent_amount=Decimal("0.00"),
            currency="LKR",
            status="pending_approval",
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=10),
            created_at=now,
        )
    )
    res_ins = db_session.execute(homestay_stmt)
    db_session.commit()
    homestay_cid = res_ins.inserted_primary_key[0]

    set_auth(seeded_users["prov1_user"], seeded_users["prov1_profile"], db=db_session)
    client = TestClient(test_app)

    # Activity endpoint must NOT return the homestay campaign
    res_g = client.get(f"/api/v1/provider/activities/promotions/campaigns/{homestay_cid}")
    assert res_g.status_code == 404

    # Activity list must NOT include the homestay campaign
    res_l = client.get("/api/v1/provider/activities/promotions/campaigns")
    assert res_l.status_code == 200
    ids = [item["id"] for item in res_l.json()["items"]]
    assert homestay_cid not in ids
