"""Focused isolated unit/integration tests for Academic MVP Travel Community & Social Feed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPAdminAuditLog,
    MVPBase,
    MVPCommunityPost,
    MVPProviderProfile,
    MVPUser,
)
from app.services import mvp_community as community_service

API = "/api/v1"


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (16, 16), color=(0, 102, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def community_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'community_mvp.sqlite'}")
    MVPBase.metadata.create_all(engine)
    monkeypatch.setenv("COMMUNITY_UPLOAD_DIR", str(tmp_path / "uploads"))
    app = create_mvp_app()

    def override_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_mvp_db] = override_db

    with Session(engine) as db:
        traveler1 = MVPUser(
            email="traveler1@example.com", role="traveler", full_name="Nimal Perera",
            is_active=True, hashed_password="unused",
        )
        traveler2 = MVPUser(
            email="traveler2@example.com", role="traveler", full_name="Sithumi Fernando",
            is_active=True, hashed_password="unused",
        )
        provider_ok = MVPUser(
            email="provider.ok@example.com", role="provider", full_name="Kandy Craft Host",
            is_active=True, hashed_password="unused",
        )
        provider_pending = MVPUser(
            email="provider.pending@example.com", role="provider", full_name="Pending Host",
            is_active=True, hashed_password="unused",
        )
        admin = MVPUser(
            email="admin@example.com", role="admin", full_name="System Admin",
            is_active=True, hashed_password="unused",
        )
        super_admin = MVPUser(
            email="superadmin@example.com", role="super_admin", full_name="Super Admin",
            is_active=True, hashed_password="unused",
        )
        db.add_all([traveler1, traveler2, provider_ok, provider_pending, admin, super_admin])
        db.flush()

        db.add_all([
            MVPProviderProfile(
                user_id=provider_ok.id, business_name="Kandy Spice & Craft Studio",
                phone="0771112222", district="Kandy", verification_status="approved",
            ),
            MVPProviderProfile(
                user_id=provider_pending.id, business_name="Unapproved Studio",
                phone="0773334444", district="Galle", verification_status="pending",
            ),
        ])

        # Seed 20 historical demo-compatible community posts
        base_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)
        seeded = [
            MVPCommunityPost(
                author_id=traveler1.id,
                body=f"[DEMO post {i:02d}] Exploring Sri Lanka heritage route stop {i}.",
                visibility="visible",
                created_at=base_time + timedelta(minutes=i),
                updated_at=base_time + timedelta(minutes=i),
            )
            for i in range(1, 21)
        ]
        db.add_all(seeded)
        db.commit()

        tokens = {
            "traveler1": {"Authorization": f"Bearer {create_mvp_access_token(traveler1.id, traveler1.email, traveler1.role)}"},
            "traveler2": {"Authorization": f"Bearer {create_mvp_access_token(traveler2.id, traveler2.email, traveler2.role)}"},
            "provider_ok": {"Authorization": f"Bearer {create_mvp_access_token(provider_ok.id, provider_ok.email, provider_ok.role)}"},
            "provider_pending": {"Authorization": f"Bearer {create_mvp_access_token(provider_pending.id, provider_pending.email, provider_pending.role)}"},
            "admin": {"Authorization": f"Bearer {create_mvp_access_token(admin.id, admin.email, admin.role)}"},
            "super_admin": {"Authorization": f"Bearer {create_mvp_access_token(super_admin.id, super_admin.email, super_admin.role)}"},
        }
        ids = {
            "traveler1": traveler1.id,
            "traveler2": traveler2.id,
            "provider_ok": provider_ok.id,
            "seeded_last_id": seeded[-1].id,
        }

    with TestClient(app) as client:
        yield client, tokens, ids, engine
    engine.dispose()


def test_public_feed_pagination_sorting_and_seeded_posts(community_env):
    client, _, ids, _ = community_env

    # Guest access to paginated feed
    res1 = client.get(f"{API}/community/posts", params={"page": 1, "page_size": 10})
    assert res1.status_code == 200
    body1 = res1.json()
    assert body1["total"] == 20
    assert body1["page"] == 1
    assert body1["page_size"] == 10
    assert body1["total_pages"] == 2
    assert len(body1["items"]) == 10
    # Latest post appears first
    assert body1["items"][0]["id"] == ids["seeded_last_id"]
    assert body1["items"][0]["author_name"] == "Nimal Perera"
    assert body1["items"][0]["author_type"] == "traveler"
    assert body1["items"][0]["likes_count"] == 0
    assert body1["items"][0]["comments_count"] == 0
    assert body1["items"][0]["viewer_has_liked"] is False

    # Alias /feed matches /posts
    res_alias = client.get(f"{API}/community/feed", params={"page": 2, "page_size": 10})
    assert res_alias.status_code == 200
    assert len(res_alias.json()["items"]) == 10

    # Single post details
    detail = client.get(f"{API}/community/posts/{ids['seeded_last_id']}")
    assert detail.status_code == 200
    assert detail.json()["author"]["full_name"] == "Nimal Perera"


def test_traveler_and_provider_post_lifecycle_and_ownership(community_env):
    client, tokens, ids, _ = community_env

    # Traveler creates a post with HTML tags stripped cleanly
    t_res = client.post(
        f"{API}/community/posts",
        headers=tokens["traveler1"],
        json={"body": "<script>alert(1)</script>Sunrise hike at Pidurangala!"},
    )
    assert t_res.status_code == 201, t_res.text
    t_post = t_res.json()
    assert t_post["body"] == "Sunrise hike at Pidurangala!"
    assert t_post["author_type"] == "traveler"

    # Approved provider creates a post with business profile metadata exposed
    p_res = client.post(
        f"{API}/community/posts",
        headers=tokens["provider_ok"],
        json={"body": "Fresh organic cinnamon harvest open for tastings in Kandy."},
    )
    assert p_res.status_code == 201, p_res.text
    p_post = p_res.json()
    assert p_post["author_type"] == "provider"
    assert p_post["author"]["provider_business_name"] == "Kandy Spice & Craft Studio"
    assert p_post["author"]["is_verified_provider"] is True

    # Pending unapproved provider is rejected (403)
    bad_p = client.post(
        f"{API}/community/posts",
        headers=tokens["provider_pending"],
        json={"body": "Trying to post before verification."},
    )
    assert bad_p.status_code == 403

    # Filter feed by author_type=provider
    provider_feed = client.get(f"{API}/community/posts", params={"author_type": "provider"})
    assert provider_feed.status_code == 200
    assert provider_feed.json()["total"] == 1
    assert provider_feed.json()["items"][0]["id"] == p_post["id"]

    # Other user cannot edit or delete traveler1's post
    assert client.patch(
        f"{API}/community/posts/{t_post['id']}",
        headers=tokens["traveler2"],
        json={"body": "Tampered"},
    ).status_code == 403
    assert client.delete(
        f"{API}/community/posts/{t_post['id']}",
        headers=tokens["traveler2"],
    ).status_code == 403

    # Owner edits post and views own posts
    edited = client.patch(
        f"{API}/community/posts/{t_post['id']}",
        headers=tokens["traveler1"],
        json={"body": "Updated: Pidurangala Rock at 5:30 AM!"},
    )
    assert edited.status_code == 200
    assert edited.json()["body"] == "Updated: Pidurangala Rock at 5:30 AM!"

    mine = client.get(f"{API}/community/posts/me", headers=tokens["traveler1"])
    assert mine.status_code == 200
    assert mine.json()["total"] == 21

    # Owner deletes post -> no longer publicly visible
    deleted = client.delete(f"{API}/community/posts/{t_post['id']}", headers=tokens["traveler1"])
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"{API}/community/posts/{t_post['id']}").status_code == 404


def test_post_photo_upload_validation_serving_and_delete(community_env):
    client, tokens, _, _ = community_env

    post = client.post(
        f"{API}/community/posts",
        headers=tokens["traveler1"],
        json={"body": "Galle Fort lighthouse golden hour"},
    ).json()
    post_id = post["id"]

    # Non-owner blocked from uploading
    assert client.post(
        f"{API}/community/posts/{post_id}/media",
        headers=tokens["traveler2"],
        files={"file": ("photo.png", _png_bytes(), "image/png")},
    ).status_code == 403

    # Invalid image bytes rejected
    bad = client.post(
        f"{API}/community/posts/{post_id}/media",
        headers=tokens["traveler1"],
        files={"file": ("fake.png", b"not-a-real-image", "image/png")},
    )
    assert bad.status_code == 400

    # Valid PNG uploaded & EXIF-stripped
    up = client.post(
        f"{API}/community/posts/{post_id}/media",
        headers=tokens["traveler1"],
        data={"alt_text": "Galle lighthouse"},
        files={"file": ("lighthouse.png", _png_bytes(), "image/png")},
    )
    assert up.status_code == 201, up.text
    media = up.json()
    assert media["alt_text"] == "Galle lighthouse"
    assert media["image_url"] == f"{API}/community/posts/{post_id}/media/{media['id']}"

    # Feed exposes uploaded photo URL
    detail = client.get(f"{API}/community/posts/{post_id}").json()
    assert len(detail["media"]) == 1

    # Public photo serving succeeds with nosniff header
    served = client.get(media["image_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"

    # Owner deletes photo
    rem = client.delete(media["image_url"], headers=tokens["traveler1"])
    assert rem.status_code == 200
    assert client.get(media["image_url"]).status_code == 404


def test_likes_comments_and_saved_posts(community_env):
    client, tokens, ids, _ = community_env
    post_id = ids["seeded_last_id"]

    # Idempotent like prevents duplicates; viewer_has_liked reflects accurately
    l1 = client.put(f"{API}/community/posts/{post_id}/like", headers=tokens["traveler2"])
    l2 = client.put(f"{API}/community/posts/{post_id}/like", headers=tokens["traveler2"])
    assert l1.status_code == 200 and l1.json() == {"post_id": post_id, "liked": True, "likes_count": 1}
    assert l2.status_code == 200 and l2.json() == {"post_id": post_id, "liked": True, "likes_count": 1}

    feed_viewer = client.get(f"{API}/community/posts/{post_id}", headers=tokens["traveler2"]).json()
    assert feed_viewer["viewer_has_liked"] is True
    assert feed_viewer["likes_count"] == 1

    # Toggle like endpoint works for backward compatibility
    tog = client.post(f"{API}/community/posts/{post_id}/like", headers=tokens["traveler2"])
    assert tog.status_code == 200 and tog.json()["liked"] is False
    assert tog.json()["likes_count"] == 0

    # Re-like + unlike via DELETE
    assert client.put(f"{API}/community/posts/{post_id}/like", headers=tokens["traveler2"]).json()["likes_count"] == 1
    assert client.delete(f"{API}/community/posts/{post_id}/like", headers=tokens["traveler2"]).json()["likes_count"] == 0

    # Save / unsave posts
    s1 = client.put(f"{API}/community/posts/{post_id}/save", headers=tokens["traveler2"])
    assert s1.status_code == 200 and s1.json() == {"post_id": post_id, "saved": True}
    saved_list = client.get(f"{API}/community/saved", headers=tokens["traveler2"])
    assert saved_list.status_code == 200
    assert saved_list.json()["total"] == 1
    assert saved_list.json()["items"][0]["viewer_has_saved"] is True
    assert client.delete(f"{API}/community/posts/{post_id}/save", headers=tokens["traveler2"]).json()["saved"] is False
    assert client.get(f"{API}/community/saved", headers=tokens["traveler2"]).json()["total"] == 0

    # Comments: add, paginated list, edit own, unauthorized edit rejected, delete own
    c_res = client.post(
        f"{API}/community/posts/{post_id}/comments",
        headers=tokens["traveler2"],
        json={"body": "Do buses run there early in the morning?"},
    )
    assert c_res.status_code == 201
    cid = c_res.json()["id"]

    comments = client.get(f"{API}/community/posts/{post_id}/comments", params={"page": 1, "page_size": 10})
    assert comments.status_code == 200
    assert comments.json()["total"] == 1
    assert comments.json()["items"][0]["author_name"] == "Sithumi Fernando"

    assert client.patch(
        f"{API}/community/comments/{cid}",
        headers=tokens["traveler1"],
        json={"body": "Not mine"},
    ).status_code == 403

    c_edit = client.patch(
        f"{API}/community/comments/{cid}",
        headers=tokens["traveler2"],
        json={"body": "Edited: any tuk-tuks at 5 AM?"},
    )
    assert c_edit.status_code == 200 and "5 AM" in c_edit.json()["body"]

    c_del = client.delete(f"{API}/community/comments/{cid}", headers=tokens["traveler2"])
    assert c_del.status_code == 200
    assert client.get(f"{API}/community/posts/{post_id}/comments").json()["total"] == 0


def test_reports_and_admin_moderation_posts_and_comments(community_env):
    client, tokens, _, engine = community_env

    post = client.post(
        f"{API}/community/posts",
        headers=tokens["traveler1"],
        json={"body": "Questionable spam link promotion."},
    ).json()
    post_id = post["id"]

    comment = client.post(
        f"{API}/community/posts/{post_id}/comments",
        headers=tokens["traveler2"],
        json={"body": "Abusive reply comment."},
    ).json()
    comment_id = comment["id"]

    # Self-report blocked
    assert client.post(
        f"{API}/community/reports",
        headers=tokens["traveler1"],
        json={"entity_type": "post", "entity_id": post_id, "reason": "Spam"},
    ).status_code == 422

    # Traveler2 reports post; duplicate report rejected (409)
    rep = client.post(
        f"{API}/community/reports",
        headers=tokens["traveler2"],
        json={"entity_type": "post", "entity_id": post_id, "reason": "Spam link", "description": "Promotional spam"},
    )
    assert rep.status_code == 201, rep.text
    report_id = rep.json()["id"]
    assert client.post(
        f"{API}/community/reports",
        headers=tokens["traveler2"],
        json={"entity_type": "post", "entity_id": post_id, "reason": "Spam again"},
    ).status_code == 409

    # Non-admin cannot access admin moderation endpoints
    assert client.get(f"{API}/admin/community/reports", headers=tokens["traveler1"]).status_code == 403
    assert client.patch(
        f"{API}/admin/community/comments/{comment_id}",
        headers=tokens["traveler1"],
        json={"visibility": "hidden", "reason": "Not admin"},
    ).status_code == 403

    # Admin lists reports & resolves while hiding post
    q = client.get(f"{API}/admin/community/reports", headers=tokens["admin"], params={"status": "pending"})
    assert q.status_code == 200 and q.json()["total"] == 1

    rev = client.patch(
        f"{API}/admin/community/reports/{report_id}",
        headers=tokens["admin"],
        json={"status": "resolved", "review_notes": "Hidden due to spam.", "action_visibility": "hidden"},
    )
    assert rev.status_code == 200 and rev.json()["status"] == "resolved"
    assert client.get(f"{API}/community/posts/{post_id}").status_code == 404

    # Admin moderates comment and restores/hides post via direct admin endpoint
    c_mod = client.patch(
        f"{API}/admin/community/comments/{comment_id}",
        headers=tokens["admin"],
        json={"visibility": "deleted", "reason": "Abusive language removed."},
    )
    assert c_mod.status_code == 200 and c_mod.json()["visibility"] == "deleted"

    # Author sees own hidden post in /posts/me but cannot edit hidden post
    assert client.patch(
        f"{API}/community/posts/{post_id}",
        headers=tokens["traveler1"],
        json={"body": "Trying to bypass moderation"},
    ).status_code == 403

    with Session(engine) as db:
        logs = db.scalars(
            select(MVPAdminAuditLog).where(MVPAdminAuditLog.subject_post_id == post_id)
        ).all()
        assert len(logs) >= 2
        assert all(log.action == "post_moderation" for log in logs)


def test_graceful_degradation_when_extension_tables_absent(community_env, monkeypatch):
    client, tokens, ids, _ = community_env

    monkeypatch.setattr(community_service, "media_installed", lambda _db: False)
    monkeypatch.setattr(community_service, "saved_posts_installed", lambda _db: False)
    monkeypatch.setattr(community_service, "reports_installed", lambda _db: False)

    # Core feed reads still return 200 with empty media
    feed = client.get(f"{API}/community/posts", headers=tokens["traveler1"])
    assert feed.status_code == 200
    assert feed.json()["total"] == 20
    assert feed.json()["items"][0]["media"] == []

    # Extension-dependent writes return explicit 503 awaiting 1.0.5 migration approval
    assert client.post(
        f"{API}/community/posts/{ids['seeded_last_id']}/media",
        headers=tokens["traveler1"],
        files={"file": ("photo.png", _png_bytes(), "image/png")},
    ).status_code == 503
    assert client.put(
        f"{API}/community/posts/{ids['seeded_last_id']}/save",
        headers=tokens["traveler2"],
    ).status_code == 503
    assert client.post(
        f"{API}/community/reports",
        headers=tokens["traveler2"],
        json={"entity_type": "post", "entity_id": ids["seeded_last_id"], "reason": "Test reason"},
    ).status_code == 503


def test_super_admin_can_moderate_and_delete_community_posts(community_env):
    client, tokens, *_ = community_env
    post_res = client.post(
        f"{API}/community/posts",
        headers=tokens["traveler1"],
        json={"body": "Post for super admin moderation regression check."},
    )
    assert post_res.status_code == 201
    post_id = post_res.json()["id"]

    hide_res = client.patch(
        f"{API}/admin/community/posts/{post_id}",
        headers=tokens["super_admin"],
        json={"visibility": "hidden", "reason": "Super admin moderation check"},
    )
    assert hide_res.status_code == 200
    assert hide_res.json()["visibility"] == "hidden"

    view_res = client.get(f"{API}/community/posts/{post_id}", headers=tokens["super_admin"])
    assert view_res.status_code == 200

    del_res = client.delete(f"{API}/community/posts/{post_id}", headers=tokens["super_admin"])
    assert del_res.status_code == 200
