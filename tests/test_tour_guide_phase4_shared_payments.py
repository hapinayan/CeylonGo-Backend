"""
End-to-End Phase 4 Integration Tests for Shared Payment Hooks in app/routers/payments.py.

Verifies the 6 minimal additive Tour Guide integration hooks through the live
FastAPI routes (using mocked PayHere IPN form payloads — zero real payments):
1. POST /api/v1/payments/tour-guides/bookings/{booking_id}/initiate (alias route)
2. POST /api/v1/payments/payhere/notify (valid, invalid signature, spoofed merchant, amount mismatch, duplicate replay)
3. POST /api/v1/payments/admin/refunds/{refund_id}/process (Tour Guide refund completion sync)
4. POST /api/v1/payments/admin/settlements & GET /api/v1/payments/admin/summary (Tour Guide online settlement + cash exclusion)
5. GET  /api/v1/payments/admin/reconciliation-queue (Tour Guide late/cancelled paid transaction flagging)
6. SQL Server Migration 022 Tour Guide CHECK constraint compatibility check
"""

from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib

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
    PaymentRefund,
    PaymentTransaction,
)
from app.models.tour_guide import (
    TourGuide,
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
PAYMENTS_BASE = "/api/v1/payments"


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def sign_ipn(
    *,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str = "LKR",
    status_code: str = "2",
    payment_id: str = "PH-SHARED-001",
    merchant_id: str | None = None,
) -> dict[str, str]:
    m_id = merchant_id if merchant_id is not None else payhere_service.merchant_id
    secret_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
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
def env(db_session: Session):
    admin = User(
        full_name="Platform Admin",
        email="admin.p4s@example.com",
        role="admin",
        is_active=True,
    )
    traveler = User(
        full_name="Nadia Tourist",
        email="nadia.p4s@example.com",
        role="traveler",
        is_active=True,
    )
    provider_user = User(
        full_name="Chathura Guide",
        email="chathura.p4s@example.com",
        role="provider",
        is_active=True,
    )
    db_session.add_all([admin, traveler, provider_user])
    db_session.flush()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Anuradhapura Ancient Tours",
        provider_type="tour_guide",
        phone="0774445555",
        district="Anuradhapura",
        nic_number="198412345609",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.flush()

    guide = TourGuide(
        provider_profile_id=profile.id,
        bio="Licensed heritage tour guide in Anuradhapura.",
        years_of_experience=12,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("18000.00"),
        hourly_rate=Decimal("3000.00"),
        currency="LKR",
        base_city="Anuradhapura",
        base_district="Anuradhapura",
        max_group_size=6,
        cancellation_policy="Free cancellation up to 24 hours before start.",
        free_cancellation_hours=24,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    db_session.add(guide)
    db_session.flush()

    pkg = TourGuidePackage(
        tour_guide_id=guide.id,
        title="Sacred City Full Circuit",
        description="Half-day guided archaeological tour.",
        duration_hours=Decimal("4.00"),
        price=Decimal("10000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Sri Maha Bodhi Main Gate",
        is_active=True,
    )
    db_session.add(pkg)
    db_session.commit()

    return {
        "admin": admin,
        "traveler": traveler,
        "provider_user": provider_user,
        "profile": profile,
        "guide": guide,
        "pkg": pkg,
    }


def _book_and_accept(
    client: TestClient, env: dict, *, payment_method: str = "online", days_ahead: int = 6
) -> dict:
    created = client.post(
        f"{PUBLIC_BASE}/{env['guide'].id}/bookings",
        headers=bearer(env["traveler"]),
        json={
            "rate_type": "package",
            "package_id": env["pkg"].id,
            "booking_date": _future_date(days_ahead).isoformat(),
            "start_time": "08:00:00",
            "number_of_people": 2,
            "payment_method": payment_method,
        },
    )
    assert created.status_code == 201, created.text
    b = created.json()
    acc = client.post(
        f"{PROVIDER_BOOKINGS_BASE}/{b['id']}/accept",
        headers=bearer(env["provider_user"]),
    )
    assert acc.status_code == 200, acc.text
    return acc.json()


# =============================================================================
# 1. SHARED INITIATE ALIAS + /api/v1/payments/payhere/notify WEBHOOK FLOW
# =============================================================================

def test_shared_initiate_alias_and_payhere_webhook_security_and_idempotency(
    client: TestClient, db_session: Session, env: dict
):
    booking = _book_and_accept(client, env, payment_method="online", days_ahead=6)

    # Initiate via shared alias route /api/v1/payments/tour-guides/bookings/{id}/initiate
    r_init = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{booking['id']}/initiate",
        headers=bearer(env["traveler"]),
    )
    assert r_init.status_code == 200, r_init.text
    init_data = r_init.json()
    order_id = init_data["order_id"]
    assert init_data["booking_id"] == booking["id"]
    assert Decimal(init_data["amount"]) == Decimal("10000.00")

    # Invalid signature via live shared webhook -> 400
    bad_sig = sign_ipn(order_id=order_id, payhere_amount="10000.00")
    bad_sig["md5sig"] = "0" * 32
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=bad_sig).status_code == 400
    )

    # Spoofed merchant_id -> 400
    spoofed = sign_ipn(
        order_id=order_id, payhere_amount="10000.00", merchant_id="FAKE_MERCHANT"
    )
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=spoofed).status_code == 400
    )

    # Amount tampering -> 400
    tampered = sign_ipn(order_id=order_id, payhere_amount="50.00")
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=tampered).status_code == 400
    )

    # Valid PayHere success callback -> 200 & paid with 10% commission / 90% payable
    valid_ipn = sign_ipn(
        order_id=order_id,
        payhere_amount="10000.00",
        status_code="2",
        payment_id="PH-LIVE-MOCK-101",
    )
    r_ok = client.post(f"{PAYMENTS_BASE}/payhere/notify", data=valid_ipn)
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["payment_status"] == "paid"

    orm_b = db_session.get(TourGuideBooking, booking["id"])
    assert orm_b.booking_status == "confirmed"
    assert orm_b.payment_status == "paid"

    tx = db_session.query(PaymentTransaction).first()
    assert tx is not None
    assert tx.gateway_transaction_id == "PH-LIVE-MOCK-101"
    assert tx.commission_amount == Decimal("1000.00")
    assert tx.provider_payable_amount == Decimal("9000.00")

    hist_count = db_session.query(BookingStatusHistory).count()

    # Duplicate callback replay -> 200 already_processed, zero duplicate writes
    r_dup = client.post(f"{PAYMENTS_BASE}/payhere/notify", data=valid_ipn)
    assert r_dup.status_code == 200
    assert r_dup.json()["status"] == "already_processed"
    assert db_session.query(BookingStatusHistory).count() == hist_count
    assert db_session.query(PaymentTransaction).count() == 1


# =============================================================================
# 2. SHARED ADMIN REFUND PROCESSING & TOUR GUIDE BOOKING SYNC
# =============================================================================

def test_shared_admin_refund_processing_syncs_tour_guide_booking_to_refunded(
    client: TestClient, db_session: Session, env: dict
):
    booking = _book_and_accept(client, env, payment_method="online", days_ahead=7)
    init = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{booking['id']}/initiate",
        headers=bearer(env["traveler"]),
    ).json()
    assert (
        client.post(
            f"{PAYMENTS_BASE}/payhere/notify",
            data=sign_ipn(
                order_id=init["order_id"],
                payhere_amount="10000.00",
                payment_id="PH-REF-SYNC-202",
            ),
        ).status_code
        == 200
    )

    # Cancel outside cutoff & submit refund request
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/cancel",
            headers=bearer(env["traveler"]),
            json={"reason": "Flight cancelled well ahead of 24-hour window."},
        ).status_code
        == 200
    )
    r_req = client.post(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/request-refund",
        headers=bearer(env["traveler"]),
        json={"reason": "Eligible free cancellation refund request."},
    )
    assert r_req.status_code == 201, r_req.text
    refund_id = r_req.json()["id"]

    # Admin marks refund completed via shared /api/v1/payments/admin/refunds/{id}/process
    r_proc = client.post(
        f"{PAYMENTS_BASE}/admin/refunds/{refund_id}/process",
        headers=bearer(env["admin"]),
        json={
            "status": "completed",
            "notes": "PayHere Refund Ref PH-RF-998811 verified",
        },
    )
    assert r_proc.status_code == 200, r_proc.text
    assert r_proc.json()["refund_status"] == "completed"

    # TourGuideBooking & BookingReference synchronized to 'refunded'
    orm_b = db_session.get(TourGuideBooking, booking["id"])
    db_session.refresh(orm_b)
    assert orm_b.payment_status == "refunded"

    ref = (
        db_session.query(BookingReference)
        .filter_by(module_type="tour_guide", target_booking_id=booking["id"])
        .first()
    )
    assert ref.payment_status == "refunded"

    # Payment status endpoint reflects completed refund
    r_st = client.get(
        f"{TRAVELER_BOOKINGS_BASE}/{booking['id']}/payment/status",
        headers=bearer(env["traveler"]),
    )
    assert r_st.status_code == 200
    assert r_st.json()["payment_status"] == "refunded"
    assert r_st.json()["refunds"][0]["refund_status"] == "completed"


# =============================================================================
# 3. SHARED ADMIN SETTLEMENTS, FINANCIAL SUMMARY & RECONCILIATION QUEUE
# =============================================================================

def test_shared_settlements_financial_summary_and_reconciliation_queue(
    client: TestClient, db_session: Session, env: dict
):
    # A. Online paid + completed booking (10,000 gross -> 1,000 commission, 9,000 payable)
    b_online = _book_and_accept(client, env, payment_method="online", days_ahead=6)
    init1 = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{b_online['id']}/initiate",
        headers=bearer(env["traveler"]),
    ).json()
    assert (
        client.post(
            f"{PAYMENTS_BASE}/payhere/notify",
            data=sign_ipn(
                order_id=init1["order_id"],
                payhere_amount="10000.00",
                payment_id="PH-SETTLE-301",
            ),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b_online['id']}/complete",
            headers=bearer(env["provider_user"]),
        ).status_code
        == 200
    )

    # B. Direct cash paid + completed booking (10,000 -> MUST NOT inflate platform settlement!)
    b_cash = _book_and_accept(client, env, payment_method="pay_at_location", days_ahead=7)
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b_cash['id']}/cash-received",
            headers=bearer(env["provider_user"]),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b_cash['id']}/complete",
            headers=bearer(env["provider_user"]),
        ).status_code
        == 200
    )

    # Admin financial summary includes Tour Guide online liability (9,000) and excludes cash
    r_sum = client.get(f"{PAYMENTS_BASE}/admin/summary", headers=bearer(env["admin"]))
    assert r_sum.status_code == 200, r_sum.text
    summary = r_sum.json()
    assert Decimal(summary["total_gross_booking_revenue"]) == Decimal("10000.00")
    assert Decimal(summary["total_ceylongo_commission"]) == Decimal("1000.00")
    assert Decimal(summary["total_provider_liabilities"]) == Decimal("9000.00")

    # Over-settlement attempt (trying to include direct cash) blocked with 400
    p_start = _future_date(1).isoformat()
    p_end = _future_date(15).isoformat()
    r_over = client.post(
        f"{PAYMENTS_BASE}/admin/settlements",
        headers=bearer(env["admin"]),
        json={
            "provider_profile_id": env["profile"].id,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "20000.00",
            "platform_fee": "2000.00",
            "net_payable": "18000.00",
            "settlement_status": "pending",
        },
    )
    assert r_over.status_code == 400

    # Valid settlement for verified 9,000.00 online balance succeeds (201)
    r_settle = client.post(
        f"{PAYMENTS_BASE}/admin/settlements",
        headers=bearer(env["admin"]),
        json={
            "provider_profile_id": env["profile"].id,
            "period_start": p_start,
            "period_end": p_end,
            "gross_amount": "10000.00",
            "platform_fee": "1000.00",
            "net_payable": "9000.00",
            "payout_reference": "BANK-TX-TG-554433",
            "settlement_status": "completed",
        },
    )
    assert r_settle.status_code == 201, r_settle.text
    assert Decimal(r_settle.json()["net_payable"]) == Decimal("9000.00")

    # Provider Tour Guide earnings now show 0.00 available settlement balance & 10,000 cash separate
    r_earn = client.get(
        "/api/v1/provider/tour-guides/earnings",
        headers=bearer(env["provider_user"]),
    )
    assert r_earn.status_code == 200
    assert Decimal(r_earn.json()["available_settlement_balance"]) == Decimal("0.00")
    assert Decimal(r_earn.json()["total_settled"]) == Decimal("9000.00")
    assert Decimal(r_earn.json()["cash_collected_total"]) == Decimal("10000.00")

    # C. Late payment on cancelled Tour Guide booking appears in admin reconciliation queue
    b_late = _book_and_accept(client, env, payment_method="online", days_ahead=9)
    init_late = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{b_late['id']}/initiate",
        headers=bearer(env["traveler"]),
    ).json()
    assert (
        client.post(
            f"{TRAVELER_BOOKINGS_BASE}/{b_late['id']}/cancel",
            headers=bearer(env["traveler"]),
            json={"reason": "Cancelled right before gateway callback arrived."},
        ).status_code
        == 200
    )
    r_late_cb = client.post(
        f"{PAYMENTS_BASE}/payhere/notify",
        data=sign_ipn(
            order_id=init_late["order_id"],
            payhere_amount="10000.00",
            payment_id="PH-LATE-RECON-404",
        ),
    )
    assert r_late_cb.status_code == 200
    assert r_late_cb.json()["booking_status"] == "cancelled"
    assert r_late_cb.json()["payment_status"] == "refund_pending"

    r_recon = client.get(
        f"{PAYMENTS_BASE}/admin/reconciliation-queue",
        headers=bearer(env["admin"]),
    )
    assert r_recon.status_code == 200, r_recon.text
    recon_ids = {t["gateway_transaction_id"] for t in r_recon.json()}
    assert "PH-LATE-RECON-404" in recon_ids
    assert "PH-SETTLE-301" not in recon_ids


# =============================================================================
# 4. MIGRATION 022 TOUR GUIDE CHECK CONSTRAINT COMPATIBILITY VERIFICATION
# =============================================================================

def test_tour_guide_booking_values_comply_with_migration_022_check_constraints(
    client: TestClient, db_session: Session, env: dict
):
    allowed_booking_statuses = {
        "pending",
        "confirmed",
        "in_progress",
        "completed",
        "cancelled",
        "rejected",
        "expired",
    }
    allowed_payment_methods = {"online", "pay_at_location"}
    allowed_payment_statuses = {
        "unpaid",
        "pending",
        "paid",
        "refund_pending",
        "refunded",
        "failed",
    }

    b = _book_and_accept(client, env, payment_method="online", days_ahead=8)
    init = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{b['id']}/initiate",
        headers=bearer(env["traveler"]),
    ).json()
    client.post(
        f"{PAYMENTS_BASE}/payhere/notify",
        data=sign_ipn(
            order_id=init["order_id"],
            payhere_amount="10000.00",
            payment_id="PH-CK-505",
        ),
    )
    for row in db_session.query(TourGuideBooking).all():
        assert row.booking_status in allowed_booking_statuses
        assert row.payment_method in allowed_payment_methods
        assert row.payment_status in allowed_payment_statuses


# =============================================================================
# 5. LIVE /api/v1/payments/payhere/notify AD-TG ADVERTISING DELEGATION (PR-19)
# =============================================================================

def test_shared_payhere_notify_handles_tour_guide_ad_tg_and_preserves_homestay_ad(
    client: TestClient, db_session: Session, env: dict
):
    from app.models.shared import AdvertisingPayment, PromotionCampaign

    guide = env["guide"]
    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=10)).replace(tzinfo=None).isoformat()

    # Provider creates campaign & prepares AD-TG- PayHere order
    r_camp = client.post(
        "/api/v1/provider/tour-guides/promotions/campaigns",
        headers=bearer(env["provider_user"]),
        json={
            "campaign_name": "Live Shared Webhook AD-TG Campaign",
            "budget": "6500.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": True,
        },
    )
    assert r_camp.status_code == 201, r_camp.text
    camp_id = r_camp.json()["id"]

    r_prep = client.post(
        f"/api/v1/provider/tour-guides/promotions/campaigns/{camp_id}/payment/prepare",
        headers=bearer(env["provider_user"]),
    )
    assert r_prep.status_code == 200, r_prep.text
    order_ref = r_prep.json()["order_reference"]
    assert order_ref.startswith("AD-TG-")

    # 1. Invalid MD5 signature -> 400
    bad_sig = sign_ipn(order_id=order_ref, payhere_amount="6500.00")
    bad_sig["md5sig"] = "A" * 32
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=bad_sig).status_code == 400
    )

    # 2. Spoofed merchant_id -> 400
    spoofed = sign_ipn(
        order_id=order_ref, payhere_amount="6500.00", merchant_id="EVIL_MERCHANT"
    )
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=spoofed).status_code == 400
    )

    # 3. Amount tampering -> 400
    tampered = sign_ipn(order_id=order_ref, payhere_amount="1.00")
    assert (
        client.post(f"{PAYMENTS_BASE}/payhere/notify", data=tampered).status_code == 400
    )

    # 4. Valid AD-TG- callback via live shared /api/v1/payments/payhere/notify -> activates campaign
    valid_ad_ipn = sign_ipn(
        order_id=order_ref,
        payhere_amount="6500.00",
        status_code="2",
        payment_id="PH-ADTG-LIVE-777",
    )
    r_ok = client.post(f"{PAYMENTS_BASE}/payhere/notify", data=valid_ad_ipn)
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["status"] == "ok"
    assert r_ok.json()["campaign_status"] == "active"
    assert r_ok.json()["payment_status"] == "completed"

    orm_camp = db_session.get(PromotionCampaign, camp_id)
    assert orm_camp.status == "active"
    assert orm_camp.spent_amount == Decimal("6500.00")
    orm_ad_pay = (
        db_session.query(AdvertisingPayment)
        .filter_by(order_reference=order_ref)
        .first()
    )
    assert orm_ad_pay.payment_status == "completed"
    assert orm_ad_pay.gateway_transaction_id == "PH-ADTG-LIVE-777"

    # 5. Duplicate callback replay -> 200 already_processed
    r_dup = client.post(f"{PAYMENTS_BASE}/payhere/notify", data=valid_ad_ipn)
    assert r_dup.status_code == 200
    assert r_dup.json()["status"] == "already_processed"

    # 6. Public guide discovery immediately reflects sponsored badge
    r_disc = client.get(PUBLIC_BASE)
    assert r_disc.status_code == 200
    assert any(
        g["id"] == guide.id and g["is_sponsored"] is True
        for g in r_disc.json()["items"]
    )

    # 7. Existing Homestay AD- callback path remains completely preserved
    hs_ipn = sign_ipn(
        order_id="AD-HOMESTAY-NONEXISTENT-999",
        payhere_amount="2500.00",
        status_code="2",
        payment_id="PH-HS-999",
    )
    r_hs = client.post(f"{PAYMENTS_BASE}/payhere/notify", data=hs_ipn)
    assert r_hs.status_code == 404
    assert "Advertising payment order reference" in r_hs.json()["detail"]


def test_dual_tour_guide_webhook_routing_preserves_both_pr25_and_module05(
    client: TestClient, db_session: Session, env: dict
):
    """Verify explicit PayHere routing preserves BOTH:
    - PR #25 unified provider_reservations ('RS-*') -> app.services.guide_payments.handle_guide_callback
    - Module 05 tour_guide_bookings ('BK-TG-*')     -> app.services.tour_guide_payment.handle_tour_guide_payhere_callback
    """
    from app.models.shared import BookingReference, PaymentTransaction
    from app.models.tour_guide import TourGuideAvailability

    # Seed explicit full-day availability window required by PR #25's /api/v1/tour-guide-reservations
    db_session.add(
        TourGuideAvailability(
            tour_guide_id=env["guide"].id,
            available_date=_future_date(8),
            is_available=True,
        )
    )
    db_session.commit()

    # 1. PR #25 reservation booking ('RS-*') created via /api/v1/tour-guide-reservations
    start_dt = datetime.combine(_future_date(8), time(9, 0), tzinfo=timezone(timedelta(hours=5, minutes=30)))
    r_pr25 = client.post(
        "/api/v1/tour-guide-reservations",
        headers=bearer(env["traveler"]),
        json={
            "guide_id": env["guide"].id,
            "package_id": env["pkg"].id,
            "people": 2,
            "starts_at": start_dt.isoformat(),
            "payment_method": "online",
        },
    )
    assert r_pr25.status_code == 201, r_pr25.text
    pr25_id = r_pr25.json()["id"]

    # Provider accepts via PR #25 unified endpoint -> starts online hold (awaiting_payment)
    r_pr25_acc = client.post(
        f"/api/v1/provider/bookings/{pr25_id}/action",
        headers=bearer(env["provider_user"]),
        json={"action": "accept"},
    )
    assert r_pr25_acc.status_code == 200, r_pr25_acc.text

    r_pr25_init = client.post(
        f"/api/v1/tour-guide-reservations/{pr25_id}/payments/initiate",
        headers=bearer(env["traveler"]),
    )
    assert r_pr25_init.status_code == 200, r_pr25_init.text
    pr25_order = r_pr25_init.json()["order_id"]
    assert pr25_order.startswith("RS-")

    r_pr25_cb = client.post(
        f"{PAYMENTS_BASE}/payhere/notify",
        data=sign_ipn(
            order_id=pr25_order,
            payhere_amount="10000.00",
            payment_id="PH-PR25-DUAL-001",
        ),
    )
    assert r_pr25_cb.status_code == 200, r_pr25_cb.text
    assert r_pr25_cb.json()["payment_status"] == "paid"

    r_pr25_st = client.get(
        f"/api/v1/tour-guide-reservations/{pr25_id}/payments/status",
        headers=bearer(env["traveler"]),
    )
    assert r_pr25_st.status_code == 200
    assert r_pr25_st.json()["booking_status"] == "confirmed"
    assert r_pr25_st.json()["payment_status"] == "paid"

    # 2. Module 05 booking ('BK-TG-*') on a separate date routes to Module 05 handler
    m05_booking = _book_and_accept(client, env, payment_method="online", days_ahead=10)
    r_m05_init = client.post(
        f"{PAYMENTS_BASE}/tour-guides/bookings/{m05_booking['id']}/initiate",
        headers=bearer(env["traveler"]),
    )
    assert r_m05_init.status_code == 200, r_m05_init.text
    m05_order = r_m05_init.json()["order_id"]
    assert m05_order.startswith("BK-TG-")

    r_m05_cb = client.post(
        f"{PAYMENTS_BASE}/payhere/notify",
        data=sign_ipn(
            order_id=m05_order,
            payhere_amount="10000.00",
            payment_id="PH-M05-DUAL-002",
        ),
    )
    assert r_m05_cb.status_code == 200, r_m05_cb.text
    assert r_m05_cb.json()["payment_status"] == "paid"

    # Verify both references coexist cleanly in dbo.booking_references & dbo.payment_transactions
    refs = (
        db_session.query(BookingReference)
        .filter(BookingReference.booking_reference.in_([pr25_order, m05_order]))
        .all()
    )
    assert len(refs) == 2
    assert {r.payment_status for r in refs} == {"paid"}
    txs = (
        db_session.query(PaymentTransaction)
        .filter(PaymentTransaction.booking_reference_id.in_([r.id for r in refs]))
        .all()
    )
    assert len(txs) == 2
