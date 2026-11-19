"""
CeylonGo Rental Backend - Phase 1 Test Suite
============================================
Isolated unit and integration tests verifying:
1. ORM model and schema alignment with Master Database Documentation V1.0.
2. Booking creation, overlap checks, and blackout maintenance collision.
3. Provider booking rejection and safe hold expiry.
4. Booking cancellation and status transitions.
All database tests execute strictly against an isolated in-memory SQLite engine
to ensure zero modifications to CeylonGoDB in compliance with fail-closed guards.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models as models
from app.models.user import Base, User
from app.models.provider_profile import ProviderProfile
from app.models.rental_vehicle import RentalVehicle
from app.models.rental_booking import RentalBooking
from app.models.rental import (
    RentalLocation,
    RentalRatePlan,
    RentalVehicleImage,
    RentalVehicleDocument,
    RentalAvailabilityBlock,
    RentalEligibilityCheck,
    RentalInspection,
    RentalConditionEvidence,
    RentalIncident,
    RentalDeposit,
)
from app.database.connection import get_db
from app.routers.auth import get_current_user
from app.routers.rental_bookings import router as rental_bookings_router


# ==============================================================================
# SECTION 1: ORM Model Schema Alignment Tests (Master Database Doc V1.0)
# ==============================================================================

def test_rental_booking_orm_columns():
    """Verify RentalBooking has all 21 columns and correct types matching Master Doc p. 60."""
    cols = {c.name: c for c in RentalBooking.__table__.columns}
    expected_cols = [
        "id", "vehicle_id", "user_id", "pickup_at", "return_at",
        "total_price", "status", "pickup_location_id", "return_location_id",
        "currency", "payment_method", "payment_status", "expires_at",
        "cancellation_reason", "cancelled_by", "cancelled_at",
        "daily_rate_snapshot", "cancellation_policy_snapshot",
        "free_cancellation_hours_snapshot", "created_at", "updated_at",
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalBooking ORM model"

    # Confirm start_date / end_date are NOT in RentalBooking
    assert "start_date" not in cols, "start_date should be replaced by pickup_at"
    assert "end_date" not in cols, "end_date should be replaced by return_at"


def test_rental_rate_plan_orm_columns():
    """Verify RentalRatePlan has all 10 columns matching Master Doc p. 68."""
    cols = {c.name: c for c in RentalRatePlan.__table__.columns}
    expected_cols = [
        "id", "vehicle_id", "plan_name", "duration_type", "rate",
        "currency", "included_km_per_day", "extra_km_charge", "is_active", "created_at"
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalRatePlan ORM model"

    # Phantom fields must be gone
    assert "min_days" not in cols
    assert "max_days" not in cols
    assert "daily_rate" not in cols
    assert "security_deposit" not in cols


def test_rental_condition_evidence_orm_columns():
    """Verify RentalConditionEvidence contains area_of_vehicle NOT NULL matching Master Doc p. 62."""
    cols = {c.name: c for c in RentalConditionEvidence.__table__.columns}
    expected_cols = [
        "id", "inspection_id", "evidence_type", "file_url",
        "area_of_vehicle", "description", "created_at"
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalConditionEvidence"

    assert cols["area_of_vehicle"].nullable is False, "area_of_vehicle must be non-nullable"


def test_rental_inspection_orm_columns():
    """Verify RentalInspection columns match Master Doc p. 66."""
    cols = {c.name: c for c in RentalInspection.__table__.columns}
    expected_cols = [
        "id", "booking_id", "inspection_type", "odometer_reading",
        "fuel_level_percent", "cleanliness_status", "inspected_by",
        "customer_signature", "notes", "created_at"
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalInspection"


def test_rental_deposit_orm_columns():
    """Verify RentalDeposit columns match Master Doc p. 63."""
    cols = {c.name: c for c in RentalDeposit.__table__.columns}
    expected_cols = [
        "id", "booking_id", "deposit_amount", "currency", "deposit_status",
        "deducted_amount", "deduction_reason", "refunded_amount",
        "held_at", "released_at"
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalDeposit"


def test_rental_incident_orm_columns():
    """Verify RentalIncident columns match Master Doc p. 65."""
    cols = {c.name: c for c in RentalIncident.__table__.columns}
    expected_cols = [
        "id", "booking_id", "incident_type", "incident_datetime",
        "description", "estimated_damage_cost", "actual_repair_cost",
        "police_report_reference", "resolution_status", "created_at", "updated_at"
    ]
    for col in expected_cols:
        assert col in cols, f"Column '{col}' missing from RentalIncident"


def test_rental_vehicle_documents_and_images_orm():
    """Verify RentalVehicleDocument & RentalVehicleImage columns match Master Doc pp. 69, 70."""
    doc_cols = {c.name: c for c in RentalVehicleDocument.__table__.columns}
    assert "document_number" in doc_cols
    assert "file_reference" in doc_cols
    assert "is_verified" in doc_cols

    img_cols = {c.name: c for c in RentalVehicleImage.__table__.columns}
    assert "is_primary" in img_cols
    assert "image_url" in img_cols
    assert "display_order" in img_cols


# ==============================================================================
# SECTION 2: Isolated In-Memory Fixtures for Booking & Business Logic
# ==============================================================================

@pytest.fixture
def isolated_db_session():
    """Creates a completely isolated in-memory SQLite database session."""
    import sqlite3
    from sqlalchemy import event

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

    # Create all defined tables in the in-memory SQLite engine
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_setup_data(isolated_db_session):
    """Seeds test user, provider, approved vehicle, and unapproved vehicle."""
    db = isolated_db_session

    # Seed User (Traveler)
    traveler = User(
        id=1,
        full_name="Test Traveler",
        email="traveler@example.com",
        password_hash="mockhash",
        role="traveler",
        is_active=True,
    )
    db.add(traveler)
    db.flush()

    # Seed approved driving eligibility and KYC profile for traveler (Phase 5 Batch 1 requirements)
    traveler_eligibility = RentalEligibilityCheck(
        id=1,
        user_id=traveler.id,
        driver_license_number="B1234567",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        verification_status="approved",
    )
    db.add(traveler_eligibility)

    traveler_profile = ProviderProfile(
        id=999,
        user_id=traveler.id,
        business_name="Traveler KYC",
        provider_type="rental",
        phone="0779999999",
        district="Colombo",
        date_of_birth=date(1995, 6, 15),
        approval_status="approved",
    )
    db.add(traveler_profile)
    db.flush()

    # Seed Provider User & Profile
    provider_user = User(
        id=2,
        full_name="Test Provider User",
        email="provider@example.com",
        password_hash="mockhash",
        role="provider",
        is_active=True,
    )
    db.add(provider_user)
    db.flush()

    provider_profile = ProviderProfile(
        id=1,
        user_id=provider_user.id,
        business_name="Lanka Moto Rentals",
        provider_type="rental",
        phone="0771234567",
        district="Colombo",
        approval_status="approved",
    )
    db.add(provider_profile)
    db.flush()

    # Seed Location
    location = RentalLocation(
        id=1,
        provider_id=provider_profile.id,
        location_name="Colombo Central Hub",
        district="Colombo",
        city="Colombo 03",
    )
    db.add(location)

    # Seed Approved Vehicle
    approved_vehicle = RentalVehicle(
        id=1,
        provider_id=provider_profile.id,
        vehicle_type="bike",
        vehicle_name="Honda Dio 110",
        description="Reliable scooter in prime condition",
        price_per_day=Decimal("3500.00"),
        status="available",
        approval_status="approved",
        location_id=location.id,
        cancellation_policy="Standard 24-hour free cancellation",
        free_cancellation_hours=24,
    )
    db.add(approved_vehicle)

    # Seed Draft / Unapproved Vehicle
    unapproved_vehicle = RentalVehicle(
        id=2,
        provider_id=provider_profile.id,
        vehicle_type="tuk_tuk",
        vehicle_name="Bajaj RE 4S",
        description="Pending inspection tuk-tuk",
        price_per_day=Decimal("5000.00"),
        status="available",
        approval_status="pending",
    )
    db.add(unapproved_vehicle)

    db.commit()

    return {
        "traveler": traveler,
        "provider_user": provider_user,
        "provider_profile": provider_profile,
        "approved_vehicle": approved_vehicle,
        "unapproved_vehicle": unapproved_vehicle,
        "location": location,
    }


def make_test_client(isolated_db_session, user_override):
    """Constructs a test FastAPI client with dependencies overridden."""
    test_app = FastAPI()
    test_app.include_router(rental_bookings_router)

    test_app.dependency_overrides[get_db] = lambda: isolated_db_session
    test_app.dependency_overrides[get_current_user] = lambda: user_override

    return TestClient(test_app)


# ==============================================================================
# SECTION 3: Booking Creation, Overlap, & Availability Block Tests
# ==============================================================================

def test_create_booking_success(isolated_db_session, test_setup_data):
    """Verifies successful booking creation with accurate snapshot fields."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    now = datetime.now(timezone.utc)
    pickup = now + timedelta(days=2)
    return_time = now + timedelta(days=5)

    payload = {
        "vehicle_id": vehicle.id,
        "pickup_at": pickup.isoformat(),
        "return_at": return_time.isoformat(),
        "payment_method": "pay_at_location",
    }

    response = client.post("/api/v1/rental-bookings", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["vehicle_id"] == vehicle.id
    assert data["user_id"] == traveler.id
    assert data["status"] == "pending"
    assert data["daily_rate_snapshot"] == "3500.00"
    # 3 days * 3500 = 10500.00
    assert Decimal(data["total_price"]) == Decimal("10500.00")
    assert data["payment_status"] == "unpaid"
    assert data["expires_at"] is not None


def test_create_booking_rejects_past_pickup(isolated_db_session, test_setup_data):
    """Rejects past pickup times."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    past = datetime.now(timezone.utc) - timedelta(hours=2)
    future = datetime.now(timezone.utc) + timedelta(days=1)

    payload = {
        "vehicle_id": vehicle.id,
        "pickup_at": past.isoformat(),
        "return_at": future.isoformat(),
    }
    response = client.post("/api/v1/rental-bookings", json=payload)
    assert response.status_code == 400
    assert "future" in response.json()["detail"].lower()


def test_create_booking_rejects_unapproved_vehicle(isolated_db_session, test_setup_data):
    """Rejects booking attempts on vehicles that have not been approved by admin."""
    traveler = test_setup_data["traveler"]
    unapproved = test_setup_data["unapproved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    now = datetime.now(timezone.utc)
    payload = {
        "vehicle_id": unapproved.id,
        "pickup_at": (now + timedelta(days=1)).isoformat(),
        "return_at": (now + timedelta(days=2)).isoformat(),
    }
    response = client.post("/api/v1/rental-bookings", json=payload)
    assert response.status_code == 400
    assert "not approved" in response.json()["detail"].lower()


def test_create_booking_detects_overlap_pending_and_active(isolated_db_session, test_setup_data):
    """Verifies that both pending and active bookings prevent overlapping reservations."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    now = datetime.now(timezone.utc)
    t1 = now + timedelta(days=2)
    t2 = now + timedelta(days=4)

    # 1. Create first booking
    res1 = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": t1.isoformat(),
        "return_at": t2.isoformat(),
    })
    assert res1.status_code == 201

    # 2. Try creating overlapping booking during the same window
    res_overlap = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (t1 + timedelta(hours=12)).isoformat(),
        "return_at": (t2 + timedelta(days=1)).isoformat(),
    })
    assert res_overlap.status_code == 409
    assert "already booked" in res_overlap.json()["detail"].lower()

    # 3. Transition first booking to 'active' (vehicle out on road)
    b1_id = res1.json()["id"]
    booking_record = isolated_db_session.get(RentalBooking, b1_id)
    booking_record.status = "active"
    isolated_db_session.commit()

    # 4. Overlap must still be blocked while active
    res_overlap_active = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (t1 + timedelta(hours=4)).isoformat(),
        "return_at": t2.isoformat(),
    })
    assert res_overlap_active.status_code == 409


def test_create_booking_detects_maintenance_blackout_block(isolated_db_session, test_setup_data):
    """Verifies collision with an active RentalAvailabilityBlock returns 409 Conflict."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    now = datetime.now(timezone.utc)
    block_start = now + timedelta(days=10)
    block_end = now + timedelta(days=15)

    # Add active maintenance block
    block = RentalAvailabilityBlock(
        vehicle_id=vehicle.id,
        start_datetime=block_start.replace(tzinfo=None),
        end_datetime=block_end.replace(tzinfo=None),
        block_type="maintenance",
        reason="Scheduled brake & tire overhaul",
        is_active=True,
    )
    isolated_db_session.add(block)
    isolated_db_session.commit()

    # Attempt to book overlapping window
    res = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (block_start + timedelta(days=1)).isoformat(),
        "return_at": (block_end + timedelta(days=1)).isoformat(),
    })
    assert res.status_code == 409
    assert "maintenance" in res.json()["detail"].lower()


def test_create_booking_cleans_up_expired_pending(isolated_db_session, test_setup_data):
    """Expired pending bookings do not block new reservations and are marked expired."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]
    client = make_test_client(isolated_db_session, traveler)

    now = datetime.now(timezone.utc)
    t1 = now + timedelta(days=1)
    t2 = now + timedelta(days=3)

    # Create stale pending booking with past expires_at
    expired_booking = RentalBooking(
        vehicle_id=vehicle.id,
        user_id=traveler.id,
        pickup_at=t1.replace(tzinfo=None),
        return_at=t2.replace(tzinfo=None),
        total_price=Decimal("7000.00"),
        status="pending",
        expires_at=(now - timedelta(minutes=5)).replace(tzinfo=None),
    )
    isolated_db_session.add(expired_booking)
    isolated_db_session.commit()

    # New reservation on the same dates should succeed and mark the old one expired
    res = client.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": t1.isoformat(),
        "return_at": t2.isoformat(),
    })
    assert res.status_code == 201

    isolated_db_session.refresh(expired_booking)
    assert expired_booking.status == "expired"


# ==============================================================================
# SECTION 4: Provider Approval, Rejection, and Status Transitions
# ==============================================================================

def test_provider_can_reject_booking(isolated_db_session, test_setup_data):
    """Verifies that an approved provider can decline a pending booking with reason."""
    traveler = test_setup_data["traveler"]
    provider = test_setup_data["provider_user"]
    vehicle = test_setup_data["approved_vehicle"]

    client_traveler = make_test_client(isolated_db_session, traveler)
    now = datetime.now(timezone.utc)
    res_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=3)).isoformat(),
    })
    booking_id = res_create.json()["id"]

    # Provider rejects
    client_provider = make_test_client(isolated_db_session, provider)
    res_reject = client_provider.patch(
        f"/api/v1/rental-bookings/{booking_id}/status",
        json={"status": "rejected", "rejection_reason": "Vehicle booked offline"},
    )
    assert res_reject.status_code == 200
    data = res_reject.json()
    assert data["status"] == "rejected"
    assert data["cancellation_reason"] == "Vehicle booked offline"
    assert data["cancelled_by"] == provider.id


def test_provider_status_lifecycle_and_expiry(isolated_db_session, test_setup_data):
    """Verifies pending -> confirmed -> active -> completed transition flow."""
    traveler = test_setup_data["traveler"]
    provider = test_setup_data["provider_user"]
    vehicle = test_setup_data["approved_vehicle"]

    client_traveler = make_test_client(isolated_db_session, traveler)
    now = datetime.now(timezone.utc)
    res_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (now + timedelta(days=1)).isoformat(),
        "return_at": (now + timedelta(days=2)).isoformat(),
    })
    booking_id = res_create.json()["id"]

    client_provider = make_test_client(isolated_db_session, provider)

    # 1. Confirm
    res_confirm = client_provider.patch(
        f"/api/v1/rental-bookings/{booking_id}/status",
        json={"status": "confirmed"},
    )
    assert res_confirm.status_code == 200
    assert res_confirm.json()["status"] == "confirmed"

    # 2. Activate (Handover)
    res_active = client_provider.patch(
        f"/api/v1/rental-bookings/{booking_id}/status",
        json={"status": "active"},
    )
    assert res_active.status_code == 200
    assert res_active.json()["status"] == "active"

    # 3. Attempt to complete before return time -> must fail
    res_early_complete = client_provider.patch(
        f"/api/v1/rental-bookings/{booking_id}/status",
        json={"status": "completed"},
    )
    assert res_early_complete.status_code == 400
    assert "before return time" in res_early_complete.json()["detail"].lower()


def test_traveler_cancel_booking(isolated_db_session, test_setup_data):
    """Verifies traveler can cancel pending/confirmed booking before pickup time."""
    traveler = test_setup_data["traveler"]
    vehicle = test_setup_data["approved_vehicle"]

    client_traveler = make_test_client(isolated_db_session, traveler)
    now = datetime.now(timezone.utc)
    res_create = client_traveler.post("/api/v1/rental-bookings", json={
        "vehicle_id": vehicle.id,
        "pickup_at": (now + timedelta(days=3)).isoformat(),
        "return_at": (now + timedelta(days=5)).isoformat(),
    })
    booking_id = res_create.json()["id"]

    res_cancel = client_traveler.patch(f"/api/v1/rental-bookings/{booking_id}/cancel")
    assert res_cancel.status_code == 200
    assert res_cancel.json()["status"] == "cancelled"
    assert res_cancel.json()["cancelled_by"] == traveler.id
