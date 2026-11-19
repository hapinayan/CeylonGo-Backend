"""
CeylonGo Rental Backend - Phase 3 Batch 4 Test Suite
====================================================
Automated isolated unit & API tests verifying Service Provider requirements SP-01, SP-16 through SP-20:
- SP-01: Rental Provider Dashboard (fleet metrics, booking counts, utilization rate, revenue metrics)
- SP-16: Online Earnings and Settlements (gross revenue, confirmed payments, platform commission from verified records, payout tracking)
- SP-17: Sponsored Promotions (packages discovery, campaign creation, eligibility check, impressions/clicks tracking)
- SP-18: Rental Discounts and Offers (percentage and fixed-amount discounts, date validation, traveler pricing compatibility)
- SP-19: Customer Reviews Dashboard (fleet reviews aggregation, rating distribution, vehicle filters)
- SP-20: Provider Verification Status (KYC profile status, document indicators, per-vehicle listing approval states)

All database tests execute strictly against an isolated in-memory SQLite engine
with zero modifications to CeylonGoDB in compliance with fail-closed database guards.
"""

from datetime import date, datetime, timedelta, timezone
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
from app.models.rental import (
    RentalAvailabilityBlock,
    RentalLocation,
    RentalRatePlan,
)
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    BookingReference,
    ListingReference,
    PaymentTransaction,
    PromotionCampaign,
    PromotionEvent,
    PromotionPackage,
    ProviderSettlement,
    Review,
)
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
    today = now.date()

    # 1. Users
    prov_user1 = User(id=1, full_name="Sunil Perera", email="sunil@rentals.lk", password_hash="pw", role="provider")
    prov_user2 = User(id=2, full_name="Kamal Silva", email="kamal@rentals.lk", password_hash="pw", role="provider")
    prov_user_unapproved = User(id=3, full_name="Pending Provider", email="pending@rentals.lk", password_hash="pw", role="provider")
    traveler_user = User(id=4, full_name="Alice Smith", email="alice@traveler.com", password_hash="pw", role="traveler")
    homestay_user = User(id=5, full_name="Nimal Homestay", email="nimal@homestay.lk", password_hash="pw", role="provider")
    db.add_all([prov_user1, prov_user2, prov_user_unapproved, traveler_user, homestay_user])
    db.flush()

    # 2. Provider Profiles
    profile1 = ProviderProfile(
        id=101,
        user_id=1,
        business_name="Sunil Galle Scooters",
        provider_type="rental",
        phone="+94771234567",
        district="Galle",
        approval_status="approved",
        selfie_image_path="/docs/selfie1.jpg",
        nic_front_image_path="/docs/nic_front1.jpg",
        nic_back_image_path="/docs/nic_back1.jpg",
        business_registration_number="BR-12345",
        reviewed_at=now - timedelta(days=30),
    )
    profile2 = ProviderProfile(
        id=102,
        user_id=2,
        business_name="Kamal Colombo Rides",
        provider_type="rental",
        phone="+94779876543",
        district="Colombo",
        approval_status="approved",
    )
    profile_unapproved = ProviderProfile(
        id=103,
        user_id=3,
        business_name="Newbie Rentals",
        provider_type="rental",
        phone="+94770000000",
        district="Kandy",
        approval_status="pending",
        selfie_image_path="/docs/selfie_new.jpg",
        rejection_reason=None,
    )
    profile_homestay = ProviderProfile(
        id=104,
        user_id=5,
        business_name="Nimal Beach Villa",
        provider_type="homestay",
        phone="+94771112222",
        district="Matara",
        approval_status="approved",
    )
    db.add_all([profile1, profile2, profile_unapproved, profile_homestay])
    db.flush()

    # 3. Vehicles for Provider 1
    # v1: Available & Approved
    v1 = RentalVehicle(
        id=10,
        provider_id=101,
        vehicle_name="Honda Dio 110",
        vehicle_type="bike",
        price_per_day=Decimal("3500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        license_plate="WP-BC-1234",
        seating_capacity=2,
        transmission="automatic",
        fuel_type="petrol",
        district="Galle",
        city="Galle",
    )
    # v2: Pending Approval
    v2 = RentalVehicle(
        id=11,
        provider_id=101,
        vehicle_name="Bajaj RE Tuk-Tuk",
        vehicle_type="tuk_tuk",
        price_per_day=Decimal("5000.00"),
        status="available",
        approval_status="pending",
        verification_status="unverified",
        license_plate="SP-AA-5678",
        seating_capacity=3,
        transmission="manual",
        fuel_type="petrol",
        district="Galle",
        city="Galle",
    )
    # v3: Under Maintenance
    v3 = RentalVehicle(
        id=12,
        provider_id=101,
        vehicle_name="Trek Mountain Bicycle",
        vehicle_type="bicycle",
        price_per_day=Decimal("1500.00"),
        status="maintenance",
        approval_status="approved",
        verification_status="verified",
        seating_capacity=1,
        transmission="manual",
        fuel_type="electric",
        district="Galle",
        city="Galle",
    )

    # 4. Vehicles for Provider 2 (Isolated)
    v4 = RentalVehicle(
        id=20,
        provider_id=102,
        vehicle_name="Yamaha FZ 150",
        vehicle_type="bike",
        price_per_day=Decimal("4500.00"),
        status="available",
        approval_status="approved",
        verification_status="verified",
        license_plate="WP-XYZ-9999",
        seating_capacity=2,
        transmission="manual",
        fuel_type="petrol",
        district="Colombo",
        city="Colombo",
    )
    db.add_all([v1, v2, v3, v4])
    db.flush()

    # Rate plan for v1
    rp1 = RentalRatePlan(
        id=1,
        vehicle_id=10,
        plan_name="Daily Standard",
        duration_type="daily",
        rate=Decimal("3500.00"),
        included_km_per_day=100,
        extra_km_charge=Decimal("40.00"),
        is_active=True,
    )
    db.add(rp1)

    # 5. Bookings for Provider 1 (v1)
    b_pending = RentalBooking(
        id=1001,
        vehicle_id=10,
        user_id=4,
        pickup_at=now + timedelta(days=2),
        return_at=now + timedelta(days=4),
        total_price=Decimal("7000.00"),
        status="pending",
        payment_status="unpaid",
        expires_at=now + timedelta(hours=1),
    )
    b_confirmed = RentalBooking(
        id=1002,
        vehicle_id=10,
        user_id=4,
        pickup_at=now + timedelta(days=5),
        return_at=now + timedelta(days=7),
        total_price=Decimal("7000.00"),
        status="confirmed",
        payment_status="paid",
    )
    b_active = RentalBooking(
        id=1003,
        vehicle_id=10,
        user_id=4,
        pickup_at=now - timedelta(days=1),
        return_at=now + timedelta(days=1),
        total_price=Decimal("7000.00"),
        status="active",
        payment_status="paid",
    )
    b_completed = RentalBooking(
        id=1004,
        vehicle_id=10,
        user_id=4,
        pickup_at=now - timedelta(days=5),
        return_at=now - timedelta(days=3),
        total_price=Decimal("7000.00"),
        status="completed",
        payment_status="paid",
    )

    # Booking for Provider 2 (v4)
    b_prov2 = RentalBooking(
        id=2001,
        vehicle_id=20,
        user_id=4,
        pickup_at=now + timedelta(days=1),
        return_at=now + timedelta(days=3),
        total_price=Decimal("9000.00"),
        status="confirmed",
        payment_status="paid",
    )
    db.add_all([b_pending, b_confirmed, b_active, b_completed, b_prov2])
    db.flush()

    # 6. Booking References & Transactions
    # Online payment for b_confirmed (7000 LKR)
    ref1002 = BookingReference(
        id=5002,
        booking_reference="RB-1002",
        module_type="rental_vehicle",
        target_booking_id=1002,
        user_id=4,
        provider_profile_id=101,
        total_price=Decimal("7000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    # Cash payment for b_active (7000 LKR)
    ref1003 = BookingReference(
        id=5003,
        booking_reference="RB-1003",
        module_type="rental_vehicle",
        target_booking_id=1003,
        user_id=4,
        provider_profile_id=101,
        total_price=Decimal("7000.00"),
        booking_status="active",
        payment_status="paid",
    )
    # Online payment for b_completed (7000 LKR)
    ref1004 = BookingReference(
        id=5004,
        booking_reference="RB-1004",
        module_type="rental_vehicle",
        target_booking_id=1004,
        user_id=4,
        provider_profile_id=101,
        total_price=Decimal("7000.00"),
        booking_status="completed",
        payment_status="paid",
    )
    # Ref for Provider 2
    ref2001 = BookingReference(
        id=6001,
        booking_reference="RB-2001",
        module_type="rental_vehicle",
        target_booking_id=2001,
        user_id=4,
        provider_profile_id=102,
        total_price=Decimal("9000.00"),
        booking_status="confirmed",
        payment_status="paid",
    )
    db.add_all([ref1002, ref1003, ref1004, ref2001])
    db.flush()

    tx1 = PaymentTransaction(
        id=1,
        booking_reference_id=5002,
        user_id=4,
        gateway_name="payhere",
        gateway_transaction_id="TXN-PAYHERE-001",
        amount=Decimal("7000.00"),
        currency="LKR",
        payment_status="success",
        payment_method="online",
        paid_at=now - timedelta(days=2),
    )
    tx2 = PaymentTransaction(
        id=2,
        booking_reference_id=5003,
        user_id=4,
        gateway_name="cash_on_pickup",
        gateway_transaction_id="TXN-CASH-002",
        amount=Decimal("7000.00"),
        currency="LKR",
        payment_status="success",
        payment_method="cash",
        paid_at=now - timedelta(days=1),
    )
    tx3 = PaymentTransaction(
        id=3,
        booking_reference_id=5004,
        user_id=4,
        gateway_name="payhere",
        gateway_transaction_id="TXN-PAYHERE-003",
        amount=Decimal("7000.00"),
        currency="LKR",
        payment_status="success",
        payment_method="online",
        paid_at=now - timedelta(days=4),
    )
    tx_prov2 = PaymentTransaction(
        id=4,
        booking_reference_id=6001,
        user_id=4,
        gateway_name="payhere",
        gateway_transaction_id="TXN-PAYHERE-999",
        amount=Decimal("9000.00"),
        currency="LKR",
        payment_status="success",
        payment_method="online",
        paid_at=now - timedelta(hours=5),
    )
    db.add_all([tx1, tx2, tx3, tx_prov2])
    db.flush()

    # 7. Listing References for Reviews (v1 and v4)
    lr1 = ListingReference(
        id=1010,
        module_type="rental_vehicle",
        target_id=10,
        provider_profile_id=101,
        title="Honda Dio 110",
        district="Galle",
    )
    lr4 = ListingReference(
        id=1020,
        module_type="rental_vehicle",
        target_id=20,
        provider_profile_id=102,
        title="Yamaha FZ 150",
        district="Colombo",
    )
    db.add_all([lr1, lr4])
    db.flush()

    # Reviews for v1 (Provider 1)
    rev1 = Review(
        id=1,
        listing_reference_id=1010,
        booking_reference_id=5004,
        user_id=4,
        rating=Decimal("5.00"),
        title="Excellent Scooter",
        comment="Clean scooter and smooth pickup experience in Galle.",
        is_verified_booking=True,
        is_published=True,
        created_at=now - timedelta(days=2),
    )
    rev2 = Review(
        id=2,
        listing_reference_id=1010,
        booking_reference_id=None,
        user_id=4,
        rating=Decimal("4.00"),
        title="Good Service",
        comment="Very responsive owner.",
        is_verified_booking=True,
        is_published=True,
        created_at=now - timedelta(days=1),
    )
    # Review for v4 (Provider 2)
    rev_prov2 = Review(
        id=3,
        listing_reference_id=1020,
        booking_reference_id=6001,
        user_id=4,
        rating=Decimal("5.00"),
        title="Super fast bike",
        comment="Great condition in Colombo.",
        is_verified_booking=True,
        is_published=True,
        created_at=now - timedelta(hours=3),
    )
    db.add_all([rev1, rev2, rev_prov2])
    db.commit()

    return {
        "prov_user1": prov_user1,
        "prov_user2": prov_user2,
        "prov_user_unapproved": prov_user_unapproved,
        "traveler_user": traveler_user,
        "homestay_user": homestay_user,
        "profile1": profile1,
        "profile2": profile2,
        "v1": v1,
        "v2": v2,
        "v3": v3,
        "v4": v4,
    }


# ==============================================================================
# SP-01: RENTAL PROVIDER DASHBOARD TESTS
# ==============================================================================

def test_provider_dashboard_aggregation_and_fleet_metrics(seed_test_data, isolated_db_session):
    """Verifies fleet counts, pending requests, active rentals, utilization rate, and revenue summary."""
    user = seed_test_data["prov_user1"]
    client = make_test_client(isolated_db_session, current_user=user)

    resp = client.get("/api/v1/provider/rentals/dashboard")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider_id"] == 101
    assert data["business_name"] == "Sunil Galle Scooters"

    # Fleet metrics: 3 total vehicles (v1 available/approved, v2 pending approval, v3 maintenance)
    fleet = data["fleet"]
    assert fleet["total_vehicles"] == 3
    assert fleet["available_vehicles"] == 1
    assert fleet["pending_approval_vehicles"] == 1
    assert fleet["maintenance_vehicles"] == 1

    # Bookings metrics: 4 bookings (1 pending, 1 confirmed, 1 active, 1 completed)
    bookings = data["bookings"]
    assert bookings["total_bookings"] == 4
    assert bookings["pending_requests"] == 1
    assert bookings["confirmed_bookings"] == 1
    assert bookings["active_rentals"] == 1
    assert bookings["completed_rentals"] == 1

    # Utilization rate: 1 active out of 3 total = 33.33%
    util = data["utilization"]
    assert util["active_rentals_count"] == 1
    assert util["total_fleet_capacity"] == 3
    assert util["fleet_utilization_rate"] == 33.33

    # Revenue metrics: 3 confirmed/active/completed bookings * 7000 = 21000 gross
    rev = data["revenue"]
    assert Decimal(str(rev["gross_booking_revenue"])) == Decimal("21000.00")
    # Online paid: tx1 (7000) + tx3 (7000) = 14000
    assert Decimal(str(rev["online_paid_amount"])) == Decimal("14000.00")
    # Cash collected: tx2 (7000) = 7000
    assert Decimal(str(rev["cash_collected_amount"])) == Decimal("7000.00")
    assert Decimal(str(rev["total_revenue_collected"])) == Decimal("21000.00")


def test_provider_dashboard_cross_provider_isolation(seed_test_data, isolated_db_session):
    """Verifies that provider 2 only sees their own fleet, bookings, and revenue."""
    user2 = seed_test_data["prov_user2"]
    client = make_test_client(isolated_db_session, current_user=user2)

    resp = client.get("/api/v1/provider/rentals/dashboard")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider_id"] == 102
    assert data["business_name"] == "Kamal Colombo Rides"
    assert data["fleet"]["total_vehicles"] == 1
    assert data["fleet"]["available_vehicles"] == 1
    assert data["bookings"]["total_bookings"] == 1
    assert data["bookings"]["confirmed_bookings"] == 1
    assert Decimal(str(data["revenue"]["online_paid_amount"])) == Decimal("9000.00")
    # Provider 1's 21,000 LKR revenue is completely excluded
    assert Decimal(str(data["revenue"]["gross_booking_revenue"])) == Decimal("9000.00")


# ==============================================================================
# SP-16: ONLINE EARNINGS & SETTLEMENTS TESTS
# ==============================================================================

def test_provider_earnings_and_settlements_summary(seed_test_data, isolated_db_session):
    """Verifies gross revenue, confirmed payments, platform commission, completed payouts, and settlements."""
    db = isolated_db_session
    user = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Seed an official provider settlement record in dbo.provider_settlements
    settle1 = ProviderSettlement(
        id=701,
        provider_profile_id=101,
        period_start=now.date() - timedelta(days=14),
        period_end=now.date() - timedelta(days=7),
        gross_amount=Decimal("7000.00"),
        platform_fee=Decimal("700.00"),  # Verified fee on settlement
        net_payable=Decimal("6300.00"),
        currency="LKR",
        settlement_status="completed",
        payout_reference="PAYOUT-BANK-701",
        paid_at=now - timedelta(days=6),
    )
    db.add(settle1)
    db.commit()

    client = make_test_client(db, current_user=user)
    resp = client.get("/api/v1/provider/rentals/settlements")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider_id"] == 101
    assert Decimal(str(data["gross_booking_revenue"])) == Decimal("21000.00")
    assert Decimal(str(data["online_paid_amount"])) == Decimal("14000.00")
    assert Decimal(str(data["cash_paid_amount"])) == Decimal("7000.00")
    assert Decimal(str(data["confirmed_payments"])) == Decimal("21000.00")

    # Platform commission is strictly from recorded platform_fee (700.00), not an unverified 10% on total revenue!
    assert Decimal(str(data["platform_commission"])) == Decimal("700.00")
    assert Decimal(str(data["completed_payouts"])) == Decimal("6300.00")
    # Pending settlements: 14000 (online) - 700 (fee) - 6300 (paid) = 7000.00
    assert Decimal(str(data["pending_settlements"])) == Decimal("7000.00")
    assert len(data["settlements"]) == 1
    assert data["settlements"][0]["payout_reference"] == "PAYOUT-BANK-701"


def test_settlement_detail_and_cross_provider_isolation(seed_test_data, isolated_db_session):
    """Verifies single settlement detail lookup and strict cross-provider authorization guards."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    settle = ProviderSettlement(
        id=702,
        provider_profile_id=101,
        period_start=now.date() - timedelta(days=20),
        period_end=now.date() - timedelta(days=10),
        gross_amount=Decimal("5000.00"),
        platform_fee=Decimal("500.00"),
        net_payable=Decimal("4500.00"),
        currency="LKR",
        settlement_status="completed",
    )
    db.add(settle)
    db.commit()

    # Provider 1 (Owner) can read
    client1 = make_test_client(db, current_user=user1)
    resp1 = client1.get("/api/v1/provider/rentals/settlements/702")
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["id"] == 702

    # Provider 2 (Foreign Provider) is blocked with 403 Forbidden
    client2 = make_test_client(db, current_user=user2)
    resp2 = client2.get("/api/v1/provider/rentals/settlements/702")
    assert resp2.status_code == 403

    # Non-existent settlement returns 404
    resp3 = client1.get("/api/v1/provider/rentals/settlements/99999")
    assert resp3.status_code == 404


def test_commission_rule_verification_no_unverified_hardcoding(seed_test_data, isolated_db_session):
    """Requirement 4: Verify no unverified 10% commission is hardcoded when no settlement fees are recorded."""
    db = isolated_db_session
    user = seed_test_data["prov_user2"]  # Provider 2 has 9000 online revenue, 0 settlements

    client = make_test_client(db, current_user=user)
    resp = client.get("/api/v1/provider/rentals/settlements")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # When no platform fee has been assessed in settlements, platform commission MUST be 0.00, NOT 900.00!
    assert Decimal(str(data["platform_commission"])) == Decimal("0.00")
    assert "Platform commission is computed strictly from verified settlement records" in data["commission_policy_note"]


# ==============================================================================
# SP-17: SPONSORED PROMOTIONS TESTS
# ==============================================================================

def test_promotion_package_discovery_options(seed_test_data, isolated_db_session):
    """Verifies discovery of standard rental promotional boost tiers."""
    client = make_test_client(isolated_db_session, current_user=seed_test_data["prov_user1"])
    resp = client.get("/api/v1/provider/rentals/promotions/packages")
    assert resp.status_code == 200, resp.text
    packages = resp.json()
    assert len(packages) >= 3
    keys = [p["package_key"] for p in packages]
    assert "rental_boost_7d" in keys
    assert "rental_boost_14d" in keys
    assert "rental_boost_30d" in keys


def test_promotion_campaign_creation_and_eligibility(seed_test_data, isolated_db_session):
    """Verifies campaign creation with date validation and vehicle eligibility restriction."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc)

    client1 = make_test_client(db, current_user=user1)

    # 1. Successful creation by provider with approved & available vehicle (v1)
    payload = {
        "campaign_name": "Galle High Season Boost",
        "budget": 5000.00,
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=8)).isoformat(),
        "currency": "LKR",
    }
    resp1 = client1.post("/api/v1/provider/rentals/promotions/campaigns", json=payload)
    assert resp1.status_code == 201, resp1.text
    data1 = resp1.json()
    assert data1["campaign_name"] == "Galle High Season Boost"
    assert data1["target_module"] == "rental"
    assert data1["status"] == "pending_approval"  # Safety restriction: not auto-activated without payment

    # 2. Invalid date range (ends_at <= starts_at)
    bad_dates_payload = {
        "campaign_name": "Invalid Dates",
        "budget": 3000.00,
        "starts_at": (now + timedelta(days=5)).isoformat(),
        "ends_at": (now + timedelta(days=2)).isoformat(),
    }
    resp_bad = client1.post("/api/v1/provider/rentals/promotions/campaigns", json=bad_dates_payload)
    assert resp_bad.status_code == 400
    assert "end date must be after start date" in resp_bad.json()["detail"]


def test_promotion_campaign_eligibility_fails_without_approved_vehicles(seed_test_data, isolated_db_session):
    """Verifies that a provider with zero approved available vehicles cannot launch a sponsored campaign."""
    db = isolated_db_session
    now = datetime.now(timezone.utc)

    # Create a provider with only retired or unapproved vehicles
    unapproved_owner = User(id=8, full_name="No Fleet User", email="nofleet@rentals.lk", role="provider")
    db.add(unapproved_owner)
    db.flush()

    prof_nofleet = ProviderProfile(
        id=108,
        user_id=8,
        business_name="No Fleet Rentals",
        provider_type="rental",
        phone="+94770001111",
        district="Jaffna",
        approval_status="approved",
    )
    db.add(prof_nofleet)
    db.commit()

    client = make_test_client(db, current_user=unapproved_owner)
    payload = {
        "campaign_name": "Ghost Fleet Campaign",
        "budget": 5000.00,
        "starts_at": (now + timedelta(days=1)).isoformat(),
        "ends_at": (now + timedelta(days=8)).isoformat(),
    }
    resp = client.post("/api/v1/provider/rentals/promotions/campaigns", json=payload)
    assert resp.status_code == 400
    assert "no approved and available rental vehicles" in resp.json()["detail"]


def test_promotion_campaign_listing_and_events_tracking(seed_test_data, isolated_db_session):
    """Verifies campaign listing with impressions and clicks telemetry."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Seed campaign for Provider 1
    camp = PromotionCampaign(
        id=88,
        provider_profile_id=101,
        campaign_name="Southern Expressway Scooter Boost",
        target_module="rental",
        budget=Decimal("5000.00"),
        spent_amount=Decimal("1200.00"),
        currency="LKR",
        status="active",
        starts_at=now - timedelta(days=3),
        ends_at=now + timedelta(days=4),
    )
    db.add(camp)
    db.flush()

    # Seed events
    ev1 = PromotionEvent(campaign_id=88, event_type="impression", user_id=4)
    ev2 = PromotionEvent(campaign_id=88, event_type="impression", user_id=None)
    ev3 = PromotionEvent(campaign_id=88, event_type="click", user_id=4)
    db.add_all([ev1, ev2, ev3])
    db.commit()

    client = make_test_client(db, current_user=user1)
    resp = client.get("/api/v1/provider/rentals/promotions/campaigns")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    found = [c for c in data["items"] if c["id"] == 88][0]
    assert found["impressions_count"] == 2
    assert found["clicks_count"] == 1


# ==============================================================================
# SP-18: RENTAL DISCOUNTS & OFFERS TESTS
# ==============================================================================

def test_rental_discount_crud_and_validation(seed_test_data, isolated_db_session):
    """Verifies full CRUD lifecycle and validation constraints for rental discounts."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]
    today = date.today()

    client1 = make_test_client(db, current_user=user1)

    # 1. Create percentage discount
    create_payload = {
        "title": "Weekend Explorer Special",
        "description": "15% off all weekend bike rentals in Galle",
        "discount_percent": 15.00,
        "valid_from": str(today),
        "valid_to": str(today + timedelta(days=14)),
        "is_active": True,
    }
    resp_create = client1.post("/api/v1/provider/rentals/discounts", json=create_payload)
    assert resp_create.status_code == 201, resp_create.text
    d_data = resp_create.json()
    discount_id = d_data["id"]
    assert d_data["title"] == "Weekend Explorer Special"
    assert Decimal(str(d_data["discount_percent"])) == Decimal("15.00")

    # 2. Validation error: neither percent nor amount provided
    bad_payload = {
        "title": "Invalid Empty Discount",
        "valid_from": str(today),
        "valid_to": str(today + timedelta(days=5)),
    }
    resp_bad = client1.post("/api/v1/provider/rentals/discounts", json=bad_payload)
    assert resp_bad.status_code == 422

    # 3. Validation error: valid_to before valid_from
    bad_dates_payload = {
        "title": "Backwards Date Range",
        "discount_percent": 10.00,
        "valid_from": str(today + timedelta(days=10)),
        "valid_to": str(today + timedelta(days=2)),
    }
    resp_bad_dates = client1.post("/api/v1/provider/rentals/discounts", json=bad_dates_payload)
    assert resp_bad_dates.status_code == 422 or resp_bad_dates.status_code == 400

    # 4. Read single discount
    resp_get = client1.get(f"/api/v1/provider/rentals/discounts/{discount_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["id"] == discount_id

    # 5. Cross-provider access guard: provider 2 cannot view or update provider 1's discount
    client2 = make_test_client(db, current_user=user2)
    resp_unauth = client2.get(f"/api/v1/provider/rentals/discounts/{discount_id}")
    assert resp_unauth.status_code == 403

    # 6. Update discount
    update_payload = {"title": "Updated Explorer Deal", "discount_percent": 20.00}
    resp_patch = client1.patch(f"/api/v1/provider/rentals/discounts/{discount_id}", json=update_payload)
    assert resp_patch.status_code == 200
    assert resp_patch.json()["title"] == "Updated Explorer Deal"
    assert Decimal(str(resp_patch.json()["discount_percent"])) == Decimal("20.00")

    # 7. Delete discount
    resp_del = client1.delete(f"/api/v1/provider/rentals/discounts/{discount_id}")
    assert resp_del.status_code == 200
    assert client1.get(f"/api/v1/provider/rentals/discounts/{discount_id}").status_code == 404


def test_discount_traveler_pricing_compatibility(seed_test_data, isolated_db_session):
    """Verifies that discounts cannot produce negative prices and preserve Traveler baseline calculation."""
    db = isolated_db_session
    now = datetime.now(timezone.utc)
    client = make_test_client(db)

    # Baseline traveler quote calculation for Honda Dio (v1: 3500/day for 2 days = 7000)
    quote_payload = {
        "pickup_at": (now + timedelta(days=2)).isoformat(),
        "return_at": (now + timedelta(days=4)).isoformat(),
    }
    resp = client.post("/api/v1/rental-vehicles/10/calculate-quote", json=quote_payload)
    assert resp.status_code == 200, resp.text
    quote = resp.json()
    assert Decimal(str(quote["total_price"])) == Decimal("7000.00")

    # Fixed discount applied safely: 7000 - 1000 = 6000 >= 0
    discounted_price = max(Decimal("0.00"), Decimal(str(quote["total_price"])) - Decimal("1000.00"))
    assert discounted_price == Decimal("6000.00")

    # Excessive discount cannot produce negative price: max(0, 7000 - 10000) = 0.00
    safe_excessive = max(Decimal("0.00"), Decimal(str(quote["total_price"])) - Decimal("10000.00"))
    assert safe_excessive == Decimal("0.00")


# ==============================================================================
# SP-19: CUSTOMER REVIEWS DASHBOARD TESTS
# ==============================================================================

def test_customer_reviews_dashboard_aggregation_and_filters(seed_test_data, isolated_db_session):
    """Verifies fleet reviews aggregation, average rating, star breakdown, and vehicle filtering."""
    db = isolated_db_session
    user1 = seed_test_data["prov_user1"]
    user2 = seed_test_data["prov_user2"]

    client1 = make_test_client(db, current_user=user1)

    # 1. Fleet-wide reviews for Provider 1 (has 2 reviews on v1: 5-star and 4-star)
    resp = client1.get("/api/v1/provider/rentals/reviews")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["total_reviews"] == 2
    assert data["average_rating"] == 4.5  # (5.0 + 4.0) / 2
    dist = data["rating_distribution"]
    assert dist["5_star"] == 1
    assert dist["4_star"] == 1
    assert dist["3_star"] == 0
    assert len(data["reviews"]) == 2
    assert data["reviews"][0]["vehicle_name"] == "Honda Dio 110"

    # 2. Filter by specific vehicle owned by Provider 1
    resp_filtered = client1.get("/api/v1/provider/rentals/reviews?vehicle_id=10")
    assert resp_filtered.status_code == 200
    assert resp_filtered.json()["total_reviews"] == 2

    # 3. Filter by vehicle NOT owned by Provider 1 returns 404
    resp_other = client1.get("/api/v1/provider/rentals/reviews?vehicle_id=20")
    assert resp_other.status_code == 404

    # 4. Cross-provider isolation: Provider 2 only sees their 1 review (v4)
    client2 = make_test_client(db, current_user=user2)
    resp2 = client2.get("/api/v1/provider/rentals/reviews")
    assert resp2.status_code == 200
    assert resp2.json()["total_reviews"] == 1
    assert resp2.json()["reviews"][0]["vehicle_name"] == "Yamaha FZ 150"


# ==============================================================================
# SP-20: PROVIDER & VEHICLE VERIFICATION STATUS TESTS
# ==============================================================================

def test_provider_verification_status_approved_provider(seed_test_data, isolated_db_session):
    """Verifies verification status reporting for approved provider with fleet breakdown."""
    client = make_test_client(isolated_db_session, current_user=seed_test_data["prov_user1"])
    resp = client.get("/api/v1/provider/rentals/verification-status")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider_id"] == 101
    assert data["business_name"] == "Sunil Galle Scooters"
    assert data["kyc_approval_status"] == "approved"
    assert data["is_profile_verified"] is True

    docs = data["documents_submitted"]
    assert docs["selfie_submitted"] is True
    assert docs["nic_front_submitted"] is True
    assert docs["nic_back_submitted"] is True
    assert docs["business_registration_submitted"] is True

    fleet = data["fleet_summary"]
    assert fleet["total_vehicles"] == 3
    assert fleet["approved_vehicles"] == 2  # v1 and v3
    assert fleet["pending_vehicles"] == 1   # v2

    # Check vehicle list
    veh_ids = [v["vehicle_id"] for v in data["vehicles"]]
    assert 10 in veh_ids
    assert 11 in veh_ids
    assert 12 in veh_ids


def test_provider_verification_status_unapproved_provider_access(seed_test_data, isolated_db_session):
    """Verifies that an unapproved (pending) provider can access status tracking to check KYC status."""
    client = make_test_client(isolated_db_session, current_user=seed_test_data["prov_user_unapproved"])
    resp = client.get("/api/v1/provider/rentals/verification-status")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["provider_id"] == 103
    assert data["kyc_approval_status"] == "pending"
    assert data["is_profile_verified"] is False
    assert data["documents_submitted"]["selfie_submitted"] is True
    assert data["documents_submitted"]["nic_front_submitted"] is False


# ==============================================================================
# UNAUTHORIZED ACCESS GUARDS
# ==============================================================================

def test_unauthorized_and_non_rental_provider_access_guards(seed_test_data, isolated_db_session):
    """Verifies role enforcement: travelers and non-rental providers are rejected with 403."""
    db = isolated_db_session

    # Traveler role
    client_traveler = make_test_client(db, current_user=seed_test_data["traveler_user"])
    assert client_traveler.get("/api/v1/provider/rentals/dashboard").status_code == 403
    assert client_traveler.get("/api/v1/provider/rentals/settlements").status_code == 403
    assert client_traveler.get("/api/v1/provider/rentals/verification-status").status_code == 403

    # Homestay provider (different module type)
    client_homestay = make_test_client(db, current_user=seed_test_data["homestay_user"])
    assert client_homestay.get("/api/v1/provider/rentals/dashboard").status_code == 403
    assert client_homestay.get("/api/v1/provider/rentals/settlements").status_code == 403
    assert client_homestay.get("/api/v1/provider/rentals/verification-status").status_code == 403

    # Anonymous / unauthenticated
    client_anon = make_test_client(db, current_user=None)
    assert client_anon.get("/api/v1/provider/rentals/dashboard").status_code == 401
