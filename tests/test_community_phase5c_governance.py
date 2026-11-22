"""
Automated Step 5C Tests — Travel Community & Social Feed
Admin Community Governance (TC-AD-02):
1. Community-only user suspension (dbo.community_user_profiles, NEVER alters users.is_active),
   expiry auto-resume, admin bypass & anti-admin-suspension guard, and 15/15 write-endpoint
   enforcement while preserving read-only access.
2. Admin featured-post management (is_featured toggle, featured_only=true feed filtering,
   unpublished/hidden/removed rejection, auto-unfeature on hide, non-admin prevention).
3. Blocked & featured hashtag governance (mutual exclusion CHECK constraint, blocked-hashtag
   rejection on create & edit, hiding blocked hashtags from feed/search/trending/post responses
   without deleting historical posts, featured hashtag ordering).
4. Transactional AdminAuditLog atomicity, HR-01 protection, and Step 5B compatibility.

Runs against an isolated in-memory SQLite database with PRAGMA foreign_keys=ON.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
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
from app.models.shared import AdminAuditLog, ListingReference, Notification
from app.models.user import Base, User
from app.services import (
    community_engagement,
    community_moderation,
    community_posts,
    community_profiles,
)

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
        AdminAuditLog.__table__,
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

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _override_get_db():
        yield db_session

    monkeypatch.setenv("COMMUNITY_UPLOAD_DIR", str(tmp_path))
    community_posts.reset_community_post_limiters()
    community_engagement.reset_community_engagement_limiters()
    community_profiles.reset_community_follow_limiter()
    community_moderation.reset_community_report_limiter()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        app.dependency_overrides.clear()
        community_posts.reset_community_post_limiters()
        community_engagement.reset_community_engagement_limiters()
        community_profiles.reset_community_follow_limiter()
        community_moderation.reset_community_report_limiter()


def bearer(user: User) -> dict[str, str]:
    token = create_access_token(
        {"sub": str(user.id), "email": user.email, "role": user.role}
    )
    return {"Authorization": f"Bearer {token}"}


def _make_jpeg_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 64), color=(24, 140, 120)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def actors(db_session: Session) -> dict[str, User]:
    t1 = User(
        full_name="Kavindu Perera",
        email="kavindu.5c@example.com",
        password_hash="x",
        role="tourist",
        is_active=True,
    )
    t2 = User(
        full_name="Nethmi Silva",
        email="nethmi.5c@example.com",
        password_hash="x",
        role="tourist",
        is_active=True,
    )
    provider = User(
        full_name="Ella Eco Host",
        email="provider.5c@example.com",
        password_hash="x",
        role="provider",
        is_active=True,
    )
    admin = User(
        full_name="Community Admin",
        email="admin.5c@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    other_admin = User(
        full_name="Second Admin",
        email="admin2.5c@example.com",
        password_hash="x",
        role="admin",
        is_active=True,
    )
    db_session.add_all([t1, t2, provider, admin, other_admin])
    db_session.commit()
    for u in (t1, t2, provider, admin, other_admin):
        db_session.refresh(u)
    return {
        "t1": t1,
        "t2": t2,
        "provider": provider,
        "admin": admin,
        "other_admin": other_admin,
    }


# =============================================================================
# 1. COMMUNITY-ONLY USER SUSPENSION & 15/15 WRITE ENDPOINT ENFORCEMENT
# =============================================================================

def test_community_only_suspension_enforced_across_all_15_write_endpoints_without_touching_users_is_active(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, admin, other_admin = (
        actors["t1"],
        actors["t2"],
        actors["admin"],
        actors["other_admin"],
    )

    # Seed pre-suspension content owned by t1 & t2 so every single write endpoint has valid targets
    t1_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Sunrise hike at Pidurangala #sigiriya", "district": "Matale"},
    ).json()
    t1_media = client.post(
        f"{COMMUNITY_BASE}/posts/{t1_post['id']}/media",
        headers=bearer(t1),
        files={"file": ("sunrise.jpg", _make_jpeg_bytes(), "image/jpeg")},
    ).json()
    t2_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t2),
        json={"content": "Galle Fort sunset walk #galle", "district": "Galle"},
    ).json()
    t1_comment = client.post(
        f"{COMMUNITY_BASE}/posts/{t2_post['id']}/comments",
        headers=bearer(t1),
        json={"content": "Amazing view!"},
    ).json()
    assert client.put(
        f"{COMMUNITY_BASE}/posts/{t2_post['id']}/like", headers=bearer(t1)
    ).status_code == 200
    assert client.put(
        f"{COMMUNITY_BASE}/posts/{t2_post['id']}/save", headers=bearer(t1)
    ).status_code == 200
    assert client.put(
        f"{COMMUNITY_BASE}/profiles/{t2.id}/follow", headers=bearer(t1)
    ).status_code == 200

    # Non-admin cannot suspend anyone
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
            headers=bearer(t2),
            json={"is_community_suspended": True, "suspension_reason": "Trying without role"},
        ).status_code
        == 403
    )

    # Admin cannot suspend another admin or themselves
    r_adm = client.patch(
        f"{COMMUNITY_BASE}/admin/profiles/{other_admin.id}",
        headers=bearer(admin),
        json={"is_community_suspended": True, "suspension_reason": "Attempt on admin"},
    )
    assert r_adm.status_code == 400
    assert "Administrator" in r_adm.json()["detail"]

    # Invalid short reason or past expiry rejected cleanly (422)
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
            headers=bearer(admin),
            json={"is_community_suspended": True, "suspension_reason": "ab"},
        ).status_code
        == 422
    )
    past_dt = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
            headers=bearer(admin),
            json={
                "is_community_suspended": True,
                "suspension_reason": "Repeated spam links",
                "suspended_until": past_dt,
            },
        ).status_code
        == 422
    )

    # Admin suspends t1 temporarily (7 days) — dbo.users.is_active MUST remain True!
    future_dt = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    r_susp = client.patch(
        f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
        headers=bearer(admin),
        json={
            "is_community_suspended": True,
            "suspension_reason": "Repeated promotional spam in travel threads",
            "suspended_until": future_dt,
        },
    )
    assert r_susp.status_code == 200, r_susp.text
    s_body = r_susp.json()
    assert s_body["user_id"] == t1.id
    assert s_body["is_community_suspended"] is True
    assert s_body["is_currently_suspended"] is True
    assert s_body["suspended_by"] == admin.id
    assert s_body["suspended_at"] is not None
    assert s_body["suspended_until"] is not None
    assert s_body["action"] == "community_user_suspend"
    assert s_body["audit_log_id"] > 0

    # CRITICAL: dbo.users.is_active is completely untouched
    db_session.expire_all()
    refreshed_t1 = db_session.get(User, t1.id)
    assert refreshed_t1 is not None and refreshed_t1.is_active is True

    # Audit log recorded accurately
    audit_row = db_session.get(AdminAuditLog, s_body["audit_log_id"])
    assert audit_row is not None
    assert audit_row.entity_name == "community_user_profile"
    assert audit_row.entity_id == t1.id
    assert audit_row.action == "community_user_suspend"

    # ALL 15 community write endpoints MUST return 403 for suspended user t1
    write_checks = [
        ("POST /posts", client.post(f"{COMMUNITY_BASE}/posts", headers=bearer(t1), json={"content": "Blocked post"})),
        ("PATCH /posts/{id}", client.patch(f"{COMMUNITY_BASE}/posts/{t1_post['id']}", headers=bearer(t1), json={"content": "Blocked edit"})),
        ("DELETE /posts/{id}", client.delete(f"{COMMUNITY_BASE}/posts/{t1_post['id']}", headers=bearer(t1))),
        ("POST /posts/{id}/media", client.post(f"{COMMUNITY_BASE}/posts/{t1_post['id']}/media", headers=bearer(t1), files={"file": ("b.jpg", _make_jpeg_bytes(), "image/jpeg")})),
        ("DELETE /posts/{id}/media/{mid}", client.delete(f"{COMMUNITY_BASE}/posts/{t1_post['id']}/media/{t1_media['id']}", headers=bearer(t1))),
        ("PUT /posts/{id}/like", client.put(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/like", headers=bearer(t1))),
        ("DELETE /posts/{id}/like", client.delete(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/like", headers=bearer(t1))),
        ("POST /posts/{id}/comments", client.post(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/comments", headers=bearer(t1), json={"content": "Blocked comment"})),
        ("PATCH /comments/{cid}", client.patch(f"{COMMUNITY_BASE}/comments/{t1_comment['id']}", headers=bearer(t1), json={"content": "Blocked edit"})),
        ("DELETE /comments/{cid}", client.delete(f"{COMMUNITY_BASE}/comments/{t1_comment['id']}", headers=bearer(t1))),
        ("PUT /posts/{id}/save", client.put(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/save", headers=bearer(t1))),
        ("DELETE /posts/{id}/save", client.delete(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/save", headers=bearer(t1))),
        ("PUT /profiles/{uid}/follow", client.put(f"{COMMUNITY_BASE}/profiles/{t2.id}/follow", headers=bearer(t1))),
        ("DELETE /profiles/{uid}/follow", client.delete(f"{COMMUNITY_BASE}/profiles/{t2.id}/follow", headers=bearer(t1))),
        ("POST /reports", client.post(f"{COMMUNITY_BASE}/reports", headers=bearer(t1), json={"entity_type": "post", "entity_id": t2_post["id"], "report_reason": "Blocked report"})),
    ]
    assert len(write_checks) == 15
    for label, resp in write_checks:
        assert resp.status_code == 403, f"Expected 403 on {label}, got {resp.status_code}: {resp.text}"
        assert "suspended" in resp.json()["detail"].lower()

    # Read-only community endpoints MUST continue working for suspended user t1
    assert client.get(f"{COMMUNITY_BASE}/feed", headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/posts/{t2_post['id']}", headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/posts/{t2_post['id']}/comments", headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/saved", headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/profiles/{t2.id}", headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/search", params={"q": "galle"}, headers=bearer(t1)).status_code == 200
    assert client.get(f"{COMMUNITY_BASE}/hashtags/trending", headers=bearer(t1)).status_code == 200

    # Admin retains full moderation capabilities (including DELETE /comments/{id})
    r_adm_del = client.delete(
        f"{COMMUNITY_BASE}/comments/{t1_comment['id']}", headers=bearer(admin)
    )
    assert r_adm_del.status_code == 200

    # Admin unsuspends t1 -> write access resumes immediately & audit log recorded
    r_unsusp = client.patch(
        f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
        headers=bearer(admin),
        json={"is_community_suspended": False, "suspension_reason": "Appeal approved"},
    )
    assert r_unsusp.status_code == 200, r_unsusp.text
    u_body = r_unsusp.json()
    assert u_body["is_community_suspended"] is False
    assert u_body["is_currently_suspended"] is False
    assert u_body["suspended_until"] is None
    assert u_body["action"] == "community_user_unsuspend"
    assert u_body["audit_log_id"] > 0

    r_post_after = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Back after unsuspension!"},
    )
    assert r_post_after.status_code == 201


def test_expired_community_suspension_automatically_allows_writes(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, admin = actors["t1"], actors["admin"]

    # Insert an already-expired suspension row directly (simulating time passing past suspended_until)
    past_start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
    past_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
    db_session.add(
        CommunityUserProfile(
            user_id=t1.id,
            is_community_suspended=True,
            suspension_reason="Temporary cooldown",
            suspended_by=admin.id,
            suspended_at=past_start,
            suspended_until=past_until,
        )
    )
    db_session.commit()

    # Expired suspension automatically permits posting without requiring manual admin intervention
    r_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Posting cleanly after suspension expiry"},
    )
    assert r_post.status_code == 201, r_post.text


# =============================================================================
# 2. ADMIN FEATURED POST MANAGEMENT & FEED FILTERING
# =============================================================================

def test_admin_featured_post_management_and_featured_feed_filtering(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, admin = actors["t1"], actors["t2"], actors["admin"]

    published_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Ella Nine Arch Bridge guide #ella", "district": "Badulla"},
    ).json()
    normal_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Kandy Lake morning walk #kandy", "district": "Kandy"},
    ).json()
    draft_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Unpublished draft story", "is_published": False},
    ).json()

    # Non-admin cannot feature a post (403 on admin route) and cannot set is_featured on user create/edit
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
            headers=bearer(t1),
            json={"is_featured": True},
        ).status_code
        == 403
    )
    r_user_edit = client.patch(
        f"{COMMUNITY_BASE}/posts/{published_post['id']}",
        headers=bearer(t1),
        json={"content": "Ella Nine Arch Bridge updated", "is_featured": True},
    )
    assert r_user_edit.status_code == 200
    assert r_user_edit.json()["is_featured"] is False

    # Admin cannot feature an unpublished draft (400)
    r_feat_draft = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{draft_post['id']}",
        headers=bearer(admin),
        json={"is_featured": True, "reason": "Try featuring draft"},
    )
    assert r_feat_draft.status_code == 400

    # Admin features published_post -> succeeds + audit logged
    r_feat = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
        headers=bearer(admin),
        json={"is_featured": True, "reason": "Editor's pick for Hill Country"},
    )
    assert r_feat.status_code == 200, r_feat.text
    f_body = r_feat.json()
    assert f_body["is_featured"] is True
    assert f_body["previous_is_featured"] is False
    assert f_body["is_published"] is True
    assert f_body["action"] == "community_post_feature"
    assert f_body["audit_log_id"] > 0

    # GET /feed?featured_only=true returns ONLY the featured active published post
    r_feed = client.get(f"{COMMUNITY_BASE}/feed", params={"featured_only": "true"})
    assert r_feed.status_code == 200
    feed_ids = [item["id"] for item in r_feed.json()["items"]]
    assert feed_ids == [published_post["id"]]
    assert normal_post["id"] not in feed_ids
    assert r_feed.json()["items"][0]["is_featured"] is True

    # Admin hiding a featured post MUST auto-clear is_featured=False and remove it from featured_only feed
    r_hide = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
        headers=bearer(admin),
        json={"is_published": False, "reason": "Policy review"},
    )
    assert r_hide.status_code == 200
    assert r_hide.json()["is_published"] is False
    assert r_hide.json()["is_featured"] is False
    assert r_hide.json()["moderation_status"] == "hidden_by_admin"

    assert (
        client.get(f"{COMMUNITY_BASE}/feed", params={"featured_only": "true"}).json()["total"]
        == 0
    )

    # Admin cannot feature a hidden_by_admin post without restoring it first
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
            headers=bearer(admin),
            json={"is_featured": True},
        ).status_code
        == 400
    )

    # HR-01 protection still holds: author t1 cannot edit or republish hidden_by_admin post
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{published_post['id']}",
            headers=bearer(t1),
            json={"is_published": True},
        ).status_code
        == 403
    )

    # Admin restores & re-features, then unfeatures cleanly
    r_restore = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
        headers=bearer(admin),
        json={"is_published": True, "is_featured": True, "reason": "Cleared review"},
    )
    assert r_restore.status_code == 200
    assert r_restore.json()["is_published"] is True
    assert r_restore.json()["is_featured"] is True

    r_unfeat = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{published_post['id']}",
        headers=bearer(admin),
        json={"is_featured": False, "reason": "Rotation complete"},
    )
    assert r_unfeat.status_code == 200
    assert r_unfeat.json()["action"] == "community_post_unfeature"
    assert r_unfeat.json()["is_featured"] is False
    assert (
        client.get(f"{COMMUNITY_BASE}/feed", params={"featured_only": "true"}).json()["total"]
        == 0
    )

    # Soft-removed post (removed_by_author) cannot be featured even if admin attempts it
    # Create a reported post so delete soft-removes it as removed_by_author
    r_rep = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={
            "entity_type": "post",
            "entity_id": normal_post["id"],
            "report_reason": "Preserving moderation evidence",
        },
    )
    assert r_rep.status_code == 201
    r_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{normal_post['id']}", headers=bearer(t1)
    )
    assert r_del.status_code == 200
    assert r_del.json()["deletion_mode"] == "soft_unpublished_moderation_preserved"

    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/posts/{normal_post['id']}",
            headers=bearer(admin),
            json={"is_featured": True},
        ).status_code
        == 400
    )


# =============================================================================
# 3. BLOCKED & FEATURED HASHTAG GOVERNANCE
# =============================================================================

@pytest.mark.parametrize("role", ["content_moderator", "super_admin", "admin"])
def test_generic_post_visibility_uses_audited_moderation(client, db_session, actors, role):
    author, unrelated = actors["t1"], actors["t2"]
    moderator = actors["admin"]
    moderator.role = role
    db_session.commit()
    author_headers, moderator_headers = bearer(author), bearer(moderator)
    post = client.post(f"{COMMUNITY_BASE}/posts", headers=author_headers,
                       json={"content": "Moderation parity"}).json()
    path = f"{COMMUNITY_BASE}/posts/{post['id']}"
    admin_path = f"{COMMUNITY_BASE}/admin/posts/{post['id']}"
    edit = client.patch(path, headers=author_headers, json={"content": "Author update"})
    assert edit.status_code == 200
    assert edit.json()["content"] == "Author update"
    assert client.patch(path, headers=bearer(unrelated), json={"is_published": False}).status_code == 403
    assert client.patch(path, headers=moderator_headers,
                        json={"is_published": False, "content": "Moderator rewrite"}).status_code == 403
    assert db_session.query(AdminAuditLog).count() == 0
    upload = client.post(f"{path}/media", headers=author_headers,
                         files={"file": ("review.jpg", _make_jpeg_bytes(), "image/jpeg")})
    assert upload.status_code == 201
    media_url = upload.json()["media_url"]
    assert client.get(media_url).status_code == 200
    assert client.patch(admin_path, headers=moderator_headers, json={"is_featured": True}).status_code == 200

    hidden = client.patch(path, headers=moderator_headers, json={"is_published": False})
    assert hidden.status_code == 200
    assert set(hidden.json()) == set(post)
    assert hidden.json()["content"] == "Author update"
    assert hidden.json()["is_published"] is False
    assert hidden.json()["is_featured"] is False
    assert client.get(path).status_code == 404
    assert client.get(media_url, headers=bearer(unrelated)).status_code == 404
    assert client.get(media_url, headers=moderator_headers).status_code == 200
    assert post["id"] not in [item["id"] for item in client.get(f"{COMMUNITY_BASE}/feed").json()["items"]]
    search = f"{COMMUNITY_BASE}/search"
    assert client.get(search, params={"q": "Author update", "type": "posts"}).json()["posts"] == []
    assert client.patch(path, headers=author_headers, json={"is_published": True}).status_code == 403
    assert client.patch(path, headers=moderator_headers, json={"is_published": False}).status_code == 200
    assert db_session.query(AdminAuditLog).filter_by(action="community_post_hide").count() == 1

    restored = client.patch(path, headers=moderator_headers, json={"is_published": True})
    assert restored.status_code == 200
    assert restored.json()["is_published"] is True
    assert restored.json()["is_featured"] is False
    assert client.get(path).status_code == client.get(media_url).status_code == 200
    assert [item["id"] for item in client.get(search, params={"q": "Author update", "type": "posts"}).json()["posts"]] == [post["id"]]
    events = db_session.query(AdminAuditLog).order_by(AdminAuditLog.id).all()
    assert [event.action for event in events] == [
        "community_post_feature", "community_post_hide", "community_post_restore",
    ]
    for event, published, previous, current in (
        (events[1], False, "active", "hidden_by_admin"),
        (events[2], True, "hidden_by_admin", "active"),
    ):
        assert event.admin_user_id == moderator.id
        assert event.created_at is not None
        assert event.entity_id == post["id"]
        assert json.loads(event.details) == {
            "author_id": author.id, "previous_is_published": not published, "new_is_published": published,
            "previous_is_featured": not published, "new_is_featured": False,
            "previous_moderation_status": previous, "new_moderation_status": current,
            "reason": None, "state_changed": True,
        }

    dedicated = client.patch(admin_path, headers=moderator_headers,
                             json={"is_published": True, "reason": "Already reviewed"})
    assert dedicated.status_code == 200
    assert dedicated.json()["reason"] == "Already reviewed"
    event = db_session.get(AdminAuditLog, dedicated.json()["audit_log_id"])
    assert json.loads(event.details)["state_changed"] is False
    retried = client.patch(admin_path, headers=moderator_headers,
                           json={"is_published": True, "reason": "Already reviewed"})
    assert retried.status_code == 200
    assert retried.json()["audit_log_id"] == event.id
    assert db_session.query(AdminAuditLog).count() == 4


def test_public_surfaces_hide_inconsistent_moderated_post(client, db_session, actors):
    headers = bearer(actors["t1"])
    post = client.post(f"{COMMUNITY_BASE}/posts", headers=headers,
                       json={"content": "Legacy inconsistent moderation"}).json()
    path = f"{COMMUNITY_BASE}/posts/{post['id']}"
    upload = client.post(f"{path}/media", headers=headers,
                         files={"file": ("review.jpg", _make_jpeg_bytes(), "image/jpeg")})
    assert upload.status_code == 201
    row = db_session.get(CommunityPost, post["id"])
    assert row.is_published is True
    row.moderation_status = "hidden_by_admin"
    db_session.commit()

    assert client.get(path).status_code == 404
    assert client.get(upload.json()["media_url"]).status_code == 404
    assert client.get(f"{COMMUNITY_BASE}/feed").json()["items"] == []
    assert client.get(f"{COMMUNITY_BASE}/search", params={"q": "Legacy"}).json()["posts"] == []


@pytest.mark.parametrize("role", ["operations_admin", "finance_admin", "support_admin"])
def test_generic_and_dedicated_visibility_deny_other_admin_roles(client, db_session, actors, role):
    denied = actors["admin"]
    denied.role = role
    db_session.commit()
    post = client.post(f"{COMMUNITY_BASE}/posts", headers=bearer(actors["t1"]),
                       json={"content": "Unchanged post"}).json()
    for prefix in ("posts", "admin/posts"):
        response = client.patch(f"{COMMUNITY_BASE}/{prefix}/{post['id']}", headers=bearer(denied),
                                json={"is_published": False})
        assert response.status_code == 403
    assert db_session.get(CommunityPost, post["id"]).is_published is True
    assert db_session.query(AdminAuditLog).count() == 0


@pytest.mark.parametrize("operation", ["patch", "soft_delete", "hard_delete"])
def test_generic_visibility_and_removal_audit_rollback(client, db_session, actors, monkeypatch, operation):
    post = client.post(f"{COMMUNITY_BASE}/posts", headers=bearer(actors["t1"]),
                       json={"content": "Audit rollback #rollback"}).json()
    path = f"{COMMUNITY_BASE}/posts/{post['id']}"
    if operation == "soft_delete":
        report = client.post(f"{COMMUNITY_BASE}/reports", headers=bearer(actors["t2"]),
                             json={"entity_type": "post", "entity_id": post["id"], "report_reason": "Check"})
        assert report.status_code == 201

    record_audit = community_moderation.record_community_admin_audit

    def fail_after_audit(*args, **kwargs):
        record_audit(*args, **kwargs)
        raise RuntimeError("simulated audit failure after flush")

    def moderate():
        headers = bearer(actors["admin"])
        if operation == "patch":
            return client.patch(path, headers=headers, json={"is_published": False})
        return client.delete(path, headers=headers)

    with monkeypatch.context() as patch:
        patch.setattr(community_moderation, "record_community_admin_audit", fail_after_audit)
        with pytest.raises(RuntimeError, match="simulated audit failure"):
            moderate()
    db_session.expire_all()
    assert db_session.get(CommunityPost, post["id"]).is_published is True
    assert db_session.query(AdminAuditLog).count() == 0

    response = moderate()
    assert response.status_code == 200
    event = db_session.query(AdminAuditLog).one()
    assert event.admin_user_id == actors["admin"].id
    assert event.entity_id == post["id"]
    if operation == "hard_delete":
        assert response.json()["deletion_mode"] == "hard_deleted"
        assert event.action == "community_post_delete"
        assert db_session.get(CommunityPost, post["id"]) is None
    else:
        assert event.action == "community_post_hide"
        assert db_session.get(CommunityPost, post["id"]).is_published is False
        if operation == "soft_delete":
            assert response.json()["deletion_mode"] == "soft_unpublished_moderation_preserved"


def test_hashtag_governance_blocking_featuring_and_public_visibility(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, admin = actors["t1"], actors["t2"], actors["admin"]

    # Seed historical posts with hashtags
    p1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Mirissa whale watching #mirissa #spamsale", "district": "Matara"},
    ).json()
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t2),
        json={"content": "Ella train ride #ella #mirissa", "district": "Badulla"},
    ).json()

    # Non-admin cannot access admin hashtag endpoints
    assert client.get(f"{COMMUNITY_BASE}/admin/hashtags", headers=bearer(t1)).status_code == 403

    # Admin lists hashtags
    r_list = client.get(f"{COMMUNITY_BASE}/admin/hashtags", headers=bearer(admin))
    assert r_list.status_code == 200
    tags_by_name = {item["tag"]: item for item in r_list.json()["items"]}
    assert {"mirissa", "spamsale", "ella"}.issubset(tags_by_name.keys())

    spam_id = tags_by_name["spamsale"]["id"]
    ella_id = tags_by_name["ella"]["id"]

    # Non-admin cannot modify hashtags
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/hashtags/{spam_id}",
            headers=bearer(t1),
            json={"is_blocked": True},
        ).status_code
        == 403
    )

    # Reject simultaneous is_blocked=True and is_featured=True (400)
    r_both = client.patch(
        f"{COMMUNITY_BASE}/admin/hashtags/{spam_id}",
        headers=bearer(admin),
        json={"is_blocked": True, "is_featured": True},
    )
    assert r_both.status_code == 400

    # Admin blocks #spamsale -> succeeds + audit logged
    r_block = client.patch(
        f"{COMMUNITY_BASE}/admin/hashtags/{spam_id}",
        headers=bearer(admin),
        json={"is_blocked": True, "reason": "Disallowed commercial spam tag"},
    )
    assert r_block.status_code == 200, r_block.text
    assert r_block.json()["action"] == "community_hashtag_block"
    assert r_block.json()["hashtag"]["is_blocked"] is True
    assert r_block.json()["audit_log_id"] > 0

    # Cannot feature an already-blocked hashtag without unblocking it
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/hashtags/{spam_id}",
            headers=bearer(admin),
            json={"is_featured": True},
        ).status_code
        == 400
    )

    # Admin features #ella (usage_count=1, lower than #mirissa usage_count=2)
    r_feat_tag = client.patch(
        f"{COMMUNITY_BASE}/admin/hashtags/{ella_id}",
        headers=bearer(admin),
        json={"is_featured": True, "reason": "Seasonal highlight"},
    )
    assert r_feat_tag.status_code == 200
    assert r_feat_tag.json()["action"] == "community_hashtag_feature"
    assert r_feat_tag.json()["hashtag"]["is_featured"] is True

    # Historical post p1 MUST NOT be deleted, but #spamsale is hidden from its serialized hashtags
    r_p1 = client.get(f"{COMMUNITY_BASE}/posts/{p1['id']}")
    assert r_p1.status_code == 200
    assert [h["tag"] for h in r_p1.json()["hashtags"]] == ["mirissa"]
    assert db_session.get(CommunityPost, p1["id"]) is not None

    # Blocked hashtag excluded from feed ?hashtag=, search, and trending
    r_feed_blocked = client.get(f"{COMMUNITY_BASE}/feed", params={"hashtag": "spamsale"})
    assert r_feed_blocked.status_code == 200
    assert r_feed_blocked.json()["total"] == 0

    r_search_blocked = client.get(
        f"{COMMUNITY_BASE}/search", params={"q": "spamsale", "type": "hashtags"}
    )
    assert r_search_blocked.status_code == 200
    assert r_search_blocked.json()["hashtags_total"] == 0

    r_trending = client.get(f"{COMMUNITY_BASE}/hashtags/trending")
    assert r_trending.status_code == 200
    trending_items = r_trending.json()["items"]
    trending_tags = [item["tag"] for item in trending_items]
    assert "spamsale" not in trending_tags
    # Featured hashtag #ella is prioritized first in trending
    assert trending_tags[0] == "ella"
    assert trending_items[0]["is_featured"] is True

    # Creating a new post with blocked hashtag #spamsale (via body or explicit list) rejected (400)
    r_create_body = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Trying #spamsale in body"},
    )
    assert r_create_body.status_code == 400
    assert "blocked" in r_create_body.json()["detail"].lower()

    r_create_list = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Clean text", "hashtags": ["spamsale"]},
    )
    assert r_create_list.status_code == 400

    # Editing an existing post to add blocked hashtag #spamsale rejected (400)
    r_edit_blocked = client.patch(
        f"{COMMUNITY_BASE}/posts/{p2['id']}",
        headers=bearer(t2),
        json={"content": "Updated with #spamsale"},
    )
    assert r_edit_blocked.status_code == 400

    # Admin filtering on GET /admin/hashtags works cleanly
    r_blocked_only = client.get(
        f"{COMMUNITY_BASE}/admin/hashtags",
        headers=bearer(admin),
        params={"is_blocked": "true"},
    )
    assert r_blocked_only.status_code == 200
    assert [i["tag"] for i in r_blocked_only.json()["items"]] == ["spamsale"]

    r_featured_only = client.get(
        f"{COMMUNITY_BASE}/admin/hashtags",
        headers=bearer(admin),
        params={"is_featured": "true"},
    )
    assert r_featured_only.status_code == 200
    assert [i["tag"] for i in r_featured_only.json()["items"]] == ["ella"]

    # Admin unblocks #spamsale -> post creation succeeds again
    r_unblock = client.patch(
        f"{COMMUNITY_BASE}/admin/hashtags/{spam_id}",
        headers=bearer(admin),
        json={"is_blocked": False, "reason": "Tag policy revised"},
    )
    assert r_unblock.status_code == 200
    assert r_unblock.json()["action"] == "community_hashtag_unblock"
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts",
            headers=bearer(t1),
            json={"content": "Now allowed #spamsale"},
        ).status_code
        == 201
    )

    # Admin activity feed surfaces suspension / feature / hashtag audit entries cleanly
    r_act = client.get(f"{COMMUNITY_BASE}/admin/activity", headers=bearer(admin))
    assert r_act.status_code == 200
    entity_names = {a["entity_name"] for a in r_act.json()["recent_moderation_actions"]}
    assert "community_hashtag" in entity_names


# =============================================================================
# 4. ATOMIC AUDIT LOG ROLLBACK FOR GOVERNANCE ACTIONS
# =============================================================================

def test_governance_actions_roll_back_atomically_if_audit_fails(
    client: TestClient,
    db_session: Session,
    actors: dict[str, User],
    monkeypatch: pytest.MonkeyPatch,
):
    t1, admin = actors["t1"], actors["admin"]
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Rollback governance check #atomictag"},
    ).json()
    tag = db_session.query(CommunityHashtag).filter(CommunityHashtag.tag == "atomictag").one()

    def _boom(*_args, **_kwargs):
        raise OperationalError("INSERT admin_audit_logs", {}, Exception("simulated failure"))

    monkeypatch.setattr(community_moderation, "record_community_admin_audit", _boom)

    # 1. Suspension rollback
    with pytest.raises(OperationalError):
        client.patch(
            f"{COMMUNITY_BASE}/admin/profiles/{t1.id}",
            headers=bearer(admin),
            json={"is_community_suspended": True, "suspension_reason": "Rollback test"},
        )
    db_session.expire_all()
    assert db_session.get(CommunityUserProfile, t1.id) is None

    # 2. Featured post rollback
    with pytest.raises(OperationalError):
        client.patch(
            f"{COMMUNITY_BASE}/admin/posts/{post['id']}",
            headers=bearer(admin),
            json={"is_featured": True},
        )
    db_session.expire_all()
    assert bool(db_session.get(CommunityPost, post["id"]).is_featured) is False  # type: ignore[union-attr]

    # 3. Hashtag block rollback
    with pytest.raises(OperationalError):
        client.patch(
            f"{COMMUNITY_BASE}/admin/hashtags/{tag.id}",
            headers=bearer(admin),
            json={"is_blocked": True},
        )
    db_session.expire_all()
    assert bool(db_session.get(CommunityHashtag, tag.id).is_blocked) is False  # type: ignore[union-attr]
