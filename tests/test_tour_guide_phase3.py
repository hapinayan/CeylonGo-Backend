"""
Comprehensive Phase 3 Automated Tests for Local Tour Guide Module 05:
Booking Engine, Cash Payment Workflow, Cancellation, Calendar Sync & Admin Oversight.

Covers all 11 Phase 3 deliverables and requirement IDs:
- TR-11, TR-13, TR-14, TR-15, TR-19
- PR-11, PR-12, PR-13, PR-14
- AD-05
- GS-04, GS-05
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentTransaction,
)
from app.models.tour_guide import (
    TourGuide,
    TourGuideAvailability,
    TourGuideBooking,
    TourGuidePackage,
)
from app.models.user import Base, User

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(
    bind=test_engine, autoflush=False, expire_on_commit=False
)

PUBLIC_BASE = "/api/v1/tour-guides"
TRAVELER_BOOKINGS_BASE = "/api/v1/tour-guide-bookings"
PROVIDER_BOOKINGS_BASE = "/api/v1/provider/tour-guides/bookings"
PROVIDER_BASE = "/api/v1/provider/tour-guides"
ADMIN_BOOKINGS_BASE = "/api/v1/admin/tour-guides/bookings"


def bearer(user: User, expires_delta: timedelta | None = None) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)}, expires_delta=expires_delta)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session() -> Session:
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session: Session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def booking_env(db_session: Session):
    admin = User(
        full_name="Admin Oversight",
        email="admin.p3@example.com",
        role="admin",
        is_active=True,
    )
    traveler1 = User(
        full_name="Alice Traveler",
        email="alice.p3@example.com",
        role="traveler",
        is_active=True,
    )
    traveler2 = User(
        full_name="Bob Explorer",
        email="bob.p3@example.com",
        role="traveler",
        is_active=True,
    )
    provider1_user = User(
        full_name="Kamal Guide",
        email="kamal.p3@example.com",
        role="provider",
        is_active=True,
    )
    provider2_user = User(
        full_name="Nimal OtherGuide",
        email="nimal.p3@example.com",
        role="provider",
        is_active=True,
    )
    pending_guide_user = User(
        full_name="Unapproved Guide",
        email="pending.p3@example.com",
        role="provider",
        is_active=True,
    )
    db_session.add_all(
        [admin, traveler1, traveler2, provider1_user, provider2_user, pending_guide_user]
    )
    db_session.flush()

    prof1 = ProviderProfile(
        user_id=provider1_user.id,
        business_name="Kandy Heritage Walks",
        provider_type="tour_guide",
        phone="0771112222",
        district="Kandy",
        nic_number="198812345601",
        approval_status="approved",
    )
    prof2 = ProviderProfile(
        user_id=provider2_user.id,
        business_name="Galle Fort Tours",
        provider_type="tour_guide",
        phone="0773334444",
        district="Galle",
        nic_number="198912345602",
        approval_status="approved",
    )
    prof_pending = ProviderProfile(
        user_id=pending_guide_user.id,
        business_name="Pending Guide Co",
        provider_type="tour_guide",
        phone="0775556666",
        district="Colombo",
        nic_number="199012345603",
        approval_status="approved",
    )
    db_session.add_all([prof1, prof2, prof_pending])
    db_session.flush()

    guide1 = TourGuide(
        provider_profile_id=prof1.id,
        bio="Licensed national cultural guide in Kandy.",
        years_of_experience=8,
        specialties="Cultural & Heritage, Nature & Wildlife",
        daily_rate=Decimal("18000.00"),
        hourly_rate=Decimal("3000.00"),
        currency="LKR",
        base_city="Kandy",
        base_district="Kandy",
        max_group_size=6,
        cancellation_policy="Free cancellation up to 24 hours before tour start.",
        free_cancellation_hours=24,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    guide2 = TourGuide(
        provider_profile_id=prof2.id,
        bio="Galle coastal heritage specialist.",
        years_of_experience=5,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("15000.00"),
        hourly_rate=None,
        currency="LKR",
        base_city="Galle",
        base_district="Galle",
        max_group_size=4,
        cancellation_policy="Free cancellation up to 48 hours before start.",
        free_cancellation_hours=48,
        is_available=True,
        approval_status="approved",
        verification_status="unverified",
        is_active=True,
    )
    guide_pending = TourGuide(
        provider_profile_id=prof_pending.id,
        bio="Pending approval guide.",
        years_of_experience=2,
        specialties="City & Local Life",
        daily_rate=Decimal("10000.00"),
        hourly_rate=Decimal("2000.00"),
        currency="LKR",
        base_city="Colombo",
        base_district="Colombo",
        max_group_size=5,
        is_available=True,
        approval_status="pending",
        verification_status="unverified",
        is_active=True,
    )
    db_session.add_all([guide1, guide2, guide_pending])
    db_session.flush()

    pkg_per_person = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Temple of the Tooth Half-Day",
        description="Guided walking tour of Kandy sacred precinct.",
        duration_hours=Decimal("4.00"),
        price=Decimal("4500.00"),
        currency="LKR",
        pricing_type="per_person",
        max_people=4,
        meeting_point="Kandy Lake Clock Tower",
        is_active=True,
    )
    pkg_per_group = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Full-Day Cultural Triangle Private",
        description="Private full-day heritage tour.",
        duration_hours=Decimal("8.00"),
        price=Decimal("22000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Kandy Railway Station",
        is_active=True,
    )
    pkg_inactive = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Archived Night Walk",
        description="Inactive package.",
        duration_hours=Decimal("2.00"),
        price=Decimal("3000.00"),
        currency="LKR",
        pricing_type="per_person",
        max_people=4,
        meeting_point="Kandy City Centre",
        is_active=False,
    )
    pkg_guide2 = TourGuidePackage(
        tour_guide_id=guide2.id,
        title="Galle Lighthouse Walk",
        description="Galle Fort sunset walk.",
        duration_hours=Decimal("3.00"),
        price=Decimal("6000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=4,
        meeting_point="Galle Fort Lighthouse",
        is_active=True,
    )
    db_session.add_all([pkg_per_person, pkg_per_group, pkg_inactive, pkg_guide2])
    db_session.commit()

    return {
        "admin": admin,
        "traveler1": traveler1,
        "traveler2": traveler2,
        "provider1_user": provider1_user,
        "provider2_user": provider2_user,
        "guide1": guide1,
        "guide2": guide2,
        "guide_pending": guide_pending,
        "pkg_per_person": pkg_per_person,
        "pkg_per_group": pkg_per_group,
        "pkg_inactive": pkg_inactive,
        "pkg_guide2": pkg_guide2,
    }


def _future_date(days: int = 5) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


# =============================================================================
# 1. BOOKING CREATION, SERVER-SIDE PRICING, DURATION & GROUP-SIZE ENFORCEMENT
# =============================================================================

def test_create_bookings_server_side_pricing_duration_and_side_effects(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    provider1_user = booking_env["provider1_user"]
    pkg_pp = booking_env["pkg_per_person"]
    pkg_pg = booking_env["pkg_per_group"]

    # 1A. Package per_person booking (3 * 4500 = 13500, duration 4h: 09:00 -> 13:00)
    d1 = _future_date(5)
    resp = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 3,
            "pickup_location": "Queen's Hotel Lobby",
            "special_requests": "English speaking please",
            "payment_method": "pay_at_location",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["booking_status"] == "pending"
    assert data["payment_method"] == "pay_at_location"
    assert data["payment_status"] == "unpaid"
    assert data["is_online_payment"] is False
    assert data["cash_payment_status"] == "UNPAID"
    assert Decimal(data["total_price"]) == Decimal("13500.00")
    assert data["currency"] == "LKR"
    assert data["start_datetime"] == f"{d1.isoformat()}T09:00:00"
    assert data["end_datetime"] == f"{d1.isoformat()}T13:00:00"
    assert data["booking_reference"].startswith("BK-TG-")
    assert data["free_cancellation_hours_snapshot"] == 24
    assert "24 hours" in data["cancellation_policy_snapshot"]
    assert len(data["status_history"]) == 1
    assert data["status_history"][0]["from_status"] is None
    assert data["status_history"][0]["to_status"] == "pending"
    assert data["refund_eligibility"]["is_free_cancellation"] is True

    # Verify BookingReference & dual notifications created
    ref = (
        db_session.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id == data["id"],
        )
        .first()
    )
    assert ref is not None
    assert ref.booking_status == "pending"
    assert ref.payment_status == "unpaid"
    assert ref.total_price == Decimal("13500.00")

    notifs = db_session.query(Notification).all()
    notif_users = {n.user_id for n in notifs}
    assert traveler1.id in notif_users
    assert provider1_user.id in notif_users

    # 1B. Package per_group booking (flat 22000 for 5 people, 8h: 08:00 -> 16:00)
    d2 = _future_date(6)
    resp_pg = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pg.id,
            "booking_date": d2.isoformat(),
            "start_time": "08:00:00",
            "number_of_people": 5,
            "payment_method": "online",
        },
    )
    assert resp_pg.status_code == 201, resp_pg.text
    pg_data = resp_pg.json()
    assert Decimal(pg_data["total_price"]) == Decimal("22000.00")
    assert pg_data["end_datetime"] == f"{d2.isoformat()}T16:00:00"
    assert pg_data["is_online_payment"] is True
    assert pg_data["cash_payment_status"] is None

    # 1C. Hourly booking (3.5h * 3000 = 10500)
    d3 = _future_date(7)
    resp_hr = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "hourly",
            "booking_date": d3.isoformat(),
            "start_time": "10:00:00",
            "duration_hours": "3.50",
            "number_of_people": 2,
            "payment_method": "pay_at_location",
        },
    )
    assert resp_hr.status_code == 201, resp_hr.text
    hr_data = resp_hr.json()
    assert Decimal(hr_data["total_price"]) == Decimal("10500.00")
    assert hr_data["end_datetime"] == f"{d3.isoformat()}T13:30:00"

    # 1D. Daily booking (2 days * 18000 = 36000)
    d4 = _future_date(9)
    resp_day = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "daily",
            "booking_date": d4.isoformat(),
            "start_time": "08:00:00",
            "duration_days": 2,
            "number_of_people": 6,
            "payment_method": "pay_at_location",
        },
    )
    assert resp_day.status_code == 201, resp_day.text
    day_data = resp_day.json()
    assert Decimal(day_data["total_price"]) == Decimal("36000.00")
    expected_last_day = (d4 + timedelta(days=1)).isoformat()
    assert day_data["end_datetime"] == f"{expected_last_day}T18:00:00"


def test_booking_validation_tampering_group_size_and_authorization(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    guide2 = booking_env["guide2"]
    guide_pending = booking_env["guide_pending"]
    traveler1 = booking_env["traveler1"]
    provider1_user = booking_env["provider1_user"]
    admin = booking_env["admin"]
    pkg_pp = booking_env["pkg_per_person"]
    pkg_inactive = booking_env["pkg_inactive"]
    pkg_guide2 = booking_env["pkg_guide2"]
    d1 = _future_date(5)

    # Anonymous user rejected (401)
    r_anon = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    )
    assert r_anon.status_code == 401

    # Provider / Admin cannot use traveler booking creation (403)
    for non_traveler in (provider1_user, admin):
        r_role = client.post(
            f"{PUBLIC_BASE}/{guide1.id}/bookings",
            headers=bearer(non_traveler),
            json={
                "rate_type": "package",
                "package_id": pkg_pp.id,
                "booking_date": d1.isoformat(),
                "number_of_people": 2,
            },
        )
        assert r_role.status_code == 403

    # Client-side price/status/currency tampering rejected by extra='forbid' (422)
    for forbidden_field in (
        {"total_price": "1.00"},
        {"booking_status": "confirmed"},
        {"payment_status": "paid"},
        {"currency": "USD"},
    ):
        payload = {
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
            **forbidden_field,
        }
        r_tamper = client.post(
            f"{PUBLIC_BASE}/{guide1.id}/bookings",
            headers=bearer(traveler1),
            json=payload,
        )
        assert r_tamper.status_code == 422

    # Group size exceeding package.max_people (4) rejected (422)
    r_pkg_max = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 5,
        },
    )
    assert r_pkg_max.status_code == 422
    assert "package maximum" in r_pkg_max.json()["detail"].lower()

    # Group size exceeding guide.max_group_size (6) on daily rate rejected (422)
    r_guide_max = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "daily",
            "booking_date": d1.isoformat(),
            "duration_days": 1,
            "number_of_people": 7,
        },
    )
    assert r_guide_max.status_code == 422
    assert "guide maximum" in r_guide_max.json()["detail"].lower()

    # Past booking datetime rejected (422)
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    r_past = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": yesterday.isoformat(),
            "number_of_people": 2,
        },
    )
    assert r_past.status_code == 422

    # Cross-guide package_id rejected (404)
    r_cross = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_guide2.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    )
    assert r_cross.status_code == 404

    # Inactive package rejected (404)
    r_inact_pkg = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_inactive.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    )
    assert r_inact_pkg.status_code == 404

    # Hourly booking on guide without hourly_rate rejected (400)
    r_no_hr = client.post(
        f"{PUBLIC_BASE}/{guide2.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "hourly",
            "booking_date": d1.isoformat(),
            "duration_hours": "2.00",
            "number_of_people": 2,
        },
    )
    assert r_no_hr.status_code == 400

    # Unapproved guide rejected (404)
    r_unapproved = client.post(
        f"{PUBLIC_BASE}/{guide_pending.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "daily",
            "booking_date": d1.isoformat(),
            "duration_days": 1,
            "number_of_people": 1,
        },
    )
    assert r_unapproved.status_code == 404

    # Guide with is_available=False rejected (400)
    guide1.is_available = False
    db_session.commit()
    r_unavail = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    )
    assert r_unavail.status_code == 400


# =============================================================================
# 2. OVERLAP PROTECTION, DUPLICATE CONFIRMATION GUARD & CALENDAR SYNC
# =============================================================================

def test_overlap_protection_duplicate_confirmation_and_availability_sync(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    traveler2 = booking_env["traveler2"]
    provider1_user = booking_env["provider1_user"]
    pkg_pp = booking_env["pkg_per_person"]
    d_blocked = _future_date(5)
    d_race = _future_date(6)

    # 2A. Blocked availability slot prevents booking creation (409)
    db_session.add(
        TourGuideAvailability(
            tour_guide_id=guide1.id,
            available_date=d_blocked,
            start_time=time(9, 0),
            end_time=time(13, 0),
            is_available=False,
            reason="Personal errand",
        )
    )
    db_session.commit()

    r_blocked = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_blocked.isoformat(),
            "start_time": "10:00:00",
            "number_of_people": 2,
        },
    )
    assert r_blocked.status_code == 409

    # Non-overlapping slot on the same day succeeds (14:00 -> 18:00)
    r_clear = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_blocked.isoformat(),
            "start_time": "14:00:00",
            "number_of_people": 2,
        },
    )
    assert r_clear.status_code == 201

    # 2B. Duplicate overlapping pending request from the SAME traveler blocked (409)
    r_dup_same = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_blocked.isoformat(),
            "start_time": "15:00:00",
            "number_of_people": 1,
        },
    )
    assert r_dup_same.status_code == 409

    # 2C. Two DIFFERENT travelers submit overlapping pending requests on d_race
    r_t1 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_race.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
        },
    )
    assert r_t1.status_code == 201
    b1_id = r_t1.json()["id"]

    r_t2 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler2),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_race.isoformat(),
            "start_time": "11:00:00",
            "number_of_people": 2,
        },
    )
    assert r_t2.status_code == 201
    b2_id = r_t2.json()["id"]

    # Guide accepts b1 -> confirmed
    r_acc1 = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b1_id}/accept",
        headers=bearer(provider1_user),
        json={"notes": "Confirmed for morning walk"},
    )
    assert r_acc1.status_code == 200
    assert r_acc1.json()["booking_status"] == "confirmed"

    # Re-accepting b1 fails (409)
    r_acc1_again = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b1_id}/accept",
        headers=bearer(provider1_user),
    )
    assert r_acc1_again.status_code == 409

    # Attempting to accept overlapping b2 fails with 409 (prevents double confirmation)
    r_acc2 = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b2_id}/accept",
        headers=bearer(provider1_user),
    )
    assert r_acc2.status_code == 409

    # New traveler request overlapping confirmed b1 fails with 409
    r_new_overlap = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler2),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_race.isoformat(),
            "start_time": "10:00:00",
            "number_of_people": 1,
        },
    )
    assert r_new_overlap.status_code == 409

    # Provider trying to block availability over confirmed b1 fails with 409
    r_block_conflict = client.post(
        f"{PROVIDER_BASE}/availability",
        headers=bearer(provider1_user),
        json={
            "available_date": d_race.isoformat(),
            "start_time": "10:00:00",
            "end_time": "12:00:00",
            "is_available": False,
        },
    )
    assert r_block_conflict.status_code == 409

    # Public availability calendar reflects the confirmed booking (anonymized, no PII)
    r_pub_cal = client.get(
        f"{PUBLIC_BASE}/{guide1.id}/availability",
        params={"date_from": d_race.isoformat(), "date_to": d_race.isoformat()},
    )
    assert r_pub_cal.status_code == 200
    pub_day = r_pub_cal.json()["days"][0]
    assert len(pub_day["booked_windows"]) == 1
    assert "Alice" not in r_pub_cal.text


# =============================================================================
# 3. FULL CASH PAYMENT LIFECYCLE, PROVIDER REQUEST MANAGEMENT & RBAC ISOLATION
# =============================================================================

def test_provider_cash_payment_lifecycle_and_strict_online_separation(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    provider1_user = booking_env["provider1_user"]
    provider2_user = booking_env["provider2_user"]
    pkg_pp = booking_env["pkg_per_person"]
    d_cash = _future_date(6)
    d_online = _future_date(7)

    # Create a pay_at_location (cash) booking
    r_cash = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_cash.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "pay_at_location",
        },
    )
    assert r_cash.status_code == 201
    cash_id = r_cash.json()["id"]

    # Create an online booking for separation tests
    r_onl = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_online.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "online",
        },
    )
    assert r_onl.status_code == 201
    online_id = r_onl.json()["id"]

    # Provider2 cannot view/accept/cash/complete Provider1's booking (404)
    assert (
        client.get(
            f"{PROVIDER_BOOKINGS_BASE}/{cash_id}", headers=bearer(provider2_user)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/accept", headers=bearer(provider2_user)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/cash-received",
            headers=bearer(provider2_user),
        ).status_code
        == 404
    )

    # Traveler cannot call provider cash-received endpoint (403)
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/cash-received",
            headers=bearer(traveler1),
        ).status_code
        == 403
    )

    # Provider lists pending requests bucket
    r_req = client.get(
        PROVIDER_BOOKINGS_BASE,
        headers=bearer(provider1_user),
        params={"bucket": "requests"},
    )
    assert r_req.status_code == 200
    assert r_req.json()["total"] == 2

    # Cash receipt cannot be recorded before acceptance (409)
    r_early_cash = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/cash-received",
        headers=bearer(provider1_user),
    )
    assert r_early_cash.status_code == 409

    # Accept both bookings
    r_acc_cash = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/accept",
        headers=bearer(provider1_user),
    )
    assert r_acc_cash.status_code == 200
    assert r_acc_cash.json()["booking_status"] == "confirmed"
    assert r_acc_cash.json()["payment_status"] == "unpaid"
    assert r_acc_cash.json()["cash_payment_status"] == "UNPAID"
    assert r_acc_cash.json()["is_online_payment"] is False

    r_acc_onl = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{online_id}/accept",
        headers=bearer(provider1_user),
    )
    assert r_acc_onl.status_code == 200

    # Cash endpoint rejects online bookings (400)
    r_cash_on_online = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{online_id}/cash-received",
        headers=bearer(provider1_user),
    )
    assert r_cash_on_online.status_code == 400

    # Cannot complete cash booking while unpaid (400)
    r_comp_unpaid = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/complete",
        headers=bearer(provider1_user),
    )
    assert r_comp_unpaid.status_code == 400

    # Record cash received (CASH_RECEIVED)
    r_paid = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/cash-received",
        headers=bearer(provider1_user),
        json={"notes": "Paid 9000 LKR in cash at Kandy Lake"},
    )
    assert r_paid.status_code == 200, r_paid.text
    paid_data = r_paid.json()
    assert paid_data["booking_status"] == "confirmed"
    assert paid_data["payment_method"] == "pay_at_location"
    assert paid_data["payment_status"] == "paid"
    assert paid_data["is_online_payment"] is False
    assert paid_data["cash_payment_status"] == "CASH_RECEIVED"
    assert any("CASH_RECEIVED" in (h["notes"] or "") for h in paid_data["status_history"])

    # CRITICAL: Direct cash receipt MUST NOT insert any row into payment_transactions
    assert db_session.query(PaymentTransaction).count() == 0

    # Duplicate cash-received blocked (409)
    r_dup_cash = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/cash-received",
        headers=bearer(provider1_user),
    )
    assert r_dup_cash.status_code == 409

    # Start tour (confirmed -> in_progress)
    r_start = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/start",
        headers=bearer(provider1_user),
        json={"notes": "Met traveler at clock tower"},
    )
    assert r_start.status_code == 200
    assert r_start.json()["booking_status"] == "in_progress"

    # Complete tour (in_progress -> completed)
    r_comp = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{cash_id}/complete",
        headers=bearer(provider1_user),
        json={"notes": "Tour concluded safely"},
    )
    assert r_comp.status_code == 200
    comp_data = r_comp.json()
    assert comp_data["booking_status"] == "completed"
    assert comp_data["payment_status"] == "paid"
    assert comp_data["cash_payment_status"] == "CASH_RECEIVED"

    # Full audit trail recorded in BookingStatusHistory
    statuses = [h["to_status"] for h in comp_data["status_history"]]
    assert statuses == ["pending", "confirmed", "confirmed", "in_progress", "completed"]

    # BookingReference synchronized to completed/paid
    ref = (
        db_session.query(BookingReference)
        .filter(
            BookingReference.module_type == "tour_guide",
            BookingReference.target_booking_id == cash_id,
        )
        .first()
    )
    assert ref.booking_status == "completed"
    assert ref.payment_status == "paid"

    # Provider completed bucket shows the completed booking
    r_comp_list = client.get(
        PROVIDER_BOOKINGS_BASE,
        headers=bearer(provider1_user),
        params={"bucket": "completed"},
    )
    assert r_comp_list.status_code == 200
    assert [i["id"] for i in r_comp_list.json()["items"]] == [cash_id]


def test_provider_reject_and_suspended_guide_cannot_accept(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    provider1_user = booking_env["provider1_user"]
    pkg_pp = booking_env["pkg_per_person"]
    d1 = _future_date(5)
    d2 = _future_date(6)

    b1 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    ).json()

    # Short reason (<5 chars) rejected (422)
    r_short = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b1['id']}/reject",
        headers=bearer(provider1_user),
        json={"reason": "No"},
    )
    assert r_short.status_code == 422

    # Valid rejection
    r_rej = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b1['id']}/reject",
        headers=bearer(provider1_user),
        json={"reason": "Guide has a family commitment on this morning."},
    )
    assert r_rej.status_code == 200
    rej_data = r_rej.json()
    assert rej_data["booking_status"] == "rejected"
    assert "family commitment" in rej_data["cancellation_reason"]

    # Create another booking then suspend guide -> suspended guide cannot accept (403)
    b2 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d2.isoformat(),
            "number_of_people": 2,
        },
    ).json()
    guide1.approval_status = "suspended"
    db_session.commit()

    r_susp = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b2['id']}/accept",
        headers=bearer(provider1_user),
    )
    assert r_susp.status_code == 403


# =============================================================================
# 4. TRAVELER BOOKINGS, POLICY-BASED CANCELLATION & REFUND ELIGIBILITY
# =============================================================================

def test_traveler_and_provider_cancellations_refund_eligibility_and_slot_release(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    traveler2 = booking_env["traveler2"]
    provider1_user = booking_env["provider1_user"]
    pkg_pp = booking_env["pkg_per_person"]

    # Case A: Confirmed cash booking cancelled OUTSIDE cutoff (> 24h) -> releases slot
    d_free = _future_date(6)
    b_cash = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_free.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "pay_at_location",
        },
    ).json()
    client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b_cash['id']}/accept",
        headers=bearer(provider1_user),
    )

    # Traveler2 cannot inspect or cancel Traveler1's booking (403)
    assert (
        client.get(
            f"{TRAVELER_BOOKINGS_BASE}/{b_cash['id']}", headers=bearer(traveler2)
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{b_cash['id']}/cancel",
            headers=bearer(traveler2),
            json={"reason": "Not my booking attempt"},
        ).status_code
        == 403
    )

    r_cancel_cash = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_cash['id']}/cancel",
        headers=bearer(traveler1),
        json={"reason": "Flight schedule changed by airline."},
    )
    assert r_cancel_cash.status_code == 200, r_cancel_cash.text
    c_cash = r_cancel_cash.json()
    assert c_cash["booking"]["booking_status"] == "cancelled"
    assert c_cash["booking"]["payment_status"] == "unpaid"
    assert c_cash["availability_released"] is True
    assert c_cash["refund_eligibility"]["is_free_cancellation"] is True
    assert Decimal(c_cash["refund_eligibility"]["eligible_refund_amount"]) == Decimal("0.00")

    # Slot is now released — Traveler2 can immediately book the exact same window!
    r_rebook = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler2),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_free.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "pay_at_location",
        },
    )
    assert r_rebook.status_code == 201

    # Duplicate cancel returns 409
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{b_cash['id']}/cancel",
            headers=bearer(traveler1),
            json={"reason": "Cancel twice check"},
        ).status_code
        == 409
    )

    # Case B: Paid online booking cancelled OUTSIDE cutoff -> 100% refund_pending
    d_online_free = _future_date(8)
    b_onl_free = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_online_free.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "online",
        },
    ).json()
    # Simulate confirmed & online-paid state
    orm_onl_free = db_session.get(TourGuideBooking, b_onl_free["id"])
    orm_onl_free.booking_status = "confirmed"
    orm_onl_free.payment_status = "paid"
    db_session.commit()

    r_cancel_onl_free = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_onl_free['id']}/cancel",
        headers=bearer(traveler1),
        json={"reason": "Changing itinerary five days in advance."},
    )
    assert r_cancel_onl_free.status_code == 200
    c_onl_free = r_cancel_onl_free.json()
    assert c_onl_free["booking"]["booking_status"] == "cancelled"
    assert c_onl_free["booking"]["payment_status"] == "refund_pending"
    assert c_onl_free["refund_eligibility"]["is_free_cancellation"] is True
    assert c_onl_free["refund_eligibility"]["eligible_refund_percent"] == 100
    assert Decimal(c_onl_free["refund_eligibility"]["eligible_refund_amount"]) == Decimal("9000.00")

    # Case C: Paid online booking cancelled INSIDE cutoff (< 24h) -> 0% refund, stays paid
    soon_start = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=6)
    b_late = TourGuideBooking(
        tour_guide_id=guide1.id,
        traveler_id=traveler1.id,
        package_id=pkg_pp.id,
        booking_date=soon_start.date(),
        start_datetime=soon_start,
        end_datetime=soon_start + timedelta(hours=4),
        number_of_people=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_method="online",
        payment_status="paid",
        free_cancellation_hours_snapshot=24,
        cancellation_policy_snapshot="Free cancellation up to 24 hours before start.",
    )
    db_session.add(b_late)
    db_session.commit()

    r_cancel_late = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_late.id}/cancel",
        headers=bearer(traveler1),
        json={"reason": "Last minute illness."},
    )
    assert r_cancel_late.status_code == 200
    c_late = r_cancel_late.json()
    assert c_late["booking"]["booking_status"] == "cancelled"
    assert c_late["booking"]["payment_status"] == "paid"
    assert c_late["refund_eligibility"]["is_free_cancellation"] is False
    assert c_late["refund_eligibility"]["eligible_refund_percent"] == 0
    assert Decimal(c_late["refund_eligibility"]["eligible_refund_amount"]) == Decimal("0.00")

    # Case D: Provider cancels a confirmed paid online booking INSIDE cutoff -> still 100% refund_pending
    soon_start2 = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=8)
    b_prov_cancel = TourGuideBooking(
        tour_guide_id=guide1.id,
        traveler_id=traveler1.id,
        package_id=pkg_pp.id,
        booking_date=soon_start2.date(),
        start_datetime=soon_start2,
        end_datetime=soon_start2 + timedelta(hours=4),
        number_of_people=2,
        total_price=Decimal("9000.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_method="online",
        payment_status="paid",
        free_cancellation_hours_snapshot=24,
        cancellation_policy_snapshot="Free cancellation up to 24 hours before start.",
    )
    db_session.add(b_prov_cancel)
    db_session.commit()

    r_prov_cancel = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b_prov_cancel.id}/cancel",
        headers=bearer(provider1_user),
        json={"reason": "Unexpected medical emergency preventing tour delivery."},
    )
    assert r_prov_cancel.status_code == 200
    pc = r_prov_cancel.json()
    assert pc["booking"]["booking_status"] == "cancelled"
    assert pc["booking"]["payment_status"] == "refund_pending"
    assert pc["refund_eligibility"]["is_free_cancellation"] is True
    assert pc["refund_eligibility"]["eligible_refund_percent"] == 100
    assert Decimal(pc["refund_eligibility"]["eligible_refund_amount"]) == Decimal("9000.00")

    # Re-reading detail preserves provider cancellation refund eligibility accurately
    r_detail_after = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/{b_prov_cancel.id}",
        headers=bearer(traveler1),
    )
    assert r_detail_after.status_code == 200
    assert r_detail_after.json()["refund_eligibility"]["eligible_refund_percent"] == 100

    # Traveler booking list buckets work properly
    r_my_cancelled = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/me",
        headers=bearer(traveler1),
        params={"bucket": "cancelled"},
    )
    assert r_my_cancelled.status_code == 200
    assert r_my_cancelled.json()["total"] == 4


# =============================================================================
# 5. LAZY EXPIRATION OF STALE PENDING REQUESTS
# =============================================================================

def test_lazy_expiration_transitions_stale_pending_booking_and_blocks_accept(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    provider1_user = booking_env["provider1_user"]
    pkg_pp = booking_env["pkg_per_person"]
    d1 = _future_date(5)

    created = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d1.isoformat(),
            "number_of_people": 2,
        },
    ).json()

    # Force expires_at into the past
    orm_b = db_session.get(TourGuideBooking, created["id"])
    orm_b.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    db_session.commit()

    # Provider attempting to accept stale booking receives 409 and booking transitions to expired
    r_acc = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{created['id']}/accept",
        headers=bearer(provider1_user),
    )
    assert r_acc.status_code == 409
    assert "expired" in r_acc.json()["detail"].lower()

    r_detail = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/{created['id']}",
        headers=bearer(traveler1),
    )
    assert r_detail.status_code == 200
    assert r_detail.json()["booking_status"] == "expired"
    assert r_detail.json()["status_history"][-1]["to_status"] == "expired"


# =============================================================================
# 6. PROVIDER UNIFIED BOOKING CALENDAR & ADMIN DISPUTE-SAFE OVERSIGHT (AD-05)
# =============================================================================

def test_provider_unified_calendar_and_admin_booking_oversight(
    client: TestClient, db_session: Session, booking_env: dict
):
    guide1 = booking_env["guide1"]
    traveler1 = booking_env["traveler1"]
    traveler2 = booking_env["traveler2"]
    provider1_user = booking_env["provider1_user"]
    admin = booking_env["admin"]
    pkg_pp = booking_env["pkg_per_person"]
    d_day1 = _future_date(5)
    d_day2 = _future_date(6)

    # Blocked slot on day 1 morning
    db_session.add(
        TourGuideAvailability(
            tour_guide_id=guide1.id,
            available_date=d_day1,
            start_time=time(8, 0),
            end_time=time(11, 0),
            is_available=False,
            reason="Morning unavailable",
        )
    )
    db_session.commit()

    # Confirmed cash booking on day 1 afternoon
    b_conf = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_day1.isoformat(),
            "start_time": "13:00:00",
            "number_of_people": 2,
            "payment_method": "pay_at_location",
        },
    ).json()
    client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b_conf['id']}/accept",
        headers=bearer(provider1_user),
    )

    # Pending booking on day 2
    b_pend = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler2),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_day2.isoformat(),
            "start_time": "09:00:00",
            "number_of_people": 2,
            "payment_method": "online",
        },
    ).json()

    # Cancelled booking on day 2 afternoon (for admin issues_only visibility)
    b_canc = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/bookings",
        headers=bearer(traveler1),
        json={
            "rate_type": "package",
            "package_id": pkg_pp.id,
            "booking_date": d_day2.isoformat(),
            "start_time": "14:00:00",
            "number_of_people": 1,
            "payment_method": "pay_at_location",
        },
    ).json()
    client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_canc['id']}/cancel",
        headers=bearer(traveler1),
        json={"reason": "Travel plans updated by group."},
    )

    # 6A. Provider unified calendar shows slots + pending/confirmed bookings (not cancelled)
    r_cal = client.get(
        f"{PROVIDER_BOOKINGS_BASE}/calendar",
        headers=bearer(provider1_user),
        params={"date_from": d_day1.isoformat(), "date_to": d_day2.isoformat()},
    )
    assert r_cal.status_code == 200, r_cal.text
    cal = r_cal.json()
    assert len(cal["days"]) == 2
    day1 = cal["days"][0]
    day2 = cal["days"][1]
    assert day1["is_blocked"] is True
    assert day1["has_confirmed_booking"] is True
    assert day1["has_pending_request"] is False
    assert day1["bookings"][0]["booking_id"] == b_conf["id"]
    assert day2["has_pending_request"] is True
    assert day2["has_confirmed_booking"] is False
    assert [b["booking_id"] for b in day2["bookings"]] == [b_pend["id"]]

    # 6B. Non-admin cannot access admin booking oversight endpoints (403)
    assert (
        client.get(ADMIN_BOOKINGS_BASE, headers=bearer(traveler1)).status_code == 403
    )
    assert (
        client.get(ADMIN_BOOKINGS_BASE, headers=bearer(provider1_user)).status_code
        == 403
    )

    # 6C. Admin lists all bookings with accurate status_counts and issues_count
    r_adm_all = client.get(ADMIN_BOOKINGS_BASE, headers=bearer(admin))
    assert r_adm_all.status_code == 200, r_adm_all.text
    adm_all = r_adm_all.json()
    assert adm_all["total"] == 3
    assert adm_all["status_counts"]["confirmed"] == 1
    assert adm_all["status_counts"]["pending"] == 1
    assert adm_all["status_counts"]["cancelled"] == 1
    assert adm_all["issues_count"] == 1

    # Admin issues_only filter surfaces disputed/cancelled/refund-pending bookings
    r_adm_issues = client.get(
        ADMIN_BOOKINGS_BASE,
        headers=bearer(admin),
        params={"issues_only": "true"},
    )
    assert r_adm_issues.status_code == 200
    assert [i["id"] for i in r_adm_issues.json()["items"]] == [b_canc["id"]]

    # Admin detail returns full dispute-safe audit trail & refund eligibility
    r_adm_detail = client.get(
        f"{ADMIN_BOOKINGS_BASE}/{b_canc['id']}",
        headers=bearer(admin),
    )
    assert r_adm_detail.status_code == 200
    det = r_adm_detail.json()
    assert det["booking_status"] == "cancelled"
    assert det["cancellation_reason"] == "Travel plans updated by group."
    assert len(det["status_history"]) == 2
    assert det["status_history"][-1]["to_status"] == "cancelled"
