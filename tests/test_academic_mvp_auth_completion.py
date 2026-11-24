"""
CeylonGo Academic MVP — Phase 03 Authentication Completion Test Suite
Validates check-email, Google OAuth, password reset, rate limiting, and session security.
Runs against an isolated in-memory SQLite database without touching the production database.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.rate_limiter import reset_all_limiters
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPUser
from app.schemas.mvp import PasswordResetRequest
from app.services.mvp_services import MVPAuthService

# 1. Isolated In-Memory SQLite Setup
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_mvp_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app = create_mvp_app()
app.dependency_overrides[get_mvp_db] = override_get_mvp_db
client = TestClient(app)


@pytest.fixture(autouse=True, scope="function")
def setup_and_teardown_db():
    MVPBase.metadata.create_all(bind=test_engine)
    reset_all_limiters()
    yield
    MVPBase.metadata.drop_all(bind=test_engine)
    reset_all_limiters()


# ============================================================================
# 1. Email Detection (/api/v1/auth/check-email)
# ============================================================================
def test_check_email_registered_user():
    # Provision existing user
    db = TestingSessionLocal()
    user = MVPUser(
        email="traveler.exist@ceylongo.lk",
        full_name="Existing Traveler",
        hashed_password="some_argon2_hash",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.close()

    res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "traveler.exist@ceylongo.lk"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "traveler.exist@ceylongo.lk"
    assert data["is_registered"] is True
    # Verify no sensitive account attributes are leaked
    assert "role" not in data
    assert "id" not in data
    assert "password" not in data


def test_check_email_unregistered_user():
    res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "nonexistent.traveler@ceylongo.lk"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "nonexistent.traveler@ceylongo.lk"
    assert data["is_registered"] is False


def test_check_email_normalization():
    db = TestingSessionLocal()
    user = MVPUser(
        email="normalized.traveler@ceylongo.lk",
        full_name="Normalized Traveler",
        hashed_password="some_argon2_hash",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.close()

    # Pass uppercase and leading/trailing whitespace
    res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "  NORMALIZED.TRAVELER@CEYLONGO.LK  "},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "normalized.traveler@ceylongo.lk"
    assert data["is_registered"] is True


def test_check_email_invalid_format():
    res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "not-an-email"},
    )
    assert res.status_code in (422, 400)


def test_check_email_rate_limiting():
    # Rapidly call check-email up to limit
    for _ in range(10):
        r = client.post("/api/v1/auth/check-email", json={"email": "spam@example.com"})
        assert r.status_code == 200

    # 11th call must trigger HTTP 429
    r_blocked = client.post("/api/v1/auth/check-email", json={"email": "spam@example.com"})
    assert r_blocked.status_code == 429
    assert "Retry-After" in r_blocked.headers


# ============================================================================
# 2. Google OAuth Authentication (/api/v1/auth/google)
# ============================================================================
def test_google_auth_new_traveler_provisioning(monkeypatch):
    # Mock verify_google_id_token
    def mock_verify_google_id_token(token_str, client_id=None):
        return {
            "google_sub": "google-uid-12345",
            "email": "google.traveler@ceylongo.lk",
            "full_name": "Google New Traveler",
            "picture": "https://lh3.googleusercontent.com/photo.jpg",
        }

    monkeypatch.setattr("app.core.google_auth.verify_google_id_token", mock_verify_google_id_token)

    res = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_gis_id_token_xyz"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "google.traveler@ceylongo.lk"
    assert data["user"]["full_name"] == "Google New Traveler"
    assert data["user"]["role"] == "traveler"
    assert data["user"]["is_active"] is True

    # Verify user exists in database
    db = TestingSessionLocal()
    u = db.query(MVPUser).filter(MVPUser.email == "google.traveler@ceylongo.lk").first()
    assert u is not None
    assert u.role == "traveler"
    db.close()


def test_google_auth_existing_user_login(monkeypatch):
    db = TestingSessionLocal()
    user = MVPUser(
        email="existing.google@ceylongo.lk",
        full_name="Existing Google User",
        hashed_password="some_argon2_hash",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.close()

    def mock_verify_google_id_token(token_str, client_id=None):
        return {
            "google_sub": "google-uid-99999",
            "email": "existing.google@ceylongo.lk",
            "full_name": "Existing Google User Updated",
            "picture": None,
        }

    monkeypatch.setattr("app.core.google_auth.verify_google_id_token", mock_verify_google_id_token)

    res = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_token"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["email"] == "existing.google@ceylongo.lk"


def test_google_auth_inactive_account_rejected(monkeypatch):
    db = TestingSessionLocal()
    user = MVPUser(
        email="inactive.google@ceylongo.lk",
        full_name="Suspended User",
        hashed_password="some_argon2_hash",
        role="traveler",
        is_active=False,
    )
    db.add(user)
    db.commit()
    db.close()

    def mock_verify_google_id_token(token_str, client_id=None):
        return {
            "google_sub": "google-uid-suspended",
            "email": "inactive.google@ceylongo.lk",
            "full_name": "Suspended User",
            "picture": None,
        }

    monkeypatch.setattr("app.core.google_auth.verify_google_id_token", mock_verify_google_id_token)

    res = client.post(
        "/api/v1/auth/google",
        json={"id_token": "mock_token"},
    )
    assert res.status_code == 403
    assert "suspended or inactive" in res.json()["detail"].lower()


# ============================================================================
# 3. Password Recovery & Single-Use Verification Flow
# ============================================================================
def test_forgot_password_generic_anti_enumeration():
    # Test with unregistered email
    res_unregistered = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "nobody@ceylongo.lk"},
    )
    assert res_unregistered.status_code == 200
    assert "instructions have been sent" in res_unregistered.json()["message"]
    # Verify no token is leaked
    assert "token" not in res_unregistered.text
    assert "reset_token_demo" not in res_unregistered.json()

    # Test with registered email
    db = TestingSessionLocal()
    user = MVPUser(
        email="traveler.reset@ceylongo.lk",
        full_name="Reset Traveler",
        hashed_password="initial_hash",
        role="traveler",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.close()

    res_registered = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "traveler.reset@ceylongo.lk"},
    )
    assert res_registered.status_code == 200
    assert res_registered.json()["message"] == res_unregistered.json()["message"]


def test_verify_and_confirm_password_reset():
    db = TestingSessionLocal()
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "email": "pwchange@ceylongo.lk",
            "full_name": "Change User",
            "password": "InitialPassword123!",
            "role": "traveler",
        },
    )
    assert reg.status_code == 201

    # Request reset token via service for test isolation
    raw_token = MVPAuthService.request_password_reset(
        db, PasswordResetRequest(email="pwchange@ceylongo.lk"), send_email=False
    )
    db.close()

    # Verify token
    r_ver = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert r_ver.status_code == 200
    assert r_ver.json()["valid"] is True
    assert r_ver.json()["email"] == "pwchange@ceylongo.lk"

    # Confirm reset with new password
    r_conf = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NewStrongPassword789!"},
    )
    assert r_conf.status_code == 200
    assert "successfully updated" in r_conf.json()["message"]

    # Replay attack: reusing token fails
    r_replay = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "AnotherPassword123!"},
    )
    assert r_replay.status_code == 400

    # Old password fails login
    r_old_login = client.post(
        "/api/v1/auth/login",
        json={"email": "pwchange@ceylongo.lk", "password": "InitialPassword123!"},
    )
    assert r_old_login.status_code == 401

    # New password succeeds login
    r_new_login = client.post(
        "/api/v1/auth/login",
        json={"email": "pwchange@ceylongo.lk", "password": "NewStrongPassword789!"},
    )
    assert r_new_login.status_code == 200
    assert "access_token" in r_new_login.json()
