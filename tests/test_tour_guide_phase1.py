"""
Comprehensive Phase 1 Automated Tests for Local Tour Guide Module 05.

Covers all 12 Phase 1 deliverables:
1. Provider guide language management (CRUD, uniqueness, primary swap, RBAC)
2. Provider service area management (CRUD, uniqueness, primary swap, RBAC)
3. Guide categories and specialties validation (4 canonical categories + currency)
4. Profile image gallery management (Pillow validation, primary swap, replace, delete, serving)
5. Private qualification & credential management (privacy, no-store streaming, audit lock)
6. Tour packages, pricing, meeting points, and duration management
7. Availability calendar, overlap detection, bulk blocking, and active-booking conflicts
8. Provider contact & verification status management
9. Admin approval, rejection, suspension, reinstatement, audit logs & notifications
10. Admin credential verification & evidence-gated Verified Guide Badge
11. Centralized dbo.listing_references synchronization
12. Security, RBAC, ownership isolation & path traversal protections
"""

from datetime import UTC, date, datetime, time
from decimal import Decimal
import io

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, ListingReference, Notification
from app.models.tour_guide import (
    TourGuide,
    TourGuideBooking,
    TourGuidePackage,
    TourGuideVerification,
)
from app.models.user import Base, User

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
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

PROVIDER_BASE = "/api/v1/provider/tour-guides"
ADMIN_BASE = "/api/v1/admin/tour-guides"


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def make_png_bytes(color: tuple[int, int, int] = (34, 139, 34)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color=color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_jpeg_bytes(color: tuple[int, int, int] = (10, 80, 160)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 64), color=color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


def make_pdf_bytes() -> bytes:
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setenv("TOUR_GUIDE_IMAGES_DIR", str(tmp_path / "guide_images"))
    monkeypatch.setenv("TOUR_GUIDE_CREDENTIALS_DIR", str(tmp_path / "guide_credentials"))
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session() -> Session:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def users_and_guides(db_session: Session):
    admin = User(
        full_name="System Admin",
        email="admin.tg@example.com",
        role="admin",
        is_active=True,
    )
    traveler = User(
        full_name="Maya Traveler",
        email="maya.traveler@example.com",
        role="traveler",
        is_active=True,
    )
    provider_a_user = User(
        full_name="Kasun Perera",
        email="kasun.guide@example.com",
        role="provider",
        is_active=True,
    )
    provider_b_user = User(
        full_name="Nimal Silva",
        email="nimal.guide@example.com",
        role="provider",
        is_active=True,
    )
    homestay_provider_user = User(
        full_name="Saman Host",
        email="saman.host@example.com",
        role="provider",
        is_active=True,
    )
    db_session.add_all(
        [admin, traveler, provider_a_user, provider_b_user, homestay_provider_user]
    )
    db_session.commit()

    profile_a = ProviderProfile(
        user_id=provider_a_user.id,
        business_name="Kasun Heritage Tours",
        provider_type="tour_guide",
        phone="0771234567",
        district="Kandy",
        approval_status="approved",
    )
    profile_b = ProviderProfile(
        user_id=provider_b_user.id,
        business_name="Nimal Wildlife Walks",
        provider_type="tour_guide",
        phone="0779876543",
        district="Galle",
        approval_status="approved",
    )
    profile_homestay = ProviderProfile(
        user_id=homestay_provider_user.id,
        business_name="Saman Villa",
        provider_type="homestay",
        phone="0711112222",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add_all([profile_a, profile_b, profile_homestay])
    db_session.commit()

    guide_a = TourGuide(
        provider_profile_id=profile_a.id,
        bio="Experienced licensed tour guide covering Kandy and the Cultural Triangle.",
        years_of_experience=8,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("15000.00"),
        hourly_rate=Decimal("2500.00"),
        currency="LKR",
        base_city="Kandy",
        base_district="Kandy",
        base_latitude=Decimal("7.290572"),
        base_longitude=Decimal("80.633728"),
        approval_status="draft",
        verification_status="unverified",
        is_active=False,
    )
    guide_b = TourGuide(
        provider_profile_id=profile_b.id,
        bio="Galle Fort walking tour specialist and birdwatching local guide.",
        years_of_experience=5,
        specialties="City & Village, Wildlife & Eco",
        daily_rate=Decimal("12000.00"),
        currency="LKR",
        base_city="Galle",
        base_district="Galle",
        base_latitude=Decimal("6.032895"),
        base_longitude=Decimal("80.216791"),
        approval_status="approved",
        verification_status="unverified",
        is_active=True,
    )
    db_session.add_all([guide_a, guide_b])
    db_session.commit()

    return {
        "admin": admin,
        "traveler": traveler,
        "provider_a": provider_a_user,
        "provider_b": provider_b_user,
        "homestay_provider": homestay_provider_user,
        "profile_a": profile_a,
        "profile_b": profile_b,
        "guide_a": guide_a,
        "guide_b": guide_b,
    }


# =============================================================================
# 1. CATEGORIES & SPECIALTIES VALIDATION (PR-07 / TR-03)
# =============================================================================

def test_public_categories_and_provider_category_validation(client, users_and_guides):
    cats_res = client.get("/api/v1/tour-guides/categories")
    assert cats_res.status_code == 200
    slugs = [c["slug"] for c in cats_res.json()]
    assert slugs == [
        "cultural_heritage",
        "adventure_nature",
        "city_village",
        "wildlife_eco",
    ]

    headers_a = bearer(users_and_guides["provider_a"])

    # Valid alias normalization on PATCH specialties
    patch_res = client.patch(
        PROVIDER_BASE,
        json={"specialties": "cultural, wildlife/eco, Cultural & Heritage"},
        headers=headers_a,
    )
    assert patch_res.status_code == 200, patch_res.text
    assert patch_res.json()["specialties"] == "Cultural & Heritage, Wildlife & Eco"
    assert patch_res.json()["categories"] == ["cultural_heritage", "wildlife_eco"]

    # Invalid specialty rejected
    bad_spec = client.patch(
        PROVIDER_BASE,
        json={"specialties": "Space Tourism"},
        headers=headers_a,
    )
    assert bad_spec.status_code == 422

    # Unsupported currency rejected
    bad_curr = client.patch(
        PROVIDER_BASE,
        json={"currency": "XYZ"},
        headers=headers_a,
    )
    assert bad_curr.status_code == 422

    # PUT /categories updates specialties cleanly
    put_res = client.put(
        f"{PROVIDER_BASE}/categories",
        json={"categories": ["adventure_nature", "city_village"]},
        headers=headers_a,
    )
    assert put_res.status_code == 200
    assert put_res.json()["specialties"] == "Adventure & Nature, City & Village"
    assert put_res.json()["categories"] == ["adventure_nature", "city_village"]

    # Empty or invalid categories rejected
    assert client.put(
        f"{PROVIDER_BASE}/categories", json={"categories": []}, headers=headers_a
    ).status_code == 422
    assert client.put(
        f"{PROVIDER_BASE}/categories", json={"categories": ["unknown"]}, headers=headers_a
    ).status_code == 422


# =============================================================================
# 2. LANGUAGE MANAGEMENT CRUD & ISOLATION (PR-05)
# =============================================================================

def test_provider_languages_crud_and_isolation(client, users_and_guides):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_b = bearer(users_and_guides["provider_b"])

    # Create primary English
    r1 = client.post(
        f"{PROVIDER_BASE}/languages",
        json={
            "language_code": "EN",
            "language_name": "English",
            "proficiency_level": "fluent",
            "is_primary": True,
        },
        headers=headers_a,
    )
    assert r1.status_code == 201, r1.text
    lang_en = r1.json()
    assert lang_en["language_code"] == "en"
    assert lang_en["is_primary"] is True

    # Duplicate language_code rejected (409)
    dup = client.post(
        f"{PROVIDER_BASE}/languages",
        json={"language_code": "en", "language_name": "English", "proficiency_level": "native"},
        headers=headers_a,
    )
    assert dup.status_code == 409

    # Invalid language_code & proficiency rejected (422)
    assert client.post(
        f"{PROVIDER_BASE}/languages",
        json={"language_code": "1234", "language_name": "Bad", "proficiency_level": "fluent"},
        headers=headers_a,
    ).status_code == 422
    assert client.post(
        f"{PROVIDER_BASE}/languages",
        json={"language_code": "si", "language_name": "Sinhala", "proficiency_level": "expert"},
        headers=headers_a,
    ).status_code == 422

    # Adding Sinhala as primary clears primary from English
    r2 = client.post(
        f"{PROVIDER_BASE}/languages",
        json={
            "language_code": "si",
            "language_name": "Sinhala",
            "proficiency_level": "native",
            "is_primary": True,
        },
        headers=headers_a,
    )
    assert r2.status_code == 201
    lang_si = r2.json()

    listed = client.get(f"{PROVIDER_BASE}/languages", headers=headers_a).json()
    assert len(listed) == 2
    assert listed[0]["language_code"] == "si" and listed[0]["is_primary"] is True
    assert listed[1]["language_code"] == "en" and listed[1]["is_primary"] is False

    # Update English back to primary
    upd = client.patch(
        f"{PROVIDER_BASE}/languages/{lang_en['id']}",
        json={"proficiency_level": "native", "is_primary": True},
        headers=headers_a,
    )
    assert upd.status_code == 200
    assert upd.json()["proficiency_level"] == "native"
    assert upd.json()["is_primary"] is True

    # Cross-guide isolation: Guide B cannot update or delete Guide A's language
    assert client.patch(
        f"{PROVIDER_BASE}/languages/{lang_en['id']}",
        json={"proficiency_level": "basic"},
        headers=headers_b,
    ).status_code == 404
    assert client.delete(
        f"{PROVIDER_BASE}/languages/{lang_en['id']}", headers=headers_b
    ).status_code == 404

    # Delete Sinhala
    assert client.delete(
        f"{PROVIDER_BASE}/languages/{lang_si['id']}", headers=headers_a
    ).status_code == 204
    assert len(client.get(f"{PROVIDER_BASE}/languages", headers=headers_a).json()) == 1


# =============================================================================
# 3. SERVICE AREAS MANAGEMENT CRUD & ISOLATION (PR-06)
# =============================================================================

def test_provider_service_areas_crud_and_isolation(client, users_and_guides):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_b = bearer(users_and_guides["provider_b"])

    r1 = client.post(
        f"{PROVIDER_BASE}/service-areas",
        json={"province": "Central", "district": "Kandy", "city": "Kandy", "is_primary": True},
        headers=headers_a,
    )
    assert r1.status_code == 201, r1.text
    area1 = r1.json()

    # Duplicate (district, city) rejected
    assert client.post(
        f"{PROVIDER_BASE}/service-areas",
        json={"province": "Central", "district": "Kandy", "city": "Kandy"},
        headers=headers_a,
    ).status_code == 409

    r2 = client.post(
        f"{PROVIDER_BASE}/service-areas",
        json={"province": "Central", "district": "Matale", "city": "Sigiriya", "is_primary": True},
        headers=headers_a,
    )
    assert r2.status_code == 201
    area2 = r2.json()

    listed = client.get(f"{PROVIDER_BASE}/service-areas", headers=headers_a).json()
    assert len(listed) == 2
    assert listed[0]["id"] == area2["id"] and listed[0]["is_primary"] is True
    assert listed[1]["id"] == area1["id"] and listed[1]["is_primary"] is False

    # Update area2 to collide with area1 -> 409
    assert client.patch(
        f"{PROVIDER_BASE}/service-areas/{area2['id']}",
        json={"district": "Kandy", "city": "Kandy"},
        headers=headers_a,
    ).status_code == 409

    # Valid update
    upd = client.patch(
        f"{PROVIDER_BASE}/service-areas/{area2['id']}",
        json={"city": "Dambulla"},
        headers=headers_a,
    )
    assert upd.status_code == 200 and upd.json()["city"] == "Dambulla"

    # Cross-guide isolation
    assert client.delete(
        f"{PROVIDER_BASE}/service-areas/{area2['id']}", headers=headers_b
    ).status_code == 404

    assert client.delete(
        f"{PROVIDER_BASE}/service-areas/{area2['id']}", headers=headers_a
    ).status_code == 204


# =============================================================================
# 4. PROFILE IMAGE GALLERY MANAGEMENT (PR-03)
# =============================================================================

def test_provider_image_gallery_upload_replace_primary_and_delete(client, users_and_guides):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_b = bearer(users_and_guides["provider_b"])

    # Invalid file bytes rejected
    bad_img = client.post(
        f"{PROVIDER_BASE}/images",
        files={"file": ("fake.png", b"not-a-real-image", "image/png")},
        headers=headers_a,
    )
    assert bad_img.status_code == 400

    # Disallowed extension rejected
    bad_ext = client.post(
        f"{PROVIDER_BASE}/images",
        files={"file": ("script.svg", b"<svg></svg>", "image/svg+xml")},
        headers=headers_a,
    )
    assert bad_ext.status_code == 400

    # Upload primary profile image
    u1 = client.post(
        f"{PROVIDER_BASE}/images",
        files={"file": ("profile.png", make_png_bytes(), "image/png")},
        data={"caption": "At Temple of the Tooth", "display_order": "0", "is_profile": "true"},
        headers=headers_a,
    )
    assert u1.status_code == 201, u1.text
    img1 = u1.json()
    assert img1["is_profile"] is True
    assert img1["image_url"].startswith("/api/v1/uploads/tour-guides/")

    # Public image serving works
    served = client.get(img1["image_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.headers["x-content-type-options"] == "nosniff"

    # Uploading a second is_profile=true cleanly replaces primary without constraint error
    u2 = client.post(
        f"{PROVIDER_BASE}/images",
        files={"file": ("tour.jpg", make_jpeg_bytes(), "image/jpeg")},
        data={"caption": "Knuckles hike", "display_order": "1", "is_profile": "true"},
        headers=headers_a,
    )
    assert u2.status_code == 201, u2.text
    img2 = u2.json()
    assert img2["is_profile"] is True

    gallery = client.get(f"{PROVIDER_BASE}/images", headers=headers_a).json()
    assert len(gallery) == 2
    assert sum(1 for item in gallery if item["is_profile"]) == 1
    assert gallery[0]["id"] == img2["id"]

    # Patch img1 back to primary & update caption
    p1 = client.patch(
        f"{PROVIDER_BASE}/images/{img1['id']}",
        json={"caption": "Updated profile photo", "is_profile": True, "display_order": 0},
        headers=headers_a,
    )
    assert p1.status_code == 200 and p1.json()["is_profile"] is True

    # Replace file in-place
    old_url = img1["image_url"]
    rep = client.put(
        f"{PROVIDER_BASE}/images/{img1['id']}/file",
        files={"file": ("new.jpg", make_jpeg_bytes((200, 50, 50)), "image/jpeg")},
        headers=headers_a,
    )
    assert rep.status_code == 200
    new_url = rep.json()["image_url"]
    assert new_url != old_url
    assert client.get(new_url).status_code == 200
    assert client.get(old_url).status_code == 404

    # Cross-guide isolation
    assert client.delete(f"{PROVIDER_BASE}/images/{img1['id']}", headers=headers_b).status_code == 404

    # Delete image removes file too
    assert client.delete(f"{PROVIDER_BASE}/images/{img1['id']}", headers=headers_a).status_code == 204
    assert client.get(new_url).status_code == 404


# =============================================================================
# 5. PRIVATE CREDENTIALS & CONFIDENTIALITY (PR-04 / GS-02)
# =============================================================================

def test_private_credentials_upload_privacy_and_access_control(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_b = bearer(users_and_guides["provider_b"])
    headers_traveler = bearer(users_and_guides["traveler"])
    headers_admin = bearer(users_and_guides["admin"])

    # Invalid PDF header rejected
    bad_pdf = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("license.pdf", b"not-a-pdf", "application/pdf")},
        data={
            "credential_type": "sltda_license",
            "title": "SLTDA National License",
            "issuing_authority": "SLTDA",
        },
        headers=headers_a,
    )
    assert bad_pdf.status_code == 400

    # Expiry before issue date rejected
    bad_dates = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("license.pdf", make_pdf_bytes(), "application/pdf")},
        data={
            "credential_type": "sltda_license",
            "title": "SLTDA National License",
            "issuing_authority": "SLTDA",
            "issue_date": "2026-06-01",
            "expiry_date": "2025-06-01",
        },
        headers=headers_a,
    )
    assert bad_dates.status_code == 422

    # Valid private credential upload
    up = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("sltda_license.pdf", make_pdf_bytes(), "application/pdf")},
        data={
            "credential_type": "sltda_license",
            "title": "SLTDA National Guide License",
            "issuing_authority": "Sri Lanka Tourism Development Authority",
            "certificate_number": "SLTDA-2026-1001",
            "issue_date": "2024-01-15",
            "expiry_date": "2028-01-15",
        },
        headers=headers_a,
    )
    assert up.status_code == 201, up.text
    cred = up.json()
    assert cred["is_confidential"] is True
    assert cred["verification_status"] == "pending"
    assert cred["has_document"] is True
    # CRITICAL PRIVACY CHECK: file_reference must NEVER leak in API responses
    assert "file_reference" not in cred

    # Guide verification_status moved from unverified -> pending
    db_session.refresh(users_and_guides["guide_a"])
    assert users_and_guides["guide_a"].verification_status == "pending"

    # Owner can view own document with anti-caching headers
    doc_owner = client.get(
        f"{PROVIDER_BASE}/credentials/{cred['id']}/document", headers=headers_a
    )
    assert doc_owner.status_code == 200
    assert doc_owner.headers["content-type"] == "application/pdf"
    assert doc_owner.headers["cache-control"] == "no-store"
    assert doc_owner.headers["x-content-type-options"] == "nosniff"

    # Admin can view private document
    doc_admin = client.get(
        f"{ADMIN_BASE}/credentials/{cred['id']}/document", headers=headers_admin
    )
    assert doc_admin.status_code == 200
    assert doc_admin.headers["cache-control"] == "no-store"

    # Other guide, traveler, and anonymous are strictly blocked
    assert client.get(
        f"{PROVIDER_BASE}/credentials/{cred['id']}/document", headers=headers_b
    ).status_code == 404
    assert client.get(
        f"{PROVIDER_BASE}/credentials/{cred['id']}/document", headers=headers_traveler
    ).status_code == 403
    assert client.get(
        f"{PROVIDER_BASE}/credentials/{cred['id']}/document"
    ).status_code == 401
    assert client.get(
        f"{ADMIN_BASE}/credentials/{cred['id']}/document", headers=headers_a
    ).status_code == 403

    # Update metadata & delete unreviewed credential
    upd = client.patch(
        f"{PROVIDER_BASE}/credentials/{cred['id']}",
        json={"title": "SLTDA National Guide License (Renewed)"},
        headers=headers_a,
    )
    assert upd.status_code == 200
    assert upd.json()["verification_status"] == "pending"
    assert "file_reference" not in upd.json()

    # Delete unreviewed credential succeeds
    assert client.delete(
        f"{PROVIDER_BASE}/credentials/{cred['id']}", headers=headers_a
    ).status_code == 204


# =============================================================================
# 6. TOUR PACKAGES, PRICING, MEETING POINTS & DURATION (PR-08 / PR-09 / GS-03..05)
# =============================================================================

def test_provider_packages_crud_validation_and_booking_protection(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_b = bearer(users_and_guides["provider_b"])

    # Create half-day package
    p1 = client.post(
        f"{PROVIDER_BASE}/packages",
        json={
            "title": "Kandy Heritage Half-Day Walk",
            "description": "Guided cultural walk covering Temple of the Tooth and Royal Palace.",
            "itinerary": "08:00 Meet -> 08:30 Temple -> 11:30 Market",
            "duration_hours": "4.00",
            "pricing_type": "per_person",
            "price": "4500.00",
            "currency": "lkr",
            "max_people": 8,
            "included_services": "Licensed guide, bottled water",
            "excluded_services": "Temple entry ticket",
            "meeting_point": "Kandy City Centre Main Entrance",
            "meeting_latitude": "7.292500",
            "meeting_longitude": "80.637000",
        },
        headers=headers_a,
    )
    assert p1.status_code == 201, p1.text
    pkg = p1.json()
    assert pkg["currency"] == "LKR"
    assert pkg["duration_category"] == "half_day"

    # Full-day and multi-day duration classification
    p2 = client.post(
        f"{PROVIDER_BASE}/packages",
        json={
            "title": "Cultural Triangle 2-Day Private Tour",
            "description": "Two-day private cultural tour across Sigiriya, Polonnaruwa and Dambulla.",
            "duration_hours": "36.00",
            "pricing_type": "per_group",
            "price": "45000.00",
            "currency": "LKR",
            "max_people": 6,
            "meeting_point": "Kandy Railway Station",
        },
        headers=headers_a,
    )
    assert p2.status_code == 201 and p2.json()["duration_category"] == "multi_day"

    # Rejects half-null coordinates on POST and partial PATCH
    assert client.post(
        f"{PROVIDER_BASE}/packages",
        json={
            "title": "Bad Coords Tour",
            "description": "Tour with only latitude supplied which must fail validation.",
            "duration_hours": "3.00",
            "price": "3000.00",
            "meeting_point": "Clock Tower",
            "meeting_latitude": "7.290000",
        },
        headers=headers_a,
    ).status_code == 422

    assert client.patch(
        f"{PROVIDER_BASE}/packages/{pkg['id']}",
        json={"meeting_latitude": None},
        headers=headers_a,
    ).status_code == 422

    # Valid update
    upd = client.patch(
        f"{PROVIDER_BASE}/packages/{pkg['id']}",
        json={"price": "5000.00", "max_people": 12},
        headers=headers_a,
    )
    assert upd.status_code == 200
    assert Decimal(upd.json()["price"]) == Decimal("5000.00")
    assert upd.json()["max_people"] == 12

    # Cross-guide isolation
    assert client.get(f"{PROVIDER_BASE}/packages/{pkg['id']}", headers=headers_b).status_code == 404
    assert client.delete(f"{PROVIDER_BASE}/packages/{pkg['id']}", headers=headers_b).status_code == 404

    # Attach a booking to pkg -> DELETE must soft-deactivate instead of failing FK
    booking = TourGuideBooking(
        tour_guide_id=users_and_guides["guide_a"].id,
        traveler_id=users_and_guides["traveler"].id,
        package_id=pkg["id"],
        booking_date=date(2026, 12, 20),
        start_datetime=datetime(2026, 12, 20, 8, 0),
        end_datetime=datetime(2026, 12, 20, 12, 0),
        number_of_people=2,
        total_price=Decimal("10000.00"),
        booking_status="confirmed",
    )
    db_session.add(booking)
    db_session.commit()

    assert client.delete(f"{PROVIDER_BASE}/packages/{pkg['id']}", headers=headers_a).status_code == 204
    persisted = db_session.get(TourGuidePackage, pkg["id"])
    assert persisted is not None and persisted.is_active is False

    # Unused package p2 is hard-deleted
    p2_id = p2.json()["id"]
    assert client.delete(f"{PROVIDER_BASE}/packages/{p2_id}", headers=headers_a).status_code == 204
    assert db_session.get(TourGuidePackage, p2_id) is None


# =============================================================================
# 7. AVAILABILITY CALENDAR & OVERLAP VALIDATION (PR-10)
# =============================================================================

def test_provider_availability_overlaps_bulk_block_and_booking_conflicts(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])

    # Morning open slot 08:00 - 12:00
    s1 = client.post(
        f"{PROVIDER_BASE}/availability",
        json={
            "available_date": "2026-12-10",
            "start_time": "08:00:00",
            "end_time": "12:00:00",
            "is_available": True,
        },
        headers=headers_a,
    )
    assert s1.status_code == 201, s1.text

    # Adjacent afternoon slot 12:00 - 16:00 is allowed (no overlap)
    s2 = client.post(
        f"{PROVIDER_BASE}/availability",
        json={
            "available_date": "2026-12-10",
            "start_time": "12:00:00",
            "end_time": "16:00:00",
            "is_available": True,
        },
        headers=headers_a,
    )
    assert s2.status_code == 201, s2.text

    # Overlapping timed slot 11:00 - 13:00 -> 409 Conflict
    overlap_timed = client.post(
        f"{PROVIDER_BASE}/availability",
        json={
            "available_date": "2026-12-10",
            "start_time": "11:00:00",
            "end_time": "13:00:00",
            "is_available": True,
        },
        headers=headers_a,
    )
    assert overlap_timed.status_code == 409

    # Full-day slot on same date overlaps existing timed slot -> 409 Conflict
    overlap_full = client.post(
        f"{PROVIDER_BASE}/availability",
        json={"available_date": "2026-12-10", "is_available": False, "reason": "Off"},
        headers=headers_a,
    )
    assert overlap_full.status_code == 409

    # Invalid partial times or end <= start -> 422
    assert client.post(
        f"{PROVIDER_BASE}/availability",
        json={"available_date": "2026-12-11", "start_time": "09:00:00"},
        headers=headers_a,
    ).status_code == 422
    assert client.post(
        f"{PROVIDER_BASE}/availability",
        json={"available_date": "2026-12-11", "start_time": "15:00:00", "end_time": "10:00:00"},
        headers=headers_a,
    ).status_code == 422

    # Confirmed booking on 2026-12-15 blocks unavailable creation
    confirmed_booking = TourGuideBooking(
        tour_guide_id=users_and_guides["guide_a"].id,
        traveler_id=users_and_guides["traveler"].id,
        booking_date=date(2026, 12, 15),
        start_datetime=datetime(2026, 12, 15, 9, 0),
        end_datetime=datetime(2026, 12, 15, 17, 0),
        number_of_people=2,
        total_price=Decimal("15000.00"),
        booking_status="confirmed",
    )
    db_session.add(confirmed_booking)
    db_session.commit()

    # Blocking over confirmed booking -> 409
    assert client.post(
        f"{PROVIDER_BASE}/availability",
        json={"available_date": "2026-12-15", "is_available": False, "reason": "Vacation"},
        headers=headers_a,
    ).status_code == 409

    # Bulk block range touching confirmed booking -> 409 and atomic (0 rows created)
    bad_bulk = client.post(
        f"{PROVIDER_BASE}/availability/block-range",
        json={"start_date": "2026-12-14", "end_date": "2026-12-16", "reason": "Holiday"},
        headers=headers_a,
    )
    assert bad_bulk.status_code == 409

    # Clean bulk block range succeeds
    good_bulk = client.post(
        f"{PROVIDER_BASE}/availability/block-range",
        json={"start_date": "2026-12-18", "end_date": "2026-12-20", "reason": "Personal leave"},
        headers=headers_a,
    )
    assert good_bulk.status_code == 201
    assert len(good_bulk.json()) == 3

    # Filtered list works
    filtered = client.get(
        f"{PROVIDER_BASE}/availability",
        params={"date_from": "2026-12-18", "date_to": "2026-12-20", "is_available": "false"},
        headers=headers_a,
    )
    assert filtered.status_code == 200 and len(filtered.json()) == 3

    # Patching slot s2 to overlap s1 fails -> 409
    s2_id = s2.json()["id"]
    assert client.patch(
        f"{PROVIDER_BASE}/availability/{s2_id}",
        json={"start_time": "10:00:00", "end_time": "16:00:00"},
        headers=headers_a,
    ).status_code == 409

    # Delete slot works
    assert client.delete(f"{PROVIDER_BASE}/availability/{s2_id}", headers=headers_a).status_code == 204


# =============================================================================
# 8. CONTACT & VERIFICATION STATUS (PR-17 / PR-18)
# =============================================================================

def test_provider_contact_and_verification_status(client, users_and_guides):
    headers_a = bearer(users_and_guides["provider_a"])

    c_get = client.get(f"{PROVIDER_BASE}/contact", headers=headers_a)
    assert c_get.status_code == 200
    assert c_get.json()["phone"] == "0771234567"

    c_upd = client.patch(
        f"{PROVIDER_BASE}/contact",
        json={"phone": "+94 71 234 5678", "secondary_phone": "0765554444"},
        headers=headers_a,
    )
    assert c_upd.status_code == 200
    assert c_upd.json()["phone"] == "0712345678"
    assert c_upd.json()["secondary_phone"] == "0765554444"

    # Invalid phone rejected
    assert client.patch(
        f"{PROVIDER_BASE}/contact",
        json={"phone": "12345"},
        headers=headers_a,
    ).status_code == 422

    # Verification status endpoint
    v_res = client.get(f"{PROVIDER_BASE}/verification-status", headers=headers_a)
    assert v_res.status_code == 200
    body = v_res.json()
    assert body["guide_approval_status"] == "draft"
    assert body["verification_status"] == "unverified"
    assert body["is_verified_badge"] is False
    assert body["experience_verified"] is False


# =============================================================================
# 9. ADMIN APPROVAL, REJECTION, SUSPENSION, REINSTATEMENT & LISTING SYNC (AD-01 / AD-04)
# =============================================================================

def test_admin_guide_lifecycle_listing_sync_audit_and_notifications(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_admin = bearer(users_and_guides["admin"])
    headers_traveler = bearer(users_and_guides["traveler"])
    guide_a = users_and_guides["guide_a"]

    # Non-admin blocked on admin endpoints
    assert client.get(ADMIN_BASE, headers=headers_a).status_code == 403
    assert client.get(ADMIN_BASE, headers=headers_traveler).status_code == 403
    assert client.get(ADMIN_BASE).status_code == 401

    # Provider submits draft guide -> pending
    sub = client.post(f"{PROVIDER_BASE}/submit", headers=headers_a)
    assert sub.status_code == 200 and sub.json()["approval_status"] == "pending"

    # Admin lists pending guides & inspects detail
    lst = client.get(ADMIN_BASE, params={"approval_status": "pending"}, headers=headers_admin)
    assert lst.status_code == 200 and lst.json()["total"] == 1
    det = client.get(f"{ADMIN_BASE}/{guide_a.id}", headers=headers_admin)
    assert det.status_code == 200 and det.json()["business_name"] == "Kasun Heritage Tours"

    # Reject requires reason >= 5 chars
    assert client.post(
        f"{ADMIN_BASE}/{guide_a.id}/reject",
        json={"reason": "No"},
        headers=headers_admin,
    ).status_code == 422

    # Admin rejects pending guide
    rej = client.post(
        f"{ADMIN_BASE}/{guide_a.id}/reject",
        json={"reason": "Please complete languages and service areas before approval."},
        headers=headers_admin,
    )
    assert rej.status_code == 200
    assert rej.json()["approval_status"] == "rejected"
    assert rej.json()["is_active"] is False

    # Provider sees rejection reason on profile and verification-status
    status_view = client.get(f"{PROVIDER_BASE}/verification-status", headers=headers_a).json()
    assert status_view["guide_approval_status"] == "rejected"
    assert "languages" in status_view["rejection_reason"]

    # Provider resubmits and Admin approves -> syncs ListingReference(is_active=True)
    assert client.post(f"{PROVIDER_BASE}/submit", headers=headers_a).status_code == 200
    app_res = client.post(f"{ADMIN_BASE}/{guide_a.id}/approve", headers=headers_admin)
    assert app_res.status_code == 200
    assert app_res.json()["approval_status"] == "approved"
    assert app_res.json()["is_active"] is True
    assert app_res.json()["listing_reference_synced"] is True

    listing_ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide_a.id,
        )
        .first()
    )
    assert listing_ref is not None
    assert listing_ref.is_active is True
    assert listing_ref.title == "Kasun Heritage Tours"

    # Admin suspends approved guide -> deactivates ListingReference
    sus = client.post(
        f"{ADMIN_BASE}/{guide_a.id}/suspend",
        json={"reason": "Multiple verified traveler complaints regarding conduct."},
        headers=headers_admin,
    )
    assert sus.status_code == 200
    assert sus.json()["approval_status"] == "suspended"
    assert sus.json()["is_active"] is False
    db_session.refresh(listing_ref)
    assert listing_ref.is_active is False

    # Admin reinstates suspended guide -> reactivates ListingReference
    rein = client.post(f"{ADMIN_BASE}/{guide_a.id}/reinstate", headers=headers_admin)
    assert rein.status_code == 200
    assert rein.json()["approval_status"] == "approved"
    assert rein.json()["is_active"] is True
    db_session.refresh(listing_ref)
    assert listing_ref.is_active is True

    # Provider soft-deactivates own guide -> ListingReference becomes inactive
    assert client.delete(PROVIDER_BASE, headers=headers_a).status_code == 204
    db_session.refresh(listing_ref)
    assert listing_ref.is_active is False

    # Verify AdminAuditLogs and Notifications were created
    audit_actions = [
        row.action
        for row in db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.entity_name == "tour_guides", AdminAuditLog.entity_id == guide_a.id)
        .all()
    ]
    assert set(audit_actions) == {
        "tour_guide_rejected",
        "tour_guide_approved",
        "tour_guide_suspended",
        "tour_guide_reinstated",
    }

    notif_types = [
        n.notification_type
        for n in db_session.query(Notification)
        .filter(Notification.user_id == users_and_guides["provider_a"].id)
        .all()
    ]
    assert "guide_profile_approved" in notif_types
    assert "guide_profile_suspended" in notif_types
    assert "guide_profile_reinstated" in notif_types


# =============================================================================
# 10. ADMIN CREDENTIAL VERIFICATION & EVIDENCE-GATED BADGE (AD-02 / AD-03)
# =============================================================================

def test_admin_credential_verification_and_evidence_gated_badge(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_admin = bearer(users_and_guides["admin"])
    guide_a = users_and_guides["guide_a"]

    # Attempting to grant verified badge without approved credential evidence -> 400
    premature_badge = client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={"verification_status": "verified"},
        headers=headers_admin,
    )
    assert premature_badge.status_code == 400

    # Provider uploads SLTDA license credential
    up = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("sltda.pdf", make_pdf_bytes(), "application/pdf")},
        data={
            "credential_type": "sltda_license",
            "title": "SLTDA National License",
            "issuing_authority": "SLTDA",
        },
        headers=headers_a,
    )
    assert up.status_code == 201
    cred_id = up.json()["id"]

    # Admin lists pending credentials (no file_reference exposed)
    c_list = client.get(
        f"{ADMIN_BASE}/credentials",
        params={"verification_status": "pending"},
        headers=headers_admin,
    )
    assert c_list.status_code == 200
    assert c_list.json()["total"] == 1
    assert "file_reference" not in c_list.json()["items"][0]["credential"]

    # Admin requests more info (notes mandatory)
    assert client.post(
        f"{ADMIN_BASE}/credentials/{cred_id}/verify",
        json={"status": "requires_info", "notes": "ok"},
        headers=headers_admin,
    ).status_code == 422

    req_info = client.post(
        f"{ADMIN_BASE}/credentials/{cred_id}/verify",
        json={
            "status": "requires_info",
            "verification_type": "sltda_check",
            "notes": "Please confirm license renewal stamp on back page.",
        },
        headers=headers_admin,
    )
    assert req_info.status_code == 200
    assert req_info.json()["credential"]["verification_status"] == "pending"

    # Once verification history exists, provider cannot delete credential (audit preservation)
    assert client.delete(
        f"{PROVIDER_BASE}/credentials/{cred_id}", headers=headers_a
    ).status_code == 409

    # Still cannot grant verified badge while credential is only pending
    assert client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={"verification_status": "verified"},
        headers=headers_admin,
    ).status_code == 400

    # Admin approves credential
    approve_cred = client.post(
        f"{ADMIN_BASE}/credentials/{cred_id}/verify",
        json={
            "status": "approved",
            "verification_type": "sltda_registry_check",
            "notes": "Verified against SLTDA registry.",
        },
        headers=headers_admin,
    )
    assert approve_cred.status_code == 200
    assert approve_cred.json()["credential"]["verification_status"] == "approved"

    # Now admin grants Verified Guide Badge -> succeeds
    grant = client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={"verification_status": "verified"},
        headers=headers_admin,
    )
    assert grant.status_code == 200
    assert grant.json()["verification_status"] == "verified"
    assert grant.json()["is_verified_badge"] is True

    # Provider verification-status reflects badge & chronological review history
    v_status = client.get(f"{PROVIDER_BASE}/verification-status", headers=headers_a).json()
    assert v_status["is_verified_badge"] is True
    assert v_status["experience_verified"] is True
    assert v_status["credentials_summary"]["approved"] == 1
    assert len(v_status["verification_history"]) == 2

    # Revoking badge requires reason >= 5 chars
    assert client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={"verification_status": "suspended"},
        headers=headers_admin,
    ).status_code == 422

    revoke = client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={
            "verification_status": "suspended",
            "reason": "SLTDA license expired pending renewal.",
        },
        headers=headers_admin,
    )
    assert revoke.status_code == 200
    assert revoke.json()["verification_status"] == "suspended"
    assert revoke.json()["is_verified_badge"] is False

    # Verify verification rows & audit logs persisted
    assert db_session.query(TourGuideVerification).filter(
        TourGuideVerification.tour_guide_id == guide_a.id
    ).count() == 2


# =============================================================================
# 11. NON-TOUR-GUIDE PROVIDER RBAC GUARD ACROSS SUB-RESOURCES
# =============================================================================

@pytest.mark.parametrize(
    ("method", "suffix"),
    (
        ("GET", "/languages"),
        ("GET", "/service-areas"),
        ("GET", "/images"),
        ("GET", "/credentials"),
        ("GET", "/packages"),
        ("GET", "/availability"),
        ("GET", "/contact"),
        ("GET", "/verification-status"),
    ),
)
def test_non_tour_guide_provider_forbidden_on_all_subresources(
    client, users_and_guides, method, suffix
):
    homestay_headers = bearer(users_and_guides["homestay_provider"])
    traveler_headers = bearer(users_and_guides["traveler"])

    assert client.request(method, f"{PROVIDER_BASE}{suffix}", headers=homestay_headers).status_code == 403
    assert client.request(method, f"{PROVIDER_BASE}{suffix}", headers=traveler_headers).status_code == 403
    assert client.request(method, f"{PROVIDER_BASE}{suffix}").status_code == 401


def test_credential_document_replacement_demotes_verified_badge_and_rejects_public_leak(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_admin = bearer(users_and_guides["admin"])
    guide_a = users_and_guides["guide_a"]

    up = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("license.pdf", make_pdf_bytes(), "application/pdf")},
        data={
            "credential_type": "sltda_license",
            "title": "SLTDA License",
            "issuing_authority": "SLTDA",
        },
        headers=headers_a,
    )
    assert up.status_code == 201
    cred_id = up.json()["id"]

    # Approve credential and grant badge
    assert client.post(
        f"{ADMIN_BASE}/credentials/{cred_id}/verify",
        json={"status": "approved", "notes": "Checked"},
        headers=headers_admin,
    ).status_code == 200
    assert client.post(
        f"{ADMIN_BASE}/{guide_a.id}/verification-badge",
        json={"verification_status": "verified"},
        headers=headers_admin,
    ).status_code == 200

    # Replace credential file -> credential & badge must demote to pending
    rep = client.put(
        f"{PROVIDER_BASE}/credentials/{cred_id}/document",
        files={"file": ("renewed.png", make_png_bytes(), "image/png")},
        headers=headers_a,
    )
    assert rep.status_code == 200
    assert rep.json()["verification_status"] == "pending"
    db_session.refresh(guide_a)
    assert guide_a.verification_status == "pending"

    # Public image endpoint rejects non-image extensions & traversal attempts
    assert client.get("/api/v1/uploads/tour-guides/secret.pdf").status_code == 404
    assert client.get("/api/v1/uploads/tour-guides/..%5Csecret.png").status_code in (400, 404)


def test_admin_credential_rejection_and_location_edit_listing_sync(
    client, db_session, users_and_guides
):
    headers_a = bearer(users_and_guides["provider_a"])
    headers_admin = bearer(users_and_guides["admin"])
    guide_a = users_and_guides["guide_a"]

    # Upload credential and reject it via admin
    up = client.post(
        f"{PROVIDER_BASE}/credentials",
        files={"file": ("id.png", make_png_bytes(), "image/png")},
        data={
            "credential_type": "first_aid",
            "title": "First Aid Cert",
            "issuing_authority": "Red Cross",
        },
        headers=headers_a,
    )
    assert up.status_code == 201
    cred_id = up.json()["id"]

    rej_cred = client.post(
        f"{ADMIN_BASE}/credentials/{cred_id}/verify",
        json={
            "status": "rejected",
            "rejection_reason": "Certificate photo is illegible and expired.",
        },
        headers=headers_admin,
    )
    assert rej_cred.status_code == 200
    assert rej_cred.json()["credential"]["verification_status"] == "rejected"
    assert rej_cred.json()["guide_verification_status"] == "rejected"

    # Submit and approve guide, then edit base_city -> reverts to draft & deactivates ListingReference
    assert client.post(f"{PROVIDER_BASE}/submit", headers=headers_a).status_code == 200
    assert client.post(f"{ADMIN_BASE}/{guide_a.id}/approve", headers=headers_admin).status_code == 200

    listing_ref = (
        db_session.query(ListingReference)
        .filter(
            ListingReference.module_type == "tour_guide",
            ListingReference.target_id == guide_a.id,
        )
        .first()
    )
    assert listing_ref is not None and listing_ref.is_active is True

    loc_edit = client.patch(
        PROVIDER_BASE,
        json={"base_city": "Peradeniya"},
        headers=headers_a,
    )
    assert loc_edit.status_code == 200
    assert loc_edit.json()["approval_status"] == "draft"
    assert loc_edit.json()["is_active"] is False

    db_session.refresh(listing_ref)
    assert listing_ref.is_active is False
    assert listing_ref.city == "Peradeniya"

