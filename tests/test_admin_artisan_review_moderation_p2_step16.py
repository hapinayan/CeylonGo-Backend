"""
Automated tests for Phase P2 Step 16: Admin Review Moderation.
Verifies:
1. Review persistence path: reviews use shared dbo.reviews and dbo.listing_references (module_type='artisan').
2. Admin can list reviews across artisan businesses (/api/v1/admin/artisan/reviews).
3. Admin can hide an abusive/inappropriate review (is_published=False).
4. Hidden reviews are strictly excluded from public listing (GET /api/v1/artisan-reviews/listing/{id}).
5. Hidden reviews are strictly excluded from public rating summaries and averages (GET /api/v1/artisan-reviews/listing/{id}/summary).
6. Admin can re-publish a review (is_published=True), which restores it to public visibility and rating calculation.
7. Role-based access control: Non-admins cannot access moderation endpoints (HTTP 403).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, ListingReference, Review
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_shared_reference import (
    ARTISAN_MODULE_TYPE,
    ARTISAN_RESERVATION_MODULE_TYPE,
    sync_artisan_booking_reference,
    sync_artisan_listing_reference,
)

# In-memory SQLite engine for tests
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


@pytest.fixture
def admin_headers(db_session):
    admin = User(
        full_name="Admin Moderator",
        email="admin_mod@ceylongo.com",
        password_hash="mock_hash",
        role="admin",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    token = create_access_token(data={"sub": str(admin.id), "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def review_setup(db_session):
    # Provider & Profile
    provider_user = User(
        full_name="Provider Mod",
        email="prov_mod@ceylongo.com",
        password_hash="mock_hash",
        role="service_provider",
        is_active=True,
    )
    db_session.add(provider_user)
    db_session.commit()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Southern Craft Studio",
        provider_type="artisan",
        phone="+94771234567",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    # Listing
    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Southern Craft Studio Galle",
        craft_category="pottery",
        district="Galle",
        city="Galle",
        approval_status="approved",
        is_active=True,
        is_verified=True,
    )
    db_session.add(listing)
    db_session.commit()
    sync_artisan_listing_reference(db_session, listing)

    # Product
    product = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Terracotta Vase",
        item_type="pottery",
        price=Decimal("3500.00"),
        stock_quantity=5,
        is_available=True,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(product)
    db_session.commit()

    # Traveler 1
    t1 = User(
        full_name="Traveler One",
        email="traveler1@ceylongo.com",
        password_hash="mock_hash",
        role="traveler",
        is_active=True,
    )
    # Traveler 2
    t2 = User(
        full_name="Traveler Two",
        email="traveler2@ceylongo.com",
        password_hash="mock_hash",
        role="traveler",
        is_active=True,
    )
    db_session.add_all([t1, t2])
    db_session.commit()

    # Reservation 1 (completed)
    res1 = ArtisanReservation(
        showcase_item_id=product.id,
        artisan_listing_id=listing.id,
        traveler_id=t1.id,
        quantity=1,
        total_price=Decimal("3500.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    # Reservation 2 (completed)
    res2 = ArtisanReservation(
        showcase_item_id=product.id,
        artisan_listing_id=listing.id,
        traveler_id=t2.id,
        quantity=1,
        total_price=Decimal("3500.00"),
        currency="LKR",
        reservation_status="completed",
        pickup_date=date.today(),
    )
    db_session.add_all([res1, res2])
    db_session.commit()

    sync_artisan_booking_reference(db_session, res1)
    sync_artisan_booking_reference(db_session, res2)

    l_ref = db_session.query(ListingReference).filter(ListingReference.target_id == listing.id).first()
    b_ref1 = db_session.query(BookingReference).filter(BookingReference.target_booking_id == res1.id).first()
    b_ref2 = db_session.query(BookingReference).filter(BookingReference.target_booking_id == res2.id).first()

    # Review 1: 5-star (good)
    rev1 = Review(
        listing_reference_id=l_ref.id,
        booking_reference_id=b_ref1.id,
        user_id=t1.id,
        rating=Decimal("5.00"),
        title="Excellent craftsmanship",
        comment="Loved the pottery workshop!",
        is_verified_booking=True,
        is_published=True,
    )
    # Review 2: 1-star (abusive/spam, candidate for moderation)
    rev2 = Review(
        listing_reference_id=l_ref.id,
        booking_reference_id=b_ref2.id,
        user_id=t2.id,
        rating=Decimal("1.00"),
        title="Spam comment with abuse",
        comment="Inappropriate content violation",
        is_verified_booking=True,
        is_published=True,
    )
    db_session.add_all([rev1, rev2])
    db_session.commit()

    return {
        "listing": listing,
        "rev1": rev1,
        "rev2": rev2,
    }


def test_admin_list_reviews_for_moderation(client, admin_headers, review_setup):
    """Verify admin can list all artisan reviews with moderation status."""
    response = client.get("/api/v1/admin/artisan/reviews", headers=admin_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    titles = [item["title"] for item in data["items"]]
    assert "Excellent craftsmanship" in titles
    assert "Spam comment with abuse" in titles


def test_unified_queue_is_private_scoped_paginated_and_role_gated(client, db_session, review_setup):
    first, second = review_setup["rev1"], review_setup["rev2"]
    second.created_at = first.created_at
    second.is_published = False
    first.comment = "private@example.com phone 0771234567"
    listing = review_setup["listing"]
    artisan_ref = db_session.get(ListingReference, first.listing_reference_id)
    foreign = ListingReference(module_type="homestay", target_id=listing.id,
                               provider_profile_id=artisan_ref.provider_profile_id, title="Foreign", district="Kandy")
    db_session.add(foreign)
    db_session.flush()
    db_session.add(Review(listing_reference_id=foreign.id, user_id=first.user_id, rating=1))
    db_session.commit()

    path = "/api/v1/admin/reviews"
    for role in ("super_admin", "content_moderator", "admin", "operations_admin", "finance_admin", "support_admin", "traveler"):
        user = User(full_name=role, email=f"queue-{role}@example.com", role=role)
        db_session.add(user)
        db_session.commit()
        token = create_access_token({"sub": str(user.id)})
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get(path, headers=headers, params={"module": "artisan", "page_size": 1})
        if role not in ("super_admin", "content_moderator", "admin"):
            assert response.status_code == 403
            continue
        assert response.status_code == 200
        page = response.json()
        assert page["module"] == "artisan" and page["total"] == 2 and page["total_pages"] == 2
        assert page["items"][0]["id"] == second.id
        assert set(page["items"][0]) == {"id", "listing_id", "rating", "is_published",
                                        "is_verified_booking", "created_at", "updated_at"}
        filtered = client.get(path, headers=headers, params={"module": "artisan", "is_published": True,
                                                             "listing_id": listing.id}).json()
        assert [item["id"] for item in filtered["items"]] == [first.id]
        assert "private@example.com" not in str(filtered)
        assert client.get(path, headers=headers, params={"module": "homestay"}).status_code == 422
        assert client.get(path, headers=headers, params={"module": "artisan", "page_size": 101}).status_code == 422
    assert client.get(path, params={"module": "artisan"}).status_code == 401


def test_admin_hide_review_and_verify_public_isolation(client, admin_headers, review_setup):
    """
    Verify admin can hide a review (is_published=False), and that hidden review is:
    1. Excluded from public review listing.
    2. Excluded from public rating summaries and averages.
    """
    listing = review_setup["listing"]
    rev2 = review_setup["rev2"]

    # Before moderation: public list has 2 reviews, average is (5 + 1)/2 = 3.0
    pub_list_before = client.get(f"/api/v1/artisan-reviews/listing/{listing.id}")
    assert pub_list_before.status_code == 200
    assert pub_list_before.json()["total"] == 2

    pub_summary_before = client.get(f"/api/v1/artisan-reviews/listing/{listing.id}/summary")
    assert pub_summary_before.status_code == 200
    assert pub_summary_before.json()["average_rating"] == 3.0

    # Admin hides rev2 (is_published=False)
    mod_resp = client.patch(
        f"/api/v1/admin/artisan/reviews/{rev2.id}/publish",
        json={"is_published": False, "moderation_reason": "Contains abusive language"},
        headers=admin_headers,
    )
    assert mod_resp.status_code == 200
    assert mod_resp.json()["is_published"] is False
    assert "hidden" in mod_resp.json()["message"]

    # After moderation: public list has only 1 review
    pub_list_after = client.get(f"/api/v1/artisan-reviews/listing/{listing.id}")
    assert pub_list_after.status_code == 200
    assert pub_list_after.json()["total"] == 1
    assert pub_list_after.json()["items"][0]["id"] == review_setup["rev1"].id

    # Public summary average rating now reflects ONLY the published review: 5.0
    pub_summary_after = client.get(f"/api/v1/artisan-reviews/listing/{listing.id}/summary")
    assert pub_summary_after.status_code == 200
    assert pub_summary_after.json()["average_rating"] == 5.0
    assert pub_summary_after.json()["total_reviews"] == 1


def test_admin_republish_review(client, admin_headers, review_setup):
    """Verify admin can restore a hidden review to published state."""
    rev2 = review_setup["rev2"]
    listing = review_setup["listing"]

    # Hide it
    client.patch(
        f"/api/v1/admin/artisan/reviews/{rev2.id}/publish",
        json={"is_published": False},
        headers=admin_headers,
    )

    # Re-publish it
    repub_resp = client.patch(
        f"/api/v1/admin/artisan/reviews/{rev2.id}/publish",
        json={"is_published": True, "moderation_reason": "Appeal accepted"},
        headers=admin_headers,
    )
    assert repub_resp.status_code == 200
    assert repub_resp.json()["is_published"] is True

    # Public list now has 2 reviews again
    pub_list = client.get(f"/api/v1/artisan-reviews/listing/{listing.id}")
    assert pub_list.json()["total"] == 2
