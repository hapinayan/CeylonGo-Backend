"""
Comprehensive Phase 5 Automated Tests for Local Tour Guide Module 05:
- TR-16: Verified traveler reviews, rating recalculation, duplicate guard & admin moderation
- TR-17: Traveler wishlist / favorite guides (save, unsave, list, status, privacy gate)
- PR-01: Provider onboarding checklist (pre-profile and post-profile) & operational dashboard
- PR-16: Provider customer reviews & rating distribution
- PR-19: Sponsored promotion campaigns, PayHere ad payment prep & mocked IPN activation
- PR-20: Promotion event logging, campaign analytics & provider performance dashboard
- AD-07: Admin promotion package CRUD, campaign approval/rejection/pause/cancel & ad payments
- AD-08: Admin platform-wide guide, booking, revenue, and engagement reports
"""

from datetime import UTC, date, datetime, timedelta
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
from app.models.shared import AdminAuditLog, Notification
from app.models.tour_guide import (
    TourGuide,
    TourGuideLanguage,
    TourGuidePackage,
    TourGuideServiceArea,
)
from app.models.user import Base, User
from app.services.tour_guide_phase5 import handle_tour_guide_advertising_payment_callback

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
WISHLIST_BASE = "/api/v1/tour-guide-wishlist"
PROMO_PUBLIC_BASE = "/api/v1/tour-guide-promotions"
PROVIDER_BASE = "/api/v1/provider/tour-guides"
PROVIDER_BOOKINGS_BASE = "/api/v1/provider/tour-guides/bookings"
ADMIN_BASE = "/api/v1/admin/tour-guides"
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
    payment_id: str = "PH-P5-001",
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


def _future_date(days: int = 5) -> date:
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
    admin = User(full_name="Admin P5", email="admin.p5@example.com", role="admin", is_active=True)
    traveler1 = User(full_name="Lina Traveler", email="lina.p5@example.com", role="traveler", is_active=True)
    traveler2 = User(full_name="Mark Visitor", email="mark.p5@example.com", role="traveler", is_active=True)
    provider1_user = User(full_name="Nuwan Guide", email="nuwan.p5@example.com", role="provider", is_active=True)
    provider2_user = User(full_name="Dilshan NewGuide", email="dilshan.p5@example.com", role="provider", is_active=True)
    pending_guide_user = User(full_name="Draft Guide", email="draft.p5@example.com", role="provider", is_active=True)
    db_session.add_all(
        [admin, traveler1, traveler2, provider1_user, provider2_user, pending_guide_user]
    )
    db_session.flush()

    prof1 = ProviderProfile(
        user_id=provider1_user.id,
        business_name="Nuwara Eliya Tea Trails",
        provider_type="tour_guide",
        phone="0771002003",
        district="Nuwara Eliya",
        nic_number="198712345611",
        approval_status="approved",
    )
    prof2_onboarding = ProviderProfile(
        user_id=provider2_user.id,
        business_name="Ella Eco Walks",
        provider_type="tour_guide",
        phone="0772003004",
        district="Badulla",
        nic_number="199112345622",
        approval_status="approved",
    )
    prof_pending = ProviderProfile(
        user_id=pending_guide_user.id,
        business_name="Unapproved Guide Co",
        provider_type="tour_guide",
        phone="0773004005",
        district="Colombo",
        nic_number="199212345633",
        approval_status="approved",
    )
    db_session.add_all([prof1, prof2_onboarding, prof_pending])
    db_session.flush()

    guide1 = TourGuide(
        provider_profile_id=prof1.id,
        bio="Hill-country specialist guide.",
        years_of_experience=9,
        specialties="Cultural & Heritage, Nature & Wildlife",
        daily_rate=Decimal("16000.00"),
        hourly_rate=Decimal("2500.00"),
        currency="LKR",
        base_city="Nuwara Eliya",
        base_district="Nuwara Eliya",
        max_group_size=6,
        cancellation_policy="Free cancellation up to 24 hours before start.",
        free_cancellation_hours=24,
        is_available=True,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
    )
    guide_pending = TourGuide(
        provider_profile_id=prof_pending.id,
        bio="Pending guide.",
        years_of_experience=1,
        specialties="City & Local Life",
        daily_rate=Decimal("10000.00"),
        currency="LKR",
        base_city="Colombo",
        base_district="Colombo",
        max_group_size=4,
        is_available=True,
        approval_status="pending",
        verification_status="unverified",
        is_active=False,
    )
    db_session.add_all([guide1, guide_pending])
    db_session.flush()

    db_session.add(
        TourGuideLanguage(
            tour_guide_id=guide1.id,
            language_code="en",
            language_name="English",
            proficiency_level="fluent",
            is_primary=True,
        )
    )
    db_session.add(
        TourGuideServiceArea(
            tour_guide_id=guide1.id,
            province="Central",
            district="Nuwara Eliya",
            city="Nuwara Eliya",
            is_primary=True,
        )
    )
    pkg1 = TourGuidePackage(
        tour_guide_id=guide1.id,
        title="Horton Plains Dawn Trek",
        description="Guided sunrise trek to World's End.",
        duration_hours=Decimal("5.00"),
        price=Decimal("10000.00"),
        currency="LKR",
        pricing_type="per_group",
        max_people=6,
        meeting_point="Nuwara Eliya Post Office",
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
        "pending_guide_user": pending_guide_user,
        "prof1": prof1,
        "prof2_onboarding": prof2_onboarding,
        "guide1": guide1,
        "guide_pending": guide_pending,
        "pkg1": pkg1,
    }


def _complete_booking(
    client: TestClient,
    env: dict,
    *,
    traveler: User,
    payment_method: str = "pay_at_location",
    days_ahead: int = 5,
    complete: bool = True,
) -> dict:
    r = client.post(
        f"{PUBLIC_BASE}/{env['guide1'].id}/bookings",
        headers=bearer(traveler),
        json={
            "rate_type": "package",
            "package_id": env["pkg1"].id,
            "booking_date": _future_date(days_ahead).isoformat(),
            "start_time": "07:00:00",
            "number_of_people": 2,
            "payment_method": payment_method,
        },
    )
    assert r.status_code == 201, r.text
    b = r.json()
    assert (
        client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b['id']}/accept",
            headers=bearer(env["provider1_user"]),
        ).status_code
        == 200
    )
    if payment_method == "pay_at_location":
        assert (
            client.post(
                f"{PROVIDER_BOOKINGS_BASE}/{b['id']}/cash-received",
                headers=bearer(env["provider1_user"]),
            ).status_code
            == 200
        )
    else:
        init = client.post(
            f"{PAYMENTS_BASE}/tour-guides/bookings/{b['id']}/initiate",
            headers=bearer(traveler),
        ).json()
        assert (
            client.post(
                f"{PAYMENTS_BASE}/payhere/notify",
                data=sign_ipn(
                    order_id=init["order_id"],
                    payhere_amount="10000.00",
                    payment_id=f"PH-P5-{b['id']}",
                ),
            ).status_code
            == 200
        )
    if complete:
        comp = client.post(
            f"{PROVIDER_BOOKINGS_BASE}/{b['id']}/complete",
            headers=bearer(env["provider1_user"]),
        )
        assert comp.status_code == 200
        return comp.json()
    return b


# =============================================================================
# 1. REVIEWS & RATINGS (TR-16, PR-16) + ADMIN MODERATION
# =============================================================================

def test_reviews_verified_booking_enforcement_rating_sync_and_moderation(
    client: TestClient, db_session: Session, env: dict
):
    guide1 = env["guide1"]
    traveler1 = env["traveler1"]
    traveler2 = env["traveler2"]

    # Non-completed booking cannot be reviewed (400)
    uncompleted = _complete_booking(
        client, env, traveler=traveler1, days_ahead=5, complete=False
    )
    r_early = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/reviews",
        headers=bearer(traveler1),
        json={"booking_id": uncompleted["id"], "rating": "5.00", "title": "Too early"},
    )
    assert r_early.status_code == 400

    # Completed bookings by traveler1 (5 stars) & traveler2 (4 stars)
    b1 = _complete_booking(client, env, traveler=traveler1, days_ahead=6, complete=True)
    b2 = _complete_booking(client, env, traveler=traveler2, days_ahead=7, complete=True)

    # Traveler2 cannot review Traveler1's booking (404)
    assert (
        client.post(
            f"{PUBLIC_BASE}/{guide1.id}/reviews",
            headers=bearer(traveler2),
            json={"booking_id": b1["id"], "rating": "1.00", "title": "Hijack attempt"},
        ).status_code
        == 404
    )

    # Provider cannot submit a traveler review (403)
    assert (
        client.post(
            f"{PUBLIC_BASE}/{guide1.id}/reviews",
            headers=bearer(env["provider1_user"]),
            json={"booking_id": b1["id"], "rating": "5.00"},
        ).status_code
        == 403
    )

    # Valid review #1 (5.00)
    r1 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/reviews",
        headers=bearer(traveler1),
        json={
            "booking_id": b1["id"],
            "rating": "5.00",
            "title": "Unforgettable sunrise trek",
            "comment": "Nuwan knew every bird and plant along the trail.",
        },
    )
    assert r1.status_code == 201, r1.text
    rev1 = r1.json()
    assert rev1["is_verified_booking"] is True
    assert Decimal(rev1["rating"]) == Decimal("5.00")

    # Duplicate review for same booking blocked (409)
    assert (
        client.post(
            f"{PUBLIC_BASE}/{guide1.id}/reviews",
            headers=bearer(traveler1),
            json={"booking_id": b1["id"], "rating": "5.00"},
        ).status_code
        == 409
    )

    # Valid review #2 (4.00) -> average becomes 4.50, review_count = 2
    r2 = client.post(
        f"{PUBLIC_BASE}/{guide1.id}/reviews",
        headers=bearer(traveler2),
        json={"booking_id": b2["id"], "rating": "4.00", "title": "Great guide"},
    )
    assert r2.status_code == 201
    rev2 = r2.json()

    # Public reviews list & distribution
    r_pub = client.get(f"{PUBLIC_BASE}/{guide1.id}/reviews")
    assert r_pub.status_code == 200
    pub = r_pub.json()
    assert pub["total"] == 2
    assert Decimal(pub["average_rating"]) == Decimal("4.50")
    assert pub["rating_distribution"] == {"5": 1, "4": 1, "3": 0, "2": 0, "1": 0}

    # TourGuide ORM rating_average & review_count synchronized
    db_session.refresh(guide1)
    assert guide1.rating_average == Decimal("4.50")
    assert guide1.review_count == 2

    # Provider reviews endpoint (PR-16)
    r_prov = client.get(
        f"{PROVIDER_BASE}/reviews", headers=bearer(env["provider1_user"])
    )
    assert r_prov.status_code == 200
    assert r_prov.json()["total"] == 2
    assert Decimal(r_prov.json()["average_rating"]) == Decimal("4.50")

    # Admin unpublishes rev2 -> recalculates guide rating to 5.00, count 1
    assert (
        client.patch(
            f"{ADMIN_BASE}/reviews/{rev2['id']}/moderate",
            headers=bearer(traveler1),
            json={"is_published": False},
        ).status_code
        == 403
    )
    r_mod = client.patch(
        f"{ADMIN_BASE}/reviews/{rev2['id']}/moderate",
        headers=bearer(env["admin"]),
        json={"is_published": False, "reason": "Spam link in comment"},
    )
    assert r_mod.status_code == 200
    assert r_mod.json()["is_published"] is False
    db_session.refresh(guide1)
    assert guide1.rating_average == Decimal("5.00")
    assert guide1.review_count == 1
    assert db_session.query(AdminAuditLog).count() == 1


# =============================================================================
# 2. WISHLIST / FAVORITE GUIDES (TR-17)
# =============================================================================

def test_traveler_wishlist_crud_privacy_gate_and_duplicates(
    client: TestClient, db_session: Session, env: dict
):
    guide1 = env["guide1"]
    guide_pending = env["guide_pending"]
    traveler1 = env["traveler1"]
    traveler2 = env["traveler2"]

    # Unapproved guide cannot be saved (404)
    assert (
        client.post(
            f"{WISHLIST_BASE}/{guide_pending.id}", headers=bearer(traveler1)
        ).status_code
        == 404
    )

    # Save approved guide -> 201
    r_add = client.post(
        f"{WISHLIST_BASE}/{guide1.id}",
        headers=bearer(traveler1),
        json={"notes": "Top choice for December hill country trip"},
    )
    assert r_add.status_code == 201, r_add.text
    assert r_add.json()["saved"] is True

    # Duplicate save -> 409
    assert (
        client.post(
            f"{WISHLIST_BASE}/{guide1.id}", headers=bearer(traveler1)
        ).status_code
        == 409
    )

    # Status check (true for traveler1, false for traveler2)
    assert (
        client.get(
            f"{WISHLIST_BASE}/{guide1.id}/status", headers=bearer(traveler1)
        ).json()["saved"]
        is True
    )
    assert (
        client.get(
            f"{WISHLIST_BASE}/{guide1.id}/status", headers=bearer(traveler2)
        ).json()["saved"]
        is False
    )

    # List saved guides
    r_list = client.get(f"{WISHLIST_BASE}/me", headers=bearer(traveler1))
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 1
    assert r_list.json()["items"][0]["tour_guide_id"] == guide1.id
    assert r_list.json()["items"][0]["is_verified"] is True

    # Remove from wishlist -> 200, second delete -> 404
    r_del = client.delete(f"{WISHLIST_BASE}/{guide1.id}", headers=bearer(traveler1))
    assert r_del.status_code == 200
    assert r_del.json()["saved"] is False
    assert (
        client.delete(
            f"{WISHLIST_BASE}/{guide1.id}", headers=bearer(traveler1)
        ).status_code
        == 404
    )


# =============================================================================
# 3. PROVIDER ONBOARDING DASHBOARD (PR-01), PROMOTIONS (PR-19, PR-20, AD-07)
#    AND ADMIN REPORTS OVERVIEW (AD-08)
# =============================================================================

def test_provider_onboarding_promotions_analytics_and_admin_reports(
    client: TestClient, db_session: Session, env: dict
):
    guide1 = env["guide1"]
    traveler1 = env["traveler1"]
    admin = env["admin"]

    # 3A. PR-01 Onboarding dashboard BEFORE TourGuide profile is created
    r_new_dash = client.get(
        f"{PROVIDER_BASE}/dashboard", headers=bearer(env["provider2_user"])
    )
    assert r_new_dash.status_code == 200, r_new_dash.text
    nd = r_new_dash.json()
    assert nd["tour_guide_id"] is None
    assert nd["onboarding"]["has_profile"] is False
    assert nd["onboarding"]["completion_percent"] == 0
    assert len(nd["onboarding"]["next_steps"]) >= 1

    # 3B. AD-07 Admin creates & updates a promotion package; public lists active packages
    r_pkg = client.post(
        f"{ADMIN_BASE}/promotions/packages",
        headers=bearer(admin),
        json={
            "title": "Featured Guide Boost 14 Days",
            "description": "Priority placement in search results.",
            "discount_percent": "15.00",
            "currency": "LKR",
            "valid_from": _future_date(0).isoformat(),
            "valid_to": _future_date(60).isoformat(),
            "is_active": True,
        },
    )
    assert r_pkg.status_code == 201, r_pkg.text
    promo_pkg_id = r_pkg.json()["id"]

    r_pub_pkgs = client.get(f"{PROMO_PUBLIC_BASE}/packages")
    assert r_pub_pkgs.status_code == 200
    assert any(p["id"] == promo_pkg_id for p in r_pub_pkgs.json())

    r_upd_pkg = client.patch(
        f"{ADMIN_BASE}/promotions/packages/{promo_pkg_id}",
        headers=bearer(admin),
        json={"title": "Featured Guide Boost 14 Days (Updated)"},
    )
    assert r_upd_pkg.status_code == 200
    assert "Updated" in r_upd_pkg.json()["title"]

    # 3C. PR-19 Provider creates draft campaign -> submits -> prepares ad payment -> mocked IPN activates it
    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=10)).replace(tzinfo=None).isoformat()

    r_camp = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns",
        headers=bearer(env["provider1_user"]),
        json={
            "campaign_name": "Peak Season Tea Trails Boost",
            "budget": "5000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": False,
        },
    )
    assert r_camp.status_code == 201, r_camp.text
    camp = r_camp.json()
    assert camp["status"] == "draft"
    assert camp["target_module"] == "tour_guide"

    r_sub = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/submit",
        headers=bearer(env["provider1_user"]),
    )
    assert r_sub.status_code == 200
    assert r_sub.json()["status"] == "pending_approval"

    # Admin can also create/reject a second campaign to test AD-07 rejection + audit log
    r_camp2 = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns",
        headers=bearer(env["provider1_user"]),
        json={
            "campaign_name": "Rejected Test Campaign",
            "budget": "2000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": True,
        },
    )
    camp2_id = r_camp2.json()["id"]
    r_rej = client.post(
        f"{ADMIN_BASE}/promotions/campaigns/{camp2_id}/decision",
        headers=bearer(admin),
        json={"action": "reject", "reason": "Misleading campaign headline text."},
    )
    assert r_rej.status_code == 200
    assert r_rej.json()["status"] == "cancelled"

    # Prepare ad payment & verify mocked PayHere IPN activates campaign #1
    r_ad_prep = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/payment/prepare",
        headers=bearer(env["provider1_user"]),
    )
    assert r_ad_prep.status_code == 200, r_ad_prep.text
    ad_prep = r_ad_prep.json()
    assert ad_prep["order_reference"].startswith("AD-TG-")

    ad_cb = handle_tour_guide_advertising_payment_callback(
        db_session,
        form_data=sign_ipn(
            order_id=ad_prep["order_reference"],
            payhere_amount="5000.00",
            status_code="2",
            payment_id="PH-AD-TG-701",
        ),
    )
    assert ad_cb["status"] == "ok"
    assert ad_cb["campaign_status"] == "active"

    # Public guide discovery now shows guide1 as sponsored!
    r_disc = client.get(PUBLIC_BASE)
    assert r_disc.status_code == 200
    assert r_disc.json()["items"][0]["is_sponsored"] is True

    # 3D. Complete 1 online booking + 1 cash booking + 1 review + 1 wishlist save
    b_onl = _complete_booking(
        client, env, traveler=traveler1, payment_method="online", days_ahead=6, complete=True
    )
    _complete_booking(
        client, env, traveler=traveler1, payment_method="pay_at_location", days_ahead=7, complete=True
    )
    client.post(
        f"{PUBLIC_BASE}/{guide1.id}/reviews",
        headers=bearer(traveler1),
        json={"booking_id": b_onl["id"], "rating": "5.00", "title": "Excellent"},
    )
    client.post(f"{WISHLIST_BASE}/{guide1.id}", headers=bearer(traveler1))

    # 3E. PR-20 Record impression, click, and attributed booking events -> verify analytics
    for ev_type, b_id in (
        ("impression", None),
        ("impression", None),
        ("click", None),
        ("booking", b_onl["id"]),
    ):
        r_ev = client.post(
            f"{PROMO_PUBLIC_BASE}/events",
            headers=bearer(traveler1),
            json={"campaign_id": camp["id"], "event_type": ev_type, "booking_id": b_id},
        )
        assert r_ev.status_code == 201, r_ev.text

    r_camp_an = client.get(
        f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/analytics",
        headers=bearer(env["provider1_user"]),
    )
    assert r_camp_an.status_code == 200
    ca = r_camp_an.json()
    assert ca["impressions"] == 2
    assert ca["clicks"] == 1
    assert ca["bookings"] == 1
    assert ca["ctr_percent"] == 50.0
    assert ca["conversion_rate_percent"] == 100.0

    # Provider pause & resume campaign lifecycle
    assert (
        client.patch(
            f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/status",
            headers=bearer(env["provider1_user"]),
            json={"status": "paused"},
        ).json()["status"]
        == "paused"
    )
    assert (
        client.patch(
            f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/status",
            headers=bearer(env["provider1_user"]),
            json={"status": "active"},
        ).json()["status"]
        == "active"
    )

    # 3F. PR-01 Active guide operational dashboard + PR-20 Provider analytics dashboard
    r_dash = client.get(
        f"{PROVIDER_BASE}/dashboard", headers=bearer(env["provider1_user"])
    )
    assert r_dash.status_code == 200, r_dash.text
    dash = r_dash.json()
    assert dash["tour_guide_id"] == guide1.id
    assert dash["bookings"]["completed"] == 2
    assert Decimal(dash["earnings"]["total_online_gross_earnings"]) == Decimal("10000.00")
    assert Decimal(dash["earnings"]["cash_collected_total"]) == Decimal("10000.00")
    assert dash["wishlist_saves_count"] == 1
    assert dash["promotions"]["active_campaigns"] == 1
    assert dash["promotions"]["total_impressions"] == 2

    r_an = client.get(
        f"{PROVIDER_BASE}/analytics", headers=bearer(env["provider1_user"])
    )
    assert r_an.status_code == 200, r_an.text
    an = r_an.json()
    assert an["total_bookings"] == 2
    assert an["completed_bookings"] == 2
    assert an["completion_rate_percent"] == 100.0
    assert an["promotion_impressions"] == 2
    assert an["promotion_bookings"] == 1

    # 3G. AD-07 Admin campaign list & ad payments + AD-08 Admin reports overview
    assert (
        client.get(f"{ADMIN_BASE}/reports/overview", headers=bearer(traveler1)).status_code
        == 403
    )

    r_adm_camps = client.get(
        f"{ADMIN_BASE}/promotions/campaigns", headers=bearer(admin)
    )
    assert r_adm_camps.status_code == 200
    assert r_adm_camps.json()["total"] == 2

    r_adm_adpay = client.get(
        f"{ADMIN_BASE}/promotions/payments", headers=bearer(admin)
    )
    assert r_adm_adpay.status_code == 200
    assert Decimal(r_adm_adpay.json()["total_verified_revenue"]) == Decimal("5000.00")

    r_rep = client.get(f"{ADMIN_BASE}/reports/overview", headers=bearer(admin))
    assert r_rep.status_code == 200, r_rep.text
    rep = r_rep.json()
    assert rep["total_guides"] == 2
    assert rep["guides_by_approval_status"]["approved"] == 1
    assert rep["guides_by_approval_status"]["pending"] == 1
    assert rep["total_bookings"] == 2
    assert rep["bookings_by_payment_method"]["online"] == 1
    assert rep["bookings_by_payment_method"]["pay_at_location"] == 1
    assert Decimal(rep["online_gross_revenue"]) == Decimal("10000.00")
    assert Decimal(rep["ceylongo_commission_revenue"]) == Decimal("1000.00")
    assert Decimal(rep["direct_cash_collected_total"]) == Decimal("10000.00")
    assert Decimal(rep["verified_advertising_revenue"]) == Decimal("5000.00")
    assert rep["total_published_reviews"] == 1
    assert Decimal(rep["platform_average_rating"]) == Decimal("5.00")
    assert rep["total_wishlist_saves"] == 1
    assert rep["active_sponsored_campaigns"] == 1


# =============================================================================
# 4. PHASE 5 SECURITY, CROSS-PROVIDER ISOLATION, AUTO-EXPIRY & AD IPN GUARDS
# =============================================================================

def test_phase5_rbac_cross_provider_isolation_and_campaign_auto_expiry(
    client: TestClient, db_session: Session, env: dict
):
    from app.models.shared import PromotionCampaign

    guide1 = env["guide1"]
    traveler1 = env["traveler1"]
    admin = env["admin"]

    # Unapproved/pending guide cannot create a sponsored campaign (400)
    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=5)).replace(tzinfo=None).isoformat()
    r_unapp = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns",
        headers=bearer(env["pending_guide_user"]),
        json={
            "campaign_name": "Unauthorized Draft Guide Promo",
            "budget": "3000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    )
    assert r_unapp.status_code == 400

    # Provider1 creates and admin approves campaign
    camp = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns",
        headers=bearer(env["provider1_user"]),
        json={
            "campaign_name": "Approved Guide Promo",
            "budget": "4000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
            "submit_immediately": True,
        },
    ).json()
    assert (
        client.post(
            f"{ADMIN_BASE}/promotions/campaigns/{camp['id']}/decision",
            headers=bearer(admin),
            json={"action": "approve"},
        ).status_code
        == 200
    )

    # Other guide provider (pending_guide_user) cannot access or mutate Provider1's campaign (404)
    assert (
        client.get(
            f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/analytics",
            headers=bearer(env["pending_guide_user"]),
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/status",
            headers=bearer(env["pending_guide_user"]),
            json={"status": "cancelled"},
        ).status_code
        == 404
    )

    # Non-admin blocked on admin promotion endpoints (403)
    for actor in (traveler1, env["provider1_user"]):
        assert (
            client.get(
                f"{ADMIN_BASE}/promotions/campaigns", headers=bearer(actor)
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"{ADMIN_BASE}/promotions/payments", headers=bearer(actor)
            ).status_code
            == 403
        )

    # Auto-expiry: force campaign ends_at into the past -> transitions to 'completed' & blocks events
    orm_camp = db_session.get(PromotionCampaign, camp["id"])
    orm_camp.ends_at = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
    db_session.commit()

    r_expired_ev = client.post(
        f"{PROMO_PUBLIC_BASE}/events",
        headers=bearer(traveler1),
        json={"campaign_id": camp["id"], "event_type": "click"},
    )
    assert r_expired_ev.status_code == 400
    db_session.refresh(orm_camp)
    assert orm_camp.status == "completed"

    # Wishlist privacy gate: saving guide1 then suspending guide1 hides it from wishlist listing
    assert (
        client.post(f"{WISHLIST_BASE}/{guide1.id}", headers=bearer(traveler1)).status_code
        == 201
    )
    guide1.approval_status = "suspended"
    db_session.commit()
    r_wl = client.get(f"{WISHLIST_BASE}/me", headers=bearer(traveler1))
    assert r_wl.status_code == 200
    assert r_wl.json()["total"] == 0


def test_phase5_advertising_payment_callback_security_and_idempotency(
    client: TestClient, db_session: Session, env: dict
):
    from fastapi import HTTPException

    starts_at = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
    ends_at = (datetime.now(UTC) + timedelta(days=7)).replace(tzinfo=None).isoformat()
    camp = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns",
        headers=bearer(env["provider1_user"]),
        json={
            "campaign_name": "Ad Payment Security Test",
            "budget": "6000.00",
            "currency": "LKR",
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
    ).json()
    prep = client.post(
        f"{PROVIDER_BASE}/promotions/campaigns/{camp['id']}/payment/prepare",
        headers=bearer(env["provider1_user"]),
    ).json()
    order_ref = prep["order_reference"]

    # Invalid signature rejected (400)
    bad = sign_ipn(order_id=order_ref, payhere_amount="6000.00")
    bad["md5sig"] = "F" * 32
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_advertising_payment_callback(db_session, form_data=bad)
    assert exc.value.status_code == 400

    # Amount mismatch rejected (400)
    wrong_amt = sign_ipn(order_id=order_ref, payhere_amount="10.00")
    with pytest.raises(HTTPException) as exc:
        handle_tour_guide_advertising_payment_callback(db_session, form_data=wrong_amt)
    assert exc.value.status_code == 400

    # Failed ad payment (-2) does not activate campaign
    fail_res = handle_tour_guide_advertising_payment_callback(
        db_session,
        form_data=sign_ipn(
            order_id=order_ref,
            payhere_amount="6000.00",
            status_code="-2",
            payment_id="PH-AD-FAIL",
        ),
    )
    assert fail_res["payment_status"] == "failed"
    assert fail_res["campaign_status"] == "draft"

    # Valid success callback activates campaign; duplicate replay returns already_processed
    ok_ipn = sign_ipn(
        order_id=order_ref,
        payhere_amount="6000.00",
        status_code="2",
        payment_id="PH-AD-OK-808",
    )
    ok_res = handle_tour_guide_advertising_payment_callback(db_session, form_data=ok_ipn)
    assert ok_res["campaign_status"] == "active"
    assert ok_res["payment_status"] == "completed"

    dup_res = handle_tour_guide_advertising_payment_callback(db_session, form_data=ok_ipn)
    assert dup_res["status"] == "already_processed"

