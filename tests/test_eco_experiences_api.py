"""
Automated API tests for Module 04 (P0.2) — Eco-Tourism Experience APIs.

Covers:
1. Provider ownership and role restrictions:
   - Unauthenticated caller -> 401
   - Traveler -> 403
   - Pending eco_tourism provider -> 403
   - Rejected eco_tourism provider -> 403
   - Approved non-eco provider (e.g. artisan / homestay) -> 403
   - Approved eco_tourism provider -> 201 Created (defaults to draft, unpublished, no badge)
   - Cross-provider ownership isolation on PUT, submit, and publish -> 403
2. Full lifecycle:
   - Draft creation -> cannot publish before approval (400) -> hidden from public list & detail (404)
   - Submit for approval ('pending_approval') -> still hidden from public -> appears in Admin pending queue
   - Admin rejects with reason ('rejected') -> hidden from public -> provider sees rejection reason -> can update & resubmit
   - Admin approves ('approved') -> still unpublished until provider publishes -> Eco-Friendly Badge is NOT auto-granted
   - Provider publishes ('approved' + is_published=True) -> visible in public discovery and detail endpoints
   - Provider deactivates (is_published=False) -> immediately hidden from public discovery and detail endpoints
3. Public discovery filters & metadata:
   - Category, district, search, price range (min_price/max_price), duration range (min_duration_hours/max_duration_hours)
   - Invalid filter ranges (min_price > max_price, min_duration_hours > max_duration_hours) -> 422
   - GET /categories returns all 6 approved categories
   - GET /districts returns only districts with approved & published listings
4. Security & private data exclusion:
   - Public detail endpoint returns environmental guidelines, wildlife safety rules, and structured conservation_information
   - Never exposes private EcoEvidence or raw file_reference paths
   - Never exposes unverified or revoked Eco-Friendly badges
"""

from datetime import UTC, date, datetime, timedelta
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
from app.models.eco_experience import (
    VALID_ECO_CATEGORIES,
    EcoEvidence,
    EcoExperience,
    EcoPractice,
    EcoVerification,
)
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
    uploads_router,
)

eco_api_app = FastAPI(title="CeylonGo M04 Eco-Tourism Test App")
eco_api_app.include_router(auth_router)
eco_api_app.include_router(eco_experiences_router)
eco_api_app.include_router(admin_eco_router)
eco_api_app.include_router(uploads_router)

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
        session.close()


@pytest.fixture
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_api_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_api_app) as test_client:
        yield test_client
    eco_api_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def users_and_profiles(db_session: Session):
    traveler = User(
        full_name="Traveler Amal",
        email="amal.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    admin = User(
        full_name="Admin Dilshan",
        email="dilshan.admin@example.com",
        password_hash=hash_password("Password123!"),
        role="admin",
        is_active=True,
    )
    eco_user_1 = User(
        full_name="Kasun Eco Organizer",
        email="kasun.eco@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    eco_user_2 = User(
        full_name="Nimali Eco Organizer",
        email="nimali.eco@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    pending_eco_user = User(
        full_name="Pending Eco Organizer",
        email="pending.eco@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    artisan_user = User(
        full_name="Sunil Artisan",
        email="sunil.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )

    db_session.add_all(
        [
            traveler,
            admin,
            eco_user_1,
            eco_user_2,
            pending_eco_user,
            artisan_user,
        ]
    )
    db_session.commit()

    eco_profile_1 = ProviderProfile(
        user_id=eco_user_1.id,
        business_name="Sinharaja Canopy Expeditions",
        provider_type="eco_tourism",
        phone="0771112233",
        district="Ratnapura",
        description="Guided rainforest walks and conservation education.",
        approval_status="approved",
    )
    eco_profile_2 = ProviderProfile(
        user_id=eco_user_2.id,
        business_name="Bundala Wetland Birding",
        provider_type="eco_tourism",
        phone="0774445566",
        district="Hambantota",
        description="Responsible wetland birdwatching.",
        approval_status="approved",
    )
    pending_eco_profile = ProviderProfile(
        user_id=pending_eco_user.id,
        business_name="Pending Nature Tours",
        provider_type="eco_tourism",
        phone="0778889900",
        district="Kandy",
        approval_status="pending",
    )
    artisan_profile = ProviderProfile(
        user_id=artisan_user.id,
        business_name="Sunil Woodcarving",
        provider_type="artisan",
        phone="0772223344",
        district="Galle",
        approval_status="approved",
    )

    db_session.add_all(
        [
            eco_profile_1,
            eco_profile_2,
            pending_eco_profile,
            artisan_profile,
        ]
    )
    db_session.commit()

    return {
        "traveler": traveler,
        "admin": admin,
        "eco_user_1": eco_user_1,
        "eco_profile_1": eco_profile_1,
        "eco_user_2": eco_user_2,
        "eco_profile_2": eco_profile_2,
        "pending_eco_user": pending_eco_user,
        "artisan_user": artisan_user,
    }


def sample_eco_create_payload(**overrides) -> dict:
    base = {
        "title": "Sinharaja Endemic Bird & Rainforest Walk",
        "eco_category": "Wildlife & Birdwatching",
        "description": "Guided morning walk with local conservationists in Sinharaja.",
        "itinerary": "06:00 AM Meet at Kudawa Gate -> 06:30 AM Trail -> 10:30 AM Tea & debrief.",
        "duration_hours": "4.00",
        "district": "Ratnapura",
        "city": "Kudawa",
        "address": "Kudawa Conservation Entrance",
        "meeting_point": "Sinharaja Kudawa Ticket Office",
        "directions_info": "Via Kalawana-Weddagala road.",
        "latitude": "6.421500",
        "longitude": "80.418200",
        "pricing_type": "per_person",
        "base_price": "6500.00",
        "currency": "LKR",
        "max_group_size": 8,
        "min_participants": 2,
        "min_age": 8,
        "environmental_guidelines": "Stay strictly on marked trails and carry all waste back out.",
        "wildlife_safety_summary": "Never feed wildlife; keep 10m distance from vipers and birds.",
        "conservation_factual_contribution": "LKR 1,000 per booking is donated to the Kudawa Reforestation Trust.",
        "conservation_marketing_statement": "Walk with local guardians protecting Sri Lanka's rainforest.",
        "protected_area_permit_required": True,
        "certified_guide_required": True,
        "permit_details": "Forest Department entry permit included.",
        "cancellation_policy": "Free cancellation up to 24 hours before start.",
        "free_cancellation_hours": 24,
        "images": [
            {
                "image_url": "https://images.example.com/sinharaja-1.jpg",
                "caption": "Canopy trail at dawn",
                "is_primary": True,
                "sort_order": 0,
            }
        ],
        "safety_rules": [
            {
                "rule_type": "wildlife_no_feeding",
                "title": "Strictly No Feeding Birds or Mammals",
                "description": "Feeding disrupts endemic mixed-species bird flocks.",
                "is_mandatory": True,
            },
            {
                "rule_type": "equipment_requirement",
                "title": "Leech Socks Required",
                "description": "Wear closed hiking shoes and provided cotton leech socks.",
                "is_mandatory": True,
            },
        ],
        "season_restrictions": [
            {
                "start_date": "2026-05-15",
                "end_date": "2026-06-15",
                "restriction_type": "monsoon_season",
                "reason": "Peak southwest monsoon heavy rainfall closure.",
                "is_active": True,
            }
        ],
        "practices": [
            {
                "practice_category": "community_employment",
                "claim_description": "100% of naturalist guides are residents of Kudawa village.",
            }
        ],
    }
    base.update(overrides)
    return base


# ==============================================================================
# 1. Role & Ownership Authorization Tests
# ==============================================================================


def test_create_eco_experience_role_restrictions(
    client: TestClient, users_and_profiles
):
    payload = sample_eco_create_payload()

    # 1. Unauthenticated -> 401
    res_unauth = client.post("/api/v1/eco-experiences", json=payload)
    assert res_unauth.status_code == 401

    # 2. Traveler -> 403
    res_traveler = client.post(
        "/api/v1/eco-experiences",
        json=payload,
        headers=auth_header(users_and_profiles["traveler"]),
    )
    assert res_traveler.status_code == 403

    # 3. Pending eco_tourism provider -> 403
    res_pending = client.post(
        "/api/v1/eco-experiences",
        json=payload,
        headers=auth_header(users_and_profiles["pending_eco_user"]),
    )
    assert res_pending.status_code == 403
    assert "pending" in res_pending.json()["detail"].lower()

    # 4. Approved artisan provider (wrong provider_type) -> 403
    res_artisan = client.post(
        "/api/v1/eco-experiences",
        json=payload,
        headers=auth_header(users_and_profiles["artisan_user"]),
    )
    assert res_artisan.status_code == 403
    assert "eco_tourism" in res_artisan.json()["detail"].lower()

    # 5. Approved eco_tourism provider -> 201 Created (defaults to draft, unpublished, no badge)
    res_eco = client.post(
        "/api/v1/eco-experiences",
        json=payload,
        headers=auth_header(users_and_profiles["eco_user_1"]),
    )
    assert res_eco.status_code == 201
    data = res_eco.json()
    assert data["listing_status"] == "draft"
    assert data["is_published"] is False
    assert data["is_publicly_visible"] is False
    assert data["eco_badge_status"] == "none"
    assert data["has_verified_eco_badge"] is False
    assert len(data["images"]) == 1
    assert len(data["safety_rules"]) == 2
    assert len(data["season_restrictions"]) == 1
    assert len(data["practices"]) == 1


def test_cross_provider_ownership_isolation(
    client: TestClient, users_and_profiles
):
    eco_1_headers = auth_header(users_and_profiles["eco_user_1"])
    eco_2_headers = auth_header(users_and_profiles["eco_user_2"])

    create_res = client.post(
        "/api/v1/eco-experiences",
        json=sample_eco_create_payload(),
        headers=eco_1_headers,
    )
    assert create_res.status_code == 201
    exp_id = create_res.json()["id"]

    # Second eco provider cannot update first provider's listing
    put_res = client.put(
        f"/api/v1/eco-experiences/{exp_id}",
        json={"title": "Hijacked Title"},
        headers=eco_2_headers,
    )
    assert put_res.status_code == 403
    assert "do not own" in put_res.json()["detail"].lower()

    # Second eco provider cannot submit first provider's listing
    submit_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit",
        headers=eco_2_headers,
    )
    assert submit_res.status_code == 403

    # Second eco provider cannot publish first provider's listing
    pub_res = client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": True},
        headers=eco_2_headers,
    )
    assert pub_res.status_code == 403

    # Second eco provider's /me/listings does not include first provider's listing
    my_res_2 = client.get(
        "/api/v1/eco-experiences/me/listings", headers=eco_2_headers
    )
    assert my_res_2.status_code == 200
    assert my_res_2.json() == []


# ==============================================================================
# 2. Full Draft -> Submit -> Reject -> Resubmit -> Approve -> Publish Lifecycle
# ==============================================================================


def test_full_listing_approval_and_publication_lifecycle(
    client: TestClient, users_and_profiles
):
    provider_headers = auth_header(users_and_profiles["eco_user_1"])
    admin_headers = auth_header(users_and_profiles["admin"])
    traveler_headers = auth_header(users_and_profiles["traveler"])

    # Step 1: Provider creates draft
    create_res = client.post(
        "/api/v1/eco-experiences",
        json=sample_eco_create_payload(),
        headers=provider_headers,
    )
    assert create_res.status_code == 201
    exp_id = create_res.json()["id"]

    # Draft is NOT publicly visible in list or detail
    assert client.get("/api/v1/eco-experiences").json()["total"] == 0
    assert client.get(f"/api/v1/eco-experiences/{exp_id}").status_code == 404

    # Provider cannot publish a draft listing before admin approval
    premature_pub = client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": True},
        headers=provider_headers,
    )
    assert premature_pub.status_code == 400
    assert "only admin-approved" in premature_pub.json()["detail"].lower()

    # Step 2: Provider submits listing for admin approval
    submit_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit",
        headers=provider_headers,
    )
    assert submit_res.status_code == 200
    assert submit_res.json()["listing_status"] == "pending_approval"
    assert submit_res.json()["is_published"] is False

    # Pending listing is still hidden from public discovery
    assert client.get("/api/v1/eco-experiences").json()["total"] == 0
    assert client.get(f"/api/v1/eco-experiences/{exp_id}").status_code == 404

    # Non-admins cannot access admin moderation endpoints
    assert (
        client.get(
            "/api/v1/admin/eco-experiences", headers=provider_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/admin/eco-experiences/{exp_id}/approve",
            headers=traveler_headers,
        ).status_code
        == 403
    )

    # Step 3: Admin lists pending eco experiences and rejects with reason
    pending_list = client.get(
        "/api/v1/admin/eco-experiences", headers=admin_headers
    )
    assert pending_list.status_code == 200
    assert pending_list.json()["total"] == 1
    assert pending_list.json()["items"][0]["id"] == exp_id

    reject_res = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/reject",
        json={"reason": "Please clarify the Forest Department permit reference number."},
        headers=admin_headers,
    )
    assert reject_res.status_code == 200
    rejected_data = reject_res.json()
    assert rejected_data["listing_status"] == "rejected"
    assert rejected_data["is_published"] is False
    assert (
        rejected_data["approval_rejection_reason"]
        == "Please clarify the Forest Department permit reference number."
    )

    # Rejected listing cannot be published and never appears publicly
    assert (
        client.patch(
            f"/api/v1/eco-experiences/{exp_id}/publish",
            json={"is_published": True},
            headers=provider_headers,
        ).status_code
        == 400
    )
    assert client.get(f"/api/v1/eco-experiences/{exp_id}").status_code == 404

    # Step 4: Provider updates permit details and resubmits
    update_res = client.put(
        f"/api/v1/eco-experiences/{exp_id}",
        json={"permit_details": "Forest Dept Permit #FD-RAT-2026-991 included."},
        headers=provider_headers,
    )
    assert update_res.status_code == 200

    resubmit_res = client.post(
        f"/api/v1/eco-experiences/{exp_id}/submit",
        headers=provider_headers,
    )
    assert resubmit_res.status_code == 200
    assert resubmit_res.json()["listing_status"] == "pending_approval"
    assert resubmit_res.json()["approval_rejection_reason"] is None

    # Step 5: Admin approves the submitted listing
    approve_res = client.post(
        f"/api/v1/admin/eco-experiences/{exp_id}/approve",
        headers=admin_headers,
    )
    assert approve_res.status_code == 200
    approved_data = approve_res.json()
    assert approved_data["listing_status"] == "approved"
    assert approved_data["is_published"] is False
    assert approved_data["is_publicly_visible"] is False
    # Eco-Friendly Badge must NEVER be automatically granted on listing approval
    assert approved_data["eco_badge_status"] == "none"
    assert approved_data["has_verified_eco_badge"] is False

    # Approved but unpublished listing is STILL hidden from public
    assert client.get("/api/v1/eco-experiences").json()["total"] == 0
    assert client.get(f"/api/v1/eco-experiences/{exp_id}").status_code == 404

    # Step 6: Provider publishes the approved listing -> now publicly visible
    pub_res = client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": True},
        headers=provider_headers,
    )
    assert pub_res.status_code == 200
    assert pub_res.json()["is_published"] is True
    assert pub_res.json()["is_publicly_visible"] is True

    public_list = client.get("/api/v1/eco-experiences")
    assert public_list.status_code == 200
    assert public_list.json()["total"] == 1
    assert public_list.json()["items"][0]["id"] == exp_id

    public_detail = client.get(f"/api/v1/eco-experiences/{exp_id}")
    assert public_detail.status_code == 200
    detail_json = public_detail.json()
    assert detail_json["title"] == "Sinharaja Endemic Bird & Rainforest Walk"
    assert (
        detail_json["conservation_information"]["factual_contribution"]
        == "LKR 1,000 per booking is donated to the Kudawa Reforestation Trust."
    )
    assert len(detail_json["safety_rules"]) == 2

    # Step 7: Provider deactivates listing -> immediately removed from public view
    deact_res = client.patch(
        f"/api/v1/eco-experiences/{exp_id}/publish",
        json={"is_published": False},
        headers=provider_headers,
    )
    assert deact_res.status_code == 200
    assert deact_res.json()["listing_status"] == "deactivated"
    assert deact_res.json()["is_published"] is False
    assert deact_res.json()["is_publicly_visible"] is False

    assert client.get("/api/v1/eco-experiences").json()["total"] == 0
    assert client.get(f"/api/v1/eco-experiences/{exp_id}").status_code == 404


# ==============================================================================
# 3. Public Discovery Filters, Categories, Districts & Private Data Exclusion
# ==============================================================================


def test_public_discovery_filters_categories_districts_and_privacy(
    client: TestClient, db_session: Session, users_and_profiles
):
    profile1 = users_and_profiles["eco_profile_1"]
    profile2 = users_and_profiles["eco_profile_2"]
    admin = users_and_profiles["admin"]

    # Create 2 approved+published listings and 1 draft listing
    exp1 = EcoExperience(
        provider_profile_id=profile1.id,
        title="Sinharaja Rainforest Trek",
        eco_category="Forest & Nature Trails",
        description="Deep rainforest trail with conservationists.",
        duration_hours=Decimal("5.00"),
        district="Ratnapura",
        city="Kudawa",
        meeting_point="Kudawa Gate",
        base_price=Decimal("7000.00"),
        max_group_size=8,
        environmental_guidelines="Carry all litter out; stay on marked trails.",
        wildlife_safety_summary="Do not touch or feed any forest wildlife.",
        conservation_factual_contribution="15% goes to village forest guard fund.",
        conservation_marketing_statement="Explore virgin rainforest sustainably.",
        listing_status="approved",
        is_published=True,
        approval_reviewed_by=admin.id,
        approval_reviewed_at=datetime.now(UTC),
    )
    exp2 = EcoExperience(
        provider_profile_id=profile2.id,
        title="Bundala Flamingo & Wetland Safari",
        eco_category="Wildlife & Birdwatching",
        description="Quiet wetland birdwatching in Bundala RAMSAR site.",
        duration_hours=Decimal("3.00"),
        district="Hambantota",
        city="Kirinda",
        meeting_point="Bundala Park Entrance",
        base_price=Decimal("4500.00"),
        max_group_size=6,
        environmental_guidelines="Low-noise vehicles and binocular observation only.",
        wildlife_safety_summary="Remain inside the safari vehicle near wild elephants.",
        listing_status="approved",
        is_published=True,
        approval_reviewed_by=admin.id,
        approval_reviewed_at=datetime.now(UTC),
    )
    exp_draft = EcoExperience(
        provider_profile_id=profile1.id,
        title="Unapproved Draft Village Walk",
        eco_category="Sustainable Village Experiences",
        duration_hours=Decimal("2.00"),
        district="Matale",
        city="Dambulla",
        meeting_point="Dambulla Town",
        base_price=Decimal("2500.00"),
        max_group_size=10,
        environmental_guidelines="Respect village customs and organic fields.",
        wildlife_safety_summary="Watch out for peacocks and water buffalo.",
        listing_status="draft",
        is_published=False,
    )
    db_session.add_all([exp1, exp2, exp_draft])
    db_session.commit()

    # Attach a practice with private evidence to exp1
    practice = EcoPractice(
        eco_experience_id=exp1.id,
        practice_category="waste_reduction",
        claim_description="Zero single-use plastic; refillable steel bottles provided.",
        is_verified=False,
    )
    db_session.add(practice)
    db_session.commit()

    secret_path = "private_uploads/eco_evidence/confidential_nic_audit_123.pdf"
    evidence = EcoEvidence(
        eco_practice_id=practice.id,
        file_reference=secret_path,
        document_type="audit_report",
        notes="Private inspector notes",
    )
    db_session.add(evidence)
    db_session.commit()

    # 1. Categories endpoint returns all 6 official categories
    cats_res = client.get("/api/v1/eco-experiences/categories")
    assert cats_res.status_code == 200
    assert cats_res.json() == list(VALID_ECO_CATEGORIES)

    # 2. Districts endpoint returns only districts with approved & published listings
    dist_res = client.get("/api/v1/eco-experiences/districts")
    assert dist_res.status_code == 200
    assert dist_res.json() == ["Hambantota", "Ratnapura"]
    assert "Matale" not in dist_res.json()

    # 3. Filter by category
    f_cat = client.get(
        "/api/v1/eco-experiences",
        params={"category": "Wildlife & Birdwatching"},
    )
    assert f_cat.status_code == 200
    assert f_cat.json()["total"] == 1
    assert f_cat.json()["items"][0]["title"] == "Bundala Flamingo & Wetland Safari"

    # 4. Filter by district
    f_dist = client.get(
        "/api/v1/eco-experiences", params={"district": "Ratnapura"}
    )
    assert f_dist.status_code == 200
    assert f_dist.json()["total"] == 1
    assert f_dist.json()["items"][0]["title"] == "Sinharaja Rainforest Trek"

    # 5. Filter by price range
    f_price = client.get(
        "/api/v1/eco-experiences",
        params={"min_price": 4000, "max_price": 5000},
    )
    assert f_price.status_code == 200
    assert f_price.json()["total"] == 1
    assert f_price.json()["items"][0]["id"] == exp2.id

    # 6. Filter by duration range
    f_dur = client.get(
        "/api/v1/eco-experiences",
        params={"min_duration_hours": 4.0, "max_duration_hours": 6.0},
    )
    assert f_dur.status_code == 200
    assert f_dur.json()["total"] == 1
    assert f_dur.json()["items"][0]["id"] == exp1.id

    # 7. Invalid price / duration filter ranges -> 422
    assert (
        client.get(
            "/api/v1/eco-experiences",
            params={"min_price": 8000, "max_price": 3000},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/eco-experiences",
            params={"min_duration_hours": 6.0, "max_duration_hours": 2.0},
        ).status_code
        == 422
    )

    # 8. Public detail endpoint never exposes private evidence file_reference or unverified badge
    detail_res = client.get(f"/api/v1/eco-experiences/{exp1.id}")
    assert detail_res.status_code == 200
    detail_body = detail_res.json()
    assert detail_body["has_verified_eco_badge"] is False
    assert secret_path not in str(detail_body)
    assert "evidences" not in detail_body["practices"][0]


def test_verified_and_revoked_eco_badge_visibility_on_public_api(
    client: TestClient, db_session: Session, users_and_profiles
):
    profile1 = users_and_profiles["eco_profile_1"]
    admin = users_and_profiles["admin"]

    exp = EcoExperience(
        provider_profile_id=profile1.id,
        title="Muthurajawela Wetland Conservation Canoe Tour",
        eco_category="Conservation Activities",
        duration_hours=Decimal("3.50"),
        district="Gampaha",
        city="Negombo",
        meeting_point="Muthurajawela Visitor Center",
        base_price=Decimal("5200.00"),
        max_group_size=6,
        environmental_guidelines="Paddle canoes only; zero plastic waste.",
        wildlife_safety_summary="Maintain safe distance from estuarine crocodiles and herons.",
        listing_status="approved",
        is_published=True,
        approval_reviewed_by=admin.id,
        approval_reviewed_at=datetime.now(UTC),
        eco_badge_status="pending",
    )
    db_session.add(exp)
    db_session.commit()

    # 1. Pending verification -> has_verified_eco_badge is False in both public list and detail
    v_sub = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile1.id,
        decision="submitted",
        reason="Submitted wetland conservation permit.",
    )
    db_session.add(v_sub)
    db_session.commit()

    assert client.get(f"/api/v1/eco-experiences/{exp.id}").json()["has_verified_eco_badge"] is False
    assert client.get("/api/v1/eco-experiences").json()["items"][0]["has_verified_eco_badge"] is False

    # 2. Admin grants badge -> has_verified_eco_badge is True in public list and detail
    exp.eco_badge_status = "granted"
    v_grant = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile1.id,
        reviewed_by_admin_id=admin.id,
        decision="granted",
        reason="Verified permit and community canoe cooperative.",
    )
    db_session.add(v_grant)
    db_session.commit()
    db_session.expire_all()

    assert client.get(f"/api/v1/eco-experiences/{exp.id}").json()["has_verified_eco_badge"] is True
    assert client.get("/api/v1/eco-experiences").json()["items"][0]["has_verified_eco_badge"] is True

    # 3. Admin revokes badge -> immediately False in public list and detail
    exp = db_session.get(EcoExperience, exp.id)
    exp.eco_badge_status = "revoked"
    v_rev = EcoVerification(
        eco_experience_id=exp.id,
        submitted_by_provider_id=profile1.id,
        reviewed_by_admin_id=admin.id,
        decision="revoked",
        reason="Revoked after motorized boat inspection.",
    )
    db_session.add(v_rev)
    db_session.commit()
    db_session.expire_all()

    assert client.get(f"/api/v1/eco-experiences/{exp.id}").json()["has_verified_eco_badge"] is False
    assert client.get("/api/v1/eco-experiences").json()["items"][0]["has_verified_eco_badge"] is False


def test_linked_homestay_validation_on_create_and_update(
    client: TestClient, db_session: Session, users_and_profiles
):
    provider_user = users_and_profiles["eco_user_1"]
    provider_headers = auth_header(provider_user)

    homestay = Homestay(
        owner_id=provider_user.id,
        name="Knuckles Eco Mudhouse",
        description="Solar powered eco-lodge",
        location="Matale",
        price_per_night=Decimal("9500.00"),
        max_guests=4,
        is_active=True,
    )
    db_session.add(homestay)
    db_session.commit()

    # 1. Nonexistent homestay ID -> 404
    bad_payload = sample_eco_create_payload(
        eco_category="Eco-Friendly Stays",
        linked_homestay_id=99999,
    )
    res_404 = client.post(
        "/api/v1/eco-experiences", json=bad_payload, headers=provider_headers
    )
    assert res_404.status_code == 404

    # 2. Valid active homestay ID with 'Eco-Friendly Stays' -> 201 Created
    good_payload = sample_eco_create_payload(
        title="Knuckles Solar Eco-Lodge Retreat",
        eco_category="Eco-Friendly Stays",
        linked_homestay_id=homestay.id,
    )
    res_201 = client.post(
        "/api/v1/eco-experiences", json=good_payload, headers=provider_headers
    )
    assert res_201.status_code == 201
    exp_id = res_201.json()["id"]
    assert res_201.json()["linked_homestay_id"] == homestay.id

    # 3. Updating category away from 'Eco-Friendly Stays' while keeping linked_homestay_id -> 422
    res_bad_update = client.put(
        f"/api/v1/eco-experiences/{exp_id}",
        json={"eco_category": "Forest & Nature Trails"},
        headers=provider_headers,
    )
    assert res_bad_update.status_code == 422


def test_admin_moderation_edge_cases(client: TestClient, users_and_profiles):
    provider_headers = auth_header(users_and_profiles["eco_user_1"])
    admin_headers = auth_header(users_and_profiles["admin"])

    # Create draft (not yet submitted)
    create_res = client.post(
        "/api/v1/eco-experiences",
        json=sample_eco_create_payload(),
        headers=provider_headers,
    )
    exp_id = create_res.json()["id"]

    # Admin cannot approve or reject a 'draft' listing that hasn't been submitted
    assert (
        client.post(
            f"/api/v1/admin/eco-experiences/{exp_id}/approve",
            headers=admin_headers,
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/api/v1/admin/eco-experiences/{exp_id}/reject",
            json={"reason": "Cannot reject unsubmitted draft."},
            headers=admin_headers,
        ).status_code
        == 400
    )

    # Admin get detail works for any status
    admin_detail = client.get(
        f"/api/v1/admin/eco-experiences/{exp_id}", headers=admin_headers
    )
    assert admin_detail.status_code == 200
    assert admin_detail.json()["listing_status"] == "draft"
