"""
Comprehensive Pre-PR Tests for the Final 5 Schema-Guarded Requirements
(TR-18, PR-17, GS-01, GS-03, GS-06) and Draft Migration 034 Audit.

Tests BOTH:
1. Safe Migration 033 guarded mode (default): zero queries to unmigrated columns,
   HTTP 503 on M034 writes, safe defaults on reads.
2. Full end-to-end application logic inside an isolated in-memory SQLite DB with
   simulated Migration 034 columns + TOUR_GUIDE_ENABLE_M034_FIELDS=true (never
   modifies migrations/ or any real database).
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, Notification
from app.models.tour_guide import (
    TourGuide,
    TourGuideCredential,
    TourGuidePackage,
)
from app.models.user import Base, User
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


def bearer(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
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
def env(db_session: Session):
    admin = User(full_name="Admin Final5", email="admin.f5@example.com", role="admin", is_active=True)
    traveler = User(full_name="Sara Traveler", email="sara.f5@example.com", role="traveler", is_active=True)
    prov1_user = User(full_name="Kasun Guide", email="kasun.f5@example.com", role="provider", is_active=True)
    prov2_user = User(full_name="Other Guide", email="other.f5@example.com", role="provider", is_active=True)
    db_session.add_all([admin, traveler, prov1_user, prov2_user])
    db_session.flush()

    prof1 = ProviderProfile(
        user_id=prov1_user.id,
        business_name="Knuckles Trekking Guides",
        provider_type="tour_guide",
        phone="0771119999",
        district="Kandy",
        nic_number="198612345699",
        approval_status="approved",
    )
    prof2 = ProviderProfile(
        user_id=prov2_user.id,
        business_name="Other Guide Co",
        provider_type="tour_guide",
        phone="0772228888",
        district="Galle",
        nic_number="198712345688",
        approval_status="approved",
    )
    db_session.add_all([prof1, prof2])
    db_session.flush()

    guide1 = TourGuide(
        provider_profile_id=prof1.id,
        bio="Experienced Knuckles range trek guide.",
        years_of_experience=11,
        specialties="Adventure & Nature",
        daily_rate=Decimal("18000.00"),
        hourly_rate=Decimal("3000.00"),
        currency="LKR",
        base_city="Kandy",
        base_district="Kandy",
        max_group_size=6,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    guide2 = TourGuide(
        provider_profile_id=prof2.id,
        bio="Pending guide.",
        years_of_experience=3,
        specialties="Cultural & Heritage",
        daily_rate=Decimal("12000.00"),
        currency="LKR",
        base_city="Galle",
        base_district="Galle",
        max_group_size=4,
        is_available=True,
        approval_status="pending",
        verification_status="unverified",
        is_active=False,
    )
    db_session.add_all([guide1, guide2])
    db_session.flush()

    pkg1 = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Knuckles Cloud Forest Summit",
        description="Full-day mountain trek.",
        duration_hours=Decimal("7.50"),
        price=Decimal("15000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Bambarella Trailhead",
        is_active=True,
    )
    pkg2 = TourGuidePackage(
        tour_guide_id=guide2.id,
        title="Unapproved Guide Package",
        description="Hidden.",
        duration_hours=Decimal("3.00"),
        price=Decimal("5000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=4,
        meeting_point="Galle Bus Stand",
        is_active=True,
    )
    db_session.add_all([pkg1, pkg2])
    db_session.commit()

    return {
        "admin": admin,
        "traveler": traveler,
        "prov1_user": prov1_user,
        "prov2_user": prov2_user,
        "guide1": guide1,
        "guide2": guide2,
        "pkg1": pkg1,
        "pkg2": pkg2,
    }


def _apply_simulated_m034_columns_in_memory_only(db: Session) -> None:
    """In-memory SQLite-only test helper simulating Migration 034 columns."""
    stmts = [
        "ALTER TABLE tour_guides ADD COLUMN whatsapp_number VARCHAR(30)",
        "ALTER TABLE tour_guides ADD COLUMN allow_whatsapp_contact BOOLEAN NOT NULL DEFAULT 0",
        "ALTER TABLE tour_guides ADD COLUMN verified_years_of_experience INTEGER",
        "ALTER TABLE tour_guide_packages ADD COLUMN package_type VARCHAR(20)",
        "ALTER TABLE tour_guide_packages ADD COLUMN meeting_instructions VARCHAR(1000)",
        "ALTER TABLE tour_guide_packages ADD COLUMN special_requirements VARCHAR(2000)",
    ]
    for s in stmts:
        db.execute(text(s))
    db.commit()


# =============================================================================
# 1. SAFE GUARDED BEHAVIOR ON MIGRATION 033 SCHEMA (DEFAULT)
# =============================================================================

def test_migration_033_guarded_mode_blocks_writes_and_returns_safe_defaults(
    client: TestClient, db_session: Session, env: dict, monkeypatch
):
    monkeypatch.delenv("TOUR_GUIDE_ENABLE_M034_FIELDS", raising=False)
    guide1 = env["guide1"]
    pkg1 = env["pkg1"]

    # PR-17 read returns safe Migration 033 default
    r_get_cs = client.get(
        "/api/v1/provider/tour-guides/contact-settings",
        headers=bearer(env["prov1_user"]),
    )
    assert r_get_cs.status_code == 200
    assert r_get_cs.json()["migration_034_active"] is False
    assert r_get_cs.json()["allow_whatsapp_contact"] is False
    assert r_get_cs.json()["whatsapp_number"] is None

    # PR-17 validation still catches bad phone before 503 gate (422)
    r_bad_wa = client.put(
        "/api/v1/provider/tour-guides/contact-settings",
        headers=bearer(env["prov1_user"]),
        json={"whatsapp_number": "not-a-phone", "allow_whatsapp_contact": True},
    )
    assert r_bad_wa.status_code == 422

    # PR-17 valid write safely returns 503 while Migration 034 is unapplied
    r_put_cs = client.put(
        "/api/v1/provider/tour-guides/contact-settings",
        headers=bearer(env["prov1_user"]),
        json={"whatsapp_number": "0771234567", "allow_whatsapp_contact": True},
    )
    assert r_put_cs.status_code == 503

    # GS-01 evidence gate still runs (400 without approved credential), then 503 once approved
    r_no_cred = client.put(
        f"/api/v1/admin/tour-guides/{guide1.id}/verified-experience",
        headers=bearer(env["admin"]),
        json={"verified_years_of_experience": 9},
    )
    assert r_no_cred.status_code == 400

    db_session.add(
        TourGuideCredential(
            tour_guide_id=guide1.id,
            credential_type="sltda_license",
            title="SLTDA National License",
            issuing_authority="SLTDA",
            file_reference="private/sltda_f5.pdf",
            verification_status="approved",
        )
    )
    db_session.commit()
    r_exp_503 = client.put(
        f"/api/v1/admin/tour-guides/{guide1.id}/verified-experience",
        headers=bearer(env["admin"]),
        json={"verified_years_of_experience": 9},
    )
    assert r_exp_503.status_code == 503

    # GS-03 / GS-06 public read returns safe defaults; provider write returns 503
    r_req_get = client.get(f"/api/v1/tour-guides/packages/{pkg1.id}/requirements")
    assert r_req_get.status_code == 200
    assert r_req_get.json()["migration_034_active"] is False
    assert r_req_get.json()["meeting_instructions"] is None
    assert r_req_get.json()["special_requirements"] is None

    r_req_put = client.put(
        f"/api/v1/provider/tour-guides/packages/{pkg1.id}/requirements",
        headers=bearer(env["prov1_user"]),
        json={
            "meeting_instructions": "Meet by the trailhead sign 15 minutes early.",
            "special_requirements": {"fitness_level": "challenging"},
        },
    )
    assert r_req_put.status_code == 503


# =============================================================================
# 2. FULL E2E VERIFICATION WITH SIMULATED IN-MEMORY MIGRATION 034 + FLAG ON
# =============================================================================

def test_final_five_requirements_end_to_end_when_migration_034_enabled_in_memory(
    client: TestClient, db_session: Session, env: dict, monkeypatch
):
    _apply_simulated_m034_columns_in_memory_only(db_session)
    monkeypatch.setenv("TOUR_GUIDE_ENABLE_M034_FIELDS", "true")

    guide1 = env["guide1"]
    pkg1 = env["pkg1"]
    pkg2 = env["pkg2"]

    # --- PR-17 & TR-18: WhatsApp opt-in + anonymous masking + authenticated wa.me link ---
    r_opt_in = client.put(
        "/api/v1/provider/tour-guides/contact-settings",
        headers=bearer(env["prov1_user"]),
        json={"whatsapp_number": "077 123 4567", "allow_whatsapp_contact": True},
    )
    assert r_opt_in.status_code == 200, r_opt_in.text
    cs = r_opt_in.json()
    assert cs["migration_034_active"] is True
    assert cs["whatsapp_number"] == "+94771234567"
    assert cs["allow_whatsapp_contact"] is True

    # Anonymous traveler sees masked WhatsApp number and NO direct wa.me link
    r_anon = client.get(f"/api/v1/tour-guides/{guide1.id}/contact")
    assert r_anon.status_code == 200
    anon_c = r_anon.json()
    assert anon_c["whatsapp_available"] is True
    assert "*" in anon_c["whatsapp_number"]
    assert anon_c["whatsapp_link"] is None
    assert "whatsapp" in anon_c["available_channels"]

    # Authenticated traveler sees full WhatsApp number + wa.me link
    r_auth = client.get(
        f"/api/v1/tour-guides/{guide1.id}/contact",
        headers=bearer(env["traveler"]),
    )
    assert r_auth.status_code == 200
    auth_c = r_auth.json()
    assert auth_c["whatsapp_available"] is True
    assert auth_c["whatsapp_number"] == "+94771234567"
    assert auth_c["whatsapp_link"] == "https://wa.me/94771234567"

    # Provider opts out -> WhatsApp immediately hidden publicly
    assert (
        client.put(
            "/api/v1/provider/tour-guides/contact-settings",
            headers=bearer(env["prov1_user"]),
            json={"whatsapp_number": "+94771234567", "allow_whatsapp_contact": False},
        ).status_code
        == 200
    )
    r_hidden = client.get(
        f"/api/v1/tour-guides/{guide1.id}/contact",
        headers=bearer(env["traveler"]),
    )
    assert r_hidden.json()["whatsapp_available"] is False
    assert r_hidden.json()["whatsapp_link"] is None

    # --- GS-01: Evidence-gated admin-verified years of experience ---
    # Non-admin blocked (403)
    assert (
        client.put(
            f"/api/v1/admin/tour-guides/{guide1.id}/verified-experience",
            headers=bearer(env["traveler"]),
            json={"verified_years_of_experience": 9},
        ).status_code
        == 403
    )
    # Without approved credential blocked (400)
    assert (
        client.put(
            f"/api/v1/admin/tour-guides/{guide1.id}/verified-experience",
            headers=bearer(env["admin"]),
            json={"verified_years_of_experience": 9},
        ).status_code
        == 400
    )
    # Add approved credential -> admin sets verified_years_of_experience=9
    db_session.add(
        TourGuideCredential(
            tour_guide_id=guide1.id,
            credential_type="sltda_license",
            title="SLTDA National License",
            issuing_authority="SLTDA",
            file_reference="private/sltda_f5_ok.pdf",
            verification_status="approved",
        )
    )
    db_session.commit()
    r_ver = client.put(
        f"/api/v1/admin/tour-guides/{guide1.id}/verified-experience",
        headers=bearer(env["admin"]),
        json={"verified_years_of_experience": 9, "notes": "Verified against SLTDA registry"},
    )
    assert r_ver.status_code == 200, r_ver.text
    assert r_ver.json()["verified_years_of_experience"] == 9
    assert r_ver.json()["self_reported_years_of_experience"] == 11
    assert r_ver.json()["experience_is_verified"] is True
    assert db_session.query(AdminAuditLog).count() == 1
    assert db_session.query(Notification).count() == 1

    # --- GS-03 & GS-06: Package meeting instructions & structured special requirements ---
    # Other provider cannot edit pkg1 (404)
    assert (
        client.put(
            f"/api/v1/provider/tour-guides/packages/{pkg1.id}/requirements",
            headers=bearer(env["prov2_user"]),
            json={"meeting_instructions": "Hijack attempt"},
        ).status_code
        == 404
    )

    r_pkg_upd = client.put(
        f"/api/v1/provider/tour-guides/packages/{pkg1.id}/requirements",
        headers=bearer(env["prov1_user"]),
        json={
            "package_type": "full_day",
            "meeting_instructions": "Meet at Bambarella entrance gate 15 mins prior; look for blue CeylonGo badge.",
            "special_requirements": {
                "fitness_level": "challenging",
                "clothing": "Long trousers, leech socks, rain jacket.",
                "equipment": "Hiking boots, 2L water bottle, headlamp.",
                "other_notes": "Not recommended for children under 10.",
            },
        },
    )
    assert r_pkg_upd.status_code == 200, r_pkg_upd.text
    pu = r_pkg_upd.json()
    assert pu["migration_034_active"] is True
    assert pu["package_type"] == "full_day"
    assert "Bambarella" in pu["meeting_instructions"]
    assert pu["special_requirements"]["fitness_level"] == "challenging"
    assert "leech socks" in pu["special_requirements"]["clothing"]

    # Public read returns exact instructions & requirements; unapproved guide's package returns 404
    r_pub = client.get(f"/api/v1/tour-guides/packages/{pkg1.id}/requirements")
    assert r_pub.status_code == 200
    assert r_pub.json()["special_requirements"]["equipment"] == "Hiking boots, 2L water bottle, headlamp."
    assert client.get(f"/api/v1/tour-guides/packages/{pkg2.id}/requirements").status_code == 404

    # Public guide detail & package detail automatically surface GS-01, GS-03, GS-06 when M034 active
    r_gdet = client.get(f"/api/v1/tour-guides/{guide1.id}")
    assert r_gdet.status_code == 200
    assert r_gdet.json()["verified_years_of_experience"] == 9
    assert "Bambarella" in r_gdet.json()["packages"][0]["meeting_instructions"]
    assert r_gdet.json()["packages"][0]["special_requirements"]["fitness_level"] == "challenging"

    r_pdet = client.get(f"/api/v1/tour-guides/packages/{pkg1.id}")
    assert r_pdet.status_code == 200
    assert "Bambarella" in r_pdet.json()["package"]["meeting_instructions"]
    assert r_pdet.json()["package"]["special_requirements"]["fitness_level"] == "challenging"


# =============================================================================
# 3. DRAFT MIGRATION 034, SHARED CHECK ALIGNMENT & AD-TG WEBHOOK PATCH AUDIT
# =============================================================================

def test_promoted_migration_035_hardening_and_rollback_completeness():
    repo_root = Path(__file__).resolve().parents[1]
    versions = [m.version for m in discover_migrations(repo_root / "migrations")]
    assert versions[:36] == [f"{i:03d}" for i in range(1, 37)]
    assert (repo_root / "migrations" / "034_create_eco_reservations.sql").exists()

    fwd = (
        repo_root / "migrations" / "035_extend_tour_guide_module_final_gaps.sql"
    ).read_text(encoding="utf-8")
    rbk = (
        repo_root
        / "migrations"
        / "rollback"
        / "035_extend_tour_guide_module_final_gaps_rollback.sql"
    ).read_text(encoding="utf-8")
    review_doc = (
        repo_root
        / "docs"
        / "tour-guide"
        / "DATABASE_COORDINATOR_REVIEW_MIGRATION_034_AND_SHARED_PAYMENTS.md"
    )
    checklist_doc = (
        repo_root
        / "docs"
        / "tour-guide"
        / "APPROVAL_CHECKLIST_DB_COORDINATOR_AND_TEAM_LEAD.md"
    )
    assert review_doc.exists()
    assert checklist_doc.exists()

    assert len(split_sql_batches(fwd)) >= 7
    assert len(split_sql_batches(rbk)) >= 7
    assert "SET ANSI_NULLS ON;" in fwd
    assert "DECIMAL(18, 2)" in fwd
    assert "NVARCHAR(2000)" in fwd
    assert "CK_tour_guides_whatsapp_opt_in" in fwd
    assert "CK_tour_guides_whatsapp_opt_in" in rbk
    assert "verified_years_of_experience <= 80" in fwd
    assert "THROW 50340" in rbk


def test_promoted_migration_036_shared_payment_check_and_rollback_safety():
    repo_root = Path(__file__).resolve().parents[1]
    fwd_path = (
        repo_root / "migrations" / "036_align_shared_payment_status_constraints.sql"
    )
    rbk_path = (
        repo_root
        / "migrations"
        / "rollback"
        / "036_align_shared_payment_status_constraints_rollback.sql"
    )
    assert fwd_path.exists()
    assert rbk_path.exists()

    fwd = fwd_path.read_text(encoding="utf-8")
    rbk = rbk_path.read_text(encoding="utf-8")

    assert len(split_sql_batches(fwd)) == 5
    assert len(split_sql_batches(rbk)) == 5
    for constraint in (
        "CK_payment_transactions_status",
        "CK_payment_refunds_status",
        "CK_provider_settlements_status",
    ):
        assert constraint in fwd
        assert constraint in rbk
    assert "'paid'" in fwd
    assert "'approved'" in fwd
    assert "'completed'" in fwd
    for err_code in ("THROW 50351", "THROW 50352", "THROW 50353"):
        assert err_code in rbk


def test_proposed_pr19_ad_tg_webhook_patch_and_mocked_delegation_flow(
    client: TestClient, db_session: Session, env: dict
):
    from datetime import timedelta
    import hashlib
    from fastapi import HTTPException
    from app.core.payhere import payhere_service
    from app.services.tour_guide_phase5 import (
        handle_tour_guide_advertising_payment_callback,
    )

    repo_root = Path(__file__).resolve().parents[1]
    patch_path = (
        repo_root / "docs" / "tour-guide" / "PROPOSED_PR19_AD_TG_WEBHOOK_DELEGATION.patch"
    )
    assert patch_path.exists()
    patch_text = patch_path.read_text(encoding="utf-8")
    assert 'if order_id.startswith("AD-TG-"):' in patch_text
    assert "handle_tour_guide_advertising_payment_callback" in patch_text

    # Mocked end-to-end AD-TG advertising delegation test (mirroring the exact proposed dispatch)
    guide1 = env["guide1"]
    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=10)).replace(tzinfo=None).isoformat()

    camp = client.post(
        "/api/v1/provider/tour-guides/promotions/campaigns",
        headers=bearer(env["prov1_user"]),
        json={
            "campaign_name": "PR-19 Webhook Delegation Mock Test",
            "budget": "7500.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": True,
        },
    ).json()
    prep = client.post(
        f"/api/v1/provider/tour-guides/promotions/campaigns/{camp['id']}/payment/prepare",
        headers=bearer(env["prov1_user"]),
    ).json()
    order_ref = prep["order_reference"]
    assert order_ref.startswith("AD-TG-")

    def make_ad_ipn(
        *,
        order_id: str,
        amount: str = "7500.00",
        status_code: str = "2",
        payment_id: str = "PH-ADTG-901",
        merchant_id: str | None = None,
    ) -> dict[str, str]:
        m_id = merchant_id if merchant_id is not None else payhere_service.merchant_id
        sec_hash = hashlib.md5(payhere_service.merchant_secret.encode("utf-8")).hexdigest().upper()
        raw = f"{m_id}{order_id}{amount}LKR{status_code}{sec_hash}"
        return {
            "merchant_id": m_id,
            "order_id": order_id,
            "payment_id": payment_id,
            "payhere_amount": amount,
            "payhere_currency": "LKR",
            "status_code": status_code,
            "md5sig": hashlib.md5(raw.encode("utf-8")).hexdigest().upper(),
        }

    # Simulated dispatcher matching PROPOSED_PR19_AD_TG_WEBHOOK_DELEGATION.patch
    def simulated_ad_tg_dispatcher(form_data: dict) -> dict:
        if form_data.get("order_id", "").startswith("AD-TG-"):
            return handle_tour_guide_advertising_payment_callback(
                db=db_session, form_data=form_data
            )
        return {"routed_to": "homestay_ad_handler"}

    # Homestay AD- order is NEVER intercepted by AD-TG- prefix
    assert simulated_ad_tg_dispatcher({"order_id": "AD-12345"}) == {
        "routed_to": "homestay_ad_handler"
    }

    # Spoofed merchant_id rejected (400)
    with pytest.raises(HTTPException) as exc:
        simulated_ad_tg_dispatcher(
            make_ad_ipn(order_id=order_ref, merchant_id="SPOOFED_MERCHANT")
        )
    assert exc.value.status_code == 400

    # Valid AD-TG callback activates campaign & surfaces guide as sponsored publicly
    ok_payload = make_ad_ipn(order_id=order_ref)
    res = simulated_ad_tg_dispatcher(ok_payload)
    assert res["status"] == "ok"
    assert res["campaign_status"] == "active"
    assert res["payment_status"] == "completed"

    # Duplicate replay returns already_processed cleanly
    dup = simulated_ad_tg_dispatcher(ok_payload)
    assert dup["status"] == "already_processed"

    # Guide1 appears as sponsored on public discovery
    r_pub_list = client.get("/api/v1/tour-guides")
    assert r_pub_list.status_code == 200
    assert any(
        item["id"] == guide1.id and item["is_sponsored"] is True
        for item in r_pub_list.json()["items"]
    )
