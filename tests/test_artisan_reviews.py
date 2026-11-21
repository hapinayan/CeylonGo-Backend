"""
Automated Integration Tests for Artisan Reviews & Ratings API (Phase P1 Step 10).

Covers all required security, validation, and contract behaviors:
1. Completed reservation review submission (201 Created, verified booking).
2. Pending reservation review rejection (400 Bad Request).
3. Confirmed & ready_for_pickup reservation review rejection (400 Bad Request).
4. Cancelled & expired reservation review rejection (400 Bad Request).
5. Duplicate review prevention (409 Conflict).
6. Invalid rating range rejection (< 1.0 or > 5.0 -> 422 Unprocessable Entity).
7. Traveler ownership enforcement (403 Forbidden for reviewing others' bookings).
8. Provider self-review prevention (403 Forbidden).
9. Public review discovery for approved listings (200 OK, paginated, sorted).
10. Unapproved / inactive listing visibility check (404 Not Found, never leaks reviews).
11. Average star rating calculation and 5-star distribution breakdown.
12. Listing detail endpoint embedding average_rating and total_reviews.
13. Traveler's own reviews endpoint (/api/v1/artisan-reviews/me).
14. Review update endpoint by author (200 OK) and non-author rejection (403 Forbidden).
15. Review deletion endpoint by author (200 OK) and non-author rejection (403 Forbidden).
16. Direct review creation endpoint (/api/v1/artisan-reviews).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.main import app
from app.models.artisan import ArtisanReservation
from app.models.artisan_listing import ArtisanListing
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    ListingReference,
    Review,
)
from app.models.showcase_item import ShowcaseItem
from app.models.user import Base, User
from app.services.artisan_shared_reference import (
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


# ==============================================================================
# User & Helper Fixtures
# ==============================================================================

def auth_header(user: User) -> dict[str, str]:
    token = create_access_token(data={"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Kasun",
        email="kasun.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def second_traveler_user(db_session) -> User:
    user = User(
        full_name="Traveler Sarah",
        email="sarah.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def approved_artisan_user(db_session) -> tuple[User, ProviderProfile, ArtisanListing]:
    user = User(
        full_name="Artisan Nimal",
        email="nimal.artisan@example.com",
        password_hash=hash_password("Password123!"),
        role="provider",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    profile = ProviderProfile(
        user_id=user.id,
        business_name="Nimal Woodcrafts",
        provider_type="artisan",
        phone="+94771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    listing = ArtisanListing(
        provider_profile_id=profile.id,
        title="Nimal Kandy Mask Studio",
        craft_category="Woodcarving & Masks",
        district="Kandy",
        city="Pilimathalawa",
        approval_status="approved",
        is_verified=True,
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    db_session.refresh(listing)

    # Pre-sync shared reference
    sync_artisan_listing_reference(db_session, listing)

    return user, profile, listing


@pytest.fixture
def product_item(db_session, approved_artisan_user) -> ShowcaseItem:
    _, _, listing = approved_artisan_user
    item = ShowcaseItem(
        artisan_listing_id=listing.id,
        title="Handcrafted Raksha Mask",
        price=Decimal("4500.00"),
        stock_quantity=10,
        is_available=True,
        is_active=True,
        approval_status="approved",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def create_test_reservation(
    db_session,
    traveler: User,
    listing: ArtisanListing,
    product: ShowcaseItem,
    status: str = "completed",
) -> ArtisanReservation:
    """Helper to generate a reservation in any status with synced booking reference."""
    res = ArtisanReservation(
        artisan_listing_id=listing.id,
        traveler_id=traveler.id,
        showcase_item_id=product.id,
        quantity=1,
        total_price=product.price,
        reservation_status=status,
        pickup_date=date.today() + timedelta(days=2),
    )
    db_session.add(res)
    db_session.commit()
    db_session.refresh(res)

    sync_artisan_booking_reference(db_session, res)
    return res


# ==============================================================================
# 1. Successful Review Submission (Completed Reservation)
# ==============================================================================

def test_create_review_for_completed_reservation_success(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": 4.80,
        "title": "Masterful craftsmanship!",
        "comment": "The Raksha mask was stunning in person and pickup was seamless.",
    }

    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )

    assert res.status_code == 201
    data = res.json()
    assert data["id"] is not None
    assert float(data["rating"]) == 4.80
    assert data["title"] == "Masterful craftsmanship!"
    assert data["comment"] == "The Raksha mask was stunning in person and pickup was seamless."
    assert data["is_verified_booking"] is True
    assert data["is_published"] is True
    assert data["user_name"] == "Traveler Kasun"
    assert data["artisan_listing_id"] == listing.id
    assert data["artisan_listing_title"] == "Nimal Kandy Mask Studio"
    assert data["booking_reference_code"] is not None

    # Verify persisted in database
    db_review = db_session.query(Review).filter(Review.id == data["id"]).first()
    assert db_review is not None
    assert db_review.is_verified_booking is True
    assert db_review.booking_reference_id is not None


# ==============================================================================
# 2. Reject Reviews for Non-Completed Reservations (Pending, Confirmed, Ready)
# ==============================================================================

@pytest.mark.parametrize("status_name", ["pending", "confirmed", "ready_for_pickup"])
def test_create_review_for_active_uncompleted_reservation_rejected(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
    status_name: str,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status=status_name)

    payload = {
        "reservation_id": reservation.id,
        "rating": 5.0,
        "title": "Too early review",
        "comment": "Have not picked up yet.",
    }

    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )

    assert res.status_code == 400
    assert f"Only completed reservations with confirmed in-store pickup can be reviewed" in res.json()["detail"]


# ==============================================================================
# 3. Reject Reviews for Cancelled and Expired Reservations
# ==============================================================================

@pytest.mark.parametrize("status_name", ["cancelled", "expired"])
def test_create_review_for_cancelled_or_expired_reservation_rejected(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
    status_name: str,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status=status_name)

    payload = {
        "reservation_id": reservation.id,
        "rating": 1.0,
        "title": "Cancelled pickup",
        "comment": "Never picked up.",
    }

    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )

    assert res.status_code == 400
    assert "Only completed reservations" in res.json()["detail"]


# ==============================================================================
# 4. Duplicate Review Prevention (409 Conflict)
# ==============================================================================

def test_duplicate_review_for_same_reservation_rejected(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": 5.0,
        "title": "Initial review",
        "comment": "First submission.",
    }

    # First submission -> 201
    res1 = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res1.status_code == 201

    # Second submission -> 409 Conflict
    res2 = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res2.status_code == 409
    assert "A review has already been submitted for this reservation" in res2.json()["detail"]


# ==============================================================================
# 5. Invalid Rating Validation (< 1.00 or > 5.00)
# ==============================================================================

@pytest.mark.parametrize("invalid_rating", [0.5, 0.0, -1.0, 5.5, 6.0])
def test_create_review_invalid_rating_rejected(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
    invalid_rating: float,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": invalid_rating,
        "title": "Invalid star count",
    }

    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )

    assert res.status_code == 422


# ==============================================================================
# 6. Traveler Ownership Enforcement (Cannot Review Others' Reservations)
# ==============================================================================

def test_traveler_cannot_review_another_users_reservation(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    # Reservation belongs to traveler_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": 5.0,
        "title": "Impersonated review",
        "comment": "Attempting to review someone else's order.",
    }

    # second_traveler_user attempts to review traveler_user's booking
    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(second_traveler_user),
    )

    assert res.status_code == 403
    assert "You can only submit reviews for reservations you personally booked" in res.json()["detail"]


# ==============================================================================
# 7. Provider Self-Review Prevention (Cannot Review Own Business)
# ==============================================================================

def test_provider_cannot_review_own_artisan_business(
    client: TestClient,
    db_session,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    provider_user, _, listing = approved_artisan_user
    # Even if reservation traveler was somehow provider_user
    reservation = create_test_reservation(db_session, provider_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": 5.0,
        "title": "Self endorsement",
        "comment": "I am the best craftsman in town.",
    }

    res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(provider_user),
    )

    assert res.status_code == 403
    assert "Providers cannot review their own businesses" in res.json()["detail"]


# ==============================================================================
# 8. Unapproved Listing Visibility & Review Guard (404 Not Found)
# ==============================================================================

@pytest.mark.parametrize("approval_status", ["pending", "rejected", "suspended"])
def test_unapproved_listing_review_endpoints_return_404(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
    approval_status: str,
):
    _, _, listing = approved_artisan_user
    listing.approval_status = approval_status
    db_session.commit()

    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    payload = {
        "reservation_id": reservation.id,
        "rating": 5.0,
        "title": "Hidden listing review",
    }

    # POST returns 404
    post_res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert post_res.status_code == 404

    # Public GET reviews returns 404
    get_res = client.get(f"/api/v1/artisan-listings/{listing.id}/reviews")
    assert get_res.status_code == 404

    # Public GET summary returns 404
    summary_res = client.get(f"/api/v1/artisan-listings/{listing.id}/reviews/summary")
    assert summary_res.status_code == 404


# ==============================================================================
# 9. Public Review Discovery & Pagination
# ==============================================================================

def test_public_get_reviews_pagination_and_sorting(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user

    # Create 2 completed reservations & reviews
    res1 = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")
    res2 = create_test_reservation(db_session, second_traveler_user, listing, product_item, status="completed")

    client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res1.id, "rating": 3.0, "title": "Good mask", "comment": "Okay experience."},
        headers=auth_header(traveler_user),
    )
    client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res2.id, "rating": 5.0, "title": "Incredible work", "comment": "Outstanding!"},
        headers=auth_header(second_traveler_user),
    )

    # Public unauthenticated request
    res = client.get(f"/api/v1/artisan-listings/{listing.id}/reviews?page=1&page_size=10&sort_by=highest_rating")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert data["total_reviews"] == 2
    assert float(data["average_rating"]) == 4.0
    assert len(data["items"]) == 2
    assert float(data["items"][0]["rating"]) == 5.0
    assert float(data["items"][1]["rating"]) == 3.0


# ==============================================================================
# 10. Star Rating Summary & Distribution Breakdown Calculation
# ==============================================================================

def test_reviews_summary_and_rating_breakdown(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    listing_ref = sync_artisan_listing_reference(db_session, listing)

    # Insert a collection of reviews directly
    ratings = [5.0, 4.8, 4.0, 3.2, 1.0]
    for r in ratings:
        rev = Review(
            listing_reference_id=listing_ref.id,
            user_id=traveler_user.id,
            rating=Decimal(str(r)),
            title=f"Review score {r}",
            comment="Detailed commentary.",
            is_verified_booking=True,
            is_published=True,
        )
        db_session.add(rev)
    db_session.commit()

    # Call summary endpoint
    res = client.get(f"/api/v1/artisan-listings/{listing.id}/reviews/summary")
    assert res.status_code == 200
    data = res.json()

    assert data["listing_id"] == listing.id
    assert data["listing_title"] == "Nimal Kandy Mask Studio"
    assert data["total_reviews"] == 5
    assert data["verified_reviews_count"] == 5
    # (5.0 + 4.8 + 4.0 + 3.2 + 1.0) / 5 = 18.0 / 5 = 3.60
    assert float(data["average_rating"]) == 3.60

    # 5.0 -> tier 5; 4.8 -> tier 5; 4.0 -> tier 4; 3.2 -> tier 3; 1.0 -> tier 1
    assert data["rating_breakdown"]["5"] == 2
    assert data["rating_breakdown"]["4"] == 1
    assert data["rating_breakdown"]["3"] == 1
    assert data["rating_breakdown"]["2"] == 0
    assert data["rating_breakdown"]["1"] == 1


# ==============================================================================
# 11. Listing Detail Endpoint Embeds Rating and Review Count
# ==============================================================================

def test_listing_detail_endpoint_embeds_rating_and_review_count(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    res1 = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res1.id, "rating": 4.5, "title": "Top notch"},
        headers=auth_header(traveler_user),
    )

    res = client.get(f"/api/v1/artisan-listings/{listing.id}")
    assert res.status_code == 200
    data = res.json()
    assert float(data["average_rating"]) == 4.5
    assert data["total_reviews"] == 1


# ==============================================================================
# 12. Traveler's Own Reviews Endpoint (/api/v1/artisan-reviews/me)
# ==============================================================================

def test_get_my_artisan_reviews(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    res1 = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")
    res2 = create_test_reservation(db_session, second_traveler_user, listing, product_item, status="completed")

    client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res1.id, "rating": 4.0, "title": "Kasun review"},
        headers=auth_header(traveler_user),
    )
    client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res2.id, "rating": 5.0, "title": "Sarah review"},
        headers=auth_header(second_traveler_user),
    )

    # Kasun calls /me
    res = client.get("/api/v1/artisan-reviews/me", headers=auth_header(traveler_user))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 1
    assert items[0]["title"] == "Kasun review"
    assert items[0]["user_name"] == "Traveler Kasun"


# ==============================================================================
# 13. Update Review (PUT /api/v1/artisan-reviews/{id})
# ==============================================================================

def test_update_artisan_review_author_success_and_forbidden_for_other(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    res1 = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    post_res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res1.id, "rating": 3.0, "title": "Initial thought", "comment": "Good"},
        headers=auth_header(traveler_user),
    )
    review_id = post_res.json()["id"]

    # Non-author tries to update -> 403 Forbidden
    put_non_author = client.put(
        f"/api/v1/artisan-reviews/{review_id}",
        json={"rating": 1.0, "title": "Tampered"},
        headers=auth_header(second_traveler_user),
    )
    assert put_non_author.status_code == 403

    # Author updates rating and title -> 200 OK
    put_res = client.put(
        f"/api/v1/artisan-reviews/{review_id}",
        json={"rating": 5.0, "title": "Revised: Exceptional!", "comment": "After using it for a week, 5 stars!"},
        headers=auth_header(traveler_user),
    )
    assert put_res.status_code == 200
    data = put_res.json()
    assert float(data["rating"]) == 5.0
    assert data["title"] == "Revised: Exceptional!"
    assert data["comment"] == "After using it for a week, 5 stars!"


# ==============================================================================
# 14. Delete Review (DELETE /api/v1/artisan-reviews/{id})
# ==============================================================================

def test_delete_artisan_review_author_success_and_forbidden_for_other(
    client: TestClient,
    db_session,
    traveler_user: User,
    second_traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    res1 = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    post_res = client.post(
        f"/api/v1/artisan-listings/{listing.id}/reviews",
        json={"reservation_id": res1.id, "rating": 4.0, "title": "Will be deleted"},
        headers=auth_header(traveler_user),
    )
    review_id = post_res.json()["id"]

    # Non-author tries to delete -> 403 Forbidden
    del_non_author = client.delete(
        f"/api/v1/artisan-reviews/{review_id}",
        headers=auth_header(second_traveler_user),
    )
    assert del_non_author.status_code == 403

    # Author deletes -> 200 OK
    del_res = client.delete(
        f"/api/v1/artisan-reviews/{review_id}",
        headers=auth_header(traveler_user),
    )
    assert del_res.status_code == 200

    # Ensure review is removed from DB
    assert db_session.query(Review).filter(Review.id == review_id).first() is None


# ==============================================================================
# 15. Direct Review Endpoint (/api/v1/artisan-reviews)
# ==============================================================================

def test_direct_artisan_review_submission(
    client: TestClient,
    db_session,
    traveler_user: User,
    approved_artisan_user,
    product_item: ShowcaseItem,
):
    _, _, listing = approved_artisan_user
    reservation = create_test_reservation(db_session, traveler_user, listing, product_item, status="completed")

    # Omitting listing_id (resolved from reservation)
    payload = {
        "reservation_id": reservation.id,
        "rating": 4.9,
        "title": "Direct submission",
        "comment": "Convenience endpoint works cleanly.",
    }

    res = client.post(
        "/api/v1/artisan-reviews",
        json=payload,
        headers=auth_header(traveler_user),
    )
    assert res.status_code == 201
    data = res.json()
    assert float(data["rating"]) == 4.90
    assert data["artisan_listing_id"] == listing.id
    assert data["artisan_listing_title"] == listing.title
