from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from app.core.rate_limiter import password_reset_limiter
from app.core.security import hash_password, hash_token, verify_password
from app.database.connection import get_db
from app.main import app
from app.models.user import Base, PasswordResetToken, Role, User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Setup in-memory SQLite for testing
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    execution_options={"schema_translate_map": {"dbo": None}},
)


@event.listens_for(test_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
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
    session = TestingSessionLocal()
    try:
        session.add_all(
            [
                Role(role_id=1, role_name="Tourist"),
                Role(role_id=2, role_name="Vendor"),
                Role(role_id=3, role_name="Admin"),
            ]
        )
        session.commit()
    finally:
        session.close()

    # Reset in-memory rate limiter before each test
    password_reset_limiter.clear()

    yield
    Base.metadata.drop_all(bind=test_engine)


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


@pytest.fixture
def sample_user(db_session) -> User:
    user = User(
        full_name="Traveler One",
        email="traveler.one@example.com",
        password_hash=hash_password("OldPassword123!"),
        role="traveler",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ============================================================================
# Forgot Password Tests
# ============================================================================


def test_forgot_password_registered_active_user(client: TestClient, db_session, sample_user):
    with patch("app.services.email_service.email_service.send_password_reset_email") as mock_email:
        mock_email.return_value = True

        response = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "TRAVELER.ONE@example.com"},
        )

        assert response.status_code == 200
        data = response.json()
        assert (
            data["message"]
            == "If an account with that email exists, password reset instructions have been sent."
        )

        # Verify token created in DB
        tokens = (
            db_session.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == sample_user.id)
            .all()
        )
        assert len(tokens) == 1
        assert tokens[0].is_used is False
        assert tokens[0].expires_at > datetime.now(UTC).replace(tzinfo=None)

        # Verify email dispatched
        mock_email.assert_called_once()
        args, _ = mock_email.call_args
        assert args[0] == "traveler.one@example.com"
        # Token passed to email should hash to the stored hash
        assert hash_token(args[1]) == tokens[0].token_hash


def test_forgot_password_unknown_email_anti_enumeration(client: TestClient, db_session):
    with patch("app.services.email_service.email_service.send_password_reset_email") as mock_email:
        response = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "nonexistent@example.com"},
        )

        assert response.status_code == 200
        assert (
            response.json()["message"]
            == "If an account with that email exists, password reset instructions have been sent."
        )

        # Verify no token or email dispatched
        assert db_session.query(PasswordResetToken).count() == 0
        mock_email.assert_not_called()


def test_forgot_password_inactive_user_anti_enumeration(client: TestClient, db_session):
    inactive = User(
        full_name="Inactive Traveler",
        email="inactive@example.com",
        password_hash=hash_password("Password123!"),
        role="traveler",
        is_active=False,
    )
    db_session.add(inactive)
    db_session.commit()

    with patch("app.services.email_service.email_service.send_password_reset_email") as mock_email:
        response = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "inactive@example.com"},
        )

        assert response.status_code == 200
        assert (
            response.json()["message"]
            == "If an account with that email exists, password reset instructions have been sent."
        )

        assert db_session.query(PasswordResetToken).count() == 0
        mock_email.assert_not_called()


def test_forgot_password_invalidates_older_tokens(client: TestClient, db_session, sample_user):
    with patch("app.services.email_service.email_service.send_password_reset_email"):
        # First request
        r1 = client.post("/api/v1/auth/forgot-password", json={"email": sample_user.email})
        assert r1.status_code == 200

        # Second request
        r2 = client.post("/api/v1/auth/forgot-password", json={"email": sample_user.email})
        assert r2.status_code == 200

        tokens = (
            db_session.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == sample_user.id)
            .order_by(PasswordResetToken.id.asc())
            .all()
        )
        assert len(tokens) == 2
        # First token must be invalidated
        assert tokens[0].is_used is True
        # Second token must be active
        assert tokens[1].is_used is False


def test_forgot_password_rate_limiting(client: TestClient):
    # Send 5 requests (the limit)
    for i in range(5):
        resp = client.post(
            "/api/v1/auth/forgot-password",
            json={"email": "rate.limit@example.com"},
            headers={"X-Forwarded-For": "198.51.100.1"},
        )
        assert resp.status_code == 200

    # 6th request must trigger rate limit
    blocked = client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "rate.limit@example.com"},
        headers={"X-Forwarded-For": "198.51.100.1"},
    )
    assert blocked.status_code == 429
    assert "too many" in blocked.json()["detail"].lower()


# ============================================================================
# Reset Password Tests
# ============================================================================


def test_reset_password_success_and_subsequent_login(client: TestClient, db_session, sample_user):
    captured_token = None

    def capture_email(to, raw_token, expires):
        nonlocal captured_token
        captured_token = raw_token
        return True

    with patch(
        "app.services.email_service.email_service.send_password_reset_email",
        side_effect=capture_email,
    ):
        client.post(
            "/api/v1/auth/forgot-password",
            json={"email": sample_user.email},
        )

    assert captured_token is not None

    # Reset password with valid token and new password
    reset_resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": captured_token,
            "password": "NewSecretPassword456!",
        },
    )

    assert reset_resp.status_code == 200
    assert "successfully" in reset_resp.json()["message"].lower()

    # Verify token is now marked used
    token_record = (
        db_session.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == hash_token(captured_token))
        .first()
    )
    assert token_record.is_used is True
    assert token_record.used_at is not None

    # Verify user can log in with new password
    login_resp = client.post(
        "/api/v1/auth/login",
        json={
            "email": sample_user.email,
            "password": "NewSecretPassword456!",
        },
    )
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()

    # Verify user cannot log in with old password
    failed_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": sample_user.email,
            "password": "OldPassword123!",
        },
    )
    assert failed_login.status_code == 401


def test_reset_password_reused_token_rejected(client: TestClient, db_session, sample_user):
    raw_token = "single-use-test-token-abcdef123456"
    token_record = PasswordResetToken(
        user_id=sample_user.id,
        token_hash=hash_token(raw_token),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15),
        is_used=True,  # Already used!
    )
    db_session.add(token_record)
    db_session.commit()

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "password": "NewValidPassword123!",
        },
    )
    assert resp.status_code == 400
    assert "invalid or expired" in resp.json()["detail"].lower()


def test_reset_password_expired_token_rejected(client: TestClient, db_session, sample_user):
    raw_token = "expired-test-token-abcdef123456789"
    # Token expired 10 minutes ago
    past_time = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
    token_record = PasswordResetToken(
        user_id=sample_user.id,
        token_hash=hash_token(raw_token),
        expires_at=past_time,
        is_used=False,
    )
    db_session.add(token_record)
    db_session.commit()

    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "password": "NewValidPassword123!",
        },
    )
    assert resp.status_code == 400
    assert "invalid or expired" in resp.json()["detail"].lower()

    # Expired token should be flagged is_used = True
    db_session.refresh(token_record)
    assert token_record.is_used is True


def test_reset_password_unknown_token_rejected(client: TestClient):
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": "unknown-nonexistent-token-12345678",
            "password": "NewValidPassword123!",
        },
    )
    assert resp.status_code == 400
    assert "invalid or expired" in resp.json()["detail"].lower()


@pytest.mark.parametrize(
    "weak_password",
    [
        "short",  # < 8 characters
        "1234567",
        "",
    ],
)
def test_reset_password_complexity_validation(client: TestClient, weak_password):
    resp = client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": "valid-token-string-at-least-10-chars",
            "password": weak_password,
        },
    )
    assert resp.status_code == 422
