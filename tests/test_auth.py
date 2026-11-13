import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.email import (
    build_password_reset_email_message,
    build_password_reset_url,
    get_smtp_port,
    get_smtp_timeout,
    is_smtp_configured,
    send_password_reset_email,
)
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
def mock_smtp_success(monkeypatch):
    class MockSMTP:
        last_sent_msg = None

        def __init__(self, host, port, timeout=10):
            self.host = host
            self.port = port
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            MockSMTP.last_sent_msg = msg

    MockSMTP.last_sent_msg = None
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USERNAME", "traveler@ceylongo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "mock-app-password")
    monkeypatch.setattr(smtplib, "SMTP", MockSMTP)
    return MockSMTP


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


def test_forgot_password_registered_user(
    client: TestClient, db_session, mock_smtp_success
):
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


def test_forgot_password_normalization(
    client: TestClient, db_session, mock_smtp_success
):
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
    # Ensure no unusable reset records remain in the database when delivery fails
    assert db_session.query(PasswordReset).count() == 0


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


# ==========================================
# Phase 3.1: Complete SMTP Email Tests
# ==========================================


def test_smtp_configured_helpers(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert is_smtp_configured() is False

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    assert is_smtp_configured() is True

    monkeypatch.setenv("SMTP_PORT", "not-a-number")
    assert get_smtp_port() == 587

    monkeypatch.setenv("SMTP_PORT", "465")
    assert get_smtp_port() == 465

    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "invalid")
    assert get_smtp_timeout() == 10

    monkeypatch.setenv("SMTP_TIMEOUT_SECONDS", "25")
    assert get_smtp_timeout() == 25


def test_reset_url_construction(monkeypatch):
    monkeypatch.setenv(
        "FRONTEND_RESET_PASSWORD_URL", "http://localhost:4200/reset-password"
    )
    url1 = build_password_reset_url("test-token-123")
    assert url1 == "http://localhost:4200/reset-password?token=test-token-123"

    monkeypatch.setenv(
        "FRONTEND_RESET_PASSWORD_URL", "http://localhost:4200/auth/reset?mode=traveler"
    )
    url2 = build_password_reset_url("test-token-456")
    assert url2 == "http://localhost:4200/auth/reset?mode=traveler&token=test-token-456"


def test_email_html_and_plaintext_content():
    to = "traveler.amara@example.com"
    reset_url = "http://localhost:4200/reset-password?token=sample-test-token"
    name = "Amara Jayawardena"

    msg = build_password_reset_email_message(to, reset_url, name)

    assert msg["Subject"] == "CeylonGo — Reset Your Password"
    assert msg["To"] == to
    assert "CeylonGo" in msg["From"]

    # Check plain text content
    text_content = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Hello Amara Jayawardena," in text_content
    assert "CeylonGo" in text_content
    assert reset_url in text_content
    assert "30 minutes" in text_content
    assert "If you did not request this password reset" in text_content
    assert "Discover Local. Feel at Home." in text_content

    # Check HTML content
    html_content = msg.get_body(preferencelist=("html",)).get_content()
    assert "CeylonGo" in html_content
    assert "#082B5C" in html_content  # Brand Navy
    assert "#FF9F1C" in html_content  # Brand Orange
    assert "Reset Password" in html_content
    assert reset_url in html_content
    assert "30 minutes" in html_content
    assert "If you did not request" in html_content


def test_unconfigured_smtp_does_not_send_or_create_record(
    client: TestClient, db_session, monkeypatch
):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert is_smtp_configured() is False

    user = User(
        full_name="Unconfigured SMTP Traveler",
        email="unconf.smtp@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Direct function returns False
    res_bool = send_password_reset_email("unconf.smtp@example.com", "any-token")
    assert res_bool is False

    # Endpoint returns HTTP 200 generic message without leaving unusable reset records
    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "unconf.smtp@example.com"},
    )
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )
    assert db_session.query(PasswordReset).count() == 0


def test_successful_smtp_dispatch(client: TestClient, db_session, mock_smtp_success):
    user = User(
        full_name="Dispatch Traveler",
        email="dispatch.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "dispatch.traveler@example.com"},
    )
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )

    # Verify SMTP server received the message
    sent_msg = mock_smtp_success.last_sent_msg
    assert sent_msg is not None
    assert sent_msg["To"] == "dispatch.traveler@example.com"
    assert sent_msg["Subject"] == "CeylonGo — Reset Your Password"

    # Verify reset record was committed with SHA-256 token hash
    record = (
        db_session.query(PasswordReset).filter(PasswordReset.user_id == user.id).first()
    )
    assert record is not None
    assert len(record.token_hash) == 64
    assert record.is_used is False


def test_smtp_authentication_failure(client: TestClient, db_session, monkeypatch):
    user = User(
        full_name="Auth Failure Traveler",
        email="auth.fail@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    class AuthFailSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, pwd):
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Bad credentials")

        def send_message(self, msg):
            pass

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USERNAME", "traveler@ceylongo.com")
    monkeypatch.setenv("SMTP_PASSWORD", "bad-password")
    monkeypatch.setattr(smtplib, "SMTP", AuthFailSMTP)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "auth.fail@example.com"},
    )
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )
    # Ensure transaction was rolled back and no unusable record was created
    assert db_session.query(PasswordReset).count() == 0


def test_smtp_connection_timeout(client: TestClient, db_session, monkeypatch):
    user = User(
        full_name="Timeout Traveler",
        email="timeout.traveler@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    class TimeoutSMTP:
        def __init__(self, *args, **kwargs):
            raise TimeoutError("Connection to SMTP host timed out")

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(smtplib, "SMTP", TimeoutSMTP)

    response = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "timeout.traveler@example.com"},
    )
    assert response.status_code == 200
    assert (
        response.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )
    # Ensure transaction was rolled back
    assert db_session.query(PasswordReset).count() == 0


def test_registered_and_unregistered_anti_enumeration(
    client: TestClient, db_session, mock_smtp_success
):
    user = User(
        full_name="Known User",
        email="known@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    res_reg = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "known@example.com"},
    )
    res_unreg = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "unknown-stranger@example.com"},
    )

    assert res_reg.status_code == 200
    assert res_unreg.status_code == 200
    assert res_reg.json() == res_unreg.json()
    assert (
        res_reg.json()["message"]
        == "If an account with that email exists, a password reset link has been sent."
    )


def test_no_secrets_exposed_in_logs_or_api_responses(
    client: TestClient, db_session, monkeypatch, caplog
):
    secret_smtp_password = "SuperSecretGmailPassword123!"
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USERNAME", "traveler@ceylongo.com")
    monkeypatch.setenv("SMTP_PASSWORD", secret_smtp_password)

    user = User(
        full_name="Secret Leak Check",
        email="leak.check@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, pwd):
            raise smtplib.SMTPAuthenticationError(535, b"Auth failed")

        def send_message(self, msg):
            pass

    monkeypatch.setattr(smtplib, "SMTP", FailingSMTP)

    import logging

    with caplog.at_level(logging.DEBUG):
        response = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "leak.check@example.com"},
        )

    assert response.status_code == 200
    # Verify password was never logged
    assert secret_smtp_password not in caplog.text
    # Verify response body only contains the generic public message
    assert response.json() == {
        "message": "If an account with that email exists, a password reset link has been sent."
    }


# ==============================================================================
# PHASE 3.2: GOOGLE SIGN-IN TESTS
# ==============================================================================


@pytest.fixture
def mock_google_client_id(monkeypatch):
    test_client_id = "test-mock-client-id.apps.googleusercontent.com"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", test_client_id)
    return test_client_id


def test_google_auth_new_user_registration(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 1: New traveler registration via Google Sign-In with verified payload."""
    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_sub_1001",
        "email": "Amara.Traveler@Gmail.Com",
        "email_verified": True,
        "name": "Amara Jayawardena",
        "picture": "https://lh3.googleusercontent.com/a/photo_1",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "amara.traveler@gmail.com"
    assert data["user"]["full_name"] == "Amara Jayawardena"
    assert data["user"]["role"] == "traveler"
    assert data["user"]["is_active"] is True

    # Verify database persistence
    user = db_session.query(User).filter(User.google_sub == "google_sub_1001").first()
    assert user is not None
    assert user.email == "amara.traveler@gmail.com"
    assert (
        user.password_hash is None
    )  # Google-only accounts do not have local passwords

    # Verify issued token
    decoded = decode_access_token(data["access_token"])
    assert decoded["sub"] == str(user.id)


def test_google_auth_existing_google_user_login(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 2: Existing Google traveler logs in without creating duplicate records."""
    existing_user = User(
        full_name="Returning Traveler",
        email="returning.traveler@gmail.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="google_sub_2002",
    )
    db_session.add(existing_user)
    db_session.commit()

    google_payload = {
        "iss": "accounts.google.com",
        "sub": "google_sub_2002",
        "email": "returning.traveler@gmail.com",
        "email_verified": True,
        "name": "Returning Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_2"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == existing_user.id
    assert data["user"]["email"] == "returning.traveler@gmail.com"

    # Verify no duplicate user was created
    users_count = (
        db_session.query(User)
        .filter(User.email == "returning.traveler@gmail.com")
        .count()
    )
    assert users_count == 1


def test_google_auth_safe_account_linking(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 3: Existing password-registered traveler safely links Google account."""
    hashed_pwd = hash_password("TravelerSecret123!")
    existing_user = User(
        full_name="Local Registered Traveler",
        email="local.traveler@gmail.com",
        password_hash=hashed_pwd,
        role="traveler",
        is_active=True,
        google_sub=None,
    )
    db_session.add(existing_user)
    db_session.commit()

    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_sub_3003",
        "email": "Local.Traveler@Gmail.Com",
        "email_verified": True,
        "name": "Local Registered Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_3"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == existing_user.id

    # Verify Google sub was linked and password hash preserved
    db_session.refresh(existing_user)
    assert existing_user.google_sub == "google_sub_3003"
    assert existing_user.password_hash == hashed_pwd

    # Verify user can STILL log in using their local email and password
    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "local.traveler@gmail.com",
            "password": "TravelerSecret123!",
        },
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()


def test_google_auth_invalid_signature_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 4: Token with invalid cryptographic signature returns 401."""
    import google.oauth2.id_token

    def mock_invalid_token(*args, **kwargs):
        raise ValueError("Invalid token signature")

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_invalid_token
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "tampered_token_payload"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_expired_token_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 5: Expired Google ID token returns 401."""
    import google.oauth2.id_token

    def mock_expired_token(*args, **kwargs):
        raise ValueError("Token used too late")

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_expired_token
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "expired_token_payload"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_wrong_audience_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 6: Token minted for a different Client ID returns 401."""
    import google.oauth2.id_token

    def mock_wrong_audience(*args, **kwargs):
        raise ValueError(
            "Token has wrong audience, expected test-mock-client-id.apps.googleusercontent.com"
        )

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_wrong_audience
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "wrong_audience_token"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_invalid_issuer_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 7: Token with untrusted issuer returns 401."""
    google_payload = {
        "iss": "https://untrusted-identity-provider.com",
        "sub": "untrusted_sub",
        "email": "hacker@example.com",
        "email_verified": True,
        "name": "Attacker",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "untrusted_issuer_token"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token issuer" in response.json()["detail"]


def test_google_auth_unverified_email_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 8: Google account with unverified email is rejected with 400 Bad Request."""
    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "unverified_sub_8008",
        "email": "unverified@example.com",
        "email_verified": False,
        "name": "Unverified User",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "unverified_email_token"},
    )

    assert response.status_code == 400
    assert "email is not verified" in response.json()["detail"].lower()


def test_google_auth_conflict_different_google_sub(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 9: Account linking conflict if email is already linked to another Google sub."""
    existing_user = User(
        full_name="Original Google User",
        email="conflict.traveler@example.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="original_google_sub_1",
    )
    db_session.add(existing_user)
    db_session.commit()

    # Attempt to sign in with same email but a DIFFERENT Google sub
    mismatched_payload = {
        "iss": "https://accounts.google.com",
        "sub": "different_google_sub_2",
        "email": "conflict.traveler@example.com",
        "email_verified": True,
        "name": "Impostor",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: mismatched_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "conflict_sub_token"},
    )

    assert response.status_code == 409
    assert "already associated with another Google account" in response.json()["detail"]


def test_google_only_user_password_login_returns_401_cleanly(
    client: TestClient, db_session
):
    """Test 10: Attempting password login on Google-only user rejects cleanly without crashing."""
    google_user = User(
        full_name="Google Only Traveler",
        email="google.only@example.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="google_only_sub_999",
    )
    db_session.add(google_user)
    db_session.commit()

    # Attempt password login
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "google.only@example.com", "password": "Password123!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_google_auth_inactive_account_rejected(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 11: Inactive traveler account is blocked from Google Sign-In with 403 Forbidden."""
    inactive_user = User(
        full_name="Banned Traveler",
        email="banned@example.com",
        password_hash=None,
        role="traveler",
        is_active=False,
        google_sub="google_banned_sub",
    )
    db_session.add(inactive_user)
    db_session.commit()

    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_banned_sub",
        "email": "banned@example.com",
        "email_verified": True,
        "name": "Banned Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "banned_user_token"},
    )

    assert response.status_code == 403
    assert "account is inactive" in response.json()["detail"].lower()


def test_google_auth_missing_client_id_raises_runtime_error(monkeypatch):
    """Test 12: Missing GOOGLE_CLIENT_ID configuration raises RuntimeError."""
    from app.core.google_auth import get_google_client_id

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        get_google_client_id()
    assert "GOOGLE_CLIENT_ID environment variable is not set" in str(exc_info.value)


def test_google_auth_empty_id_token_rejected(client: TestClient):
    """Test 13: Empty ID token rejected by Pydantic validation with 422."""
    response = client.post("/api/v1/auth/google", json={"id_token": ""})
    assert response.status_code == 422


# ==============================================================================
# PHASE 3.2: GOOGLE SIGN-IN TESTS
# ==============================================================================


@pytest.fixture
def mock_google_client_id(monkeypatch):
    test_client_id = "test-mock-client-id.apps.googleusercontent.com"
    monkeypatch.setenv("GOOGLE_CLIENT_ID", test_client_id)
    return test_client_id


def test_google_auth_new_user_registration(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 1: New traveler registration via Google Sign-In with verified payload."""
    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_sub_1001",
        "email": "Amara.Traveler@Gmail.Com",
        "email_verified": True,
        "name": "Amara Jayawardena",
        "picture": "https://lh3.googleusercontent.com/a/photo_1",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_1"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "amara.traveler@gmail.com"
    assert data["user"]["full_name"] == "Amara Jayawardena"
    assert data["user"]["role"] == "traveler"
    assert data["user"]["is_active"] is True

    # Verify database persistence
    user = (
        db_session.query(User).filter(User.google_sub == "google_sub_1001").first()
    )
    assert user is not None
    assert user.email == "amara.traveler@gmail.com"
    assert user.password_hash is None  # Google-only accounts do not have local passwords

    # Verify issued token
    decoded = decode_access_token(data["access_token"])
    assert decoded["sub"] == str(user.id)


def test_google_auth_existing_google_user_login(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 2: Existing Google traveler logs in without creating duplicate records."""
    existing_user = User(
        full_name="Returning Traveler",
        email="returning.traveler@gmail.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="google_sub_2002",
    )
    db_session.add(existing_user)
    db_session.commit()

    google_payload = {
        "iss": "accounts.google.com",
        "sub": "google_sub_2002",
        "email": "returning.traveler@gmail.com",
        "email_verified": True,
        "name": "Returning Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_2"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == existing_user.id
    assert data["user"]["email"] == "returning.traveler@gmail.com"

    # Verify no duplicate user was created
    users_count = (
        db_session.query(User)
        .filter(User.email == "returning.traveler@gmail.com")
        .count()
    )
    assert users_count == 1


def test_google_auth_safe_account_linking(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 3: Existing password-registered traveler safely links Google account."""
    hashed_pwd = hash_password("TravelerSecret123!")
    existing_user = User(
        full_name="Local Registered Traveler",
        email="local.traveler@gmail.com",
        password_hash=hashed_pwd,
        role="traveler",
        is_active=True,
        google_sub=None,
    )
    db_session.add(existing_user)
    db_session.commit()

    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_sub_3003",
        "email": "Local.Traveler@Gmail.Com",
        "email_verified": True,
        "name": "Local Registered Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "valid_mock_google_id_token_3"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["user"]["id"] == existing_user.id

    # Verify Google sub was linked and password hash preserved
    db_session.refresh(existing_user)
    assert existing_user.google_sub == "google_sub_3003"
    assert existing_user.password_hash == hashed_pwd

    # Verify user can STILL log in using their local email and password
    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "local.traveler@gmail.com",
            "password": "TravelerSecret123!",
        },
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()


def test_google_auth_invalid_signature_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 4: Token with invalid cryptographic signature returns 401."""
    import google.oauth2.id_token

    def mock_invalid_token(*args, **kwargs):
        raise ValueError("Invalid token signature")

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_invalid_token
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "tampered_token_payload"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_expired_token_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 5: Expired Google ID token returns 401."""
    import google.oauth2.id_token

    def mock_expired_token(*args, **kwargs):
        raise ValueError("Token used too late")

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_expired_token
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "expired_token_payload"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_wrong_audience_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 6: Token minted for a different Client ID returns 401."""
    import google.oauth2.id_token

    def mock_wrong_audience(*args, **kwargs):
        raise ValueError(
            "Token has wrong audience, expected test-mock-client-id.apps.googleusercontent.com"
        )

    monkeypatch.setattr(
        google.oauth2.id_token, "verify_oauth2_token", mock_wrong_audience
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "wrong_audience_token"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token" in response.json()["detail"]


def test_google_auth_invalid_issuer_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 7: Token with untrusted issuer returns 401."""
    google_payload = {
        "iss": "https://untrusted-identity-provider.com",
        "sub": "untrusted_sub",
        "email": "hacker@example.com",
        "email_verified": True,
        "name": "Attacker",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "untrusted_issuer_token"},
    )

    assert response.status_code == 401
    assert "Invalid Google ID token issuer" in response.json()["detail"]


def test_google_auth_unverified_email_rejected(
    client: TestClient, mock_google_client_id, monkeypatch
):
    """Test 8: Google account with unverified email is rejected with 400 Bad Request."""
    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "unverified_sub_8008",
        "email": "unverified@example.com",
        "email_verified": False,
        "name": "Unverified User",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "unverified_email_token"},
    )

    assert response.status_code == 400
    assert "email is not verified" in response.json()["detail"].lower()


def test_google_auth_conflict_different_google_sub(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 9: Account linking conflict if email is already linked to another Google sub."""
    existing_user = User(
        full_name="Original Google User",
        email="conflict.traveler@example.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="original_google_sub_1",
    )
    db_session.add(existing_user)
    db_session.commit()

    # Attempt to sign in with same email but a DIFFERENT Google sub
    mismatched_payload = {
        "iss": "https://accounts.google.com",
        "sub": "different_google_sub_2",
        "email": "conflict.traveler@example.com",
        "email_verified": True,
        "name": "Impostor",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: mismatched_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "conflict_sub_token"},
    )

    assert response.status_code == 409
    assert (
        "already associated with another Google account"
        in response.json()["detail"]
    )


def test_google_only_user_password_login_returns_401_cleanly(
    client: TestClient, db_session
):
    """Test 10: Attempting password login on Google-only user rejects cleanly without crashing."""
    google_user = User(
        full_name="Google Only Traveler",
        email="google.only@example.com",
        password_hash=None,
        role="traveler",
        is_active=True,
        google_sub="google_only_sub_999",
    )
    db_session.add(google_user)
    db_session.commit()

    # Attempt password login
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "google.only@example.com", "password": "Password123!"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_google_auth_inactive_account_rejected(
    client: TestClient, db_session, mock_google_client_id, monkeypatch
):
    """Test 11: Inactive traveler account is blocked from Google Sign-In with 403 Forbidden."""
    inactive_user = User(
        full_name="Banned Traveler",
        email="banned@example.com",
        password_hash=None,
        role="traveler",
        is_active=False,
        google_sub="google_banned_sub",
    )
    db_session.add(inactive_user)
    db_session.commit()

    google_payload = {
        "iss": "https://accounts.google.com",
        "sub": "google_banned_sub",
        "email": "banned@example.com",
        "email_verified": True,
        "name": "Banned Traveler",
        "aud": mock_google_client_id,
    }

    import google.oauth2.id_token

    monkeypatch.setattr(
        google.oauth2.id_token,
        "verify_oauth2_token",
        lambda token, request, audience: google_payload,
    )

    response = client.post(
        "/api/v1/auth/google",
        json={"id_token": "banned_user_token"},
    )

    assert response.status_code == 403
    assert "account is inactive" in response.json()["detail"].lower()


def test_google_auth_missing_client_id_raises_runtime_error(monkeypatch):
    """Test 12: Missing GOOGLE_CLIENT_ID configuration raises RuntimeError."""
    from app.core.google_auth import get_google_client_id

    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        get_google_client_id()
    assert "GOOGLE_CLIENT_ID environment variable is not set" in str(
        exc_info.value
    )


def test_google_auth_empty_id_token_rejected(client: TestClient):
    """Test 13: Empty ID token rejected by Pydantic validation with 422."""
    response = client.post("/api/v1/auth/google", json={"id_token": ""})
    assert response.status_code == 422

