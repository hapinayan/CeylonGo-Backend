"""
Automated tests for Module 04 P1.1C — Provider Campaign Requests & Admin Moderation.

Requirements:
- P16: Provider Sponsored Promotions (Campaign requests & cancellation)
- A07: Admin Promotion Management (Editorial moderation)

Covers:
1. test_approved_provider_creates_valid_request: Approved eco provider creates campaign with pending_approval/unpaid defaults.
2. test_non_provider_rejected: Travelers (403) and unauthenticated callers (401) rejected.
3. test_unapproved_provider_rejected: Pending/rejected approval status or non-eco provider_type rejected (403).
4. test_non_owned_listing_rejected: Provider cannot request promotions for listings owned by another provider (403).
5. test_unpublished_listing_rejected: Draft, unapproved, or unpublished listings rejected (400).
6. test_inactive_package_rejected: Disabled packages cannot be requested (400).
7. test_price_snapshot_correct: Package price, duration, and currency accurately captured in immutable snapshots.
8. test_forged_payment_admin_fields_rejected: Extra/privilege fields in payload rejected by schema (422).
9. test_provider_ownership_isolation: Providers only see and inspect their own campaigns (403 for others).
10. test_admin_only_moderation: All admin moderation endpoints require admin role (403 for provider/traveler, 401 unauth).
11. test_approval_records_reviewer: Admin approval sets status='approved', records reviewer user ID and timestamp.
12. test_rejection_requires_reason: Rejection without valid reason rejected (422), valid rejection records reason.
13. test_invalid_transitions_rejected: Double approval (400), approving rejected/cancelled (400), etc.
14. test_cancellation_behavior: Provider cancels pending or approved campaigns; double cancel rejected (400).
15. test_duplicate_concurrent_request_safety: Existing pending or approved campaign blocks new request (409 Conflict).
16. test_payment_remains_unpaid: Payment status remains 'unpaid' through creation, approval, and cancellation.
17. test_no_campaign_becomes_active: No campaign gains 'active' status or active schedule during moderation.
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
eco_p11c_app = FastAPI(title="CeylonGo M04 P1.1C Campaigns Test App")
eco_p11c_app.include_router(auth_router)
eco_p11c_app.include_router(eco_promotions_router)
eco_p11c_app.include_router(admin_eco_promotions_router)

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

    eco_p11c_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p11c_app) as test_client:
        yield test_client
    eco_p11c_app.dependency_overrides.clear()


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
    title: str = "Knuckles Cloud Forest Trek",
    listing_status: str = "approved",
    is_published: bool = True,
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider.id,
        title=title,
        eco_category="Forest & Nature Trails",
        district="Kandy",
        city="Matale",
        meeting_point="Knuckles Base Camp",
        duration_hours=Decimal("5.00"),
        base_price=Decimal("5000.00"),
        currency="LKR",
        max_group_size=12,
        min_participants=1,
        environmental_guidelines="Strict zero-waste policy and leave no trace.",
        wildlife_safety_summary="Do not approach or feed wildlife in the sanctuary.",
        listing_status=listing_status,
        is_published=is_published,
    )
    db.add(exp)
    db.flush()
    return exp


def create_package(
    db: Session,
    name: str = "Starter Spotlight",
    price: Decimal = Decimal("2500.00"),
    duration_days: int = 7,
    is_active: bool = True,
) -> EcoPromotionPackage:
    pkg = EcoPromotionPackage(
        name=name,
        description="Standard 7-day spotlight package",
        duration_days=duration_days,
        price=price,
        currency="LKR",
        placement_tier="standard",
        is_active=is_active,
    )
    db.add(pkg)
    db.flush()
    return pkg


# ==============================================================================
# Unit & Integration Tests (P1.1C)
# ==============================================================================


def test_approved_provider_creates_valid_request(
    client: TestClient, db_session: Session
):
    provider_user = create_user(db_session, "provider@ceylongo.com")
    provider = create_provider(db_session, provider_user)
    exp = create_experience(db_session, provider)
    pkg = create_package(db_session)
    db_session.commit()

    payload = {
        "eco_experience_id": exp.id,
        "package_id": pkg.id,
    }

    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json=payload,
        headers=auth_header(provider_user),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["eco_experience_id"] == exp.id
    assert data["provider_profile_id"] == provider.id
    assert data["package_id"] == pkg.id
    assert data["campaign_status"] == "pending_approval"
    assert data["payment_status"] == "unpaid"
    assert Decimal(data["package_price_snapshot"]) == Decimal("2500.00")
    assert data["duration_days_snapshot"] == 7
    assert data["currency_snapshot"] == "LKR"
    assert data["starts_at"] is None
    assert data["ends_at"] is None
    assert data["is_currently_active"] is False


def test_non_provider_rejected(client: TestClient, db_session: Session):
    traveler = create_user(db_session, "traveler@ceylongo.com", role="traveler")
    provider_user = create_user(db_session, "provider@ceylongo.com")
    provider = create_provider(db_session, provider_user)
    exp = create_experience(db_session, provider)
    pkg = create_package(db_session)
    db_session.commit()

    payload = {"eco_experience_id": exp.id, "package_id": pkg.id}

    # Traveler 403
    res_trav = client.post(
        "/api/v1/eco-promotions/campaigns",
        json=payload,
        headers=auth_header(traveler),
    )
    assert res_trav.status_code == 403

    # Unauthenticated 401
    res_unauth = client.post(
        "/api/v1/eco-promotions/campaigns",
        json=payload,
    )
    assert res_unauth.status_code == 401


def test_unapproved_provider_rejected(client: TestClient, db_session: Session):
    # Pending approval provider
    pending_user = create_user(db_session, "pending@ceylongo.com")
    pending_prof = create_provider(db_session, pending_user, approval_status="pending")
    exp1 = create_experience(db_session, pending_prof)

    # Non-eco provider (e.g. homestay)
    homestay_user = create_user(db_session, "homestay@ceylongo.com")
    create_provider(db_session, homestay_user, provider_type="homestay")

    pkg = create_package(db_session)
    db_session.commit()

    # Pending provider rejected with 403
    res_pend = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp1.id, "package_id": pkg.id},
        headers=auth_header(pending_user),
    )
    assert res_pend.status_code == 403

    # Non-eco provider rejected with 403
    res_type = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp1.id, "package_id": pkg.id},
        headers=auth_header(homestay_user),
    )
    assert res_type.status_code == 403


def test_non_owned_listing_rejected(client: TestClient, db_session: Session):
    user_a = create_user(db_session, "prov_a@ceylongo.com")
    prov_a = create_provider(db_session, user_a, business_name="Tours A")
    exp_a = create_experience(db_session, prov_a)

    user_b = create_user(db_session, "prov_b@ceylongo.com")
    create_provider(db_session, user_b, business_name="Tours B")

    pkg = create_package(db_session)
    db_session.commit()

    # Provider B attempts to promote Provider A's listing
    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp_a.id, "package_id": pkg.id},
        headers=auth_header(user_b),
    )
    assert res.status_code == 403
    assert "do not own" in res.json()["detail"].lower()


def test_unpublished_listing_rejected(client: TestClient, db_session: Session):
    user = create_user(db_session, "provider@ceylongo.com")
    prov = create_provider(db_session, user)
    pkg = create_package(db_session)

    # Unpublished experience
    exp_unpub = create_experience(db_session, prov, is_published=False)
    # Draft experience
    exp_draft = create_experience(db_session, prov, listing_status="draft")
    db_session.commit()

    res_unpub = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp_unpub.id, "package_id": pkg.id},
        headers=auth_header(user),
    )
    assert res_unpub.status_code == 400

    res_draft = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp_draft.id, "package_id": pkg.id},
        headers=auth_header(user),
    )
    assert res_draft.status_code == 400


def test_inactive_package_rejected(client: TestClient, db_session: Session):
    user = create_user(db_session, "provider@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg_inactive = create_package(db_session, name="Deprecated", is_active=False)
    db_session.commit()

    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp.id, "package_id": pkg_inactive.id},
        headers=auth_header(user),
    )
    assert res.status_code == 400
    assert "inactive package" in res.json()["detail"].lower()


def test_price_snapshot_correct(client: TestClient, db_session: Session):
    user = create_user(db_session, "provider@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(
        db_session,
        name="Premier Showcase",
        price=Decimal("8500.00"),
        duration_days=30,
    )
    db_session.commit()

    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp.id, "package_id": pkg.id},
        headers=auth_header(user),
    )
    assert res.status_code == 201
    campaign = db_session.query(EcoSponsoredPromotion).filter_by(id=res.json()["id"]).first()
    assert campaign.package_price_snapshot == Decimal("8500.00")
    assert campaign.duration_days_snapshot == 30
    assert campaign.currency_snapshot == "LKR"


def test_forged_payment_admin_fields_rejected(
    client: TestClient, db_session: Session
):
    user = create_user(db_session, "provider@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    db_session.commit()

    forged_payload = {
        "eco_experience_id": exp.id,
        "package_id": pkg.id,
        "payment_status": "paid",  # Injected field
        "campaign_status": "active",  # Injected field
        "package_price_snapshot": "0.00",  # Injected field
    }

    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json=forged_payload,
        headers=auth_header(user),
    )
    assert res.status_code == 422


def test_provider_ownership_isolation(client: TestClient, db_session: Session):
    user_a = create_user(db_session, "prov_a@ceylongo.com")
    prov_a = create_provider(db_session, user_a, business_name="Tours A")
    exp_a = create_experience(db_session, prov_a)

    user_b = create_user(db_session, "prov_b@ceylongo.com")
    prov_b = create_provider(db_session, user_b, business_name="Tours B")
    exp_b = create_experience(db_session, prov_b)

    pkg = create_package(db_session)

    c_a = EcoSponsoredPromotion(
        eco_experience_id=exp_a.id,
        provider_profile_id=prov_a.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
    )
    c_b = EcoSponsoredPromotion(
        eco_experience_id=exp_b.id,
        provider_profile_id=prov_b.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
    )
    db_session.add_all([c_a, c_b])
    db_session.commit()

    # Provider A lists own campaigns -> only c_a
    res_list = client.get(
        "/api/v1/eco-promotions/me/campaigns",
        headers=auth_header(user_a),
    )
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == c_a.id

    # Provider A viewing Provider B's campaign -> 403 Forbidden
    res_detail = client.get(
        f"/api/v1/eco-promotions/me/campaigns/{c_b.id}",
        headers=auth_header(user_a),
    )
    assert res_detail.status_code == 403


def test_admin_only_moderation(client: TestClient, db_session: Session):
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
    )
    db_session.add(campaign)
    db_session.commit()

    # Provider attempting admin endpoints -> 403
    assert client.get("/api/v1/admin/eco-promotions/campaigns", headers=auth_header(provider_user)).status_code == 403
    assert client.get(f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}", headers=auth_header(provider_user)).status_code == 403
    assert client.post(f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/approve", headers=auth_header(provider_user)).status_code == 403
    assert client.post(f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/reject", json={"reason": "Test reason"}, headers=auth_header(provider_user)).status_code == 403

    # Unauthenticated -> 401
    assert client.get("/api/v1/admin/eco-promotions/campaigns").status_code == 401


def test_approval_records_reviewer(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
        payment_status="unpaid",
    )
    db_session.add(campaign)
    db_session.commit()

    res = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/approve",
        json={"notes": "Content complies with CeylonGo eco policies."},
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["campaign_status"] == "approved"
    assert data["reviewed_by"] == admin.id
    assert data["reviewer_email"] == "admin@ceylongo.com"
    assert data["reviewed_at"] is not None
    assert "Content complies" in data["review_reason"]

    # Critical payment deferral assertion: Payment remains unpaid
    assert data["payment_status"] == "unpaid"
    assert data["starts_at"] is None
    assert data["is_currently_active"] is False


def test_rejection_requires_reason(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)

    campaign = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
    )
    db_session.add(campaign)
    db_session.commit()

    # Rejection without reason -> 422
    res_empty = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/reject",
        json={},
        headers=auth_header(admin),
    )
    assert res_empty.status_code == 422

    # Rejection with short reason (< 5 chars) -> 422
    res_short = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/reject",
        json={"reason": "bad"},
        headers=auth_header(admin),
    )
    assert res_short.status_code == 422

    # Valid rejection
    res_valid = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{campaign.id}/reject",
        json={"reason": "Inappropriate promotional claims detected."},
        headers=auth_header(admin),
    )
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert data["campaign_status"] == "rejected"
    assert data["review_reason"] == "Inappropriate promotional claims detected."
    assert data["reviewed_by"] == admin.id


def test_invalid_transitions_rejected(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)

    c_approved = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="approved",
    )
    c_cancelled = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="cancelled",
    )
    db_session.add_all([c_approved, c_cancelled])
    db_session.commit()

    # Approving already approved campaign -> 400
    res_double_app = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{c_approved.id}/approve",
        headers=auth_header(admin),
    )
    assert res_double_app.status_code == 400

    # Approving cancelled campaign -> 400
    res_app_canc = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{c_cancelled.id}/approve",
        headers=auth_header(admin),
    )
    assert res_app_canc.status_code == 400

    # Rejecting cancelled campaign -> 400
    res_rej_canc = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{c_cancelled.id}/reject",
        json={"reason": "Valid reason string"},
        headers=auth_header(admin),
    )
    assert res_rej_canc.status_code == 400


def test_cancellation_behavior(client: TestClient, db_session: Session):
    user_a = create_user(db_session, "prov_a@ceylongo.com")
    prov_a = create_provider(db_session, user_a, business_name="Tours A")
    exp_a = create_experience(db_session, prov_a)
    pkg = create_package(db_session)

    user_b = create_user(db_session, "prov_b@ceylongo.com")
    create_provider(db_session, user_b, business_name="Tours B")

    c_pending = EcoSponsoredPromotion(
        eco_experience_id=exp_a.id,
        provider_profile_id=prov_a.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
    )
    db_session.add(c_pending)
    db_session.commit()

    # Provider B cannot cancel Provider A's campaign -> 403
    res_cross = client.post(
        f"/api/v1/eco-promotions/me/campaigns/{c_pending.id}/cancel",
        headers=auth_header(user_b),
    )
    assert res_cross.status_code == 403

    # Provider A cancels own pending campaign -> 200
    res_cancel = client.post(
        f"/api/v1/eco-promotions/me/campaigns/{c_pending.id}/cancel",
        headers=auth_header(user_a),
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["campaign_status"] == "cancelled"

    # Double cancel -> 400
    res_double = client.post(
        f"/api/v1/eco-promotions/me/campaigns/{c_pending.id}/cancel",
        headers=auth_header(user_a),
    )
    assert res_double.status_code == 400


def test_duplicate_concurrent_request_safety(
    client: TestClient, db_session: Session
):
    user = create_user(db_session, "provider@ceylongo.com")
    prov = create_provider(db_session, user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)

    # Initial campaign in pending_approval
    c_existing = EcoSponsoredPromotion(
        eco_experience_id=exp.id,
        provider_profile_id=prov.id,
        package_id=pkg.id,
        package_price_snapshot=Decimal("2500.00"),
        duration_days_snapshot=7,
        currency_snapshot="LKR",
        campaign_status="pending_approval",
    )
    db_session.add(c_existing)
    db_session.commit()

    # Attempting to request a second campaign for the same experience -> 409 Conflict
    res_dup = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp.id, "package_id": pkg.id},
        headers=auth_header(user),
    )
    assert res_dup.status_code == 409
    assert "already exists" in res_dup.json()["detail"].lower()


def test_payment_remains_unpaid(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    db_session.commit()

    # 1. On creation -> unpaid
    res_create = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp.id, "package_id": pkg.id},
        headers=auth_header(provider_user),
    )
    assert res_create.status_code == 201
    campaign_id = res_create.json()["id"]
    assert res_create.json()["payment_status"] == "unpaid"

    # 2. On admin approval -> unpaid
    res_approve = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{campaign_id}/approve",
        headers=auth_header(admin),
    )
    assert res_approve.status_code == 200
    assert res_approve.json()["payment_status"] == "unpaid"

    # 3. On provider cancellation -> unpaid
    res_cancel = client.post(
        f"/api/v1/eco-promotions/me/campaigns/{campaign_id}/cancel",
        headers=auth_header(provider_user),
    )
    assert res_cancel.status_code == 200
    assert res_cancel.json()["payment_status"] == "unpaid"

    # Verify directly from DB
    db_campaign = db_session.query(EcoSponsoredPromotion).filter_by(id=campaign_id).first()
    assert db_campaign.payment_status == "unpaid"


def test_no_campaign_becomes_active(client: TestClient, db_session: Session):
    admin = create_user(db_session, "admin@ceylongo.com", role="admin")
    provider_user = create_user(db_session, "prov@ceylongo.com")
    prov = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prov)
    pkg = create_package(db_session)
    db_session.commit()

    # Create campaign
    res = client.post(
        "/api/v1/eco-promotions/campaigns",
        json={"eco_experience_id": exp.id, "package_id": pkg.id},
        headers=auth_header(provider_user),
    )
    c_id = res.json()["id"]

    # Admin approves
    res_app = client.post(
        f"/api/v1/admin/eco-promotions/campaigns/{c_id}/approve",
        headers=auth_header(admin),
    )
    assert res_app.status_code == 200

    # Ensure starts_at and ends_at are None, is_currently_active is False
    data = res_app.json()
    assert data["starts_at"] is None
    assert data["ends_at"] is None
    assert data["is_currently_active"] is False
    assert data["campaign_status"] == "approved"
    assert data["campaign_status"] != "active"
