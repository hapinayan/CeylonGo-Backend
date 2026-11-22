"""
Automated regression & verification tests for CeylonGo Module 04 — Phase 5A:
Independent Read-Only Metrics, Monitoring & Earnings.

Requirements Covered:
- P01: Provider Dashboard booking & revenue metrics (GET /api/v1/eco-experiences/me/summary)
- A05: Admin Booking Monitoring & audit (GET /api/v1/admin/eco-experiences/bookings)
- A06: Admin Payment Monitoring & commission audit (GET /api/v1/admin/eco-experiences/payments/audit)
- A08: Admin Booking and Revenue Reports (GET /api/v1/admin/eco-experiences/overview)
- P14: Provider Online Earnings and Settlements (GET /api/v1/eco-experiences/me/earnings/summary, GET /api/v1/eco-experiences/me/earnings/settlements)

Safety & Governance:
- 100% Read-Only — Zero mutations performed.
- Strict cross-module isolation: Module 03 generic activity bookings are excluded.
- Provider data isolation: Provider B cannot view Provider A's metrics or settlements.
- Admin-only access control enforced (401/403 for unauthorized callers).
- Exact Decimal calculations for financial aggregation.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.models.user import Base, User
from app.routers.auth import router as auth_router
from app.routers.eco_experiences import (
    admin_eco_router,
    router as eco_experiences_router,
)

# Test application isolating M04 routers
eco_p5a_app = FastAPI(title="CeylonGo Phase 5A Test App")
eco_p5a_app.include_router(auth_router)
eco_p5a_app.include_router(eco_experiences_router)
eco_p5a_app.include_router(admin_eco_router)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
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
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    eco_p5a_app.dependency_overrides[get_db] = override_get_db
    with TestClient(eco_p5a_app) as c:
        yield c
    eco_p5a_app.dependency_overrides.clear()


def create_user(
    db: Session,
    email: str,
    role: str = "traveler",
    is_active: bool = True,
    full_name: str = "Test User",
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("Password123!"),
        full_name=full_name,
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_provider(
    db: Session,
    user: User,
    business_name: str = "Eco Green Adventures",
    provider_type: str = "eco_tourism",
    approval_status: str = "approved",
) -> ProviderProfile:
    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type=provider_type,
        approval_status=approval_status,
        phone="+94771234567",
        district="Kandy",
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def create_experience(
    db: Session,
    provider_id: int,
    title: str = "Rainforest Trek",
    category: str = "Forest & Nature Trails",
    base_price: Decimal = Decimal("5000.00"),
    listing_status: str = "approved",
    is_published: bool = True,
) -> EcoExperience:
    exp = EcoExperience(
        provider_profile_id=provider_id,
        title=title,
        description="Immersive guided trek",
        eco_category=category,
        pricing_type="per_person",
        base_price=base_price,
        duration_hours=Decimal("4.0"),
        max_group_size=15,
        min_participants=1,
        district="Kandy",
        city="Kandy",
        meeting_point="Kandy Main Gate",
        environmental_guidelines="Strict leave-no-trace policy.",
        wildlife_safety_summary="No feeding of wildlife permitted.",
        listing_status=listing_status,
        is_published=is_published,
    )
    db.add(exp)
    db.commit()
    db.refresh(exp)
    return exp


def create_slot(
    db: Session,
    provider_id: int,
    eco_experience_id: int | None = None,
) -> ActivitySlot:
    act = Activity(
        provider_profile_id=provider_id,
        title="Base Activity",
        description="A base activity description",
        category="cultural",
        district="Kandy",
        city="Kandy",
        meeting_point="Kandy Point",
        duration_minutes=240,
        pricing_type="per_person",
        price=Decimal("5000.00"),
    )
    db.add(act)
    db.flush()

    now = datetime.now(UTC)
    slot = ActivitySlot(
        activity_id=act.id,
        eco_experience_id=eco_experience_id,
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=4),
        capacity=20,
        reserved_count=0,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def create_booking(
    db: Session,
    eco_experience_id: int | None,
    user_id: int,
    total_price: Decimal = Decimal("5000.00"),
    participants: int = 1,
    booking_status: str = "confirmed",
    payment_status: str = "paid",
    payment_method: str = "online",
    slot_id: int | None = None,
    reference_code: str | None = None,
    provider_id: int | None = None,
) -> ActivityBooking:
    if slot_id is None:
        p_id = provider_id or 1
        slot = create_slot(db, p_id, eco_experience_id)
        slot_id = slot.id

    booking = ActivityBooking(
        activity_slot_id=slot_id,
        eco_experience_id=eco_experience_id,
        user_id=user_id,
        participants=participants,
        total_price=total_price,
        currency="LKR",
        payment_method=payment_method,
        payment_status=payment_status,
        booking_status=booking_status,
        created_at=datetime.now(UTC),
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    if reference_code and provider_id:
        ref = BookingReference(
            booking_reference=reference_code,
            module_type="activity",
            target_booking_id=booking.id,
            user_id=user_id,
            provider_profile_id=provider_id,
            total_price=total_price,
            currency="LKR",
            booking_status=booking_status,
            payment_status=payment_status,
        )
        db.add(ref)
        db.commit()

    return booking


def auth_header(user: User) -> dict:
    token = create_access_token(data={"sub": str(user.id), "role": user.role})
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# 1. EMPTY DATASETS TESTS
# =============================================================================


def test_empty_datasets(client: TestClient, db_session: Session):
    """
    Verifies that all Phase 5A endpoints return valid zero/empty responses when
    no data exists in the database.
    """
    provider_user = create_user(db_session, "empty_provider@test.com", role="provider")
    create_provider(db_session, provider_user)
    admin_user = create_user(db_session, "empty_admin@test.com", role="admin")

    headers_p = auth_header(provider_user)
    headers_a = auth_header(admin_user)

    # P01: Provider summary with 0 bookings
    resp_p01 = client.get("/api/v1/eco-experiences/me/summary", headers=headers_p)
    assert resp_p01.status_code == 200
    p01_data = resp_p01.json()
    assert p01_data["total_bookings"] == 0
    assert p01_data["total_revenue"] == "0.00"
    assert p01_data["pending_cash_revenue"] == "0.00"
    assert p01_data["refunded_amount"] == "0.00"

    # P14: Provider earnings summary
    resp_p14 = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    assert resp_p14.status_code == 200
    p14_data = resp_p14.json()
    assert p14_data["total_bookings"] == 0
    assert p14_data["gross_earnings"] == "0.00"
    assert p14_data["platform_commission"] == "0.00"
    assert p14_data["net_earnings"] == "0.00"
    assert p14_data["total_settled"] == "0.00"
    assert p14_data["pending_settlement"] == "0.00"

    # P14: Provider settlements pagination
    resp_settle = client.get("/api/v1/eco-experiences/me/earnings/settlements", headers=headers_p)
    assert resp_settle.status_code == 200
    assert resp_settle.json()["total"] == 0
    assert resp_settle.json()["items"] == []

    # A08: Admin overview booking & financial metrics
    resp_a08 = client.get("/api/v1/admin/eco-experiences/overview", headers=headers_a)
    assert resp_a08.status_code == 200
    a08_data = resp_a08.json()
    assert a08_data["booking_metrics"]["total_bookings"] == 0
    assert a08_data["financial_metrics"]["gross_booking_value"] == "0.00"
    assert a08_data["financial_metrics"]["paid_revenue"] == "0.00"

    # A05: Admin booking monitoring
    resp_a05 = client.get("/api/v1/admin/eco-experiences/bookings", headers=headers_a)
    assert resp_a05.status_code == 200
    assert resp_a05.json()["total"] == 0
    assert resp_a05.json()["items"] == []

    # A06: Admin payment audit
    resp_a06 = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    assert resp_a06.status_code == 200
    assert resp_a06.json()["total"] == 0
    assert resp_a06.json()["summary"]["total_bookings"] == 0
    assert resp_a06.json()["summary"]["total_paid_volume"] == "0.00"


# =============================================================================
# 2. ADMIN-ONLY ACCESS CONTROL TESTS
# =============================================================================


def test_admin_access_control(client: TestClient, db_session: Session):
    """
    Verifies that admin-only monitoring endpoints strictly reject unauthenticated
    and non-admin callers (401 and 403).
    """
    traveler = create_user(db_session, "traveler_auth@test.com", role="traveler")
    provider_user = create_user(db_session, "provider_auth@test.com", role="provider")
    create_provider(db_session, provider_user)

    admin_endpoints = [
        "/api/v1/admin/eco-experiences/bookings",
        "/api/v1/admin/eco-experiences/payments/audit",
        "/api/v1/admin/eco-experiences/overview",
    ]

    for ep in admin_endpoints:
        # 1. Unauthenticated -> 401
        assert client.get(ep).status_code == 401

        # 2. Traveler -> 403
        assert client.get(ep, headers=auth_header(traveler)).status_code == 403

        # 3. Provider -> 403
        assert client.get(ep, headers=auth_header(provider_user)).status_code == 403


# =============================================================================
# 3. PROVIDER DATA ISOLATION TESTS
# =============================================================================


def test_provider_data_isolation(client: TestClient, db_session: Session):
    """
    Verifies that Provider B cannot view Provider A's metrics, revenues,
    or settlement records under any circumstances.
    """
    user_a = create_user(db_session, "provider_a@test.com", role="provider", full_name="Provider A")
    prof_a = create_provider(db_session, user_a, business_name="Provider A Eco")
    exp_a = create_experience(db_session, prof_a.id, "Experience A")

    user_b = create_user(db_session, "provider_b@test.com", role="provider", full_name="Provider B")
    prof_b = create_provider(db_session, user_b, business_name="Provider B Eco")
    create_experience(db_session, prof_b.id, "Experience B")

    traveler = create_user(db_session, "traveler1@test.com", role="traveler")

    # Create bookings for Provider A only
    create_booking(
        db_session, exp_a.id, traveler.id,
        total_price=Decimal("12000.00"),
        booking_status="confirmed", payment_status="paid",
        reference_code="ECO-BK-A1", provider_id=prof_a.id,
    )

    # Record settlement for Provider A only
    settle_a = ProviderSettlement(
        provider_profile_id=prof_a.id,
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 15),
        gross_amount=Decimal("12000.00"),
        platform_fee=Decimal("1200.00"),
        net_payable=Decimal("10800.00"),
        currency="LKR",
        settlement_status="paid",
        payout_reference="PAY-REF-A1",
        paid_at=datetime.now(UTC),
    )
    db_session.add(settle_a)
    db_session.commit()

    # Query as Provider A -> sees non-zero metrics
    resp_a_p01 = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(user_a))
    assert resp_a_p01.json()["total_bookings"] == 1
    assert resp_a_p01.json()["total_revenue"] == "12000.00"

    resp_a_p14 = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=auth_header(user_a))
    assert resp_a_p14.json()["gross_earnings"] == "12000.00"
    assert resp_a_p14.json()["total_settled"] == "10800.00"
    assert resp_a_p14.json()["pending_settlement"] == "0.00"

    resp_a_set = client.get("/api/v1/eco-experiences/me/earnings/settlements", headers=auth_header(user_a))
    assert resp_a_set.json()["total"] == 1
    assert resp_a_set.json()["items"][0]["payout_reference"] == "PAY-REF-A1"

    # Query as Provider B -> strictly isolated, sees 0 metrics & 0 settlements
    resp_b_p01 = client.get("/api/v1/eco-experiences/me/summary", headers=auth_header(user_b))
    assert resp_b_p01.json()["total_bookings"] == 0
    assert resp_b_p01.json()["total_revenue"] == "0.00"

    resp_b_p14 = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=auth_header(user_b))
    assert resp_b_p14.json()["gross_earnings"] == "0.00"
    assert resp_b_p14.json()["total_settled"] == "0.00"
    assert resp_b_p14.json()["pending_settlement"] == "0.00"

    resp_b_set = client.get("/api/v1/eco-experiences/me/earnings/settlements", headers=auth_header(user_b))
    assert resp_b_set.json()["total"] == 0
    assert resp_b_set.json()["items"] == []


# =============================================================================
# 4. CROSS-MODULE ISOLATION TESTS
# =============================================================================


def test_cross_module_isolation(client: TestClient, db_session: Session):
    """
    Verifies that Module 03 generic activity bookings (eco_experience_id is None)
    are strictly excluded from Module 04 eco dashboards and admin monitoring.
    """
    provider_user = create_user(db_session, "provider_m04@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Pure Eco Experience")
    admin_user = create_user(db_session, "admin_m04@test.com", role="admin")
    traveler = create_user(db_session, "traveler_m04@test.com", role="traveler")

    # 1. Create a pure Module 04 Eco booking
    create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("7500.00"),
        booking_status="confirmed", payment_status="paid",
        reference_code="ECO-M04-001", provider_id=prof.id,
    )

    # 2. Create a generic Module 03 Activity booking (eco_experience_id=None)
    create_booking(
        db_session, None, traveler.id,
        total_price=Decimal("50000.00"),
        booking_status="confirmed", payment_status="paid",
        reference_code="ACT-M03-999", provider_id=prof.id,
    )

    # Admin bookings list -> Should only show the 1 Eco booking
    resp_a05 = client.get("/api/v1/admin/eco-experiences/bookings", headers=auth_header(admin_user))
    assert resp_a05.status_code == 200
    data_a05 = resp_a05.json()
    assert data_a05["total"] == 1
    assert data_a05["items"][0]["booking_reference"] == "ECO-M04-001"
    assert data_a05["items"][0]["total_price"] == "7500.00"

    # Admin payments audit -> Gross volume must be 7500.00, NOT 57500.00
    resp_a06 = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=auth_header(admin_user))
    assert resp_a06.status_code == 200
    summary = resp_a06.json()["summary"]
    assert summary["total_bookings"] == 1
    assert summary["total_paid_volume"] == "7500.00"

    # Admin overview -> Gross booking value must be 7500.00
    resp_a08 = client.get("/api/v1/admin/eco-experiences/overview", headers=auth_header(admin_user))
    assert resp_a08.status_code == 200
    fin = resp_a08.json()["financial_metrics"]
    assert fin["gross_booking_value"] == "7500.00"
    assert fin["paid_revenue"] == "7500.00"


# =============================================================================
# 5. FINANCIAL ACCURACY & STATUS FILTERING TESTS
# =============================================================================


def test_financial_accuracy_and_filtering(client: TestClient, db_session: Session):
    """
    Verifies:
    1. Financial calculation accuracy:
       - Paid: 10,000.00 -> 10% commission = 1,000.00 -> Net = 9,000.00
       - Pending Cash: 4,000.00
       - Refunded: 2,500.00
    2. Booking status and payment status filtering on /admin/eco-experiences/bookings.
    3. Date range validation (end_date < start_date -> 422).
    """
    provider_user = create_user(db_session, "calc_provider@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Calc Safari")
    admin_user = create_user(db_session, "calc_admin@test.com", role="admin")
    traveler = create_user(db_session, "calc_traveler@test.com", role="traveler")

    # Booking 1: Online, Paid, Confirmed
    b1 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("10000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="ECO-CALC-001", provider_id=prof.id,
    )

    # Booking 2: Pay at location, Unpaid, Pending
    b2 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("4000.00"),
        booking_status="pending", payment_status="unpaid", payment_method="pay_at_location",
        reference_code="ECO-CALC-002", provider_id=prof.id,
    )

    # Booking 3: Online, Refunded, Cancelled
    b3 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("2500.00"),
        booking_status="cancelled", payment_status="refunded", payment_method="online",
        reference_code="ECO-CALC-003", provider_id=prof.id,
    )

    headers_p = auth_header(provider_user)
    headers_a = auth_header(admin_user)

    # Provider Summary (P01)
    resp_p01 = client.get("/api/v1/eco-experiences/me/summary", headers=headers_p)
    assert resp_p01.status_code == 200
    p01 = resp_p01.json()
    assert p01["total_bookings"] == 3
    assert p01["confirmed_bookings"] == 1
    assert p01["pending_bookings"] == 1
    assert p01["cancelled_bookings"] == 1
    assert p01["total_revenue"] == "10000.00"
    assert p01["pending_cash_revenue"] == "4000.00"
    assert p01["refunded_amount"] == "2500.00"

    # Provider Earnings Summary (P14)
    resp_p14 = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    assert resp_p14.status_code == 200
    p14 = resp_p14.json()
    assert p14["gross_earnings"] == "10000.00"
    assert p14["platform_commission"] == "1000.00"
    assert p14["net_earnings"] == "9000.00"
    assert p14["total_settled"] == "0.00"
    assert p14["pending_settlement"] == "9000.00"
    assert p14["pending_cash_to_collect"] == "4000.00"
    assert p14["refunded_amount"] == "2500.00"

    # Admin Payments Audit (A06)
    resp_a06 = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    assert resp_a06.status_code == 200
    a06 = resp_a06.json()
    assert a06["summary"]["total_bookings"] == 3
    assert a06["summary"]["total_gross_volume"] == "16500.00"
    assert a06["summary"]["total_paid_volume"] == "10000.00"
    assert a06["summary"]["total_pending_cash_volume"] == "4000.00"
    assert a06["summary"]["total_refunded_volume"] == "2500.00"
    assert a06["summary"]["total_platform_commission"] == "1000.00"
    assert a06["summary"]["total_provider_payable"] == "9000.00"

    # Admin Booking Filtering (A05)
    # Filter by booking_status=confirmed -> 1
    resp_f_conf = client.get("/api/v1/admin/eco-experiences/bookings?status=confirmed", headers=headers_a)
    assert resp_f_conf.json()["total"] == 1
    assert resp_f_conf.json()["items"][0]["booking_reference"] == "ECO-CALC-001"

    # Filter by payment_status=unpaid -> 1
    resp_f_unpaid = client.get("/api/v1/admin/eco-experiences/bookings?payment_status=unpaid", headers=headers_a)
    assert resp_f_unpaid.json()["total"] == 1
    assert resp_f_unpaid.json()["items"][0]["booking_reference"] == "ECO-CALC-002"

    # Filter by experience_id -> 3
    resp_f_exp = client.get(f"/api/v1/admin/eco-experiences/bookings?experience_id={exp.id}", headers=headers_a)
    assert resp_f_exp.json()["total"] == 3

    # Date range error check: end_date < start_date -> 422
    resp_date_err = client.get(
        "/api/v1/admin/eco-experiences/bookings?start_date=2026-11-20&end_date=2026-11-10",
        headers=headers_a,
    )
    assert resp_date_err.status_code == 422


# =============================================================================
# 6. SETTLEMENT STATUS FILTERING & PAGINATION TESTS
# =============================================================================


def test_settlement_status_filtering_and_pagination(client: TestClient, db_session: Session):
    """
    Verifies that provider settlement listing correctly filters by settlement_status
    and calculates total_pages and pagination offset.
    """
    provider_user = create_user(db_session, "settle_provider@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    headers = auth_header(provider_user)

    # 1. Add 2 settlements: one paid, one pending
    s1 = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 15),
        gross_amount=Decimal("15000.00"),
        platform_fee=Decimal("1500.00"),
        net_payable=Decimal("13500.00"),
        currency="LKR",
        settlement_status="paid",
        payout_reference="SETTLE-101",
        paid_at=datetime.now(UTC),
    )
    s2 = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 16),
        period_end=date(2026, 10, 31),
        gross_amount=Decimal("8000.00"),
        platform_fee=Decimal("800.00"),
        net_payable=Decimal("7200.00"),
        currency="LKR",
        settlement_status="pending",
        payout_reference=None,
    )
    db_session.add_all([s1, s2])
    db_session.commit()

    # Query all settlements -> total=2
    resp_all = client.get("/api/v1/eco-experiences/me/earnings/settlements", headers=headers)
    assert resp_all.status_code == 200
    assert resp_all.json()["total"] == 2

    # Query status=paid -> total=1
    resp_paid = client.get("/api/v1/eco-experiences/me/earnings/settlements?settlement_status=paid", headers=headers)
    assert resp_paid.status_code == 200
    assert resp_paid.json()["total"] == 1
    assert resp_paid.json()["items"][0]["payout_reference"] == "SETTLE-101"

    # Query status=pending -> total=1
    resp_pending = client.get("/api/v1/eco-experiences/me/earnings/settlements?settlement_status=pending", headers=headers)
    assert resp_pending.status_code == 200
    assert resp_pending.json()["total"] == 1
    assert resp_pending.json()["items"][0]["payout_reference"] is None

    # Test pagination: page=1, page_size=1 -> total_pages=2, returns 1 item
    resp_p1 = client.get("/api/v1/eco-experiences/me/earnings/settlements?page=1&page_size=1", headers=headers)
    assert resp_p1.status_code == 200
    assert resp_p1.json()["page"] == 1
    assert resp_p1.json()["page_size"] == 1
    assert resp_p1.json()["total_pages"] == 2
    assert len(resp_p1.json()["items"]) == 1

    # Test pagination: page=2, page_size=1 -> returns next item
    resp_p2 = client.get("/api/v1/eco-experiences/me/earnings/settlements?page=2&page_size=1", headers=headers)
    assert resp_p2.status_code == 200
    assert resp_p2.json()["page"] == 2
    assert len(resp_p2.json()["items"]) == 1


# =============================================================================
# 7. ADMIN PAYMENT AUDIT FILTERING & SCHEMA VALIDATION TESTS
# =============================================================================


def test_admin_payment_audit_filtering_and_schemas(client: TestClient, db_session: Session):
    """
    Verifies that admin payments audit endpoint:
    - Filters by payment_method ('online' vs 'pay_at_location')
    - Filters by payment_status ('paid', 'unpaid', 'refunded')
    - Filters by provider_id
    - Validates date range validation
    - Conforms to exact Pydantic response schemas
    """
    from app.schemas.eco_experience import (
        EcoAdminBookingPagination,
        EcoAdminOverviewResponse,
        EcoAdminPaymentAuditResponse,
        EcoProviderEarningsSummaryResponse,
        EcoProviderListingSummary,
        EcoProviderSettlementPagination,
    )

    admin_user = create_user(db_session, "audit_admin@test.com", role="admin")
    provider_user = create_user(db_session, "audit_provider@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Audit Eco Experience")
    traveler = create_user(db_session, "audit_traveler@test.com", role="traveler")

    # Booking 1: online, paid
    create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("15000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="AUDIT-ONLINE-001", provider_id=prof.id,
    )

    # Booking 2: cash, unpaid
    create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("5000.00"),
        booking_status="pending", payment_status="unpaid", payment_method="pay_at_location",
        reference_code="AUDIT-CASH-002", provider_id=prof.id,
    )

    headers_a = auth_header(admin_user)
    headers_p = auth_header(provider_user)

    # 1. Filter by payment_method=online -> total=1
    resp_online = client.get("/api/v1/admin/eco-experiences/payments/audit?payment_method=online", headers=headers_a)
    assert resp_online.status_code == 200
    assert resp_online.json()["total"] == 1
    assert resp_online.json()["items"][0]["payment_method"] == "online"

    # 2. Filter by payment_method=pay_at_location -> total=1
    resp_cash = client.get("/api/v1/admin/eco-experiences/payments/audit?payment_method=pay_at_location", headers=headers_a)
    assert resp_cash.status_code == 200
    assert resp_cash.json()["total"] == 1
    assert resp_cash.json()["items"][0]["payment_method"] == "pay_at_location"

    # 3. Filter by provider_id
    resp_prov = client.get(f"/api/v1/admin/eco-experiences/payments/audit?provider_id={prof.id}", headers=headers_a)
    assert resp_prov.status_code == 200
    assert resp_prov.json()["total"] == 2

    # 4. Date validation error on payments audit
    resp_d_err = client.get(
        "/api/v1/admin/eco-experiences/payments/audit?start_date=2026-12-01&end_date=2026-11-01",
        headers=headers_a,
    )
    assert resp_d_err.status_code == 422

    # 5. Schema validation check across all responses
    p01_resp = client.get("/api/v1/eco-experiences/me/summary", headers=headers_p)
    EcoProviderListingSummary.model_validate(p01_resp.json())

    p14_sum = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    EcoProviderEarningsSummaryResponse.model_validate(p14_sum.json())

    p14_set = client.get("/api/v1/eco-experiences/me/earnings/settlements", headers=headers_p)
    EcoProviderSettlementPagination.model_validate(p14_set.json())

    a08_resp = client.get("/api/v1/admin/eco-experiences/overview", headers=headers_a)
    EcoAdminOverviewResponse.model_validate(a08_resp.json())

    a05_resp = client.get("/api/v1/admin/eco-experiences/bookings", headers=headers_a)
    EcoAdminBookingPagination.model_validate(a05_resp.json())

    a06_resp = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    EcoAdminPaymentAuditResponse.model_validate(a06_resp.json())


# =============================================================================
# 8. PHASE 5A FINANCIAL CORRECTION TESTS
# =============================================================================


def test_provider_payout_accounting_and_settlement_evidence(
    client: TestClient, db_session: Session
):
    """
    Verifies Phase 5A Financial Correction #1:
    - Only settlements with status 'paid' AND valid payout evidence (payout_reference or paid_at)
      count as actual disbursed payouts.
    - Settlements with status 'approved' count separately as approved but undisbursed payables.
    - Settlements with status 'on_hold' count separately.
    - Settlements with status 'completed' do NOT count as disbursed payouts.
    - Settlements with status 'paid' lacking payout evidence do NOT count as disbursed payouts.
    - Actual recorded platform fees are aggregated from dbo.provider_settlements.
    """
    provider_user = create_user(db_session, "payout_prov@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Payout Test Safari")
    traveler = create_user(db_session, "payout_trav@test.com", role="traveler")
    admin_user = create_user(db_session, "payout_admin@test.com", role="admin")

    # 1. Booking: 50,000.00 paid online
    create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("50000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="ECO-PAYOUT-001", provider_id=prof.id,
    )

    now = datetime.now(UTC)

    # Settlement 1: 'paid' WITH valid payout evidence -> counts as disbursed payout
    s_valid_paid = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 15),
        gross_amount=Decimal("11000.00"),
        platform_fee=Decimal("1000.00"),
        net_payable=Decimal("10000.00"),
        currency="LKR",
        settlement_status="paid",
        payout_reference="DISBURSED-REF-001",
        paid_at=now,
    )

    # Settlement 2: 'paid' WITHOUT payout evidence (no reference, no paid_at) -> excluded from disbursed payouts
    s_unproven_paid = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 16),
        period_end=date(2026, 10, 20),
        gross_amount=Decimal("5500.00"),
        platform_fee=Decimal("500.00"),
        net_payable=Decimal("5000.00"),
        currency="LKR",
        settlement_status="paid",
        payout_reference=None,
        paid_at=None,
    )

    # Settlement 3: 'approved' -> authorized but undisbursed payable
    s_approved = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 21),
        period_end=date(2026, 10, 25),
        gross_amount=Decimal("16500.00"),
        platform_fee=Decimal("1500.00"),
        net_payable=Decimal("15000.00"),
        currency="LKR",
        settlement_status="approved",
        payout_reference=None,
        paid_at=None,
    )

    # Settlement 4: 'on_hold' -> withheld / disputed
    s_on_hold = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 26),
        period_end=date(2026, 10, 28),
        gross_amount=Decimal("8800.00"),
        platform_fee=Decimal("800.00"),
        net_payable=Decimal("8000.00"),
        currency="LKR",
        settlement_status="on_hold",
        payout_reference=None,
        paid_at=None,
    )

    # Settlement 5: 'completed' (invalid under verified SQL Server schema) -> excluded from disbursed payouts
    s_completed = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 10, 29),
        period_end=date(2026, 10, 31),
        gross_amount=Decimal("7700.00"),
        platform_fee=Decimal("700.00"),
        net_payable=Decimal("7000.00"),
        currency="LKR",
        settlement_status="completed",
        payout_reference="COMPLETED-REF-005",
        paid_at=now,
    )

    # Settlement 6: 'pending' -> awaiting review
    s_pending = ProviderSettlement(
        provider_profile_id=prof.id,
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 5),
        gross_amount=Decimal("6600.00"),
        platform_fee=Decimal("600.00"),
        net_payable=Decimal("6000.00"),
        currency="LKR",
        settlement_status="pending",
        payout_reference=None,
        paid_at=None,
    )

    db_session.add_all([s_valid_paid, s_unproven_paid, s_approved, s_on_hold, s_completed, s_pending])
    db_session.commit()

    headers_p = auth_header(provider_user)
    headers_a = auth_header(admin_user)

    # 1. Test Provider Earnings Endpoint (P14)
    resp_p = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    assert resp_p.status_code == 200
    p_data = resp_p.json()

    assert p_data["gross_earnings"] == "50000.00"
    assert p_data["estimated_platform_commission"] == "5000.00"
    assert p_data["platform_commission"] == "5000.00"
    assert p_data["net_earnings"] == "45000.00"

    # Only s_valid_paid counts as disbursed payout (net_payable: 10000.00)
    assert p_data["total_disbursed_payouts"] == "10000.00"
    assert p_data["total_settled"] == "10000.00"  # Backwards compatibility alias

    # Approved settlements kept separate
    assert p_data["approved_pending_payouts"] == "15000.00"

    # On-hold settlements kept separate
    assert p_data["on_hold_payouts"] == "8000.00"

    # Actual recorded platform fees across settlements (1000+500+1500+800+700+600 = 5100.00)
    assert p_data["recorded_platform_fees"] == "5100.00"

    # Pending settlement = net_earnings (45000) - disbursed (10000) - approved (15000) = 20000.00
    assert p_data["pending_settlement"] == "20000.00"

    # 2. Test Admin Overview Endpoint (A08)
    resp_a = client.get("/api/v1/admin/eco-experiences/overview", headers=headers_a)
    assert resp_a.status_code == 200
    a_fin = resp_a.json()["financial_metrics"]

    assert a_fin["paid_revenue"] == "50000.00"
    assert a_fin["estimated_platform_commission"] == "5000.00"
    assert a_fin["total_disbursed_settlements"] == "10000.00"
    assert a_fin["approved_pending_settlements"] == "15000.00"
    assert a_fin["recorded_platform_fees"] == "5100.00"

    # 3. Test Admin Payments Audit Endpoint (A06)
    resp_audit = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    assert resp_audit.status_code == 200
    audit_sum = resp_audit.json()["summary"]

    assert audit_sum["total_paid_volume"] == "50000.00"
    assert audit_sum["estimated_platform_commission"] == "5000.00"
    assert audit_sum["total_platform_commission"] == "5000.00"
    assert audit_sum["total_disbursed_settlements"] == "10000.00"
    assert audit_sum["total_approved_settlements"] == "15000.00"
    assert audit_sum["recorded_platform_fees"] == "5100.00"


def test_authoritative_refund_accounting_and_isolation(
    client: TestClient, db_session: Session
):
    """
    Verifies Phase 5A Financial Correction #3:
    - Authoritative refund accounting uses actual PaymentRefund.amount where refund_status == 'processed'.
    - Partial refunds use PaymentRefund.amount, NOT ActivityBooking.total_price.
    - Falls back to ActivityBooking.total_price where payment_status == 'refunded' ONLY when no PaymentRefund row exists.
    - Prevents double counting (a booking with PaymentRefund never also triggers fallback total_price).
    - Excludes 'pending' refunds (not yet disbursed).
    - Strict cross-module isolation: generic Activity bookings without eco_experience_id are excluded.
    """
    provider_user = create_user(db_session, "rf_provider@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Refund Test Safari")
    traveler = create_user(db_session, "rf_traveler@test.com", role="traveler")
    admin_user = create_user(db_session, "rf_admin@test.com", role="admin")

    # Booking 1: Total 10,000.00, paid. Has processed partial PaymentRefund of 3,500.00
    b1 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("10000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="ECO-RF-001", provider_id=prof.id,
    )
    ref1 = db_session.query(BookingReference).filter(BookingReference.target_booking_id == b1.id).first()
    tx1 = PaymentTransaction(
        booking_reference_id=ref1.id,
        user_id=traveler.id,
        amount=Decimal("10000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
        paid_at=datetime.now(UTC),
    )
    db_session.add(tx1)
    db_session.flush()

    rf1 = PaymentRefund(
        payment_transaction_id=tx1.id,
        amount=Decimal("3500.00"),  # Partial refund
        currency="LKR",
        refund_reason="Guest cancelled optional boat ride segment",
        refund_status="processed",  # Processed -> authoritative!
    )
    db_session.add(rf1)

    # Booking 2: Total 4,000.00, payment_status='refunded'. NO PaymentRefund record -> fallback test
    b2 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("4000.00"),
        booking_status="cancelled", payment_status="refunded", payment_method="online",
        reference_code="ECO-RF-002", provider_id=prof.id,
    )

    # Booking 3: Total 6,000.00, paid. Has PENDING PaymentRefund -> should NOT be counted
    b3 = create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("6000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="ECO-RF-003", provider_id=prof.id,
    )
    ref3 = db_session.query(BookingReference).filter(BookingReference.target_booking_id == b3.id).first()
    tx3 = PaymentTransaction(
        booking_reference_id=ref3.id,
        user_id=traveler.id,
        amount=Decimal("6000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
        paid_at=datetime.now(UTC),
    )
    db_session.add(tx3)
    db_session.flush()

    rf3 = PaymentRefund(
        payment_transaction_id=tx3.id,
        amount=Decimal("6000.00"),
        currency="LKR",
        refund_reason="Pending bank review",
        refund_status="pending",  # Pending -> NOT counted
    )
    db_session.add(rf3)

    # Booking 4: Generic M03 booking (eco_experience_id is NULL) with 8,000.00 processed refund -> CROSS-MODULE ISOLATION
    b4 = create_booking(
        db_session, eco_experience_id=None, user_id=traveler.id,
        total_price=Decimal("8000.00"),
        booking_status="cancelled", payment_status="refunded", payment_method="online",
        reference_code="M03-GENERIC-004", provider_id=prof.id,
    )
    ref4 = db_session.query(BookingReference).filter(BookingReference.target_booking_id == b4.id).first()
    tx4 = PaymentTransaction(
        booking_reference_id=ref4.id,
        user_id=traveler.id,
        amount=Decimal("8000.00"),
        currency="LKR",
        payment_status="paid",
        payment_method="online",
        paid_at=datetime.now(UTC),
    )
    db_session.add(tx4)
    db_session.flush()

    rf4 = PaymentRefund(
        payment_transaction_id=tx4.id,
        amount=Decimal("8000.00"),
        currency="LKR",
        refund_reason="Module 03 refund",
        refund_status="processed",
    )
    db_session.add(rf4)
    db_session.commit()

    headers_p = auth_header(provider_user)
    headers_a = auth_header(admin_user)

    # Expected Eco refund total = 3500.00 (processed) + 4000.00 (fallback) = 7500.00
    # Pending 6000.00 and generic 8000.00 are strictly excluded.
    # Booking 1 total_price (10,000.00) is never double counted in fallback.

    # 1. Provider Listing Summary (P01)
    resp_p01 = client.get("/api/v1/eco-experiences/me/summary", headers=headers_p)
    assert resp_p01.status_code == 200
    assert resp_p01.json()["refunded_amount"] == "7500.00"

    # 2. Provider Earnings Summary (P14)
    resp_p14 = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    assert resp_p14.status_code == 200
    assert resp_p14.json()["refunded_amount"] == "7500.00"

    # 3. Admin Overview (A08)
    resp_a08 = client.get("/api/v1/admin/eco-experiences/overview", headers=headers_a)
    assert resp_a08.status_code == 200
    assert resp_a08.json()["financial_metrics"]["refunded_amount"] == "7500.00"

    # 4. Admin Payment Audit (A06)
    resp_a06 = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    assert resp_a06.status_code == 200
    assert resp_a06.json()["summary"]["total_refunded_volume"] == "7500.00"


def test_commission_reporting_and_liability_separation(
    client: TestClient, db_session: Session
):
    """
    Verifies Phase 5A Financial Correction #2:
    - 10% commission rate is explicitly labeled and returned as estimated_platform_commission.
    - Contractual platform fee deductions are tracked via recorded_platform_fees from dbo.provider_settlements.
    - Individual payment audit items report estimated_platform_commission.
    - Backwards compatibility alias platform_commission remains consistent with estimated_platform_commission.
    """
    provider_user = create_user(db_session, "comm_provider@test.com", role="provider")
    prof = create_provider(db_session, provider_user)
    exp = create_experience(db_session, prof.id, "Commission Test Trek")
    traveler = create_user(db_session, "comm_traveler@test.com", role="traveler")
    admin_user = create_user(db_session, "comm_admin@test.com", role="admin")

    create_booking(
        db_session, exp.id, traveler.id,
        total_price=Decimal("20000.00"),
        booking_status="confirmed", payment_status="paid", payment_method="online",
        reference_code="ECO-COMM-001", provider_id=prof.id,
    )

    headers_p = auth_header(provider_user)
    headers_a = auth_header(admin_user)

    # Provider earnings check
    resp_p = client.get("/api/v1/eco-experiences/me/earnings/summary", headers=headers_p)
    assert resp_p.status_code == 200
    p = resp_p.json()
    assert p["gross_earnings"] == "20000.00"
    assert p["estimated_platform_commission"] == "2000.00"
    assert p["platform_commission"] == "2000.00"
    assert p["recorded_platform_fees"] == "0.00"  # No settlements recorded yet

    # Admin payments audit check
    resp_audit = client.get("/api/v1/admin/eco-experiences/payments/audit", headers=headers_a)
    assert resp_audit.status_code == 200
    audit_data = resp_audit.json()
    assert audit_data["summary"]["estimated_platform_commission"] == "2000.00"
    assert audit_data["summary"]["total_platform_commission"] == "2000.00"
    assert audit_data["summary"]["recorded_platform_fees"] == "0.00"
    assert len(audit_data["items"]) == 1
    assert audit_data["items"][0]["estimated_platform_commission"] == "2000.00"
    assert audit_data["items"][0]["platform_commission"] == "2000.00"
    assert audit_data["items"][0]["provider_share"] == "18000.00"


