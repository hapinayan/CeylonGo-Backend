"""
Offline Unit and Integration Tests for P1 Stage 2 — Role-Based Access Control (RBAC).

Tests cover:
1. Each of the five admin roles (super_admin, operations_admin, finance_admin,
   content_moderator, support_admin) allowed on authorized endpoints and blocked (403)
   on unauthorized endpoints.
2. Legacy role='admin' allowed on Super Admin and all module admin endpoints.
3. Non-admin users (traveler, provider) and inactive admin accounts blocked (403)
   on all admin endpoints.
4. Unauthenticated requests blocked (401) on all admin endpoints.
5. Cross-role boundaries:
   - Finance Admin blocked from provider approval, listing moderation, community/map moderation.
   - Content Moderator blocked from financial settlements, refund processing, provider approval.
   - Support Admin allowed on read-only booking/incident/refund/eligibility inspection and incident
     status updates, but blocked from refund processing, settlement creation, provider approval,
     and listing/promotion mutations.
   - Operations Admin blocked from financial settlements, refund processing, advertising payment ledger,
     and community moderation.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import os
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    APPROVED_ADMIN_ROLES,
    require_admin,
    require_admin_roles,
    require_content_moderator,
    require_finance_admin,
    require_finance_or_support_read,
    require_operations_admin,
    require_operations_or_content_moderator,
    require_operations_or_finance_admin,
    require_operations_or_support_read,
    require_super_admin,
    require_support_admin,
    require_support_or_operations_admin,
)
from app.core.homestay_permissions import check_homestay_ownership, require_homestay_manager
from app.core.rate_limiter import reset_all_limiters
from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.provider_profile import ProviderProfile
from app.models.rental import RentalEligibilityCheck
from app.models.shared import (
    AdminAuditLog,
    BookingReference,
    PaymentRefund,
    PaymentTransaction,
    PromotionCampaign,
    ProviderSettlement,
)
from app.models.tour_guide import TourGuide
from app.models.user import Base, User


SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    dbapi_connection.create_function(
        "sysutcdatetime",
        0,
        lambda: datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
    )


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(bind=engine)
    reset_all_limiters()
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _create_user_and_headers(
    db,
    *,
    email: str,
    role: str,
    is_active: bool = True,
    full_name: str = "Test User",
) -> tuple[User, dict[str, str]]:
    user = User(
        full_name=full_name,
        email=email,
        password_hash=hash_password("ValidPass123!"),
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_access_token({"sub": str(user.id), "role": user.role})
    return user, {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def role_headers(db_session) -> dict[str, dict[str, str]]:
    """Create active test users and Authorization headers for all 5 admin roles, legacy admin, traveler, and provider."""
    headers_map: dict[str, dict[str, str]] = {}
    all_roles = [
        "super_admin",
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
        "admin",
        "traveler",
        "provider",
    ]
    for r in all_roles:
        _, hdr = _create_user_and_headers(
            db_session,
            email=f"{r}@ceylongo.com",
            role=r,
            is_active=True,
            full_name=f"Role {r}",
        )
        headers_map[r] = hdr
    return headers_map


# ==============================================================================
# 1. Dependency Factory Unit Tests
# ==============================================================================


def test_require_admin_roles_rejects_unrecognized_role_definition():
    with pytest.raises(ValueError, match="Unrecognized admin roles"):
        require_admin_roles("invalid_role_name")


def test_homestay_manager_and_ownership_rbac_boundaries(db_session):
    super_user, _ = _create_user_and_headers(db_session, email="sa@c.com", role="super_admin")
    legacy_user, _ = _create_user_and_headers(db_session, email="la@c.com", role="admin")
    ops_user, _ = _create_user_and_headers(db_session, email="oa@c.com", role="operations_admin")
    fin_user, _ = _create_user_and_headers(db_session, email="fa@c.com", role="finance_admin")
    mod_user, _ = _create_user_and_headers(db_session, email="cm@c.com", role="content_moderator")
    sup_user, _ = _create_user_and_headers(db_session, email="su@c.com", role="support_admin")

    class DummyHomestay:
        owner_id = 99999
        provider_profile_id = None

    dummy = DummyHomestay()

    # Super Admin, legacy admin, and Operations Admin have homestay management override
    assert require_homestay_manager(current_user=super_user, db=db_session).id == super_user.id
    assert require_homestay_manager(current_user=legacy_user, db=db_session).id == legacy_user.id
    assert require_homestay_manager(current_user=ops_user, db=db_session).id == ops_user.id

    assert check_homestay_ownership(dummy, super_user) is True
    assert check_homestay_ownership(dummy, legacy_user) is True
    assert check_homestay_ownership(dummy, ops_user) is True

    # Finance Admin, Content Moderator, and Support Admin do NOT have homestay ownership override
    assert check_homestay_ownership(dummy, fin_user) is False
    assert check_homestay_ownership(dummy, mod_user) is False
    assert check_homestay_ownership(dummy, sup_user) is False


# ==============================================================================
# 2. Operations Admin Domain Matrix Tests (Providers, Listings, Eligibility)
# ==============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/admin/providers",
        "/api/v1/admin/homestays/pending",
        "/api/v1/admin/artisan/listings",
        "/api/v1/admin/artisan/products",
        "/api/v1/admin/activities",
        "/api/v1/admin/eco-experiences",
        "/api/v1/admin/eco-experiences/overview",
        "/api/v1/admin/rentals/vehicles",
        "/api/v1/admin/rentals/vehicles/pending",
        "/api/v1/admin/rentals/documents/pending",
        "/api/v1/admin/rentals/reports/fleet-summary",
        "/api/v1/admin/tour-guides",
        "/api/v1/admin/tour-guides/credentials",
    ],
)
def test_operations_admin_read_endpoints_matrix(client, role_headers, endpoint):
    # Allowed: super_admin, legacy admin, operations_admin
    for allowed in ("super_admin", "admin", "operations_admin"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}: {res.text}"

    # Denied (403): finance_admin, content_moderator, support_admin, traveler, provider
    for denied in ("finance_admin", "content_moderator", "support_admin", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"

    # Unauthenticated (401)
    unauth = client.get(endpoint)
    assert unauth.status_code == 401


def test_operations_write_endpoints_block_non_operations_roles(client, role_headers):
    """Finance, Content Moderator, and Support Admin must be blocked (403) before reaching DB lookup on Operations write endpoints."""
    write_calls = [
        ("POST", "/api/v1/admin/providers/999/approve", None),
        ("POST", "/api/v1/admin/providers/999/reject", {"reason": "Incomplete business docs"}),
        ("POST", "/api/v1/admin/homestays/999/review", {"decision": "approved"}),
        ("POST", "/api/v1/admin/artisan/listings/999/approve", None),
        ("POST", "/api/v1/admin/artisan/products/999/approve", None),
        ("POST", "/api/v1/admin/artisan/reservations/process-expired", None),
        ("PATCH", "/api/v1/artisan-listings/999/status", {"is_verified": True}),
        ("POST", "/api/v1/admin/activities/999/approve", {}),
        ("POST", "/api/v1/admin/eco-experiences/999/approve", None),
        ("POST", "/api/v1/admin/eco-experiences/999/badge-decision", {"decision": "granted", "reason": "Verified"}),
        ("POST", "/api/v1/admin/rentals/vehicles/999/review", {"decision": "approved"}),
        ("POST", "/api/v1/admin/rentals/documents/999/verify", {"decision": "approved"}),
        ("POST", "/api/v1/rental-eligibility/admin/999/review", {"decision": "approved"}),
        ("POST", "/api/v1/admin/tour-guides/999/approve", None),
        ("POST", "/api/v1/admin/tour-guides/credentials/999/verify", {"status": "approved", "verification_type": "license"}),
        ("PUT", "/api/v1/admin/tour-guides/999/verified-experience", {"years_of_experience": 5, "reason": "Verified certificate"}),
    ]
    for method, url, payload in write_calls:
        for denied in ("finance_admin", "content_moderator", "support_admin", "traveler", "provider"):
            res = client.request(method, url, json=payload, headers=role_headers[denied])
            assert res.status_code == 403, f"Expected 403 for {denied} on {method} {url}, got {res.status_code}"

        # Authorized roles pass the RBAC gate (returning 200 or 404/400/503 from handler logic, never 401/403)
        for allowed in ("super_admin", "admin", "operations_admin"):
            res = client.request(method, url, json=payload, headers=role_headers[allowed])
            assert res.status_code not in (401, 403), (
                f"Role {allowed} was unexpectedly blocked on {method} {url} with {res.status_code}"
            )


# ==============================================================================
# 3. Finance Admin Domain Matrix Tests (Payments, Settlements, Refunds, Ad Revenue)
# ==============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/payments/admin/transactions",
        "/api/v1/payments/admin/summary",
        "/api/v1/payments/admin/reconciliation-queue",
        "/api/v1/admin/advertising/payments",
        "/api/v1/admin/advertising/revenue-summary",
        "/api/v1/admin/rentals/payments",
        "/api/v1/admin/rentals/settlements",
        "/api/v1/admin/tour-guides/promotions/payments",
    ],
)
def test_finance_admin_strict_read_endpoints_matrix(client, role_headers, endpoint):
    # Allowed: super_admin, legacy admin, finance_admin
    for allowed in ("super_admin", "admin", "finance_admin"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}"

    # Denied (403): operations_admin, content_moderator, support_admin, traveler, provider
    for denied in ("operations_admin", "content_moderator", "support_admin", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"


def test_finance_write_endpoints_strictly_restricted_to_finance_and_super_admin(client, role_headers):
    """Settlement creation and refund execution/review must block Operations, Content Moderator, and Support Admin."""
    finance_writes = [
        ("POST", "/api/v1/payments/admin/refunds/999/process", {"status": "approved", "notes": "Checked"}),
        (
            "POST",
            "/api/v1/payments/admin/settlements",
            {
                "provider_profile_id": 999,
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "gross_amount": "10000.00",
                "platform_fee": "1000.00",
                "net_payable": "9000.00",
                "settlement_status": "pending",
            },
        ),
        ("POST", "/api/v1/admin/rentals/refunds/999/review", {"decision": "approved", "admin_notes": "Valid"}),
    ]
    for method, url, payload in finance_writes:
        # Support Admin has read-only refund inspection, so MUST be blocked on refund write/process!
        for denied in ("operations_admin", "content_moderator", "support_admin", "traveler", "provider"):
            res = client.request(method, url, json=payload, headers=role_headers[denied])
            assert res.status_code == 403, f"Expected 403 for {denied} on {method} {url}, got {res.status_code}"

        for allowed in ("super_admin", "admin", "finance_admin"):
            res = client.request(method, url, json=payload, headers=role_headers[allowed])
            assert res.status_code == 404, f"Expected 404 (passed RBAC gate) for {allowed} on {method} {url}, got {res.status_code}"


# ==============================================================================
# 4. Content Moderator Domain Matrix Tests (Community, Reviews, Map, Featured)
# ==============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/community/admin/reports",
        "/api/v1/community/admin/hashtags",
        "/api/v1/community/admin/activity",
        "/api/v1/admin/artisan/reviews",
    ],
)
def test_content_moderator_read_endpoints_matrix(client, role_headers, endpoint):
    for allowed in ("super_admin", "admin", "content_moderator"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}"

    for denied in ("operations_admin", "finance_admin", "support_admin", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"


def test_content_moderator_write_endpoints_matrix(client, role_headers):
    moderation_writes = [
        ("PATCH", "/api/v1/community/admin/reports/999", {"report_status": "investigating"}),
        ("PATCH", "/api/v1/community/admin/posts/999", {"is_published": False, "reason": "Spam post"}),
        ("PATCH", "/api/v1/community/admin/profiles/999", {"is_community_suspended": True, "reason": "Abuse"}),
        ("PATCH", "/api/v1/community/admin/hashtags/999", {"is_blocked": True}),
        ("DELETE", "/api/v1/community/admin/comments/999", None),
        ("POST", "/api/v1/homestays/reviews/999/moderate", {"is_published": False}),
        ("PATCH", "/api/v1/admin/artisan/reviews/999/publish", {"is_published": False}),
        ("PATCH", "/api/v1/admin/tour-guides/reviews/999/moderate", {"is_published": False, "reason": "Inappropriate"}),
    ]
    for method, url, payload in moderation_writes:
        for denied in ("operations_admin", "finance_admin", "support_admin", "traveler", "provider"):
            res = client.request(method, url, json=payload, headers=role_headers[denied])
            assert res.status_code == 403, f"Expected 403 for {denied} on {method} {url}, got {res.status_code}"

        for allowed in ("super_admin", "admin", "content_moderator"):
            res = client.request(method, url, json=payload, headers=role_headers[allowed])
            assert res.status_code == 404, f"Expected 404 (passed RBAC gate) for {allowed} on {method} {url}, got {res.status_code}"


def test_map_and_artisan_featuring_shared_operations_and_content_moderator(client, role_headers):
    # Map location queue and artisan feature/unfeature allow super_admin, legacy admin, operations_admin, content_moderator
    res_map_ops = client.get("/api/v1/admin/map/locations", headers=role_headers["operations_admin"])
    res_map_mod = client.get("/api/v1/admin/map/locations", headers=role_headers["content_moderator"])
    assert res_map_ops.status_code == 200
    assert res_map_mod.status_code == 200

    for denied in ("finance_admin", "support_admin", "traveler", "provider"):
        assert client.get("/api/v1/admin/map/locations", headers=role_headers[denied]).status_code == 403
        assert client.post("/api/v1/admin/artisan/listings/999/feature", headers=role_headers[denied]).status_code == 403
        assert client.post("/api/v1/admin/artisan/listings/999/unfeature", headers=role_headers[denied]).status_code == 403


# ==============================================================================
# 5. Support Admin Read-Only & Incident Management Boundaries
# ==============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/admin/rentals/bookings",
        "/api/v1/admin/rentals/incidents",
        "/api/v1/admin/tour-guides/bookings",
        "/api/v1/rental-eligibility/admin/all",
        "/api/v1/rental-eligibility/admin/pending",
    ],
)
def test_support_and_operations_shared_read_endpoints(client, role_headers, endpoint):
    """Support Admin and Operations Admin can inspect bookings, incidents, and traveler eligibility."""
    for allowed in ("super_admin", "admin", "operations_admin", "support_admin"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}"

    for denied in ("finance_admin", "content_moderator", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/payments/admin/refunds",
        "/api/v1/admin/rentals/refunds",
        "/api/v1/admin/rentals/deposits",
    ],
)
def test_support_and_finance_shared_refund_deposit_read_endpoints(client, role_headers, endpoint):
    """Support Admin has read-only access to refund and deposit inspection alongside Finance Admin."""
    for allowed in ("super_admin", "admin", "finance_admin", "support_admin"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}"

    for denied in ("operations_admin", "content_moderator", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"


def test_support_admin_can_update_rental_incident_status(client, role_headers):
    """Support Admin and Operations Admin can update rental incident resolution status."""
    url = "/api/v1/admin/rentals/incidents/999"
    payload = {"resolution_status": "under_investigation", "admin_notes": "Investigating customer claim"}

    for allowed in ("super_admin", "admin", "support_admin", "operations_admin"):
        res = client.patch(url, json=payload, headers=role_headers[allowed])
        assert res.status_code == 404, f"Expected 404 (passed RBAC) for {allowed}, got {res.status_code}"

    for denied in ("finance_admin", "content_moderator", "traveler", "provider"):
        res = client.patch(url, json=payload, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied}, got {res.status_code}"


# ==============================================================================
# 6. Promotions & Operational/Financial Reports Shared Endpoints
# ==============================================================================


@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/v1/admin/homestays/promotions/packages",
        "/api/v1/admin/homestays/campaigns",
        "/api/v1/admin/homestays/campaigns/analytics",
        "/api/v1/admin/artisan/reports/summary",
        "/api/v1/admin/artisan/reports/export/businesses",
        "/api/v1/admin/eco-promotions/packages",
        "/api/v1/admin/eco-promotions/campaigns",
        "/api/v1/admin/eco-promotions/analytics",
        "/api/v1/admin/rentals/promotions",
        "/api/v1/admin/rentals/reports/revenue-analytics",
        "/api/v1/admin/tour-guides/promotions/packages",
        "/api/v1/admin/tour-guides/promotions/campaigns",
        "/api/v1/admin/tour-guides/reports/overview",
    ],
)
def test_promotions_and_cross_domain_reports_allow_operations_and_finance(client, role_headers, endpoint):
    for allowed in ("super_admin", "admin", "operations_admin", "finance_admin"):
        res = client.get(endpoint, headers=role_headers[allowed])
        assert res.status_code == 200, f"Expected 200 for {allowed} on {endpoint}, got {res.status_code}"

    for denied in ("content_moderator", "support_admin", "traveler", "provider"):
        res = client.get(endpoint, headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on {endpoint}, got {res.status_code}"


def test_homestay_admin_dashboard_rbac_denies_unauthorized_roles(client, role_headers):
    """Verify /api/v1/admin/homestays/dashboard blocks content_moderator, support_admin, traveler, and provider with 403."""
    for denied in ("content_moderator", "support_admin", "traveler", "provider"):
        res = client.get("/api/v1/admin/homestays/dashboard", headers=role_headers[denied])
        assert res.status_code == 403, f"Expected 403 for {denied} on /api/v1/admin/homestays/dashboard, got {res.status_code}"
    assert client.get("/api/v1/admin/homestays/dashboard").status_code == 401


@pytest.mark.parametrize("role", ["operations_admin", "finance_admin", "super_admin", "admin"])
def test_homestay_admin_dashboard_rbac_allows_authorized_roles(client, role_headers, role):
    response = client.get("/api/v1/admin/homestays/dashboard", headers=role_headers[role])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"listings", "bookings", "payments", "settlements", "campaigns", "reviews"}
    assert body["payments"] == {
        "total_online_payments_count": 0,
        "total_online_payments_volume": "0.00",
        "pending_refunds_count": 0,
        "pending_refunds_volume": "0.00",
        "currency": "LKR",
    }


def test_homestay_admin_dashboard_financial_totals(client, db_session, role_headers):
    provider = db_session.query(User).filter_by(role="provider").one()
    traveler = db_session.query(User).filter_by(role="traveler").one()
    profile = ProviderProfile(
        user_id=provider.id, business_name="Test Homestay", provider_type="homestay",
        phone="0771234567", district="Kandy",
    )
    db_session.add(profile)
    db_session.flush()
    # Identical native IDs and the same provider must not mix modules.
    homestay, rental = [
        BookingReference(
            booking_reference=f"test-{module}", module_type=module, target_booking_id=1,
            user_id=traveler.id, provider_profile_id=profile.id, total_price=Decimal("500.00"),
        )
        for module in ("homestay", "rental_vehicle")
    ]
    db_session.add_all([homestay, rental])
    db_session.flush()

    transactions = [
        PaymentTransaction(
            booking_reference_id=reference.id, user_id=traveler.id,
            payment_status=payment_status, payment_method=method,
            currency=currency, amount=Decimal(amount),
        )
        for reference, payment_status, method, currency, amount in (
            (homestay, "paid", "online", "LKR", "100.25"),
            (homestay, "completed", "online", "LKR", "200.50"),
            (homestay, "pending", "online", "LKR", "300.00"),
            (homestay, "failed", "online", "LKR", "400.00"),
            (homestay, "refunded", "online", "LKR", "500.00"),
            (homestay, "paid", "cash", "LKR", "600.00"),
            (homestay, "paid", "online", "USD", "700.00"),
            (rental, "paid", "online", "LKR", "800.00"),
        )
    ]
    db_session.add_all(transactions)
    db_session.flush()
    db_session.add_all([
        PaymentRefund(
            payment_transaction_id=transactions[index].id, refund_status=refund_status,
            refund_reason="Test refund", currency=currency, amount=Decimal(amount),
        )
        for index, refund_status, currency, amount in (
            (0, "pending", "LKR", "10.25"),
            (0, "pending", "LKR", "20.50"),
            (1, "approved", "LKR", "30.00"),
            (1, "processed", "LKR", "40.00"),
            (6, "pending", "USD", "50.00"),
            (7, "pending", "LKR", "60.00"),
        )
    ])
    db_session.add_all([
        ProviderSettlement(
            provider_profile_id=profile.id, period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31), settlement_status=settlement_status,
            gross_amount=Decimal("100.00"), net_payable=Decimal("90.00"),
        )
        for settlement_status in ("pending", "paid")
    ])
    db_session.commit()

    response = client.get("/api/v1/admin/homestays/dashboard", headers=role_headers["finance_admin"])

    assert response.status_code == 200
    body = response.json()
    assert body["payments"] == {
        "total_online_payments_count": 2,
        "total_online_payments_volume": "300.75",
        "pending_refunds_count": 2,
        "pending_refunds_volume": "30.75",
        "currency": "LKR",
    }
    assert body["settlements"] == {
        "pending_settlements_count": 0,
        "pending_settlements_volume": "0.00",
        "completed_settlements_count": 0,
        "completed_settlements_volume": "0.00",
        "currency": "LKR",
    }


# ==============================================================================
# 7. Inactive Admin Accounts Blocked Across All Protected Endpoints
# ==============================================================================


@pytest.mark.parametrize(
    "role",
    [
        "super_admin",
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
        "admin",
    ],
)
def test_inactive_admin_roles_blocked_everywhere(client, db_session, role):
    _, inactive_headers = _create_user_and_headers(
        db_session,
        email=f"inactive_{role}@ceylongo.com",
        role=role,
        is_active=False,
    )
    for endpoint in (
        "/api/v1/admin/auth/me",
        "/api/v1/admin/providers",
        "/api/v1/payments/admin/summary",
        "/api/v1/community/admin/reports",
        "/api/v1/admin/rentals/bookings",
    ):
        res = client.get(endpoint, headers=inactive_headers)
        assert res.status_code == 403, f"Inactive {role} must be blocked on {endpoint}, got {res.status_code}"


# ==============================================================================
# 8. Security Hardening: Action-Level Boundaries on Mixed Promotion Endpoints
# ==============================================================================


def _seed_rental_campaign(db_session, *, status_val: str = "pending_approval") -> PromotionCampaign:
    provider_user, _ = _create_user_and_headers(
        db_session,
        email=f"rental_prov_{status_val}_{datetime.now(UTC).timestamp()}@ceylongo.com",
        role="provider",
    )
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Lanka Wheels Rental",
        provider_type="rental",
        phone="+94771234567",
        district="Colombo",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    now_naive = datetime.now(UTC).replace(tzinfo=None)
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        campaign_name="Summer Tuk-Tuk Promo",
        target_module="rental",
        budget=Decimal("5000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status=status_val,
        starts_at=now_naive - timedelta(days=1),
        ends_at=now_naive + timedelta(days=14),
    )
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)
    return campaign


def test_rental_promotion_action_boundaries_and_zero_side_effects(client, db_session, role_headers):
    """
    Verify POST /api/v1/admin/rentals/promotions/{campaign_id}/review enforces:
    - 'approve', 'reject', 'pause' allowed ONLY for super_admin, legacy admin, operations_admin.
      finance_admin, content_moderator, support_admin receive 403 BEFORE any DB mutation or audit log.
    - 'activate' (payment-verified) allowed ONLY for super_admin, legacy admin, finance_admin.
      operations_admin, content_moderator, support_admin receive 403 BEFORE any DB mutation or audit log.
    - Unknown decision rejected with 400.
    """
    # 1. Editorial approval: finance_admin must be blocked (403) with zero side effects
    camp_pending = _seed_rental_campaign(db_session, status_val="pending_approval")
    audit_before = db_session.query(AdminAuditLog).count()

    for denied in ("finance_admin", "content_moderator", "support_admin", "traveler", "provider"):
        res = client.post(
            f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
            json={"decision": "approve"},
            headers=role_headers[denied],
        )
        assert res.status_code == 403, f"Expected 403 for {denied} on decision='approve', got {res.status_code}"
        db_session.refresh(camp_pending)
        assert camp_pending.status == "pending_approval"
        assert db_session.query(AdminAuditLog).count() == audit_before

    # operations_admin can approve
    res_ops_approve = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "approve"},
        headers=role_headers["operations_admin"],
    )
    assert res_ops_approve.status_code == 200
    db_session.refresh(camp_pending)
    assert camp_pending.status == "approved"

    # 2. Financial activation: operations_admin must be blocked (403) with zero side effects
    audit_before_act = db_session.query(AdminAuditLog).count()
    for denied in ("operations_admin", "content_moderator", "support_admin", "traveler", "provider"):
        res = client.post(
            f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
            json={"decision": "activate", "payment_reference": "PAYHERE-TX-10001"},
            headers=role_headers[denied],
        )
        assert res.status_code == 403, f"Expected 403 for {denied} on decision='activate', got {res.status_code}"
        db_session.refresh(camp_pending)
        assert camp_pending.status == "approved"
        assert db_session.query(AdminAuditLog).count() == audit_before_act

    # finance_admin can activate with payment_reference
    res_fin_activate = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "activate", "payment_reference": "PAYHERE-TX-10001"},
        headers=role_headers["finance_admin"],
    )
    assert res_fin_activate.status_code == 200
    db_session.refresh(camp_pending)
    assert camp_pending.status == "active"

    # 3. Operational pause: finance_admin blocked (403) with zero side effects; operations_admin allowed
    res_fin_pause = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "pause"},
        headers=role_headers["finance_admin"],
    )
    assert res_fin_pause.status_code == 403
    db_session.refresh(camp_pending)
    assert camp_pending.status == "active"

    res_ops_pause = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "pause"},
        headers=role_headers["operations_admin"],
    )
    assert res_ops_pause.status_code == 200
    db_session.refresh(camp_pending)
    assert camp_pending.status == "paused"

    # 4. Legacy 'admin' and 'super_admin' can perform both editorial and financial actions
    res_legacy_activate = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "activate", "payment_reference": "PAYHERE-TX-10002"},
        headers=role_headers["admin"],
    )
    assert res_legacy_activate.status_code == 200
    db_session.refresh(camp_pending)
    assert camp_pending.status == "active"

    res_super_pause = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "pause"},
        headers=role_headers["super_admin"],
    )
    assert res_super_pause.status_code == 200
    db_session.refresh(camp_pending)
    assert camp_pending.status == "paused"

    # 5. Unknown decision is rejected with 422/400 (deny-by-default)
    res_unknown = client.post(
        f"/api/v1/admin/rentals/promotions/{camp_pending.id}/review",
        json={"decision": "force_override"},
        headers=role_headers["super_admin"],
    )
    assert res_unknown.status_code in (400, 422)


def test_tour_guide_campaign_decision_boundaries_and_zero_side_effects(client, db_session, role_headers):
    """
    Verify POST /api/v1/admin/tour-guides/promotions/campaigns/{campaign_id}/decision:
    - All 4 actions ('approve', 'reject', 'pause', 'cancel') are restricted to super_admin, legacy admin, and operations_admin.
    - finance_admin, content_moderator, support_admin receive 403 BEFORE any DB mutation or audit log.
    - Unknown actions are rejected (422/400).
    """
    prov_user, _ = _create_user_and_headers(
        db_session,
        email="tg_promo_prov@ceylongo.com",
        role="provider",
    )
    profile = ProviderProfile(
        user_id=prov_user.id,
        business_name="Kandy Heritage Guides",
        provider_type="tour_guide",
        phone="+94771112233",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    guide = TourGuide(
        provider_profile_id=profile.id,
        bio="Licensed national tour guide",
        base_city="Kandy",
        base_district="Kandy",
        daily_rate=Decimal("15000.00"),
        currency="LKR",
        years_of_experience=8,
        approval_status="approved",
        verification_status="verified",
        is_active=True,
        is_available=True,
    )
    db_session.add(guide)
    db_session.commit()
    db_session.refresh(guide)

    now_naive = datetime.now(UTC).replace(tzinfo=None)
    campaign = PromotionCampaign(
        provider_profile_id=profile.id,
        campaign_name="Kandy Cultural Guide Spotlight",
        target_module="tour_guide",
        budget=Decimal("8000.00"),
        spent_amount=Decimal("0.00"),
        currency="LKR",
        status="pending_approval",
        starts_at=now_naive - timedelta(days=1),
        ends_at=now_naive + timedelta(days=10),
    )
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)

    audit_before = db_session.query(AdminAuditLog).count()

    for action in ("approve", "reject", "pause", "cancel"):
        for denied in ("finance_admin", "content_moderator", "support_admin", "traveler", "provider"):
            res = client.post(
                f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
                json={"action": action, "reason": "Testing RBAC block"},
                headers=role_headers[denied],
            )
            assert res.status_code == 403, f"Expected 403 for {denied} on action={action}, got {res.status_code}"
            db_session.refresh(campaign)
            assert campaign.status == "pending_approval"
            assert db_session.query(AdminAuditLog).count() == audit_before

    # operations_admin can approve, pause, and cancel
    res_ops_approve = client.post(
        f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
        json={"action": "approve"},
        headers=role_headers["operations_admin"],
    )
    assert res_ops_approve.status_code == 200
    db_session.refresh(campaign)
    assert campaign.status == "active"

    res_ops_pause = client.post(
        f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
        json={"action": "pause"},
        headers=role_headers["operations_admin"],
    )
    assert res_ops_pause.status_code == 200
    db_session.refresh(campaign)
    assert campaign.status == "paused"

    # legacy admin can re-approve
    res_legacy_approve = client.post(
        f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
        json={"action": "approve"},
        headers=role_headers["admin"],
    )
    assert res_legacy_approve.status_code == 200
    db_session.refresh(campaign)
    assert campaign.status == "active"

    # super_admin can cancel
    res_super_cancel = client.post(
        f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
        json={"action": "cancel", "reason": "Campaign concluded"},
        headers=role_headers["super_admin"],
    )
    assert res_super_cancel.status_code == 200
    db_session.refresh(campaign)
    assert campaign.status == "cancelled"

    # Unknown action is rejected with 422
    res_unknown = client.post(
        f"/api/v1/admin/tour-guides/promotions/campaigns/{campaign.id}/decision",
        json={"action": "activate_without_review"},
        headers=role_headers["super_admin"],
    )
    assert res_unknown.status_code == 422


# ==============================================================================
# 9. Security Hardening: Sensitive Driving License Document Access
# ==============================================================================


def test_rental_eligibility_document_access_by_role_and_owner(client, db_session, role_headers, tmp_path, monkeypatch):
    """
    Verify GET /api/v1/rental-eligibility/document/{filename}:
    - Allows document owner (traveler).
    - Allows super_admin, legacy 'admin', and operations_admin.
    - Blocks finance_admin, content_moderator, support_admin, non-owning travelers, and providers with 403.
    """
    monkeypatch.setenv("RENTAL_DOCUMENTS_DIR", str(tmp_path))
    doc_filename = "license_front_traveler_501.jpg"
    doc_path = tmp_path / doc_filename
    doc_path.write_bytes(b"\xff\xd8\xff\xe0JFIF_TEST_DRIVING_LICENSE")

    owner_user, owner_headers = _create_user_and_headers(
        db_session,
        email="doc_owner_traveler@ceylongo.com",
        role="traveler",
    )
    other_traveler, other_traveler_headers = _create_user_and_headers(
        db_session,
        email="other_traveler@ceylongo.com",
        role="traveler",
    )

    eligibility = RentalEligibilityCheck(
        user_id=owner_user.id,
        driver_license_number="B1234567",
        license_country="Sri Lanka",
        license_expiry_date=date.today() + timedelta(days=365),
        license_front_image=f"/api/v1/rental-eligibility/document/{doc_filename}",
        verification_status="pending",
    )
    db_session.add(eligibility)
    db_session.commit()

    url = f"/api/v1/rental-eligibility/document/{doc_filename}"

    # 1. Document owner allowed (200)
    res_owner = client.get(url, headers=owner_headers)
    assert res_owner.status_code == 200

    # 2. Authorized admin roles allowed (200): super_admin, legacy admin, operations_admin
    for allowed_admin in ("super_admin", "admin", "operations_admin"):
        res = client.get(url, headers=role_headers[allowed_admin])
        assert res.status_code == 200, f"Expected 200 for {allowed_admin} on {url}, got {res.status_code}"

    # 3. Non-authorized admin roles & non-owners blocked (403): finance_admin, content_moderator, support_admin, provider, other traveler
    for denied_role in ("finance_admin", "content_moderator", "support_admin", "provider", "traveler"):
        res = client.get(url, headers=role_headers[denied_role])
        assert res.status_code == 403, f"Expected 403 for {denied_role} on {url}, got {res.status_code}"

    res_other_traveler = client.get(url, headers=other_traveler_headers)
    assert res_other_traveler.status_code == 403

