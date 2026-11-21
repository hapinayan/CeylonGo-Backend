
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_homestay_invalid_page():
    response = client.get(
        "/api/v1/homestays",
        params={"page": 0},
    )

    assert response.status_code == 422


def test_homestay_invalid_page_size():
    response = client.get(
        "/api/v1/homestays",
        params={"page_size": 51},
    )

    assert response.status_code == 422


def test_homestay_invalid_guests():
    response = client.get(
        "/api/v1/homestays",
        params={"guests": 0},
    )

    assert response.status_code == 422


def test_homestay_negative_price():
    response = client.get(
        "/api/v1/homestays",
        params={"min_price": -100},
    )

    assert response.status_code == 422

def test_homestay_not_found(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays/99999"
    )

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Homestay not found"
    }

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


from app.database.connection import get_db

from app.models.user import Base, User


from app.models.homestay import (
    Homestay,
    HomestayImage,
    HomestayAmenity,
    HomestayAvailabilityBlock,
)


from app.models.booking import Booking
from app.models.shared import ListingReference

from datetime import datetime, timezone

@pytest.fixture
def homestay_test_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    import app.models as app_models  # Ensure all models are registered on Base without shadowing app
    Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(bind=engine)

    with TestingSession() as db:
        db.add(

Homestay(
    name="Jaffna Heritage Homestay",
    description="Test homestay",
    location="Jaffna",
    price_per_night=Decimal("5000.00"),
    max_guests=4,
    is_active=True,
    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
)

        )
        db.commit()

    def override_get_db():
        with TestingSession() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()

def test_homestay_location_search(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"location": "Jaffna"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"
    assert data["items"][0]["location"] == "Jaffna"

def test_homestay_name_search(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"name": "Heritage"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"

def test_homestay_price_filter(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={
            "min_price": 3000,
            "max_price": 6000,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"
    assert float(data["items"][0]["price_per_night"]) == 5000.0

def test_homestay_guest_filter(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"guests": 4},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"
    assert data["items"][0]["max_guests"] == 4

def test_homestay_guest_capacity_exceeded(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"guests": 6},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 0
    assert data["items"] == []

def test_homestay_pagination(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={
            "page": 1,
            "page_size": 1,
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"

def test_homestay_multiple_pages(homestay_test_client):
    from app.database.connection import get_db

    # Get the isolated test database session
    db = next(app.dependency_overrides[get_db]())

    try:
        db.add_all([
            Homestay(
                name="Kandy Hill Homestay",
                description="Test homestay",
                location="Kandy",
                price_per_night=Decimal("7000.00"),
                max_guests=3,
                is_active=True,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
            Homestay(
                name="Galle Beach Homestay",
                description="Test homestay",
                location="Galle",
                price_per_night=Decimal("9000.00"),
                max_guests=5,
                is_active=True,
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
            ),
        ])
        db.commit()
    finally:
        db.close()

    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"page": 2, "page_size": 1},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 3
    assert data["page"] == 2
    assert data["page_size"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Kandy Hill Homestay"

def test_homestay_details(homestay_test_client):
    list_response = homestay_test_client.get(
        "/api/v1/homestays"
    )

    assert list_response.status_code == 200

    homestay_id = list_response.json()["items"][0]["id"]

    response = homestay_test_client.get(
        f"/api/v1/homestays/{homestay_id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["id"] == homestay_id
    assert data["name"] == "Jaffna Heritage Homestay"
    assert data["location"] == "Jaffna"
    assert float(data["price_per_night"]) == 5000.0
    assert data["max_guests"] == 4
    assert data["is_active"] is True
    assert data["images"] == []

def test_homestay_images(homestay_test_client):
    # Access the isolated test database
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.add(
            HomestayImage(
                homestay_id=homestay.id,
                image_url="https://example.com/homestay.jpg",
                is_primary=True,
            )
        )
        db.commit()

        homestay_id = homestay.id
    finally:
        db.close()

    response = homestay_test_client.get(
        f"/api/v1/homestays/{homestay_id}"
    )

    assert response.status_code == 200

    data = response.json()

    assert len(data["images"]) == 1
    assert data["images"][0]["image_url"] == (
        "https://example.com/homestay.jpg"
    )
    assert data["images"][0]["is_primary"] is True

def test_availability_invalid_dates(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays/1/availability",
        params={
            "check_in": "2026-12-25",
            "check_out": "2026-12-23",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Check-out date must be after check-in date"
    )

def test_availability_already_booked(homestay_test_client):
    from datetime import date

    from sqlalchemy import text

    # Add a confirmed booking to the test database
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.execute(
            text("""
                INSERT INTO users
                    (full_name, email, role, is_active, created_at)
                VALUES
                    (:name, :email, :role, 1, :created_at)
            """),
            {
                "name": "Test Traveler",
                "email": "availability.test@example.com",
                "role": "traveler",
                "created_at": datetime(2026, 11, 15),
            },
        )

        user_id = db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": "availability.test@example.com"},
        ).scalar_one()

        db.add(
            Booking(
                homestay_id=homestay.id,
                user_id=user_id,
                check_in=date(2026, 12, 10),
                check_out=date(2026, 12, 12),
                guests=2,
                total_price=Decimal("10000.00"),
                status="confirmed",
                created_at=datetime(2026, 11, 15),
            )
        )

        db.commit()
        homestay_id = homestay.id
    finally:
        db.close()

    response = homestay_test_client.get(
        f"/api/v1/homestays/{homestay_id}/availability",
        params={
            "check_in": "2026-12-10",
            "check_out": "2026-12-12",
        },
    )

    assert response.status_code == 200
    assert response.json()["available"] is False

def test_availability_free_dates(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays/1/availability",
        params={
            "check_in": "2026-12-20",
            "check_out": "2026-12-22",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["homestay_id"] == 1
    assert data["available"] is True

def test_availability_checkout_boundary(homestay_test_client):
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        traveler = User(
            full_name="Boundary Test Traveler",
            email="boundary.test@example.com",
            role="traveler",
            is_active=True,
            created_at=datetime(2026, 11, 15),
        )

        db.add(traveler)
        db.flush()

        booking = Booking(
            homestay_id=homestay.id,
            user_id=traveler.id,
            check_in=datetime(2026, 12, 10).date(),
            check_out=datetime(2026, 12, 12).date(),
            guests=2,
            total_price=Decimal("10000.00"),
            status="confirmed",
            created_at=datetime(2026, 11, 15),
        )

        db.add(booking)
        db.commit()

        homestay_id = homestay.id
    finally:
        db.close()

    response = homestay_test_client.get(
        f"/api/v1/homestays/{homestay_id}/availability",
        params={
            "check_in": "2026-12-12",
            "check_out": "2026-12-14",
        },
    )

    assert response.status_code == 200
    assert response.json()["available"] is True

def test_homestay_filter_by_amenity(homestay_test_client):
    from app.models.homestay import HomestayAmenity

    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.add(
            HomestayAmenity(
                homestay_id=homestay.id,
                name="Wi-Fi",
            )
        )
        db.commit()
    finally:
        db.close()

    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"amenity": "Wi-Fi"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Jaffna Heritage Homestay"
    assert data["items"][0]["amenities"][0]["name"] == "Wi-Fi"

def test_homestay_filter_by_missing_amenity(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"amenity": "Parking"},
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 0
    assert data["items"] == []

def test_homestay_amenity_case_insensitive(homestay_test_client):
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.add(
            HomestayAmenity(
                homestay_id=homestay.id,
                name="Wi-Fi",
            )
        )
        db.commit()
    finally:
        db.close()

    for amenity in ["wi-fi", "WI-FI", "Wi-Fi"]:
        response = homestay_test_client.get(
            "/api/v1/homestays",
            params={"amenity": amenity},
        )

        assert response.status_code == 200

        data = response.json()

        assert data["total"] == 1
        assert data["items"][0]["name"] == "Jaffna Heritage Homestay"

def test_homestay_filter_multiple_amenities(homestay_test_client):
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.add_all([
            HomestayAmenity(
                homestay_id=homestay.id,
                name="Wi-Fi",
            ),
            HomestayAmenity(
                homestay_id=homestay.id,
                name="Parking",
            ),
        ])

        db.commit()
    finally:
        db.close()

    response = homestay_test_client.get(
        "/api/v1/homestays",
        params=[
            ("amenity", "Wi-Fi"),
            ("amenity", "Parking"),
        ],
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1

    amenity_names = {
        item["name"]
        for item in data["items"][0]["amenities"]
    }

    assert amenity_names == {"Wi-Fi", "Parking"}

def test_homestay_missing_one_requested_amenity(homestay_test_client):
    db = next(app.dependency_overrides[get_db]())

    try:
        homestay = db.query(Homestay).first()

        db.add(
            HomestayAmenity(
                homestay_id=homestay.id,
                name="Wi-Fi",
            )
        )
        db.commit()
    finally:
        db.close()

    response = homestay_test_client.get(
        "/api/v1/homestays",
        params=[
            ("amenity", "Wi-Fi"),
            ("amenity", "Parking"),
        ],
    )

    assert response.status_code == 200

    data = response.json()

    assert data["total"] == 0
    assert data["items"] == []

def test_homestay_invalid_empty_amenity(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"amenity": "   "},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid amenity"

def test_homestay_amenity_too_long(homestay_test_client):
    response = homestay_test_client.get(
        "/api/v1/homestays",
        params={"amenity": "A" * 101},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid amenity"
