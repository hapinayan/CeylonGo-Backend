"""
Automated tests for Module 04 P1.1D — Sponsored Discovery & Max-2 Ranking.

Requirements:
- T17: Sponsored Eco Listings
- P16: Provider Sponsored Promotions
- A07: Admin Promotion Management

Covers the 19 required P1.1D scenarios:
1. test_unpaid_approved_campaign_is_not_sponsored: Approved campaign with unpaid status is not marked sponsored.
2. test_pending_campaign_is_not_sponsored: Pending approval campaign is not marked sponsored.
3. test_rejected_or_cancelled_campaign_is_not_sponsored: Rejected and cancelled campaigns are not sponsored.
4. test_expired_campaign_is_not_sponsored: Campaign with ends_at <= now is not sponsored.
5. test_active_paid_campaign_inside_window_is_sponsored: Campaign active + paid + now in window is sponsored.
6. test_future_campaign_is_not_sponsored: Campaign with starts_at > now is not sponsored.
7. test_unpublished_experience_excluded_from_discovery: Unpublished listing with active campaign is excluded.
8. test_unapproved_experience_excluded_from_discovery: Unapproved listing with active campaign is excluded.
9. test_multiple_campaigns_do_not_duplicate_experience: Overlapping active campaigns do not duplicate listing.
10. test_max_2_sponsored_items_per_page: Maximum 2 sponsored items interleaved at top of each page.
11. test_page_size_1_interleaving: When page_size=1, exactly 1 item returned with correct sponsored flag.
12. test_page_size_2_interleaving: When page_size=2, up to 2 sponsored items returned per page.
13. test_stable_multi_page_traversal_no_duplicates: No listing repeated across consecutive or later pages.
14. test_no_skipped_organic_listings: All organic listings appear across paginated traversal.
15. test_filters_apply_to_both_sponsored_and_organic: Filters (category, district, price, duration, badge, search) filter both pools.
16. test_total_count_equals_unique_matching_experiences: Total count equals unique matching items.
17. test_sponsored_and_eco_badge_independence: is_sponsored and has_verified_eco_badge are orthogonal (all 4 permutations).
18. test_private_payment_metadata_never_leaked: No payment, billing, or review notes exposed in summary or detail.
19. test_sql_server_compatible_query_generation: Query compilation compatible with SQL Server (mssql dialect).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.dialects import mssql
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience, EcoVerification
from app.models.eco_promotion import (
    EcoPromotionPackage,
    EcoSponsoredPromotion,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import router as eco_experiences_router
from app.routers.eco_promotions import eco_promotions_router

# Test application isolating M04 discovery & promotion routers
discovery_app = FastAPI(title="CeylonGo M04 P1.1D Discovery Test App")
discovery_app.include_router(auth_router)
discovery_app.include_router(eco_experiences_router)
discovery_app.include_router(eco_promotions_router)

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

    discovery_app.dependency_overrides[get_db] = override_get_db
    with TestClient(discovery_app) as test_client:
        yield test_client
    discovery_app.dependency_overrides.clear()


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
    business_name: str = "Ceylon Eco Tours",
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
    title: str = "Knuckles Forest Trek",
    category: str = "Forest & Nature Trails",
    district: str = "Kandy",
    city: str = "Matale",
    price: Decimal = Decimal("5000.00"),
    duration_hours: Decimal = Decimal("4.00"),
    listing_status: str = "approved",
    is_published: bool = True,
    has_verified_eco_badge: bool = False,
    admin_user: User | None = None,
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
        eco_badge_status="granted" if has_verified_eco_badge else "none",
    )
    db.add(exp)
    db.flush()
    if has_verified_eco_badge:
        ver = EcoVerification(
            eco_experience_id=exp.id,
            submitted_by_provider_id=provider.id,
            reviewed_by_admin_id=admin_user.id if admin_user else provider.user_id,
            decision="granted",
            reason="Verified eco credentials",
            created_at=datetime.now(UTC),
        )
        db.add(ver)
        db.flush()
    return exp


def create_package(
    db: Session,
    name: str = "Standard Spotlight",
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


def create_synthetic_campaign(
    db: Session,
    provider: ProviderProfile,
    experience: EcoExperience,
    package: EcoPromotionPackage,
    campaign_status: str = "active",
    payment_status: str = "paid",
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    payment_reference: str | None = "SYNTH-REF-999",
    review_reason: str | None = "Editorial review note",
) -> EcoSponsoredPromotion:
    now = datetime.now(UTC)
    if campaign_status == "active" and starts_at is None:
        starts_at = now - timedelta(days=1)
    if campaign_status == "active" and ends_at is None:
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
        payment_reference=payment_reference,
        review_reason=review_reason,
    )
    db.add(campaign)
    db.flush()
    return campaign


# ==============================================================================
# P1.1D Test Scenarios
# ==============================================================================


def test_unpaid_approved_campaign_is_not_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 1: Approved campaign with unpaid payment_status is not sponsored."""
    user = create_user(db_session, "prov1@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Unpaid Campaign Trek")
    pkg = create_package(db_session)
    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="approved",
        payment_status="unpaid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == exp.id
    assert data["items"][0]["is_sponsored"] is False

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_sponsored"] is False


def test_pending_campaign_is_not_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 2: Campaign in pending_approval status is not sponsored."""
    user = create_user(db_session, "prov2@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Pending Promo Trek")
    pkg = create_package(db_session)
    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="pending_approval",
        payment_status="paid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"][0]["is_sponsored"] is False

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_sponsored"] is False


def test_rejected_or_cancelled_campaign_is_not_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 3: Rejected and cancelled campaigns are not sponsored."""
    user = create_user(db_session, "prov3@ceylongo.com")
    prov = create_provider(db_session, user)
    exp1 = create_experience(db_session, prov, title="Rejected Promo Trek")
    exp2 = create_experience(db_session, prov, title="Cancelled Promo Trek")
    pkg = create_package(db_session)

    create_synthetic_campaign(
        db_session,
        prov,
        exp1,
        pkg,
        campaign_status="rejected",
        payment_status="paid",
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp2,
        pkg,
        campaign_status="cancelled",
        payment_status="paid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    for item in data["items"]:
        assert item["is_sponsored"] is False


def test_expired_campaign_is_not_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 4: Campaign with ends_at in the past is not sponsored."""
    user = create_user(db_session, "prov4@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Expired Promo Trek")
    pkg = create_package(db_session)
    now = datetime.now(UTC)

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
        starts_at=now - timedelta(days=10),
        ends_at=now - timedelta(hours=1),
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["is_sponsored"] is False

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_sponsored"] is False


def test_active_paid_campaign_inside_window_is_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 5: Active, paid campaign inside date window is sponsored."""
    user = create_user(db_session, "prov5@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Active Sponsored Trek")
    pkg = create_package(db_session)
    now = datetime.now(UTC)

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=6),
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == exp.id
    assert data["items"][0]["is_sponsored"] is True

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_sponsored"] is True


def test_future_campaign_is_not_sponsored(
    client: TestClient, db_session: Session
):
    """Scenario 6: Campaign with starts_at in the future is not sponsored."""
    user = create_user(db_session, "prov6@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Future Promo Trek")
    pkg = create_package(db_session)
    now = datetime.now(UTC)

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
        starts_at=now + timedelta(days=2),
        ends_at=now + timedelta(days=9),
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["is_sponsored"] is False

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["is_sponsored"] is False


def test_unpublished_experience_excluded_from_discovery(
    client: TestClient, db_session: Session
):
    """Scenario 7: Unpublished listing with active campaign is excluded from discovery."""
    user = create_user(db_session, "prov7@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(
        db_session,
        prov,
        title="Unpublished Experience",
        listing_status="approved",
        is_published=False,
    )
    pkg = create_package(db_session)

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    assert len(resp.json()["items"]) == 0

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 404


def test_unapproved_experience_excluded_from_discovery(
    client: TestClient, db_session: Session
):
    """Scenario 8: Unapproved listing with active campaign is excluded from discovery."""
    user = create_user(db_session, "prov8@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(
        db_session,
        prov,
        title="Draft Experience",
        listing_status="draft",
        is_published=False,
    )
    pkg = create_package(db_session)

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 404


def test_multiple_campaigns_do_not_duplicate_experience(
    client: TestClient, db_session: Session
):
    """Scenario 9: Multiple active campaigns for one listing do not duplicate it."""
    user = create_user(db_session, "prov9@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov, title="Multi-Campaign Listing")
    pkg1 = create_package(db_session, name="Package A")
    pkg2 = create_package(db_session, name="Package B")

    # Add two active campaigns
    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg1,
        campaign_status="active",
        payment_status="paid",
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg2,
        campaign_status="active",
        payment_status="paid",
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == exp.id
    assert data["items"][0]["is_sponsored"] is True


def test_max_2_sponsored_items_per_page(
    client: TestClient, db_session: Session
):
    """Scenario 10: Maximum 2 sponsored items interleaved per page."""
    user = create_user(db_session, "prov10@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 4 sponsored listings
    spon_exps = []
    for i in range(4):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Experience {i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
        spon_exps.append(exp)

    # 6 organic listings
    org_exps = []
    for i in range(6):
        exp = create_experience(
            db_session, prov, title=f"Organic Experience {i+1}"
        )
        org_exps.append(exp)

    db_session.commit()

    # Page 1: page_size = 5
    # Should contain exactly 2 sponsored, 3 organic
    resp1 = client.get("/api/v1/eco-experiences?page=1&page_size=5")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total"] == 10
    assert len(data1["items"]) == 5
    assert data1["items"][0]["is_sponsored"] is True
    assert data1["items"][1]["is_sponsored"] is True
    assert data1["items"][2]["is_sponsored"] is False
    assert data1["items"][3]["is_sponsored"] is False
    assert data1["items"][4]["is_sponsored"] is False

    # Page 2: page_size = 5
    # Should contain remaining 2 sponsored, remaining 3 organic
    resp2 = client.get("/api/v1/eco-experiences?page=2&page_size=5")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 10
    assert len(data2["items"]) == 5
    assert data2["items"][0]["is_sponsored"] is True
    assert data2["items"][1]["is_sponsored"] is True
    assert data2["items"][2]["is_sponsored"] is False
    assert data2["items"][3]["is_sponsored"] is False
    assert data2["items"][4]["is_sponsored"] is False


def test_page_size_1_interleaving(client: TestClient, db_session: Session):
    """Scenario 11: page_size=1 strictly returns 1 item per page with correct flag."""
    user = create_user(db_session, "prov11@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 2 sponsored, 2 organic
    for i in range(2):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Size1 #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
    for i in range(2):
        create_experience(db_session, prov, title=f"Organic Size1 #{i+1}")
    db_session.commit()

    # page_size = 1 -> k = min(2, 1) = 1
    # Page 1: sponsored 1
    r1 = client.get("/api/v1/eco-experiences?page=1&page_size=1")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 4
    assert len(d1["items"]) == 1
    assert d1["items"][0]["is_sponsored"] is True

    # Page 2: sponsored 2
    r2 = client.get("/api/v1/eco-experiences?page=2&page_size=1")
    assert r2.status_code == 200
    d2 = r2.json()
    assert len(d2["items"]) == 1
    assert d2["items"][0]["is_sponsored"] is True

    # Page 3: organic 1
    r3 = client.get("/api/v1/eco-experiences?page=3&page_size=1")
    assert r3.status_code == 200
    d3 = r3.json()
    assert len(d3["items"]) == 1
    assert d3["items"][0]["is_sponsored"] is False

    # Page 4: organic 2
    r4 = client.get("/api/v1/eco-experiences?page=4&page_size=1")
    assert r4.status_code == 200
    d4 = r4.json()
    assert len(d4["items"]) == 1
    assert d4["items"][0]["is_sponsored"] is False


def test_page_size_2_interleaving(client: TestClient, db_session: Session):
    """Scenario 12: page_size=2 returns up to 2 sponsored on page 1."""
    user = create_user(db_session, "prov12@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 3 sponsored, 3 organic
    for i in range(3):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Size2 #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
    for i in range(3):
        create_experience(db_session, prov, title=f"Organic Size2 #{i+1}")
    db_session.commit()

    # Page 1: 2 sponsored, 0 organic
    r1 = client.get("/api/v1/eco-experiences?page=1&page_size=2")
    d1 = r1.json()
    assert len(d1["items"]) == 2
    assert d1["items"][0]["is_sponsored"] is True
    assert d1["items"][1]["is_sponsored"] is True

    # Page 2: 1 sponsored, 1 organic
    r2 = client.get("/api/v1/eco-experiences?page=2&page_size=2")
    d2 = r2.json()
    assert len(d2["items"]) == 2
    assert d2["items"][0]["is_sponsored"] is True
    assert d2["items"][1]["is_sponsored"] is False

    # Page 3: 0 sponsored, 2 organic
    r3 = client.get("/api/v1/eco-experiences?page=3&page_size=2")
    d3 = r3.json()
    assert len(d3["items"]) == 2
    assert d3["items"][0]["is_sponsored"] is False
    assert d3["items"][1]["is_sponsored"] is False


def test_stable_multi_page_traversal_no_duplicates(
    client: TestClient, db_session: Session
):
    """Scenario 13: Traversal across all pages yields strictly unique IDs."""
    user = create_user(db_session, "prov13@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 3 sponsored, 7 organic (10 total)
    for i in range(3):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Multi #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
    for i in range(7):
        create_experience(db_session, prov, title=f"Organic Multi #{i+1}")
    db_session.commit()

    seen_ids = []
    page = 1
    page_size = 3
    while True:
        resp = client.get(
            f"/api/v1/eco-experiences?page={page}&page_size={page_size}"
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        if not items:
            break
        for item in items:
            seen_ids.append(item["id"])
        page += 1

    assert len(seen_ids) == 10
    assert len(set(seen_ids)) == 10, "Duplicate items found across pages!"


def test_no_skipped_organic_listings(client: TestClient, db_session: Session):
    """Scenario 14: All organic listings appear without any missing items."""
    user = create_user(db_session, "prov14@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 2 sponsored, 5 organic
    for i in range(2):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Skipped #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )

    expected_organic_ids = set()
    for i in range(5):
        exp = create_experience(
            db_session, prov, title=f"Organic Skipped #{i+1}"
        )
        expected_organic_ids.add(exp.id)
    db_session.commit()

    returned_organic_ids = set()
    page = 1
    while True:
        resp = client.get(f"/api/v1/eco-experiences?page={page}&page_size=2")
        assert resp.status_code == 200
        items = resp.json()["items"]
        if not items:
            break
        for item in items:
            if not item["is_sponsored"]:
                returned_organic_ids.add(item["id"])
        page += 1

    assert returned_organic_ids == expected_organic_ids


def test_filters_apply_to_both_sponsored_and_organic(
    client: TestClient, db_session: Session
):
    """Scenario 15: Filters (category, district, price, duration, badge, search) filter both pools."""
    user = create_user(db_session, "prov15@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # Sponsored: Rainforest Trekking, Galle, price=3000, duration=4
    exp_s1 = create_experience(
        db_session,
        prov,
        title="Sinharaja Rainforest Expedition",
        category="Forest & Nature Trails",
        district="Galle",
        price=Decimal("3000.00"),
        duration_hours=Decimal("4.00"),
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp_s1,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )

    # Sponsored: Wildlife & Birdwatching, Matale, price=8000, duration=6
    exp_s2 = create_experience(
        db_session,
        prov,
        title="Knuckles Leopard Sanctuary",
        category="Wildlife & Birdwatching",
        district="Matale",
        price=Decimal("8000.00"),
        duration_hours=Decimal("6.00"),
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp_s2,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )

    # Organic: Rainforest Trekking, Galle, price=3500, duration=3
    exp_o1 = create_experience(
        db_session,
        prov,
        title="Sinharaja Canopy Walk",
        category="Forest & Nature Trails",
        district="Galle",
        price=Decimal("3500.00"),
        duration_hours=Decimal("3.00"),
    )

    # Organic: Wildlife & Birdwatching, Matale, price=9000, duration=7
    exp_o2 = create_experience(
        db_session,
        prov,
        title="Matale Wilderness Night Trail",
        category="Wildlife & Birdwatching",
        district="Matale",
        price=Decimal("9000.00"),
        duration_hours=Decimal("7.00"),
    )
    db_session.commit()

    # Filter by category
    resp_cat = client.get(
        "/api/v1/eco-experiences?category=Forest+%26+Nature+Trails"
    )
    assert resp_cat.status_code == 200
    data_cat = resp_cat.json()
    assert data_cat["total"] == 2
    assert {item["id"] for item in data_cat["items"]} == {exp_s1.id, exp_o1.id}

    # Filter by district
    resp_dist = client.get("/api/v1/eco-experiences?district=Matale")
    assert resp_dist.status_code == 200
    assert resp_dist.json()["total"] == 2
    assert {item["id"] for item in resp_dist.json()["items"]} == {exp_s2.id, exp_o2.id}

    # Filter by price range
    resp_price = client.get("/api/v1/eco-experiences?max_price=4000")
    assert resp_price.status_code == 200
    assert resp_price.json()["total"] == 2
    assert {item["id"] for item in resp_price.json()["items"]} == {exp_s1.id, exp_o1.id}

    # Filter by search term
    resp_search = client.get("/api/v1/eco-experiences?search=Sinharaja")
    assert resp_search.status_code == 200
    assert resp_search.json()["total"] == 2
    assert {item["id"] for item in resp_search.json()["items"]} == {exp_s1.id, exp_o1.id}


def test_total_count_equals_unique_matching_experiences(
    client: TestClient, db_session: Session
):
    """Scenario 16: Total count reflects total unique matching items across sponsored + organic."""
    user = create_user(db_session, "prov16@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # 3 sponsored, 5 organic = 8 total
    for i in range(3):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Total #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
    for i in range(5):
        create_experience(db_session, prov, title=f"Organic Total #{i+1}")
    db_session.commit()

    # Check across multiple pages
    r1 = client.get("/api/v1/eco-experiences?page=1&page_size=3")
    assert r1.json()["total"] == 8

    r2 = client.get("/api/v1/eco-experiences?page=2&page_size=3")
    assert r2.json()["total"] == 8

    r3 = client.get("/api/v1/eco-experiences?page=3&page_size=3")
    assert r3.json()["total"] == 8


def test_sponsored_and_eco_badge_independence(
    client: TestClient, db_session: Session
):
    """Scenario 17: is_sponsored and has_verified_eco_badge are orthogonal (all 4 combinations)."""
    user = create_user(db_session, "prov17@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # A: Sponsored + Badge
    exp_a = create_experience(
        db_session,
        prov,
        title="Sponsored With Badge",
        has_verified_eco_badge=True,
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp_a,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )

    # B: Sponsored + No Badge
    exp_b = create_experience(
        db_session,
        prov,
        title="Sponsored Without Badge",
        has_verified_eco_badge=False,
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp_b,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )

    # C: Organic + Badge
    exp_c = create_experience(
        db_session,
        prov,
        title="Organic With Badge",
        has_verified_eco_badge=True,
    )

    # D: Organic + No Badge
    exp_d = create_experience(
        db_session,
        prov,
        title="Organic Without Badge",
        has_verified_eco_badge=False,
    )
    db_session.commit()

    resp = client.get("/api/v1/eco-experiences?page_size=10")
    assert resp.status_code == 200
    items_by_id = {item["id"]: item for item in resp.json()["items"]}

    assert items_by_id[exp_a.id]["is_sponsored"] is True
    assert items_by_id[exp_a.id]["has_verified_eco_badge"] is True

    assert items_by_id[exp_b.id]["is_sponsored"] is True
    assert items_by_id[exp_b.id]["has_verified_eco_badge"] is False

    assert items_by_id[exp_c.id]["is_sponsored"] is False
    assert items_by_id[exp_c.id]["has_verified_eco_badge"] is True

    assert items_by_id[exp_d.id]["is_sponsored"] is False
    assert items_by_id[exp_d.id]["has_verified_eco_badge"] is False

    # Filter with eco_badge_only=true
    badge_resp = client.get(
        "/api/v1/eco-experiences?eco_badge_only=true&page_size=10"
    )
    assert badge_resp.status_code == 200
    badge_ids = {item["id"] for item in badge_resp.json()["items"]}
    assert badge_ids == {exp_a.id, exp_c.id}


def test_private_payment_metadata_never_leaked(
    client: TestClient, db_session: Session
):
    """Scenario 18: Sensitive billing and review fields are never exposed in public responses."""
    user = create_user(db_session, "prov18@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)
    exp = create_experience(db_session, prov, title="Leaked Data Audit Trek")

    create_synthetic_campaign(
        db_session,
        prov,
        exp,
        pkg,
        campaign_status="active",
        payment_status="paid",
        payment_reference="CONFIDENTIAL-BANK-REF-9999",
        review_reason="Confidential admin moderation reason",
    )
    db_session.commit()

    forbidden_keys = [
        "payment_status",
        "payment_reference",
        "package_price_snapshot",
        "duration_days_snapshot",
        "currency_snapshot",
        "review_reason",
        "reviewed_by",
        "reviewed_at",
        "requested_at",
        "package_id",
        "starts_at",
        "ends_at",
    ]

    # Check discovery list summary
    resp = client.get("/api/v1/eco-experiences")
    assert resp.status_code == 200
    summary = resp.json()["items"][0]
    for key in forbidden_keys:
        assert key not in summary, f"Found private field '{key}' in public summary!"

    # Check discovery detail
    detail_resp = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    for key in forbidden_keys:
        assert key not in detail, f"Found private field '{key}' in public detail!"


def test_sql_server_compatible_query_generation(db_session: Session):
    """Scenario 19: Validates SQL Server (mssql dialect) query generation without compilation errors."""
    from sqlalchemy import exists, select

    now = datetime.now(UTC)

    # Test the correlated EXISTS subquery compilation for MSSQL
    stmt = (
        select(EcoExperience)
        .where(
            EcoExperience.listing_status == "approved",
            EcoExperience.is_published.is_(True),
            exists().where(
                EcoSponsoredPromotion.eco_experience_id == EcoExperience.id,
                EcoSponsoredPromotion.campaign_status == "active",
                EcoSponsoredPromotion.payment_status == "paid",
                EcoSponsoredPromotion.starts_at <= now,
                EcoSponsoredPromotion.ends_at > now,
            ),
        )
        .order_by(EcoExperience.created_at.desc())
        .offset(0)
        .limit(10)
    )

    compiled_sql = str(stmt.compile(dialect=mssql.dialect()))
    assert "EXISTS" in compiled_sql
    assert (
        "OFFSET" in compiled_sql
        or "ROW_NUMBER" in compiled_sql
        or "TOP" in compiled_sql
    )


# ==============================================================================
# Edge-Case Hardening Tests
# ==============================================================================


def test_sponsored_heavy_s7_o0_p5(client: TestClient, db_session: Session):
    """
    Edge Case A: Sponsored inventory exceeds organic inventory (S=7, O=0, P=5).
    Verifies:
    - total = 7, total_pages = 2
    - Page 1 returns 5 items (2 boosted placements at top + 3 ordinary slots backfilled)
    - Page 2 returns remaining 2 items (both boosted)
    - Zero duplicates across pages, zero missing items (all 7 returned across 2 pages)
    - All 7 items have is_sponsored=True representing their active paid campaign.
    """
    user = create_user(db_session, "prov_s7o0@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    spon_exps = []
    for i in range(7):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Heavy S7 #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
        spon_exps.append(exp)
    db_session.commit()

    # Page 1 (page_size=5) -> must return full page of 5 items
    r1 = client.get("/api/v1/eco-experiences?page=1&page_size=5")
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["total"] == 7
    assert d1["total_pages"] == 2
    assert len(d1["items"]) == 5
    for item in d1["items"]:
        assert item["is_sponsored"] is True

    # Page 2 (page_size=5) -> returns remaining 2 items
    r2 = client.get("/api/v1/eco-experiences?page=2&page_size=5")
    assert r2.status_code == 200
    d2 = r2.json()
    assert len(d2["items"]) == 2
    for item in d2["items"]:
        assert item["is_sponsored"] is True

    # Traversal completeness
    all_returned_ids = [item["id"] for item in d1["items"]] + [
        item["id"] for item in d2["items"]
    ]
    expected_ids = [exp.id for exp in spon_exps]
    assert len(all_returned_ids) == 7
    assert set(all_returned_ids) == set(expected_ids)
    assert len(set(all_returned_ids)) == 7, "Found duplicate across pages!"


def test_sponsored_heavy_s10_o1_p5(client: TestClient, db_session: Session):
    """
    Edge Case B: Sponsored inventory heavily dominates (S=10, O=1, P=5).
    Verifies:
    - total = 11, total_pages = 3
    - Page 1 returns 5 items (2 boosted + 1 organic + 2 backfill)
    - Page 2 returns 5 items (2 boosted + 3 backfill)
    - Page 3 returns 1 item (1 boosted)
    - Zero duplicates across pages, zero missing items (all 11 returned across 3 pages)
    - Organic item appears accurately with is_sponsored=False
    """
    user = create_user(db_session, "prov_s10o1@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    spon_exps = []
    for i in range(10):
        exp = create_experience(
            db_session, prov, title=f"Sponsored Dominant S10 #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
        spon_exps.append(exp)

    org_exp = create_experience(
        db_session, prov, title="Single Organic Experience"
    )
    db_session.commit()

    all_returned = []
    for p in range(1, 4):
        resp = client.get(f"/api/v1/eco-experiences?page={p}&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 11
        assert data["total_pages"] == 3
        if p in (1, 2):
            assert len(data["items"]) == 5
        else:
            assert len(data["items"]) == 1
        all_returned.extend(data["items"])

    assert len(all_returned) == 11
    unique_ids = {item["id"] for item in all_returned}
    assert len(unique_ids) == 11
    assert org_exp.id in unique_ids

    # Check organic item flag
    org_item = next(item for item in all_returned if item["id"] == org_exp.id)
    assert org_item["is_sponsored"] is False


def test_mixed_s5_o5_p5(client: TestClient, db_session: Session):
    """
    Edge Case C: Evenly mixed dataset (S=5, O=5, P=5).
    Verifies:
    - total = 10, total_pages = 2
    - Page 1 returns 5 items (2 boosted sponsored + 3 organic)
    - Page 2 returns 5 items (2 boosted sponsored + 2 organic + 1 backfilled sponsored)
    - All 10 items returned with 0 duplicates and 0 missing items
    """
    user = create_user(db_session, "prov_s5o5@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    spon_exps = []
    for i in range(5):
        exp = create_experience(
            db_session, prov, title=f"Mixed Sponsored #{i+1}"
        )
        create_synthetic_campaign(
            db_session,
            prov,
            exp,
            pkg,
            campaign_status="active",
            payment_status="paid",
        )
        spon_exps.append(exp)

    org_exps = []
    for i in range(5):
        exp = create_experience(db_session, prov, title=f"Mixed Organic #{i+1}")
        org_exps.append(exp)
    db_session.commit()

    all_returned = []
    for p in range(1, 3):
        resp = client.get(f"/api/v1/eco-experiences?page={p}&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert data["total_pages"] == 2
        assert len(data["items"]) == 5
        all_returned.extend(data["items"])

    assert len(all_returned) == 10
    unique_ids = {item["id"] for item in all_returned}
    assert len(unique_ids) == 10


def test_organic_heavy_s1_o10_p5(client: TestClient, db_session: Session):
    """
    Edge Case D: Organic-heavy dataset (S=1, O=10, P=5).
    Verifies:
    - total = 11, total_pages = 3
    - Page 1 returns 5 items (1 sponsored + 4 organic)
    - Page 2 returns 5 items (5 organic)
    - Page 3 returns 1 item (1 organic)
    - All 11 items returned with 0 duplicates and 0 missing items
    """
    user = create_user(db_session, "prov_s1o10@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    exp_s = create_experience(
        db_session, prov, title="Lone Sponsored Experience"
    )
    create_synthetic_campaign(
        db_session,
        prov,
        exp_s,
        pkg,
        campaign_status="active",
        payment_status="paid",
    )

    for i in range(10):
        create_experience(db_session, prov, title=f"Heavy Organic #{i+1}")
    db_session.commit()

    all_returned = []
    for p in range(1, 4):
        resp = client.get(f"/api/v1/eco-experiences?page={p}&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 11
        assert data["total_pages"] == 3
        if p in (1, 2):
            assert len(data["items"]) == 5
        else:
            assert len(data["items"]) == 1
        all_returned.extend(data["items"])

    assert len(all_returned) == 11
    unique_ids = {item["id"] for item in all_returned}
    assert len(unique_ids) == 11
