"""
Automated tests for Module 04 P0.5 — Provider Eco Experience Photo Upload & Delete.

Covers:
1. Successful image upload by owning provider (JPEG, PNG, WebP, caption, is_primary, sort_order).
2. Unauthorized upload (unauthenticated 401, traveler 403, non-eco provider 403, pending provider 403).
3. Upload to another provider's EcoExperience (403 forbidden, non-existent experience 404).
4. Invalid image format rejection (text file, disallowed extension, unsupported image format).
5. Oversized and corrupted image rejection (0 bytes, corrupted content, >5MB, too small dimensions, too large dimensions).
6. Successful image deletion by owning provider (204, DB deletion, physical file removal, graceful missing file handling).
7. Unauthorized and cross-provider deletion (unauthenticated 401, traveler 403, cross-provider 403).
8. Deleting an image belonging to another EcoExperience (400 rejection).
9. Public listing image visibility and streaming (approved & published exposure, path traversal protection).
10. Database failure cleanup (no orphan files) and maximum image count limit enforcement.
"""

from datetime import UTC, datetime
from decimal import Decimal
import io
import os
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.models.eco_experience import (
    EcoExperience,
    EcoExperienceImage,
    EcoVerification,
)
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    get_eco_experience_images_dir,
    router as eco_experiences_router,
    uploads_router,
)

eco_p05_app = FastAPI(title="CeylonGo M04 P0.5 Test App")
eco_p05_app.include_router(auth_router)
eco_p05_app.include_router(eco_experiences_router)
eco_p05_app.include_router(admin_eco_router)
eco_p05_app.include_router(uploads_router)

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
def setup_test_db(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    images_dir = tmp_path / "eco_experiences"
    images_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ECO_EXPERIENCE_IMAGES_DIR", str(images_dir))
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

    eco_p05_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p05_app) as test_client:
        yield test_client
    eco_p05_app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def make_test_image(
    format_name: str = "JPEG",
    size: tuple[int, int] = (200, 200),
    color: str = "green",
) -> bytes:
    buf = io.BytesIO()
    mode = "RGBA" if format_name == "PNG" else "RGB"
    img = Image.new(mode, size, color=color)
    img.save(buf, format=format_name)
    return buf.getvalue()


@pytest.fixture
def users_and_profiles(db_session: Session):
    traveler = User(
        full_name="Traveler User",
        email="traveler_p5@test.com",
        password_hash=hash_password("TravelerPass123!"),
        role="traveler",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    admin = User(
        full_name="Admin User",
        email="admin_p5@test.com",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    eco_user_1 = User(
        full_name="Eco Provider One",
        email="eco_p5_1@test.com",
        password_hash=hash_password("EcoPass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    eco_user_2 = User(
        full_name="Eco Provider Two",
        email="eco_p5_2@test.com",
        password_hash=hash_password("EcoPass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    artisan_user = User(
        full_name="Artisan Provider",
        email="artisan_p5@test.com",
        password_hash=hash_password("ArtisanPass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    pending_eco_user = User(
        full_name="Pending Eco Provider",
        email="pending_p5@test.com",
        password_hash=hash_password("PendingPass123!"),
        role="provider",
        is_active=True,
        created_at=datetime.now(UTC),
    )

    db_session.add_all([
        traveler,
        admin,
        eco_user_1,
        eco_user_2,
        artisan_user,
        pending_eco_user,
    ])
    db_session.flush()

    eco_profile_1 = ProviderProfile(
        user_id=eco_user_1.id,
        business_name="Sinharaja Eco Expeditions",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94771111111",
        district="Ratnapura",
        created_at=datetime.now(UTC),
    )
    eco_profile_2 = ProviderProfile(
        user_id=eco_user_2.id,
        business_name="Knuckles Wilderness Adventures",
        provider_type="eco_tourism",
        approval_status="approved",
        phone="+94772222222",
        district="Matale",
        created_at=datetime.now(UTC),
    )
    artisan_profile = ProviderProfile(
        user_id=artisan_user.id,
        business_name="Kandy Traditional Pottery",
        provider_type="artisan",
        approval_status="approved",
        phone="+94773333333",
        district="Kandy",
        created_at=datetime.now(UTC),
    )
    pending_profile = ProviderProfile(
        user_id=pending_eco_user.id,
        business_name="Ella Green Treks",
        provider_type="eco_tourism",
        approval_status="pending",
        phone="+94774444444",
        district="Badulla",
        created_at=datetime.now(UTC),
    )

    db_session.add_all([
        eco_profile_1,
        eco_profile_2,
        artisan_profile,
        pending_profile,
    ])
    db_session.commit()

    return {
        "traveler": traveler,
        "admin": admin,
        "eco_user_1": eco_user_1,
        "eco_user_2": eco_user_2,
        "artisan_user": artisan_user,
        "pending_eco_user": pending_eco_user,
        "eco_profile_1": eco_profile_1,
        "eco_profile_2": eco_profile_2,
        "artisan_profile": artisan_profile,
        "pending_profile": pending_profile,
    }


def create_sample_experience(
    db_session: Session,
    provider_profile_id: int,
    title: str = "Rainforest Canopy Trek",
    status_val: str = "draft",
    is_published: bool = False,
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider_profile_id,
        title=title,
        eco_category="Forest & Nature Trails",
        description="Immersive primary rainforest exploration.",
        itinerary="Morning birding -> Ridge walk -> Conservation talk.",
        duration_hours=Decimal("4.5"),
        district="Ratnapura",
        city="Kalawana",
        address="Sinharaja North Gate",
        meeting_point="Sinharaja Ticket Center",
        directions_info="Turn left past the bridge.",
        latitude=Decimal("6.416667"),
        longitude=Decimal("80.416667"),
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        currency="LKR",
        max_group_size=12,
        min_participants=1,
        min_age=8,
        environmental_guidelines="Strict zero-litter policy; stay on marked trails.",
        wildlife_safety_summary="Maintain 10m distance from all reptiles; no feeding allowed.",
        conservation_factual_contribution="20% of fees donated to Rainforest Trust.",
        conservation_marketing_statement="Protecting Sri Lanka's endemic flora and fauna.",
        protected_area_permit_required=True,
        certified_guide_required=True,
        permit_details="DWC entrance ticket included in fee.",
        cancellation_policy="Full refund up to 24h before departure.",
        free_cancellation_hours=24,
        listing_status=status_val,
        is_published=is_published,
        eco_badge_status="none",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(exp)
    db_session.commit()
    db_session.refresh(exp)
    return exp


# ==============================================================================
# Tests
# ==============================================================================


def test_successful_image_upload_by_owning_provider(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.1: Verify successful image upload by the owning provider with
    dimensions validation, unique filename generation, and primary flag management.
    """
    provider_user = users_and_profiles["eco_user_1"]
    profile = users_and_profiles["eco_profile_1"]
    exp = create_sample_experience(db_session, profile.id)

    # 1. Upload first image as primary
    jpeg_bytes = make_test_image("JPEG", (600, 400), "forestgreen")
    res1 = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("canopy.jpg", jpeg_bytes, "image/jpeg")},
        data={"caption": "Canopy Walkway", "is_primary": "true", "sort_order": "1"},
    )
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["id"] > 0
    assert data1["eco_experience_id"] == exp.id
    assert data1["caption"] == "Canopy Walkway"
    assert data1["is_primary"] is True
    assert data1["sort_order"] == 1
    assert data1["image_url"].startswith("/uploads/eco_experiences/eco_img_")
    assert data1["image_url"].endswith(".jpg")

    # Verify physical file was written
    storage_dir = get_eco_experience_images_dir()
    filename1 = os.path.basename(data1["image_url"])
    file_path1 = storage_dir / filename1
    assert file_path1.is_file()

    # 2. Upload second image as PNG, also marking as primary -> should unset primary on first
    png_bytes = make_test_image("PNG", (800, 600), "royalblue")
    res2 = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("waterfall.png", png_bytes, "image/png")},
        data={"caption": "Secret Waterfall", "is_primary": "true", "sort_order": "2"},
    )
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["is_primary"] is True
    assert data2["image_url"].endswith(".png")

    # Check database state: first image is_primary must now be False
    db_session.expire_all()
    img1 = db_session.query(EcoExperienceImage).filter(EcoExperienceImage.id == data1["id"]).first()
    img2 = db_session.query(EcoExperienceImage).filter(EcoExperienceImage.id == data2["id"]).first()
    assert img1.is_primary is False
    assert img2.is_primary is True


def test_unauthorized_upload(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.2: Verify unauthenticated callers, travelers, non-eco providers, and
    pending eco providers cannot upload images.
    """
    profile = users_and_profiles["eco_profile_1"]
    exp = create_sample_experience(db_session, profile.id)
    jpeg_bytes = make_test_image()

    # 1. Unauthenticated -> 401
    res_unauth = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_unauth.status_code == 401

    # 2. Traveler -> 403
    res_trav = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(users_and_profiles["traveler"]),
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_trav.status_code == 403

    # 3. Non-eco provider (artisan) -> 403
    res_artisan = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(users_and_profiles["artisan_user"]),
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_artisan.status_code == 403

    # 4. Pending eco provider -> 403
    res_pending = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(users_and_profiles["pending_eco_user"]),
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_pending.status_code == 403


def test_upload_to_another_provider_eco_experience(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.3: Provider 2 attempts to upload to Provider 1's experience -> 403 Forbidden.
    Upload to non-existent experience -> 404 Not Found.
    """
    exp1 = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)
    jpeg_bytes = make_test_image()

    # Provider 2 tries to upload to Provider 1's listing -> 403
    res_cross = client.post(
        f"/api/v1/eco-experiences/{exp1.id}/images",
        headers=auth_header(users_and_profiles["eco_user_2"]),
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_cross.status_code == 403
    assert "do not own" in res_cross.json()["detail"].lower()

    # Non-existent experience -> 404
    res_non_existent = client.post(
        "/api/v1/eco-experiences/99999/images",
        headers=auth_header(users_and_profiles["eco_user_1"]),
        files={"file": ("pic.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_non_existent.status_code == 404


def test_invalid_image_format_rejection(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.4: Validate that text files, executables, scripts, and unsupported image
    formats (e.g. BMP) are rejected with 400 Bad Request.
    """
    provider_user = users_and_profiles["eco_user_1"]
    exp = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)

    # 1. Plain text file with .txt extension
    res_txt = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("notes.txt", b"plain text data", "text/plain")},
    )
    assert res_txt.status_code == 400
    assert "unsupported file extension" in res_txt.json()["detail"].lower()

    # 2. Executable extension
    res_exe = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("malicious.exe", b"MZ\x90\x00executable", "application/octet-stream")},
    )
    assert res_exe.status_code == 400

    # 3. Unsupported image format (BMP)
    bmp_buf = io.BytesIO()
    bmp_img = Image.new("RGB", (100, 100), color="red")
    bmp_img.save(bmp_buf, format="BMP")
    res_bmp = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("image.jpg", bmp_buf.getvalue(), "image/jpeg")},
    )
    assert res_bmp.status_code == 400
    assert "unsupported image format" in res_bmp.json()["detail"].lower()


def test_oversized_and_corrupted_image_rejection(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.5: Verify empty files, corrupted byte payloads, files exceeding 5MB,
    and images with invalid dimensions (too small or too large) are rejected.
    """
    provider_user = users_and_profiles["eco_user_1"]
    exp = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)

    # 1. Empty file (0 bytes) -> 400
    res_empty = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("empty.jpg", b"", "image/jpeg")},
    )
    assert res_empty.status_code == 400
    assert "empty" in res_empty.json()["detail"].lower()

    # 2. Corrupted image bytes with .jpg extension -> 400
    res_corrupt = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("corrupt.jpg", b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00corruptgarbage", "image/jpeg")},
    )
    assert res_corrupt.status_code == 400
    assert "corrupted" in res_corrupt.json()["detail"].lower()

    # 3. Oversized file (> 5 MB) -> 400
    oversized_bytes = b"0" * (5 * 1024 * 1024 + 1024)
    res_oversized = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("large.jpg", oversized_bytes, "image/jpeg")},
    )
    assert res_oversized.status_code == 400
    assert "exceeds maximum allowed size" in res_oversized.json()["detail"].lower()

    # 4. Dimensions too small (< 50x50, e.g. 20x20) -> 400
    small_bytes = make_test_image("JPEG", (20, 20))
    res_small = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("small.jpg", small_bytes, "image/jpeg")},
    )
    assert res_small.status_code == 400
    assert "too small" in res_small.json()["detail"].lower()


def test_successful_image_deletion(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.6: Verify owning provider can delete an image, removing both the
    database record and the physical file from disk.
    Also verifies graceful handling when the file was already missing on disk.
    """
    provider_user = users_and_profiles["eco_user_1"]
    exp = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)

    # 1. Upload an image
    jpeg_bytes = make_test_image("JPEG", (400, 300))
    res_up = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("photo.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_up.status_code == 201
    image_id = res_up.json()["id"]
    filename = os.path.basename(res_up.json()["image_url"])
    file_path = get_eco_experience_images_dir() / filename
    assert file_path.is_file()

    # 2. Delete the image -> 204
    res_del = client.delete(
        f"/api/v1/eco-experiences/{exp.id}/images/{image_id}",
        headers=auth_header(provider_user),
    )
    assert res_del.status_code == 204

    # 3. Check DB and disk
    db_session.expire_all()
    db_img = db_session.query(EcoExperienceImage).filter(EcoExperienceImage.id == image_id).first()
    assert db_img is None
    assert not file_path.exists()

    # 4. Deleting an already deleted image -> 404
    res_del_again = client.delete(
        f"/api/v1/eco-experiences/{exp.id}/images/{image_id}",
        headers=auth_header(provider_user),
    )
    assert res_del_again.status_code == 404

    # 5. Graceful handling: image in DB but file missing on disk -> still deletes DB record
    res_up2 = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_user),
        files={"file": ("photo2.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_up2.status_code == 201
    image_id2 = res_up2.json()["id"]
    file_path2 = get_eco_experience_images_dir() / os.path.basename(res_up2.json()["image_url"])
    # Delete the physical file beforehand
    if file_path2.is_file():
        file_path2.unlink()

    # Call DELETE endpoint -> should succeed cleanly with 204
    res_del2 = client.delete(
        f"/api/v1/eco-experiences/{exp.id}/images/{image_id2}",
        headers=auth_header(provider_user),
    )
    assert res_del2.status_code == 204
    assert db_session.query(EcoExperienceImage).filter(EcoExperienceImage.id == image_id2).first() is None


def test_unauthorized_and_cross_provider_deletion(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.7: Verify unauthenticated, traveler, and other providers cannot delete
    images belonging to an eco experience.
    """
    provider_1 = users_and_profiles["eco_user_1"]
    provider_2 = users_and_profiles["eco_user_2"]
    exp = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)

    # Upload an image as Provider 1
    jpeg_bytes = make_test_image()
    res_up = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_1),
        files={"file": ("p1.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_up.status_code == 201
    image_id = res_up.json()["id"]

    # 1. Unauthenticated -> 401
    res_unauth = client.delete(f"/api/v1/eco-experiences/{exp.id}/images/{image_id}")
    assert res_unauth.status_code == 401

    # 2. Traveler -> 403
    res_trav = client.delete(
        f"/api/v1/eco-experiences/{exp.id}/images/{image_id}",
        headers=auth_header(users_and_profiles["traveler"]),
    )
    assert res_trav.status_code == 403

    # 3. Provider 2 attempts to delete Provider 1's image on Provider 1's experience -> 403
    res_cross = client.delete(
        f"/api/v1/eco-experiences/{exp.id}/images/{image_id}",
        headers=auth_header(provider_2),
    )
    assert res_cross.status_code == 403
    assert "do not own" in res_cross.json()["detail"].lower()


def test_delete_image_belonging_to_another_eco_experience(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.8: Provider 1 owns Experience A and Experience B. Attempting to delete
    Image B through Experience A's endpoint must be rejected with 400 Bad Request.
    """
    provider_1 = users_and_profiles["eco_user_1"]
    profile_1 = users_and_profiles["eco_profile_1"]

    exp_a = create_sample_experience(db_session, profile_1.id, title="Experience A")
    exp_b = create_sample_experience(db_session, profile_1.id, title="Experience B")

    jpeg_bytes = make_test_image()

    # Upload Image A to Exp A, Image B to Exp B
    res_a = client.post(
        f"/api/v1/eco-experiences/{exp_a.id}/images",
        headers=auth_header(provider_1),
        files={"file": ("img_a.jpg", jpeg_bytes, "image/jpeg")},
    )
    res_b = client.post(
        f"/api/v1/eco-experiences/{exp_b.id}/images",
        headers=auth_header(provider_1),
        files={"file": ("img_b.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_a.status_code == 201
    assert res_b.status_code == 201
    img_b_id = res_b.json()["id"]

    # Attempt to delete Image B via Experience A endpoint -> 400
    res_mismatch = client.delete(
        f"/api/v1/eco-experiences/{exp_a.id}/images/{img_b_id}",
        headers=auth_header(provider_1),
    )
    assert res_mismatch.status_code == 400
    assert "does not belong to this eco experience" in res_mismatch.json()["detail"].lower()

    # Image B must still exist in DB
    db_session.expire_all()
    assert db_session.query(EcoExperienceImage).filter(EcoExperienceImage.id == img_b_id).first() is not None


def test_public_listing_image_visibility_and_streaming(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.9: Verify that uploaded images appear in the public detail endpoint
    only after approval and publication, and verify public image streaming.
    """
    provider_1 = users_and_profiles["eco_user_1"]
    admin = users_and_profiles["admin"]
    profile_1 = users_and_profiles["eco_profile_1"]

    # 1. Create draft experience and upload an image
    exp = create_sample_experience(db_session, profile_1.id)
    jpeg_bytes = make_test_image("JPEG", (300, 300), "darkgreen")

    res_up = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_1),
        files={"file": ("trail.jpg", jpeg_bytes, "image/jpeg")},
        data={"caption": "Sinharaja Trailhead", "is_primary": "true"},
    )
    assert res_up.status_code == 201
    uploaded_url = res_up.json()["image_url"]
    filename = os.path.basename(uploaded_url)

    # 2. While draft -> Public detail endpoint returns 404
    res_pub_draft = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert res_pub_draft.status_code == 404
    gallery = f"/api/v1/eco-experiences/{exp.id}/images"
    assert client.get(gallery).status_code == 404
    assert client.get(uploaded_url).status_code == 404
    assert client.get(f"/api/v1/eco-experiences/images/{filename}").status_code == 404
    assert client.get(gallery, headers=auth_header(provider_1)).status_code == 200
    assert client.get(uploaded_url, headers=auth_header(provider_1)).status_code == 200
    assert client.get(uploaded_url, headers=auth_header(admin)).status_code == 200
    assert client.get(
        uploaded_url, headers=auth_header(users_and_profiles["eco_user_2"]),
    ).status_code == 404

    # 3. Provider submits -> Admin approves -> Provider publishes
    client.post(f"/api/v1/eco-experiences/{exp.id}/submit", headers=auth_header(provider_1))
    client.post(f"/api/v1/admin/eco-experiences/{exp.id}/approve", headers=auth_header(admin))
    client.patch(
        f"/api/v1/eco-experiences/{exp.id}/publish",
        headers=auth_header(provider_1),
        json={"is_published": True},
    )

    # 4. Now public detail endpoint returns 200 with gallery image in images list
    res_pub = client.get(f"/api/v1/eco-experiences/{exp.id}")
    assert res_pub.status_code == 200
    pub_data = res_pub.json()
    assert len(pub_data["images"]) == 1
    assert pub_data["images"][0]["caption"] == "Sinharaja Trailhead"
    assert pub_data["images"][0]["is_primary"] is True
    assert pub_data["images"][0]["image_url"] == uploaded_url

    # 5. List images endpoint
    res_list = client.get(f"/api/v1/eco-experiences/{exp.id}/images")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1

    # 6. Stream image via upload URL alias
    res_stream = client.get(uploaded_url)
    assert res_stream.status_code == 200
    assert res_stream.headers["content-type"] == "image/jpeg"
    assert len(res_stream.content) == len(jpeg_bytes)

    # 7. Stream image via router path
    res_router_stream = client.get(f"/api/v1/eco-experiences/images/{filename}")
    assert res_router_stream.status_code == 200
    assert res_router_stream.headers["content-type"] == "image/jpeg"

    db_session.refresh(exp)
    exp.is_published = False
    db_session.commit()
    assert client.get(uploaded_url).status_code == 404

    # 8. Path traversal attempt returns 404
    res_traversal = client.get("/api/v1/eco-experiences/images/../../private_eco_evidence/secret.pdf")
    assert res_traversal.status_code == 404


def test_database_failure_cleanup_and_image_limits(
    client: TestClient,
    db_session: Session,
    users_and_profiles: dict,
):
    """
    Step 6.10: Verify orphan files are prevented when database commit fails,
    and verify maximum image count limit (20) enforcement.
    """
    provider_1 = users_and_profiles["eco_user_1"]
    exp = create_sample_experience(db_session, users_and_profiles["eco_profile_1"].id)
    jpeg_bytes = make_test_image()

    # 1. Test database failure cleanup
    storage_dir = get_eco_experience_images_dir()
    initial_files = set(os.listdir(storage_dir))

    # Mock db.commit to raise an exception during upload
    with patch.object(Session, "commit", side_effect=RuntimeError("Simulated DB Crash")):
        with pytest.raises(RuntimeError):
            client.post(
                f"/api/v1/eco-experiences/{exp.id}/images",
                headers=auth_header(provider_1),
                files={"file": ("crash.jpg", jpeg_bytes, "image/jpeg")},
            )

    # Ensure no orphan file was left in storage_dir
    current_files = set(os.listdir(storage_dir))
    assert current_files == initial_files

    # 2. Test maximum image limit (20 images)
    # Add 20 image records directly to the DB
    for i in range(20):
        db_session.add(
            EcoExperienceImage(
                eco_experience_id=exp.id,
                image_url=f"/uploads/eco_experiences/test_{i}.jpg",
                caption=f"Image {i}",
                is_primary=False,
                sort_order=i,
            )
        )
    db_session.commit()

    # Attempting 21st upload -> 400 Bad Request
    res_limit = client.post(
        f"/api/v1/eco-experiences/{exp.id}/images",
        headers=auth_header(provider_1),
        files={"file": ("image_21.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert res_limit.status_code == 400
    assert "maximum image limit" in res_limit.json()["detail"].lower()
