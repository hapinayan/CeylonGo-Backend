"""
CeylonGo Rental Backend - Phase 2 Batch 1 Test Suite
====================================================
Automated isolated unit & API tests verifying requirements TR-01 through TR-08:
- TR-01: Rental Vehicle Discovery (Pagination, cover photo, location, pricing)
- TR-02: Search & Multi-Criteria Filters (district, city, vehicle_type, price, calendar availability)
- TR-03: Vehicle Categories (types, fleet counts, license/helmet requirements)
- TR-04: Vehicle Details & Specifications (specs, photos, rate plans, 404 on unapproved)
- TR-05: Photo Gallery (chronological order by display_order)
- TR-06: Rental Locations & Google Maps (public hubs, GPS coordinates, vehicle hubs)
- TR-07: Pricing Plans & Quote Calculator (hourly, daily, weekly tiers, security deposit)
- TR-08: Availability Calendar (booked + maintenance ranges)

All database tests execute strictly against an isolated in-memory SQLite engine
to ensure zero mutations to CeylonGoDB in compliance with fail-closed guards.
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

from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.rental import (
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
    RentalAvailabilityBlock,
)
from app.models.shared import AdvertisingPayment, PromotionCampaign
from app.database.connection import get_db
from app.routers.auth import get_current_user
from app.routers.rental_vehicles import router as rental_vehicles_router
from app.routers.rental_locations import router as rental_locations_router


# ==============================================================================
# Isolated SQLite Fixture
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


@pytest.fixture
def test_app_and_client(isolated_db_session):
    """Initializes FastAPI test app overriding get_db."""
    test_app = FastAPI()
    test_app.include_router(rental_vehicles_router)
    test_app.include_router(rental_locations_router)

    def override_get_db():
        try:
            yield isolated_db_session
        finally:
            pass

    test_app.dependency_overrides[get_db] = override_get_db
    client = TestClient(test_app)
    return client, isolated_db_session


# Helper setup
def seed_test_fleet(db):
    """Seeds test users, provider profiles, locations, vehicles, images, and rate plans."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    u_prov1 = User(id=1, full_name="Provider One", email="provider1@ceylongo.com", password_hash="pw", role="provider")
    u_prov2 = User(id=2, full_name="Provider Two", email="provider2@ceylongo.com", password_hash="pw", role="provider")
    u_traveler = User(id=3, full_name="Traveler Bob", email="traveler@ceylongo.com", password_hash="pw", role="traveler")
    db.add_all([u_prov1, u_prov2, u_traveler])
    db.flush()

    # 2. Provider Profiles
    p1 = ProviderProfile(
        id=1, user_id=1, business_name="Galle Coastal Rentals",
        provider_type="rental", phone="+94771111111", district="Galle",
        approval_status="approved", identity_document_type="nic"
    )
    p2 = ProviderProfile(
        id=2, user_id=2, business_name="Kandy Hills Mobility",
        provider_type="rental", phone="+94772222222", district="Kandy",
        approval_status="pending", identity_document_type="nic"  # unverified provider
    )
    db.add_all([p1, p2])
    db.flush()

    # 3. Locations
    loc1 = RentalLocation(
        id=1, provider_id=1, location_name="Galle Fort Hub",
        address="12 Lighthouse Street", city="Galle", district="Galle",
        latitude=Decimal("6.0328"), longitude=Decimal("80.2170")
    )
    loc2 = RentalLocation(
        id=2, provider_id=1, location_name="Mirissa Beach Station",
        address="45 Beach Road", city="Mirissa", district="Matara",
        latitude=Decimal("5.9482"), longitude=Decimal("80.4716")
    )
    loc3 = RentalLocation(
        id=3, provider_id=2, location_name="Kandy City Station",
        address="8 Dalada Veediya", city="Kandy", district="Kandy",
        latitude=Decimal("7.2906"), longitude=Decimal("80.6337")
    )
    db.add_all([loc1, loc2, loc3])
    db.flush()

    # 4. Vehicles
    # V1: Approved Bike in Galle
    v1 = RentalVehicle(
        id=1, provider_id=1, vehicle_type="bike", vehicle_name="Honda Dio 110cc Scooter",
        description="Reliable automatic scooter for exploring Southern beaches.",
        price_per_day=Decimal("3500.00"), status="available", model_year=2023,
        seating_capacity=2, transmission="automatic", fuel_type="petrol",
        district="Galle", city="Galle", location_id=1, approval_status="approved",
        cancellation_policy="Free cancellation up to 24 hours before pickup", free_cancellation_hours=24
    )
    # V2: Approved Tuk-Tuk in Matara (Mirissa)
    v2 = RentalVehicle(
        id=2, provider_id=1, vehicle_type="tuk_tuk", vehicle_name="Bajaj RE 4-Stroke Tuk-Tuk",
        description="Classic Sri Lankan 3-wheeler for scenic self-drive adventures.",
        price_per_day=Decimal("6500.00"), status="available", model_year=2022,
        seating_capacity=3, transmission="manual", fuel_type="petrol",
        district="Matara", city="Mirissa", location_id=2, approval_status="approved",
        cancellation_policy="Strict 48-hour cancellation policy", free_cancellation_hours=48
    )
    # V3: Approved Bicycle in Galle
    v3 = RentalVehicle(
        id=3, provider_id=1, vehicle_type="bicycle", vehicle_name="Trek Marlin 6 Mountain Bike",
        description="Lightweight aluminum frame with hydraulic disc brakes.",
        price_per_day=Decimal("1500.00"), status="available", model_year=2024,
        seating_capacity=1, transmission="manual", fuel_type="manual",
        district="Galle", city="Galle", location_id=1, approval_status="approved",
        cancellation_policy="Free cancellation up to 12 hours", free_cancellation_hours=12
    )
    # V4: Unapproved Vehicle (should not show in discovery or details)
    v4 = RentalVehicle(
        id=4, provider_id=1, vehicle_type="bike", vehicle_name="Yamaha FZ 150cc",
        description="Unapproved draft bike.", price_per_day=Decimal("4500.00"),
        status="available", approval_status="pending", district="Galle", city="Galle"
    )
    # V5: Unavailable Vehicle (in maintenance / retired)
    v5 = RentalVehicle(
        id=5, provider_id=1, vehicle_type="bike", vehicle_name="Suzuki Access 125",
        description="In shop for repairs.", price_per_day=Decimal("3000.00"),
        status="unavailable", approval_status="approved", district="Galle", city="Galle"
    )
    # V6: Vehicle owned by unverified provider
    v6 = RentalVehicle(
        id=6, provider_id=2, vehicle_type="bike", vehicle_name="TVS King Kandy",
        description="Kandy bike.", price_per_day=Decimal("4000.00"),
        status="available", approval_status="approved", district="Kandy", city="Kandy", location_id=3
    )
    db.add_all([v1, v2, v3, v4, v5, v6])
    db.flush()

    # 5. Images for V1
    img1 = RentalVehicleImage(id=1, vehicle_id=1, image_url="https://images.ceylongo.com/v1_front.jpg", display_order=1, is_primary=True)
    img2 = RentalVehicleImage(id=2, vehicle_id=1, image_url="https://images.ceylongo.com/v1_side.jpg", display_order=2, is_primary=False)
    db.add_all([img1, img2])

    # 6. Rate Plans for V1 (Hourly, Daily, Weekly)
    rp_hourly = RentalRatePlan(
        id=1, vehicle_id=1, plan_name="Hourly Flex", duration_type="hourly",
        rate=Decimal("500.00"), currency="LKR", included_km_per_day=20, extra_km_charge=Decimal("40.00"), is_active=True
    )
    rp_daily = RentalRatePlan(
        id=2, vehicle_id=1, plan_name="Standard Daily", duration_type="daily",
        rate=Decimal("3500.00"), currency="LKR", included_km_per_day=100, extra_km_charge=Decimal("40.00"), is_active=True
    )
    rp_weekly = RentalRatePlan(
        id=3, vehicle_id=1, plan_name="Weekly Discount Saver", duration_type="weekly",
        rate=Decimal("21000.00"), currency="LKR", included_km_per_day=120, extra_km_charge=Decimal("35.00"), is_active=True
    )
    db.add_all([rp_hourly, rp_daily, rp_weekly])
    db.commit()


# ==============================================================================
# TR-01: RENTAL VEHICLE DISCOVERY TESTS
# ==============================================================================

def test_discovery_returns_only_approved_and_available_vehicles(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles")
    assert resp.status_code == 200
    data = resp.json()

    # Check pagination metadata
    assert "total" in data
    assert "page" in data
    assert "limit" in data
    assert "pages" in data
    assert "items" in data

    # Only V1, V2 and V3 are published by an approved provider.
    assert data["total"] == 3
    returned_ids = [item["id"] for item in data["items"]]
    assert 1 in returned_ids
    assert 2 in returned_ids
    assert 3 in returned_ids
    assert 6 not in returned_ids
    assert 4 not in returned_ids
    assert 5 not in returned_ids

    # Verify primary image and location summary enriched on V1
    v1_item = next(i for i in data["items"] if i["id"] == 1)
    assert v1_item["primary_image_url"] == "https://images.ceylongo.com/v1_front.jpg"
    assert v1_item["location_name"] == "Galle Fort Hub"
    assert v1_item["provider_business_name"] == "Galle Coastal Rentals"
    assert v1_item["is_provider_verified"] is True


def test_discovery_pagination_controls(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # Page 1, limit 2
    resp = client.get("/api/v1/rental-vehicles?page=1&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["total"] == 3
    assert data["pages"] == 2
    assert data["page"] == 1

    # Page 2, limit 2
    resp2 = client.get("/api/v1/rental-vehicles?page=2&limit=2")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 1
    assert data2["page"] == 2


# ==============================================================================
# TR-02: SEARCH & MULTI-CRITERIA FILTER TESTS
# ==============================================================================

def test_search_by_district_and_city(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # Filter by district 'Galle'
    resp = client.get("/api/v1/rental-vehicles?district=Galle")
    assert resp.status_code == 200
    ids = [i["id"] for i in resp.json()["items"]]
    assert 1 in ids  # V1 is in Galle
    assert 3 in ids  # V3 is in Galle
    assert 2 not in ids  # V2 is in Matara
    assert 6 not in ids  # V6 is in Kandy

    # Filter by city 'Mirissa'
    resp_city = client.get("/api/v1/rental-vehicles?city=Mirissa")
    assert resp_city.status_code == 200
    city_ids = [i["id"] for i in resp_city.json()["items"]]
    assert city_ids == [2]


def test_search_by_vehicle_type(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # Filter by category 'tuk_tuk'
    resp = client.get("/api/v1/rental-vehicles?vehicle_type=tuk_tuk")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == 2
    assert items[0]["vehicle_type"] == "tuk_tuk"

    # Filter by category 'bicycle'
    resp_b = client.get("/api/v1/rental-vehicles?vehicle_type=bicycle")
    assert resp_b.status_code == 200
    items_b = resp_b.json()["items"]
    assert len(items_b) == 1
    assert items_b[0]["id"] == 3


def test_search_by_price_range(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # Max price 2000 LKR (only Bicycle 1500)
    resp = client.get("/api/v1/rental-vehicles?max_price=2000")
    assert resp.status_code == 200
    ids = [i["id"] for i in resp.json()["items"]]
    assert ids == [3]

    # Min price 5000 LKR (only Tuk-Tuk 6500)
    resp_min = client.get("/api/v1/rental-vehicles?min_price=5000")
    assert resp_min.status_code == 200
    ids_min = [i["id"] for i in resp_min.json()["items"]]
    assert ids_min == [2]


def test_search_by_date_availability_excludes_booked_and_blocked(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=2)
    ret = now + timedelta(days=5)

    # Create active booking for V1 on days 3 to 4 (overlaps requested days 2 to 5)
    b1 = RentalBooking(
        vehicle_id=1, user_id=3,
        pickup_at=now + timedelta(days=3), return_at=now + timedelta(days=4),
        total_price=Decimal("7000.00"), status="confirmed", currency="LKR"
    )
    # Create maintenance blackout block for V2 on days 1 to 3 (overlaps requested pickup)
    blk = RentalAvailabilityBlock(
        vehicle_id=2, block_type="maintenance",
        start_datetime=now + timedelta(days=1), end_datetime=now + timedelta(days=3),
        reason="Brake overhaul", is_active=True
    )
    db.add_all([b1, blk])
    db.commit()

    # Query with date window [pickup, ret]
    resp = client.get(
        f"/api/v1/rental-vehicles?pickup_at={pickup.isoformat()}&return_at={ret.isoformat()}"
    )
    assert resp.status_code == 200
    avail_ids = [i["id"] for i in resp.json()["items"]]

    # V1 and V2 must be excluded because of booking and maintenance block
    assert 1 not in avail_ids
    assert 2 not in avail_ids
    # V3 is free. V6 belongs to a pending provider.
    assert 3 in avail_ids
    assert 6 not in avail_ids


def test_combined_filters_use_inclusive_price_and_half_open_availability(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)
    pickup = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=3)
    vehicle = db.get(RentalVehicle, 1)
    booking = RentalBooking(
        vehicle_id=vehicle.id, user_id=3, pickup_at=pickup - timedelta(days=1),
        return_at=pickup, total_price=vehicle.price_per_day, status="in_progress",
    )
    db.add(booking)
    db.commit()
    query = {
        "district": vehicle.district, "city": vehicle.city, "vehicle_type": vehicle.vehicle_type,
        "min_price": str(vehicle.price_per_day), "max_price": str(vehicle.price_per_day),
        "pickup_at": pickup.isoformat(), "return_at": (pickup + timedelta(hours=2)).isoformat(),
    }
    response = client.get("/api/v1/rental-vehicles", params=query)
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [vehicle.id]
    booking.return_at = pickup + timedelta(seconds=1)
    db.commit()
    response = client.get("/api/v1/rental-vehicles", params=query)
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_search_date_availability_invalid_range_rejected(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # return before pickup
    resp = client.get(
        f"/api/v1/rental-vehicles?pickup_at={(now + timedelta(days=5)).isoformat()}&return_at={(now + timedelta(days=2)).isoformat()}"
    )
    assert resp.status_code == 400
    assert "return_at must be strictly after pickup_at" in resp.json()["detail"]


# ==============================================================================
# TR-03: VEHICLE CATEGORIES TESTS
# ==============================================================================

def test_get_categories_returns_supported_types_and_fleet_counts(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles/categories")
    assert resp.status_code == 200
    categories = resp.json()

    assert len(categories) == 3
    cat_map = {c["category"]: c for c in categories}

    assert "bike" in cat_map
    assert "bicycle" in cat_map
    assert "tuk_tuk" in cat_map

    # Active fleet counts:
    # 2 approved & available bikes (V1, V6) (V4 unapproved, V5 unavailable)
    assert cat_map["bike"]["active_fleet_count"] == 2
    assert cat_map["bike"]["requires_license"] is True
    assert cat_map["bike"]["requires_helmet"] is True

    # 1 approved & available bicycle (V3)
    assert cat_map["bicycle"]["active_fleet_count"] == 1
    assert cat_map["bicycle"]["requires_license"] is False
    assert cat_map["bicycle"]["requires_helmet"] is False

    # 1 approved & available tuk-tuk (V2)
    assert cat_map["tuk_tuk"]["active_fleet_count"] == 1
    assert cat_map["tuk_tuk"]["requires_license"] is True
    assert cat_map["tuk_tuk"]["requires_helmet"] is False


# ==============================================================================
# TR-04: VEHICLE DETAILS & SPECIFICATIONS TESTS
# ==============================================================================

def test_vehicle_details_returns_full_specifications_and_relations(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles/1")
    assert resp.status_code == 200
    v = resp.json()

    assert v["id"] == 1
    assert v["vehicle_name"] == "Honda Dio 110cc Scooter"
    assert v["model_year"] == 2023
    assert v["seating_capacity"] == 2
    assert v["transmission"] == "automatic"
    assert v["fuel_type"] == "petrol"
    assert v["cancellation_policy"] == "Free cancellation up to 24 hours before pickup"
    assert v["free_cancellation_hours"] == 24
    assert v["is_provider_verified"] is True
    assert v["provider_business_name"] == "Galle Coastal Rentals"
    assert v["location"]["location_name"] == "Galle Fort Hub"
    assert float(v["location"]["latitude"]) == 6.0328
    assert len(v["images"]) == 2
    assert len(v["rate_plans"]) == 3


def test_unapproved_vehicle_details_returns_404(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # V4 is pending admin approval
    resp = client.get("/api/v1/rental-vehicles/4")
    assert resp.status_code == 404
    assert "Rental vehicle not found" in resp.json()["detail"]


# ==============================================================================
# TR-05: PHOTO GALLERY TESTS
# ==============================================================================

def test_get_vehicle_photo_gallery_ordered_by_display_order(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles/1/images")
    assert resp.status_code == 200
    images = resp.json()

    assert len(images) == 2
    assert images[0]["display_order"] == 1
    assert images[0]["is_primary"] is True
    assert images[0]["image_url"] == "https://images.ceylongo.com/v1_front.jpg"
    assert images[1]["display_order"] == 2
    assert images[1]["is_primary"] is False


def test_gallery_unapproved_vehicle_returns_404(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles/4/images")
    assert resp.status_code == 404


# ==============================================================================
# TR-06: RENTAL LOCATIONS & GOOGLE MAPS TESTS
# ==============================================================================

def test_get_rental_locations_public_and_district_filter(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    db.add(RentalLocation(
        provider_id=1, location_name="Inactive Galle hub", district="Galle", city="Galle", is_active=False,
    ))
    db.commit()

    # All active locations
    resp = client.get("/api/v1/rental-locations")
    assert resp.status_code == 200
    locs = resp.json()
    assert len(locs) == 3

    # Filter district 'Galle'
    resp_galle = client.get("/api/v1/rental-locations?district=Galle")
    assert resp_galle.status_code == 200
    galle_locs = resp_galle.json()
    assert len(galle_locs) == 1
    assert galle_locs[0]["location_name"] == "Galle Fort Hub"
    assert float(galle_locs[0]["latitude"]) == 6.0328
    assert float(galle_locs[0]["longitude"]) == 80.2170


def test_get_vehicle_locations(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # The primary hub stays first even when the second hub sorts before it.
    secondary = db.get(RentalLocation, 2)
    secondary.location_name = "A secondary hub"
    secondary.latitude = secondary.longitude = None
    db.add(RentalLocation(
        provider_id=1, location_name="Inactive Galle hub", district="Galle", city="Galle", is_active=False,
    ))
    db.commit()
    resp = client.get("/api/v1/rental-vehicles/1/locations")
    assert resp.status_code == 200
    locs = resp.json()
    assert len(locs) == 2
    # Primary vehicle location (Galle Fort Hub) is first
    assert locs[0]["id"] == 1
    assert locs[0]["location_name"] == "Galle Fort Hub"
    assert locs[0]["latitude"] == "6.032800"
    assert locs[0]["longitude"] == "80.217000"
    assert locs[1]["id"] == 2
    assert locs[1]["latitude"] is None
    assert locs[1]["longitude"] is None


def test_vehicle_locations_exclude_foreign_historical_primary(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)
    db.get(RentalVehicle, 1).location_id = 3
    db.commit()

    response = client.get("/api/v1/rental-vehicles/1/locations")
    assert response.status_code == 200
    assert [hub["id"] for hub in response.json()] == [1, 2]


# ==============================================================================
# TR-07: PRICING PLANS & QUOTE CALCULATOR TESTS
# ==============================================================================

def test_get_vehicle_rate_plans_list(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    resp = client.get("/api/v1/rental-vehicles/1/rate-plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert len(plans) == 3
    types = [p["duration_type"] for p in plans]
    assert "hourly" in types
    assert "daily" in types
    assert "weekly" in types


def test_calculate_quote_hourly_tier(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=1, hours=9)
    ret = pickup + timedelta(hours=4)  # 4-hour rental

    resp = client.post(
        "/api/v1/rental-vehicles/1/calculate-quote",
        json={"pickup_at": pickup.isoformat(), "return_at": ret.isoformat()}
    )
    assert resp.status_code == 200
    quote = resp.json()

    assert quote["duration_type"] == "hourly"
    assert quote["duration_hours"] == 4.0
    # 4 hours * 500 LKR = 2000.00 LKR
    assert float(quote["total_price"]) == 2000.0
    assert float(quote["security_deposit"]) == 10000.0  # standard bike deposit


def test_calculate_quote_daily_and_weekly_tier(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=1)
    ret = pickup + timedelta(days=7)  # 7-day rental (weekly discount)

    resp = client.post(
        "/api/v1/rental-vehicles/1/calculate-quote",
        json={"pickup_at": pickup.isoformat(), "return_at": ret.isoformat()}
    )
    assert resp.status_code == 200
    quote = resp.json()

    assert quote["duration_type"] == "weekly"
    assert quote["duration_days"] == 7
    # 1 week * 21000 = 21000.00 LKR (cheaper than 7 * 3500 = 24500)
    assert float(quote["total_price"]) == 21000.0


def test_calculate_quote_fallback_to_vehicle_price_per_day(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # V2 (Tuk-Tuk) has no explicit rate plans configured; falls back to price_per_day 6500.00
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pickup = now + timedelta(days=1)
    ret = pickup + timedelta(days=3)  # 3 days

    resp = client.post(
        "/api/v1/rental-vehicles/2/calculate-quote",
        json={"pickup_at": pickup.isoformat(), "return_at": ret.isoformat()}
    )
    assert resp.status_code == 200
    quote = resp.json()

    assert quote["duration_type"] == "daily"
    assert quote["duration_days"] == 3
    # 3 * 6500 = 19500.00 LKR
    assert float(quote["total_price"]) == 19500.0
    assert float(quote["security_deposit"]) == 15000.0  # tuk-tuk standard deposit


@pytest.mark.parametrize(("pickup_at", "return_at", "expected_status"), [
    ("2090-12-05T03:30:00", "2090-12-06T09:00:00+05:30", 200),
    ("2090-12-05T09:00:00+05:30", "2090-12-06T03:30:00", 200),
    ("2090-12-05T03:30:00", "2090-12-05T09:00:00+05:30", 422),
    ("2090-12-05T09:00:00+05:30", "2090-12-05T03:29:59", 422),
])
def test_calculate_quote_mixed_datetime_offsets(test_app_and_client, pickup_at, return_at, expected_status):
    client, db = test_app_and_client
    seed_test_fleet(db)

    response = client.post(
        "/api/v1/rental-vehicles/1/calculate-quote",
        json={"pickup_at": pickup_at, "return_at": return_at},
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        quote = response.json()
        assert quote["duration_hours"] == 24
        assert Decimal(quote["total_price"]) == Decimal(quote["rate"])
    else:
        assert "Return date must be strictly after pickup date" in response.text


def test_calculate_quote_invalid_dates_rejected(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # return <= pickup
    resp = client.post(
        "/api/v1/rental-vehicles/1/calculate-quote",
        json={
            "pickup_at": (now + timedelta(days=2)).isoformat(),
            "return_at": (now + timedelta(days=1)).isoformat()
        }
    )
    assert resp.status_code == 422


# ==============================================================================
# TR-08: AVAILABILITY CALENDAR TESTS
# ==============================================================================

def test_get_availability_calendar_returns_booked_and_maintenance_windows(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Add 1 booking and 1 maintenance block on V1
    b = RentalBooking(
        vehicle_id=1, user_id=3,
        pickup_at=now + timedelta(days=2), return_at=now + timedelta(days=4),
        total_price=Decimal("7000.00"), status="confirmed", currency="LKR"
    )
    blk = RentalAvailabilityBlock(
        vehicle_id=1, block_type="maintenance",
        start_datetime=now + timedelta(days=6), end_datetime=now + timedelta(days=7),
        reason="Chain maintenance", is_active=True
    )
    db.add_all([b, blk])
    db.commit()

    # Query calendar without specific dates
    resp = client.get("/api/v1/rental-vehicles/1/availability")
    assert resp.status_code == 200
    cal = resp.json()

    assert cal["vehicle_id"] == 1
    assert len(cal["unavailable_periods"]) == 2
    assert cal["unavailable_periods"][0]["reason"] == "booked"
    assert cal["unavailable_periods"][1]["reason"] == "maintenance"

    # Query window during booking: should return is_available = False
    window_start = now + timedelta(days=3)
    window_end = now + timedelta(days=5)
    resp_booked = client.get(
        f"/api/v1/rental-vehicles/1/availability?start_date={window_start.isoformat()}&end_date={window_end.isoformat()}"
    )
    assert resp_booked.status_code == 200
    assert resp_booked.json()["is_available"] is False

    # Query window when completely free (days 8 to 10): is_available = True
    free_start = now + timedelta(days=8)
    free_end = now + timedelta(days=10)
    resp_free = client.get(
        f"/api/v1/rental-vehicles/1/availability?start_date={free_start.isoformat()}&end_date={free_end.isoformat()}"
    )
    assert resp_free.status_code == 200
    assert resp_free.json()["is_available"] is True
    assert len(resp_free.json()["unavailable_periods"]) == 0


# ==============================================================================
# TR-19 & TR-20 DISCOVERY ENRICHMENT TESTS
# ==============================================================================

def test_sponsored_vehicle_boosted_to_top_of_discovery(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # A paid campaign cannot publish a pending provider's listing.
    # Approve the provider in this positive ranking scenario.
    db.get(ProviderProfile, 2).approval_status = "approved"
    # Create active promotion campaign for Provider 2 (owning V6 in Kandy)
    camp = PromotionCampaign(
        provider_profile_id=2, campaign_name="Kandy Super Promo",
        target_module="rental", budget=Decimal("50000.00"), spent_amount=Decimal("1000.00"),
        currency="LKR", status="active",
        starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=10)
    )
    db.add(camp)
    db.commit()

    before_payment = client.get("/api/v1/rental-vehicles")
    assert before_payment.status_code == 200
    assert not any(item["is_sponsored"] for item in before_payment.json()["items"])
    db.add(AdvertisingPayment(
        campaign_id=camp.id, provider_profile_id=camp.provider_profile_id,
        order_reference="AD-RENTAL-KANDY", gateway_transaction_id="KANDY-PAID",
        amount=camp.budget, currency=camp.currency, payment_status="completed", verified_at=now,
    ))
    db.commit()

    resp = client.get("/api/v1/rental-vehicles")
    assert resp.status_code == 200
    items = resp.json()["items"]

    # V6 must be first item in the list and flagged is_sponsored = True
    assert items[0]["id"] == 6
    assert items[0]["is_sponsored"] is True

    # Other vehicles must be is_sponsored = False
    other_items = [i for i in items if i["id"] != 6]
    for i in other_items:
        assert i["is_sponsored"] is False

    for campaign_status, starts_at, ends_at in (
        ("rejected", now - timedelta(days=1), now + timedelta(days=1)),
        ("approved", now - timedelta(days=1), now + timedelta(days=1)),
        ("active", now - timedelta(days=2), now - timedelta(days=1)),
        ("active", now + timedelta(days=1), now + timedelta(days=2)),
    ):
        camp.status, camp.starts_at, camp.ends_at = campaign_status, starts_at, ends_at
        db.commit()
        response = client.get("/api/v1/rental-vehicles")
        assert response.status_code == 200
        assert not any(item["is_sponsored"] for item in response.json()["items"])


@pytest.mark.parametrize("query", [
    "pickup_at=2090-01-01T10%3A00%3A00Z",
    "return_at=2090-01-02T10%3A00%3A00Z",
    "min_price=9000&max_price=7000",
])
def test_discovery_rejects_incomplete_or_inverted_filters(test_app_and_client, query):
    client, _ = test_app_and_client
    response = client.get(f"/api/v1/rental-vehicles?{query}")
    assert response.status_code == 400


def test_verified_only_provider_filter(test_app_and_client):
    client, db = test_app_and_client
    seed_test_fleet(db)

    # Provider 1 is approved/verified. Provider 2 is pending/unverified.
    resp = client.get("/api/v1/rental-vehicles?verified_only=true")
    assert resp.status_code == 200
    items = resp.json()["items"]

    returned_ids = [i["id"] for i in items]
    # V1, V2, V3 belong to Provider 1 (verified)
    assert 1 in returned_ids
    assert 2 in returned_ids
    assert 3 in returned_ids
    # V6 belongs to Provider 2 (unverified) and must be excluded
    assert 6 not in returned_ids
