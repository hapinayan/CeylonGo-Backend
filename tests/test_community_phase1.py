"""
Automated Phase 1 Tests — Travel Community & Social Feed (Post Management & Secure Photo Uploads).

Isolated SQLite in-memory database with PRAGMA foreign_keys=ON (enforcing SQL Server-equivalent
ON DELETE NO ACTION constraints on community_saved_posts.post_id and community_comments.parent_comment_id)
and temporary upload storage directory (never touches shared/production databases or repo uploads/).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
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
from app.models.shared import ListingReference
from app.models.user import Base, User
from app.services import community_posts
from app.services.community_posts import resolve_safe_community_media_path

COMMUNITY_BASE = "/api/v1/community"
UPLOADS_BASE = "/api/v1/uploads/community"


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
    def _enable_sqlite_fks(dbapi_connection, _connection_record):
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
    upload_dir = tmp_path / "community_uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COMMUNITY_UPLOAD_DIR", str(upload_dir))
    monkeypatch.delenv("COMMUNITY_MAX_MEDIA_BYTES", raising=False)
    monkeypatch.delenv("COMMUNITY_MAX_PHOTOS_PER_POST", raising=False)
    community_posts.reset_community_post_limiters()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        community_posts.reset_community_post_limiters()


@pytest.fixture
def actors(db_session: Session) -> dict:
    traveler1 = User(
        full_name="Kasun Perera",
        email="kasun.tc1@example.com",
        role="traveler",
        is_active=True,
    )
    traveler2 = User(
        full_name="Nimali Silva",
        email="nimali.tc1@example.com",
        role="traveler",
        is_active=True,
    )
    provider_user = User(
        full_name="Ruwan Fernando",
        email="ruwan.tc1@example.com",
        role="provider",
        is_active=True,
    )
    admin = User(
        full_name="Platform Admin",
        email="admin.tc1@example.com",
        role="admin",
        is_active=True,
    )
    db_session.add_all([traveler1, traveler2, provider_user, admin])
    db_session.flush()

    provider_profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Ella Sunrise Homestay",
        provider_type="homestay",
        phone="0771234567",
        district="Badulla",
        nic_number="198812345678",
        approval_status="approved",
    )
    db_session.add(provider_profile)
    db_session.commit()

    return {
        "traveler1": traveler1,
        "traveler2": traveler2,
        "provider_user": provider_user,
        "provider_profile": provider_profile,
        "admin": admin,
    }


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def _make_image_bytes(
    fmt: str = "JPEG",
    *,
    size: tuple[int, int] = (64, 48),
    with_exif_gps: bool = False,
) -> bytes:
    mode = "RGBA" if fmt == "PNG" else "RGB"
    color = (20, 120, 200, 255) if mode == "RGBA" else (20, 120, 200)
    img = Image.new(mode, size, color=color)
    buf = BytesIO()
    if fmt == "JPEG" and with_exif_gps:
        exif = img.getexif()
        exif[271] = "TestCameraMake"  # Make
        exif[272] = "SecretGPSPhone"  # Model
        exif[305] = "ExifSoftwareTest"  # Software
        exif[34853] = {  # GPSInfo IFD
            1: "N",
            2: (6.0, 52.0, 12.0),
            3: "E",
            4: (81.0, 2.0, 45.0),
        }
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format=fmt)
    return buf.getvalue()


# =============================================================================
# 1. AUTHENTICATED POST CREATION & UNAUTHENTICATED WRITE REJECTION
# =============================================================================

def test_create_post_authenticated_and_reject_unauthenticated_writes(
    client: TestClient, actors: dict
):
    # Unauthenticated POST rejected
    r_unauth = client.post(
        f"{COMMUNITY_BASE}/posts",
        json={"content": "Unauthenticated post"},
    )
    assert r_unauth.status_code in (401, 403)

    # Authenticated traveler creates post with location, coordinates & hashtags
    r_create = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={
            "content": "Sunrise hike at Little Adam's Peak! #Ella #SriLanka",
            "location_tag": "Little Adam's Peak",
            "district": "Badulla",
            "latitude": "6.866700",
            "longitude": "81.046600",
            "hashtags": ["#Hiking", "ella"],
        },
    )
    assert r_create.status_code == 201, r_create.text
    body = r_create.json()
    assert body["author_id"] == actors["traveler1"].id
    assert body["author"]["full_name"] == "Kasun Perera"
    assert body["author"]["is_verified_provider"] is False
    assert "email" not in body["author"]
    assert body["location"]["district"] == "Badulla"
    assert Decimal(body["latitude"]) == Decimal("6.866700")
    assert Decimal(body["longitude"]) == Decimal("81.046600")
    assert body["likes_count"] == 0
    assert body["comments_count"] == 0
    assert body["is_published"] is True
    assert body["share_url"] == f"/community/posts/{body['id']}"
    assert [h["tag"] for h in body["hashtags"]] == ["ella", "hiking", "srilanka"]
    assert all(h["usage_count"] == 1 for h in body["hashtags"])

    post_id = body["id"]

    # Unauthenticated PATCH, DELETE, and media upload rejected
    assert client.patch(f"{COMMUNITY_BASE}/posts/{post_id}", json={"content": "x"}).status_code in (401, 403)
    assert client.delete(f"{COMMUNITY_BASE}/posts/{post_id}").status_code in (401, 403)
    assert client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        files={"file": ("photo.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    ).status_code in (401, 403)

    # Approved provider author summary exposes verified badge
    r_prov = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["provider_user"]),
        json={"content": "Fresh Ceylon tea mornings in #Ella"},
    )
    assert r_prov.status_code == 201
    prov_body = r_prov.json()
    assert prov_body["author"]["is_verified_provider"] is True
    assert prov_body["author"]["business_name"] == "Ella Sunrise Homestay"
    assert prov_body["author"]["provider_type"] == "homestay"


# =============================================================================
# 2. CAPTION SANITIZATION & COORDINATE VALIDATION
# =============================================================================

def test_caption_sanitization_and_coordinate_validation(client: TestClient, actors: dict):
    # Script and HTML tags stripped safely
    r_san = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={
            "content": "<b>Sigiriya</b> Rock <script>alert(1)</script> view #Sigiriya",
            "location_tag": "<i>Sigiriya Rock</i>",
            "district": "Matale",
        },
    )
    assert r_san.status_code == 201, r_san.text
    assert "<script>" not in r_san.json()["content"]
    assert "alert(1)" not in r_san.json()["content"]
    assert "<b>" not in r_san.json()["content"]
    assert r_san.json()["location_tag"] == "Sigiriya Rock"

    # Caption empty after sanitization rejected (422)
    r_empty = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "<script>alert('xss')</script>   "},
    )
    assert r_empty.status_code == 422

    # Partial coordinate pair rejected via shared validate_coordinate_pair (422)
    r_lat_only = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Only lat", "latitude": "7.290600"},
    )
    assert r_lat_only.status_code == 422

    r_lon_only = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Only lon", "longitude": "80.633700"},
    )
    assert r_lon_only.status_code == 422

    # Out-of-range coordinates rejected (422)
    r_oob = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Invalid lat", "latitude": "95.0", "longitude": "80.0"},
    )
    assert r_oob.status_code == 422

    # Partial PATCH that clears only longitude while latitude remains set -> rejected (422)
    created = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Kandy Lake", "latitude": "7.293100", "longitude": "80.641300"},
    ).json()
    r_bad_patch = client.patch(
        f"{COMMUNITY_BASE}/posts/{created['id']}",
        headers=bearer(actors["traveler1"]),
        json={"longitude": None},
    )
    assert r_bad_patch.status_code == 422


# =============================================================================
# 3. OWNERSHIP, EDITING, ADMIN MODERATION & UNPUBLISHED VISIBILITY
# =============================================================================

def test_ownership_editing_and_unpublished_post_visibility(client: TestClient, actors: dict):
    created = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Draft itinerary for #Galle", "is_published": False},
    ).json()
    post_id = created["id"]

    # Unpublished post hidden from anonymous and other users (404)
    assert client.get(f"{COMMUNITY_BASE}/posts/{post_id}").status_code == 404
    assert (
        client.get(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["traveler2"]),
        ).status_code
        == 404
    )

    # Visible to author and admin (200)
    assert (
        client.get(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["traveler1"]),
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["admin"]),
        ).status_code
        == 200
    )

    # Another traveler cannot edit or delete (403)
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["traveler2"]),
            json={"content": "Hijacked"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["traveler2"]),
        ).status_code
        == 403
    )

    # Author publishes & updates post
    r_pub = client.patch(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(actors["traveler1"]),
        json={"content": "Published sunset at Galle Fort #Galle #Sunset", "is_published": True},
    )
    assert r_pub.status_code == 200
    assert r_pub.json()["is_published"] is True
    assert r_pub.json()["updated_at"] is not None

    # Public viewer can now read the published post
    assert client.get(f"{COMMUNITY_BASE}/posts/{post_id}").status_code == 200

    # Admin cannot rewrite another user's caption (403), but CAN moderate visibility
    assert (
        client.patch(
            f"{COMMUNITY_BASE}/posts/{post_id}",
            headers=bearer(actors["admin"]),
            json={"content": "Admin rewriting user caption"},
        ).status_code
        == 403
    )
    r_mod = client.patch(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(actors["admin"]),
        json={"is_published": False},
    )
    assert r_mod.status_code == 200
    assert r_mod.json()["is_published"] is False
    assert client.get(f"{COMMUNITY_BASE}/posts/{post_id}").status_code == 404


# =============================================================================
# 4. HASHTAG NORMALIZATION, VALIDATION & COUNTER SYNCHRONIZATION
# =============================================================================

def test_hashtag_synchronization_validation_and_nonnegative_counters(
    client: TestClient, db_session: Session, actors: dict
):
    # Invalid hashtag characters rejected (422)
    r_bad_tag = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Bad tag", "hashtags": ["ella-rock!"]},
    )
    assert r_bad_tag.status_code == 422

    # Too many hashtags (>20) rejected (422)
    r_many = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Spam", "hashtags": [f"tag{i}" for i in range(21)]},
    )
    assert r_many.status_code == 422

    # Two posts share #kandy
    p1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Temple visit #Kandy #Culture #kandy"},
    ).json()
    p2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler2"]),
        json={"content": "Lake walk #KANDY"},
    ).json()

    kandy = db_session.query(CommunityHashtag).filter_by(tag="kandy").first()
    culture = db_session.query(CommunityHashtag).filter_by(tag="culture").first()
    assert kandy is not None and kandy.usage_count == 2
    assert culture is not None and culture.usage_count == 1

    # Editing p1 replaces #culture with #tea while keeping #kandy
    r_edit = client.patch(
        f"{COMMUNITY_BASE}/posts/{p1['id']}",
        headers=bearer(actors["traveler1"]),
        json={"content": "Tea plantation #Kandy #Tea"},
    )
    assert r_edit.status_code == 200
    assert [h["tag"] for h in r_edit.json()["hashtags"]] == ["kandy", "tea"]

    db_session.expire_all()
    assert db_session.query(CommunityHashtag).filter_by(tag="kandy").first().usage_count == 2
    assert db_session.query(CommunityHashtag).filter_by(tag="culture").first().usage_count == 0
    assert db_session.query(CommunityHashtag).filter_by(tag="tea").first().usage_count == 1

    # Deleting p1 decrements #kandy -> 1 and #tea -> 0 (never negative)
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/posts/{p1['id']}",
            headers=bearer(actors["traveler1"]),
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert db_session.query(CommunityHashtag).filter_by(tag="kandy").first().usage_count == 1
    assert db_session.query(CommunityHashtag).filter_by(tag="tea").first().usage_count == 0
    assert (
        db_session.query(CommunityPostHashtag).filter_by(post_id=p1["id"]).count() == 0
    )

    # Deleting p2 brings #kandy cleanly to 0
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/posts/{p2['id']}",
            headers=bearer(actors["traveler2"]),
        ).status_code
        == 200
    )
    db_session.expire_all()
    assert db_session.query(CommunityHashtag).filter_by(tag="kandy").first().usage_count == 0


# =============================================================================
# 5. SECURE PHOTO UPLOADS, EXIF REMOVAL, PATH SECURITY & FAILURE CLEANUP
# =============================================================================

def test_valid_image_uploads_exif_stripping_and_serving(
    client: TestClient, actors: dict, monkeypatch: pytest.MonkeyPatch
):
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Coastal train to Mirissa #Mirissa"},
    ).json()
    post_id = post["id"]

    # Upload JPEG containing embedded GPS & camera EXIF metadata
    jpg_with_exif = _make_image_bytes("JPEG", with_exif_gps=True)
    with Image.open(BytesIO(jpg_with_exif)) as original_img:
        assert len(original_img.getexif()) > 0  # Sanity check: EXIF present before upload

    r_jpg = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("../../evil_name.jpg", jpg_with_exif, "image/jpeg")},
    )
    assert r_jpg.status_code == 201, r_jpg.text
    jpg_media = r_jpg.json()
    assert jpg_media["media_type"] == "image"
    assert jpg_media["display_order"] == 0
    assert jpg_media["media_url"].startswith(f"{UPLOADS_BASE}/post_{post_id}_")
    assert "evil_name" not in jpg_media["media_url"]
    assert ".." not in jpg_media["media_url"]

    # Download served image and verify ALL EXIF & GPS metadata was stripped!
    r_serve = client.get(jpg_media["media_url"])
    assert r_serve.status_code == 200
    assert r_serve.headers["content-type"].startswith("image/jpeg")
    assert r_serve.headers.get("x-content-type-options") == "nosniff"
    with Image.open(BytesIO(r_serve.content)) as served_img:
        assert len(served_img.getexif()) == 0
    assert b"TestCameraMake" not in r_serve.content
    assert b"SecretGPSPhone" not in r_serve.content

    # PNG and WebP also supported
    r_png = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("beach.png", _make_image_bytes("PNG"), "image/png")},
    )
    assert r_png.status_code == 201, r_png.text
    assert r_png.json()["display_order"] == 1

    r_webp = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("sunset.webp", _make_image_bytes("WEBP"), "image/webp")},
    )
    assert r_webp.status_code == 201, r_webp.text
    assert r_webp.json()["display_order"] == 2

    # Enforce configurable max photo count per post
    monkeypatch.setenv("COMMUNITY_MAX_PHOTOS_PER_POST", "3")
    r_limit = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("fourth.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    )
    assert r_limit.status_code == 400


def test_invalid_oversized_images_path_security_and_media_ownership(
    client: TestClient, actors: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Hidden draft photo test", "is_published": False},
    ).json()
    post_id = post["id"]

    # Non-owner cannot upload photo (403)
    r_forbid = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler2"]),
        files={"file": ("ok.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    )
    assert r_forbid.status_code == 403

    # Unsupported extension / video / SVG rejected (400)
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{post_id}/media",
            headers=bearer(actors["traveler1"]),
            files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{post_id}/media",
            headers=bearer(actors["traveler1"]),
            files={"file": ("xss.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
        ).status_code
        == 400
    )

    # Spoofed magic bytes (text disguised as .jpg) rejected (400)
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{post_id}/media",
            headers=bearer(actors["traveler1"]),
            files={"file": ("fake.jpg", b"not actually a jpeg", "image/jpeg")},
        ).status_code
        == 400
    )

    # Extension vs MIME mismatch rejected (400)
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{post_id}/media",
            headers=bearer(actors["traveler1"]),
            files={"file": ("pic.png", _make_image_bytes("JPEG"), "image/jpeg")},
        ).status_code
        == 400
    )

    # Oversized image rejected (413)
    monkeypatch.setenv("COMMUNITY_MAX_MEDIA_BYTES", "500")
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{post_id}/media",
            headers=bearer(actors["traveler1"]),
            files={"file": ("big.jpg", _make_image_bytes("JPEG", size=(300, 300)), "image/jpeg")},
        ).status_code
        == 413
    )
    monkeypatch.delenv("COMMUNITY_MAX_MEDIA_BYTES", raising=False)

    # Upload valid image on unpublished post -> hidden from public & other users (404), visible to owner/admin (200)
    uploaded = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("draft.png", _make_image_bytes("PNG"), "image/png")},
    ).json()
    assert client.get(uploaded["media_url"]).status_code == 404
    assert client.get(uploaded["media_url"], headers=bearer(actors["traveler2"])).status_code == 404
    assert client.get(uploaded["media_url"], headers=bearer(actors["traveler1"])).status_code == 200
    assert client.get(uploaded["media_url"], headers=bearer(actors["admin"])).status_code == 200

    # Non-owner cannot delete media (403)
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/posts/{post_id}/media/{uploaded['id']}",
            headers=bearer(actors["traveler2"]),
        ).status_code
        == 403
    )

    # Arbitrary unregistered local file inside upload dir is NOT exposed (404)
    upload_dir = tmp_path / "community_uploads"
    secret_file = upload_dir / "unregistered_secret.jpg"
    secret_file.write_bytes(_make_image_bytes("JPEG"))
    assert client.get(f"{UPLOADS_BASE}/unregistered_secret.jpg").status_code == 404

    # Path traversal blocked directly by resolver and endpoint
    for bad_name in ("../secret.txt", "..\\secret.txt", "sub/file.jpg", "a..b.jpg"):
        with pytest.raises(Exception):
            resolve_safe_community_media_path(bad_name, upload_dir)
    assert client.get(f"{UPLOADS_BASE}/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)

    # Owner deletes media -> file removed from disk
    fname = uploaded["media_url"].split("/")[-1]
    assert (upload_dir / fname).is_file()
    r_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{post_id}/media/{uploaded['id']}",
        headers=bearer(actors["traveler1"]),
    )
    assert r_del.status_code == 200
    assert not (upload_dir / fname).exists()


def test_media_upload_cleans_up_disk_file_on_db_commit_failure(
    client: TestClient, actors: dict, tmp_path: Path
):
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Rollback cleanup test"},
    ).json()
    upload_dir = tmp_path / "community_uploads"
    before = set(p.name for p in upload_dir.iterdir())

    with patch(
        "app.services.community_posts._commit",
        side_effect=OperationalError("stmt", {}, Exception("simulated db error")),
    ):
        with pytest.raises(OperationalError):
            client.post(
                f"{COMMUNITY_BASE}/posts/{post['id']}/media",
                headers=bearer(actors["traveler1"]),
                files={"file": ("fail.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
            )

    after = set(p.name for p in upload_dir.iterdir())
    assert after == before  # Zero orphaned files left on disk


# =============================================================================
# 6. SQL SERVER FK-SAFE DELETION & MODERATION EVIDENCE PRESERVATION
# =============================================================================

def test_hard_delete_post_is_fk_safe_with_likes_nested_comments_bookmarks_hashtags_media(
    client: TestClient, db_session: Session, actors: dict, tmp_path: Path
):
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Full engagement post #Yala"},
    ).json()
    post_id = post["id"]

    media = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("leopard.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    ).json()
    media_file = tmp_path / "community_uploads" / media["media_url"].split("/")[-1]
    assert media_file.is_file()

    # Seed likes, saved bookmark (ON DELETE NO ACTION), and nested reply comments (ON DELETE NO ACTION)
    db_session.add(CommunityPostLike(post_id=post_id, user_id=actors["traveler2"].id))
    db_session.add(CommunitySavedPost(post_id=post_id, user_id=actors["traveler2"].id))
    parent_c = CommunityComment(
        post_id=post_id,
        author_id=actors["traveler2"].id,
        content="Amazing sighting!",
    )
    db_session.add(parent_c)
    db_session.flush()
    child_c = CommunityComment(
        post_id=post_id,
        author_id=actors["traveler1"].id,
        parent_comment_id=parent_c.id,
        content="Thanks! Early morning safari.",
    )
    db_session.add(child_c)
    db_session.commit()

    # Delete succeeds with PRAGMA foreign_keys=ON (zero FK violations)
    r_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(actors["traveler1"]),
    )
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["deletion_mode"] == "hard_deleted"

    db_session.expire_all()
    assert db_session.get(CommunityPost, post_id) is None
    assert db_session.query(CommunitySavedPost).filter_by(post_id=post_id).count() == 0
    assert db_session.query(CommunityPostLike).filter_by(post_id=post_id).count() == 0
    assert db_session.query(CommunityComment).filter_by(post_id=post_id).count() == 0
    assert db_session.query(CommunityPostMedia).filter_by(post_id=post_id).count() == 0
    assert db_session.query(CommunityHashtag).filter_by(tag="yala").first().usage_count == 0
    assert not media_file.exists()


def test_delete_reported_post_preserves_moderation_evidence(
    client: TestClient, db_session: Session, actors: dict, tmp_path: Path
):
    post = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(actors["traveler1"]),
        json={"content": "Reported post content #Colombo"},
    ).json()
    post_id = post["id"]

    media = client.post(
        f"{COMMUNITY_BASE}/posts/{post_id}/media",
        headers=bearer(actors["traveler1"]),
        files={"file": ("evidence.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    ).json()
    media_file = tmp_path / "community_uploads" / media["media_url"].split("/")[-1]
    assert media_file.is_file()

    db_session.add(CommunitySavedPost(post_id=post_id, user_id=actors["traveler2"].id))
    db_session.add(
        CommunityReport(
            reporter_id=actors["traveler2"].id,
            entity_type="post",
            entity_id=post_id,
            report_reason="spam",
            description="Reported promotional spam.",
        )
    )
    db_session.commit()

    # Author cannot delete individual media while report references the post (409)
    assert (
        client.delete(
            f"{COMMUNITY_BASE}/posts/{post_id}/media/{media['id']}",
            headers=bearer(actors["traveler1"]),
        ).status_code
        == 409
    )

    # Deleting the post hides it publicly while preserving DB row + media evidence for admins
    r_del = client.delete(
        f"{COMMUNITY_BASE}/posts/{post_id}",
        headers=bearer(actors["traveler1"]),
    )
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["deletion_mode"] == "soft_unpublished_moderation_preserved"

    db_session.expire_all()
    preserved = db_session.get(CommunityPost, post_id)
    assert preserved is not None
    assert preserved.is_published is False
    assert db_session.query(CommunitySavedPost).filter_by(post_id=post_id).count() == 0
    assert db_session.query(CommunityHashtag).filter_by(tag="colombo").first().usage_count == 0
    assert media_file.is_file()  # Evidence file preserved on disk

    # Public & other users cannot see hidden post or its image; admin CAN still inspect both
    assert client.get(f"{COMMUNITY_BASE}/posts/{post_id}").status_code == 404
    assert client.get(media["media_url"]).status_code == 404
    assert client.get(f"{COMMUNITY_BASE}/posts/{post_id}", headers=bearer(actors["admin"])).status_code == 200
    assert client.get(media["media_url"], headers=bearer(actors["admin"])).status_code == 200


# =============================================================================
# 10. POST CREATION & MEDIA UPLOAD RATE LIMITING (TC-SEC-04)
# =============================================================================

def test_post_create_rate_limit_isolation_and_no_db_mutation(
    client: TestClient,
    db_session: Session,
    actors: dict,
    monkeypatch: pytest.MonkeyPatch,
):
    t1 = actors["traveler1"]
    t2 = actors["traveler2"]

    # Verify environment-configurable factory settings
    monkeypatch.setenv("COMMUNITY_POST_CREATE_RATE_LIMIT", "7")
    monkeypatch.setenv("COMMUNITY_POST_CREATE_RATE_WINDOW_SECONDS", "120")
    configured = community_posts.get_default_community_post_create_limiter()
    assert configured.max_requests == 7
    assert configured.window_seconds == 120

    monkeypatch.setattr(community_posts.community_post_create_limiter, "max_requests", 2)

    # Unauthenticated requests fail auth BEFORE rate limiter runs
    assert (
        client.post(f"{COMMUNITY_BASE}/posts", json={"content": "Unauth post"}).status_code
        in (401, 403)
    )

    # First 2 posts by traveler1 succeed
    r1 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Post 1 #Sigiriya"},
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Post 2 #Ella"},
    )
    assert r2.status_code == 201, r2.text

    posts_before = db_session.query(CommunityPost).count()
    hashtags_before = db_session.query(CommunityHashtag).count()
    links_before = db_session.query(CommunityPostHashtag).count()

    # 3rd post by traveler1 hits 429 with valid Retry-After and ZERO DB mutations
    r3 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Blocked post #NeverCreatedTag"},
    )
    assert r3.status_code == 429, r3.text
    assert "Retry-After" in r3.headers
    assert int(r3.headers["Retry-After"]) >= 1

    assert db_session.query(CommunityPost).count() == posts_before
    assert db_session.query(CommunityHashtag).count() == hashtags_before
    assert db_session.query(CommunityPostHashtag).count() == links_before
    assert (
        db_session.query(CommunityHashtag)
        .filter_by(tag="nevercreatedtag")
        .first()
        is None
    )

    # User-scoped isolation: traveler2 can still create posts cleanly
    r_t2 = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t2),
        json={"content": "Traveler 2 unaffected #Galle"},
    )
    assert r_t2.status_code == 201, r_t2.text


def test_media_upload_rate_limit_isolation_and_no_file_or_db_mutation(
    client: TestClient,
    db_session: Session,
    actors: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    t1 = actors["traveler1"]
    t2 = actors["traveler2"]
    upload_dir = tmp_path / "community_uploads"

    # Verify environment-configurable factory settings
    monkeypatch.setenv("COMMUNITY_MEDIA_UPLOAD_RATE_LIMIT", "12")
    monkeypatch.setenv("COMMUNITY_MEDIA_UPLOAD_RATE_WINDOW_SECONDS", "300")
    configured = community_posts.get_default_community_media_upload_limiter()
    assert configured.max_requests == 12
    assert configured.window_seconds == 300

    p1_id = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t1),
        json={"content": "Photo post by t1"},
    ).json()["id"]
    p2_id = client.post(
        f"{COMMUNITY_BASE}/posts",
        headers=bearer(t2),
        json={"content": "Photo post by t2"},
    ).json()["id"]

    monkeypatch.setattr(community_posts.community_media_upload_limiter, "max_requests", 2)

    img_bytes = _make_image_bytes("PNG")

    # Unauthenticated upload fails auth BEFORE rate limiter runs
    assert (
        client.post(
            f"{COMMUNITY_BASE}/posts/{p1_id}/media",
            files={"file": ("unauth.png", img_bytes, "image/png")},
        ).status_code
        in (401, 403)
    )

    # First 2 uploads by traveler1 succeed
    for idx in range(2):
        r_ok = client.post(
            f"{COMMUNITY_BASE}/posts/{p1_id}/media",
            headers=bearer(t1),
            files={"file": (f"ok_{idx}.png", img_bytes, "image/png")},
        )
        assert r_ok.status_code == 201, r_ok.text

    media_count_before = db_session.query(CommunityPostMedia).count()
    files_before = sorted(p.name for p in upload_dir.iterdir() if p.is_file())
    assert len(files_before) == 2

    # 3rd upload by traveler1 hits 429 with Retry-After, ZERO DB rows, ZERO disk files written
    r_blocked = client.post(
        f"{COMMUNITY_BASE}/posts/{p1_id}/media",
        headers=bearer(t1),
        files={"file": ("blocked.png", img_bytes, "image/png")},
    )
    assert r_blocked.status_code == 429, r_blocked.text
    assert "Retry-After" in r_blocked.headers
    assert int(r_blocked.headers["Retry-After"]) >= 1

    assert db_session.query(CommunityPostMedia).count() == media_count_before
    files_after = sorted(p.name for p in upload_dir.iterdir() if p.is_file())
    assert files_after == files_before

    # User-scoped isolation: traveler2 can still upload photos to their own post
    r_t2_up = client.post(
        f"{COMMUNITY_BASE}/posts/{p2_id}/media",
        headers=bearer(t2),
        files={"file": ("t2_ok.png", img_bytes, "image/png")},
    )
    assert r_t2_up.status_code == 201, r_t2_up.text
    assert db_session.query(CommunityPostMedia).count() == media_count_before + 1
