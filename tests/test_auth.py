from datetime import UTC, datetime, timedelta

import pytest
from app.core.rate_limiter import reset_all_limiters
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.database.connection import get_db
from app.main import app
from app.models.password_reset import PasswordReset
from app.models.user import Base, User
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Create isolated in-memory SQLite database
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    # Provide MSSQL's sysutcdatetime() function inside SQLite
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


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    reset_all_limiters()
    yield
    reset_all_limiters()


@pytest.fixture
def db_session():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


# ==========================================
# Registration Tests
# ==========================================


def test_register_success(client: TestClient):
    payload = {
        "full_name": "  Test Traveler  ",
        "email": "Traveler@Example.com",
        "password": "TestPassword123!",
    }
    response = client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["message"] == "Account registered successfully"
    user = data["user"]
    assert user["full_name"] == "Test Traveler"
    assert user["email"] == "traveler@example.com"
    assert user["role"] == "traveler"
    assert user["is_active"] is True
    assert "id" in user
    assert "created_at" in user


def test_register_duplicate_email(client: TestClient):
    payload = {
        "full_name": "Test Traveler",
        "email": "duplicate@example.com",
        "password": "TestPassword123!",
    }
    first_response = client.post("/api/v1/auth/register", json=payload)
    assert first_response.status_code == 201

    second_response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Another User",
            "email": "DUPLICATE@example.com",
            "password": "DifferentPassword123!",
        },
    )
    assert second_response.status_code == 409
    assert "already exists" in second_response.json()["detail"].lower()


@pytest.mark.parametrize(
    "payload,expected_error_field",
    [
        (
            {
                "full_name": "A",
                "email": "valid@example.com",
                "password": "TestPassword123!",
            },
            "full_name",
        ),
        (
            {
                "full_name": "   ",
                "email": "valid@example.com",
                "password": "TestPassword123!",
            },
            "full_name",
        ),
        (
            {
                "full_name": "Valid Name",
                "email": "invalid-email",
                "password": "TestPassword123!",
            },
            "email",
        ),
        (
            {
                "full_name": "Valid Name",
                "email": "valid@example.com",
                "password": "short",
            },
            "password",
        ),
    ],
)
def test_register_invalid_data(client: TestClient, payload, expected_error_field):
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 422


# ==========================================
# Login Tests
# ==========================================


def test_login_success(client: TestClient, db_session):
    user = User(
        full_name="Active Traveler",
        email="login.test@example.com",
        password_hash=hash_password("CorrectPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "LOGIN.TEST@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "login.test@example.com"
    assert data["user"]["role"] == "traveler"


def test_login_incorrect_password(client: TestClient, db_session):
    user = User(
        full_name="Traveler User",
        email="user@example.com",
        password_hash=hash_password("CorrectPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "WrongPassword123!"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_unknown_email(client: TestClient):
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "AnyPassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}


def test_login_inactive_account(client: TestClient, db_session):
    user = User(
        full_name="Inactive User",
        email="inactive@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "Password123!"},
    )

    assert response.status_code == 403
    assert "inactive" in response.json()["detail"].lower()


# ==========================================
# Security & Token Utility Tests
# ==========================================


def test_password_stored_as_argon2_hash(client: TestClient, db_session):
    payload = {
        "full_name": "Argon Test User",
        "email": "argon@example.com",
        "password": "PlainSecretPassword123!",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    saved_user = (
        db_session.query(User).filter(User.email == "argon@example.com").first()
    )
    assert saved_user is not None
    assert saved_user.password_hash != "PlainSecretPassword123!"
    assert saved_user.password_hash.startswith("$argon2")
    assert verify_password("PlainSecretPassword123!", saved_user.password_hash) is True


def test_jwt_payload_contents(client: TestClient, db_session):
    user = User(
        full_name="JWT Traveler",
        email="jwt@example.com",
        password_hash=hash_password("JwtPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "jwt@example.com", "password": "JwtPassword123!"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    payload = decode_access_token(token)
    assert payload["sub"] == str(user.id)
    assert "exp" in payload
    exp_datetime = datetime.fromtimestamp(payload["exp"], tz=UTC)
    assert exp_datetime > datetime.now(UTC)


def test_responses_never_expose_passwords(client: TestClient, db_session):
    # Registration response check
    reg_response = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Security Tester",
            "email": "security@example.com",
            "password": "SecurePassword123!",
        },
    )
    assert reg_response.status_code == 201
    reg_data = reg_response.json()
    assert "password" not in reg_data["user"]
    assert "password_hash" not in reg_data["user"]
    assert "password" not in reg_response.text
    assert "SecurePassword123!" not in reg_response.text

    # Login response check
    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "security@example.com",
            "password": "SecurePassword123!",
        },
    )
    assert login_response.status_code == 200
    login_data = login_response.json()
    assert "password" not in login_data["user"]
    assert "password_hash" not in login_data["user"]
    assert "password_hash" not in login_response.text
    assert "SecurePassword123!" not in login_response.text


# ==========================================
# Protected /me Endpoint Tests
# ==========================================


def test_me_with_valid_jwt_returns_user(client: TestClient, db_session):
    user = User(
        full_name="Profile User",
        email="profile@example.com",
        password_hash=hash_password("ValidPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    token = create_access_token(data={"sub": str(user.id)})
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == user.id
    assert data["full_name"] == "Profile User"
    assert data["email"] == "profile@example.com"
    assert data["role"] == "traveler"
    assert data["is_active"] is True
    assert "created_at" in data


def test_me_without_token_returns_401(client: TestClient):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_with_invalid_token_returns_401(client: TestClient):
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert response.status_code == 401


def test_me_with_expired_token_returns_401(client: TestClient, db_session):
    user = User(
        full_name="Expired User",
        email="expired@example.com",
        password_hash=hash_password("ValidPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    expired_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=timedelta(minutes=-15),
    )
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


def test_me_with_nonexistent_user_returns_401(client: TestClient):
    nonexistent_token = create_access_token(data={"sub": "9999999"})
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {nonexistent_token}"},
    )
    assert response.status_code == 401
    assert "user not found" in response.json()["detail"].lower()


def test_me_with_inactive_user_returns_403(client: TestClient, db_session):
    user = User(
        full_name="Inactive Traveler",
        email="inactive.profile@example.com",
        password_hash=hash_password("ValidPassword123!"),
        role="traveler",
        is_active=False,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    token = create_access_token(data={"sub": str(user.id)})
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "inactive" in response.json()["detail"].lower()


def test_me_never_exposes_password_hash(client: TestClient, db_session):
    user = User(
        full_name="Secret Traveler",
        email="secret@example.com",
        password_hash=hash_password("SuperSecret123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    token = create_access_token(data={"sub": str(user.id)})
    response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "password" not in data
    assert "password_hash" not in data
    assert "password_hash" not in response.text
    assert "SuperSecret123!" not in response.text


# ==========================================
# CORS Tests
# ==========================================


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ],
)
def test_cors_preflight_allowed_origins(client: TestClient, origin):
    response = client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Content-Type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin
    allow_headers = response.headers.get("access-control-allow-headers", "")
    assert "Authorization" in allow_headers
    assert "Content-Type" in allow_headers


def test_cors_disallowed_origin_no_allow_header(client: TestClient):
    response = client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://untrusted-domain.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.headers.get("access-control-allow-origin") is None


# ==========================================
# Check Email (Email-First Flow) Tests
# ==========================================


def test_check_email_registered_user(client: TestClient, db_session):
    user = User(
        full_name="Registered Traveler",
        email="traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/check-email",
        json={"email": "traveler@example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "traveler@example.com"
    assert data["is_registered"] is True


def test_check_email_unregistered_user(client: TestClient):
    response = client.post(
        "/api/v1/auth/check-email",
        json={"email": "unregistered@example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "unregistered@example.com"
    assert data["is_registered"] is False


def test_check_email_normalization(client: TestClient, db_session):
    user = User(
        full_name="Case Traveler",
        email="case.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Uppercase and extra whitespace
    response = client.post(
        "/api/v1/auth/check-email",
        json={"email": "   CASE.TRAVELER@EXAMPLE.COM   "},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "case.traveler@example.com"
    assert data["is_registered"] is True

    # Unregistered email with mixed casing and whitespace
    response_unreg = client.post(
        "/api/v1/auth/check-email",
        json={"email": "   SOME.NEW.USER@EXAMPLE.COM   "},
    )
    assert response_unreg.status_code == 200
    data_unreg = response_unreg.json()
    assert data_unreg["email"] == "some.new.user@example.com"
    assert data_unreg["is_registered"] is False


@pytest.mark.parametrize(
    "invalid_email",
    [
        "not-an-email",
        "missingatsign.com",
        "user@",
        "@domain.com",
        "user@ domain.com",
        "",
    ],
)
def test_check_email_invalid_format(client: TestClient, invalid_email):
    response = client.post(
        "/api/v1/auth/check-email",
        json={"email": invalid_email},
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_check_email_rate_limiting(client: TestClient):
    # Exceed rate limit (default 10 requests allowed)
    for i in range(10):
        res = client.post(
            "/api/v1/auth/check-email",
            json={"email": f"check{i}@example.com"},
        )
        assert res.status_code == 200

    # 11th request from the same IP should be rate limited
    blocked_res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "blocked@example.com"},
    )
    assert blocked_res.status_code == 429
    assert "Retry-After" in blocked_res.headers
    assert int(blocked_res.headers["Retry-After"]) > 0
    blocked_data = blocked_res.json()
    assert "Too many email check requests" in blocked_data["detail"]

    # Different client IP should not be blocked
    other_ip_res = client.post(
        "/api/v1/auth/check-email",
        json={"email": "other@example.com"},
        headers={"X-Forwarded-For": "198.51.100.42"},
    )
    assert other_ip_res.status_code == 200


def test_check_email_database_error_generic(client: TestClient, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    def mock_query(self, *args, **kwargs):
        raise OperationalError(
            "SELECT 1", {}, Exception("Connection lost to CeylonGoDB")
        )

    monkeypatch.setattr(Session, "query", mock_query)

    response = client.post(
        "/api/v1/auth/check-email",
        json={"email": "error@example.com"},
    )

    assert response.status_code == 500
    data = response.json()
    assert "An error occurred while verifying the email address" in data["detail"]
    # Ensure internal DB exception details are never leaked
    assert "CeylonGoDB" not in response.text
    assert "OperationalError" not in response.text


# ==========================================
# Phase 2: Password Reset Flow Tests
# ==========================================


def test_forgot_password_registered_user(client: TestClient, db_session):
    user = User(
        full_name="Password Reset Traveler",
        email="reset.traveler@example.com",
        password_hash=hash_password("InitialPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "reset.traveler@example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert (
        data["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )

    # Verify reset record was created in database with SHA-256 hash
    reset_record = (
        db_session.query(PasswordReset).filter(PasswordReset.user_id == user.id).first()
    )
    assert reset_record is not None
    assert len(reset_record.token_hash) == 64
    assert reset_record.is_used is False
    assert reset_record.used_at is None
    # Expire time should be in the future (~30 min)
    now_utc = datetime.now(UTC).replace(tzinfo=None)
    assert reset_record.expires_at > now_utc


def test_forgot_password_unregistered_user_anti_enumeration(
    client: TestClient, db_session
):
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "nobody@example.com"},
    )

    # Identical HTTP 200 and generic message
    assert response.status_code == 200
    data = response.json()
    assert (
        data["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )

    # Verify no reset records created
    count = db_session.query(PasswordReset).count()
    assert count == 0


def test_forgot_password_normalization(client: TestClient, db_session):
    user = User(
        full_name="Cased Reset Traveler",
        email="cased.traveler@example.com",
        password_hash=hash_password("InitialPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "   CASED.TRAVELER@EXAMPLE.COM   "},
    )

    assert response.status_code == 200
    reset_record = (
        db_session.query(PasswordReset).filter(PasswordReset.user_id == user.id).first()
    )
    assert reset_record is not None


def test_forgot_password_invalid_email(client: TestClient):
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "invalid-email-string"},
    )
    assert response.status_code == 422


def test_forgot_password_rate_limiting(client: TestClient):
    # Default limit is 5 requests per window
    for i in range(5):
        res = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": f"rate{i}@example.com"},
        )
        assert res.status_code == 200

    # 6th request should be blocked
    blocked_res = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "rate_blocked@example.com"},
    )
    assert blocked_res.status_code == 429
    assert "Retry-After" in blocked_res.headers
    assert int(blocked_res.headers["Retry-After"]) > 0
    assert "Too many password reset requests" in blocked_res.json()["detail"]


def test_forgot_password_email_failure_handled_safely(
    client: TestClient, db_session, monkeypatch
):
    user = User(
        full_name="SMTP Fail User",
        email="smtp.fail@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    import smtplib

    def mock_smtp(*args, **kwargs):
        raise smtplib.SMTPException("Connection to mail server timed out")

    monkeypatch.setenv("SMTP_HOST", "mail.example.com")
    monkeypatch.setattr(smtplib, "SMTP", mock_smtp)

    # Calling forgot-password should still safely succeed with HTTP 200
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "smtp.fail@example.com"},
    )
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )


def test_verify_reset_token_valid(client: TestClient, db_session):
    user = User(
        full_name="Verify Traveler",
        email="verify@example.com",
        password_hash=hash_password("OldPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "valid-test-token-string-12345678"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        is_used=False,
    )
    db_session.add(reset)
    db_session.commit()

    response = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert response.status_code == 200
    data = response.json()
    assert data["valid"] is True
    assert data["email"] == "verify@example.com"
    assert data["message"] == "Password reset token is valid."


def test_verify_reset_token_invalid(client: TestClient):
    response = client.get("/api/v1/auth/verify-reset-token?token=unknown-bogus-token")
    assert response.status_code == 400
    assert "Invalid or expired" in response.json()["detail"]


def test_verify_reset_token_expired(client: TestClient, db_session):
    user = User(
        full_name="Expired Traveler",
        email="expired@example.com",
        password_hash=hash_password("OldPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "expired-token-string"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
        is_used=False,
    )
    db_session.add(reset)
    db_session.commit()

    response = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_verify_reset_token_already_used(client: TestClient, db_session):
    user = User(
        full_name="Used Traveler",
        email="used@example.com",
        password_hash=hash_password("OldPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "used-token-string"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        is_used=True,
        used_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(reset)
    db_session.commit()

    response = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert response.status_code == 400
    assert "already been used" in response.json()["detail"]


def test_reset_password_success_and_login(client: TestClient, db_session):
    user = User(
        full_name="Reset Login Traveler",
        email="reset.login@example.com",
        password_hash=hash_password("InitialOldPass123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "valid-reset-token-for-login"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        is_used=False,
    )
    db_session.add(reset)
    db_session.commit()

    # Reset password
    payload = {
        "token": raw_token,
        "new_password": "NewBrandNewPassword456!",
    }
    response = client.post("/api/v1/auth/reset-password", json=payload)
    assert response.status_code == 200
    assert "Password has been reset successfully" in response.json()["message"]

    # Verify token is marked as used
    db_session.refresh(reset)
    assert reset.is_used is True
    assert reset.used_at is not None

    # Login with OLD password must FAIL
    old_login = client.post(
        "/api/v1/auth/login",
        json={"email": "reset.login@example.com", "password": "InitialOldPass123!"},
    )
    assert old_login.status_code == 401

    # Login with NEW password must SUCCEED
    new_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "reset.login@example.com",
            "password": "NewBrandNewPassword456!",
        },
    )
    assert new_login.status_code == 200
    token_data = new_login.json()
    assert "access_token" in token_data
    assert token_data["user"]["email"] == "reset.login@example.com"


def test_reset_password_replay_fails(client: TestClient, db_session):
    user = User(
        full_name="Replay Traveler",
        email="replay@example.com",
        password_hash=hash_password("InitialPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "single-use-replay-token"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        is_used=False,
    )
    db_session.add(reset)
    db_session.commit()

    # First reset succeeds
    res1 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NewPassword123!"},
    )
    assert res1.status_code == 200

    # Second reset with SAME token fails (replay attack prevention)
    res2 = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "AnotherPassword456!"},
    )
    assert res2.status_code == 400
    assert "already been used" in res2.json()["detail"]


def test_reset_password_expired_fails(client: TestClient, db_session):
    user = User(
        full_name="Expired Reset Traveler",
        email="expired.reset@example.com",
        password_hash=hash_password("InitialPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    raw_token = "expired-reset-token"
    token_hash = hash_reset_token(raw_token)
    reset = PasswordReset(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10),
        is_used=False,
    )
    db_session.add(reset)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NewPassword123!"},
    )
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_reset_password_invalid_token_fails(client: TestClient):
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "totally-fake-token", "new_password": "NewPassword123!"},
    )
    assert response.status_code == 400
    assert "Invalid or expired" in response.json()["detail"]


def test_reset_password_short_password_validation(client: TestClient):
    response = client.post(
        "/api/v1/auth/reset-password",
        json={"token": "any-token", "new_password": "short"},
    )
    assert response.status_code == 422
