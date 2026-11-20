"""
Automated Step 5B Tests — Travel Community & Social Feed
(Provider Promotional Posts, Secure Listing Linking Across All 6 Modules,
Idempotent ListingReference Creation, Live Revocation Suppression, Promotional Feed
Filtering & N+1 Prevention — TC-PR-01, TC-PR-02, TC-SEC-02).

Runs against an isolated in-memory SQLite database with PRAGMA foreign_keys=ON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.community import (
    CommunityComment,
    CommunityFollow,
    CommunityHashtag,
    CommunityPost,
    CommunityPostHashtag,
    CommunityPostLike,
    CommunityPostMedia,
    CommunityReport,
    CommunitySavedPost,
    CommunityUserProfile,
)
from app.models.eco_experience import EcoExperience
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import AdminAuditLog, ListingReference, Notification
from app.models.tour_guide import TourGuide
from app.models.user import Base, User
from app.services import community_engagement, community_posts, community_profiles

COMMUNITY_BASE = "/api/v1/community"


# =============================================================================
# ISOLATED FIXTURES
# =============================================================================

@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        dbapi_connection.isolation_level = None
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
        )
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _do_begin(conn):
        conn.exec_driver_sql("BEGIN")

    tables = [
        User.__table__,
        ProviderProfile.__table__,
        Notification.__table__,
        AdminAuditLog.__table__,
        ListingReference.__table__,
        Homestay.__table__,
        TourGuide.__table__,
        Activity.__table__,
        EcoExperience.__table__,
        ArtisanListing.__table__,
        RentalLocation.__table__,
        RentalVehicle.__table__,
        CommunityUserProfile.__table__,
        CommunityPost.__table__,
        CommunityPostMedia.__table__,
        CommunityPostLike.__table__,
        CommunityComment.__table__,
        CommunitySavedPost.__table__,
        CommunityFollow.__table__,
        CommunityReport.__table__,
        CommunityHashtag.__table__,
        CommunityPostHashtag.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine):
    SessionLocal = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session):
    community_posts.reset_community_post_limiters()
    community_profiles.reset_community_follow_limiter()
    community_engagement.reset_community_engagement_limiters()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        community_posts.reset_community_post_limiters()
        community_profiles.reset_community_follow_limiter()
        community_engagement.reset_community_engagement_limiters()


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def actors_and_listings(db_session: Session) -> dict:
    traveler = User(full_name="Sahan Traveler", email="sahan.5b@example.com", role="traveler", is_active=True)
    prov1_u = User(full_name="Amila Host", email="amila.5b@example.com", role="provider", is_active=True)
    prov2_u = User(full_name="Nadeeka Craft", email="nadeeka.5b@example.com", role="provider", is_active=True)
    pending_u = User(full_name="Pending Host", email="pending.5b@example.com", role="provider", is_active=True)
    admin = User(full_name="Admin Mod", email="admin.5b@example.com", role="admin", is_active=True)
    db_session.add_all([traveler, prov1_u, prov2_u, pending_u, admin])
    db_session.flush()

    prov1 = ProviderProfile(
        user_id=prov1_u.id,
        business_name="Ceylon Highlands Collective",
        provider_type="homestay",
        phone="0771000001",
        district="Kandy",
        nic_number="198810000001",
        approval_status="approved",
    )
    prov2 = ProviderProfile(
        user_id=prov2_u.id,
        business_name="Galle Heritage Artisans",
        provider_type="artisan",
        phone="0771000002",
        district="Galle",
        nic_number="198910000002",
        approval_status="approved",
    )
    pending_prof = ProviderProfile(
        user_id=pending_u.id,
        business_name="Pending Villa",
        provider_type="homestay",
        phone="0771000003",
        district="Colombo",
        nic_number="199010000003",
        approval_status="pending",
    )
    db_session.add_all([prov1, prov2, pending_prof])
    db_session.flush()

    # All 6 approved module listings owned by prov1 (no pre-existing ListingReference for 5 of them!)
    hs_ok = Homestay(
        name="Kandy Tea Estate Villa",
        location="Kandy",
        district="Kandy",
        city="Kandy",
        price_per_night=Decimal("15000.00"),
        max_guests=4,
        owner_id=prov1_u.id,
        provider_profile_id=prov1.id,
        approval_status="approved",
        is_active=True,
    )
    hs_pending = Homestay(
        name="Unapproved Draft Villa",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("9000.00"),
        max_guests=2,
        owner_id=prov1_u.id,
        provider_profile_id=prov1.id,
        approval_status="pending",
        is_active=True,
    )
    hs_foreign = Homestay(
        name="Foreign Galle Villa",
        location="Galle",
        district="Galle",
        price_per_night=Decimal("18000.00"),
        max_guests=4,
        owner_id=prov2_u.id,
        provider_profile_id=prov2.id,
        approval_status="approved",
        is_active=True,
    )
    guide_ok = TourGuide(
        provider_profile_id=prov1.id,
        bio="Licensed cultural guide",
        daily_rate=Decimal("12000.00"),
        base_city="Kandy",
        base_district="Kandy",
        approval_status="approved",
        is_active=True,
    )
    act_ok = Activity(
        provider_profile_id=prov1.id,
        title="Hanthana Sunset Hike",
        description="Guided ridge trek",
        category="adventure",
        district="Kandy",
        city="Kandy",
        meeting_point="Kandy Town",
        duration_minutes=180,
        pricing_type="per_person",
        price=Decimal("4000.00"),
        approval_status="approved",
        is_active=True,
    )
    eco_ok = EcoExperience(
        provider_profile_id=prov1.id,
        title="Knuckles Forest Trail",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("4.00"),
        district="Kandy",
        city="Kandy",
        meeting_point="Riverston",
        base_price=Decimal("5000.00"),
        max_group_size=8,
        environmental_guidelines="Leave no trace",
        wildlife_safety_summary="Keep distance",
        listing_status="approved",
        is_published=True,
    )
    artisan_ok = ArtisanListing(
        provider_profile_id=prov1.id,
        title="Dumbara Handloom Studio",
        craft_category="handloom",
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        is_active=True,
        is_verified=True,
    )
    artisan_unverified = ArtisanListing(
        provider_profile_id=prov1.id,
        title="Unverified Craft Stall",
        craft_category="pottery",
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        is_active=True,
        is_verified=False,
    )
    vehicle_ok = RentalVehicle(
        provider_id=prov1.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Kandy Scenic Tuk",
        price_per_day=Decimal("4500.00"),
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        status="available",
    )
    vehicle_maint = RentalVehicle(
        provider_id=prov1.id,
        vehicle_type="bike",
        vehicle_name="Unavailable Bike",
        price_per_day=Decimal("2500.00"),
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        status="maintenance",
    )

    db_session.add_all(
        [
            hs_ok,
            hs_pending,
            hs_foreign,
            guide_ok,
            act_ok,
            eco_ok,
            artisan_ok,
            artisan_unverified,
            vehicle_ok,
            vehicle_maint,
        ]
    )
    db_session.flush()

    # Pre-seed a ListingReference ONLY for hs_ok and hs_foreign (leaving 5 other modules missing!)
    ref_hs_ok = ListingReference(
        module_type="homestay",
        target_id=hs_ok.id,
        provider_profile_id=prov1.id,
        title=hs_ok.name,
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    ref_hs_foreign = ListingReference(
        module_type="homestay",
        target_id=hs_foreign.id,
        provider_profile_id=prov2.id,
        title=hs_foreign.name,
        district="Galle",
        city="Galle",
        is_active=True,
    )
    db_session.add_all([ref_hs_ok, ref_hs_foreign])
    db_session.commit()

    return {
        "traveler": traveler,
        "prov1_u": prov1_u,
        "prov1": prov1,
        "prov2_u": prov2_u,
        "prov2": prov2,
        "pending_u": pending_u,
        "admin": admin,
        "hs_ok": hs_ok,
        "hs_pending": hs_pending,
        "hs_foreign": hs_foreign,
        "guide_ok": guide_ok,
        "act_ok": act_ok,
        "eco_ok": eco_ok,
        "artisan_ok": artisan_ok,
        "artisan_unverified": artisan_unverified,
        "vehicle_ok": vehicle_ok,
        "vehicle_maint": vehicle_maint,
        "ref_hs_ok": ref_hs_ok,
        "ref_hs_foreign": ref_hs_foreign,
    }


# =============================================================================
# 1. AUTHORIZATION & PROMOTIONAL FEED FILTERING (TC-PR-01, TC-SEC-02)
# =============================================================================

def test_provider_promotional_posts_rbac_and_promotional_only_feed(
    client: TestClient, actors_and_listings: dict
):
    traveler = actors_and_listings["traveler"]
    prov1_u = actors_and_listings["prov1_u"]
    pending_u = actors_and_listings["pending_u"]
    admin = actors_and_listings["admin"]
    ref_hs_ok = actors_and_listings["ref_hs_ok"]

    # Travelers, pending providers, and admins CANNOT create promotional posts or link listings
    for unauth_user in (traveler, pending_u, admin):
        assert (
            client.post(
                f"{COMMUNITY_BASE}/posts",
                headers=bearer(unauth_user),
                json={"content": "Unauthorized promo", "is_promotional": True},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"{COMMUNITY_BASE}/posts",
                headers=bearer(unauth_user),
                json={
                    "content": "Unauthorized listing link",
                    "listing_reference_id": ref_hs_ok.id,
                },
            ).status_code
            == 403
        )

    # Ordinary traveler post & ordinary non-promotional provider post still work cleanly
    r_trav = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(traveler),
        json={"content": "Regular traveler story #Kandy"},
    )
    assert r_trav.status_code == 201
    assert r_trav.json()["is_promotional"] is False
    assert r_trav.json()["linked_listing"] is None

    r_prov_reg = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "Regular non-promotional update from host"},
    )
    assert r_prov_reg.status_code == 201
    assert r_prov_reg.json()["is_promotional"] is False

    # Approved provider creates promotional post WITHOUT listing link (general offer)
    r_promo_offer = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "20% monsoon discount this weekend! #Kandy", "is_promotional": True},
    )
    assert r_promo_offer.status_code == 201, r_promo_offer.text
    assert r_promo_offer.json()["is_promotional"] is True
    assert r_promo_offer.json()["linked_listing"] is None

    # Approved provider creates promotional post WITH existing listing_reference_id
    r_promo_linked = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={
            "content": "Book our Kandy Tea Estate Villa #Kandy",
            "listing_reference_id": ref_hs_ok.id,
        },
    )
    assert r_promo_linked.status_code == 201, r_promo_linked.text
    linked_body = r_promo_linked.json()
    assert linked_body["is_promotional"] is True
    assert linked_body["listing_reference_id"] == ref_hs_ok.id
    assert linked_body["linked_listing"]["module_type"] == "homestay"
    assert linked_body["linked_listing"]["public_url"] == f"/homestays/{actors_and_listings['hs_ok'].id}"

    # promotional_only=true returns ONLY the 2 promotional posts from approved providers
    r_promo_feed = client.get(f"{COMMUNITY_BASE}/feed", params={"promotional_only": True})
    assert r_promo_feed.status_code == 200, r_promo_feed.text
    promo_items = r_promo_feed.json()["items"]
    assert r_promo_feed.json()["total"] == 2
    assert {i["id"] for i in promo_items} == {r_promo_offer.json()["id"], linked_body["id"]}
    assert all(i["is_promotional"] is True for i in promo_items)

    # Admin-hidden promotional post disappears from promotional_only feed and cannot be restored by provider
    r_hide = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{linked_body['id']}",
        headers=bearer(admin),
        json={"is_published": False, "reason": "Misleading pricing claim"},
    )
    assert r_hide.status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"promotional_only": True}).json()["total"] == 1
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{linked_body['id']}",
            headers=bearer(prov1_u),
            json={"is_published": True},
        ).status_code
        == 403
    )


# =============================================================================
# 2. OWNERSHIP, APPROVAL REJECTION, ALL 6 MODULES & IDEMPOTENT REFERENCE SYNC
# =============================================================================

def test_all_six_modules_linking_ownership_approval_and_duplicate_ref_prevention(
    client: TestClient, db_session: Session, db_engine, actors_and_listings: dict
):
    prov1_u = actors_and_listings["prov1_u"]
    d = actors_and_listings

    # Reject linking another provider's listing (both by listing_reference_id and by module+target_id)
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={"content": "Foreign ref", "listing_reference_id": d["ref_hs_foreign"].id},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={
                "content": "Foreign pair",
                "listing_module_type": "homestay",
                "listing_target_id": d["hs_foreign"].id,
            },
        ).status_code
        == 403
    )

    # Reject unapproved, unverified, and non-available owned listings (400) & missing IDs (404)
    for mtype, tid in (
        ("homestay", d["hs_pending"].id),
        ("artisan", d["artisan_unverified"].id),
        ("rental_vehicle", d["vehicle_maint"].id),
    ):
        r_bad = client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={
                "content": f"Ineligible {mtype}",
                "listing_module_type": mtype,
                "listing_target_id": tid,
            },
        )
        assert r_bad.status_code == 400, f"Expected 400 for {mtype}: {r_bad.status_code} {r_bad.text}"

    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={"content": "Missing listing", "listing_module_type": "activity", "listing_target_id": 999999},
        ).status_code
        == 404
    )
    # Invalid partial/mixed payload rejected with 422
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={"content": "Partial pair", "listing_module_type": "activity"},
        ).status_code
        == 422
    )

    # Link ALL 6 approved owned modules (5 of them lack ListingReference -> created idempotently!)
    six_targets = [
        ("homestay", d["hs_ok"].id, f"/homestays/{d['hs_ok'].id}"),
        ("tour_guide", d["guide_ok"].id, f"/tour-guides/{d['guide_ok'].id}"),
        ("activity", d["act_ok"].id, f"/activities/{d['act_ok'].id}"),
        ("eco_experience", d["eco_ok"].id, f"/eco-experiences/{d['eco_ok'].id}"),
        ("artisan", d["artisan_ok"].id, f"/artisans/{d['artisan_ok'].id}"),
        ("rental_vehicle", d["vehicle_ok"].id, f"/rentals/vehicles/{d['vehicle_ok'].id}"),
    ]
    created_posts = []
    for mtype, tid, expected_url in six_targets:
        r_ok = client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(prov1_u),
            json={
                "content": f"Showcase {mtype} #Kandy",
                "listing_module_type": mtype,
                "listing_target_id": tid,
            },
        )
        assert r_ok.status_code == 201, f"Failed for {mtype}: {r_ok.text}"
        body = r_ok.json()
        assert body["is_promotional"] is True
        assert body["linked_listing"] is not None
        assert body["linked_listing"]["module_type"] == mtype
        assert body["linked_listing"]["target_id"] == tid
        assert body["linked_listing"]["public_url"] == expected_url
        created_posts.append(body)

    # Duplicate ListingReference prevention: linking the same artisan listing a 2nd time reuses exact same ref
    refs_before = (
        db_session.query(ListingReference)
        .filter_by(module_type="artisan", target_id=d["artisan_ok"].id)
        .all()
    )
    assert len(refs_before) == 1

    r_second_artisan = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={
            "content": "Second showcase of Dumbara Handloom Studio",
            "listing_module_type": "artisan",
            "listing_target_id": d["artisan_ok"].id,
        },
    )
    assert r_second_artisan.status_code == 201
    assert r_second_artisan.json()["listing_reference_id"] == refs_before[0].id
    assert (
        db_session.query(ListingReference)
        .filter_by(module_type="artisan", target_id=d["artisan_ok"].id)
        .count()
        == 1
    )

    # Verify N+1-free batched hydration on GET /feed containing all 7 linked posts across 6 modules
    queries: list[str] = []

    def _capture(_conn, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        r_feed = client.get(f"{COMMUNITY_BASE}/feed", params={"page_size": 20})
        assert r_feed.status_code == 200
        assert r_feed.json()["total"] == 7
        assert all(item["linked_listing"] is not None for item in r_feed.json()["items"])
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    # Bounded query count (count + posts + authors + profiles + media + hashtags + refs + <=6 module batch queries)
    assert len(queries) <= 18, f"Expected batched queries, got {len(queries)}"


# =============================================================================
# 3. LIVE REVOCATION SAFETY (SUPPRESS LINK, PRESERVE HISTORICAL POST)
# =============================================================================

def test_revoked_listing_suppresses_public_link_while_preserving_historical_post(
    client: TestClient, db_session: Session, actors_and_listings: dict
):
    prov1_u = actors_and_listings["prov1_u"]
    artisan_ok = actors_and_listings["artisan_ok"]

    # Provider publishes promotional post linked to verified artisan listing
    r_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={
            "content": "Handcrafted Dumbara textiles live now #Kandy",
            "listing_module_type": "artisan",
            "listing_target_id": artisan_ok.id,
        },
    )
    assert r_post.status_code == 201
    post_id = r_post.json()["id"]
    ref_id = r_post.json()["listing_reference_id"]
    assert ref_id is not None

    # Listing loses verification (or becomes rejected/unpublished) AFTER post creation
    artisan_ok.is_verified = False
    db_session.commit()

    # Historical post STILL appears in public feed, search, and post detail —
    # but public booking/promotional link is SUPPRESSED (None)!
    r_detail = client.get(f"{COMMUNITY_BASE}/posts/{post_id}")
    assert r_detail.status_code == 200
    assert r_detail.json()["content"] == "Handcrafted Dumbara textiles live now #Kandy"
    assert r_detail.json()["linked_listing"] is None
    assert r_detail.json()["listing_reference_id"] is None

    r_feed = client.get(f"{COMMUNITY_BASE}/feed")
    assert r_feed.json()["total"] == 1
    assert r_feed.json()["items"][0]["linked_listing"] is None

    r_search = client.get(f"{COMMUNITY_BASE}/search", params={"q": "Dumbara", "type": "posts"})
    assert r_search.json()["posts_total"] == 1
    assert r_search.json()["posts"][0]["linked_listing"] is None

    # Underlying DB FK remains preserved; if listing is re-verified, link cleanly reappears
    db_session.expire_all()
    post_row = db_session.get(CommunityPost, post_id)
    assert post_row is not None and post_row.listing_reference_id == ref_id

    artisan_row = db_session.get(ArtisanListing, artisan_ok.id)
    assert artisan_row is not None
    artisan_row.is_verified = True
    db_session.commit()

    r_restored = client.get(f"{COMMUNITY_BASE}/posts/{post_id}")
    assert r_restored.status_code == 200
    assert r_restored.json()["linked_listing"] is not None
    assert r_restored.json()["linked_listing"]["target_id"] == artisan_ok.id
