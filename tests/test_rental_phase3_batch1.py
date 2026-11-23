"""
CeylonGo Rental Backend - Phase 3 Batch 1 Test Suite
====================================================
Automated isolated unit & API tests verifying Service Provider requirements SP-02 through SP-06:
- SP-02: Add Rental Vehicle (with technical specifications, initial status='available', approval_status='pending')
- SP-03: Manage Rental Vehicles (fleet listing with pagination & filters, detail view, spec/status updates, safe soft-retirement with active booking guard)
- SP-04: Vehicle Photo Management (upload, caption, display order reordering, primary photo designation invariant, deletion with auto-promotion)
- SP-05: Category Management (strict category-specific rules for bike, bicycle, tuk_tuk)
- SP-06: Technical Specifications (model year, seating capacity, transmission, fuel type, license plate)

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalLocation, RentalVehicleImage
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.rental_providers import router as rental_providers_router
from app.routers.rental_vehicles import router as rental_vehicles_router


# ==============================================================================
# Isolated SQLite Database Fixture
# ==============================================================================

@pytest.fixture
def isolated_db_session():
    """Creates a completely isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _register_sqlite_functions(dbapi_conn, _):
        if isinstance(dbapi_conn, sqlite3.Connection):
            dbapi_conn.create_function(
                "sysutcdatetime", 0,
                lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            )

    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_test_client(db, current_user: User | None = None):
    app = FastAPI()
    app.include_router(rental_providers_router)
    app.include_router(rental_vehicles_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


@pytest.fixture
def seed_test_data(isolated_db_session):
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    prov_user1 = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="provider")
    prov_user2 = User(id=2, full_name="Kamal Silva", email="kamal@rentals.lk", password_hash="pw", role="provider")
    pending_prov_user = User(id=3, full_name="Pending Prov", email="pending@rentals.lk", password_hash="pw", role="provider")
    homestay_prov_user = User(id=4, full_name="Homestay Prov", email="home@prov.lk", password_hash="pw", role="provider")
    traveler_user = User(id=5, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")
    admin_user = User(id=6, full_name="Admin Boss", email="admin@ceylongo.lk", password_hash="pw", role="admin")

    # 2. Provider Profiles
    prov_profile1 = ProviderProfile(
        id=101,
        user_id=1,
        business_name="Sunil Southern Scooters",
        phone="+94771234567",
        district="Galle",
        provider_type="rental",
        approval_status="approved",
    )
    prov_profile2 = ProviderProfile(
        id=102,
        user_id=2,
        business_name="Kamal Tuk Tuks",
        phone="+94772345678",
        district="Matara",
        provider_type="rental",
        approval_status="approved",
    )
    pending_profile = ProviderProfile(
        id=103,
        user_id=3,
        business_name="Unapproved Rentals",
        phone="+94773456789",
        district="Galle",
        provider_type="rental",
        approval_status="pending",
    )
    homestay_profile = ProviderProfile(
        id=104,
        user_id=4,
        business_name="Beachside Villa",
        phone="+94774567890",
        district="Galle",
        provider_type="homestay",
        approval_status="approved",
    )

    # 3. Location
    loc1 = RentalLocation(
        id=1,
        provider_id=101,
        location_name="Galle Fort Hub",
        address="12 Church St, Galle",
        city="Galle",
        district="Galle",
        latitude=Decimal("6.0267"),
        longitude=Decimal("80.2170"),
        is_active=True,
    )

    # 4. Existing Vehicles for Provider 1 (Sunil)
    v1 = RentalVehicle(
        id=1,
        provider_id=101,
        vehicle_type="bike",
        vehicle_name="Honda Dio 110cc",
        description="Reliable scooter in Galle",
        price_per_day=Decimal("2500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        model_year=2022,
        license_plate="SP-BDC-1234",
        seating_capacity=2,
        transmission="automatic",
        fuel_type="petrol",
        district="Galle",
        city="Galle Fort",
        location_id=1,
        cancellation_policy="Free cancellation up to 24h before pickup",
        free_cancellation_hours=24,
    )
    v2 = RentalVehicle(
        id=2,
        provider_id=101,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4-Stroke Tuk-Tuk",
        description="Iconic 3-wheeler in Galle",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        model_year=2020,
        license_plate="SP-AA-5678",
        seating_capacity=3,
        transmission="manual",
        fuel_type="petrol",
        district="Galle",
        city="Galle Fort",
        location_id=1,
    )
    v3 = RentalVehicle(
        id=3,
        provider_id=101,
        vehicle_type="bicycle",
        vehicle_name="Trek Mountain Bicycle",
        description="Ideal for quiet village rides",
        price_per_day=Decimal("1200.00"),
        status="unavailable",
        approval_status="pending",
        verification_status="unverified",
        model_year=2023,
        seating_capacity=1,
        transmission="manual",
        fuel_type="pedal",
        district="Galle",
        city="Unawatuna",
    )

    # 5. Vehicle for Provider 2 (Kamal)
    v4 = RentalVehicle(
        id=4,
        provider_id=102,
        vehicle_type="tuk_tuk",
        vehicle_name="Piaggio Ape City Tuk-Tuk",
        description="Comfortable ride around Mirissa",
        price_per_day=Decimal("4200.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        model_year=2021,
        license_plate="SP-AB-9999",
        seating_capacity=4,
        transmission="manual",
        fuel_type="petrol",
        district="Matara",
        city="Mirissa",
    )

    # 6. Images for Vehicle 1
    img1 = RentalVehicleImage(
        id=1,
        vehicle_id=1,
        image_url="https://images.ceylongo.lk/rentals/dio_front.jpg",
        caption="Front View",
        display_order=1,
        is_primary=True,
    )
    img2 = RentalVehicleImage(
        id=2,
        vehicle_id=1,
        image_url="https://images.ceylongo.lk/rentals/dio_side.jpg",
        caption="Side View",
        display_order=2,
        is_primary=False,
    )

    # 7. Bookings on Vehicle 1 (Sunil)
    # Active/confirmed booking
    b1 = RentalBooking(
        id=1,
        vehicle_id=1,
        user_id=5,
        pickup_at=now + timedelta(days=2),
        return_at=now + timedelta(days=5),
        total_price=Decimal("12500.00"),
        status="confirmed",
        payment_status="paid",
    )

    db.add_all([
        prov_user1, prov_user2, pending_prov_user, homestay_prov_user, traveler_user, admin_user,
        prov_profile1, prov_profile2, pending_profile, homestay_profile,
        loc1,
        v1, v2, v3, v4,
        img1, img2,
        b1,
    ])
    db.commit()

    return {
        "prov_user1": prov_user1,
        "prov_user2": prov_user2,
        "pending_prov_user": pending_prov_user,
        "homestay_prov_user": homestay_prov_user,
        "traveler_user": traveler_user,
        "admin_user": admin_user,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
        "img1": img1,
        "img2": img2,
        "b1": b1,
    }


# ==============================================================================
# SP-02: ADD RENTAL VEHICLE TESTS
# ==============================================================================

def test_add_rental_vehicle_success(isolated_db_session, seed_test_data):
    """SP-02, SP-05, SP-06: Approved rental provider creates a vehicle with full specs."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    payload = {
        "vehicle_type": "bike",
        "vehicle_name": "Yamaha FZ-S 150cc",
        "description": "Sporty motorcycle for scenic coast rides",
        "price_per_day": "3500.00",
        "model_year": 2023,
        "license_plate": "SP-BDD-8888",
        "seating_capacity": 2,
        "transmission": "manual",
        "fuel_type": "petrol",
        "district": "Galle",
        "city": "Hikkaduwa",
        "free_cancellation_hours": 48,
        "cancellation_policy": "Full refund up to 48h before rental start",
    }

    resp = client.post("/api/v1/provider/rentals/vehicles", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["vehicle_name"] == "Yamaha FZ-S 150cc"
    assert data["provider_id"] == 101
    assert data["vehicle_type"] == "bike"
    assert data["status"] == "available"
    # Strict compliance: new provider listing starts in pending approval state
    assert data["approval_status"] == "pending"
    assert data["verification_status"] == "unverified"
    assert data["seating_capacity"] == 2
    assert data["fuel_type"] == "petrol"
    assert data["model_year"] == 2023
    assert data["free_cancellation_hours"] == 48


def test_add_rental_vehicle_rbac_guards(isolated_db_session, seed_test_data):
    """SP-02: RBAC checks - travelers, unapproved providers, and non-rental providers blocked."""
    db = isolated_db_session

    payload = {
        "vehicle_type": "bike",
        "vehicle_name": "Yamaha FZ-S 150cc",
        "price_per_day": "3500.00",
        "seating_capacity": 2,
        "fuel_type": "petrol",
    }

    # 1. Traveler attempt -> 403 Forbidden
    client_traveler = make_test_client(db, current_user=seed_test_data["traveler_user"])
    resp1 = client_traveler.post("/api/v1/provider/rentals/vehicles", json=payload)
    assert resp1.status_code == 403
    assert "provider role required" in resp1.json()["detail"]

    # 2. Unapproved (pending) provider -> 403 Forbidden
    client_pending = make_test_client(db, current_user=seed_test_data["pending_prov_user"])
    resp2 = client_pending.post("/api/v1/provider/rentals/vehicles", json=payload)
    assert resp2.status_code == 403
    assert "pending" in resp2.json()["detail"]

    # 3. Homestay provider -> 403 Forbidden
    client_homestay = make_test_client(db, current_user=seed_test_data["homestay_prov_user"])
    resp3 = client_homestay.post("/api/v1/provider/rentals/vehicles", json=payload)
    assert resp3.status_code == 403
    assert "rental service providers" in resp3.json()["detail"]


# ==============================================================================
# SP-05 & SP-06: CATEGORY & TECHNICAL SPECIFICATIONS VALIDATION TESTS
# ==============================================================================

def test_category_specs_validation_bicycle(isolated_db_session, seed_test_data):
    """SP-05, SP-06: Bicycles must have <= 2 seats and non-petrol fuel."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Invalid: bicycle with 3 seats
    resp_seats = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "bicycle",
        "vehicle_name": "City Bicycle",
        "price_per_day": "1000.00",
        "seating_capacity": 3,
        "fuel_type": "pedal",
    })
    assert resp_seats.status_code == 422
    assert "Bicycles cannot have a seating capacity greater than 2" in resp_seats.text

    # Invalid: bicycle with petrol fuel
    resp_fuel = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "bicycle",
        "vehicle_name": "City Bicycle",
        "price_per_day": "1000.00",
        "seating_capacity": 1,
        "fuel_type": "petrol",
    })
    assert resp_fuel.status_code == 422
    assert "fuel_type" in resp_fuel.text

    # Valid: bicycle with pedal / electric / none
    resp_valid = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "bicycle",
        "vehicle_name": "Giant Electric Bicycle",
        "price_per_day": "1800.00",
        "seating_capacity": 1,
        "fuel_type": "electric",
    })
    assert resp_valid.status_code == 201


def test_category_specs_validation_tuk_tuk(isolated_db_session, seed_test_data):
    """SP-05, SP-06: Tuk-tuks must have 3-4 seats."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Invalid: tuk-tuk with 2 seats
    resp_low = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "tuk_tuk",
        "vehicle_name": "Mini Tuk",
        "price_per_day": "4000.00",
        "seating_capacity": 2,
        "fuel_type": "petrol",
    })
    assert resp_low.status_code == 422
    assert "Tuk-tuks must have a legal passenger seating capacity between 3 and 4" in resp_low.text

    # Invalid: tuk-tuk with 5 seats
    resp_high = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "tuk_tuk",
        "vehicle_name": "Mega Tuk",
        "price_per_day": "4000.00",
        "seating_capacity": 5,
        "fuel_type": "petrol",
    })
    assert resp_high.status_code == 422

    # Valid: tuk-tuk with 3 seats
    resp_valid = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "tuk_tuk",
        "vehicle_name": "Standard Tuk",
        "price_per_day": "4000.00",
        "seating_capacity": 3,
        "fuel_type": "petrol",
    })
    assert resp_valid.status_code == 201


def test_category_specs_validation_bike(isolated_db_session, seed_test_data):
    """SP-05, SP-06: Motorbikes cannot exceed 2 seats."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    resp_invalid = client.post("/api/v1/provider/rentals/vehicles", json={
        "vehicle_type": "bike",
        "vehicle_name": "Overloaded Scooter",
        "price_per_day": "2500.00",
        "seating_capacity": 3,
        "fuel_type": "petrol",
    })
    assert resp_invalid.status_code == 422
    assert "Motorbikes / scooters cannot exceed a seating capacity of 2" in resp_invalid.text


# ==============================================================================
# SP-03: MANAGE RENTAL VEHICLES (FLEET LIST & DETAIL) TESTS
# ==============================================================================

def test_list_provider_vehicles_isolation(isolated_db_session, seed_test_data):
    """SP-03: Provider fleet listing isolates data strictly by provider_id."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    resp1 = client1.get("/api/v1/provider/rentals/vehicles")
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total"] == 3
    # Provider 1 has vehicles 1, 2, 3
    v_ids = [item["id"] for item in data1["items"]]
    assert set(v_ids) == {1, 2, 3}
    assert 4 not in v_ids  # Vehicle 4 belongs to Provider 2!

    # Check enriched fields
    v1_item = next(item for item in data1["items"] if item["id"] == 1)
    assert v1_item["primary_image_url"] == "https://images.ceylongo.lk/rentals/dio_front.jpg"
    assert v1_item["image_count"] == 2
    assert v1_item["active_booking_count"] == 1  # b1 is confirmed

    # Check Provider 2 sees only their vehicle
    user2 = seed_test_data["prov_user2"]
    client2 = make_test_client(db, current_user=user2)
    resp2 = client2.get("/api/v1/provider/rentals/vehicles")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] == 1
    assert data2["items"][0]["id"] == 4


def test_list_provider_vehicles_filters_and_pagination(isolated_db_session, seed_test_data):
    """SP-03: Fleet listing supports filtering by status, approval_status, vehicle_type, and pagination."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Filter by vehicle_type
    resp_type = client.get("/api/v1/provider/rentals/vehicles?vehicle_type=tuk_tuk")
    assert resp_type.status_code == 200
    data_type = resp_type.json()
    assert data_type["total"] == 1
    assert data_type["items"][0]["id"] == 2

    # Filter by status
    resp_stat = client.get("/api/v1/provider/rentals/vehicles?status=unavailable")
    assert resp_stat.status_code == 200
    data_stat = resp_stat.json()
    assert data_stat["total"] == 1
    assert data_stat["items"][0]["id"] == 3

    # Filter by approval_status
    resp_appr = client.get("/api/v1/provider/rentals/vehicles?approval_status=pending")
    assert resp_appr.status_code == 200
    data_appr = resp_appr.json()
    assert data_appr["total"] == 1
    assert data_appr["items"][0]["id"] == 3

    # Pagination
    resp_p1 = client.get("/api/v1/provider/rentals/vehicles?page=1&limit=2")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert len(data_p1["items"]) == 2
    assert data_p1["page"] == 1
    assert data_p1["pages"] == 2

    resp_p2 = client.get("/api/v1/provider/rentals/vehicles?page=2&limit=2")
    assert resp_p2.status_code == 200
    data_p2 = resp_p2.json()
    assert len(data_p2["items"]) == 1


def test_get_provider_vehicle_detail(isolated_db_session, seed_test_data):
    """SP-03: Get single vehicle detail and cross-provider isolation."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    client1 = make_test_client(db, current_user=user1)

    # Provider 1 accesses own vehicle (id=1)
    resp = client1.get("/api/v1/provider/rentals/vehicles/1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["vehicle_name"] == "Honda Dio 110cc"
    assert len(data["images"]) == 2
    assert data["primary_image_url"] == "https://images.ceylongo.lk/rentals/dio_front.jpg"

    # Provider 1 attempts to access Provider 2's vehicle (id=4) -> 404
    resp_cross = client1.get("/api/v1/provider/rentals/vehicles/4")
    assert resp_cross.status_code == 404


# ==============================================================================
# SP-03: UPDATE & SAFE SOFT-RETIREMENT TESTS
# ==============================================================================

def test_update_provider_vehicle(isolated_db_session, seed_test_data):
    """SP-03, SP-05, SP-06: Update pricing, description, availability, and protect admin fields."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    payload = {
        "price_per_day": "2800.00",
        "description": "Updated scooter description with helmet included",
        "status": "unavailable",
        "approval_status": "approved",  # Tampering attempt
    }

    resp = client.patch("/api/v1/provider/rentals/vehicles/3", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(str(data["price_per_day"])) == Decimal("2800.00")
    assert data["description"] == "Updated scooter description with helmet included"
    assert data["status"] == "unavailable"
    # Protected field: provider cannot self-approve
    assert data["approval_status"] == "pending"


def test_soft_retirement_blocked_by_active_booking(isolated_db_session, seed_test_data, monkeypatch):
    """SP-03: Safe soft-retirement MUST prevent retirement if active or confirmed bookings exist (409 Conflict)."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)
    locks = []
    monkeypatch.setattr("app.routers.rental_providers.lock_booking_resource",
                        lambda session, key: locks.append(key))

    # Vehicle 1 has active/confirmed booking b1 -> MUST FAIL with 409 Conflict
    resp_del = client.delete("/api/v1/provider/rentals/vehicles/1")
    assert resp_del.status_code == 409
    assert "Cannot retire vehicle" in resp_del.json()["detail"]
    assert "booking(s) exist" in resp_del.json()["detail"]

    # Also test via PATCH status='retired' -> MUST ALSO FAIL with 409 Conflict
    resp_patch = client.patch("/api/v1/provider/rentals/vehicles/1", json={"status": "retired"})
    assert resp_patch.status_code == 409
    assert "Cannot retire vehicle" in resp_patch.json()["detail"]
    assert locks == ["rental_vehicle_booking_1", "rental_vehicle_booking_1"]


def test_soft_retirement_success_when_no_active_bookings(isolated_db_session, seed_test_data):
    """SP-03: Safe soft-retirement succeeds when no active bookings exist."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Vehicle 2 has no bookings -> retirement succeeds
    resp_del = client.delete("/api/v1/provider/rentals/vehicles/2")
    assert resp_del.status_code == 200
    data = resp_del.json()
    assert data["status"] == "retired"
    assert data["vehicle_id"] == 2

    # Verify vehicle status is now 'retired'
    v2_resp = client.get("/api/v1/provider/rentals/vehicles/2")
    assert v2_resp.json()["status"] == "retired"


# ==============================================================================
# SP-04: VEHICLE PHOTO MANAGEMENT TESTS
# ==============================================================================

def test_vehicle_photo_management_lifecycle(isolated_db_session, seed_test_data):
    """SP-04: Photo upload, caption update, reorder, primary designation, and delete with auto-promotion."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # 1. List existing images for vehicle 1
    resp_list = client.get("/api/v1/provider/rentals/vehicles/1/images")
    assert resp_list.status_code == 200
    assert len(resp_list.json()) == 2

    # 2. Add a new image (as primary)
    new_img_payload = {
        "image_url": "https://images.ceylongo.lk/rentals/dio_dashboard.jpg",
        "caption": "Digital Dashboard",
        "display_order": 0,
        "is_primary": True,
    }
    resp_add = client.post("/api/v1/provider/rentals/vehicles/1/images", json=new_img_payload)
    assert resp_add.status_code == 201
    img3_data = resp_add.json()
    assert img3_data["is_primary"] is True
    assert img3_data["caption"] == "Digital Dashboard"

    # Verify previous primary (img1) was unset
    resp_list_after = client.get("/api/v1/provider/rentals/vehicles/1/images")
    images = resp_list_after.json()
    assert len(images) == 3
    # Exactly one image has is_primary=True
    primary_images = [img for img in images if img["is_primary"]]
    assert len(primary_images) == 1
    assert primary_images[0]["id"] == img3_data["id"]

    # 3. Update photo caption & display order
    patch_payload = {
        "caption": "High-Tech Digital Display",
        "display_order": 5,
    }
    resp_patch = client.patch(f"/api/v1/provider/rentals/vehicles/1/images/{img3_data['id']}", json=patch_payload)
    assert resp_patch.status_code == 200
    assert resp_patch.json()["caption"] == "High-Tech Digital Display"
    assert resp_patch.json()["display_order"] == 5

    # 4. Delete the primary photo (img3) -> auto-promotes lowest display_order photo
    resp_del = client.delete(f"/api/v1/provider/rentals/vehicles/1/images/{img3_data['id']}")
    assert resp_del.status_code == 200
    assert "deleted successfully" in resp_del.json()["message"]

    # Verify remaining photos and auto-promoted primary
    resp_list_final = client.get("/api/v1/provider/rentals/vehicles/1/images")
    final_images = resp_list_final.json()
    assert len(final_images) == 2
    # Ensure there is still an active primary
    final_primaries = [img for img in final_images if img["is_primary"]]
    assert len(final_primaries) == 1
    assert final_primaries[0]["id"] == 1  # Lowest display_order was promoted back!


def test_vehicle_photo_cross_provider_isolation(isolated_db_session, seed_test_data):
    """SP-04: Cross-provider photo access blocked."""
    db = isolated_db_session
    user2 = seed_test_data["prov_user2"]
    client2 = make_test_client(db, current_user=user2)

    # Provider 2 attempts to upload photo to Provider 1's vehicle 1 -> 404
    resp = client2.post("/api/v1/provider/rentals/vehicles/1/images", json={
        "image_url": "https://images.ceylongo.lk/rentals/hacked.jpg",
    })
    assert resp.status_code == 404

    # Provider 2 attempts to delete photo from Provider 1's vehicle 1 -> 404
    resp_del = client2.delete("/api/v1/provider/rentals/vehicles/1/images/1")
    assert resp_del.status_code == 404


def test_legacy_vehicle_create_sets_pending_for_provider(isolated_db_session, seed_test_data):
    """Verify legacy POST /api/v1/rental-vehicles enforces pending status for non-admin providers."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    payload = {
        "vehicle_type": "bike",
        "vehicle_name": "TVS Apache 160",
        "price_per_day": "3200.00",
        "model_year": 2021,
        "seating_capacity": 2,
        "transmission": "manual",
        "fuel_type": "petrol",
        "district": "Galle",
    }

    resp = client.post("/api/v1/rental-vehicles", json=payload)
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["vehicle_name"] == "TVS Apache 160"

    # Verify directly in the database that non-admin provider creation starts in pending approval state
    created_vehicle = db.query(RentalVehicle).filter(RentalVehicle.id == data["id"]).first()
    assert created_vehicle is not None
    assert created_vehicle.approval_status == "pending"
