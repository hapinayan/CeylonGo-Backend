"""
Automated tests for Module 04 P0.7 — Provider & Admin Dashboard Statistics.

Requirements:
- P01: Provider listing summary (GET /api/v1/eco-experiences/me/summary)
- A08: Admin eco-tourism overview (GET /api/v1/admin/eco-experiences/overview)

Covers:
1. test_provider_empty_summary: Provider with 0 listings -> all zeros.
2. test_provider_aggregation_correctness: Diverse statuses, publication, and badge states.
3. test_cross_provider_isolation: Provider A vs Provider B strictly isolated.
4. test_provider_unauthorized_access: 401 unauthenticated, 403 traveler, 403 non-eco provider, 403 pending provider, 403 admin.
5. test_admin_empty_overview: Clean DB -> 0 total, 0 published, 0 active providers, all 6 categories present with 0, empty districts.
6. test_admin_aggregation_correctness: Platform-wide headline counts and by_status breakdown.
7. test_category_breakdown_including_zeros: All 6 official categories represented.
8. test_district_breakdown_ordering: Ordered by count DESC, tie-broken by district ASC.
9. test_admin_unauthorized_access: 401 unauthenticated, 403 traveler, 403 provider.
10. test_private_information_exclusion: Response contains only aggregate statistics, no private evidence or paths.
11. test_active_provider_filtering: Inactive users, non-eco providers, and pending approvals excluded from total_active_providers.
"""

from datetime import UTC, datetime
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.models.eco_experience import (
    VALID_ECO_CATEGORIES,
    EcoExperience,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)

# Test application isolating M04 routers
eco_p07_app = FastAPI(title="CeylonGo M04 P0.7 Dashboard Test App")
eco_p07_app.include_router(auth_router)
eco_p07_app.include_router(eco_experiences_router)
eco_p07_app.include_router(admin_eco_router)

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

    eco_p07_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p07_app) as test_client:
        yield test_client
    eco_p07_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def create_user_and_provider(
    db: Session,
    email: str,
    role: str = "provider",
    provider_type: str = "eco_tourism",
    approval_status: str = "approved",
    is_active: bool = True,
    business_name: str = "Ceylon Safaris",
) -> tuple[User, ProviderProfile | None]:
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

    profile = None
    if role == "provider":
        profile = ProviderProfile(
            user_id=user.id,
            business_name=business_name,
            provider_type=provider_type,
            approval_status=approval_status,
            phone="+94771234567",
            business_registration_number=f"REG-{user.id}",
            district="Kandy",
            created_at=datetime.now(UTC),
        )
        db.add(profile)
        db.flush()

    db.commit()
    return user, profile


def create_experience_helper(
    db: Session,
    provider_profile_id: int,
    title: str,
    eco_category: str = "Forest & Nature Trails",
    district: str = "Kandy",
    listing_status: str = "approved",
    is_published: bool = True,
    eco_badge_status: str = "none",
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider_profile_id,
        title=title,
        eco_category=eco_category,
        description="A wonderful eco experience in nature.",
        itinerary="Day 1: Hike through the rainforest trail.",
        duration_hours=Decimal("4.5"),
        district=district,
        city="Peradeniya",
        meeting_point="Trailhead Entrance",
        base_price=Decimal("45.00"),
        currency="USD",
        max_group_size=12,
        min_participants=2,
        min_age=8,
        environmental_guidelines="Strict leave-no-trace policy.",
        wildlife_safety_summary="No feeding of wildlife permitted.",
        listing_status=listing_status,
        is_published=is_published,
        eco_badge_status=eco_badge_status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


# ==============================================================================
# Provider Summary Tests (Requirement P01)
# ==============================================================================


def test_provider_empty_summary(client: TestClient, db_session: Session):
    """Provider with zero listings should receive all zeros in summary metrics."""
    user, profile = create_user_and_provider(db_session, "provider_empty@test.com")
    headers = auth_header(user)

    response = client.get("/api/v1/eco-experiences/me/summary", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 0
    assert data["draft"] == 0
    assert data["pending_approval"] == 0
    assert data["approved"] == 0
    assert data["rejected"] == 0
    assert data["deactivated"] == 0
    assert data["published"] == 0
    assert data["verified_badges"] == 0
    assert data["pending_badges"] == 0


def test_provider_aggregation_correctness(client: TestClient, db_session: Session):
    """
    Provider with multiple listings across diverse statuses and badge states
    should have all metrics calculated correctly via SQL aggregation.
    """
    user, profile = create_user_and_provider(db_session, "provider_active@test.com")
    headers = auth_header(user)

    # 1. Draft listing
    create_experience_helper(
        db_session, profile.id, "Draft 1", listing_status="draft", is_published=False
    )
    # 2. Pending approval listing
    create_experience_helper(
        db_session, profile.id, "Pending 1", listing_status="pending_approval", is_published=False, eco_badge_status="pending"
    )
    # 3. Approved & Published with Granted Badge
    create_experience_helper(
        db_session, profile.id, "Approved Published 1", listing_status="approved", is_published=True, eco_badge_status="granted"
    )
    # 4. Approved & Published without Badge
    create_experience_helper(
        db_session, profile.id, "Approved Published 2", listing_status="approved", is_published=True, eco_badge_status="none"
    )
    # 5. Approved but Unpublished (deactivated by provider)
    create_experience_helper(
        db_session, profile.id, "Approved Unpublished", listing_status="approved", is_published=False, eco_badge_status="granted"
    )
    # 6. Rejected listing
    create_experience_helper(
        db_session, profile.id, "Rejected 1", listing_status="rejected", is_published=False
    )
    # 7. Deactivated listing (admin deactivated)
    create_experience_helper(
        db_session, profile.id, "Deactivated 1", listing_status="deactivated", is_published=False
    )

    response = client.get("/api/v1/eco-experiences/me/summary", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 7
    assert data["draft"] == 1
    assert data["pending_approval"] == 1
    assert data["approved"] == 3
    assert data["rejected"] == 1
    assert data["deactivated"] == 1
    assert data["published"] == 2
    assert data["verified_badges"] == 2  # items 3 and 5
    assert data["pending_badges"] == 1   # item 2


def test_cross_provider_isolation(client: TestClient, db_session: Session):
    """Provider A and Provider B should only see counts for their own listings."""
    user_a, profile_a = create_user_and_provider(db_session, "provider_a@test.com")
    user_b, profile_b = create_user_and_provider(db_session, "provider_b@test.com")

    # Provider A has 3 listings
    create_experience_helper(db_session, profile_a.id, "A-1", listing_status="draft", is_published=False)
    create_experience_helper(db_session, profile_a.id, "A-2", listing_status="approved", is_published=True, eco_badge_status="granted")
    create_experience_helper(db_session, profile_a.id, "A-3", listing_status="approved", is_published=True)

    # Provider B has 1 listing
    create_experience_helper(db_session, profile_b.id, "B-1", listing_status="rejected", is_published=False)

    # Check Provider A
    resp_a = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(user_a))
    assert resp_a.status_code == 200
    data_a = resp_a.json()
    assert data_a["total"] == 3
    assert data_a["draft"] == 1
    assert data_a["approved"] == 2
    assert data_a["rejected"] == 0
    assert data_a["published"] == 2
    assert data_a["verified_badges"] == 1

    # Check Provider B
    resp_b = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(user_b))
    assert resp_b.status_code == 200
    data_b = resp_b.json()
    assert data_b["total"] == 1
    assert data_b["draft"] == 0
    assert data_b["approved"] == 0
    assert data_b["rejected"] == 1
    assert data_b["published"] == 0
    assert data_b["verified_badges"] == 0


def test_provider_unauthorized_access(client: TestClient, db_session: Session):
    """Test that unauthorized, unauthenticated, non-eco or pending providers receive 401 or 403."""
    # 1. Unauthenticated
    resp_unauth = client.get("/api/v1/eco-experiences/me/summary")
    assert resp_unauth.status_code == 401

    # 2. Traveler
    traveler, _ = create_user_and_provider(db_session, "traveler_p7@test.com", role="traveler")
    resp_traveler = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(traveler))
    assert resp_traveler.status_code == 403

    # 3. Non-eco provider (homestay provider)
    homestay_provider, _ = create_user_and_provider(
        db_session, "homestay_p7@test.com", role="provider", provider_type="homestay"
    )
    resp_homestay = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(homestay_provider))
    assert resp_homestay.status_code == 403

    # 4. Pending eco provider
    pending_provider, _ = create_user_and_provider(
        db_session, "pending_p7@test.com", role="provider", provider_type="eco_tourism", approval_status="pending"
    )
    resp_pending = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(pending_provider))
    assert resp_pending.status_code == 403

    # 5. Admin user (summary is strictly for providers)
    admin_user, _ = create_user_and_provider(db_session, "admin_not_provider@test.com", role="admin")
    resp_admin = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(admin_user))
    assert resp_admin.status_code == 403


# ==============================================================================
# Admin Overview Tests (Requirement A08)
# ==============================================================================


def test_admin_empty_overview(client: TestClient, db_session: Session):
    """In an empty database, admin overview should return zeros, all 6 categories with 0, and empty districts."""
    admin_user, _ = create_user_and_provider(db_session, "admin_overview_empty@test.com", role="admin")
    headers = auth_header(admin_user)

    response = client.get("/api/v1/admin/eco-experiences/overview", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert data["total_listings"] == 0
    assert data["published_listings"] == 0
    assert data["verified_badges"] == 0
    assert data["pending_badges"] == 0
    assert data["total_active_providers"] == 0

    assert data["by_status"]["draft"] == 0
    assert data["by_status"]["pending_approval"] == 0
    assert data["by_status"]["approved"] == 0
    assert data["by_status"]["rejected"] == 0
    assert data["by_status"]["deactivated"] == 0

    assert len(data["by_category"]) == 6
    category_names = [item["category"] for item in data["by_category"]]
    for valid_cat in VALID_ECO_CATEGORIES:
        assert valid_cat in category_names
    for item in data["by_category"]:
        assert item["count"] == 0

    assert data["by_district"] == []


def test_admin_aggregation_correctness(client: TestClient, db_session: Session):
    """Platform-wide aggregation across multiple providers and diverse listing states."""
    admin_user, _ = create_user_and_provider(db_session, "admin_p7_agg@test.com", role="admin")
    _, profile_1 = create_user_and_provider(db_session, "p1_agg@test.com", business_name="Provider One")
    _, profile_2 = create_user_and_provider(db_session, "p2_agg@test.com", business_name="Provider Two")

    # Provider 1 listings
    create_experience_helper(
        db_session, profile_1.id, "P1 Exp 1", eco_category="Forest & Nature Trails", district="Kandy",
        listing_status="approved", is_published=True, eco_badge_status="granted"
    )
    create_experience_helper(
        db_session, profile_1.id, "P1 Exp 2", eco_category="Wildlife & Birdwatching", district="Matale",
        listing_status="pending_approval", is_published=False, eco_badge_status="pending"
    )
    create_experience_helper(
        db_session, profile_1.id, "P1 Exp 3", eco_category="Conservation Activities", district="Kandy",
        listing_status="draft", is_published=False, eco_badge_status="none"
    )

    # Provider 2 listings
    create_experience_helper(
        db_session, profile_2.id, "P2 Exp 1", eco_category="Forest & Nature Trails", district="Kandy",
        listing_status="approved", is_published=True, eco_badge_status="none"
    )
    create_experience_helper(
        db_session, profile_2.id, "P2 Exp 2", eco_category="Eco-Friendly Stays", district="Galle",
        listing_status="rejected", is_published=False, eco_badge_status="none"
    )
    create_experience_helper(
        db_session, profile_2.id, "P2 Exp 3", eco_category="Community Eco-Tourism", district="Galle",
        listing_status="deactivated", is_published=False, eco_badge_status="none"
    )

    response = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    assert data["total_listings"] == 6
    assert data["published_listings"] == 2
    assert data["verified_badges"] == 1
    assert data["pending_badges"] == 1
    assert data["total_active_providers"] == 2

    assert data["by_status"]["draft"] == 1
    assert data["by_status"]["pending_approval"] == 1
    assert data["by_status"]["approved"] == 2
    assert data["by_status"]["rejected"] == 1
    assert data["by_status"]["deactivated"] == 1


def test_category_breakdown_including_zeros(client: TestClient, db_session: Session):
    """Admin overview must always return all 6 official categories, including zero-count ones."""
    admin_user, _ = create_user_and_provider(db_session, "admin_cats@test.com", role="admin")
    _, profile = create_user_and_provider(db_session, "provider_cats@test.com")

    # Add 2 listings in Forest & Nature Trails, 1 in Wildlife & Birdwatching
    create_experience_helper(db_session, profile.id, "Trail 1", eco_category="Forest & Nature Trails")
    create_experience_helper(db_session, profile.id, "Trail 2", eco_category="Forest & Nature Trails")
    create_experience_helper(db_session, profile.id, "Bird 1", eco_category="Wildlife & Birdwatching")

    response = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    cat_map = {item["category"]: item["count"] for item in data["by_category"]}
    assert len(data["by_category"]) == 6
    assert cat_map["Forest & Nature Trails"] == 2
    assert cat_map["Wildlife & Birdwatching"] == 1
    assert cat_map["Sustainable Village Experiences"] == 0
    assert cat_map["Conservation Activities"] == 0
    assert cat_map["Eco-Friendly Stays"] == 0
    assert cat_map["Community Eco-Tourism"] == 0


def test_district_breakdown_ordering(client: TestClient, db_session: Session):
    """
    District breakdown must be ordered descending by count, with ascending alphabetical
    tie-breaking when counts are equal.
    """
    admin_user, _ = create_user_and_provider(db_session, "admin_dist@test.com", role="admin")
    _, profile = create_user_and_provider(db_session, "provider_dist@test.com")

    # 3 in Kandy
    create_experience_helper(db_session, profile.id, "Kandy 1", district="Kandy")
    create_experience_helper(db_session, profile.id, "Kandy 2", district="Kandy")
    create_experience_helper(db_session, profile.id, "Kandy 3", district="Kandy")

    # 2 in Colombo
    create_experience_helper(db_session, profile.id, "Colombo 1", district="Colombo")
    create_experience_helper(db_session, profile.id, "Colombo 2", district="Colombo")

    # 2 in Galle (Tie with Colombo: count=2; Colombo < Galle alphabetically)
    create_experience_helper(db_session, profile.id, "Galle 1", district="Galle")
    create_experience_helper(db_session, profile.id, "Galle 2", district="Galle")

    # 1 in Badulla
    create_experience_helper(db_session, profile.id, "Badulla 1", district="Badulla")

    # 1 in Anuradhapura (Tie with Badulla: count=1; Anuradhapura < Badulla alphabetically)
    create_experience_helper(db_session, profile.id, "Anuradhapura 1", district="Anuradhapura")

    response = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    districts = data["by_district"]
    assert len(districts) == 5

    # Order check:
    # 1. Kandy: 3
    # 2. Colombo: 2
    # 3. Galle: 2
    # 4. Anuradhapura: 1
    # 5. Badulla: 1
    assert districts[0] == {"district": "Kandy", "count": 3}
    assert districts[1] == {"district": "Colombo", "count": 2}
    assert districts[2] == {"district": "Galle", "count": 2}
    assert districts[3] == {"district": "Anuradhapura", "count": 1}
    assert districts[4] == {"district": "Badulla", "count": 1}


def test_admin_unauthorized_access(client: TestClient, db_session: Session):
    """Admin overview endpoint must reject unauthenticated, traveler, and provider users."""
    # 1. Unauthenticated
    resp_unauth = client.get("/api/v1/admin/eco-experiences/overview")
    assert resp_unauth.status_code == 401

    # 2. Traveler
    traveler, _ = create_user_and_provider(db_session, "traveler_admin_test@test.com", role="traveler")
    resp_traveler = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(traveler))
    assert resp_traveler.status_code == 403

    # 3. Provider
    provider, _ = create_user_and_provider(db_session, "provider_admin_test@test.com", role="provider")
    resp_provider = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(provider))
    assert resp_provider.status_code == 403


def test_private_information_exclusion(client: TestClient, db_session: Session):
    """
    Ensure dashboard endpoints return only numeric aggregates and public breakdowns,
    never leaking internal file paths, private evidence documents, or sensitive IDs.
    """
    user, profile = create_user_and_provider(db_session, "privacy_provider@test.com")
    admin_user, _ = create_user_and_provider(db_session, "privacy_admin@test.com", role="admin")

    create_experience_helper(
        db_session, profile.id, "Secret Trail", listing_status="approved", is_published=True, eco_badge_status="granted"
    )

    # Provider summary response keys check
    resp_p = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(user))
    data_p = resp_p.json()
    allowed_p_keys = {
        "total", "draft", "pending_approval", "approved", "rejected",
        "deactivated", "published", "verified_badges", "pending_badges"
    }
    assert set(data_p.keys()) == allowed_p_keys
    for k, v in data_p.items():
        assert isinstance(v, int)

    # Admin overview response keys check
    resp_a = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    data_a = resp_a.json()
    allowed_a_keys = {
        "total_listings", "published_listings", "verified_badges",
        "pending_badges", "total_active_providers", "by_status",
        "by_category", "by_district"
    }
    assert set(data_a.keys()) == allowed_a_keys


def test_active_provider_filtering(client: TestClient, db_session: Session):
    """
    Verifies that total_active_providers only counts:
    - provider_type == 'eco_tourism'
    - approval_status == 'approved'
    - user.is_active == True
    """
    admin_user, _ = create_user_and_provider(db_session, "admin_filter_test@test.com", role="admin")

    # 1. Approved active eco provider -> Should be counted (+1)
    create_user_and_provider(
        db_session, "eco_approved_active@test.com", role="provider",
        provider_type="eco_tourism", approval_status="approved", is_active=True
    )

    # 2. Approved inactive eco provider -> Should NOT be counted
    create_user_and_provider(
        db_session, "eco_approved_inactive@test.com", role="provider",
        provider_type="eco_tourism", approval_status="approved", is_active=False
    )

    # 3. Pending active eco provider -> Should NOT be counted
    create_user_and_provider(
        db_session, "eco_pending_active@test.com", role="provider",
        provider_type="eco_tourism", approval_status="pending", is_active=True
    )

    # 4. Rejected active eco provider -> Should NOT be counted
    create_user_and_provider(
        db_session, "eco_rejected_active@test.com", role="provider",
        provider_type="eco_tourism", approval_status="rejected", is_active=True
    )

    # 5. Approved active homestay provider -> Should NOT be counted
    create_user_and_provider(
        db_session, "homestay_approved_active@test.com", role="provider",
        provider_type="homestay", approval_status="approved", is_active=True
    )

    # 6. Approved active artisan provider -> Should NOT be counted
    create_user_and_provider(
        db_session, "artisan_approved_active@test.com", role="provider",
        provider_type="artisan", approval_status="approved", is_active=True
    )

    response = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    assert response.status_code == 200
    data = response.json()

    assert data["total_active_providers"] == 1
