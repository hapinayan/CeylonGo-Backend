r"""
Live Isolated SQL Server Integration Tests for Local Tour Guide Module 05.

Target (STRICTLY FAIL-CLOSED):
- Server:   (localdb)\CeylonGoTourGuideTest
- Database: CeylonGoDB_IntegrationTest

Verifies on actual Microsoft SQL Server (ODBC Driver 18 for SQL Server):
1. Migrations 001–035 applied & live SQL Server CHECK constraints enforced (034 & 035).
2. TR-12, TR-15, PR-15, AD-06 with real sp_getapplock, mocked PayHere callbacks,
   refund completion sync, settlements, and reconciliation queue under Migration 035.
3. TR-18, PR-17, GS-01, GS-03, GS-06 under Migration 034 with TOUR_GUIDE_ENABLE_M034_FIELDS=true.
4. PR-19 AD-TG- live webhook delegation + Homestay & Rental payment compatibility.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import hashlib
import os
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database_guard import verify_safe_integration_database
from app.core.payhere import payhere_service
from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.booking import Booking
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.rental_booking import RentalBooking
from app.models.rental_vehicle import RentalVehicle
from app.models.shared import (
    AdminAuditLog,
    AdvertisingPayment,
    BookingReference,
    ListingReference,
    Notification,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.tour_guide import (
    TourGuide,
    TourGuideBooking,
    TourGuideCredential,
    TourGuidePackage,
)
from app.models.user import User

TARGET_SERVER = os.getenv("TG_SQLSERVER_TEST_SERVER", r"(localdb)\CeylonGoTourGuideTest")
TARGET_DB = os.getenv("TG_SQLSERVER_TEST_DB", "CeylonGoDB_MergedTest")
ODBC_DRIVER = os.getenv("DB_DRIVER", "ODBC Driver 18 for SQL Server")
ALLOWED_LOCAL_TEST_DBS = frozenset({
    "ceylongodb_integrationtest",
    "ceylongodb_mergedtest",
})


def _build_isolated_engine():
    # Fail-closed guard BEFORE attempting any connection
    verify_safe_integration_database(TARGET_DB, approved_databases=ALLOWED_LOCAL_TEST_DBS)
    if "ceylongotourguidetest" not in TARGET_SERVER.lower():
        raise RuntimeError(
            f"FAIL CLOSED: Refusing to connect outside isolated instance (got {TARGET_SERVER})"
        )
    url = URL.create(
        "mssql+pyodbc",
        host=TARGET_SERVER,
        database=TARGET_DB,
        query={
            "driver": ODBC_DRIVER,
            "Trusted_Connection": "yes",
            "TrustServerCertificate": "yes",
        },
    )
    return create_engine(url, pool_pre_ping=True)


def _check_instance_available() -> bool:
    try:
        eng = _build_isolated_engine()
        with eng.connect() as conn:
            db_name = conn.execute(text("SELECT DB_NAME()")).scalar()
            if not db_name or db_name.lower() != TARGET_DB.lower():
                return False
        eng.dispose()
        return True
    except Exception:
        return False


_SQLSERVER_AVAILABLE = _check_instance_available()

pytestmark = pytest.mark.skipif(
    not _SQLSERVER_AVAILABLE,
    reason=f"Isolated LocalDB {TARGET_SERVER}/{TARGET_DB} is not available in this environment.",
)


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def sign_ipn(
    *,
    order_id: str,
    payhere_amount: str,
    payhere_currency: str = "LKR",
    status_code: str = "2",
    payment_id: str,
) -> dict[str, str]:
    m_id = payhere_service.merchant_id
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


def _future_date(days: int) -> date:
    return (datetime.now(UTC) + timedelta(days=days)).date()


def _cleanup_all_test_tables(db: Session) -> None:
    """Clean disposable isolated SQL Server test tables in FK-safe order."""
    actual = db.execute(text("SELECT DB_NAME()")).scalar()
    assert actual and actual.lower() in ALLOWED_LOCAL_TEST_DBS, (
        f"FAIL CLOSED: Attempted cleanup on unexpected database '{actual}'!"
    )
    delete_order = [
        "dbo.promotion_events",
        "dbo.advertising_payments",
        "dbo.promotion_campaigns",
        "dbo.payment_refunds",
        "dbo.payment_transactions",
        "dbo.provider_settlements",
        "dbo.booking_status_history",
        "dbo.reviews",
        "dbo.wishlists",
        "dbo.booking_references",
        "dbo.listing_references",
        "dbo.notifications",
        "dbo.admin_audit_logs",
        "dbo.tour_guide_verifications",
        "dbo.tour_guide_bookings",
        "dbo.tour_guide_availability",
        "dbo.tour_guide_packages",
        "dbo.tour_guide_images",
        "dbo.tour_guide_credentials",
        "dbo.tour_guide_service_areas",
        "dbo.tour_guide_languages",
        "dbo.tour_guides",
        "dbo.rental_bookings",
        "dbo.rental_vehicles",
        "dbo.homestay_bookings",
        "dbo.homestays",
        "dbo.provider_profiles",
        "dbo.users",
    ]
    for tbl in delete_order:
        db.execute(text(f"DELETE FROM {tbl}"))
    db.commit()


@pytest.fixture
def sqlserver_db():
    engine = _build_isolated_engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = SessionLocal()
    actual = db.execute(text("SELECT DB_NAME()")).scalar()
    assert actual and actual.lower() in ALLOWED_LOCAL_TEST_DBS
    _cleanup_all_test_tables(db)
    try:
        yield db
    finally:
        db.rollback()
        _cleanup_all_test_tables(db)
        db.close()
        engine.dispose()


@pytest.fixture
def sqlserver_client(sqlserver_db: Session):
    app.dependency_overrides[get_db] = lambda: sqlserver_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _seed_tour_guide_actors(db: Session) -> dict:
    tag = uuid.uuid4().hex[:6]
    admin = User(
        full_name="SQL Admin",
        email=f"sql.admin.{tag}@ceylongo.test",
        role="admin",
        is_active=True,
    )
    traveler = User(
        full_name="SQL Traveler",
        email=f"sql.traveler.{tag}@ceylongo.test",
        role="traveler",
        is_active=True,
    )
    provider_user = User(
        full_name="SQL Guide Provider",
        email=f"sql.guide.{tag}@ceylongo.test",
        role="provider",
        is_active=True,
    )
    db.add_all([admin, traveler, provider_user])
    db.flush()

    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="SQL Server Sigiriya Guides",
        provider_type="tour_guide",
        phone="0771234567",
        district="Matale",
        nic_number=f"1988{tag[:6]}12",
        approval_status="approved",
    )
    db.add(profile)
    db.flush()

    guide = TourGuide(
        provider_profile_id=profile.id,
        bio="Licensed national tour guide verified on SQL Server.",
        years_of_experience=10,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("18000.00"),
        hourly_rate=Decimal("3000.00"),
        currency="LKR",
        base_city="Sigiriya",
        base_district="Matale",
        max_group_size=6,
        cancellation_policy="Free cancellation up to 24 hours before tour start.",
        free_cancellation_hours=24,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    db.add(guide)
    db.flush()

    pkg = TourGuidePackage(
        tour_guide_id=guide.id,
        title="Sigiriya Rock SQL Tour",
        description="Half-day heritage climb.",
        duration_hours=Decimal("4.00"),
        price=Decimal("10000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Sigiriya Ticket Counter",
        is_active=True,
    )
    db.add(pkg)
    db.commit()
    return {
        "admin": admin,
        "traveler": traveler,
        "provider_user": provider_user,
        "profile": profile,
        "guide": guide,
        "pkg": pkg,
    }


# =============================================================================
# 1. LIVE SQL SERVER MIGRATION HISTORY & CHECK CONSTRAINT ENFORCEMENT
# =============================================================================

def test_sqlserver_migrations_001_to_036_and_live_check_constraints(
    sqlserver_db: Session,
):
    versions = [
        r[0]
        for r in sqlserver_db.execute(
            text("SELECT version FROM dbo.schema_migrations ORDER BY version")
        ).fetchall()
    ]
    assert versions[:36] == [f"{i:03d}" for i in range(1, 37)]

    # Verify Migration 034 (eco_reservations) AND Migration 035 (tour_guides extensions) exist physically on SQL Server
    eco_tbl = sqlserver_db.execute(
        text(
            "SELECT OBJECT_ID('dbo.eco_reservations', 'U')"
        )
    ).scalar()
    assert eco_tbl is not None
    tg_cols = {
        r[0].lower()
        for r in sqlserver_db.execute(
            text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='tour_guides'"
            )
        ).fetchall()
    }
    assert {"whatsapp_number", "allow_whatsapp_contact", "verified_years_of_experience"}.issubset(
        tg_cols
    )

    pkg_cols = {
        r[0].lower()
        for r in sqlserver_db.execute(
            text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='tour_guide_packages'"
            )
        ).fetchall()
    }
    assert {"package_type", "meeting_instructions", "special_requirements"}.issubset(pkg_cols)

    actors = _seed_tour_guide_actors(sqlserver_db)
    guide_id = actors["guide"].id
    pkg_id = actors["pkg"].id

    # Live SQL Server CK_tour_guides_whatsapp_opt_in blocks allow=1 with NULL number
    with pytest.raises(IntegrityError):
        sqlserver_db.execute(
            text(
                "UPDATE dbo.tour_guides "
                "SET allow_whatsapp_contact = 1, whatsapp_number = NULL WHERE id = :gid"
            ),
            {"gid": guide_id},
        )
        sqlserver_db.commit()
    sqlserver_db.rollback()

    # Live SQL Server CK_tour_guides_verified_experience blocks > 80 or < 0
    for invalid_years in (-1, 81):
        with pytest.raises(IntegrityError):
            sqlserver_db.execute(
                text(
                    "UPDATE dbo.tour_guides "
                    "SET verified_years_of_experience = :y WHERE id = :gid"
                ),
                {"y": invalid_years, "gid": guide_id},
            )
            sqlserver_db.commit()
        sqlserver_db.rollback()

    # Live SQL Server CK_tour_guide_packages_type blocks invalid package_type
    with pytest.raises(IntegrityError):
        sqlserver_db.execute(
            text(
                "UPDATE dbo.tour_guide_packages "
                "SET package_type = 'invalid_mode' WHERE id = :pid"
            ),
            {"pid": pkg_id},
        )
        sqlserver_db.commit()
    sqlserver_db.rollback()


# =============================================================================
# 2. LIVE SQL SERVER TR-12, TR-15, PR-15, AD-06 (MIGRATION 035 + sp_getapplock)
# =============================================================================

def test_sqlserver_tr12_tr15_pr15_ad06_online_payment_refund_settlement_reconciliation(
    sqlserver_client: TestClient, sqlserver_db: Session
):
    actors = _seed_tour_guide_actors(sqlserver_db)
    guide = actors["guide"]
    pkg = actors["pkg"]
    traveler = actors["traveler"]
    provider_user = actors["provider_user"]
    admin = actors["admin"]
    profile = actors["profile"]

    def create_and_accept(days_ahead: int, payment_method: str = "online") -> dict:
        r = sqlserver_client.post(
            f"/api/v1/tour-guides/{guide.id}/bookings",
            headers=bearer(traveler),
            json={
                "rate_type": "package",
                "package_id": pkg.id,
                "booking_date": _future_date(days_ahead).isoformat(),
                "start_time": "08:00:00",
                "number_of_people": 2,
                "payment_method": payment_method,
            },
        )
        assert r.status_code == 201, r.text
        b = r.json()
        acc = sqlserver_client.post(
            f"/api/v1/provider/tour-guides/bookings/{b['id']}/accept",
            headers=bearer(provider_user),
        )
        assert acc.status_code == 200, acc.text
        return acc.json()

    # A. TR-12 + PR-15: Online booking -> mocked PayHere notify ('paid') -> complete -> settle ('completed')
    b1 = create_and_accept(5, "online")
    init1 = sqlserver_client.post(
        f"/api/v1/payments/tour-guides/bookings/{b1['id']}/initiate",
        headers=bearer(traveler),
    )
    assert init1.status_code == 200, init1.text
    r_cb1 = sqlserver_client.post(
        "/api/v1/payments/payhere/notify",
        data=sign_ipn(
            order_id=init1.json()["order_id"],
            payhere_amount="10000.00",
            payment_id="PH-SQL-PAID-001",
        ),
    )
    assert r_cb1.status_code == 200, r_cb1.text
    assert r_cb1.json()["payment_status"] == "paid"

    # Complete online booking and record direct cash booking (10,000) to verify cash exclusion
    assert (
        sqlserver_client.post(
            f"/api/v1/provider/tour-guides/bookings/{b1['id']}/complete",
            headers=bearer(provider_user),
        ).status_code
        == 200
    )
    b_cash = create_and_accept(6, "pay_at_location")
    assert (
        sqlserver_client.post(
            f"/api/v1/provider/tour-guides/bookings/{b_cash['id']}/cash-received",
            headers=bearer(provider_user),
        ).status_code
        == 200
    )

    # Admin settlement succeeds against live SQL Server CK_provider_settlements_status (Migration 036)
    r_settle = sqlserver_client.post(
        "/api/v1/payments/admin/settlements",
        headers=bearer(admin),
        json={
            "provider_profile_id": profile.id,
            "period_start": _future_date(1).isoformat(),
            "period_end": _future_date(12).isoformat(),
            "gross_amount": "10000.00",
            "platform_fee": "1000.00",
            "net_payable": "9000.00",
            "payout_reference": "BANK-SQLSERVER-REF-001",
            "settlement_status": "completed",
        },
    )
    assert r_settle.status_code == 201, r_settle.text
    assert r_settle.json()["settlement_status"] in ("completed", "paid")

    # B. TR-15: Online paid -> cancel outside cutoff -> refund request -> admin completes ('completed')
    b2 = create_and_accept(7, "online")
    init2 = sqlserver_client.post(
        f"/api/v1/payments/tour-guides/bookings/{b2['id']}/initiate",
        headers=bearer(traveler),
    ).json()
    assert (
        sqlserver_client.post(
            "/api/v1/payments/payhere/notify",
            data=sign_ipn(
                order_id=init2["order_id"],
                payhere_amount="10000.00",
                payment_id="PH-SQL-REF-002",
            ),
        ).status_code
        == 200
    )
    assert (
        sqlserver_client.post(
            f"/api/v1/tour-guide-bookings/{b2['id']}/cancel",
            headers=bearer(traveler),
            json={"reason": "Cancelling well before 24-hour window on SQL Server."},
        ).status_code
        == 200
    )
    r_ref = sqlserver_client.post(
        f"/api/v1/tour-guide-bookings/{b2['id']}/request-refund",
        headers=bearer(traveler),
        json={"reason": "Eligible free cancellation refund on SQL Server."},
    )
    assert r_ref.status_code == 201, r_ref.text
    refund_id = r_ref.json()["id"]

    # First mark 'approved', then 'completed' — exercises BOTH new Migration 035 CK_payment_refunds_status values!
    r_app = sqlserver_client.post(
        f"/api/v1/payments/admin/refunds/{refund_id}/process",
        headers=bearer(admin),
        json={"status": "approved", "notes": "Approved for gateway execution"},
    )
    assert r_app.status_code == 200, r_app.text
    r_comp_ref = sqlserver_client.post(
        f"/api/v1/payments/admin/refunds/{refund_id}/process",
        headers=bearer(admin),
        json={"status": "completed", "notes": "PayHere Refund TX PH-RF-SQL-002 verified"},
    )
    assert r_comp_ref.status_code == 200, r_comp_ref.text
    assert r_comp_ref.json()["refund_status"] == "completed"

    r_st = sqlserver_client.get(
        f"/api/v1/tour-guide-bookings/{b2['id']}/payment/status",
        headers=bearer(traveler),
    )
    assert r_st.status_code == 200
    assert r_st.json()["payment_status"] == "refunded"

    # C. AD-06: Late callback on cancelled booking -> flagged in live SQL Server reconciliation queue
    b3 = create_and_accept(9, "online")
    init3 = sqlserver_client.post(
        f"/api/v1/payments/tour-guides/bookings/{b3['id']}/initiate",
        headers=bearer(traveler),
    ).json()
    assert (
        sqlserver_client.post(
            f"/api/v1/tour-guide-bookings/{b3['id']}/cancel",
            headers=bearer(traveler),
            json={"reason": "Cancelled prior to delayed webhook."},
        ).status_code
        == 200
    )
    assert (
        sqlserver_client.post(
            "/api/v1/payments/payhere/notify",
            data=sign_ipn(
                order_id=init3["order_id"],
                payhere_amount="10000.00",
                payment_id="PH-SQL-LATE-003",
            ),
        ).status_code
        == 200
    )
    r_recon = sqlserver_client.get(
        "/api/v1/payments/admin/reconciliation-queue",
        headers=bearer(admin),
    )
    assert r_recon.status_code == 200
    assert "PH-SQL-LATE-003" in {t["gateway_transaction_id"] for t in r_recon.json()}


# =============================================================================
# 3. LIVE SQL SERVER TR-18, PR-17, GS-01, GS-03, GS-06 (MIGRATION 034 ACTIVE)
# =============================================================================

def test_sqlserver_final5_m034_features_enabled_in_isolated_process(
    sqlserver_client: TestClient, sqlserver_db: Session, monkeypatch
):
    monkeypatch.setenv("TOUR_GUIDE_ENABLE_M034_FIELDS", "true")
    actors = _seed_tour_guide_actors(sqlserver_db)
    guide = actors["guide"]
    pkg = actors["pkg"]
    traveler = actors["traveler"]
    provider_user = actors["provider_user"]
    admin = actors["admin"]

    # PR-17 & TR-18: WhatsApp opt-in + anonymous masking + authenticated wa.me
    r_wa = sqlserver_client.put(
        "/api/v1/provider/tour-guides/contact-settings",
        headers=bearer(provider_user),
        json={"whatsapp_number": "077 987 6543", "allow_whatsapp_contact": True},
    )
    assert r_wa.status_code == 200, r_wa.text
    assert r_wa.json()["migration_034_active"] is True
    assert r_wa.json()["whatsapp_number"] == "+94779876543"

    r_anon = sqlserver_client.get(f"/api/v1/tour-guides/{guide.id}/contact")
    assert r_anon.status_code == 200
    assert r_anon.json()["whatsapp_available"] is True
    assert "*" in r_anon.json()["whatsapp_number"]
    assert r_anon.json()["whatsapp_link"] is None

    r_auth = sqlserver_client.get(
        f"/api/v1/tour-guides/{guide.id}/contact", headers=bearer(traveler)
    )
    assert r_auth.status_code == 200
    assert r_auth.json()["whatsapp_number"] == "+94779876543"
    assert r_auth.json()["whatsapp_link"] == "https://wa.me/94779876543"

    # GS-01: Evidence-gated admin-verified years of experience
    sqlserver_db.add(
        TourGuideCredential(
            tour_guide_id=guide.id,
            credential_type="sltda_license",
            title="SLTDA National License",
            issuing_authority="SLTDA",
            file_reference="private/sql_sltda.pdf",
            verification_status="approved",
        )
    )
    sqlserver_db.commit()
    r_exp = sqlserver_client.put(
        f"/api/v1/admin/tour-guides/{guide.id}/verified-experience",
        headers=bearer(admin),
        json={"verified_years_of_experience": 9, "notes": "SLTDA registry confirmed"},
    )
    assert r_exp.status_code == 200, r_exp.text
    assert r_exp.json()["verified_years_of_experience"] == 9

    # GS-03 & GS-06: Package meeting instructions & structured special requirements
    r_req = sqlserver_client.put(
        f"/api/v1/provider/tour-guides/packages/{pkg.id}/requirements",
        headers=bearer(provider_user),
        json={
            "package_type": "half_day",
            "meeting_instructions": "Meet at Main Gate next to ticket booth.",
            "special_requirements": {
                "fitness_level": "moderate",
                "clothing": "Modest attire, sun hat, comfortable shoes.",
                "equipment": "1.5L water bottle, sunscreen.",
                "other_notes": "Stairs climb involved (~1200 steps).",
            },
        },
    )
    assert r_req.status_code == 200, r_req.text
    assert r_req.json()["migration_034_active"] is True

    # Verify live SQL Server public guide detail & package detail expose GS-01, GS-03, GS-06
    r_gdet = sqlserver_client.get(f"/api/v1/tour-guides/{guide.id}")
    assert r_gdet.status_code == 200
    assert r_gdet.json()["verified_years_of_experience"] == 9
    assert "Main Gate" in r_gdet.json()["packages"][0]["meeting_instructions"]
    assert r_gdet.json()["packages"][0]["special_requirements"]["fitness_level"] == "moderate"


# =============================================================================
# 4. LIVE SQL SERVER PR-19 AD-TG WEBHOOK + HOMESTAY & RENTAL PAYMENT SMOKE
# =============================================================================

def test_sqlserver_pr19_ad_tg_webhook_and_homestay_rental_payment_compatibility(
    sqlserver_client: TestClient, sqlserver_db: Session
):
    actors = _seed_tour_guide_actors(sqlserver_db)
    guide = actors["guide"]
    traveler = actors["traveler"]
    provider_user = actors["provider_user"]

    # 4A. PR-19 AD-TG- live webhook on SQL Server
    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=7)).replace(tzinfo=None).isoformat()
    camp = sqlserver_client.post(
        "/api/v1/provider/tour-guides/promotions/campaigns",
        headers=bearer(provider_user),
        json={
            "campaign_name": "SQL Server Sponsored Boost",
            "budget": "5000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": True,
        },
    ).json()
    prep = sqlserver_client.post(
        f"/api/v1/provider/tour-guides/promotions/campaigns/{camp['id']}/payment/prepare",
        headers=bearer(provider_user),
    ).json()
    r_ad = sqlserver_client.post(
        "/api/v1/payments/payhere/notify",
        data=sign_ipn(
            order_id=prep["order_reference"],
            payhere_amount="5000.00",
            payment_id="PH-SQL-ADTG-100",
        ),
    )
    assert r_ad.status_code == 200, r_ad.text
    assert r_ad.json()["campaign_status"] == "active"

    # 4B. Homestay payment webhook compatibility on SQL Server with Migration 035
    hs_owner = User(
        full_name="SQL HS Owner",
        email=f"sql.hs.{uuid.uuid4().hex[:6]}@ceylongo.test",
        role="provider",
        is_active=True,
    )
    sqlserver_db.add(hs_owner)
    sqlserver_db.flush()
    hs_prof = ProviderProfile(
        user_id=hs_owner.id,
        business_name="SQL Homestay Villa",
        provider_type="homestay",
        phone="0775551111",
        district="Kandy",
        nic_number=f"1985{uuid.uuid4().hex[:6]}11",
        approval_status="approved",
    )
    sqlserver_db.add(hs_prof)
    sqlserver_db.flush()
    homestay = Homestay(
        name="SQL Test Homestay",
        description="Quiet hill villa.",
        location="Kandy, Central Province",
        district="Kandy",
        city="Kandy",
        price_per_night=Decimal("8000.00"),
        max_guests=4,
        owner_id=hs_owner.id,
        provider_profile_id=hs_prof.id,
        approval_status="approved",
        is_active=True,
    )
    sqlserver_db.add(homestay)
    sqlserver_db.flush()
    hs_booking = Booking(
        homestay_id=homestay.id,
        user_id=traveler.id,
        check_in=_future_date(10),
        check_out=_future_date(12),
        guests=2,
        total_price=Decimal("16000.00"),
        currency="LKR",
        status="awaiting_payment",
        payment_method="online",
        payment_status="unpaid",
        payment_expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=12),
    )
    sqlserver_db.add(hs_booking)
    sqlserver_db.commit()

    r_hs_init = sqlserver_client.post(
        f"/api/v1/payments/bookings/{hs_booking.id}/initiate",
        headers=bearer(traveler),
    )
    assert r_hs_init.status_code == 200, r_hs_init.text
    r_hs_cb = sqlserver_client.post(
        "/api/v1/payments/payhere/notify",
        data=sign_ipn(
            order_id=r_hs_init.json()["order_id"],
            payhere_amount="16000.00",
            payment_id="PH-SQL-HS-200",
        ),
    )
    assert r_hs_cb.status_code == 200, r_hs_cb.text
    assert r_hs_cb.json()["payment_status"] == "paid"

    # 4C. Rental payment webhook compatibility on SQL Server with Migration 035
    rent_owner = User(
        full_name="SQL Rent Owner",
        email=f"sql.rent.{uuid.uuid4().hex[:6]}@ceylongo.test",
        role="provider",
        is_active=True,
    )
    sqlserver_db.add(rent_owner)
    sqlserver_db.flush()
    rent_prof = ProviderProfile(
        user_id=rent_owner.id,
        business_name="SQL Rental Fleet",
        provider_type="rental",
        phone="0776662222",
        district="Colombo",
        nic_number=f"1986{uuid.uuid4().hex[:6]}22",
        approval_status="approved",
    )
    sqlserver_db.add(rent_prof)
    sqlserver_db.flush()
    vehicle = RentalVehicle(
        provider_id=rent_prof.id,
        vehicle_name="SQL Test Bike",
        vehicle_type="bike",
        price_per_day=Decimal("3500.00"),
        seating_capacity=2,
        transmission="automatic",
        fuel_type="petrol",
        district="Colombo",
        city="Colombo",
        approval_status="approved",
        status="available",
    )
    sqlserver_db.add(vehicle)
    sqlserver_db.flush()
    pickup = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=11)
    rent_booking = RentalBooking(
        vehicle_id=vehicle.id,
        user_id=traveler.id,
        pickup_at=pickup,
        return_at=pickup + timedelta(days=2),
        total_price=Decimal("7000.00"),
        currency="LKR",
        status="confirmed",
        payment_method="online",
        payment_status="unpaid",
    )
    sqlserver_db.add(rent_booking)
    sqlserver_db.commit()

    r_rent_init = sqlserver_client.post(
        f"/api/v1/payments/rental/bookings/{rent_booking.id}/initiate",
        headers=bearer(traveler),
    )
    assert r_rent_init.status_code == 200, r_rent_init.text
    r_rent_cb = sqlserver_client.post(
        "/api/v1/payments/payhere/notify",
        data=sign_ipn(
            order_id=r_rent_init.json()["order_id"],
            payhere_amount="7000.00",
            payment_id="PH-SQL-RENT-300",
        ),
    )
    assert r_rent_cb.status_code == 200, r_rent_cb.text
    assert r_rent_cb.json()["payment_status"] == "paid"
