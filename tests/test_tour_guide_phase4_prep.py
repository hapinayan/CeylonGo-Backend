"""
Comprehensive Phase 4 Preparation Automated Tests for Local Tour Guide Module 05:
Mocked PayHere Payment Preparation, Security, IPN Callbacks, Duplicate Guards,
Ownership/RBAC, Commission Calculations, Refund Eligibility & Migration 034 Verification.

IMPORTANT:
- Zero real payment transactions are executed.
- app/routers/payments.py is NOT modified.
- Migration 034 SQL is verified offline only (never applied to any database).
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.payhere import payhere_service
from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    Notification,
    PaymentRefund,
    PaymentTransaction,
    ProviderSettlement,
)
from app.models.tour_guide import (
    TourGuide,
    TourGuideBooking,
    TourGuidePackage,
)
from app.models.user import Base, User
from app.services.tour_guide_payment import (
    calculate_tour_guide_commission,
    handle_tour_guide_payhere_callback,
    sync_tour_guide_refund_completion,
)
from scripts.migrate import discover_migrations, split_sql_batches

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
PROVIDER_EARNINGS_URL = "/api/v1/provider/tour-guides/earnings"


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def sign_payhere_ipn(
    *,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str = "LKR",
    status_code: str = "2",
    payment_id: str = "PH-TX-90001",
    merchant_id: str | None = None,
    merchant_secret: str | None = None,
) -> dict[str, str]:
    m_id = merchant_id if merchant_id is not None else payhere_service.merchant_id
    m_sec = merchant_secret if merchant_secret is not None else payhere_service.merchant_secret
    secret_hash = hashlib.md5(m_sec.encode("utf-8")).hexdigest().upper()
    raw = f"{m_id}{order_id}{payhere_amount}{payhere_currency}{status_code}{secret_hash}"
    sig = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    return {
        "merchant_id": m_id,
        "order_id": order_id,
        "payment_id": payment_id,
        "payhere_amount": payhere_amount,
        "payhere_currency": payhere_currency,
        "status_code": status_code,
        "md5sig": sig,
    }


def _future_date(days: int = 6) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


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
def payment_env(db_session: Session):
    admin = User(
        full_name="Admin Finance",
        email="admin.p4@example.com",
        role="admin",
        is_active=True,
    )
    traveler1 = User(
        full_name="Maya Perera",
        email="maya.p4@example.com",
        role="traveler",
        is_active=True,
    )
    traveler2 = User(
        full_name="Other Traveler",
        email="other.p4@example.com",
        role="traveler",
        is_active=True,
    )
    provider1_user = User(
        full_name="Saman Guide",
        email="saman.p4@example.com",
        role="provider",
        is_active=True,
    )
    provider2_user = User(
        full_name="Ruwan Guide",
        email="ruwan.p4@example.com",
        role="provider",
        is_active=True,
    )
    db_session.add_all(
        [admin, traveler1, traveler2, provider1_user, provider2_user]
    )
    db_session.flush()

    prof1 = ProviderProfile(
        user_id=provider1_user.id,
        business_name="Sigiriya Heritage Guides",
        provider_type="tour_guide",
        phone="0771230001",
        district="Matale",
        nic_number="198512345601",
        approval_status="approved",
    )
    prof2 = ProviderProfile(
        user_id=provider2_user.id,
        business_name="Ella Nature Trails",
        provider_type="tour_guide",
        phone="0771230002",
        district="Badulla",
        nic_number="198612345602",
        approval_status="approved",
    )
    db_session.add_all([prof1, prof2])
    db_session.flush()

    guide1 = TourGuide(
        provider_profile_id=prof1.id,
        bio="Licensed Sigiriya & Dambulla national guide.",
        years_of_experience=10,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("20000.00"),
        hourly_rate=Decimal("3500.00"),
        currency="LKR",
        base_city="Sigiriya",
        base_district="Matale",
        max_group_size=8,
        cancellation_policy="Free cancellation up to 24 hours before tour start.",
        free_cancellation_hours=24,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    guide2 = TourGuide(
        provider_profile_id=prof2.id,
        bio="Ella mountain hiking guide.",
        years_of_experience=4,
        specialties="Nature & Wildlife",
        daily_rate=Decimal("12000.00"),
        hourly_rate=Decimal("2500.00"),
        currency="LKR",
        base_city="Ella",
        base_district="Badulla",
        max_group_size=6,
        is_available=True,
        approval_status="approved",
        verification_status="unverified",
        is_active=True,
    )
    db_session.add_all([guide1, guide2])
    db_session.flush()

    pkg1 = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Sigiriya Sunrise Rock Tour",
        description="Guided climb and archaeological tour.",
        duration_hours=Decimal("4.00"),
        price=Decimal("10000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Sigiriya Main Entrance Gate",
        is_active=True,
    )
    db_session.add(pkg1)
    db_session.commit()

    return {
        "admin": admin,
        "traveler1": traveler1,
        "traveler2": traveler2,
        "provider1_user": provider1_user,
        "provider2_user": provider2_user,
        "prof1": prof1,
        "guide1": guide1,
        "guide2": guide2,
        "pkg1": pkg1,
    }


def _create_and_accept_booking(
    client: TestClient,
    env: dict,
    *,
    payment_method: str = "online",
    days_ahead: int = 6,
    accept: bool = True,
) -> dict:
    created = client.post(
        f"{PUBLIC_BASE}/{env['guide1'].id}/bookings",
        headers=bearer(env["traveler1"]),
        json={
            "rate_type": "package",
            "package_id": env["pkg1"].id,
            "booking_date": _future_date(days_ahead).isoformat(),
            "start_time": "08:00:00",
            "number_of_people": 2,
            "payment_method": payment_method,
        },
    )
    assert created.status_code == 201, created.text
    booking = created.json()
    if accept:
        acc = client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{booking['id']}/accept",
            headers=bearer(env["provider1_user"]),
        )
        assert acc.status_code == 200, acc.text
        booking = acc.json()
    return booking


# =============================================================================
# 1. CHECKOUT PREPARATION, OWNERSHIP, VALIDATION & IDEMPOTENCY
# =============================================================================

def test_payment_preparation_ownership_validation_and_idempotency(
    client: TestClient, db_session: Session, payment_env: dict
):
    pending_online = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=5, accept=False
    )
    confirmed_cash = _create_and_accept_booking(
        client, payment_env, payment_method="pay_at_location", days_ahead=6, accept=True
    )
    confirmed_online = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=7, accept=True
    )

    # Anonymous rejected (401)
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{confirmed_online['id']}/payment/prepare"
        ).status_code
        == 401
    )

    # Non-owning traveler and guide provider rejected (403)
    for actor in (payment_env["traveler2"], payment_env["provider1_user"]):
        assert (
            client.post(
                f"{TRAVELER_BOOKINGS_BASE}/{confirmed_online['id']}/payment/prepare",
                headers=bearer(actor),
            ).status_code
            == 403
        )

    # Pending (unapproved) booking rejected (400)
    r_pend = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{pending_online['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_pend.status_code == 400
    assert "pending guide approval" in r_pend.json()["detail"].lower()

    # Cash (pay_at_location) booking rejected (400) — never hits online gateway
    r_cash = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{confirmed_cash['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_cash.status_code == 400
    assert "pay_at_location" in r_cash.json()["detail"].lower()

    # Valid confirmed online booking -> signed PayHere checkout payload + 10% commission preview
    r_prep1 = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{confirmed_online['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_prep1.status_code == 200, r_prep1.text
    p1 = r_prep1.json()
    assert p1["booking_id"] == confirmed_online["id"]
    assert p1["order_id"] == confirmed_online["booking_reference"]
    assert Decimal(p1["amount"]) == Decimal("10000.00")
    assert p1["currency"] == "LKR"
    assert p1["hash"] == payhere_service.generate_checkout_hash(
        order_id=p1["order_id"], amount=Decimal("10000.00"), currency="LKR"
    )
    assert Decimal(p1["commission_preview"]["commission_rate"]) == Decimal("10.00")
    assert Decimal(p1["commission_preview"]["commission_amount"]) == Decimal("1000.00")
    assert Decimal(p1["commission_preview"]["provider_payable_amount"]) == Decimal("9000.00")

    # Idempotent retry reuses the same pending PaymentTransaction row
    r_prep2 = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{confirmed_online['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_prep2.status_code == 200
    assert r_prep2.json()["payment_transaction_id"] == p1["payment_transaction_id"]
    assert db_session.query(PaymentTransaction).count() == 1


# =============================================================================
# 2. MOCKED IPN CALLBACK SECURITY, SIGNATURES, DUPLICATES & STATE HANDLING
# =============================================================================

def test_mocked_ipn_security_invalid_signatures_tampering_and_duplicates(
    client: TestClient, db_session: Session, payment_env: dict
):
    booking = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=6, accept=True
    )
    prep = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    ).json()
    order_id = prep["order_id"]

    # 2A. Missing parameters -> 400
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data={"order_id": order_id})
    assert exc.value.status_code == 400

    # 2B. Invalid / tampered signature -> 400
    bad_sig = sign_payhere_ipn(order_id=order_id, payhere_amount="10000.00")
    bad_sig["md5sig"] = "DEADBEEF" * 4
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data=bad_sig)
    assert exc.value.status_code == 400
    assert "signature" in exc.value.detail.lower()

    # 2C. Spoofed merchant_id (even with self-consistent MD5) -> 400
    spoofed_merchant = sign_payhere_ipn(
        order_id=order_id,
        payhere_amount="10000.00",
        merchant_id="EVIL_MERCHANT_999",
    )
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data=spoofed_merchant)
    assert exc.value.status_code == 400
    assert "merchant" in exc.value.detail.lower()

    # 2D. Amount mismatch (underpayment attack) -> 400
    underpaid = sign_payhere_ipn(order_id=order_id, payhere_amount="1.00")
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data=underpaid)
    assert exc.value.status_code == 400
    assert "mismatch" in exc.value.detail.lower()

    # 2E. Currency mismatch -> 400
    wrong_currency = sign_payhere_ipn(
        order_id=order_id, payhere_amount="10000.00", payhere_currency="USD"
    )
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data=wrong_currency)
    assert exc.value.status_code == 400

    # 2F. Non-tour_guide BookingReference rejected -> 400
    homestay_ref = BookingReference(
        booking_reference="BK-HS-999-TEST",
        module_type="homestay",
        target_booking_id=999,
        user_id=payment_env["traveler1"].id,
        provider_profile_id=payment_env["prof1"].id,
        total_price=Decimal("10000.00"),
        currency="LKR",
        booking_status="confirmed",
        payment_status="unpaid",
    )
    db_session.add(homestay_ref)
    db_session.commit()
    wrong_module = sign_payhere_ipn(order_id="BK-HS-999-TEST", payhere_amount="10000.00")
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_payhere_callback(db_session, form_data=wrong_module)
    assert exc.value.status_code == 400
    assert "tour_guide" in exc.value.detail.lower()

    # 2G. Failed payment callback (-2) marks failed and allows retry
    failed_ipn = sign_payhere_ipn(
        order_id=order_id,
        payhere_amount="10000.00",
        status_code="-2",
        payment_id="PH-FAIL-01",
    )
    fail_res = handle_tour_guide_payhere_callback(db_session, form_data=failed_ipn)
    assert fail_res["status"] == "ok"
    assert fail_res["payment_status"] == "failed"

    # 2H. Valid success callback (status_code="2") confirms paid & writes 10%/90% snapshot
    success_ipn = sign_payhere_ipn(
        order_id=order_id,
        payhere_amount="10000.00",
        status_code="2",
        payment_id="PH-SUCCESS-777",
    )
    ok_res = handle_tour_guide_payhere_callback(db_session, form_data=success_ipn)
    assert ok_res["status"] == "ok"
    assert ok_res["payment_status"] == "paid"
    assert ok_res["transaction_status"] == "paid"

    orm_booking = db_session.get(TourGuideBooking, booking["id"])
    assert orm_booking.payment_status == "paid"
    tx = db_session.query(PaymentTransaction).filter_by(id=prep["payment_transaction_id"]).first()
    assert tx.payment_status == "paid"
    assert tx.gateway_transaction_id == "PH-SUCCESS-777"
    assert tx.commission_amount == Decimal("1000.00")
    assert tx.provider_payable_amount == Decimal("9000.00")
    assert tx.paid_at is not None

    history_count_after_first = db_session.query(BookingStatusHistory).count()
    tx_count_after_first = db_session.query(PaymentTransaction).count()

    # 2I. Duplicate callback replay -> idempotent 'already_processed', zero duplicate rows
    dup_res = handle_tour_guide_payhere_callback(db_session, form_data=success_ipn)
    assert dup_res["status"] == "already_processed"
    assert db_session.query(BookingStatusHistory).count() == history_count_after_first
    assert db_session.query(PaymentTransaction).count() == tx_count_after_first

    # 2J. Re-preparing an already-paid booking is blocked (400)
    r_reprep = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_reprep.status_code == 400


def test_late_ipn_callback_after_cancellation_flags_refund_without_reactivating(
    client: TestClient, db_session: Session, payment_env: dict
):
    booking = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=6, accept=True
    )
    prep = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    ).json()

    # Traveler cancels booking before PayHere callback arrives
    r_canc = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/cancel",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Trip cancelled before gateway callback arrived."},
    )
    assert r_canc.status_code == 200

    # Late PayHere success IPN arrives
    late_ipn = sign_payhere_ipn(
        order_id=prep["order_id"],
        payhere_amount="10000.00",
        status_code="2",
        payment_id="PH-LATE-888",
    )
    res = handle_tour_guide_payhere_callback(db_session, form_data=late_ipn)
    assert res["status"] == "ok"
    assert res["booking_status"] == "cancelled"  # MUST NOT reactivate!
    assert res["payment_status"] == "refund_pending"

    refunds = db_session.query(PaymentRefund).all()
    assert len(refunds) == 1
    assert refunds[0].amount == Decimal("10000.00")
    assert refunds[0].refund_status == "pending"

    # Replaying late callback is idempotent and does not duplicate refund row
    dup_late = handle_tour_guide_payhere_callback(db_session, form_data=late_ipn)
    assert dup_late["status"] == "already_processed"
    assert db_session.query(PaymentRefund).count() == 1


# =============================================================================
# 3. PAYMENT STATUS RBAC, REFUND ELIGIBILITY, REQUESTS & COMPLETION SYNC
# =============================================================================

def test_payment_status_rbac_refund_requests_and_completion_sync(
    client: TestClient, db_session: Session, payment_env: dict
):
    booking = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=6, accept=True
    )
    prep = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    ).json()
    handle_tour_guide_payhere_callback(
        db_session,
        form_data=sign_payhere_ipn(
            order_id=prep["order_id"],
            payhere_amount="10000.00",
            status_code="2",
            payment_id="PH-REF-100",
        ),
    )

    # Status RBAC: traveler sees status without internal commission split; provider & admin see split
    r_st_trav = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/status",
        headers=bearer(payment_env["traveler1"]),
    )
    assert r_st_trav.status_code == 200
    assert r_st_trav.json()["payment_status"] == "paid"
    assert r_st_trav.json()["commission_breakdown"] is None

    r_st_prov = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/status",
        headers=bearer(payment_env["provider1_user"]),
    )
    assert r_st_prov.status_code == 200
    assert Decimal(r_st_prov.json()["commission_breakdown"]["provider_payable_amount"]) == Decimal("9000.00")

    # Unrelated traveler & unrelated provider blocked (403)
    for outsider in (payment_env["traveler2"], payment_env["provider2_user"]):
        assert (
            client.get(
                f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/status",
                headers=bearer(outsider),
            ).status_code
            == 403
        )

    # Cannot request refund while booking is still active (400)
    r_active_ref = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/request-refund",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Want refund before cancelling."},
    )
    assert r_active_ref.status_code == 400

    # Cancel outside cutoff -> eligible for 100% refund
    r_canc = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/cancel",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Cancelling well before 24-hour cutoff."},
    )
    assert r_canc.status_code == 200

    # Other traveler cannot request refund for traveler1's booking (403)
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/request-refund",
            headers=bearer(payment_env["traveler2"]),
            json={"reason": "Unauthorized refund request."},
        ).status_code
        == 403
    )

    # Traveler requests refund -> 201 pending PaymentRefund
    r_ref = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/request-refund",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Please refund per 24-hour free cancellation policy."},
    )
    assert r_ref.status_code == 201, r_ref.text
    ref_data = r_ref.json()
    assert Decimal(ref_data["amount"]) == Decimal("10000.00")
    assert ref_data["refund_status"] == "pending"
    assert ref_data["refund_eligibility"]["eligible_refund_percent"] == 100

    # Duplicate refund request blocked (400)
    r_dup_ref = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/request-refund",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Trying to double-claim refund."},
    )
    assert r_dup_ref.status_code == 400

    # Simulate admin completing refund + calling Phase 4 completion sync hook
    orm_refund = db_session.get(PaymentRefund, ref_data["id"])
    orm_refund.refund_status = "completed"
    booking_ref = (
        db_session.query(BookingReference)
        .filter_by(module_type="tour_guide", target_booking_id=booking["id"])
        .first()
    )
    sync_tour_guide_refund_completion(
        db_session,
        booking_ref=booking_ref,
        refund=orm_refund,
        admin_id=payment_env["admin"].id,
    )
    db_session.commit()

    orm_b = db_session.get(TourGuideBooking, booking["id"])
    assert orm_b.payment_status == "refunded"
    assert booking_ref.payment_status == "refunded"

    # Late cancellation inside cutoff (0% eligible) cannot self-request online refund (400)
    soon = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=4)
    late_b = TourGuideBooking(
        tour_guide_id=payment_env["guide1"].id,
        traveler_id=payment_env["traveler1"].id,
        package_id=payment_env["pkg1"].id,
        booking_date=soon.date(),
        start_datetime=soon,
        end_datetime=soon + timedelta(hours=4),
        number_of_people=2,
        total_price=Decimal("10000.00"),
        currency="LKR",
        booking_status="cancelled",
        payment_method="online",
        payment_status="paid",
        free_cancellation_hours_snapshot=24,
        cancelled_by=payment_env["traveler1"].id,
        cancelled_at=datetime.now(UTC).replace(tzinfo=None),
        cancellation_reason="Last-minute change.",
    )
    db_session.add(late_b)
    db_session.commit()
    r_inelig = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{late_b.id}/request-refund",
        headers=bearer(payment_env["traveler1"]),
        json={"reason": "Requesting refund despite late cancellation."},
    )
    assert r_inelig.status_code == 400


# =============================================================================
# 4. COMMISSION ROUNDING & PROVIDER EARNINGS (ONLINE VS DIRECT CASH SEPARATION)
# =============================================================================

def test_commission_calculations_and_provider_earnings_cash_separation(
    client: TestClient, db_session: Session, payment_env: dict
):
    # Exact reconciliation across tricky decimal amounts
    for raw in ("10000.00", "999.99", "12345.67", "0.00"):
        br = calculate_tour_guide_commission(Decimal(raw))
        assert br.commission_amount + br.provider_payable_amount == br.gross_amount

    # Invalid inputs rejected
    with pytest.raises(HTTPException):
        calculate_tour_guide_commission(Decimal("-1.00"))
    with pytest.raises(HTTPException):
        calculate_tour_guide_commission(Decimal("100.00"), commission_rate=Decimal("105.00"))

    # Booking 1: Online paid + completed (10,000 -> 1,000 fee, 9,000 payable)
    b_done = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=6, accept=True
    )
    p_done = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_done['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    ).json()
    handle_tour_guide_payhere_callback(
        db_session,
        form_data=sign_payhere_ipn(
            order_id=p_done["order_id"],
            payhere_amount="10000.00",
            status_code="2",
            payment_id="PH-EARN-1",
        ),
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b_done['id']}/complete",
            headers=bearer(payment_env["provider1_user"]),
        ).status_code
        == 200
    )

    # Booking 2: Online paid + confirmed (not yet completed -> pending_online_earnings = 9,000)
    b_pend = _create_and_accept_booking(
        client, payment_env, payment_method="online", days_ahead=7, accept=True
    )
    p_pend = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{b_pend['id']}/payment/prepare",
        headers=bearer(payment_env["traveler1"]),
    ).json()
    handle_tour_guide_payhere_callback(
        db_session,
        form_data=sign_payhere_ipn(
            order_id=p_pend["order_id"],
            payhere_amount="10000.00",
            status_code="2",
            payment_id="PH-EARN-2",
        ),
    )

    # Booking 3: Direct cash paid (10,000 -> cash_collected_total ONLY, zero settlement impact)
    b_cash = _create_and_accept_booking(
        client, payment_env, payment_method="pay_at_location", days_ahead=8, accept=True
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b_cash['id']}/cash-received",
            headers=bearer(payment_env["provider1_user"]),
        ).status_code
        == 200
    )

    # Partial historical settlement of 3,000 LKR
    db_session.add(
        ProviderSettlement(
            provider_profile_id=payment_env["prof1"].id,
            period_start=_future_date(1),
            period_end=_future_date(10),
            gross_amount=Decimal("3333.33"),
            platform_fee=Decimal("333.33"),
            net_payable=Decimal("3000.00"),
            currency="LKR",
            settlement_status="paid",
        )
    )
    db_session.commit()

    r_earn = client.get(
        PROVIDER_EARNINGS_URL,
        headers=bearer(payment_env["provider1_user"]),
    )
    assert r_earn.status_code == 200, r_earn.text
    e = r_earn.json()
    assert Decimal(e["total_online_gross_earnings"]) == Decimal("20000.00")
    assert Decimal(e["completed_online_booking_earnings"]) == Decimal("10000.00")
    assert Decimal(e["ceylongo_commission"]) == Decimal("1000.00")
    assert Decimal(e["provider_net_online_earnings"]) == Decimal("9000.00")
    assert Decimal(e["pending_online_earnings"]) == Decimal("9000.00")
    assert Decimal(e["total_settled"]) == Decimal("3000.00")
    assert Decimal(e["available_settlement_balance"]) == Decimal("6000.00")
    assert e["total_paid_online_bookings"] == 2
    assert Decimal(e["cash_collected_total"]) == Decimal("10000.00")
    assert e["total_cash_paid_bookings"] == 1


# =============================================================================
# 5. OFFLINE MIGRATION 034 & ROLLBACK SQL VERIFICATION (NO DB EXECUTION)
# =============================================================================

def test_promoted_migration_035_and_036_sequence_parses_cleanly():
    repo_root = Path(__file__).resolve().parents[1]
    migrations_dir = repo_root / "migrations"
    discovered = discover_migrations(migrations_dir)

    # Confirm sequential 001..036 with zero gaps or duplicates (preserving 034_create_eco_reservations.sql)
    versions = [m.version for m in discovered]
    assert versions[:36] == [f"{i:03d}" for i in range(1, 37)]
    assert (migrations_dir / "034_create_eco_reservations.sql").exists()

    forward_path = migrations_dir / "035_extend_tour_guide_module_final_gaps.sql"
    rollback_path = (
        migrations_dir / "rollback" / "035_extend_tour_guide_module_final_gaps_rollback.sql"
    )
    assert forward_path.exists()
    assert rollback_path.exists()

    forward_sql = forward_path.read_text(encoding="utf-8")
    rollback_sql = rollback_path.read_text(encoding="utf-8")

    forward_batches = split_sql_batches(forward_sql)
    rollback_batches = split_sql_batches(rollback_sql)
    assert len(forward_batches) >= 7
    assert len(rollback_batches) >= 7

    # Every new column & constraint added in forward SQL is explicitly dropped in rollback SQL
    expected_columns = (
        "approved_at",
        "payment_expires_at",
        "cash_received_at",
        "commission_rate",
        "commission_amount",
        "provider_payable_amount",
        "package_type",
        "meeting_instructions",
        "special_requirements",
        "whatsapp_number",
        "allow_whatsapp_contact",
        "verified_years_of_experience",
    )
    for col in expected_columns:
        assert col in forward_sql, f"Missing column {col} in forward Migration 034"
        assert col in rollback_sql, f"Missing column {col} in rollback Migration 034"

    assert "'awaiting_payment'" in forward_sql
    assert "THROW 50340" in rollback_sql
