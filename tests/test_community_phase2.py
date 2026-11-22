"""
Automated Phase 2 Tests — Travel Community & Social Feed
(Public Feed, Pagination, Filtering, Search, Trending Hashtags, Sharing & Privacy).

Runs against an isolated in-memory SQLite database with PRAGMA foreign_keys=ON.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
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
from app.models.provider_profile import ProviderProfile
from app.models.shared import ListingReference
from app.models.user import Base, User
from app.services import community_posts

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
        dbapi_connection.create_function(
            "sysutcdatetime",
            0,
            lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
        )
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    tables = [
        User.__table__,
        ProviderProfile.__table__,
        ListingReference.__table__,
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
def client(db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    upload_dir = tmp_path / "community_p2_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COMMUNITY_UPLOAD_DIR", str(upload_dir))
    community_posts.reset_community_post_limiters()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        community_posts.reset_community_post_limiters()


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def _png_bytes() -> bytes:
    img = Image.new("RGB", (32, 32), color=(40, 150, 90))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def seeded(db_session: Session, client: TestClient) -> dict:
    t1 = User(
        full_name="Amaya Perera",
        email="amaya.secret@private.example",
        role="traveler",
        is_active=True,
    )
    t2 = User(
        full_name="Dinesh Jayawardena",
        email="dinesh.secret@private.example",
        role="traveler",
        is_active=True,
    )
    inactive_user = User(
        full_name="Deactivated Traveler",
        email="deactivated@private.example",
        role="traveler",
        is_active=False,
    )
    prov_user = User(
        full_name="Samanthi Fernando",
        email="samanthi.provider@private.example",
        role="provider",
        is_active=True,
    )
    pending_prov_user = User(
        full_name="Pending Provider",
        email="pending.prov@private.example",
        role="provider",
        is_active=True,
    )
    admin = User(
        full_name="System Admin",
        email="admin.tc2@private.example",
        role="admin",
        is_active=True,
    )
    db_session.add_all([t1, t2, inactive_user, prov_user, pending_prov_user, admin])
    db_session.flush()

    approved_prof = ProviderProfile(
        user_id=prov_user.id,
        business_name="Sigiriya Canopy Eco Lodge",
        provider_type="homestay",
        phone="0779998888",
        district="Matale",
        nic_number="198512345678",
        approval_status="approved",
    )
    pending_prof = ProviderProfile(
        user_id=pending_prov_user.id,
        business_name="Unapproved Secret Villa",
        provider_type="homestay",
        phone="0771112222",
        district="Galle",
        nic_number="199012345678",
        approval_status="pending",
    )
    db_session.add_all([approved_prof, pending_prof])
    db_session.commit()

    # Create 4 posts with controlled timestamps for deterministic ordering tests
    base_time = datetime(2026, 11, 20, 10, 0, 0)

    r_p1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={
            "content": "Morning train through Nine Arch Bridge #Ella #SriLanka #Train",
            "location_tag": "Nine Arch Bridge",
            "district": "Badulla",
            "latitude": "6.876800",
            "longitude": "81.060800",
        },
    )
    assert r_p1.status_code == 201
    p1_id = r_p1.json()["id"]

    # Attach media to p1 for share preview verification
    r_m1 = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/media",
        headers=bearer(t1),
        files={"file": ("bridge.png", _png_bytes(), "image/png")},
    )
    assert r_m1.status_code == 201
    m1_url = r_m1.json()["media_url"]

    r_p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(prov_user),
        json={
            "content": "Sunrise mist over Sigiriya Lion Rock #Sigiriya #SriLanka",
            "location_tag": "Sigiriya Rock",
            "district": "Matale",
        },
    )
    assert r_p2.status_code == 201
    p2_id = r_p2.json()["id"]

    r_p3 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t2),
        json={
            "content": "Evening rampart walk in Galle Fort #Galle #SriLanka #Ella",
            "location_tag": "Galle Fort",
            "district": "Galle",
        },
    )
    assert r_p3.status_code == 201
    p3_id = r_p3.json()["id"]

    # Unpublished draft post (must NEVER appear in feed or search)
    r_draft = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={
            "content": "Secret unpublished draft about Sigiriya #HiddenDraft",
            "location_tag": "Secret Cave",
            "district": "Matale",
            "is_published": False,
        },
    )
    assert r_draft.status_code == 201
    draft_id = r_draft.json()["id"]

    # Post from inactive user (must NEVER appear in public feed or search)
    inactive_post = CommunityPost(
        author_id=inactive_user.id,
        content="Inactive user post about Ella",
        district="Badulla",
        is_published=True,
        created_at=base_time + timedelta(hours=5),
    )
    db_session.add(inactive_post)

    # Set exact timestamps: p2 and p3 share same timestamp to test deterministic id DESC tie-break!
    db_session.get(CommunityPost, p1_id).created_at = base_time
    db_session.get(CommunityPost, p2_id).created_at = base_time + timedelta(hours=1)
    db_session.get(CommunityPost, p3_id).created_at = base_time + timedelta(hours=1)
    db_session.get(CommunityPost, draft_id).created_at = base_time + timedelta(hours=2)

    # Seed like & saved bookmark for t1 (and a separate saved bookmark for t2 to verify privacy)
    db_session.add(CommunityPostLike(post_id=p2_id, user_id=t1.id))
    db_session.get(CommunityPost, p2_id).likes_count = 1
    db_session.add(CommunitySavedPost(post_id=p2_id, user_id=t1.id))
    db_session.add(CommunitySavedPost(post_id=p1_id, user_id=t2.id))

    # t1 follows prov_user
    db_session.add(CommunityFollow(follower_id=t1.id, following_id=prov_user.id))
    db_session.commit()

    return {
        "t1": t1,
        "t2": t2,
        "inactive_user": inactive_user,
        "prov_user": prov_user,
        "pending_prov_user": pending_prov_user,
        "admin": admin,
        "p1_id": p1_id,
        "p2_id": p2_id,
        "p3_id": p3_id,
        "draft_id": draft_id,
        "m1_url": m1_url,
    }


# =============================================================================
# 1. PUBLIC FEED, DETERMINISTIC ORDERING, PAGINATION & UNPUBLISHED EXCLUSION
# =============================================================================

def test_public_feed_pagination_deterministic_ordering_and_unpublished_exclusion(
    client: TestClient, seeded: dict
):
    r_all = client.get(f"{COMMUNITY_BASE}/feed")
    assert r_all.status_code == 200, r_all.text
    data = r_all.json()

    # Only the 3 published active-author posts appear (draft and inactive user post excluded)
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 1
    assert data["has_next"] is False
    assert data["has_previous"] is False

    # p3 & p2 share the newest timestamp -> higher id (p3) comes first, then p2, then older p1
    returned_ids = [item["id"] for item in data["items"]]
    assert returned_ids == [seeded["p3_id"], seeded["p2_id"], seeded["p1_id"]]
    assert seeded["draft_id"] not in returned_ids

    # Anonymous viewer always receives is_liked=False and is_saved=False
    for item in data["items"]:
        assert item["is_liked"] is False
        assert item["is_saved"] is False
        assert item["share_metadata"]["is_shareable"] is True
        assert item["share_metadata"]["share_url"] == f"/community/posts/{item['id']}"

    # Pagination page 1 & page 2 with page_size=2
    r_p1 = client.get(f"{COMMUNITY_BASE}/feed", params={"page": 1, "page_size": 2})
    assert r_p1.status_code == 200
    d1 = r_p1.json()
    assert [i["id"] for i in d1["items"]] == [seeded["p3_id"], seeded["p2_id"]]
    assert d1["total"] == 3
    assert d1["total_pages"] == 2
    assert d1["has_next"] is True
    assert d1["has_previous"] is False

    r_p2 = client.get(f"{COMMUNITY_BASE}/feed", params={"page": 2, "page_size": 2})
    assert r_p2.status_code == 200
    d2 = r_p2.json()
    assert [i["id"] for i in d2["items"]] == [seeded["p1_id"]]
    assert d2["has_next"] is False
    assert d2["has_previous"] is True


# =============================================================================
# 2. FEED FILTERS (DISTRICT, AUTHOR, HASHTAG, FOLLOWING_ONLY) & AUTH ENRICHMENT
# =============================================================================

def test_feed_filters_and_private_like_save_enrichment(
    client: TestClient, seeded: dict
):
    # District filter (case-insensitive)
    r_dist = client.get(f"{COMMUNITY_BASE}/feed", params={"district": "matale"})
    assert r_dist.status_code == 200
    assert [i["id"] for i in r_dist.json()["items"]] == [seeded["p2_id"]]

    # Author filter
    r_auth = client.get(f"{COMMUNITY_BASE}/feed", params={"author_id": seeded["t1"].id})
    assert r_auth.status_code == 200
    assert [i["id"] for i in r_auth.json()["items"]] == [seeded["p1_id"]]

    # Hashtag filter (accepts optional leading '#' and mixed case)
    r_tag = client.get(f"{COMMUNITY_BASE}/feed", params={"hashtag": "#ELLA"})
    assert r_tag.status_code == 200
    assert [i["id"] for i in r_tag.json()["items"]] == [seeded["p3_id"], seeded["p1_id"]]

    # following_only requires authentication (401 for anonymous)
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"following_only": "true"}).status_code == 401

    # Authenticated t1 follows prov_user -> only p2 appears
    r_fol = client.get(
        f"{COMMUNITY_BASE}/feed",
        headers=bearer(seeded["t1"]),
        params={"following_only": "true"},
    )
    assert r_fol.status_code == 200
    assert [i["id"] for i in r_fol.json()["items"]] == [seeded["p2_id"]]

    # Private is_liked / is_saved enrichment per authenticated caller
    r_t1 = client.get(f"{COMMUNITY_BASE}/feed", headers=bearer(seeded["t1"])).json()["items"]
    t1_map = {i["id"]: i for i in r_t1}
    assert t1_map[seeded["p2_id"]]["is_liked"] is True
    assert t1_map[seeded["p2_id"]]["is_saved"] is True
    assert t1_map[seeded["p1_id"]]["is_saved"] is False  # t2's bookmark is NEVER leaked to t1!

    r_t2 = client.get(f"{COMMUNITY_BASE}/feed", headers=bearer(seeded["t2"])).json()["items"]
    t2_map = {i["id"]: i for i in r_t2}
    assert t2_map[seeded["p2_id"]]["is_liked"] is False
    assert t2_map[seeded["p2_id"]]["is_saved"] is False  # t1's bookmark is NEVER leaked to t2!
    assert t2_map[seeded["p1_id"]]["is_saved"] is True


def test_feed_avoids_n_plus_one_queries(
    client: TestClient, db_engine, seeded: dict
):
    executed_sql: list[str] = []

    def _count_queries(_conn, _cursor, statement, _parameters, _context, _executemany):
        executed_sql.append(statement)

    event.listen(db_engine, "before_cursor_execute", _count_queries)
    try:
        r = client.get(f"{COMMUNITY_BASE}/feed", headers=bearer(seeded["t1"]))
        assert r.status_code == 200
        assert len(r.json()["items"]) == 3
    finally:
        event.remove(db_engine, "before_cursor_execute", _count_queries)

    # Constant batched queries: auth user lookup + count + posts + users + providers + media + hashtags + likes + saves
    assert len(executed_sql) <= 10, f"Too many queries ({len(executed_sql)}): {executed_sql}"


# =============================================================================
# 3. POST DETAILS & PUBLIC SHARING METADATA
# =============================================================================

def test_post_details_sharing_metadata_and_draft_protection(
    client: TestClient, seeded: dict
):
    # Published post with image includes complete public share metadata
    r_pub = client.get(
        f"{COMMUNITY_BASE}/posts/{seeded['p1_id']}",
        headers=bearer(seeded["t2"]),
    )
    assert r_pub.status_code == 200
    body = r_pub.json()
    assert body["is_saved"] is True
    assert body["share_url"] == f"/community/posts/{seeded['p1_id']}"
    meta = body["share_metadata"]
    assert meta["is_shareable"] is True
    assert meta["share_url"] == f"/community/posts/{seeded['p1_id']}"
    assert "Amaya Perera" in meta["preview_title"]
    assert "Nine Arch Bridge" in meta["preview_title"]
    assert "Nine Arch Bridge" in meta["preview_text"]
    assert meta["preview_image_url"] == seeded["m1_url"]

    # Unpublished draft returns 404 to public/other users
    assert client.get(f"{COMMUNITY_BASE}/posts/{seeded['draft_id']}").status_code == 404
    assert (
        client.get(
            f"{COMMUNITY_BASE}/posts/{seeded['draft_id']}",
            headers=bearer(seeded["t2"]),
        ).status_code
        == 404
    )

    # Author & admin can view unpublished draft, but share_metadata marks it non-shareable
    r_author = client.get(
        f"{COMMUNITY_BASE}/posts/{seeded['draft_id']}",
        headers=bearer(seeded["t1"]),
    )
    assert r_author.status_code == 200
    assert r_author.json()["share_metadata"]["is_shareable"] is False
    assert r_author.json()["share_metadata"]["share_url"] is None
    assert r_author.json()["share_metadata"]["preview_image_url"] is None


# =============================================================================
# 4. COMMUNITY SEARCH (POSTS, PEOPLE, HASHTAGS) & PRIVATE DATA PROTECTION
# =============================================================================

def test_search_posts_people_hashtags_and_privacy_protection(
    client: TestClient, seeded: dict
):
    # Unified search (type=all) for "Sigiriya"
    r_all = client.get(
        f"{COMMUNITY_BASE}/search",
        headers=bearer(seeded["t1"]),
        params={"q": "Sigiriya", "type": "all"},
    )
    assert r_all.status_code == 200, r_all.text
    res = r_all.json()
    assert res["search_type"] == "all"
    # Unpublished draft mentioning Sigiriya is excluded!
    assert res["posts_total"] == 1
    assert [p["id"] for p in res["posts"]] == [seeded["p2_id"]]
    # Approved provider business_name ("Sigiriya Canopy Eco Lodge") matched cleanly
    assert res["people_total"] == 1
    person = res["people"][0]
    assert person["id"] == seeded["prov_user"].id
    assert person["is_verified_provider"] is True
    assert person["business_name"] == "Sigiriya Canopy Eco Lodge"
    assert person["district"] == "Matale"
    assert person["posts_count"] == 1
    assert person["followers_count"] == 1
    assert person["is_following"] is True
    # Private fields NEVER exposed
    for forbidden_key in ("email", "phone", "nic_number", "password_hash"):
        assert forbidden_key not in person
    # Hashtag matched
    assert res["hashtags_total"] == 1
    assert res["hashtags"][0]["tag"] == "sigiriya"

    # Searching by private email or phone returns ZERO people (no email/phone enumeration!)
    r_email = client.get(
        f"{COMMUNITY_BASE}/search",
        params={"q": "amaya.secret@private.example", "type": "people"},
    )
    assert r_email.status_code == 200
    assert r_email.json()["people_total"] == 0

    # Pending (unapproved) provider business name is NOT exposed/matched; inactive user excluded
    r_pend = client.get(
        f"{COMMUNITY_BASE}/search",
        params={"q": "Unapproved Secret Villa", "type": "people"},
    )
    assert r_pend.status_code == 200
    assert r_pend.json()["people_total"] == 0

    r_inact = client.get(
        f"{COMMUNITY_BASE}/search",
        params={"q": "Deactivated", "type": "people"},
    )
    assert r_inact.status_code == 200
    assert r_inact.json()["people_total"] == 0

    # Type-specific search: hashtags only (with leading '#')
    r_ht = client.get(
        f"{COMMUNITY_BASE}/search",
        params={"q": "#sri", "type": "hashtags"},
    )
    assert r_ht.status_code == 200
    assert r_ht.json()["posts_total"] == 0
    assert r_ht.json()["people_total"] == 0
    assert r_ht.json()["hashtags_total"] == 1
    assert r_ht.json()["hashtags"][0]["tag"] == "srilanka"

    # District filter combined with posts search
    r_dist_post = client.get(
        f"{COMMUNITY_BASE}/search",
        params={"q": "SriLanka", "type": "posts", "district": "Galle"},
    )
    assert r_dist_post.status_code == 200
    assert [p["id"] for p in r_dist_post.json()["posts"]] == [seeded["p3_id"]]

    # SQL LIKE wildcard characters are escaped literally (searching "%" does NOT match all rows)
    r_wild = client.get(f"{COMMUNITY_BASE}/search", params={"q": "%", "type": "posts"})
    assert r_wild.status_code == 200
    assert r_wild.json()["posts_total"] == 0


# =============================================================================
# 5. TRENDING HASHTAGS
# =============================================================================

def test_trending_hashtags_deterministic_ordering_and_limit(
    client: TestClient, db_session: Session, seeded: dict
):
    # Add a zero-usage hashtag to confirm unused tags are excluded
    db_session.add(CommunityHashtag(tag="unused_zero", usage_count=0))
    db_session.commit()

    r_trend = client.get(f"{COMMUNITY_BASE}/hashtags/trending", params={"limit": 10})
    assert r_trend.status_code == 200, r_trend.text
    data = r_trend.json()
    tags_and_counts = [(h["tag"], h["usage_count"]) for h in data["items"]]

    # srilanka=3, ella=2, then ties at usage_count=1 ordered alphabetically by tag ASC
    assert tags_and_counts[0] == ("srilanka", 3)
    assert tags_and_counts[1] == ("ella", 2)
    tie_tags = [t for t, c in tags_and_counts if c == 1]
    assert tie_tags == sorted(tie_tags)
    assert "unused_zero" not in [t for t, _ in tags_and_counts]
    assert "hiddendraft" not in [t for t, _ in tags_and_counts]

    # Draft-only hashtag must NOT leak into public hashtag search either
    r_hidden = client.get(f"{COMMUNITY_BASE}/search", params={"q": "hiddendraft", "type": "hashtags"})
    assert r_hidden.status_code == 200
    assert r_hidden.json()["hashtags_total"] == 0

    # Limit parameter respected
    r_top2 = client.get(f"{COMMUNITY_BASE}/hashtags/trending", params={"limit": 2})
    assert r_top2.status_code == 200
    assert len(r_top2.json()["items"]) == 2
    assert r_top2.json()["limit"] == 2
    assert r_top2.json()["total"] == data["total"]


# =============================================================================
# 6. UNSUPPORTED MIGRATION-037 FILTERS & INVALID INPUT VALIDATION
# =============================================================================

def test_migration_037_filters_fail_explicitly_and_invalid_params_rejected(
    client: TestClient, seeded: dict
):
    # featured_only is now enabled (Step 5C / Migration 039) and returns 200
    r_feat = client.get(f"{COMMUNITY_BASE}/feed", params={"featured_only": "true"})
    assert r_feat.status_code == 200
    assert r_feat.json()["total"] == 0

    # promotional_only is now enabled (Step 5B / Migration 039) and returns 200
    r_promo = client.get(f"{COMMUNITY_BASE}/feed", params={"promotional_only": "true"})
    assert r_promo.status_code == 200
    assert r_promo.json()["total"] == 0

    # Invalid pagination & limits rejected (422)
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"page": 0}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"page_size": 0}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"page_size": 100}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/hashtags/trending", params={"limit": 0}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/hashtags/trending", params={"limit": 100}).status_code == 422

    # Invalid hashtag filter rejected (422)
    assert client.get(f"{COMMUNITY_BASE}/feed", params={"hashtag": "bad-tag!"}).status_code == 422

    # Invalid search type, blank search query, or oversized query rejected (422)
    assert client.get(f"{COMMUNITY_BASE}/search", params={"q": "ella", "type": "videos"}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/search", params={"q": "   "}).status_code == 422
    assert client.get(f"{COMMUNITY_BASE}/search", params={"q": "a" * 101}).status_code == 422
