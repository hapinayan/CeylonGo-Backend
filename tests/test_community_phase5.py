"""
Automated Phase 5 Tests — Travel Community & Social Feed
(Community Reporting, Admin Report Queue & Transitions, Admin Post Hide/Restore,
Admin Comment Moderation & Thread Preservation, Admin Activity & Transactional Audit Logs).

Runs against an isolated in-memory SQLite database with PRAGMA foreign_keys=ON.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json

from fastapi.testclient import TestClient
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
from app.services import community_engagement, community_moderation, community_posts

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
def client(db_session: Session):
    community_posts.reset_community_post_limiters()
    community_moderation.reset_community_report_limiter()
    community_engagement.reset_community_engagement_limiters()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        community_posts.reset_community_post_limiters()
        community_moderation.reset_community_report_limiter()
        community_engagement.reset_community_engagement_limiters()


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def actors(db_session: Session) -> dict[str, User]:
    t1 = User(
        full_name="Kasun Perera",
        email="kasun.private@p5.example",
        role="traveler",
        is_active=True,
    )
    t2 = User(
        full_name="Dilini Fernando",
        email="dilini.private@p5.example",
        role="traveler",
        is_active=True,
    )
    prov_user = User(
        full_name="Ruwan Silva",
        email="ruwan.prov@p5.example",
        role="provider",
        is_active=True,
    )
    admin = User(
        full_name="Community Admin",
        email="admin.p5@p5.example",
        role="admin",
        is_active=True,
    )
    inactive = User(
        full_name="Inactive Member",
        email="inactive.p5@p5.example",
        role="traveler",
        is_active=False,
    )
    db_session.add_all([t1, t2, prov_user, admin, inactive])
    db_session.flush()

    prov_profile = ProviderProfile(
        user_id=prov_user.id,
        business_name="Ella Eco Retreat",
        provider_type="homestay",
        phone="0779990001",
        district="Badulla",
        nic_number="199012340009",
        approval_status="approved",
    )
    db_session.add(prov_profile)
    db_session.commit()

    return {
        "t1": t1,
        "t2": t2,
        "prov": prov_user,
        "admin": admin,
        "inactive": inactive,
    }


# =============================================================================
# 1. COMMUNITY REPORTING: VALID TARGETS, INVALID TARGETS, DUPLICATES & RATE LIMIT
# =============================================================================

def test_community_reporting_valid_invalid_duplicates_and_sanitization(
    client: TestClient, actors: dict[str, User]
):
    t1, t2, inactive = actors["t1"], actors["t2"], actors["inactive"]

    # Unauthenticated report rejected
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            json={"entity_type": "user", "entity_id": t2.id, "report_reason": "Spam"},
        ).status_code
        in (401, 403)
    )

    # Seed a published post, a draft post, and a comment on the published post
    pub_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Ella sunrise train view #Ella"},
    ).json()
    draft_post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Secret draft", "is_published": False},
    ).json()
    comment = client.post(
        f"{COMMUNITY_BASE}/posts/{pub_post['id']}/comments",
        headers=bearer(t1),
        json={"content": "First comment on post"},
    ).json()

    # Valid reports for post, comment, and user (with HTML tag sanitization)
    r_post = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={
            "entity_type": "post",
            "entity_id": pub_post["id"],
            "report_reason": "<b>Misleading</b>   content",
            "description": "<script>alert(1)</script>Incorrect trail safety info",
        },
    )
    assert r_post.status_code == 201, r_post.text
    rp_body = r_post.json()
    assert rp_body["report_status"] == "pending"
    assert "<" not in rp_body["report_reason"]
    assert "<script>" not in (rp_body["description"] or "")

    r_comment = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={
            "entity_type": "comment",
            "entity_id": comment["id"],
            "report_reason": "Abusive language",
            "description": "Contains harassment",
        },
    )
    assert r_comment.status_code == 201, r_comment.text

    r_user = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={
            "entity_type": "user",
            "entity_id": t1.id,
            "report_reason": "Spam account",
        },
    )
    assert r_user.status_code == 201, r_user.text

    # Duplicate open report by same user for same target rejected with 409
    r_dup = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={
            "entity_type": "post",
            "entity_id": pub_post["id"],
            "report_reason": "Duplicate spam report",
        },
    )
    assert r_dup.status_code == 409

    # Invalid/inaccessible targets rejected
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "post", "entity_id": 999999, "report_reason": "Missing"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "post", "entity_id": draft_post["id"], "report_reason": "Draft"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "comment", "entity_id": 999999, "report_reason": "Missing"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "user", "entity_id": inactive.id, "report_reason": "Inactive"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "invalid_type", "entity_id": 1, "report_reason": "Bad type"},
        ).status_code
        == 422
    )

    # Self-reporting own post, comment, or user rejected with 400
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t1),
            json={"entity_type": "post", "entity_id": pub_post["id"], "report_reason": "Self"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t1),
            json={"entity_type": "comment", "entity_id": comment["id"], "report_reason": "Self"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t1),
            json={"entity_type": "user", "entity_id": t1.id, "report_reason": "Self"},
        ).status_code
        == 400
    )


def test_report_rate_limiting_enforced_with_429(
    client: TestClient, actors: dict[str, User], monkeypatch: pytest.MonkeyPatch
):
    t1, t2 = actors["t1"], actors["t2"]
    p1 = client.post(
        f"{COMMUNITY_BASE}/posts", headers=bearer(t1), json={"content": "Post one"}
    ).json()
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts", headers=bearer(t1), json={"content": "Post two"}
    ).json()
    p3 = client.post(
        f"{COMMUNITY_BASE}/posts", headers=bearer(t1), json={"content": "Post three"}
    ).json()

    monkeypatch.setattr(community_moderation.community_report_limiter, "max_requests", 2)

    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "post", "entity_id": p1["id"], "report_reason": "Spam 1"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "post", "entity_id": p2["id"], "report_reason": "Spam 2"},
        ).status_code
        == 201
    )
    r_429 = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "post", "entity_id": p3["id"], "report_reason": "Spam 3"},
    )
    assert r_429.status_code == 429


# =============================================================================
# 2. ADMIN RBAC, REPORT QUEUE FILTERING, PAGINATION & VALID/INVALID TRANSITIONS
# =============================================================================

def test_admin_rbac_report_queue_transitions_and_audit_logs(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, prov, admin = actors["t1"], actors["t2"], actors["prov"], actors["admin"]

    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Sigiriya fortress climb #Sigiriya"},
    ).json()
    comment = client.post(
        f"{COMMUNITY_BASE}/posts/{post['id']}/comments",
        headers=bearer(t1),
        json={"content": "Reply info here"},
    ).json()

    rep_post = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "post", "entity_id": post["id"], "report_reason": "Off-topic"},
    ).json()
    rep_comment = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "comment", "entity_id": comment["id"], "report_reason": "Spam"},
    ).json()
    rep_user = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "user", "entity_id": t1.id, "report_reason": "Impersonation"},
    ).json()

    # Non-admin rejection across all admin moderation endpoints
    for non_admin_headers in ({}, bearer(t1), bearer(prov)):
        assert client.get(f"{COMMUNITY_BASE}/admin/reports", headers=non_admin_headers).status_code in (401, 403)
        assert (
            client.patch(
                f"{COMMUNITY_BASE}/admin/reports/{rep_post['id']}",
                headers=non_admin_headers,
                json={"report_status": "investigating"},
            ).status_code
            in (401, 403)
        )
        assert (
            client.patch(
                f"{COMMUNITY_BASE}/admin/posts/{post['id']}",
                headers=non_admin_headers,
                json={"is_published": False},
            ).status_code
            in (401, 403)
        )
        assert (
            client.delete(
                f"{COMMUNITY_BASE}/admin/comments/{comment['id']}",
                headers=non_admin_headers,
            ).status_code
            in (401, 403)
        )
        assert client.get(f"{COMMUNITY_BASE}/admin/activity", headers=non_admin_headers).status_code in (401, 403)

    # Admin report queue pagination & filtering + safe target previews (zero PII)
    r_p1 = client.get(
        f"{COMMUNITY_BASE}/admin/reports",
        headers=bearer(admin),
        params={"page": 1, "page_size": 2},
    )
    assert r_p1.status_code == 200, r_p1.text
    p1_data = r_p1.json()
    assert p1_data["total"] == 3
    assert len(p1_data["items"]) == 2
    assert p1_data["has_next"] is True
    assert p1_data["has_previous"] is False
    for item in p1_data["items"]:
        assert item["target_preview"]["exists"] is True
        assert "email" not in (item["reporter"] or {})
        assert "email" not in item["target_preview"]

    r_only_comment = client.get(
        f"{COMMUNITY_BASE}/admin/reports",
        headers=bearer(admin),
        params={"entity_type": "comment", "report_status": "pending"},
    )
    assert r_only_comment.status_code == 200
    assert r_only_comment.json()["total"] == 1
    assert r_only_comment.json()["items"][0]["id"] == rep_comment["id"]
    assert r_only_comment.json()["items"][0]["target_preview"]["post_id"] == post["id"]

    # Valid transitions: pending -> investigating -> resolved
    r_inv = client.patch(
        f"{COMMUNITY_BASE}/admin/reports/{rep_post['id']}",
        headers=bearer(admin),
        json={"report_status": "investigating", "review_notes": "Checking post media"},
    )
    assert r_inv.status_code == 200, r_inv.text
    inv_body = r_inv.json()
    assert inv_body["previous_status"] == "pending"
    assert inv_body["report"]["report_status"] == "investigating"
    assert inv_body["report"]["reviewed_by"] == admin.id
    assert inv_body["report"]["reviewed_at"] is not None
    assert inv_body["report"]["review_notes"] == "Checking post media"
    assert inv_body["audit_log_id"] > 0

    # Duplicate open report still blocked while status is 'investigating'
    assert (
        client.post(
            f"{COMMUNITY_BASE}/reports",
            headers=bearer(t2),
            json={"entity_type": "post", "entity_id": post["id"], "report_reason": "Still open"},
        ).status_code
        == 409
    )

    # Invalid transition: investigating -> pending rejected with 400
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/reports/{rep_post['id']}",
            headers=bearer(admin),
            json={"report_status": "pending"},
        ).status_code
        == 400
    )

    # Valid transition: investigating -> resolved
    r_res = client.patch(
        f"{COMMUNITY_BASE}/admin/reports/{rep_post['id']}",
        headers=bearer(admin),
        json={"report_status": "resolved", "review_notes": "Action taken"},
    )
    assert r_res.status_code == 200
    assert r_res.json()["report"]["report_status"] == "resolved"

    # Invalid transition from terminal 'resolved' -> 'dismissed' or 'investigating' rejected
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/reports/{rep_post['id']}",
            headers=bearer(admin),
            json={"report_status": "dismissed"},
        ).status_code
        == 400
    )

    # Valid transition: pending -> dismissed; terminal 'dismissed' cannot reopen
    r_dis = client.patch(
        f"{COMMUNITY_BASE}/admin/reports/{rep_user['id']}",
        headers=bearer(admin),
        json={"report_status": "dismissed", "review_notes": "No policy violation"},
    )
    assert r_dis.status_code == 200
    assert r_dis.json()["report"]["report_status"] == "dismissed"
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/admin/reports/{rep_user['id']}",
            headers=bearer(admin),
            json={"report_status": "investigating"},
        ).status_code
        == 400
    )

    # Verify users.is_active was NEVER modified by user reporting/moderation
    db_session.refresh(t1)
    assert t1.is_active is True

    # Verify AdminAuditLog rows were persisted for all 3 valid report transitions
    audit_logs = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.entity_name == "community_report")
        .all()
    )
    assert len(audit_logs) == 3
    parsed = json.loads(audit_logs[-1].details or "{}")
    assert parsed["new_status"] == "dismissed"


# =============================================================================
# 3. ADMIN POST HIDE/RESTORE, COMMENT MODERATION, THREAD PRESERVATION & ACTIVITY
# =============================================================================

def test_admin_post_hide_restore_comment_moderation_and_activity_dashboard(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, admin = actors["t1"], actors["t2"], actors["admin"]

    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Mirissa whale watching unique keyword #Mirissa", "district": "Matara"},
    ).json()
    parent_c = client.post(
        f"{COMMUNITY_BASE}/posts/{post['id']}/comments",
        headers=bearer(t1),
        json={"content": "Parent comment with violation"},
    ).json()
    reply_c = client.post(
        f"{COMMUNITY_BASE}/posts/{post['id']}/comments",
        headers=bearer(t2),
        json={"content": "Helpful child reply", "parent_comment_id": parent_c["id"]},
    ).json()
    leaf_c = client.post(
        f"{COMMUNITY_BASE}/posts/{post['id']}/comments",
        headers=bearer(t2),
        json={"content": "Standalone leaf spam comment"},
    ).json()

    # File a report on the post so we can verify moderation evidence is preserved
    report = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "post", "entity_id": post["id"], "report_reason": "Policy review"},
    ).json()

    # Admin hides post via is_published=False
    r_hide = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{post['id']}",
        headers=bearer(admin),
        json={"is_published": False, "reason": "Hidden pending policy verification"},
    )
    assert r_hide.status_code == 200, r_hide.text
    hide_body = r_hide.json()
    assert hide_body["is_published"] is False
    assert hide_body["previous_is_published"] is True
    assert hide_body["action"] == "community_post_hide"
    assert hide_body["audit_log_id"] > 0

    # Hidden post disappears from public feed, public search, and public post detail/sharing
    assert client.get(f"{COMMUNITY_BASE}/feed").json()["total"] == 0
    search_hidden = client.get(
        f"{COMMUNITY_BASE}/search", params={"q": "Mirissa", "type": "posts"}
    ).json()
    assert search_hidden["posts_total"] == 0
    assert search_hidden["posts"] == []
    assert client.get(f"{COMMUNITY_BASE}/posts/{post['id']}").status_code == 404

    # Report evidence is completely preserved
    assert db_session.get(CommunityReport, report["id"]) is not None

    # Admin restores post via is_published=True
    r_restore = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{post['id']}",
        headers=bearer(admin),
        json={"is_published": True, "reason": "Cleared after review"},
    )
    assert r_restore.status_code == 200
    assert r_restore.json()["action"] == "community_post_restore"
    assert client.get(f"{COMMUNITY_BASE}/feed").json()["total"] == 1
    assert (
        client.get(
            f"{COMMUNITY_BASE}/search", params={"q": "Mirissa", "type": "posts"}
        ).json()["posts_total"]
        == 1
    )
    assert client.get(f"{COMMUNITY_BASE}/posts/{post['id']}").status_code == 200

    # Admin removes parent comment -> redacted in place, child reply thread intact & FK-safe
    r_mod_parent = client.delete(
        f"{COMMUNITY_BASE}/admin/comments/{parent_c['id']}",
        headers=bearer(admin),
        params={"reason": "Guideline violation"},
    )
    assert r_mod_parent.status_code == 200, r_mod_parent.text
    mp_body = r_mod_parent.json()
    assert mp_body["deletion_mode"] == "redacted_thread_preserved"
    assert mp_body["audit_log_id"] > 0

    thread = client.get(f"{COMMUNITY_BASE}/posts/{post['id']}/comments").json()
    parent_node = next(c for c in thread["items"] if c["id"] == parent_c["id"])
    assert parent_node["is_redacted"] is True
    assert parent_node["content"] == "[Comment removed by moderator]"
    assert len(parent_node["replies"]) == 1
    assert parent_node["replies"][0]["id"] == reply_c["id"]

    # Admin removes unreported leaf comment -> hard deleted cleanly, comments_count accurate
    r_mod_leaf = client.delete(
        f"{COMMUNITY_BASE}/admin/comments/{leaf_c['id']}",
        headers=bearer(admin),
        params={"reason": "Spam comment"},
    )
    assert r_mod_leaf.status_code == 200, r_mod_leaf.text
    assert r_mod_leaf.json()["deletion_mode"] == "hard_deleted"
    assert r_mod_leaf.json()["comments_count"] == 2  # redacted parent + child reply
    assert db_session.get(CommunityComment, leaf_c["id"]) is None

    # Admin activity endpoint returns accurate counts, open flagged reports & recent audit actions
    r_act = client.get(f"{COMMUNITY_BASE}/admin/activity", headers=bearer(admin))
    assert r_act.status_code == 200, r_act.text
    act = r_act.json()
    assert act["total_posts"] == 1
    assert act["published_posts"] == 1
    assert act["hidden_posts"] == 0
    assert act["total_comments"] == 2
    assert act["total_reports"] == 1
    assert act["pending_reports"] == 1
    assert len(act["recent_flagged_reports"]) == 1
    assert act["recent_flagged_reports"][0]["id"] == report["id"]
    assert len(act["recent_moderation_actions"]) == 4  # hide, restore, parent mod, leaf mod
    assert act["pending_migration_037_features"] == []


# =============================================================================
# 4. ATOMIC TRANSACTION ROLLBACK CONSISTENCY (MODERATION + AUDIT LOG)
# =============================================================================

def test_moderation_and_audit_log_roll_back_atomically_on_failure(
    client: TestClient, db_session: Session, actors: dict[str, User], monkeypatch: pytest.MonkeyPatch
):
    t1, t2, admin = actors["t1"], actors["t2"], actors["admin"]

    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Atomic rollback test post"},
    ).json()
    leaf_comment = client.post(
        f"{COMMUNITY_BASE}/posts/{post['id']}/comments",
        headers=bearer(t2),
        json={"content": "Comment for rollback verification"},
    ).json()
    report = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "post", "entity_id": post["id"], "report_reason": "Audit fail test"},
    ).json()

    def _fail_audit(*_args, **_kwargs):
        raise OperationalError("INSERT admin_audit_logs", {}, Exception("simulated audit failure"))

    monkeypatch.setattr(community_moderation, "record_community_admin_audit", _fail_audit)

    # 1. Report review must roll back status + reviewed_by if audit logging fails
    with pytest.raises(OperationalError):
        client.patch(
            f"{COMMUNITY_BASE}/admin/reports/{report['id']}",
            headers=bearer(admin),
            json={"report_status": "investigating", "review_notes": "Should roll back"},
        )
    db_session.expire_all()
    rep_row = db_session.get(CommunityReport, report["id"])
    assert rep_row is not None
    assert rep_row.report_status == "pending"
    assert rep_row.reviewed_by is None

    # 2. Post hide must roll back is_published if audit logging fails
    with pytest.raises(OperationalError):
        client.patch(
            f"{COMMUNITY_BASE}/admin/posts/{post['id']}",
            headers=bearer(admin),
            json={"is_published": False, "reason": "Should roll back"},
        )
    db_session.expire_all()
    post_row = db_session.get(CommunityPost, post["id"])
    assert post_row is not None
    assert post_row.is_published is True

    # 3. Comment moderation delete must roll back comment deletion if audit logging fails
    with pytest.raises(OperationalError):
        client.delete(
            f"{COMMUNITY_BASE}/admin/comments/{leaf_comment['id']}",
            headers=bearer(admin),
            params={"reason": "Should roll back"},
        )
    db_session.expire_all()
    assert db_session.get(CommunityComment, leaf_comment["id"]) is not None
    assert db_session.query(AdminAuditLog).count() == 0


# =============================================================================
# 5. STEP 5A SECURITY FIXES: ADMIN-HIDDEN & AUTHOR-REMOVED POST PROTECTION + ORM
# =============================================================================

def test_author_cannot_republish_edit_or_delete_admin_hidden_post(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, admin = actors["t1"], actors["admin"]

    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Original community post #Kandy", "district": "Kandy"},
    ).json()
    post_id = post["id"]

    # Admin hides post -> sets is_published=False AND moderation_status='hidden_by_admin'
    r_hide = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{post_id}",
        headers=bearer(admin),
        json={"is_published": False, "reason": "Policy violation"},
    )
    assert r_hide.status_code == 200, r_hide.text

    db_session.expire_all()
    row = db_session.get(CommunityPost, post_id)
    assert row is not None
    assert row.is_published is False
    assert row.moderation_status == "hidden_by_admin"

    # Author CANNOT republish an admin-hidden post
    r_repub = client.patch(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(t1),
        json={"is_published": True},
    )
    assert r_repub.status_code == 403

    # Author CANNOT edit caption/location of an admin-hidden post
    r_edit = client.patch(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(t1),
        json={"content": "Trying to rewrite moderated post"},
    )
    assert r_edit.status_code == 403

    # Author CANNOT delete an admin-hidden post
    r_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(t1),
    )
    assert r_del.status_code == 403

    # Verify DB state stayed locked as hidden_by_admin
    db_session.expire_all()
    row = db_session.get(CommunityPost, post_id)
    assert row is not None
    assert row.is_published is False
    assert row.moderation_status == "hidden_by_admin"

    # Authorized admin CAN restore post -> returns to active & published + audit logged
    r_restore = client.patch(
        f"{COMMUNITY_BASE}/admin/posts/{post_id}",
        headers=bearer(admin),
        json={"is_published": True, "reason": "Appeal accepted"},
    )
    assert r_restore.status_code == 200, r_restore.text

    db_session.expire_all()
    row = db_session.get(CommunityPost, post_id)
    assert row is not None
    assert row.is_published is True
    assert row.moderation_status == "active"

    audit = db_session.get(AdminAuditLog, r_restore.json()["audit_log_id"])
    assert audit is not None
    audit_details = json.loads(audit.details or "{}")
    assert audit_details["previous_moderation_status"] == "hidden_by_admin"
    assert audit_details["new_moderation_status"] == "active"


def test_author_cannot_restore_removed_by_author_post_while_normal_draft_works(
    client: TestClient, db_session: Session, actors: dict[str, User]
):
    t1, t2, admin = actors["t1"], actors["t2"], actors["admin"]

    # 1. Normal author draft (is_published=False, moderation_status='active') STILL works
    draft = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "My private draft #Ella", "is_published": False},
    ).json()
    draft_row = db_session.get(CommunityPost, draft["id"])
    assert draft_row is not None
    assert draft_row.moderation_status == "active"

    r_pub_draft = client.patch(
        f"{COMMUNITY_BASE}/posts/{draft['id']}",
        headers=bearer(t1),
        json={"content": "Ready to publish #Ella", "is_published": True},
    )
    assert r_pub_draft.status_code == 200, r_pub_draft.text
    assert r_pub_draft.json()["is_published"] is True

    # 2. Reported post deleted by author -> soft-unpublished with moderation_status='removed_by_author'
    r_rep = client.post(
        f"{COMMUNITY_BASE}/reports",
        headers=bearer(t2),
        json={"entity_type": "post", "entity_id": draft["id"], "report_reason": "Spam report"},
    )
    assert r_rep.status_code == 201

    r_soft_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{draft['id']}",
        headers=bearer(t1),
    )
    assert r_soft_del.status_code == 200
    assert r_soft_del.json()["deletion_mode"] == "soft_unpublished_moderation_preserved"

    db_session.expire_all()
    removed_row = db_session.get(CommunityPost, draft["id"])
    assert removed_row is not None
    assert removed_row.is_published is False
    assert removed_row.moderation_status == "removed_by_author"

    # Author CANNOT restore or edit a removed_by_author post
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{draft['id']}",
            headers=bearer(t1),
            json={"is_published": True},
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{draft['id']}",
            headers=bearer(t1),
            json={"content": "Sneaky edit"},
        ).status_code
        == 403
    )

    # 3. Verify structured CommunityComment.status persists on moderator & author redactions
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Comment status check post"},
    ).json()
    parent_c = client.post(
        f"{COMMUNITY_BASE}/posts/{p2['id']}/comments",
        headers=bearer(t1),
        json={"content": "Parent comment"},
    ).json()
    client.post(
        f"{COMMUNITY_BASE}/posts/{p2['id']}/comments",
        headers=bearer(t2),
        json={"content": "Reply keeping parent intact", "parent_comment_id": parent_c["id"]},
    )
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/admin/comments/{parent_c['id']}",
            headers=bearer(admin),
            params={"reason": "Moderation removal"},
        ).status_code
        == 200
    )
    db_session.expire_all()
    c_row = db_session.get(CommunityComment, parent_c["id"])
    assert c_row is not None
    assert c_row.status == "removed_by_moderator"
