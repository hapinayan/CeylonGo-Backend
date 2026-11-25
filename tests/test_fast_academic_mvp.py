"""
CeylonGo Academic MVP — Comprehensive Offline Test Suite
Validates the entire 17+1 unified schema, FastAPI routers, business services, and constraints using isolated in-memory SQLite.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase

# 1. Setup in-memory SQLite database
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# 2. Dependency override
def override_get_mvp_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app = create_mvp_app()
app.dependency_overrides[get_mvp_db] = override_get_mvp_db
client = TestClient(app)


@pytest.fixture(autouse=True, scope="module")
def setup_database():
    MVPBase.metadata.create_all(bind=test_engine)
    yield
    MVPBase.metadata.drop_all(bind=test_engine)


def test_01_health_and_root_endpoints():
    res = client.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["schema_profile"] == "17+1 Unified MVP"
    assert data["version"] == "1.1.0"

    res_h = client.get("/health")
    assert res_h.status_code == 200
    assert res_h.json()["status"] == "healthy"


def test_02_traveler_registration_and_authentication(verify_traveler_signup):
    # Register traveler
    reg_payload = {
        "email": "traveler.kasun@ceylongo.lk",
        "full_name": "Kasun Perera",
        "phone": "+94771234567",
        "password": "Password123!",
        "role": "traveler",
    }
    r = client.post("/api/auth/register", json=reg_payload)
    verify_traveler_signup(client, r, "/api/auth")

    # Duplicate registration rejected
    r_dup = client.post("/api/auth/register", json=reg_payload)
    assert r_dup.status_code == 409

    # Login
    login_payload = {
        "email": "traveler.kasun@ceylongo.lk",
        "password": "Password123!",
    }
    r_login = client.post("/api/auth/login", json=login_payload)
    assert r_login.status_code == 200
    token_data = r_login.json()
    assert token_data["user"]["email"] == "traveler.kasun@ceylongo.lk"
    assert token_data["user"]["role"] == "traveler"
    assert token_data["user"]["is_active"] is True
    assert "access_token" in token_data
    token = token_data["access_token"]

    # Auth me
    headers = {"Authorization": f"Bearer {token}"}
    r_me = client.get("/api/auth/me", headers=headers)
    assert r_me.status_code == 200
    assert r_me.json()["full_name"] == "Kasun Perera"

    # Logout (increments auth_version)
    r_logout = client.post("/api/auth/logout", headers=headers)
    assert r_logout.status_code == 200

    # Old token rejected
    r_old = client.get("/api/auth/me", headers=headers)
    assert r_old.status_code == 401


def test_03_password_reset_flow(verify_traveler_signup):
    # Register user for reset
    reg = client.post(
        "/api/auth/register",
        json={
            "email": "reset.user@ceylongo.lk",
            "full_name": "Reset User",
            "password": "OldPassword123!",
            "role": "traveler",
        },
    )
    verify_traveler_signup(client, reg, "/api/auth")

    # Request reset
    r_req = client.post("/api/auth/password-reset/request", json={"email": "reset.user@ceylongo.lk"})
    assert r_req.status_code == 200
    assert "If an account exists" in r_req.json()["message"]
    assert "reset_token_demo" not in r_req.json()

    # For testing confirm workflow, generate token via service directly
    db = TestingSessionLocal()
    from app.services.mvp_services import MVPAuthService
    from app.schemas.mvp import PasswordResetRequest
    raw_token = MVPAuthService.request_password_reset(db, PasswordResetRequest(email="reset.user@ceylongo.lk"), send_email=False)
    db.close()

    # Confirm reset
    r_conf = client.post(
        "/api/auth/password-reset/confirm",
        json={"token": raw_token, "new_password": "NewStrongPassword456!"},
    )
    assert r_conf.status_code == 200

    # Login with old password fails
    r_fail = client.post(
        "/api/auth/login",
        json={"email": "reset.user@ceylongo.lk", "password": "OldPassword123!"},
    )
    assert r_fail.status_code == 401

    # Login with new password succeeds
    r_succ = client.post(
        "/api/auth/login",
        json={"email": "reset.user@ceylongo.lk", "password": "NewStrongPassword456!"},
    )
    assert r_succ.status_code == 200


def test_04_provider_onboarding_and_documents(seed_mvp_account):
    seed_mvp_account(
        TestingSessionLocal, email="host.ella@ceylongo.lk", name="Nimal Silva",
        password="ProviderPass123!", role="provider",
        business=("Ella View Homestay & Tours", "+94719876543", "Badulla"),
    )
    r_login = client.post(
        "/api/auth/login",
        json={"email": "host.ella@ceylongo.lk", "password": "ProviderPass123!"},
    )
    token = r_login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/providers/me", headers=headers).json()["verification_status"] == "approved"

    # Document upload: identity document (unit_id must be None)
    dummy_sha = "a" * 64
    r_doc = client.post(
        "/api/providers/documents",
        json={
            "kind": "identity",
            "storage_key": "providers/docs/nimal_nic.jpg",
            "sha256_hex": dummy_sha,
            "unit_id": None,
        },
        headers=headers,
    )
    assert r_doc.status_code == 201

    # Vehicle document without unit_id fails cross-row consistency
    r_doc_fail = client.post(
        "/api/providers/documents",
        json={
            "kind": "insurance",
            "storage_key": "providers/docs/vehicle_ins.pdf",
            "sha256_hex": dummy_sha,
            "unit_id": None,
        },
        headers=headers,
    )
    assert r_doc_fail.status_code == 400


def test_05_admin_approval_and_audit_logging(seed_mvp_account):
    seed_mvp_account(
        TestingSessionLocal, email="admin.system@ceylongo.lk", name="Chief Admin",
        password="AdminSecurePassword123!", role="admin",
    )
    r_adm = client.post(
        "/api/auth/login",
        json={"email": "admin.system@ceylongo.lk", "password": "AdminSecurePassword123!"},
    )
    admin_token = r_adm.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Admin approves provider profile 1
    r_dec = client.post(
        "/api/admin/providers/1/decision",
        json={"verification_status": "approved", "decision_reason": "Verified business registration and NIC."},
        headers=admin_headers,
    )
    assert r_dec.status_code == 200
    assert r_dec.json()["verification_status"] == "approved"

    # Check audit log was written
    r_logs = client.get("/api/admin/audit-logs", headers=admin_headers)
    assert r_logs.status_code == 200
    logs = r_logs.json()
    assert len(logs) >= 1
    assert logs[0]["action"] == "provider_decision"
    assert logs[0]["subject_provider_id"] == 1


def test_06_listings_inventory_and_booking_allocations(submit_mvp_listing):
    # Login provider
    r_login = client.post(
        "/api/auth/login",
        json={"email": "host.ella@ceylongo.lk", "password": "ProviderPass123!"},
    )
    provider_token = r_login.json()["access_token"]
    p_headers = {"Authorization": f"Bearer {provider_token}"}

    # 1. Create Homestay listing
    r_lst = client.post(
        "/api/listings",
        json={
            "category": "homestay",
            "title": "Ella Mountain Vista Villa",
            "description": "Scenic homestay overlooking Little Adam's Peak with organic breakfast.",
            "district": "Badulla",
            "locality": "Ella",
            "latitude": 6.8667,
            "longitude": 81.0466,
            "price": 7500.00,
            "currency": "LKR",
        },
        headers=p_headers,
    )
    assert r_lst.status_code == 201
    listing = r_lst.json()
    listing_id = listing["id"]
    assert listing["approval_status"] == "draft"
    assert listing["is_active"] is False

    # 2. Add inventory unit (Deluxe Room, capacity 2)
    r_unit = client.post(
        f"/api/listings/{listing_id}/units",
        json={"label": "Deluxe Mountain View Room", "capacity": 2},
        headers=p_headers,
    )
    assert r_unit.status_code == 201
    unit_id = r_unit.json()["id"]

    # 3. Add availability slot
    start_dt = (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    end_dt = start_dt + timedelta(days=3)
    r_slot = client.post(
        f"/api/listings/{listing_id}/slots",
        json={
            "unit_id": unit_id,
            "starts_at": start_dt.isoformat(),
            "ends_at": end_dt.isoformat(),
            "capacity": 2,
            "is_open": True,
        },
        headers=p_headers,
    )
    assert r_slot.status_code == 201
    submit_mvp_listing(client, p_headers, listing_id, homestay=True)

    # 4. Admin approves listing
    r_adm = client.post(
        "/api/auth/login",
        json={"email": "admin.system@ceylongo.lk", "password": "AdminSecurePassword123!"},
    )
    admin_token = r_adm.json()["access_token"]
    a_headers = {"Authorization": f"Bearer {admin_token}"}

    r_appr = client.post(
        f"/api/admin/listings/{listing_id}/decision",
        json={"approval_status": "approved", "decision_reason": "Meets academic MVP standards."},
        headers=a_headers,
    )
    assert r_appr.status_code == 200
    assert r_appr.json()["approval_status"] == "approved"
    assert r_appr.json()["is_active"] is True

    # 5. Public discovery check
    r_srch = client.get("/api/listings?category=homestay&district=Badulla")
    assert r_srch.status_code == 200
    results = r_srch.json()
    assert len(results) >= 1
    assert results[0]["id"] == listing_id

    # 6. Traveler books listing
    # Re-login traveler
    r_t_login = client.post(
        "/api/auth/login",
        json={"email": "traveler.kasun@ceylongo.lk", "password": "Password123!"},
    )
    t_token = r_t_login.json()["access_token"]
    t_headers = {"Authorization": f"Bearer {t_token}"}

    book_req_key = str(uuid.uuid4())
    r_book = client.post(
        "/api/bookings",
        json={
            "listing_id": listing_id,
            "starts_at": (start_dt + timedelta(hours=1)).isoformat(),
            "ends_at": (end_dt - timedelta(hours=1)).isoformat(),
            "quantity": 1,
            "request_key": book_req_key,
        },
        headers=t_headers,
    )
    assert r_book.status_code == 201
    booking = r_book.json()
    assert booking["status"] == "pending"
    assert float(booking["total_amount"]) == 22500.00
    booking_id = booking["id"]

    # 7. Overbooking rejection: requesting quantity 2 when remaining capacity is only 1
    r_overbook = client.post(
        "/api/bookings",
        json={
            "listing_id": listing_id,
            "starts_at": (start_dt + timedelta(hours=1)).isoformat(),
            "ends_at": (end_dt - timedelta(hours=1)).isoformat(),
            "quantity": 2,  # 1 + 2 = 3 > 2 capacity!
            "request_key": str(uuid.uuid4()),
        },
        headers=t_headers,
    )
    assert r_overbook.status_code == 409

    # 8. Simulated payment flow
    # Attempt 1: simulated failure
    pay_key = str(uuid.uuid4())
    r_pay_fail = client.post(
        "/api/payments/simulate",
        json={
            "booking_id": booking_id,
            "request_key": pay_key,
            "attempt_no": 1,
            "simulate_outcome": "failure",
        },
        headers=t_headers,
    )
    assert r_pay_fail.status_code == 200
    assert r_pay_fail.json()["status"] == "demo_failed"

    # Attempt 2: retry with simulated success
    r_pay_succ = client.post(
        "/api/payments/simulate",
        json={
            "booking_id": booking_id,
            "request_key": str(uuid.uuid4()),
            "attempt_no": 2,
            "simulate_outcome": "success",
        },
        headers=t_headers,
    )
    assert r_pay_succ.status_code == 200
    assert r_pay_succ.json()["status"] == "demo_succeeded"

    # Host approval is required after the simulated payment.
    r_b_check = client.get(f"/api/bookings/{booking_id}", headers=t_headers)
    assert r_b_check.status_code == 200
    assert r_b_check.json()["status"] == "pending"
    decision = client.patch(f"/api/bookings/{booking_id}/status", headers=p_headers,
                            json={"to_status": "confirmed"})
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "confirmed"

    # Attempt 3: duplicate success rejected
    r_pay_dup = client.post(
        "/api/payments/simulate",
        json={
            "booking_id": booking_id,
            "request_key": str(uuid.uuid4()),
            "attempt_no": 3,
            "simulate_outcome": "success",
        },
        headers=t_headers,
    )
    assert r_pay_dup.status_code == 409


def test_07_community_feed_and_moderation():
    # Login traveler
    r_t_login = client.post(
        "/api/auth/login",
        json={"email": "traveler.kasun@ceylongo.lk", "password": "Password123!"},
    )
    t_token = r_t_login.json()["access_token"]
    t_headers = {"Authorization": f"Bearer {t_token}"}

    # Create post
    r_post = client.post(
        "/api/community/posts",
        json={"body": "Climbed Ella Rock this morning — spectacular views!"},
        headers=t_headers,
    )
    assert r_post.status_code == 201
    post = r_post.json()
    post_id = post["id"]

    # Add comment
    r_comm = client.post(
        f"/api/community/posts/{post_id}/comments",
        json={"body": "Did you take the trail via Kithalella station?"},
        headers=t_headers,
    )
    assert r_comm.status_code == 201

    # Like post (toggle like)
    r_like = client.post(f"/api/community/posts/{post_id}/like", headers=t_headers)
    assert r_like.status_code == 200
    assert r_like.json()["liked"] is True

    # Unlike post (toggle)
    r_unlike = client.post(f"/api/community/posts/{post_id}/like", headers=t_headers)
    assert r_unlike.status_code == 200
    assert r_unlike.json()["liked"] is False

    # Admin moderates post
    r_adm = client.post(
        "/api/auth/login",
        json={"email": "admin.system@ceylongo.lk", "password": "AdminSecurePassword123!"},
    )
    admin_token = r_adm.json()["access_token"]
    a_headers = {"Authorization": f"Bearer {admin_token}"}

    r_mod = client.post(
        f"/api/admin/community/posts/{post_id}/moderate",
        json={"visibility": "hidden", "reason": "Requires review for external links."},
        headers=a_headers,
    )
    assert r_mod.status_code == 200


def test_08_ai_itinerary_generation():
    r_itin = client.post(
        "/api/itinerary/generate",
        json={
            "destination": "Ella",
            "duration_days": 3,
            "budget_level": "moderate",
            "interests": ["nature", "hiking", "homestays"],
        },
    )
    assert r_itin.status_code == 200
    data = r_itin.json()
    assert data["destination"] == "Ella"
    assert data["duration_days"] == 3
    assert len(data["days"]) == 3
    assert float(data["total_estimated_cost_lkr"]) > 0
    assert len(data["tips"]) >= 1
