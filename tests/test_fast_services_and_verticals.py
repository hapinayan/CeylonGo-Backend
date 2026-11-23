"""
CeylonGo Academic MVP — Verticals & Pay-at-Location Tests
Tests Bike/Bicycle, Adventure, Tour Guide verticals, and Pay-at-Location payment method.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_mvp_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app = create_mvp_app()
app.dependency_overrides[get_mvp_db] = override_get_mvp_db
client = TestClient(app)

@pytest.fixture(autouse=True, scope="module")
def setup_database():
    MVPBase.metadata.create_all(bind=test_engine)
    yield
    MVPBase.metadata.drop_all(bind=test_engine)


def test_01_bike_rental_vertical_and_pay_at_location():
    # 1. Register provider
    client.post(
        "/api/auth/register",
        json={"email": "bike.rentals@ceylongo.lk", "full_name": "Saman Kumara", "password": "BikePass123!", "role": "provider"},
    )
    p_token = client.post("/api/auth/login", json={"email": "bike.rentals@ceylongo.lk", "password": "BikePass123!"}).json()["access_token"]
    p_headers = {"Authorization": f"Bearer {p_token}"}

    client.post(
        "/api/providers/register",
        json={"business_name": "Galle Fort Scooters", "phone": "+94912233445", "district": "Galle"},
        headers=p_headers,
    )

    # 2. Register Admin & approve provider
    client.post(
        "/api/auth/register",
        json={"email": "admin2@ceylongo.lk", "full_name": "Admin 2", "password": "AdminPass123!", "role": "admin"},
    )
    a_token = client.post("/api/auth/login", json={"email": "admin2@ceylongo.lk", "password": "AdminPass123!"}).json()["access_token"]
    a_headers = {"Authorization": f"Bearer {a_token}"}
    client.post("/api/admin/providers/1/decision", json={"verification_status": "approved"}, headers=a_headers)

    # 3. Create Bike Listing
    r_lst = client.post(
        "/api/listings",
        json={
            "category": "bike",
            "title": "Honda Dio 110cc Scooter",
            "description": "Fuel-efficient scooter with two helmets included.",
            "district": "Galle",
            "locality": "Galle Fort",
            "price": 2500.00,
            "currency": "LKR",
        },
        headers=p_headers,
    )
    assert r_lst.status_code == 201
    bike_listing_id = r_lst.json()["id"]

    # 4. Add unit and slot
    r_unit = client.post(
        f"/api/listings/{bike_listing_id}/units",
        json={"label": "Scooter #01 (WP BD-4589)", "capacity": 1},
        headers=p_headers,
    )
    unit_id = r_unit.json()["id"]

    start_dt = datetime.now(timezone.utc) + timedelta(days=1)
    end_dt = start_dt + timedelta(days=2)
    client.post(
        f"/api/listings/{bike_listing_id}/slots",
        json={"unit_id": unit_id, "starts_at": start_dt.isoformat(), "ends_at": end_dt.isoformat(), "capacity": 1},
        headers=p_headers,
    )

    # 5. Admin approves listing
    client.post(f"/api/admin/listings/{bike_listing_id}/decision", json={"approval_status": "approved"}, headers=a_headers)

    # 6. Traveler books bike
    client.post(
        "/api/auth/register",
        json={"email": "traveler.amara@ceylongo.lk", "full_name": "Amara Fernando", "password": "Password123!", "role": "traveler"},
    )
    t_token = client.post("/api/auth/login", json={"email": "traveler.amara@ceylongo.lk", "password": "Password123!"}).json()["access_token"]
    t_headers = {"Authorization": f"Bearer {t_token}"}

    r_book = client.post(
        "/api/bookings",
        json={
            "listing_id": bike_listing_id,
            "starts_at": (start_dt + timedelta(hours=1)).isoformat(),
            "ends_at": (end_dt - timedelta(hours=1)).isoformat(),
            "quantity": 1,
            "request_key": str(uuid.uuid4()),
        },
        headers=t_headers,
    )
    assert r_book.status_code == 201
    booking_id = r_book.json()["id"]

    # 7. Pay at Location
    r_pay_loc = client.post(
        "/api/payments/at-location",
        json={"booking_id": booking_id, "request_key": str(uuid.uuid4())},
        headers=t_headers,
    )
    assert r_pay_loc.status_code == 200
    pay_data = r_pay_loc.json()
    assert pay_data["method"] == "at_location"
    assert pay_data["status"] == "unpaid"
    assert pay_data["resolved_at"] is None

    # Booking is confirmed
    b_check = client.get(f"/api/bookings/{booking_id}", headers=t_headers).json()
    assert b_check["status"] == "confirmed"


def test_02_adventure_and_tour_guide_verticals():
    p_token = client.post("/api/auth/login", json={"email": "bike.rentals@ceylongo.lk", "password": "BikePass123!"}).json()["access_token"]
    p_headers = {"Authorization": f"Bearer {p_token}"}
    a_token = client.post("/api/auth/login", json={"email": "admin2@ceylongo.lk", "password": "AdminPass123!"}).json()["access_token"]
    a_headers = {"Authorization": f"Bearer {a_token}"}

    # 1. Create Adventure Listing (Whitewater Rafting Kitulgala)
    r_adv = client.post(
        "/api/listings",
        json={
            "category": "adventure",
            "title": "Kitulgala Whitewater Rafting",
            "description": "5km thrilling rapids session with certified safety instructors.",
            "district": "Kegalle",
            "locality": "Kitulgala",
            "price": 4000.00,
            "currency": "LKR",
        },
        headers=p_headers,
    )
    assert r_adv.status_code == 201
    adv_id = r_adv.json()["id"]
    client.post(f"/api/admin/listings/{adv_id}/decision", json={"approval_status": "approved"}, headers=a_headers)

    # 2. Create Tour Guide Listing (Sigiriya & Dambulla Day Guide)
    r_guide = client.post(
        "/api/listings",
        json={
            "category": "guide",
            "title": "Licensed Cultural Triangle Guide",
            "description": "Full day guided exploration of Sigiriya Lion Rock and Dambulla Cave Temple.",
            "district": "Matale",
            "locality": "Sigiriya",
            "price": 6000.00,
            "currency": "LKR",
        },
        headers=p_headers,
    )
    assert r_guide.status_code == 201
    guide_id = r_guide.json()["id"]
    client.post(f"/api/admin/listings/{guide_id}/decision", json={"approval_status": "approved"}, headers=a_headers)

    # Verify search filters correctly by category
    r_srch_adv = client.get("/api/listings?category=adventure").json()
    assert any(x["id"] == adv_id for x in r_srch_adv)

    r_srch_guide = client.get("/api/listings?category=guide").json()
    assert any(x["id"] == guide_id for x in r_srch_guide)
