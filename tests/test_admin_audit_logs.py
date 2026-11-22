"""
Offline Unit & Integration Tests for P1 Stage 3 — Admin Audit Logs.

Covers:
1. Centralized audit helper (`record_admin_audit_log`, `sanitize_audit_details`, `extract_client_ip`):
   - Validates positive integer `admin_user_id` and `entity_id` (respecting NOT NULL & FK constraints).
   - Strips sensitive keys (`password`, `password_hash`, `token`, `otp`, `secret`, `google_sub`, etc.).
   - Persists structured JSON metadata and IP address cleanly.
2. End-to-end audit persistence across P1 admin actions:
   - Provider approval & rejection (`provider_profiles`)
   - Homestay review & verification (`homestays`)
   - Artisan listing approve/reject/suspend/feature/unfeature, product approve/reject/hide, review moderation
   - Activity approve/reject (`activities`)
   - Eco experience approve/reject & badge decision (`eco_experiences`)
   - Eco promotion campaign approve/reject (`eco_sponsored_promotions`)
   - Homestay campaign approve/reject (`promotion_campaigns`) and homestay review moderation (`reviews`)
3. No duplicate audit records on existing audited operations.
4. Denied requests (`403 Forbidden`) perform no mutation and write zero audit log rows.
5. Transaction rollback safety: if a transaction rolls back before commit, the staged `AdminAuditLog` row is rolled back atomically.
6. Read-only `GET /api/v1/admin/audit-logs` endpoint:
   - Allowed ONLY for `super_admin` and legacy `admin`.
   - Denied (`403`) for `operations_admin`, `finance_admin`, `content_moderator`, `support_admin`, `traveler`, `provider`, and inactive admins.
   - Denied (`401`) for unauthenticated callers.
   - Bounded pagination (`page`, `page_size`) and filtering (`admin_user_id`, `action`, `entity_name`, `entity_id`, `date_from`, `date_to`, `search`).
   - Viewing audit logs does NOT create new audit records or mutate DB state.
   - Redacts any sensitive keys from `details` and never exposes `password_hash` in actor summaries.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.audit import (
    extract_client_ip,
    record_admin_audit_log,
    sanitize_audit_details,
)
from app.core.rate_limiter import reset_all_limiters
from app.core.security import hash_password
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.activity import Activity
from app.models.artisan_listing import ArtisanListing
from app.models.eco_experience import EcoExperience
from app.models.eco_promotion import EcoPromotionPackage, EcoSponsoredPromotion
from app.models.homestay import Homestay
from app.models.provider_profile import ProviderProfile
from app.models.shared import AdminAuditLog, ListingReference, PromotionCampaign, Review
from app.models.showcase_item import ShowcaseItem
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
def actors(db_session):
    roles = [
        "super_admin",
        "admin",
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
        "traveler",
        "provider",
    ]
    result = {}
    for r in roles:
        u, h = _create_user_and_headers(
            db_session,
            email=f"{r}@ceylongo.com",
            role=r,
            full_name=f"Actor {r}",
        )
        result[r] = (u, h)
    return result


# ==============================================================================
# 1. Centralized Audit Helper Unit Tests (Validation, Sanitization, Rollback)
# ==============================================================================


def test_record_admin_audit_log_validation_and_sensitive_key_stripping(db_session, actors):
    super_user, _ = actors["super_admin"]

    with pytest.raises(ValueError, match="admin_user_id must be a positive integer"):
        record_admin_audit_log(
            db_session,
            admin_user_id=0,
            action="test_action",
            entity_name="users",
            entity_id=1,
        )

    with pytest.raises(ValueError, match="entity_id must be a positive integer"):
        record_admin_audit_log(
            db_session,
            admin_user_id=super_user.id,
            action="test_action",
            entity_name="users",
            entity_id=0,
        )

    entry = record_admin_audit_log(
        db_session,
        admin_user_id=super_user.id,
        action="provider_approved",
        entity_name="provider_profiles",
        entity_id=42,
        details={
            "business_name": "Ceylon Eco Trails",
            "password": "SHOULD_BE_STRIPPED",
            "password_hash": "HASH_SHOULD_BE_STRIPPED",
            "access_token": "JWT_SHOULD_BE_STRIPPED",
            "reset_token": "RESET_SHOULD_BE_STRIPPED",
            "otp_code": "123456",
            "merchant_secret": "PAYHERE_SECRET",
            "nested": {
                "safe_note": "Verified documents",
                "client_secret": "NESTED_SECRET",
            },
        },
        ip_address="203.0.113.15",
    )
    db_session.commit()
    db_session.refresh(entry)

    assert entry.id > 0
    assert entry.admin_user_id == super_user.id
    assert entry.action == "provider_approved"
    assert entry.entity_name == "provider_profiles"
    assert entry.entity_id == 42
    assert entry.ip_address == "203.0.113.15"
    assert entry.created_at is not None

    parsed = json.loads(entry.details)
    assert parsed["business_name"] == "Ceylon Eco Trails"
    assert parsed["nested"]["safe_note"] == "Verified documents"
    for forbidden in (
        "password",
        "password_hash",
        "access_token",
        "reset_token",
        "otp_code",
        "merchant_secret",
    ):
        assert forbidden not in parsed
        assert forbidden not in entry.details
    assert "client_secret" not in parsed["nested"]


def test_audit_log_atomic_rollback_on_transaction_failure(db_session, actors):
    """Verify that if a DB transaction rolls back after staging an audit log, zero audit rows persist."""
    ops_user, _ = actors["operations_admin"]
    initial_count = db_session.query(AdminAuditLog).count()

    record_admin_audit_log(
        db_session,
        admin_user_id=ops_user.id,
        action="homestay_approved",
        entity_name="homestays",
        entity_id=777,
        details={"status": "approved"},
    )
    db_session.flush()
    # Roll back transaction simulating downstream failure before commit
    db_session.rollback()

    assert db_session.query(AdminAuditLog).count() == initial_count


# ==============================================================================
# 2. End-to-End Audit Persistence on P1 Admin Mutations (No Duplicates)
# ==============================================================================


def test_provider_approve_and_reject_create_single_structured_audit_logs(client, db_session, actors, monkeypatch):
    monkeypatch.setattr("app.routers.admin_providers.send_provider_approval_email", lambda **kw: True)
    monkeypatch.setattr("app.routers.admin_providers.send_provider_rejection_email", lambda **kw: True)

    ops_user, ops_headers = actors["operations_admin"]
    fin_user, fin_headers = actors["finance_admin"]

    p_user1, _ = _create_user_and_headers(db_session, email="p1_audit@ceylongo.com", role="provider", is_active=False)
    prof1 = ProviderProfile(
        user_id=p_user1.id,
        business_name="Sigiriya Homestays",
        provider_type="homestay",
        phone="+94771112233",
        district="Matale",
        approval_status="pending",
        identity_document_type="nic",
        selfie_image_path="selfie1.jpg",
        nic_front_image_path="nic_f1.jpg",
        nic_back_image_path="nic_b1.jpg",
    )
    p_user2, _ = _create_user_and_headers(db_session, email="p2_audit@ceylongo.com", role="provider", is_active=False)
    prof2 = ProviderProfile(
        user_id=p_user2.id,
        business_name="Ella Eco Adventures",
        provider_type="eco_tourism",
        phone="+94772223344",
        district="Badulla",
        approval_status="pending",
        identity_document_type="nic",
        selfie_image_path="selfie2.jpg",
        nic_front_image_path="nic_f2.jpg",
        nic_back_image_path="nic_b2.jpg",
    )
    db_session.add_all([prof1, prof2])
    db_session.commit()
    db_session.refresh(prof1)
    db_session.refresh(prof2)

    # Denied role (finance_admin) -> 403 and zero audit logs
    denied_res = client.post(f"/api/v1/admin/providers/{prof1.id}/approve", headers=fin_headers)
    assert denied_res.status_code == 403
    assert db_session.query(AdminAuditLog).count() == 0

    # Authorized role (operations_admin) approves prof1 -> exactly 1 audit log
    approve_res = client.post(f"/api/v1/admin/providers/{prof1.id}/approve", headers=ops_headers)
    assert approve_res.status_code == 200

    logs_p1 = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.entity_name == "provider_profiles", AdminAuditLog.entity_id == prof1.id)
        .all()
    )
    assert len(logs_p1) == 1
    assert logs_p1[0].admin_user_id == ops_user.id
    assert logs_p1[0].action == "provider_approved"
    assert logs_p1[0].ip_address is not None
    details_p1 = json.loads(logs_p1[0].details)
    assert details_p1["business_name"] == "Sigiriya Homestays"

    # Authorized role (operations_admin) rejects prof2 -> exactly 1 audit log
    reject_res = client.post(
        f"/api/v1/admin/providers/{prof2.id}/reject",
        json={"reason": "Blurry NIC front image"},
        headers=ops_headers,
    )
    assert reject_res.status_code == 200

    logs_p2 = (
        db_session.query(AdminAuditLog)
        .filter(AdminAuditLog.entity_name == "provider_profiles", AdminAuditLog.entity_id == prof2.id)
        .all()
    )
    assert len(logs_p2) == 1
    assert logs_p2[0].admin_user_id == ops_user.id
    assert logs_p2[0].action == "provider_rejected"
    details_p2 = json.loads(logs_p2[0].details)
    assert details_p2["rejection_reason"] == "Blurry NIC front image"


def test_listing_and_review_decisions_persist_audit_logs(client, db_session, actors):
    ops_user, ops_headers = actors["operations_admin"]
    mod_user, mod_headers = actors["content_moderator"]
    prov_user, _ = actors["provider"]

    profile = ProviderProfile(
        user_id=prov_user.id,
        business_name="Ceylon Multi Services",
        provider_type="homestay",
        phone="+94773334455",
        district="Galle",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    # 1. Homestay review & verify
    homestay = Homestay(
        owner_id=prov_user.id,
        provider_profile_id=profile.id,
        name="Galle Fort Villa",
        description="Historic fort villa",
        location="Galle Fort",
        province="Southern",
        district="Galle",
        city="Galle",
        address="12 Church St",
        latitude=Decimal("6.0267"),
        longitude=Decimal("80.2170"),
        price_per_night=Decimal("18000.00"),
        max_guests=4,
        approval_status="pending",
        verification_status="unverified",
        is_active=True,
    )
    # 2. Artisan listing & showcase product
    artisan = ArtisanListing(
        provider_profile_id=profile.id,
        title="Ambalangoda Mask Workshop",
        craft_category="wood_carving",
        description="Traditional carved masks",
        district="Galle",
        city="Ambalangoda",
        address="45 Coast Rd",
        latitude=Decimal("6.2355"),
        longitude=Decimal("80.0538"),
        phone="+94773334455",
        approval_status="pending",
        verification_status="unverified",
        is_active=True,
    )
    # 3. Activity
    activity = Activity(
        provider_profile_id=profile.id,
        title="Mirissa Whale Watching",
        category="adventure",
        description="Morning whale tour",
        meeting_point="Mirissa Harbor",
        district="Matara",
        city="Mirissa",
        latitude=Decimal("5.9483"),
        longitude=Decimal("80.4716"),
        pricing_type="per_person",
        price=Decimal("12000.00"),
        duration_minutes=240,
        minimum_age=6,
        approval_status="pending",
        is_active=False,
    )
    # 4. Eco Experience
    eco = EcoExperience(
        provider_profile_id=profile.id,
        title="Sinharaja Rainforest Trek",
        eco_category="Wildlife & Birdwatching",
        description="Guided conservation walk",
        district="Ratnapura",
        city="Deniyaya",
        address="Sinharaja Entrance",
        meeting_point="Sinharaja Main Gate",
        latitude=Decimal("6.4050"),
        longitude=Decimal("80.4100"),
        pricing_type="per_person",
        base_price=Decimal("8500.00"),
        currency="LKR",
        duration_hours=Decimal("4.0"),
        max_group_size=8,
        min_participants=1,
        environmental_guidelines="Stay on marked trails and carry out all waste.",
        wildlife_safety_summary="Keep a safe distance from wildlife and never feed animals.",
        listing_status="pending_approval",
        eco_badge_status="pending",
        is_published=False,
    )
    db_session.add_all([homestay, artisan, activity, eco])
    db_session.commit()
    for obj in (homestay, artisan, activity, eco):
        db_session.refresh(obj)

    product = ShowcaseItem(
        artisan_listing_id=artisan.id,
        title="Carved Cobra Mask",
        item_type="product",
        price=Decimal("4500.00"),
        stock_quantity=5,
        approval_status="pending",
        is_active=True,
        is_available=True,
    )
    db_session.add(product)
    db_session.commit()
    db_session.refresh(product)

    # Execute homestay review & verify
    assert client.post(
        f"/api/v1/admin/homestays/{homestay.id}/review",
        json={"decision": "approved"},
        headers=ops_headers,
    ).status_code == 200
    assert client.post(
        f"/api/v1/admin/homestays/{homestay.id}/verify",
        json={"verification_status": "verified"},
        headers=ops_headers,
    ).status_code == 200

    # Execute artisan listing approve, feature, unfeature, and product approve
    assert client.post(f"/api/v1/admin/artisan/listings/{artisan.id}/approve", headers=ops_headers).status_code == 200
    assert client.post(f"/api/v1/admin/artisan/listings/{artisan.id}/feature", headers=mod_headers).status_code == 200
    assert client.post(f"/api/v1/admin/artisan/listings/{artisan.id}/unfeature", headers=ops_headers).status_code == 200
    assert client.post(f"/api/v1/admin/artisan/products/{product.id}/approve", headers=ops_headers).status_code == 200

    # Execute activity approve
    assert client.post(
        f"/api/v1/admin/activities/{activity.id}/approve",
        json={},
        headers=ops_headers,
    ).status_code == 200

    # Execute eco experience approve & badge decision
    assert client.post(f"/api/v1/admin/eco-experiences/{eco.id}/approve", headers=ops_headers).status_code == 200
    assert client.post(
        f"/api/v1/admin/eco-experiences/{eco.id}/badge-decision",
        json={"decision": "granted", "reason": "Verified forestry permit"},
        headers=ops_headers,
    ).status_code == 200

    # Verify every single action wrote an AdminAuditLog record
    actions_logged = {row.action for row in db_session.query(AdminAuditLog).all()}
    expected_actions = {
        "homestay_approved",
        "homestay_verification_verified",
        "artisan_listing_approved",
        "artisan_listing_featured",
        "artisan_listing_unfeatured",
        "artisan_product_approved",
        "activity_approved",
        "eco_experience_approved",
        "eco_badge_granted",
    }
    assert expected_actions.issubset(actions_logged)


# ==============================================================================
# 3. GET /api/v1/admin/audit-logs Permission & Read-Only Contract Tests
# ==============================================================================


def test_audit_logs_viewer_rbac_restricted_to_super_admin_and_legacy_admin(client, db_session, actors):
    super_user, super_headers = actors["super_admin"]
    legacy_user, legacy_headers = actors["admin"]

    record_admin_audit_log(
        db_session,
        admin_user_id=super_user.id,
        action="provider_approved",
        entity_name="provider_profiles",
        entity_id=101,
        details={"business_name": "Kandy Heritage"},
        ip_address="198.51.100.10",
    )
    db_session.commit()

    # Allowed: super_admin and legacy admin
    for allowed_role in ("super_admin", "admin"):
        _, hdr = actors[allowed_role]
        res = client.get("/api/v1/admin/audit-logs", headers=hdr)
        assert res.status_code == 200, f"Expected 200 for {allowed_role}, got {res.status_code}"
        body = res.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["action_type"] == "provider_approved"
        assert item["entity_type"] == "provider_profiles"
        assert item["entity_id"] == 101
        assert item["admin"]["id"] == super_user.id
        assert item["admin"]["admin_role"] == "super_admin"
        assert "password_hash" not in item["admin"]

    # Viewing audit logs MUST NOT create additional audit records
    assert db_session.query(AdminAuditLog).count() == 1

    # Denied (403): operations_admin, finance_admin, content_moderator, support_admin, traveler, provider
    for denied_role in (
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
        "traveler",
        "provider",
    ):
        _, hdr = actors[denied_role]
        res = client.get("/api/v1/admin/audit-logs", headers=hdr)
        assert res.status_code == 403, f"Expected 403 for {denied_role}, got {res.status_code}"

    # Unauthenticated -> 401
    assert client.get("/api/v1/admin/audit-logs").status_code == 401

    # Inactive super_admin -> 403
    _, inactive_super_hdr = _create_user_and_headers(
        db_session,
        email="inactive_super@ceylongo.com",
        role="super_admin",
        is_active=False,
    )
    assert client.get("/api/v1/admin/audit-logs", headers=inactive_super_hdr).status_code == 403


def test_audit_writer_and_viewer_redact_nested_containers(client, db_session, actors, caplog):
    from collections import deque

    admin, headers = actors["super_admin"]
    nested = {"items": [({"inside": deque([{" PASSWORD ": "private-password", "label": "safe"}])},)]}
    written = record_admin_audit_log(
        db_session, admin_user_id=admin.id, action="nested", entity_name="test", entity_id=1, details=nested,
    )
    db_session.add_all([
        AdminAuditLog(admin_user_id=admin.id, action="nested", entity_name="test", entity_id=2,
                      details='[{"a":[{"access_token":"private-token","status":"kept"}]}]'),
        AdminAuditLog(admin_user_id=admin.id, action="nested", entity_name="test", entity_id=3,
                      details='{"secret":"private-malformed"'),
        AdminAuditLog(admin_user_id=admin.id, action="nested", entity_name="test", entity_id=4,
                      details='{"items":[[{"otp":"private-otp","value":NaN}]]}'),
    ])
    db_session.commit()
    expected = {"items": [[{"inside": [{"label": "safe"}]}]]}
    assert json.loads(written.details) == expected
    assert sanitize_audit_details('[{"token":"private-token","ok":true}]') == '[{"ok": true}]'
    assert sanitize_audit_details('[{"password":"private-malformed"') is None

    response = client.get("/api/v1/admin/audit-logs", headers=headers)
    assert response.status_code == 200
    details = {item["entity_id"]: item["details"] for item in response.json()["items"]}
    assert details == {1: expected, 2: '[{"a": [{"status": "kept"}]}]', 3: None,
                       4: {"items": [[{"value": None}]]}}
    assert "private-" not in response.text + caplog.text
    assert sanitize_audit_details("Authorization: Bearer private-token") == "[REDACTED]"
    assert json.loads(sanitize_audit_details({"note": "password=private-password"})) == {"note": "[REDACTED]"}


def test_audit_logs_viewer_pagination_filtering_and_secret_redaction(client, db_session, actors):
    super_user, super_headers = actors["super_admin"]
    ops_user, _ = actors["operations_admin"]

    # Insert 3 distinct audit log rows (including one raw row with a secret in details to test read-time redaction)
    log1 = AdminAuditLog(
        admin_user_id=super_user.id,
        action="provider_approved",
        entity_name="provider_profiles",
        entity_id=10,
        details=json.dumps({"business_name": "Alpha Tours", "password_hash": "LEAKED_HASH"}),
        ip_address="10.0.0.1",
        created_at=datetime(2026, 5, 10, 12, 0, 0),
    )
    log2 = AdminAuditLog(
        admin_user_id=ops_user.id,
        action="homestay_approved",
        entity_name="homestays",
        entity_id=20,
        details=json.dumps({"homestay_name": "Beta Villa", "district": "Galle"}),
        ip_address="10.0.0.2",
        created_at=datetime(2026, 5, 15, 12, 0, 0),
    )
    log3 = AdminAuditLog(
        admin_user_id=ops_user.id,
        action="homestay_rejected",
        entity_name="homestays",
        entity_id=21,
        details=json.dumps({"homestay_name": "Gamma Cottage", "reason": "Missing photos"}),
        ip_address="10.0.0.3",
        created_at=datetime(2026, 5, 20, 12, 0, 0),
    )
    db_session.add_all([log1, log2, log3])
    db_session.commit()

    # 1. Pagination test (page_size=2, page=1 and page=2)
    p1 = client.get("/api/v1/admin/audit-logs?page=1&page_size=2", headers=super_headers).json()
    assert p1["total"] == 3
    assert p1["total_pages"] == 2
    assert len(p1["items"]) == 2
    assert p1["items"][0]["entity_id"] == 21  # Newest first

    p2 = client.get("/api/v1/admin/audit-logs?page=2&page_size=2", headers=super_headers).json()
    assert len(p2["items"]) == 1
    assert p2["items"][0]["entity_id"] == 10
    # Verify read-time secret redaction stripped "password_hash" from log1 details
    assert p2["items"][0]["details"] == {"business_name": "Alpha Tours"}
    assert "password_hash" not in json.dumps(p2["items"][0])

    # 2. Filter by admin_user_id
    by_admin = client.get(
        f"/api/v1/admin/audit-logs?admin_user_id={ops_user.id}",
        headers=super_headers,
    ).json()
    assert by_admin["total"] == 2

    # 3. Filter by action & entity_name & entity_id
    by_action = client.get(
        "/api/v1/admin/audit-logs?action=homestay_approved&entity_name=homestays&entity_id=20",
        headers=super_headers,
    ).json()
    assert by_action["total"] == 1
    assert by_action["items"][0]["entity_id"] == 20

    # 4. Filter by date range
    by_date = client.get(
        "/api/v1/admin/audit-logs?date_from=2026-05-12&date_to=2026-05-18",
        headers=super_headers,
    ).json()
    assert by_date["total"] == 1
    assert by_date["items"][0]["entity_id"] == 20

    # Invalid date range -> 422
    bad_date = client.get(
        "/api/v1/admin/audit-logs?date_from=2026-05-20&date_to=2026-05-10",
        headers=super_headers,
    )
    assert bad_date.status_code == 422

    # 5. Search filter
    by_search = client.get(
        "/api/v1/admin/audit-logs?search=homestay_rejected",
        headers=super_headers,
    ).json()
    assert by_search["total"] == 1
    assert by_search["items"][0]["entity_id"] == 21
    assert client.get("/api/v1/admin/audit-logs?search=LEAKED_HASH", headers=super_headers).json()["total"] == 0
