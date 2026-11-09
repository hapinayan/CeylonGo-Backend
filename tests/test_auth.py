from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.database.connection import get_db
from app.main import app
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
