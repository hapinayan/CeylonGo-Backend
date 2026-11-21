"""
Automated Phase 4 Tests — Travel Community & Social Feed
(Public Profiles, Follow/Unfollow, Followers/Following Lists, Follow Notifications,
Approved Provider Listing Eligibility & Private Provider Engagement Metrics).

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
from app.models.shared import ListingReference, Notification
from app.models.tour_guide import TourGuide
from app.models.user import Base, User
from app.services import community_engagement, community_posts, community_profiles

COMMUNITY_BASE = "/api/v1/community"


# =============================================================================
# ISOLATED FIXTURES
# =============================================================================

@pytest.fixture
def db_session():
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

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


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
def actors(db_session: Session) -> dict:
    t1 = User(
        full_name="Ishara Perera",
        email="ishara.private@p4.example",
        role="traveler",
        is_active=True,
    )
    t2 = User(
        full_name="Malith Fernando",
        email="malith.private@p4.example",
        role="traveler",
        is_active=True,
    )
    inactive_user = User(
        full_name="Deactivated User",
        email="inactive@p4.example",
        role="traveler",
        is_active=False,
    )
    prov1_user = User(
        full_name="Chamil Jayasuriya",
        email="chamil.prov@p4.example",
        role="provider",
        is_active=True,
    )
    prov2_user = User(
        full_name="Nadeesha Silva",
        email="nadeesha.prov@p4.example",
        role="provider",
        is_active=True,
    )
    pending_prov_user = User(
        full_name="Unapproved Host",
        email="pending.prov@p4.example",
        role="provider",
        is_active=True,
    )
    admin = User(
        full_name="Admin User",
        email="admin.p4@p4.example",
        role="admin",
        is_active=True,
    )
    db_session.add_all(
        [t1, t2, inactive_user, prov1_user, prov2_user, pending_prov_user, admin]
    )
    db_session.flush()

    prov1 = ProviderProfile(
        user_id=prov1_user.id,
        business_name="Kandy Heritage Collective",
        provider_type="homestay",
        phone="0771230001",
        district="Kandy",
        nic_number="198512340001",
        approval_status="approved",
    )
    prov2 = ProviderProfile(
        user_id=prov2_user.id,
        business_name="Galle Coastal Crafts",
        provider_type="artisan",
        phone="0771230002",
        district="Galle",
        nic_number="198612340002",
        approval_status="approved",
    )
    pending_prov = ProviderProfile(
        user_id=pending_prov_user.id,
        business_name="Secret Pending Villa",
        provider_type="homestay",
        phone="0771230003",
        district="Colombo",
        nic_number="198712340003",
        approval_status="pending",
    )
    db_session.add_all([prov1, prov2, pending_prov])
    db_session.commit()

    return {
        "t1": t1,
        "t2": t2,
        "inactive": inactive_user,
        "prov1_user": prov1_user,
        "prov1": prov1,
        "prov2_user": prov2_user,
        "prov2": prov2,
        "pending_prov_user": pending_prov_user,
        "pending_prov": pending_prov,
        "admin": admin,
    }


# =============================================================================
# 1. PUBLIC PROFILES, PRIVACY & APPROVED PROVIDER BADGES
# =============================================================================

def test_public_profiles_privacy_and_approved_provider_badges(
    client: TestClient, actors: dict
):
    prov1_u = actors["prov1_user"]
    pending_u = actors["pending_prov_user"]
    t1 = actors["t1"]

    # Approved provider creates 1 published post and 1 unpublished draft
    r_pub = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "Kandy Esala mornings #Kandy"},
    )
    assert r_pub.status_code == 201
    r_draft = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "Private draft", "is_published": False},
    )
    assert r_draft.status_code == 201

    # Public profile for approved provider exposes safe badge, excludes draft & PII
    r_prof = client.get(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}")
    assert r_prof.status_code == 200, r_prof.text
    body = r_prof.json()
    assert body["id"] == prov1_u.id
    assert body["full_name"] == "Chamil Jayasuriya"
    assert body["is_verified_provider"] is True
    assert body["business_name"] == "Kandy Heritage Collective"
    assert body["provider_type"] == "homestay"
    assert body["district"] == "Kandy"
    assert body["posts_count"] == 1
    assert body["posts"]["total"] == 1
    assert body["is_following"] is False
    for forbidden in ("email", "phone", "nic_number", "password_hash"):
        assert forbidden not in body

    # Pending (unapproved) provider CANNOT impersonate an approved business
    r_pend_prof = client.get(f"{COMMUNITY_BASE}/profiles/{pending_u.id}")
    assert r_pend_prof.status_code == 200
    pend_body = r_pend_prof.json()
    assert pend_body["is_verified_provider"] is False
    assert pend_body["business_name"] is None
    assert pend_body["provider_type"] is None

    # Inactive or nonexistent user returns 404
    assert client.get(f"{COMMUNITY_BASE}/profiles/{actors['inactive'].id}").status_code == 404
    assert client.get(f"{COMMUNITY_BASE}/profiles/999999").status_code == 404

    # Pending provider creating an ordinary post still does NOT get verified badge
    r_pend_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(pending_u),
        json={"content": "Trying to look verified"},
    )
    assert r_pend_post.status_code == 201
    assert r_pend_post.json()["author"]["is_verified_provider"] is False
    assert r_pend_post.json()["author"]["business_name"] is None

    # Authenticated viewer sees is_following accurately
    assert client.get(
        f"{COMMUNITY_BASE}/profiles/{prov1_u.id}", headers=bearer(t1)
    ).json()["is_following"] is False


# =============================================================================
# 2. FOLLOW / UNFOLLOW, IDEMPOTENCY, SELF-FOLLOW, NOTIFICATIONS & LISTS
# =============================================================================

def test_follow_unfollow_idempotency_notifications_and_paginated_lists(
    client: TestClient, db_session: Session, actors: dict
):
    t1, t2, prov1_u, inactive = (
        actors["t1"],
        actors["t2"],
        actors["prov1_user"],
        actors["inactive"],
    )

    # Unauthenticated follow/unfollow rejected
    assert client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow").status_code in (401, 403)
    assert client.delete(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow").status_code in (401, 403)

    # Self-follow rejected (400) with zero self-notification
    assert client.put(f"{COMMUNITY_BASE}/profiles/{t1.id}/follow", headers=bearer(t1)).status_code == 400
    assert client.delete(f"{COMMUNITY_BASE}/profiles/{t1.id}/follow", headers=bearer(t1)).status_code == 400
    assert db_session.query(Notification).filter_by(user_id=t1.id).count() == 0

    # Inactive or nonexistent target rejected (404)
    assert client.put(f"{COMMUNITY_BASE}/profiles/{inactive.id}/follow", headers=bearer(t1)).status_code == 404
    assert client.put(f"{COMMUNITY_BASE}/profiles/999999/follow", headers=bearer(t1)).status_code == 404

    # t1 follows prov1_u (new follow -> notification sent)
    r_f1 = client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))
    assert r_f1.status_code == 200, r_f1.text
    assert r_f1.json() == {
        "target_user_id": prov1_u.id,
        "following": True,
        "followers_count": 1,
        "following_count": 1,
    }

    # Retry is idempotent -> counts unchanged, NO duplicate notification
    r_f1_dup = client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))
    assert r_f1_dup.status_code == 200
    assert r_f1_dup.json()["followers_count"] == 1
    notifs = db_session.query(Notification).filter_by(user_id=prov1_u.id).all()
    assert len(notifs) == 1
    assert notifs[0].notification_type == "community_new_follower"
    assert notifs[0].link_url == f"/community/profiles/{t1.id}"

    # t2 also follows prov1_u -> followers_count=2 and 2nd distinct notification
    assert client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t2)).json()["followers_count"] == 2
    assert db_session.query(Notification).filter_by(user_id=prov1_u.id).count() == 2

    # Profile reflects is_following=True for t1 and followers_count=2
    prof_view = client.get(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}", headers=bearer(t1)).json()
    assert prof_view["is_following"] is True
    assert prof_view["followers_count"] == 2

    # Paginated followers list (page_size=1) excludes private fields and respects ordering
    r_fol_p1 = client.get(
        f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/followers",
        params={"page": 1, "page_size": 1},
    )
    assert r_fol_p1.status_code == 200
    d1 = r_fol_p1.json()
    assert d1["total"] == 2
    assert d1["list_type"] == "followers"
    assert d1["has_next"] is True
    assert d1["items"][0]["id"] == t2.id  # Most recent follower first
    assert "email" not in d1["items"][0]

    r_fol_p2 = client.get(
        f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/followers",
        params={"page": 2, "page_size": 1},
    )
    assert r_fol_p2.status_code == 200
    assert r_fol_p2.json()["items"][0]["id"] == t1.id
    assert r_fol_p2.json()["has_previous"] is True

    # Following list for t1 shows prov1_u with approved provider badge
    r_following = client.get(
        f"{COMMUNITY_BASE}/profiles/{t1.id}/following",
        headers=bearer(t2),
    )
    assert r_following.status_code == 200
    assert r_following.json()["total"] == 1
    assert r_following.json()["items"][0]["id"] == prov1_u.id
    assert r_following.json()["items"][0]["is_verified_provider"] is True
    assert r_following.json()["items"][0]["is_following"] is True  # t2 also follows prov1_u

    # Unfollow is idempotent
    r_un = client.delete(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))
    assert r_un.status_code == 200
    assert r_un.json() == {
        "target_user_id": prov1_u.id,
        "following": False,
        "followers_count": 1,
        "following_count": 0,
    }
    r_un_dup = client.delete(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))
    assert r_un_dup.status_code == 200
    assert r_un_dup.json()["followers_count"] == 1


# =============================================================================
# 3. PROVIDER AUTHORIZATION, ELIGIBLE LISTINGS & ENGAGEMENT ISOLATION
# =============================================================================

def test_provider_authorization_and_eligible_listings_across_modules(
    client: TestClient, db_session: Session, actors: dict
):
    prov1, prov1_u = actors["prov1"], actors["prov1_user"]
    prov2, prov2_u = actors["prov2"], actors["prov2_user"]

    # Non-providers, unapproved providers, and anonymous rejected from provider endpoints
    for path in ("/provider/eligible-listings", "/provider/engagement"):
        assert client.get(f"{COMMUNITY_BASE}{path}").status_code in (401, 403)
        assert client.get(f"{COMMUNITY_BASE}{path}", headers=bearer(actors["t1"])).status_code == 403
        assert client.get(f"{COMMUNITY_BASE}{path}", headers=bearer(actors["admin"])).status_code == 403
        assert client.get(f"{COMMUNITY_BASE}{path}", headers=bearer(actors["pending_prov_user"])).status_code == 403

    # Seed mixed approved, unapproved, inactive, and foreign-owned listings across modules
    hs_ok = Homestay(
        name="Approved Kandy Villa",
        location="Kandy",
        district="Kandy",
        city="Kandy",
        price_per_night=Decimal("12000.00"),
        max_guests=4,
        owner_id=prov1_u.id,
        provider_profile_id=prov1.id,
        approval_status="approved",
        is_active=True,
    )
    hs_pending = Homestay(
        name="Pending Draft Villa",
        location="Kandy",
        district="Kandy",
        price_per_night=Decimal("10000.00"),
        max_guests=2,
        owner_id=prov1_u.id,
        provider_profile_id=prov1.id,
        approval_status="pending",
        is_active=True,
    )
    hs_foreign = Homestay(
        name="Foreign Provider Villa",
        location="Galle",
        district="Galle",
        price_per_night=Decimal("15000.00"),
        max_guests=4,
        owner_id=prov2_u.id,
        provider_profile_id=prov2.id,
        approval_status="approved",
        is_active=True,
    )
    guide_ok = TourGuide(
        provider_profile_id=prov1.id,
        bio="Licensed heritage guide",
        daily_rate=Decimal("15000.00"),
        base_city="Kandy",
        base_district="Kandy",
        approval_status="approved",
        is_active=True,
    )
    act_ok = Activity(
        provider_profile_id=prov1.id,
        title="Knuckles Sunrise Trek",
        description="Guided mountain trek",
        category="adventure",
        district="Kandy",
        city="Kandy",
        meeting_point="Kandy Clock Tower",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("4500.00"),
        approval_status="approved",
        is_active=True,
    )
    eco_ok = EcoExperience(
        provider_profile_id=prov1.id,
        title="Knuckles Cloud Forest Walk",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("4.00"),
        district="Kandy",
        city="Kandy",
        meeting_point="Riverston Gate",
        base_price=Decimal("4000.00"),
        max_group_size=8,
        environmental_guidelines="Leave no trace",
        wildlife_safety_summary="Maintain distance",
        listing_status="approved",
        is_published=True,
    )
    eco_draft = EcoExperience(
        provider_profile_id=prov1.id,
        title="Unpublished Eco Walk",
        eco_category="Forest & Nature Trails",
        duration_hours=Decimal("3.00"),
        district="Kandy",
        city="Kandy",
        meeting_point="Park Gate",
        base_price=Decimal("3000.00"),
        max_group_size=10,
        environmental_guidelines="Leave no trace",
        wildlife_safety_summary="Maintain distance",
        listing_status="approved",
        is_published=False,  # NOT published -> must be excluded!
    )
    artisan_unverified = ArtisanListing(
        provider_profile_id=prov2.id,
        title="Unverified Mask Shop",
        craft_category="wood_carving",
        district="Galle",
        city="Ambalangoda",
        approval_status="approved",
        is_active=True,
        is_verified=False,  # public_artisan() requires is_verified == True!
    )
    artisan_ok = ArtisanListing(
        provider_profile_id=prov2.id,
        title="Verified Lace Atelier",
        craft_category="handloom",
        district="Galle",
        city="Galle",
        approval_status="approved",
        is_active=True,
        is_verified=True,
    )
    vehicle_ok = RentalVehicle(
        provider_id=prov1.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Kandy Eco Tuk",
        price_per_day=Decimal("5000.00"),
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        status="available",
    )
    vehicle_maint = RentalVehicle(
        provider_id=prov1.id,
        vehicle_type="bike",
        vehicle_name="Maintenance Bike",
        price_per_day=Decimal("2500.00"),
        district="Kandy",
        city="Kandy",
        approval_status="approved",
        status="maintenance",  # public_rental() requires status == 'available'!
    )
    db_session.add_all(
        [
            hs_ok,
            hs_pending,
            hs_foreign,
            guide_ok,
            act_ok,
            eco_ok,
            eco_draft,
            artisan_unverified,
            artisan_ok,
            vehicle_ok,
            vehicle_maint,
        ]
    )
    db_session.flush()

    hs_ref = ListingReference(
        module_type="homestay",
        target_id=hs_ok.id,
        provider_profile_id=prov1.id,
        title=hs_ok.name,
        district="Kandy",
        city="Kandy",
        is_active=True,
    )
    db_session.add(hs_ref)
    db_session.commit()

    # prov1 sees ONLY prov1's approved/publicly eligible listings (5 total across 5 modules)
    r_p1 = client.get(f"{COMMUNITY_BASE}/provider/eligible-listings", headers=bearer(prov1_u))
    assert r_p1.status_code == 200, r_p1.text
    p1_data = r_p1.json()
    assert p1_data["provider_profile_id"] == prov1.id
    assert p1_data["total"] == 5
    returned_keys = {(i["module_type"], i["target_id"]) for i in p1_data["items"]}
    assert returned_keys == {
        ("homestay", hs_ok.id),
        ("tour_guide", guide_ok.id),
        ("activity", act_ok.id),
        ("eco_experience", eco_ok.id),
        ("rental_vehicle", vehicle_ok.id),
    }
    hs_item = next(i for i in p1_data["items"] if i["module_type"] == "homestay")
    assert hs_item["listing_reference_id"] == hs_ref.id
    assert "POST/PATCH /api/v1/community/posts" in p1_data["promotional_linking_note"]

    # prov2 sees ONLY prov2's approved homestay + verified artisan listing (unverified artisan excluded!)
    r_p2 = client.get(f"{COMMUNITY_BASE}/provider/eligible-listings", headers=bearer(prov2_u))
    assert r_p2.status_code == 200
    p2_keys = {(i["module_type"], i["target_id"]) for i in r_p2.json()["items"]}
    assert p2_keys == {("homestay", hs_foreign.id), ("artisan", artisan_ok.id)}


def test_provider_engagement_metrics_and_strict_provider_isolation(
    client: TestClient, actors: dict
):
    prov1_u, prov2_u, t1, t2 = (
        actors["prov1_user"],
        actors["prov2_user"],
        actors["t1"],
        actors["t2"],
    )

    # prov1 creates 1 published post + 1 draft; prov2 creates 1 published post
    p1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "Special Kandy homestay showcase #Kandy"},
    ).json()
    client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov1_u),
        json={"content": "Unpublished promo draft", "is_published": False},
    )
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov2_u),
        json={"content": "Galle handloom showcase #Galle"},
    ).json()

    # Engagement on prov1's post: 2 likes, 1 comment, 1 save, 1 follower
    client.put(f"{COMMUNITY_BASE}/posts/{p1['id']}/like", headers=bearer(t1))
    client.put(f"{COMMUNITY_BASE}/posts/{p1['id']}/like", headers=bearer(t2))
    client.post(
        f"{COMMUNITY_BASE}/posts/{p1['id']}/comments",
        headers=bearer(t1),
        json={"content": "Looks wonderful!"},
    )
    client.put(f"{COMMUNITY_BASE}/posts/{p1['id']}/save", headers=bearer(t1))
    client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))

    # Engagement on prov2's post: 1 like only
    client.put(f"{COMMUNITY_BASE}/posts/{p2['id']}/like", headers=bearer(t1))

    # prov1 sees ONLY prov1's metrics (zero leakage of prov2's metrics or who saved p1)
    r_eng1 = client.get(f"{COMMUNITY_BASE}/provider/engagement", headers=bearer(prov1_u))
    assert r_eng1.status_code == 200, r_eng1.text
    e1 = r_eng1.json()
    assert e1["total_posts"] == 2
    assert e1["published_posts"] == 1
    assert e1["unpublished_posts"] == 1
    assert e1["total_likes"] == 2
    assert e1["total_comments"] == 1
    assert e1["total_saves"] == 1
    assert e1["followers_count"] == 1
    pub_row = next(r for r in e1["posts"] if r["post_id"] == p1["id"])
    assert pub_row["total_engagements"] == 4  # 2 likes + 1 comment + 1 save

    # prov2 sees ONLY prov2's isolated metrics
    r_eng2 = client.get(f"{COMMUNITY_BASE}/provider/engagement", headers=bearer(prov2_u))
    assert r_eng2.status_code == 200
    e2 = r_eng2.json()
    assert e2["total_posts"] == 1
    assert e2["total_likes"] == 1
    assert e2["total_comments"] == 0
    assert e2["total_saves"] == 0
    assert e2["followers_count"] == 0
    assert [r["post_id"] for r in e2["posts"]] == [p2["id"]]


# =============================================================================
# 4. FOLLOW RATE LIMITING & ATOMIC ROLLBACK
# =============================================================================

def test_follow_rate_limiting_and_atomic_rollback(
    client: TestClient, db_session: Session, actors: dict, monkeypatch: pytest.MonkeyPatch
):
    from sqlalchemy.exc import OperationalError

    t1, prov1_u = actors["t1"], actors["prov1_user"]

    # Atomic rollback if commit fails after follow insert + notification insert
    def _fail_commit(_db):
        _db.rollback()
        raise OperationalError("COMMIT", {}, Exception("simulated db failure"))

    monkeypatch.setattr(community_profiles, "_commit", _fail_commit)
    with pytest.raises(OperationalError):
        client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))

    assert db_session.query(CommunityFollow).count() == 0
    assert db_session.query(Notification).count() == 0

    # Restore commit, reset rate-limiter window, and verify HTTP 429 enforcement
    monkeypatch.undo()
    community_profiles.reset_community_follow_limiter()
    monkeypatch.setattr(community_profiles.community_follow_limiter, "max_requests", 2)
    assert client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1)).status_code == 200
    assert client.delete(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1)).status_code == 200
    r_429 = client.put(f"{COMMUNITY_BASE}/profiles/{prov1_u.id}/follow", headers=bearer(t1))
    assert r_429.status_code == 429
