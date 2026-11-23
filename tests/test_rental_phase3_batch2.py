"""
CeylonGo Rental Backend - Phase 3 Batch 2 Test Suite
====================================================
Automated isolated unit & API tests verifying Service Provider requirements SP-07 through SP-09:
- SP-07: Rental Pricing Management (rate plans CRUD: hourly, daily, weekly, monthly, mileage allowances, extra km charges, activation toggle, conflict guard)
- SP-08: Rental Availability Management (blackout & maintenance blocks CRUD, booking collision detection, traveler discovery & calendar integration)
- SP-09: Pickup & Return Location Management (station/hub CRUD, GPS coordinate validation, cross-provider isolation, public discovery sync)

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.user import Base, User
from app.routers.auth import get_current_user
from app.routers.rental_locations import router as rental_locations_router
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
    app.include_router(rental_locations_router)

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
    traveler_user = User(id=3, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")

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

    # 3. Locations
    loc1 = RentalLocation(
        id=1,
        provider_id=101,
        location_name="Galle Fort Main Station",
        district="Galle",
        city="Galle",
        address="12 Church St, Galle Fort",
        latitude=Decimal("6.026700"),
        longitude=Decimal("80.217000"),
        is_active=True,
    )
    loc2 = RentalLocation(
        id=2,
        provider_id=102,
        location_name="Mirissa Beach Station",
        district="Matara",
        city="Mirissa",
        address="Beach Road, Mirissa",
        latitude=Decimal("5.948200"),
        longitude=Decimal("80.457800"),
        is_active=True,
    )

    # 4. Vehicles
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
        location_id=1,
    )
    v2 = RentalVehicle(
        id=2,
        provider_id=102,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4-Stroke",
        description="Comfortable 3-wheeler in Mirissa",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        location_id=2,
    )

    # 5. Rate Plans for Vehicle 1
    rp1 = RentalRatePlan(
        id=1,
        vehicle_id=1,
        plan_name="Standard Daily",
        duration_type="daily",
        rate=Decimal("2500.00"),
        currency="LKR",
        included_km_per_day=100,
        extra_km_charge=Decimal("25.00"),
        is_active=True,
    )
    rp2 = RentalRatePlan(
        id=2,
        vehicle_id=1,
        plan_name="Weekly Discount Rate",
        duration_type="weekly",
        rate=Decimal("15000.00"),
        currency="LKR",
        included_km_per_day=100,
        extra_km_charge=Decimal("20.00"),
        is_active=True,
    )

    # 6. Existing Active Booking on Vehicle 1 (Days 5 to 8)
    b1 = RentalBooking(
        id=1,
        vehicle_id=1,
        user_id=3,
        pickup_at=now + timedelta(days=5),
        return_at=now + timedelta(days=8),
        total_price=Decimal("7500.00"),
        status="confirmed",
        payment_status="paid",
    )

    # 7. Existing Availability Block on Vehicle 1 (Days 12 to 14)
    ab1 = RentalAvailabilityBlock(
        id=1,
        vehicle_id=1,
        start_datetime=now + timedelta(days=12),
        end_datetime=now + timedelta(days=14),
        block_type="maintenance",
        reason="Scheduled 5,000km engine oil service",
        is_active=True,
    )

    db.add_all([
        prov_user1, prov_user2, traveler_user,
        prov_profile1, prov_profile2,
        loc1, loc2,
        v1, v2,
        rp1, rp2,
        b1,
        ab1,
    ])
    db.commit()

    return {
        "prov_user1": prov_user1,
        "prov_user2": prov_user2,
        "traveler_user": traveler_user,
        "v1": v1,
        "v2": v2,
        "rp1": rp1,
        "rp2": rp2,
        "loc1": loc1,
        "loc2": loc2,
        "b1": b1,
        "ab1": ab1,
    }


# ==============================================================================
# SP-07: RENTAL PRICING MANAGEMENT TESTS
# ==============================================================================

def test_list_vehicle_rate_plans_and_isolation(isolated_db_session, seed_test_data):
    """SP-07: Provider can list all rate plans for their vehicle; cross-provider access blocked."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]

    client1 = make_test_client(db, current_user=user1)
    resp = client1.get("/api/v1/provider/rentals/vehicles/1/rate-plans")
    assert resp.status_code == 200
    plans = resp.json()
    assert len(plans) == 2
    types = [p["duration_type"] for p in plans]
    assert "daily" in types
    assert "weekly" in types

    # Provider 2 attempts to list Provider 1's vehicle rate plans -> 404
    client2 = make_test_client(db, current_user=user2)
    resp_cross = client2.get("/api/v1/provider/rentals/vehicles/1/rate-plans")
    assert resp_cross.status_code == 404


def test_add_vehicle_rate_plan_success_and_validation(isolated_db_session, seed_test_data):
    """SP-07: Provider adds rate plan; rejects invalid prices and conflicting duplicate active tiers."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # 1. Successful addition of hourly rate plan
    payload_hourly = {
        "plan_name": "Quick Hourly Cruise",
        "duration_type": "hourly",
        "rate": "500.00",
        "currency": "LKR",
        "included_km_per_day": 20,
        "extra_km_charge": "30.00",
        "is_active": True,
    }
    resp = client.post("/api/v1/provider/rentals/vehicles/1/rate-plans", json=payload_hourly)
    assert resp.status_code == 201
    data = resp.json()
    assert data["duration_type"] == "hourly"
    assert Decimal(str(data["rate"])) == Decimal("500.00")
    assert data["vehicle_id"] == 1

    # 2. Reject negative rate
    payload_negative = {
        "plan_name": "Invalid Rate",
        "duration_type": "monthly",
        "rate": "-100.00",
    }
    resp_neg = client.post("/api/v1/provider/rentals/vehicles/1/rate-plans", json=payload_negative)
    assert resp_neg.status_code == 422

    # 3. Reject duplicate active plan of the same tier (Vehicle 1 already has active 'daily' plan)
    payload_duplicate = {
        "plan_name": "Duplicate Daily Plan",
        "duration_type": "daily",
        "rate": "2800.00",
        "is_active": True,
    }
    resp_dup = client.post("/api/v1/provider/rentals/vehicles/1/rate-plans", json=payload_duplicate)
    assert resp_dup.status_code == 409
    assert "active 'daily' rate plan already exists" in resp_dup.json()["detail"]


def test_update_and_delete_rate_plan(isolated_db_session, seed_test_data):
    """SP-07: Provider can update and delete vehicle rate plans."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Update rate plan 1 (daily)
    update_payload = {
        "plan_name": "Peak Season Daily Rate",
        "rate": "3000.00",
        "extra_km_charge": "35.00",
    }
    resp_patch = client.patch("/api/v1/provider/rentals/vehicles/1/rate-plans/1", json=update_payload)
    assert resp_patch.status_code == 200
    data = resp_patch.json()
    assert data["plan_name"] == "Peak Season Daily Rate"
    assert Decimal(str(data["rate"])) == Decimal("3000.00")
    assert Decimal(str(data["extra_km_charge"])) == Decimal("35.00")

    # Delete rate plan 2 (weekly)
    resp_del = client.delete("/api/v1/provider/rentals/vehicles/1/rate-plans/2")
    assert resp_del.status_code == 200
    assert "deleted successfully" in resp_del.json()["message"]

    # Verify deletion
    resp_list = client.get("/api/v1/provider/rentals/vehicles/1/rate-plans")
    remaining_ids = [p["id"] for p in resp_list.json()]
    assert 2 not in remaining_ids


def test_traveler_quote_calculator_integration(isolated_db_session, seed_test_data):
    """SP-07: Newly added/updated provider rate plans integrate seamlessly into Traveler quote calculation."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Calculate 1-day quote for vehicle 1 (rate plan 1 was set to 2500)
    quote_req = {
        "pickup_at": (now + timedelta(days=20)).isoformat(),
        "return_at": (now + timedelta(days=21)).isoformat(),
    }
    resp = client.post("/api/v1/rental-vehicles/1/calculate-quote", json=quote_req)
    assert resp.status_code == 200
    quote = resp.json()
    assert Decimal(str(quote["total_price"])) == Decimal("2500.00")
    assert quote["selected_plan_name"] == "Standard Daily"


# ==============================================================================
# SP-08: AVAILABILITY & BLACKOUT MANAGEMENT TESTS
# ==============================================================================

def test_list_availability_blocks_and_isolation(isolated_db_session, seed_test_data):
    """SP-08: Provider lists vehicle availability blocks; cross-provider access blocked."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]

    client1 = make_test_client(db, current_user=user1)
    resp = client1.get("/api/v1/provider/rentals/vehicles/1/blocks")
    assert resp.status_code == 200
    blocks = resp.json()
    assert len(blocks) == 1
    assert blocks[0]["block_type"] == "maintenance"

    # Provider 2 attempt on Provider 1 vehicle -> 404
    client2 = make_test_client(db, current_user=user2)
    resp_cross = client2.get("/api/v1/provider/rentals/vehicles/1/blocks")
    assert resp_cross.status_code == 404


def test_create_availability_block_collision_guard(isolated_db_session, seed_test_data):
    """SP-08: Availability block rejected if dates are invalid or collide with confirmed bookings."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # 1. Invalid date range (end <= start) -> 422
    payload_invalid = {
        "start_datetime": (now + timedelta(days=10)).isoformat(),
        "end_datetime": (now + timedelta(days=9)).isoformat(),
        "block_type": "maintenance",
    }
    resp_inv = client.post("/api/v1/provider/rentals/vehicles/1/blocks", json=payload_invalid)
    assert resp_inv.status_code == 422

    # 2. Collision with confirmed booking b1 (Days 5 to 8) -> 409 Conflict
    payload_colliding = {
        "start_datetime": (now + timedelta(days=6)).isoformat(),
        "end_datetime": (now + timedelta(days=7)).isoformat(),
        "block_type": "maintenance",
        "reason": "Emergency tire repair",
    }
    resp_col = client.post("/api/v1/provider/rentals/vehicles/1/blocks", json=payload_colliding)
    assert resp_col.status_code == 409
    assert "Cannot block availability" in resp_col.json()["detail"]
    assert "active or confirmed booking(s)" in resp_col.json()["detail"]

    # 3. Non-overlapping block (Days 1 to 3) -> 201 Created
    payload_clear = {
        "start_datetime": (now + timedelta(days=1)).isoformat(),
        "end_datetime": (now + timedelta(days=3)).isoformat(),
        "block_type": "service",
        "reason": "Brake pad replacement",
        "is_active": True,
    }
    resp_clear = client.post("/api/v1/provider/rentals/vehicles/1/blocks", json=payload_clear)
    assert resp_clear.status_code == 201
    assert resp_clear.json()["block_type"] == "service"


def test_legacy_and_provider_blackouts_share_calendar_and_vehicle_lock(isolated_db_session, seed_test_data, monkeypatch):
    db = isolated_db_session
    booking = seed_test_data["b1"]
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])
    legacy = "/api/v1/rental-vehicles/1/availability-blocks"
    current = "/api/v1/provider/rentals/vehicles/1/blocks"
    lock_keys = []
    for router in ("rental_vehicles", "rental_providers"):
        monkeypatch.setattr(f"app.routers.{router}.lock_booking_resource",
                            lambda session, key: lock_keys.append(key))
    booking.status = "in_progress"
    db.commit()
    start = booking.pickup_at.date()
    end = start + timedelta(days=1)
    dates = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    assert client.post(legacy, json=dates).status_code == 409

    booking.status = "pending"
    booking.expires_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    monkeypatch.setattr("app.routers.rental_vehicles.utc_now", lambda: booking.expires_at)
    created = client.post(legacy, json=dates)
    assert created.status_code == 201, created.text
    assert created.json()["start_date"] == dates["start_date"]
    assert created.json()["end_date"] == dates["end_date"]
    block_id = created.json()["id"]
    block = db.get(RentalAvailabilityBlock, block_id)
    assert block.end_datetime == datetime.combine(end + timedelta(days=1), datetime.min.time())
    assert any(item["id"] == block_id for item in client.get(legacy).json())
    assert client.patch(f"{current}/{block_id}", json={"reason": "Shared calendar"}).status_code == 200
    assert client.delete(f"{legacy}/{block_id}").status_code == 200
    assert all(item["id"] != block_id for item in client.get(legacy).json())
    assert lock_keys == ["rental_vehicle_booking_1"] * 4


def test_update_and_delete_availability_block(isolated_db_session, seed_test_data):
    """SP-08: Provider can update and delete vehicle availability blocks."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # Update block 1 reason and type
    patch_payload = {
        "block_type": "accident_repair",
        "reason": "Rear fender scratch repair & repaint",
    }
    resp_patch = client.patch("/api/v1/provider/rentals/vehicles/1/blocks/1", json=patch_payload)
    assert resp_patch.status_code == 200
    assert resp_patch.json()["block_type"] == "accident_repair"
    assert resp_patch.json()["reason"] == "Rear fender scratch repair & repaint"

    # Delete block 1
    resp_del = client.delete("/api/v1/provider/rentals/vehicles/1/blocks/1")
    assert resp_del.status_code == 200
    assert "deleted successfully" in resp_del.json()["message"]

    # Verify deleted
    resp_list = client.get("/api/v1/provider/rentals/vehicles/1/blocks")
    assert len(resp_list.json()) == 0


def test_availability_block_traveler_integration(isolated_db_session, seed_test_data):
    """SP-08: Active availability blocks are respected by Traveler search and calendar views."""
    db = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # ab1 is active on Vehicle 1 from Days 12 to 14
    # Query traveler search during Days 12 to 13 -> Vehicle 1 should NOT be returned
    pickup = (now + timedelta(days=12, hours=1)).isoformat()
    dropoff = (now + timedelta(days=13)).isoformat()
    resp_search = client.get(f"/api/v1/rental-vehicles?pickup_at={pickup}&return_at={dropoff}")
    assert resp_search.status_code == 200
    v_ids = [v["id"] for v in resp_search.json()["items"]]
    assert 1 not in v_ids  # Excluded due to maintenance block!

    # Query traveler calendar view (TR-08) for Vehicle 1
    resp_cal = client.get(f"/api/v1/rental-vehicles/1/availability?start_date={(now + timedelta(days=10)).isoformat()}&end_date={(now + timedelta(days=16)).isoformat()}")
    assert resp_cal.status_code == 200
    cal_data = resp_cal.json()
    assert cal_data["is_available"] is False
    reasons = [p["reason"] for p in cal_data["unavailable_periods"]]
    assert "maintenance" in reasons


# ==============================================================================
# SP-09: PICKUP & RETURN LOCATION MANAGEMENT TESTS
# ==============================================================================

def test_list_provider_locations_and_isolation(isolated_db_session, seed_test_data):
    """SP-09: Provider lists their locations; strict multi-tenant isolation."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]

    client1 = make_test_client(db, current_user=user1)
    resp1 = client1.get("/api/v1/provider/rentals/locations")
    assert resp1.status_code == 200
    locs1 = resp1.json()
    assert len(locs1) == 1
    assert locs1[0]["id"] == 1
    assert locs1[0]["location_name"] == "Galle Fort Main Station"

    # Provider 2 sees only their location
    client2 = make_test_client(db, current_user=user2)
    resp2 = client2.get("/api/v1/provider/rentals/locations")
    assert resp2.status_code == 200
    locs2 = resp2.json()
    assert len(locs2) == 1
    assert locs2[0]["id"] == 2
    assert locs2[0]["location_name"] == "Mirissa Beach Station"

    # Provider 1 cannot access Provider 2's location detail -> 404
    resp_cross = client1.get("/api/v1/provider/rentals/locations/2")
    assert resp_cross.status_code == 404


def test_create_provider_location_and_gps_validation(isolated_db_session, seed_test_data):
    """SP-09: Provider creates location; validates GPS coordinate bounds and paired presence."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # 1. Reject single coordinate (lat without lng)
    resp_single = client.post("/api/v1/provider/rentals/locations", json={
        "location_name": "Unawatuna Station",
        "district": "Galle",
        "city": "Unawatuna",
        "latitude": "6.010000",
    })
    assert resp_single.status_code == 422
    assert "Both latitude and longitude must be provided together" in resp_single.text

    # 2. Reject out-of-bounds coordinates (lat > 90)
    resp_bounds = client.post("/api/v1/provider/rentals/locations", json={
        "location_name": "Out of Bounds Station",
        "district": "Galle",
        "city": "Galle",
        "latitude": "95.000000",
        "longitude": "80.000000",
    })
    assert resp_bounds.status_code == 422

    # 3. Successful creation with valid coordinates
    payload_valid = {
        "location_name": "Hikkaduwa Surf Hub",
        "district": "Galle",
        "city": "Hikkaduwa",
        "address": "Galle Road, Hikkaduwa",
        "latitude": "6.139500",
        "longitude": "80.106300",
        "is_active": True,
    }
    resp_valid = client.post("/api/v1/provider/rentals/locations", json=payload_valid)
    assert resp_valid.status_code == 201
    data = resp_valid.json()
    assert data["location_name"] == "Hikkaduwa Surf Hub"
    assert data["provider_id"] == 101
    assert Decimal(str(data["latitude"])) == Decimal("6.139500")
    assert Decimal(str(data["longitude"])) == Decimal("80.106300")


@pytest.fixture
def hub_integrity_db(isolated_db_session, seed_test_data):
    db = isolated_db_session
    db.execute(text("PRAGMA foreign_keys=ON"))
    assert db.scalar(text("PRAGMA foreign_keys")) == 1
    try:
        yield db
    finally:
        db.rollback()
        db.execute(text("PRAGMA foreign_keys=OFF"))


def test_update_and_safe_delete_location(hub_integrity_db, seed_test_data):
    """SP-09: Provider updates location; deleting location with stationed vehicles soft-deactivates safely."""
    db = hub_integrity_db
    user = seed_test_data["prov_user1"]
    client = make_test_client(db, current_user=user)

    # 1. Update location 1
    patch_payload = {
        "address": "Updated 14 Church St, Galle Fort",
        "city": "Galle City",
    }
    resp_patch = client.patch("/api/v1/provider/rentals/locations/1", json=patch_payload)
    assert resp_patch.status_code == 200
    assert resp_patch.json()["address"] == "Updated 14 Church St, Galle Fort"
    assert resp_patch.json()["city"] == "Galle City"

    # 2. Location 1 is referenced by Vehicle 1 -> Delete should safely deactivate (is_active=False)
    resp_del_ref = client.delete("/api/v1/provider/rentals/locations/1")
    assert resp_del_ref.status_code == 200
    del_data = resp_del_ref.json()
    assert del_data["is_active"] is False
    assert "stationed there" in del_data["message"]

    # 3. Create an unreferenced location and delete permanently
    create_resp = client.post("/api/v1/provider/rentals/locations", json={
        "location_name": "Temporary Kiosk",
        "district": "Galle",
        "city": "Koggala",
    })
    temp_id = create_resp.json()["id"]

    resp_del_perm = client.delete(f"/api/v1/provider/rentals/locations/{temp_id}")
    assert resp_del_perm.status_code == 200
    assert "deleted successfully" in resp_del_perm.json()["message"]
    assert db.get(RentalLocation, temp_id) is None


@pytest.mark.parametrize(("pickup_id", "return_id"), [
    (3, None), (None, 3), (3, 3),
], ids=["pickup-only", "return-only", "both"])
def test_delete_booking_referenced_hub_preserves_history(hub_integrity_db, seed_test_data, pickup_id, return_id):
    db = hub_integrity_db
    db.add(RentalLocation(id=3, provider_id=101, location_name="Alternate hub", district="Galle", city="Galle"))
    db.flush()
    booking = seed_test_data["b1"]
    booking.pickup_location_id = pickup_id
    booking.return_location_id = return_id
    db.commit()
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])

    response = client.delete("/api/v1/provider/rentals/locations/3")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "message": "Location cannot be permanently deleted because existing bookings reference it. It has been deactivated instead.",
        "location_id": 3,
        "is_active": False,
    }
    db.refresh(booking)
    assert (booking.pickup_location_id, booking.return_location_id) == (pickup_id, return_id)
    assert db.get(RentalLocation, 3).is_active is False
    assert (booking.pickup_location.location_name if pickup_id else None) == ("Alternate hub" if pickup_id else None)
    assert (booking.return_location.location_name if return_id else None) == ("Alternate hub" if return_id else None)


@pytest.mark.parametrize("field", ["location_name", "district", "city", "is_active"])
def test_patch_location_rejects_explicit_null_without_mutation(hub_integrity_db, seed_test_data, field):
    db = hub_integrity_db
    location = seed_test_data["loc1"]
    previous = getattr(location, field)
    previous_address = location.address
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])

    response = client.patch("/api/v1/provider/rentals/locations/1", json={field: None, "address": "Changed"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["loc"] == ["body", field]
    db.refresh(location)
    assert getattr(location, field) == previous
    assert location.address == previous_address


def test_patch_location_preserves_omitted_fields_and_clears_nullable_fields(hub_integrity_db, seed_test_data):
    db = hub_integrity_db
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])
    path = "/api/v1/provider/rentals/locations/1"
    before = client.get(path).json()
    assert before["address"] is not None and before["latitude"] is not None and before["longitude"] is not None
    omitted = client.patch(path, json={})
    assert omitted.status_code == 200
    assert omitted.json() == before

    response = client.patch(path, json={"city": "Galle Fort", "address": None, "latitude": None, "longitude": None})
    assert response.status_code == 200, response.text
    assert response.json() == {**before, "city": "Galle Fort", "address": None, "latitude": None, "longitude": None}


def test_provider_cannot_update_or_delete_foreign_hub(hub_integrity_db, seed_test_data):
    db = hub_integrity_db
    client = make_test_client(db, current_user=seed_test_data["prov_user1"])
    path = "/api/v1/provider/rentals/locations/2"

    assert client.patch(path, json={"is_active": False}).status_code == 404
    assert client.delete(path).status_code == 404
    assert db.get(RentalLocation, 2).is_active is True


def test_public_locations_traveler_integration(isolated_db_session, seed_test_data):
    """SP-09: Active provider locations appear in public Traveler locations endpoint."""
    db = isolated_db_session
    client = make_test_client(db)

    resp = client.get("/api/v1/rental-locations")
    assert resp.status_code == 200
    locs = resp.json()
    names = [l["location_name"] for l in locs]
    assert "Galle Fort Main Station" in names
    assert "Mirissa Beach Station" in names
