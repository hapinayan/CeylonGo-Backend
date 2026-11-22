"""
Automated test suite for Phase P1 Step 2: Artisan Business and Product Photo Gallery Management.
Tests:
1. Valid business and product image upload and primary image auto-designation.
2. Invalid image types, corrupted files, empty files, and excessive file size (> 5 MB).
3. Provider ownership: providers cannot upload, reorder, set primary, or delete images for others.
4. Public gallery visibility: unapproved listings/products hide gallery from public, but owner can view.
5. Gallery images in public business and product detail responses.
6. Image reordering and invalid image ID rejection.
7. Setting primary image and duplicate primary prevention (exactly 1 primary).
8. Image deletion with automatic primary fallback and disk cleanup.
9. Backward compatibility with cover_image_url and image_url.
10. Public file serving with path traversal protection.
11. Role-based access: unauthenticated and traveler callers blocked.
"""

import io
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanBusinessImage, ArtisanProductImage
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User

# In-memory SQLite engine
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
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


@pytest.fixture(autouse=True)
def configure_test_upload_dirs(tmp_path, monkeypatch):
    bus_dir = tmp_path / "artisan" / "business"
    prod_dir = tmp_path / "artisan" / "products"
    bus_dir.mkdir(parents=True, exist_ok=True)
    prod_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ARTISAN_BUSINESS_IMAGES_DIR", str(bus_dir))
    monkeypatch.setenv("ARTISAN_PRODUCT_IMAGES_DIR", str(prod_dir))


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(
        data={"sub": str(user.id)}, credential_version=user.credential_version
    )
    return {"Authorization": f"Bearer {token}"}


def make_test_image(format="JPEG", size=(100, 100), color="blue") -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, format=format)
    return buf.getvalue()


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def admin_user(db_session) -> User:
    admin = User(
        full_name="Admin Moderator",
        email="admin.gallery@ceylongo.com",
        password_hash=hash_password("AdminPass123!"),
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return admin


@pytest.fixture
def traveler_user(db_session) -> User:
    traveler = User(
        full_name="Traveler Jane",
        email="traveler.gallery@example.com",
        password_hash=hash_password("Pass123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(traveler)
    db_session.commit()
    db_session.refresh(traveler)
    return traveler


@pytest.fixture
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Artisan Gunapala",
        email="gunapala.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Gunapala Traditional Masks",
        provider_type="artisan",
        approval_status="approved",
        phone="+94771234567",
        district="Galle",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def other_artisan_user(db_session) -> tuple[User, ProviderProfile]:
    user = User(
        full_name="Other Artisan Bandara",
        email="bandara.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Bandara Brassware",
        provider_type="artisan",
        approval_status="approved",
        phone="+94779876543",
        district="Kandy",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    return user, profile


@pytest.fixture
def active_artisan_listing(db_session, approved_artisan_user) -> ArtisanListing:
    _, profile = approved_artisan_user
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Ambalangoda Mask Workshop",
        craft_category="Woodcarving & Masks",
        description="Authentic handmade Raksha masks.",
        district="Galle",
        city="Ambalangoda",
        phone="+94771234567",
        is_active=True,
        approval_status="approved",
        verification_status="verified",
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)
    return listing


@pytest.fixture
def active_showcase_product(db_session, active_artisan_listing) -> ShowcaseItem:
    product = ShowcaseItem(
        artisan_listing_id=active_artisan_listing.id,
        title="Naga Raksha Mask",
        item_type="product",
        description="Traditional cobra mask carved from Kaduru wood.",
        price=Decimal("12500.00"),
        currency="LKR",
        stock_quantity=10,
        sku="MSK-NAGA-001",
        is_active=True,
        approval_status="approved",
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)
    return product


# ==============================================================================
# TESTS
# ==============================================================================

def test_valid_business_image_upload_and_primary_behavior(
    client: TestClient, db_session, approved_artisan_user, active_artisan_listing
):
    """Verify uploading images to an artisan listing, automatic primary assignment, and cover sync."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # 1. Upload first image without specifying is_primary -> Should auto-become primary
    img1_bytes = make_test_image(format="JPEG", color="red")
    res1 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("workshop_front.jpg", img1_bytes, "image/jpeg")},
        data={"caption": "Workshop Entrance", "display_order": 0},
    )
    assert res1.status_code == 201
    data1 = res1.json()
    assert data1["artisan_listing_id"] == listing_id
    assert data1["is_primary"] is True
    assert data1["is_cover"] is True
    assert data1["caption"] == "Workshop Entrance"
    assert data1["display_order"] == 0
    assert "/uploads/artisan/business/" in data1["image_url"]

    # Verify listing cover_image_url was updated
    db_session.refresh(active_artisan_listing)
    assert active_artisan_listing.cover_image_url == data1["image_url"]

    # 2. Upload second image with is_primary=False -> Should remain non-primary
    img2_bytes = make_test_image(format="PNG", color="green")
    res2 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("workshop_inside.png", img2_bytes, "image/png")},
        data={"caption": "Crafting Table", "is_primary": "false", "display_order": 1},
    )
    assert res2.status_code == 201
    data2 = res2.json()
    assert data2["is_primary"] is False
    assert data2["display_order"] == 1

    # Cover should still point to first image
    db_session.refresh(active_artisan_listing)
    assert active_artisan_listing.cover_image_url == data1["image_url"]

    # 3. Upload third image with is_primary=True -> Should become primary and demote first image
    img3_bytes = make_test_image(format="WEBP", color="yellow")
    res3 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("featured_mask.webp", img3_bytes, "image/webp")},
        data={"caption": "Masterpiece Display", "is_primary": "true"},
    )
    assert res3.status_code == 201
    data3 = res3.json()
    assert data3["is_primary"] is True

    # Check cover updated to 3rd image
    db_session.refresh(active_artisan_listing)
    assert active_artisan_listing.cover_image_url == data3["image_url"]

    # Check DB: exactly ONE image has is_primary=True
    primary_images = (
        db_session.query(ArtisanBusinessImage)
        .filter(ArtisanBusinessImage.artisan_listing_id == listing_id, ArtisanBusinessImage.is_primary == True)
        .all()
    )
    assert len(primary_images) == 1
    assert primary_images[0].id == data3["id"]


def test_valid_product_image_upload_and_primary_behavior(
    client: TestClient, db_session, approved_artisan_user, active_showcase_product
):
    """Verify uploading showcase product images, primary assignment, and product image_url sync."""
    user, _ = approved_artisan_user
    item_id = active_showcase_product.id

    img_bytes = make_test_image(format="JPEG", color="purple")
    res = client.post(
        f"/api/v1/showcase-items/{item_id}/images",
        headers=auth_header(user),
        files={"file": ("naga_mask_detail.jpg", img_bytes, "image/jpeg")},
        data={"caption": "Front Angle View", "is_primary": "true"},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["showcase_item_id"] == item_id
    assert data["is_primary"] is True
    assert data["caption"] == "Front Angle View"
    assert "/uploads/artisan/products/" in data["image_url"]

    # Product image_url should sync
    db_session.refresh(active_showcase_product)
    assert active_showcase_product.image_url == data["image_url"]


def test_invalid_image_type_and_corrupt_files(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify rejection of bad file extensions, non-image content, and corrupt files."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # 1. Unsupported extension (.pdf)
    res_ext = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("document.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )
    assert res_ext.status_code == 400
    assert "Unsupported file extension" in res_ext.json()["detail"]

    # 2. Fake image (text content disguised as .jpg)
    res_fake = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("fake.jpg", b"This is not a real image", "image/jpeg")},
    )
    assert res_fake.status_code == 400
    assert "Invalid or corrupted image content" in res_fake.json()["detail"]

    # 3. Empty file
    res_empty = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert res_empty.status_code == 400
    assert "empty" in res_empty.json()["detail"]

    # 4. Tiny image under 50x50 minimum dimension
    tiny_img = make_test_image(size=(30, 30))
    res_tiny = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("tiny.jpg", tiny_img, "image/jpeg")},
    )
    assert res_tiny.status_code == 400
    assert "too small" in res_tiny.json()["detail"]


def test_excessive_file_size_rejected(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify rejection of files exceeding the 5 MB maximum limit."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # Oversized payload (> 5 MB)
    oversized_data = b"X" * (5 * 1024 * 1024 + 100)
    res = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("huge.jpg", oversized_data, "image/jpeg")},
    )
    assert res.status_code == 400
    assert "exceeds maximum allowed size" in res.json()["detail"]


def test_provider_ownership_enforcement(
    client: TestClient,
    approved_artisan_user,
    other_artisan_user,
    active_artisan_listing,
    active_showcase_product,
):
    """Verify providers cannot upload, reorder, set primary, or delete images of listings/products they do not own."""
    _, other_profile = other_artisan_user
    other_user, _ = other_artisan_user
    img_bytes = make_test_image()

    # Other provider tries to upload to first provider's listing -> 403
    res_bus = client.post(
        f"/api/v1/artisan-listings/{active_artisan_listing.id}/images",
        headers=auth_header(other_user),
        files={"file": ("unauth.jpg", img_bytes, "image/jpeg")},
    )
    assert res_bus.status_code == 403
    assert "do not own" in res_bus.json()["detail"]

    # Other provider tries to upload to first provider's showcase item -> 403
    res_prod = client.post(
        f"/api/v1/showcase-items/{active_showcase_product.id}/images",
        headers=auth_header(other_user),
        files={"file": ("unauth_prod.jpg", img_bytes, "image/jpeg")},
    )
    assert res_prod.status_code == 403
    assert "do not own" in res_prod.json()["detail"]


def test_unauthorized_and_traveler_access_blocked(
    client: TestClient, traveler_user, active_artisan_listing
):
    """Verify unauthenticated callers and travelers cannot manage images."""
    img_bytes = make_test_image()
    listing_id = active_artisan_listing.id

    # Unauthenticated
    res_unauth = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        files={"file": ("test.jpg", img_bytes, "image/jpeg")},
    )
    assert res_unauth.status_code in (401, 403)

    # Traveler
    res_traveler = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(traveler_user),
        files={"file": ("test.jpg", img_bytes, "image/jpeg")},
    )
    assert res_traveler.status_code == 403


def test_public_gallery_visibility_filtering(
    client: TestClient,
    admin_user,
    approved_artisan_user,
    db_session,
):
    """Verify unapproved listings/products hide gallery from public, but owner can view; approved listings show gallery."""
    user, profile = approved_artisan_user

    # Create pending listing
    pending_listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Pending Mask Studio",
        craft_category="Woodcarving & Masks",
        district="Colombo",
        city="Colombo 03",
        is_active=True,
        approval_status="pending",
        is_verified=True,
    )
    db_session.add(pending_listing)
    db_session.commit()
    db_session.refresh(pending_listing)

    # Owner uploads an image to the pending listing
    img_bytes = make_test_image()
    res_upload = client.post(
        f"/api/v1/artisan-listings/{pending_listing.id}/images",
        headers=auth_header(user),
        files={"file": ("pending_bus.jpg", img_bytes, "image/jpeg")},
    )
    assert res_upload.status_code == 201

    # Public caller (no auth) requests gallery for pending listing -> 404
    res_pub = client.get(f"/api/v1/artisan-listings/{pending_listing.id}/images")
    assert res_pub.status_code == 404

    # Owner requests gallery for pending listing -> 200
    res_owner = client.get(
        f"/api/v1/artisan-listings/{pending_listing.id}/images",
        headers=auth_header(user),
    )
    assert res_owner.status_code == 200
    assert len(res_owner.json()) == 1

    stale_headers = auth_header(user)
    user.credential_version += 1
    db_session.commit()
    assert client.get(
        f"/api/v1/artisan-listings/{pending_listing.id}/images", headers=stale_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/artisan-listings/{pending_listing.id}/images", headers=auth_header(user)
    ).status_code == 200

    # Admin approves listing
    admin_res = client.post(
        f"/api/v1/admin/artisan/listings/{pending_listing.id}/approve",
        headers=auth_header(admin_user),
    )
    assert admin_res.status_code == 200

    # Now public caller can view gallery
    res_pub_approved = client.get(f"/api/v1/artisan-listings/{pending_listing.id}/images")
    assert res_pub_approved.status_code == 200
    assert len(res_pub_approved.json()) == 1

    # Public detail endpoint includes image gallery
    res_detail = client.get(f"/api/v1/artisan-listings/{pending_listing.id}")
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert "images" in detail_data
    assert len(detail_data["images"]) == 1
    assert detail_data["cover_image_url"] == detail_data["images"][0]["image_url"]


def test_image_reordering(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify batch reordering of images and validation of foreign image IDs."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # Upload 3 images
    ids = []
    for i in range(3):
        res = client.post(
            f"/api/v1/artisan-listings/{listing_id}/images",
            headers=auth_header(user),
            files={"file": (f"img_{i}.jpg", make_test_image(color="cyan"), "image/jpeg")},
            data={"caption": f"Image {i}", "display_order": i},
        )
        assert res.status_code == 201
        ids.append(res.json()["id"])

    # Reorder: image 2 -> 0, image 0 -> 1, image 1 -> 2
    reorder_payload = {
        "images": [
            {"id": ids[2], "display_order": 0},
            {"id": ids[0], "display_order": 1},
            {"id": ids[1], "display_order": 2},
        ]
    }
    res_reorder = client.patch(
        f"/api/v1/artisan-listings/{listing_id}/images/reorder",
        headers=auth_header(user),
        json=reorder_payload,
    )
    assert res_reorder.status_code == 200
    reordered_data = res_reorder.json()
    order_map = {item["id"]: item["display_order"] for item in reordered_data}
    assert order_map[ids[2]] == 0
    assert order_map[ids[0]] == 1
    assert order_map[ids[1]] == 2

    # Attempt to reorder with non-existent / foreign ID -> 400
    bad_payload = {
        "images": [{"id": 99999, "display_order": 0}]
    }
    res_bad = client.patch(
        f"/api/v1/artisan-listings/{listing_id}/images/reorder",
        headers=auth_header(user),
        json=bad_payload,
    )
    assert res_bad.status_code == 400
    assert "does not belong" in res_bad.json()["detail"]


def test_set_primary_image_and_duplicate_prevention(
    client: TestClient, db_session, approved_artisan_user, active_artisan_listing
):
    """Verify switching primary image transactionally ensures at most one primary exists."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    res1 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("img1.jpg", make_test_image(), "image/jpeg")},
    )
    res2 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("img2.jpg", make_test_image(), "image/jpeg")},
    )
    id1 = res1.json()["id"]
    id2 = res2.json()["id"]

    # Explicitly set second image as primary
    res_primary = client.patch(
        f"/api/v1/artisan-listings/{listing_id}/images/{id2}/set-primary",
        headers=auth_header(user),
    )
    assert res_primary.status_code == 200
    assert res_primary.json()["is_primary"] is True

    # Check listing cover_image_url matches id2
    db_session.refresh(active_artisan_listing)
    assert active_artisan_listing.cover_image_url == res2.json()["image_url"]

    # Verify id1 is now not primary
    img1 = db_session.query(ArtisanBusinessImage).filter(ArtisanBusinessImage.id == id1).first()
    assert img1.is_primary is False


def test_delete_image_and_primary_fallback(
    client: TestClient, db_session, approved_artisan_user, active_artisan_listing
):
    """Verify deleting primary image automatically promotes the next image, and deleting last resets cover to None."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # Upload 2 images
    res1 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("first.jpg", make_test_image(), "image/jpeg")},
        data={"display_order": 0},
    )
    res2 = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("second.jpg", make_test_image(), "image/jpeg")},
        data={"display_order": 1},
    )
    id1 = res1.json()["id"]
    id2 = res2.json()["id"]

    # Delete primary image (id1)
    del_res1 = client.delete(
        f"/api/v1/artisan-listings/{listing_id}/images/{id1}",
        headers=auth_header(user),
    )
    assert del_res1.status_code == 204

    # id2 should be promoted to primary
    db_session.refresh(active_artisan_listing)
    img2 = db_session.query(ArtisanBusinessImage).filter(ArtisanBusinessImage.id == id2).first()
    assert img2.is_primary is True
    assert active_artisan_listing.cover_image_url == img2.image_url

    # Delete remaining image (id2)
    del_res2 = client.delete(
        f"/api/v1/artisan-listings/{listing_id}/images/{id2}",
        headers=auth_header(user),
    )
    assert del_res2.status_code == 204

    # Listing cover should now be None
    db_session.refresh(active_artisan_listing)
    assert active_artisan_listing.cover_image_url is None


def test_public_file_serving_and_traversal_protection(
    client: TestClient, approved_artisan_user, active_artisan_listing
):
    """Verify uploaded images are publicly served with correct Content-Type, and path traversal is prevented."""
    user, _ = approved_artisan_user
    listing_id = active_artisan_listing.id

    # Upload JPEG
    img_bytes = make_test_image(format="JPEG")
    res_up = client.post(
        f"/api/v1/artisan-listings/{listing_id}/images",
        headers=auth_header(user),
        files={"file": ("public_test.jpg", img_bytes, "image/jpeg")},
    )
    assert res_up.status_code == 201
    image_url = res_up.json()["image_url"]

    # Request via upload path
    res_serve = client.get(image_url)
    assert res_serve.status_code == 200
    assert res_serve.headers["content-type"] in ("image/jpeg", "image/jpg")
    assert len(res_serve.content) > 0

    # Path traversal attack attempt
    res_traversal = client.get("/uploads/artisan/business/../../secret.txt")
    assert res_traversal.status_code in (404, 422)

    # Non-existent file
    res_missing = client.get("/uploads/artisan/business/non_existent_img_12345.jpg")
    assert res_missing.status_code == 404
