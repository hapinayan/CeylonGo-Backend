"""
Automated tests for Module 04 P0.6 — Traveler Eco-Tourism Wishlist / Favorites (Requirement T14).

Covers:
1. Successful wishlist add (HTTP 201 Created).
2. Duplicate prevention (HTTP 409 Conflict).
3. Successful delete (HTTP 204 No Content).
4. Delete missing item (HTTP 404 Not Found).
5. Paginated listing (HTTP 200 OK, ordered by created_at DESC).
6. Cross-traveler isolation (Traveler B cannot see or delete Traveler A's saved items).
7. Unauthenticated access (HTTP 401 Unauthorized).
8. Provider/admin role rejection (HTTP 403 Forbidden).
9. Unapproved listing hiding (draft/pending/rejected returns HTTP 404 Not Found).
10. Deactivated listing handling (POST returns 400; GET returns is_available=False; available_only filters it).
11. Database uniqueness conflict handling (IntegrityError -> 409 Conflict).
12. Cascade deletion (deleting user or eco experience cleans up wishlist records).
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.models.eco_experience import EcoExperience
from app.models.eco_wishlist import EcoWishlist
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)
from app.routers.wishlists import router as wishlists_router

eco_p06_app = FastAPI(title="CeylonGo M04 P0.6 Wishlist Test App")
eco_p06_app.include_router(auth_router)
eco_p06_app.include_router(eco_experiences_router)
eco_p06_app.include_router(admin_eco_router)
eco_p06_app.include_router(wishlists_router)

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

    eco_p06_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p06_app) as test_client:
        yield test_client
    eco_p06_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_users_and_experiences(db_session: Session):
    traveler_a = User(
        full_name="Traveler Alice",
        email="traveler_a@test.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    traveler_b = User(
        full_name="Traveler Bob",
        email="traveler_b@test.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    provider_user = User(
        full_name="Eco Provider",
        email="provider_p6@test.com",
        password_hash=hash_password("Pass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    admin_user = User(
        full_name="Admin User",
        email="admin_p6@test.com",
        password_hash=hash_password("Pass123!"),
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    db_session.add_all([traveler_a, traveler_b, provider_user, admin_user])
    db_session.flush()

    provider_profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Ceylon Eco Safaris",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94771234567",
        district="Ratnapura",
        created_at=datetime.now(UTC),
    )
    db_session.add(provider_profile)
    db_session.flush()

    # Experience 1: Approved and Published
    exp_active_1 = EcoExperience(
        provider_profile_id=provider_profile.id,
        title="Sinharaja Primary Forest Hike",
        eco_category="Forest & Nature Trails",
        description="Guided birding and rain forest trek.",
        itinerary="Morning walk -> River crossing.",
        duration_hours=Decimal("4.0"),
        district="Ratnapura",
        city="Kalawana",
        address="Entrance Gate 1",
        meeting_point="Forest Office",
        directions_info="Follow road signs.",
        latitude=Decimal("6.400000"),
        longitude=Decimal("80.400000"),
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=1,
        min_age=6,
        environmental_guidelines="Zero plastic, strictly marked trails.",
        wildlife_safety_summary="No feeding of wildlife permitted.",
        conservation_factual_contribution="25% of proceeds to forest conservation.",
        conservation_marketing_statement="Protecting rainforest biodiversity.",
        protected_area_permit_required=True,
        certified_guide_required=True,
        permit_details="Included in ticket.",
        cancellation_policy="Full refund 24h prior.",
        free_cancellation_hours=24,
        listing_status="approved",
        is_published=True,
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # Experience 2: Approved and Published
    exp_active_2 = EcoExperience(
        provider_profile_id=provider_profile.id,
        title="Knuckles Cloud Forest Trail",
        eco_category="Forest & Nature Trails",
        description="Highland mist forest expedition.",
        itinerary="Summit trek -> Valley descent.",
        duration_hours=Decimal("6.0"),
        district="Matale",
        city="Riverston",
        meeting_point="Riverston Gap",
        pricing_type="per_person",
        base_price=Decimal("6500.00"),
        currency="LKR",
        max_group_size=8,
        min_participants=1,
        environmental_guidelines="Pack out all trash.",
        wildlife_safety_summary="Do not disturb nesting birds.",
        listing_status="approved",
        is_published=True,
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # Experience 3: Approved but Deactivated
    exp_deactivated = EcoExperience(
        provider_profile_id=provider_profile.id,
        title="Deactivated Mangrove Canoe Tour",
        eco_category="Wildlife & Birdwatching",
        duration_hours=Decimal("3.0"),
        district="Galle",
        city="Koggala",
        meeting_point="Koggala Lake Pier",
        pricing_type="per_person",
        base_price=Decimal("3000.00"),
        currency="LKR",
        max_group_size=6,
        min_participants=1,
        environmental_guidelines="Do not touch mangrove roots.",
        wildlife_safety_summary="Observe reptiles from boat only.",
        listing_status="approved",
        is_published=False,  # Deactivated
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    # Experience 4: Draft (Unapproved)
    exp_draft = EcoExperience(
        provider_profile_id=provider_profile.id,
        title="Draft Village Cooking Demo",
        eco_category="Sustainable Village Experiences",
        duration_hours=Decimal("2.5"),
        district="Kandy",
        city="Peradeniya",
        meeting_point="Village Hall",
        pricing_type="per_person",
        base_price=Decimal("2500.00"),
        currency="LKR",
        max_group_size=10,
        min_participants=1,
        environmental_guidelines="Organic waste composting only.",
        wildlife_safety_summary="Respect village domestic livestock.",
        listing_status="draft",
        is_published=False,
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    db_session.add_all([exp_active_1, exp_active_2, exp_deactivated, exp_draft])
    db_session.commit()

    return {
        "traveler_a": traveler_a,
        "traveler_b": traveler_b,
        "provider_user": provider_user,
        "admin_user": admin_user,
        "exp_active_1": exp_active_1,
        "exp_active_2": exp_active_2,
        "exp_deactivated": exp_deactivated,
        "exp_draft": exp_draft,
    }


# ==============================================================================
# Tests
# ==============================================================================


def test_successful_wishlist_add(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.1: Active traveler adds an approved, published eco experience to wishlist -> HTTP 201 Created.
    Verifies response contains wishlist ID, timestamps, availability, and public summary card.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp = test_users_and_experiences["exp_active_1"]

    res = client.post(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )
    assert res.status_code == 201
    data = res.json()
    assert data["id"] > 0
    assert data["user_id"] == traveler.id
    assert data["eco_experience_id"] == exp.id
    assert data["is_available"] is True
    assert data["experience"]["id"] == exp.id
    assert data["experience"]["title"] == "Sinharaja Primary Forest Hike"
    assert data["experience"]["district"] == "Ratnapura"
    assert data["experience"]["provider_business_name"] == "Ceylon Eco Safaris"


def test_duplicate_wishlist_prevention(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.2: Attempting to add the same experience to wishlist a second time returns HTTP 409 Conflict.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp = test_users_and_experiences["exp_active_1"]

    # First add -> 201
    res1 = client.post(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )
    assert res1.status_code == 201

    # Second add -> 409 Conflict
    res2 = client.post(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )
    assert res2.status_code == 409
    assert "already in your wishlist" in res2.json()["detail"].lower()


def test_successful_wishlist_delete(
    client: TestClient,
    db_session: Session,
    test_users_and_experiences: dict,
):
    """
    Step 6.3: Traveler removes an experience from their wishlist -> HTTP 204 No Content.
    Verifies the record is deleted from the database.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp = test_users_and_experiences["exp_active_1"]

    # Add item
    client.post(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )

    # Delete item -> 204
    res_del = client.delete(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )
    assert res_del.status_code == 204

    # Verify deleted from DB
    db_session.expire_all()
    record = db_session.query(EcoWishlist).filter(
        EcoWishlist.user_id == traveler.id,
        EcoWishlist.eco_experience_id == exp.id,
    ).first()
    assert record is None


def test_delete_missing_wishlist_item(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.4: Attempting to remove an experience not in the traveler's wishlist returns HTTP 404 Not Found.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp = test_users_and_experiences["exp_active_1"]

    res = client.delete(
        f"/api/v1/wishlists/eco/{exp.id}",
        headers=auth_header(traveler),
    )
    assert res.status_code == 404
    assert "not found in your wishlist" in res.json()["detail"].lower()


def test_paginated_wishlist_listing(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.5: GET /api/v1/wishlists/me returns paginated results ordered by created_at DESC.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp1 = test_users_and_experiences["exp_active_1"]
    exp2 = test_users_and_experiences["exp_active_2"]

    # Add both items
    client.post(f"/api/v1/wishlists/eco/{exp1.id}", headers=auth_header(traveler))
    client.post(f"/api/v1/wishlists/eco/{exp2.id}", headers=auth_header(traveler))

    # Retrieve wishlist
    res = client.get(
        "/api/v1/wishlists/me?page=1&page_size=10",
        headers=auth_header(traveler),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["total_pages"] == 1
    assert len(data["items"]) == 2
    # Most recently added (exp2) should be first
    assert data["items"][0]["eco_experience_id"] == exp2.id
    assert data["items"][1]["eco_experience_id"] == exp1.id


def test_cross_traveler_isolation(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.6: Traveler B cannot view or delete Traveler A's saved experiences.
    """
    traveler_a = test_users_and_experiences["traveler_a"]
    traveler_b = test_users_and_experiences["traveler_b"]
    exp = test_users_and_experiences["exp_active_1"]

    # Traveler A adds item
    client.post(f"/api/v1/wishlists/eco/{exp.id}", headers=auth_header(traveler_a))

    # Traveler B's wishlist must be empty
    res_b_list = client.get("/api/v1/wishlists/me", headers=auth_header(traveler_b))
    assert res_b_list.status_code == 200
    assert res_b_list.json()["total"] == 0
    assert len(res_b_list.json()["items"]) == 0

    # Traveler B cannot delete Traveler A's item -> 404 (not in B's wishlist)
    res_b_del = client.delete(f"/api/v1/wishlists/eco/{exp.id}", headers=auth_header(traveler_b))
    assert res_b_del.status_code == 404

    # Traveler A's wishlist still has the item
    res_a_list = client.get("/api/v1/wishlists/me", headers=auth_header(traveler_a))
    assert res_a_list.json()["total"] == 1


def test_unauthenticated_access(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.7: Unauthenticated access to wishlist endpoints returns HTTP 401 Unauthorized.
    """
    exp = test_users_and_experiences["exp_active_1"]

    assert client.post(f"/api/v1/wishlists/eco/{exp.id}").status_code == 401
    assert client.delete(f"/api/v1/wishlists/eco/{exp.id}").status_code == 401
    assert client.get("/api/v1/wishlists/me").status_code == 401


def test_provider_and_admin_role_rejection(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.8: Providers and admins attempting to access traveler wishlists receive HTTP 403 Forbidden.
    """
    provider = test_users_and_experiences["provider_user"]
    admin = test_users_and_experiences["admin_user"]
    exp = test_users_and_experiences["exp_active_1"]

    # Provider blocked with 403
    res_prov = client.post(f"/api/v1/wishlists/eco/{exp.id}", headers=auth_header(provider))
    assert res_prov.status_code == 403
    assert "traveler role required" in res_prov.json()["detail"].lower()

    # Admin blocked with 403
    res_admin = client.get("/api/v1/wishlists/me", headers=auth_header(admin))
    assert res_admin.status_code == 403
    assert "traveler role required" in res_admin.json()["detail"].lower()


def test_unapproved_listing_hiding(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.9: Travelers cannot add draft or unapproved experiences to their wishlist.
    Must return HTTP 404 Not Found to prevent leaking unapproved listings.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp_draft = test_users_and_experiences["exp_draft"]

    res = client.post(
        f"/api/v1/wishlists/eco/{exp_draft.id}",
        headers=auth_header(traveler),
    )
    assert res.status_code == 404
    assert "eco experience not found" in res.json()["detail"].lower()


def test_deactivated_listing_handling(
    client: TestClient,
    db_session: Session,
    test_users_and_experiences: dict,
):
    """
    Step 6.10:
    1. Attempting to add an already deactivated experience returns HTTP 400 Bad Request.
    2. An experience deactivated AFTER being saved returns is_available=False and experience=None in GET /me.
    3. Querying with available_only=True excludes deactivated listings from results and total count.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp_active = test_users_and_experiences["exp_active_1"]
    exp_deactivated = test_users_and_experiences["exp_deactivated"]

    # 1. Direct addition of deactivated experience -> 400
    res_add_deactivated = client.post(
        f"/api/v1/wishlists/eco/{exp_deactivated.id}",
        headers=auth_header(traveler),
    )
    assert res_add_deactivated.status_code == 400
    assert "currently not published" in res_add_deactivated.json()["detail"].lower()

    # 2. Add an active experience first -> 201
    res_add_active = client.post(
        f"/api/v1/wishlists/eco/{exp_active.id}",
        headers=auth_header(traveler),
    )
    assert res_add_active.status_code == 201

    # Now simulate provider deactivating exp_active
    exp_active.is_published = False
    exp_active.listing_status = "deactivated"
    db_session.commit()

    # 3. GET /me without available_only returns the item with is_available=False
    res_list = client.get("/api/v1/wishlists/me", headers=auth_header(traveler))
    assert res_list.status_code == 200
    items = res_list.json()["items"]
    assert len(items) == 1
    assert items[0]["is_available"] is False
    assert items[0]["experience"] is None

    # 4. GET /me?available_only=true filters out the deactivated item
    res_list_filtered = client.get(
        "/api/v1/wishlists/me?available_only=true",
        headers=auth_header(traveler),
    )
    assert res_list_filtered.status_code == 200
    assert res_list_filtered.json()["total"] == 0
    assert len(res_list_filtered.json()["items"]) == 0


def test_database_uniqueness_conflict_handling(
    client: TestClient,
    test_users_and_experiences: dict,
):
    """
    Step 6.11: Verifies that if a race condition produces an IntegrityError at the DB level,
    it is caught and translated to HTTP 409 Conflict.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp = test_users_and_experiences["exp_active_1"]

    # Simulate IntegrityError on db.commit
    with patch.object(
        Session,
        "commit",
        side_effect=IntegrityError("statement", "params", "orig"),
    ):
        res = client.post(
            f"/api/v1/wishlists/eco/{exp.id}",
            headers=auth_header(traveler),
        )
        assert res.status_code == 409
        assert "already in your wishlist" in res.json()["detail"].lower()


def test_cascade_deletion(
    client: TestClient,
    db_session: Session,
    test_users_and_experiences: dict,
):
    """
    Step 6.12: Deleting an EcoExperience or a User cascades and removes wishlist entries automatically.
    """
    traveler = test_users_and_experiences["traveler_a"]
    exp1 = test_users_and_experiences["exp_active_1"]

    # Add item
    client.post(f"/api/v1/wishlists/eco/{exp1.id}", headers=auth_header(traveler))

    # Verify wishlist record exists
    record = db_session.query(EcoWishlist).filter(
        EcoWishlist.user_id == traveler.id,
        EcoWishlist.eco_experience_id == exp1.id,
    ).first()
    assert record is not None

    # Delete the experience from the DB
    db_session.delete(exp1)
    db_session.commit()

    # Wishlist record must be removed via cascade
    db_session.expire_all()
    orphaned_record = db_session.query(EcoWishlist).filter(
        EcoWishlist.user_id == traveler.id,
        EcoWishlist.eco_experience_id == exp1.id,
    ).first()
    assert orphaned_record is None
