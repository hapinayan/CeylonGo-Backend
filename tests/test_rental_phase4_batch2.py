"""
CeylonGo Rental Backend - Phase 4 Batch 2 Test Suite
====================================================
Automated isolated unit & API tests verifying Admin Portal requirements AD-05 through AD-08:
- AD-05: Rental Booking & Incident Monitoring (platform-wide booking list, deep booking inspection, lifecycle, inspections, incidents, resolution updates)
- AD-06: Payment, Settlement & Refund Monitoring (rental-specific payments, rental provider settlements, security deposits, audit-only refund review, duplicate review prevention)
- AD-07: Sponsored Promotion Management (campaign queue, approve/reject with reason, payment verification requirement for activation, unpaid promotion restrictions)
- AD-08: Rental Reports & Analytics (fleet inventory analytics, revenue & financial analytics, separated online vs cash, verified platform commission with zero hardcoded assumptions)
- Regression Protection: Strict admin RBAC, audit logging into dbo.admin_audit_logs, traveler & provider compatibility.

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
import sqlite3
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.models.provider_profile import ProviderProfile
from app.models.rental import (
    RentalConditionEvidence,
    RentalDeposit,
    RentalIncident,
    RentalInspection,
    RentalLocation,
    RentalRatePlan,
    RentalVehicleDocument,
    RentalVehicleImage,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    BookingStatusHistory,
    ListingReference,
    Notification,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
    ProviderSettlement,
)
from app.models.user import Base, User
from app.routers.admin_rentals import router as admin_rentals_router
from app.routers.auth import get_current_user
from app.routers.rental_providers import router as rental_providers_router
from app.routers.rental_vehicles import router as rental_vehicles_router


# ==============================================================================
# Isolated SQLite Database Fixture
# ==============================================================================

@pytest.fixture
def isolated_db_session(tmp_path, monkeypatch):
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

    test_docs_dir = str(tmp_path / "rental_docs")
    os.makedirs(test_docs_dir, exist_ok=True)
    monkeypatch.setenv("RENTAL_DOCUMENTS_DIR", test_docs_dir)

    session = TestingSessionLocal()
    try:
        yield session, test_docs_dir
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def make_test_client(db, current_user: User | None = None):
    app = FastAPI()
    app.include_router(admin_rentals_router)
    app.include_router(rental_providers_router)
    app.include_router(rental_vehicles_router)

    app.dependency_overrides[get_db] = lambda: db
    if current_user:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app)


# ==============================================================================
# Seed Test Data Fixture
# ==============================================================================

@pytest.fixture
def seed_test_data(isolated_db_session):
    db, docs_dir = isolated_db_session
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Users
    admin_user = User(id=1, full_name="Admin Officer", email="admin@ceylongo.lk", password_hash="pw", role="admin")
    prov_user = User(id=2, full_name="Kamal Perera", email="kamal@rentals.lk", password_hash="pw", role="provider")
    traveler_user = User(id=3, full_name="Alice Traveler", email="alice@traveler.com", password_hash="pw", role="traveler")
    traveler_user_2 = User(id=4, full_name="Bob Rider", email="bob@rider.com", password_hash="pw", role="traveler")
    db.add_all([admin_user, prov_user, traveler_user, traveler_user_2])
    db.flush()

    # 2. Provider Profile
    provider = ProviderProfile(
        id=201,
        user_id=prov_user.id,
        business_name="Kamal Galle Wheels",
        provider_type="rental",
        phone="+94771234567",
        district="Galle",
        approval_status="approved",
    )
    db.add(provider)
    db.flush()

    # 3. Rental Locations
    loc1 = RentalLocation(
        id=301,
        provider_id=provider.id,
        location_name="Galle Fort Station",
        address="12 Lighthouse Street, Galle",
        city="Galle",
        district="Galle",
        latitude=Decimal("6.0328"),
        longitude=Decimal("80.2170"),
        is_active=True,
    )
    loc2 = RentalLocation(
        id=302,
        provider_id=provider.id,
        location_name="Unawatuna Beach Hub",
        address="Beach Road, Unawatuna",
        city="Unawatuna",
        district="Galle",
        latitude=Decimal("6.0100"),
        longitude=Decimal("80.2480"),
        is_active=True,
    )
    db.add_all([loc1, loc2])
    db.flush()

    # 4. Rental Vehicles
    # v1: Motorbike (approved, available, verified)
    v1 = RentalVehicle(
        id=401,
        provider_id=provider.id,
        vehicle_name="Honda Dio 110",
        vehicle_type="bike",
        model_year=2023,
        license_plate="BCC-1234",
        seating_capacity=2,
        transmission="automatic",
        fuel_type="petrol",
        price_per_day=Decimal("3500.00"),
        location_id=loc1.id,
        district="Galle",
        city="Galle",
        status="available",
        approval_status="approved",
        verification_status="verified",
    )
    # v2: Tuk-Tuk (approved, active/rented, verified)
    v2 = RentalVehicle(
        id=402,
        provider_id=provider.id,
        vehicle_name="Bajaj RE 4S Tuk-Tuk",
        vehicle_type="tuk_tuk",
        model_year=2022,
        license_plate="AB-5678",
        seating_capacity=3,
        transmission="manual",
        fuel_type="petrol",
        price_per_day=Decimal("5000.00"),
        location_id=loc1.id,
        district="Galle",
        city="Galle",
        status="available",
        approval_status="approved",
        verification_status="verified",
    )
    # v3: Mountain Bicycle (approved, maintenance, unverified)
    v3 = RentalVehicle(
        id=403,
        provider_id=provider.id,
        vehicle_name="Trek Marlin 5 Mountain Bike",
        vehicle_type="bicycle",
        model_year=2024,
        license_plate=None,
        seating_capacity=1,
        transmission="manual",
        fuel_type="other",
        price_per_day=Decimal("1500.00"),
        location_id=loc2.id,
        district="Galle",
        city="Unawatuna",
        status="maintenance",
        approval_status="approved",
        verification_status="unverified",
    )
    # v4: Suspended Bike
    v4 = RentalVehicle(
        id=404,
        provider_id=provider.id,
        vehicle_name="Yamaha FZ 150",
        vehicle_type="bike",
        model_year=2021,
        license_plate="WP-9988",
        seating_capacity=2,
        transmission="manual",
        fuel_type="petrol",
        price_per_day=Decimal("4000.00"),
        location_id=loc1.id,
        district="Galle",
        city="Galle",
        status="available",
        approval_status="suspended",
        verification_status="verified",
    )
    # v5: Pending Approval Tuk-Tuk
    v5 = RentalVehicle(
        id=405,
        provider_id=provider.id,
        vehicle_name="Piaggio Ape City Tuk-Tuk",
        vehicle_type="tuk_tuk",
        model_year=2023,
        license_plate="SP-4433",
        seating_capacity=3,
        transmission="manual",
        fuel_type="petrol",
        price_per_day=Decimal("4800.00"),
        location_id=loc1.id,
        district="Galle",
        city="Galle",
        status="available",
        approval_status="pending",
        verification_status="unverified",
    )
    db.add_all([v1, v2, v3, v4, v5])
    db.flush()

    # 5. Bookings
    # b1: Completed rental booking with online payment
    b1 = RentalBooking(
        id=501,
        vehicle_id=v1.id,
        user_id=traveler_user.id,
        pickup_at=now - timedelta(days=5),
        return_at=now - timedelta(days=3),
        total_price=Decimal("7000.00"),
        status="completed",
        currency="LKR",
        payment_method="payhere",
        payment_status="paid",
        pickup_location_id=loc1.id,
        return_location_id=loc1.id,
        daily_rate_snapshot=Decimal("3500.00"),
        free_cancellation_hours_snapshot=24,
    )
    # b2: Active booking right now (tuk-tuk v2) with cash payment
    b2 = RentalBooking(
        id=502,
        vehicle_id=v2.id,
        user_id=traveler_user_2.id,
        pickup_at=now - timedelta(hours=2),
        return_at=now + timedelta(days=2),
        total_price=Decimal("15000.00"),
        status="active",
        currency="LKR",
        payment_method="pay_at_location",
        payment_status="paid",
        pickup_location_id=loc1.id,
        return_location_id=loc2.id,
        daily_rate_snapshot=Decimal("5000.00"),
        free_cancellation_hours_snapshot=24,
    )
    # b3: Cancelled booking with pending refund request
    b3 = RentalBooking(
        id=503,
        vehicle_id=v1.id,
        user_id=traveler_user.id,
        pickup_at=now + timedelta(days=3),
        return_at=now + timedelta(days=5),
        total_price=Decimal("7000.00"),
        status="cancelled",
        currency="LKR",
        payment_method="payhere",
        payment_status="refunded",
        cancellation_reason="Traveler itinerary change",
        cancelled_by=traveler_user.id,
        cancelled_at=now - timedelta(hours=1),
        daily_rate_snapshot=Decimal("3500.00"),
    )
    db.add_all([b1, b2, b3])
    db.flush()

    # 6. Central Booking References
    br1 = BookingReference(
        id=601,
        booking_reference="RB-000501",
        module_type="rental",
        target_booking_id=b1.id,
        user_id=traveler_user.id,
        provider_profile_id=provider.id,
        total_price=b1.total_price,
        currency="LKR",
        booking_status="completed",
        payment_status="paid",
    )
    br2 = BookingReference(
        id=602,
        booking_reference="RB-000502",
        module_type="rental",
        target_booking_id=b2.id,
        user_id=traveler_user_2.id,
        provider_profile_id=provider.id,
        total_price=b2.total_price,
        currency="LKR",
        booking_status="active",
        payment_status="paid",
    )
    br3 = BookingReference(
        id=603,
        booking_reference="RB-000503",
        module_type="rental",
        target_booking_id=b3.id,
        user_id=traveler_user.id,
        provider_profile_id=provider.id,
        total_price=b3.total_price,
        currency="LKR",
        booking_status="cancelled",
        payment_status="refunded",
    )
    db.add_all([br1, br2, br3])
    db.flush()

    # 7. Payment Transactions
    # tx1: Online card payment for b1
    tx1 = PaymentTransaction(
        id=701,
        booking_reference_id=br1.id,
        user_id=traveler_user.id,
        gateway_name="payhere",
        gateway_transaction_id="PH-1001",
        amount=Decimal("7000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="card",
        paid_at=now - timedelta(days=5),
    )
    # tx2: Cash collected on pickup for b2
    tx2 = PaymentTransaction(
        id=702,
        booking_reference_id=br2.id,
        user_id=traveler_user_2.id,
        gateway_name="cash_on_pickup",
        gateway_transaction_id="CASH-2001",
        amount=Decimal("15000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="cash",
        paid_at=now - timedelta(hours=2),
    )
    # tx3: Online card payment for b3 (subject to refund)
    tx3 = PaymentTransaction(
        id=703,
        booking_reference_id=br3.id,
        user_id=traveler_user.id,
        gateway_name="payhere",
        gateway_transaction_id="PH-1003",
        amount=Decimal("7000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="card",
        paid_at=now - timedelta(days=1),
    )
    db.add_all([tx1, tx2, tx3])
    db.flush()

    # 8. Payment Refund Record (Pending audit review)
    rf1 = PaymentRefund(
        id=801,
        payment_transaction_id=tx3.id,
        amount=Decimal("7000.00"),
        currency="LKR",
        refund_reason="Free cancellation within policy window",
        refund_status="pending",
    )
    db.add(rf1)
    db.flush()

    # 9. Inspections & Condition Evidence for b1
    insp1 = RentalInspection(
        id=901,
        booking_id=b1.id,
        inspection_type="pickup",
        odometer_reading=12500,
        fuel_level_percent=100,
        cleanliness_status="clean",
        customer_signature="sig_pickup_alice",
        notes="Pre-rental handover complete",
    )
    insp2 = RentalInspection(
        id=902,
        booking_id=b1.id,
        inspection_type="return",
        odometer_reading=12680,
        fuel_level_percent=95,
        cleanliness_status="clean",
        customer_signature="sig_return_alice",
        notes="Normal wear, return complete",
    )
    db.add_all([insp1, insp2])
    db.flush()

    ev1 = RentalConditionEvidence(
        id=951,
        inspection_id=insp1.id,
        evidence_type="photo",
        file_url="/uploads/rentals/handover_front.jpg",
        area_of_vehicle="front",
        description="Pristine headlight and bumper",
    )
    db.add(ev1)
    db.flush()

    # 10. Incidents
    inc1 = RentalIncident(
        id=1001,
        booking_id=b1.id,
        incident_type="scratch",
        incident_datetime=now - timedelta(days=4),
        description="Minor scrape on left fairing during parking",
        estimated_damage_cost=Decimal("3500.00"),
        actual_repair_cost=None,
        police_report_reference=None,
        resolution_status="reported",
    )
    db.add(inc1)
    db.flush()

    # 11. Security Deposit
    dep1 = RentalDeposit(
        id=1101,
        booking_id=b1.id,
        deposit_amount=Decimal("10000.00"),
        currency="LKR",
        deposit_status="held",
        deducted_amount=Decimal("0.00"),
        refunded_amount=Decimal("0.00"),
        held_at=now - timedelta(days=5),
    )
    db.add(dep1)
    db.flush()

    # 12. Provider Settlement Record
    settle1 = ProviderSettlement(
        id=1201,
        provider_profile_id=provider.id,
        period_start=date.today() - timedelta(days=30),
        period_end=date.today() - timedelta(days=1),
        gross_amount=Decimal("45000.00"),
        platform_fee=Decimal("4500.00"),
        net_payable=Decimal("40500.00"),
        currency="LKR",
        settlement_status="completed",
        payout_reference="SLIP-998811",
        paid_at=now - timedelta(days=2),
    )
    settle2 = ProviderSettlement(
        id=1202,
        provider_profile_id=provider.id,
        period_start=date.today(),
        period_end=date.today() + timedelta(days=14),
        gross_amount=Decimal("22000.00"),
        platform_fee=Decimal("2200.00"),
        net_payable=Decimal("19800.00"),
        currency="LKR",
        settlement_status="pending",
    )
    db.add_all([settle1, settle2])
    db.flush()

    # 13. Promotion Campaigns
    camp1 = PromotionCampaign(
        id=1301,
        provider_profile_id=provider.id,
        campaign_name="Galle Season Boost",
        target_module="rental",
        budget=Decimal("4500.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now,
        ends_at=now + timedelta(days=14),
    )
    camp2 = PromotionCampaign(
        id=1302,
        provider_profile_id=provider.id,
        campaign_name="Active Tuk-Tuk Special",
        target_module="rental",
        budget=Decimal("8000.00"),
        spent_amount=Decimal("2500.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=3),
        ends_at=now + timedelta(days=27),
    )
    db.add_all([camp1, camp2])
    db.flush()

    # 14. Promotion Events
    pe1 = PromotionEvent(
        id=1401,
        campaign_id=camp2.id,
        event_type="impression",
        ip_address="192.168.1.1",
    )
    pe2 = PromotionEvent(
        id=1402,
        campaign_id=camp2.id,
        event_type="click",
        ip_address="192.168.1.2",
    )
    db.add_all([pe1, pe2])

    # 15. Booking Status History
    bsh1 = BookingStatusHistory(
        id=1501,
        booking_reference_id=br1.id,
        from_status="pending",
        to_status="confirmed",
        changed_by=prov_user.id,
        notes="Provider confirmed booking",
    )
    bsh2 = BookingStatusHistory(
        id=1502,
        booking_reference_id=br1.id,
        from_status="confirmed",
        to_status="completed",
        changed_by=prov_user.id,
        notes="Vehicle returned in good order",
    )
    db.add_all([bsh1, bsh2])

    db.commit()

    return {
        "admin_user": admin_user,
        "prov_user": prov_user,
        "traveler_user": traveler_user,
        "provider": provider,
        "vehicles": [v1, v2, v3, v4, v5],
        "bookings": [b1, b2, b3],
        "transactions": [tx1, tx2, tx3],
        "refund": rf1,
        "incident": inc1,
        "deposit": dep1,
        "settlements": [settle1, settle2],
        "campaigns": [camp1, camp2],
    }


# ==============================================================================
# TESTS
# ==============================================================================

def test_admin_authorization_guards_batch2(seed_test_data, isolated_db_session):
    """Verifies that non-admin users (travelers, providers, unauthenticated) are blocked across Batch 2 endpoints."""
    db, _ = isolated_db_session
    traveler_client = make_test_client(db, current_user=seed_test_data["traveler_user"])
    provider_client = make_test_client(db, current_user=seed_test_data["prov_user"])
    anon_client = make_test_client(db, current_user=None)

    endpoints = [
        ("GET", "/api/v1/admin/rentals/bookings"),
        ("GET", f"/api/v1/admin/rentals/bookings/{seed_test_data['bookings'][0].id}"),
        ("GET", "/api/v1/admin/rentals/incidents"),
        ("PATCH", f"/api/v1/admin/rentals/incidents/{seed_test_data['incident'].id}", {"resolution_status": "under_investigation"}),
        ("GET", "/api/v1/admin/rentals/payments"),
        ("GET", "/api/v1/admin/rentals/settlements"),
        ("GET", "/api/v1/admin/rentals/deposits"),
        ("GET", "/api/v1/admin/rentals/refunds"),
        ("POST", f"/api/v1/admin/rentals/refunds/{seed_test_data['refund'].id}/review", {"decision": "approved"}),
        ("GET", "/api/v1/admin/rentals/promotions"),
        ("POST", f"/api/v1/admin/rentals/promotions/{seed_test_data['campaigns'][0].id}/review", {"decision": "approve"}),
        ("GET", "/api/v1/admin/rentals/reports/fleet-summary"),
        ("GET", "/api/v1/admin/rentals/reports/revenue-analytics"),
    ]

    for item in endpoints:
        method = item[0]
        url = item[1]
        payload = item[2] if len(item) > 2 else None

        # Anonymous -> 401
        res_anon = anon_client.request(method, url, json=payload)
        assert res_anon.status_code == 401, f"Expected 401 for anon on {url}, got {res_anon.status_code}"

        # Traveler -> 403
        res_trav = traveler_client.request(method, url, json=payload)
        assert res_trav.status_code == 403, f"Expected 403 for traveler on {url}, got {res_trav.status_code}"

        # Provider -> 403
        res_prov = provider_client.request(method, url, json=payload)
        assert res_prov.status_code == 403, f"Expected 403 for provider on {url}, got {res_prov.status_code}"


def test_admin_rental_booking_monitoring_and_filters(seed_test_data, isolated_db_session):
    """Verifies platform-wide rental booking monitoring with pagination and filters."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Platform-wide list returns all 3 bookings
    res_all = client.get("/api/v1/admin/rentals/bookings")
    assert res_all.status_code == 200, res_all.text
    data_all = res_all.json()
    assert data_all["total"] == 3
    assert len(data_all["items"]) == 3

    # 2. Filter by status: 'active'
    res_active = client.get("/api/v1/admin/rentals/bookings?status=active")
    assert res_active.status_code == 200
    data_active = res_active.json()
    assert data_active["total"] == 1
    assert data_active["items"][0]["status"] == "active"
    assert data_active["items"][0]["booking_reference"] == "RB-000502"

    # 3. Filter by provider_id
    res_prov = client.get(f"/api/v1/admin/rentals/bookings?provider_id={seed_test_data['provider'].id}")
    assert res_prov.status_code == 200
    assert res_prov.json()["total"] == 3

    # 4. Search query
    res_search = client.get("/api/v1/admin/rentals/bookings?search=Honda")
    assert res_search.status_code == 200
    assert res_search.json()["total"] == 2  # b1 and b3 use Honda Dio


def test_admin_rental_booking_detailed_inspection(seed_test_data, isolated_db_session):
    """Verifies detailed booking inspection with lifecycle history, inspections, incidents and deposits."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    b1_id = seed_test_data["bookings"][0].id
    res = client.get(f"/api/v1/admin/rentals/bookings/{b1_id}")
    assert res.status_code == 200, res.text
    data = res.json()

    # Core booking fields
    assert data["booking"]["id"] == b1_id
    assert data["booking"]["customer_name"] == "Alice Traveler"
    assert data["booking"]["provider_business_name"] == "Kamal Galle Wheels"
    assert data["booking"]["vehicle_name"] == "Honda Dio 110"
    assert Decimal(str(data["booking"]["total_price"])) == Decimal("7000.00")
    assert data["pickup_location"] == "Galle Fort Station"

    # Inspections verified with evidence
    assert len(data["inspections"]) == 2
    assert data["inspections"][0]["inspection_type"] == "pickup"
    assert data["inspections"][0]["evidence_count"] == 1

    # Incidents verified
    assert len(data["incidents"]) == 1
    assert data["incidents"][0]["incident_type"] == "scratch"
    assert Decimal(str(data["incidents"][0]["estimated_damage_cost"])) == Decimal("3500.00")

    # Deposits verified
    assert len(data["deposits"]) == 1
    assert Decimal(str(data["deposits"][0]["deposit_amount"])) == Decimal("10000.00")

    # Status history verified
    assert len(data["status_history"]) == 2
    assert data["status_history"][0]["to_status"] == "confirmed"

    # Payments breakdown verified
    assert Decimal(str(data["paid_online_amount"])) == Decimal("7000.00")
    assert Decimal(str(data["cash_collected_amount"])) == Decimal("0.00")

    # 404 for non-existent booking
    res_404 = client.get("/api/v1/admin/rentals/bookings/99999")
    assert res_404.status_code == 404


def test_admin_rental_incident_monitoring_and_status_update(seed_test_data, isolated_db_session):
    """Verifies incident monitoring, detail view, and status updates with audit logging."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. List incidents
    res_list = client.get("/api/v1/admin/rentals/incidents")
    assert res_list.status_code == 200
    assert res_list.json()["total"] == 1
    assert res_list.json()["items"][0]["incident_type"] == "scratch"

    # 2. Get incident detail
    inc_id = seed_test_data["incident"].id
    res_det = client.get(f"/api/v1/admin/rentals/incidents/{inc_id}")
    assert res_det.status_code == 200
    assert res_det.json()["id"] == inc_id

    # 3. Invalid status rejected
    res_invalid = client.patch(
        f"/api/v1/admin/rentals/incidents/{inc_id}",
        json={"resolution_status": "non_existent_status"},
    )
    assert res_invalid.status_code == 400

    # 4. Update incident status to 'under_investigation' with actual repair cost
    res_update = client.patch(
        f"/api/v1/admin/rentals/incidents/{inc_id}",
        json={
            "resolution_status": "under_investigation",
            "actual_repair_cost": 3200.00,
            "police_report_reference": "POL-GL-2026-99",
            "admin_notes": "Assessed by authorized repair shop",
        },
    )
    assert res_update.status_code == 200
    up_data = res_update.json()
    assert up_data["new_status"] == "under_investigation"
    assert Decimal(str(up_data["actual_repair_cost"])) == Decimal("3200.00")
    assert up_data["police_report_reference"] == "POL-GL-2026-99"

    # 5. Verify audit log entry
    audit = (
        db.query(AdminAuditLog)
        .filter(
            AdminAuditLog.action == "update_rental_incident",
            AdminAuditLog.entity_id == inc_id,
        )
        .first()
    )
    assert audit is not None
    assert "under_investigation" in audit.details


def test_admin_rental_payments_monitoring(seed_test_data, isolated_db_session):
    """Verifies rental payments monitoring strictly scoped to rental module."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    res = client.get("/api/v1/admin/rentals/payments")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] == 3
    assert len(data["items"]) == 3

    # Check that both online (payhere) and cash transactions appear
    methods = {item["payment_method"] for item in data["items"]}
    assert "card" in methods
    assert "cash" in methods

    # Filter by payment_method=cash
    res_cash = client.get("/api/v1/admin/rentals/payments?payment_method=cash")
    assert res_cash.status_code == 200
    assert res_cash.json()["total"] == 1
    assert Decimal(str(res_cash.json()["items"][0]["amount"])) == Decimal("15000.00")


def test_admin_rental_settlements_and_deposits_monitoring(seed_test_data, isolated_db_session):
    """Verifies settlements monitoring and security deposit monitoring."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. Settlements
    res_s = client.get("/api/v1/admin/rentals/settlements")
    assert res_s.status_code == 200
    data_s = res_s.json()
    assert data_s["total"] == 2
    statuses = {item["settlement_status"] for item in data_s["items"]}
    assert "completed" in statuses
    assert "pending" in statuses

    # 2. Deposits
    res_d = client.get("/api/v1/admin/rentals/deposits")
    assert res_d.status_code == 200
    data_d = res_d.json()
    assert data_d["total"] == 1
    assert Decimal(str(data_d["items"][0]["deposit_amount"])) == Decimal("10000.00")
    assert data_d["items"][0]["deposit_status"] == "held"


def test_admin_rental_refund_audit_review_lifecycle(seed_test_data, isolated_db_session):
    """Verifies audit-only refund review, duplicate review prevention, traveler notification, and no real payouts."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. List refunds
    res_list = client.get("/api/v1/admin/rentals/refunds")
    assert res_list.status_code == 200
    assert res_list.json()["total"] == 1
    assert res_list.json()["items"][0]["refund_status"] == "pending"

    rf_id = seed_test_data["refund"].id

    # 2. Admin approves refund (audit-only)
    res_app = client.post(
        f"/api/v1/admin/rentals/refunds/{rf_id}/review",
        json={"decision": "approved", "admin_notes": "Valid cancellation within policy."},
    )
    assert res_app.status_code == 200, res_app.text
    data_app = res_app.json()
    assert data_app["new_status"] == "approved"
    assert data_app["processed_by"] == seed_test_data["admin_user"].id

    # 3. Duplicate review fails (cannot re-process already decided refund)
    res_dup = client.post(
        f"/api/v1/admin/rentals/refunds/{rf_id}/review",
        json={"decision": "rejected", "admin_notes": "Trying duplicate review."},
    )
    assert res_dup.status_code == 400
    assert "already been processed" in res_dup.json()["detail"]

    # 4. Verify audit log entry
    audit = (
        db.query(AdminAuditLog)
        .filter(
            AdminAuditLog.action == "approved_rental_refund",
            AdminAuditLog.entity_id == rf_id,
        )
        .first()
    )
    assert audit is not None
    assert "approved" in audit.details

    # 5. Verify notification sent to traveler
    notif = (
        db.query(Notification)
        .filter(
            Notification.user_id == seed_test_data["traveler_user"].id,
            Notification.notification_type == "refund_review",
        )
        .first()
    )
    assert notif is not None
    assert "Approved" in notif.title


def test_admin_rental_promotion_moderation_approval_and_rejection(seed_test_data, isolated_db_session):
    """Verifies promotion queue, approval, and rejection requiring reason."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    # 1. List promotions
    res_p = client.get("/api/v1/admin/rentals/promotions")
    assert res_p.status_code == 200
    assert res_p.json()["total"] == 2

    # 2. Rejection without reason fails
    camp1_id = seed_test_data["campaigns"][0].id
    res_rej_fail = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "reject"},
    )
    assert res_rej_fail.status_code == 400
    assert "Rejection reason is mandatory" in res_rej_fail.json()["detail"]

    # 3. Rejection with reason succeeds
    res_rej = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "reject", "reason": "Low budget threshold for 14-day tier."},
    )
    assert res_rej.status_code == 200
    assert res_rej.json()["new_status"] == "rejected"

    # 4. Approve second campaign (or re-approve)
    res_app = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "approve"},
    )
    assert res_app.status_code == 200
    assert res_app.json()["new_status"] == "approved"


def test_admin_rental_promotion_activation_payment_verification_required(seed_test_data, isolated_db_session):
    """Verifies that activating a campaign requires approval first and valid payment verification."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])
    camp1_id = seed_test_data["campaigns"][0].id

    # Reset camp1 status to pending_approval
    camp1 = db.query(PromotionCampaign).filter(PromotionCampaign.id == camp1_id).first()
    camp1.status = "pending_approval"
    db.commit()

    # 1. Activating while unapproved fails
    res_act_unapp = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "activate", "payment_reference": "TXN-9988"},
    )
    assert res_act_unapp.status_code == 400
    assert "must be approved first" in res_act_unapp.json()["detail"]

    # 2. Approve campaign first
    res_app = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "approve"},
    )
    assert res_app.status_code == 200

    # 3. Activating without payment verification fails
    res_act_nopay = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "activate"},
    )
    assert res_act_nopay.status_code == 400
    assert "payment verification required" in res_act_nopay.json()["detail"]

    # 4. Activating with valid payment reference succeeds
    res_act_ok = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "activate", "payment_reference": "PAYHERE-PR-88991"},
    )
    assert res_act_ok.status_code == 200
    assert res_act_ok.json()["new_status"] == "active"
    assert res_act_ok.json()["is_paid"] is True

    # 5. Pause active campaign
    res_pause = client.post(
        f"/api/v1/admin/rentals/promotions/{camp1_id}/review",
        json={"decision": "pause"},
    )
    assert res_pause.status_code == 200
    assert res_pause.json()["new_status"] == "paused"


def test_admin_rental_fleet_summary_analytics_correctness(seed_test_data, isolated_db_session):
    """Verifies fleet summary analytics correctness across statuses, categories and districts."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    res = client.get("/api/v1/admin/rentals/reports/fleet-summary")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["total_vehicles"] == 5
    assert data["approved_vehicles"] == 3    # v1, v2, v3
    assert data["suspended_vehicles"] == 1   # v4
    assert data["pending_approval_vehicles"] == 1  # v5
    assert data["available_vehicles"] == 4   # v1, v2, v4, v5
    assert data["maintenance_vehicles"] == 1 # v3

    # Currently rented: v2 is in active booking b2
    assert data["currently_rented_vehicles"] == 1

    # Category distribution
    cat_dist = data["category_distribution"]
    assert cat_dist["bike"] == 2
    assert cat_dist["tuk_tuk"] == 2
    assert cat_dist["bicycle"] == 1

    # District distribution
    assert data["district_distribution"]["Galle"] == 5


def test_admin_rental_revenue_analytics_correctness(seed_test_data, isolated_db_session):
    """Verifies revenue analytics correctness using verified records (no invented rates)."""
    db, _ = isolated_db_session
    client = make_test_client(db, current_user=seed_test_data["admin_user"])

    res = client.get("/api/v1/admin/rentals/reports/revenue-analytics")
    assert res.status_code == 200, res.text
    data = res.json()

    assert data["total_bookings"] == 3
    assert data["completed_bookings"] == 1
    assert data["cancelled_bookings"] == 1

    # Gross booking value: excludes cancelled booking b3 (7000.00). Includes b1 (7000) and b2 (15000) = 22000.00
    assert Decimal(str(data["gross_booking_value"])) == Decimal("22000.00")

    # Online payments: tx1 (7000) + tx3 (7000) = 14000.00
    assert Decimal(str(data["confirmed_online_payments"])) == Decimal("14000.00")

    # Cash collected: tx2 = 15000.00
    assert Decimal(str(data["cash_collected"])) == Decimal("15000.00")

    # Total collected: 14000 + 15000 = 29000.00
    assert Decimal(str(data["total_collected_revenue"])) == Decimal("29000.00")

    # Platform commission: exact sum of platform_fee from settlements: 4500 + 2200 = 6700.00
    assert Decimal(str(data["platform_commission"])) == Decimal("6700.00")

    # Completed payouts: settle1 net_payable = 40500.00
    assert Decimal(str(data["completed_settlement_payouts"])) == Decimal("40500.00")

    # Pending settlements: settle2 net_payable = 19800.00
    assert Decimal(str(data["pending_settlements"])) == Decimal("19800.00")

    # Security deposits held: dep1 = 10000.00
    assert Decimal(str(data["security_deposits_held"])) == Decimal("10000.00")


def test_traveler_and_provider_compatibility_preserved(seed_test_data, isolated_db_session):
    """Verifies that public discovery and provider portal continue working seamlessly with new admin endpoints."""
    db, _ = isolated_db_session
    traveler_client = make_test_client(db, current_user=seed_test_data["traveler_user"])
    provider_client = make_test_client(db, current_user=seed_test_data["prov_user"])

    # 1. Traveler discovery works (returns only approved and available vehicles)
    res_disc = traveler_client.get("/api/v1/rental-vehicles")
    assert res_disc.status_code == 200
    items = res_disc.json()["items"]
    # v1 (approved, available) and v2 (approved, available)
    assert len(items) == 2
    for item in items:
        assert item["status"] == "available"

    # 2. Provider fleet list works
    res_fleet = provider_client.get("/api/v1/provider/rentals/vehicles")
    assert res_fleet.status_code == 200
    assert res_fleet.json()["total"] == 5
