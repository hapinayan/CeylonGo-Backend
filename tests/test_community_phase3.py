"""
Automated Phase 3 Tests — Travel Community & Social Feed
(Likes, Threaded Comments & Replies, Private Saved Posts, Notifications & Rate Limiting).

Runs against an isolated in-memory SQLite database with PRAGMA foreign_keys=ON
(enforcing SQL Server-equivalent ON DELETE NO ACTION on community_comments.parent_comment_id
and community_saved_posts.post_id).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.community import (
    CommunityComment,
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
from app.models.shared import ListingReference, Notification
from app.models.user import Base, User
from app.services import community_engagement, community_posts

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
        CommunityUserProfile.__table__,
        CommunityPost.__table__,
        CommunityPostMedia.__table__,
        CommunityPostLike.__table__,
        CommunityComment.__table__,
        CommunitySavedPost.__table__,
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
    community_engagement.reset_community_engagement_limiters()
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        community_posts.reset_community_post_limiters()
        community_engagement.reset_community_engagement_limiters()


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def actors_and_posts(db_session: Session, client: TestClient) -> dict:
    u1 = User(full_name="Kavinda Perera", email="kavinda.p3@example.com", role="traveler", is_active=True)
    u2 = User(full_name="Tharushi Silva", email="tharushi.p3@example.com", role="traveler", is_active=True)
    u3 = User(full_name="Nuwan Bandara", email="nuwan.p3@example.com", role="traveler", is_active=True)
    admin = User(full_name="Admin Mod", email="admin.p3@example.com", role="admin", is_active=True)
    db_session.add_all([u1, u2, u3, admin])
    db_session.commit()

    p1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(u1),
        json={"content": "Sunset over Mirissa Coconut Tree Hill #Mirissa"},
    ).json()
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(u2),
        json={"content": "Nuwara Eliya tea estate #NuwaraEliya"},
    ).json()
    draft = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(u1),
        json={"content": "Private draft post", "is_published": False},
    ).json()

    return {
        "u1": u1,
        "u2": u2,
        "u3": u3,
        "admin": admin,
        "p1_id": p1["id"],
        "p2_id": p2["id"],
        "draft_id": draft["id"],
    }


# =============================================================================
# 1. LIKES: IDEMPOTENCY, COUNTER ACCURACY, UNAUTH, UNPUBLISHED & NOTIFICATIONS
# =============================================================================

def test_like_unlike_idempotency_counter_accuracy_and_notifications(
    client: TestClient, db_session: Session, actors_and_posts: dict
):
    u1, u2, u3 = actors_and_posts["u1"], actors_and_posts["u2"], actors_and_posts["u3"]
    p1_id, draft_id = actors_and_posts["p1_id"], actors_and_posts["draft_id"]

    # Unauthenticated likes/unlikes rejected
    assert client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like").status_code in (401, 403)
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like").status_code in (401, 403)

    # Cannot like unpublished draft (even as author -> 404)
    assert client.put(f"{COMMUNITY_BASE}/posts/{draft_id}/like", headers=bearer(u1)).status_code == 404
    assert client.put(f"{COMMUNITY_BASE}/posts/{draft_id}/like", headers=bearer(u2)).status_code == 404

    # u2 likes u1's post -> count=1 + 1 notification to u1
    r1 = client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u2))
    assert r1.status_code == 200, r1.text
    assert r1.json() == {"post_id": p1_id, "liked": True, "likes_count": 1}

    # Duplicate retry by u2 is idempotent -> count stays 1, NO duplicate notification
    r1_dup = client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u2))
    assert r1_dup.status_code == 200
    assert r1_dup.json() == {"post_id": p1_id, "liked": True, "likes_count": 1}

    notifs = db_session.query(Notification).filter_by(user_id=u1.id).all()
    assert len(notifs) == 1
    assert notifs[0].notification_type == "community_post_like"
    assert notifs[0].link_url == f"/community/posts/{p1_id}"

    # u3 also likes p1 within 30s -> count=2 AND distinct notification for u3 (not collapsed!)
    r2 = client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u3))
    assert r2.status_code == 200
    assert r2.json()["likes_count"] == 2
    assert db_session.query(Notification).filter_by(user_id=u1.id).count() == 2

    # Self-like by u1 -> count=3, ZERO self-notification created
    r_self = client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u1))
    assert r_self.status_code == 200
    assert r_self.json()["likes_count"] == 3
    assert db_session.query(Notification).filter_by(user_id=u1.id).count() == 2

    # Unlike by u2 -> count=2; repeated unlike stays 2 (never negative)
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "liked": False,
        "likes_count": 2,
    }
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "liked": False,
        "likes_count": 2,
    }
    # Author & u3 unlike -> count=0, repeated unlike stays 0
    client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u1))
    client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u3))
    r_zero = client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u3))
    assert r_zero.status_code == 200
    assert r_zero.json()["likes_count"] == 0


# =============================================================================
# 2. COMMENTS, THREADED REPLIES, OWNERSHIP, SAFE PARENT DELETION & NOTIFICATIONS
# =============================================================================

def test_comments_replies_pagination_cross_post_validation_and_notifications(
    client: TestClient, db_session: Session, actors_and_posts: dict
):
    u1, u2, u3 = actors_and_posts["u1"], actors_and_posts["u2"], actors_and_posts["u3"]
    p1_id, p2_id, draft_id = (
        actors_and_posts["p1_id"],
        actors_and_posts["p2_id"],
        actors_and_posts["draft_id"],
    )

    # Unauthenticated comment creation rejected
    assert client.post(f"{COMMUNITY_BASE}/posts/{p1_id}/comments", json={"content": "Hi"}).status_code in (401, 403)

    # Unpublished draft: public cannot view or comment (404)
    assert client.get(f"{COMMUNITY_BASE}/posts/{draft_id}/comments").status_code == 404
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{draft_id}/comments",
            headers=bearer(u2),
            json={"content": "Sneaky comment"},
        ).status_code
        == 404
    )

    # u2 posts sanitized top-level comment on u1's p1 -> notifies u1
    r_c1 = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u2),
        json={"content": "<b>Amazing</b> view!<script>bad()</script>"},
    )
    assert r_c1.status_code == 201, r_c1.text
    c1 = r_c1.json()
    assert c1["content"] == "Amazing view!"
    assert "email" not in c1["author"]

    # u1 self-comment on p1 -> zero self-notification
    r_c2 = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u1),
        json={"content": "Thanks everyone!"},
    )
    assert r_c2.status_code == 201
    c2 = r_c2.json()

    # Cross-post parent_comment_id rejected (400)
    r_cross = client.post(
        f"{COMMUNITY_BASE}/posts/{p2_id}/comments",
        headers=bearer(u3),
        json={"content": "Cross-post reply", "parent_comment_id": c1["id"]},
    )
    assert r_cross.status_code == 400

    # u3 replies to u2's comment c1 on u1's post p1 -> notifies u2 (reply) AND u1 (post comment)
    r_rep = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u3),
        json={"content": "Agreed, best sunrise spot!", "parent_comment_id": c1["id"]},
    )
    assert r_rep.status_code == 201, r_rep.text
    rep = r_rep.json()
    assert rep["parent_comment_id"] == c1["id"]

    u2_notifs = db_session.query(Notification).filter_by(user_id=u2.id).all()
    assert len(u2_notifs) == 1
    assert u2_notifs[0].notification_type == "community_comment_reply"

    u1_notifs = db_session.query(Notification).filter_by(user_id=u1.id).all()
    # 1 from c1 (u2) + 1 from rep (u3); 0 from c2 (u1 self)
    assert len(u1_notifs) == 2

    # Paginated listing of top-level comments includes nested reply thread & accurate counts
    r_list = client.get(f"{COMMUNITY_BASE}/posts/{p1_id}/comments", params={"page": 1, "page_size": 1})
    assert r_list.status_code == 200
    body = r_list.json()
    assert body["total"] == 2
    assert body["total_comments_on_post"] == 3
    assert body["has_next"] is True
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == c1["id"]
    assert body["items"][0]["reply_count"] == 1
    assert body["items"][0]["replies"][0]["id"] == rep["id"]

    # Page 2 returns c2
    r_list2 = client.get(f"{COMMUNITY_BASE}/posts/{p1_id}/comments", params={"page": 2, "page_size": 1})
    assert r_list2.status_code == 200
    assert r_list2.json()["items"][0]["id"] == c2["id"]
    assert r_list2.json()["has_previous"] is True


def test_comment_edit_delete_ownership_and_fk_safe_parent_deletion(
    client: TestClient, db_session: Session, actors_and_posts: dict
):
    u1, u2, u3, admin = (
        actors_and_posts["u1"],
        actors_and_posts["u2"],
        actors_and_posts["u3"],
        actors_and_posts["admin"],
    )
    p1_id = actors_and_posts["p1_id"]

    parent = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u2),
        json={"content": "Original parent comment"},
    ).json()
    reply = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u3),
        json={"content": "Reply to parent", "parent_comment_id": parent["id"]},
    ).json()
    leaf = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u2),
        json={"content": "Standalone leaf comment"},
    ).json()

    # Non-owner cannot edit or delete (403)
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/comments/{parent['id']}",
            headers=bearer(u3),
            json={"content": "Hacked"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/comments/{parent['id']}",
            headers=bearer(u3),
        ).status_code
        == 403
    )

    # Owner edits own comment
    r_edit = client.patch(
        f"{COMMUNITY_BASE}/comments/{parent['id']}",
        headers=bearer(u2),
        json={"content": "Updated parent comment"},
    )
    assert r_edit.status_code == 200
    assert r_edit.json()["content"] == "Updated parent comment"
    assert r_edit.json()["updated_at"] is not None

    # Deleting parent comment that has a child reply -> redacts in place (zero SQL Server Error 547!)
    r_del_parent = client.delete(
        f"{COMMUNITY_BASE}/comments/{parent['id']}",
        headers=bearer(u2),
    )
    assert r_del_parent.status_code == 200, r_del_parent.text
    assert r_del_parent.json()["deletion_mode"] == "redacted_thread_preserved"
    assert r_del_parent.json()["comments_count"] == 3

    # Redacted comment cannot be re-edited (409) and child reply remains intact in thread
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/comments/{parent['id']}",
            headers=bearer(u2),
            json={"content": "Try un-deleting"},
        ).status_code
        == 409
    )
    thread = client.get(f"{COMMUNITY_BASE}/posts/{p1_id}/comments").json()["items"]
    redacted_parent = next(c for c in thread if c["id"] == parent["id"])
    assert redacted_parent["is_redacted"] is True
    assert redacted_parent["replies"][0]["id"] == reply["id"]

    # Deleting leaf comment with no replies & no reports -> hard delete & comments_count decrements to 2
    r_del_leaf = client.delete(
        f"{COMMUNITY_BASE}/comments/{leaf['id']}",
        headers=bearer(u2),
    )
    assert r_del_leaf.status_code == 200
    assert r_del_leaf.json()["deletion_mode"] == "hard_deleted"
    assert r_del_leaf.json()["comments_count"] == 2
    assert db_session.get(CommunityComment, leaf["id"]) is None

    # Reported comment cannot be deleted by non-admin author (409), but CAN be moderated by admin
    db_session.add(
        CommunityReport(
            reporter_id=u1.id,
            entity_type="comment",
            entity_id=reply["id"],
            report_reason="abuse",
        )
    )
    db_session.commit()
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/comments/{reply['id']}",
            headers=bearer(u3),
        ).status_code
        == 409
    )
    r_admin_mod = client.delete(
        f"{COMMUNITY_BASE}/comments/{reply['id']}",
        headers=bearer(admin),
    )
    assert r_admin_mod.status_code == 200
    assert r_admin_mod.json()["deletion_mode"] == "redacted_thread_preserved"


# =============================================================================
# 3. PRIVATE SAVED POSTS (ISOLATION, IDEMPOTENCY, UNPUBLISHED EXCLUSION)
# =============================================================================

def test_private_saved_posts_isolation_idempotency_and_unpublished_exclusion(
    client: TestClient, actors_and_posts: dict
):
    u1, u2 = actors_and_posts["u1"], actors_and_posts["u2"]
    p1_id, p2_id, draft_id = (
        actors_and_posts["p1_id"],
        actors_and_posts["p2_id"],
        actors_and_posts["draft_id"],
    )

    # Unauthenticated save / unsave / list rejected
    assert client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/save").status_code in (401, 403)
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/save").status_code in (401, 403)
    assert client.get(f"{COMMUNITY_BASE}/saved").status_code in (401, 403)

    # Cannot bookmark unpublished post (404)
    assert client.put(f"{COMMUNITY_BASE}/posts/{draft_id}/save", headers=bearer(u2)).status_code == 404

    # u2 saves p1 twice (idempotent) and p2 once
    assert client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/save", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "saved": True,
    }
    assert client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/save", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "saved": True,
    }
    assert client.put(f"{COMMUNITY_BASE}/posts/{p2_id}/save", headers=bearer(u2)).status_code == 200

    # u2 sees own 2 saved posts; u1 sees 0 (strict bookmark privacy!)
    r_u2 = client.get(f"{COMMUNITY_BASE}/saved", headers=bearer(u2))
    assert r_u2.status_code == 200
    assert r_u2.json()["total"] == 2
    assert all(item["is_saved"] is True for item in r_u2.json()["items"])

    r_u1 = client.get(f"{COMMUNITY_BASE}/saved", headers=bearer(u1))
    assert r_u1.status_code == 200
    assert r_u1.json()["total"] == 0
    assert r_u1.json()["items"] == []

    # If u1 unpublishes p1, p1 disappears immediately from u2's saved list
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{p1_id}",
            headers=bearer(u1),
            json={"is_published": False},
        ).status_code
        == 200
    )
    r_u2_after = client.get(f"{COMMUNITY_BASE}/saved", headers=bearer(u2))
    assert r_u2_after.json()["total"] == 1
    assert [i["id"] for i in r_u2_after.json()["items"]] == [p2_id]

    # Unsave is idempotent (even on unpublished post)
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/save", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "saved": False,
    }
    assert client.delete(f"{COMMUNITY_BASE}/posts/{p1_id}/save", headers=bearer(u2)).json() == {
        "post_id": p1_id,
        "saved": False,
    }


# =============================================================================
# 4. TRANSACTION FAILURE ROLLBACK & RATE LIMITING
# =============================================================================

def test_failed_transaction_rolls_back_engagement_and_notification_atomically(
    client: TestClient, db_session: Session, actors_and_posts: dict
):
    u1, u2 = actors_and_posts["u1"], actors_and_posts["u2"]
    p1_id = actors_and_posts["p1_id"]

    with patch(
        "app.services.community_engagement._commit",
        side_effect=OperationalError("stmt", {}, Exception("simulated commit failure")),
    ):
        with pytest.raises(OperationalError):
            client.put(f"{COMMUNITY_BASE}/posts/{p1_id}/like", headers=bearer(u2))

    db_session.expire_all()
    assert db_session.query(CommunityPostLike).filter_by(post_id=p1_id).count() == 0
    assert db_session.get(CommunityPost, p1_id).likes_count == 0
    assert db_session.query(Notification).filter_by(user_id=u1.id).count() == 0


def test_engagement_rate_limiting_enforced_with_429(
    client: TestClient, actors_and_posts: dict, monkeypatch: pytest.MonkeyPatch
):
    u2 = actors_and_posts["u2"]
    p1_id = actors_and_posts["p1_id"]

    monkeypatch.setattr(community_engagement.community_comment_limiter, "max_requests", 2)
    community_engagement.reset_community_engagement_limiters()

    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
            headers=bearer(u2),
            json={"content": "Comment 1"},
        ).status_code
        == 201
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
            headers=bearer(u2),
            json={"content": "Comment 2"},
        ).status_code
        == 201
    )
    r_429 = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/comments",
        headers=bearer(u2),
        json={"content": "Comment 3 throttled"},
    )
    assert r_429.status_code == 429
    assert "Retry-After" in r_429.headers
