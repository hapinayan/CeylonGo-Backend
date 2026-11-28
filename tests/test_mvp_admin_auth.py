"""
CeylonGo Academic MVP — Dedicated Admin Authentication & RBAC Test Suite
Validates the active Academic MVP FastAPI runtime admin authentication endpoints,
RBAC enforcement, token handling, security restrictions, and error conditions.
"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.mvp_security import create_mvp_access_token, decode_mvp_token
from app.core.rate_limiter import reset_all_limiters
from app.core.security import hash_password
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPUser

# 1. Setup isolated in-memory SQLite database
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


@pytest.fixture(autouse=True)
def setup_database():
    MVPBase.metadata.create_all(bind=test_engine)
    yield
    MVPBase.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def reset_limiters():
    reset_all_limiters()
    yield
    reset_all_limiters()


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _create_user(
    db: Session,
    email: str,
    role: str = "admin",
    password: str = "AdminPassword123!",
    full_name: str = "Test Admin",
    is_active: bool = True,
) -> MVPUser:
    user = MVPUser(
        email=email.strip().lower(),
        full_name=full_name,
        role=role,
        hashed_password=hash_password(password),
        is_active=is_active,
        auth_version=0,
        credential_version=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_admin_login_success(db: Session):
    user = _create_user(db, "admin@ceylongo.com", role="admin")

    res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "  ADMIN@CEYLONGO.COM  ", "password": "AdminPassword123!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["token_type"] == "bearer"
    assert "access_token" in data and len(data["access_token"]) > 20

    user_info = data["user"]
    assert user_info["id"] == user.id
    assert user_info["email"] == "admin@ceylongo.com"
    assert user_info["role"] == "admin"
    assert user_info["is_active"] is True
    assert "hashed_password" not in user_info
    assert "password_hash" not in user_info


def test_admin_me_success(db: Session):
    user = _create_user(db, "admin.me@ceylongo.com", role="admin")

    login_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "admin.me@ceylongo.com", "password": "AdminPassword123!"},
    )
    token = login_res.json()["access_token"]

    me_res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 200
    data = me_res.json()
    assert data["id"] == user.id
    assert data["email"] == "admin.me@ceylongo.com"
    assert data["role"] == "admin"
    assert data["is_active"] is True


def test_admin_login_invalid_password(db: Session):
    _create_user(db, "admin.wrong@ceylongo.com", role="admin")

    res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "admin.wrong@ceylongo.com", "password": "IncorrectPassword!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_admin_login_unknown_email():
    res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "nonexistent@ceylongo.com", "password": "AnyPassword123!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_admin_login_inactive_account(db: Session):
    _create_user(db, "inactive.admin@ceylongo.com", role="admin", is_active=False)

    res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "inactive.admin@ceylongo.com", "password": "AdminPassword123!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_non_admin_cannot_login_as_admin(db: Session):
    # Traveler
    _create_user(db, "traveler@ceylongo.com", role="traveler")
    t_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "traveler@ceylongo.com", "password": "AdminPassword123!"},
    )
    assert t_res.status_code == 401

    # Provider
    _create_user(db, "provider@ceylongo.com", role="provider")
    p_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "provider@ceylongo.com", "password": "AdminPassword123!"},
    )
    assert p_res.status_code == 401


def test_admin_me_rejects_missing_token():
    res = client.get("/api/v1/admin/auth/me")
    assert res.status_code == 401


def test_admin_me_rejects_expired_token():
    token = create_mvp_access_token(
        user_id=1,
        email="expired@ceylongo.com",
        role="admin",
        expires_delta=timedelta(seconds=-10),
    )
    res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 401
    assert "expired" in res.json()["detail"].lower()


def test_admin_me_rejects_malformed_token():
    res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": "Bearer not-a-valid-jwt-token"},
    )
    assert res.status_code == 401


def test_admin_me_rejects_traveler_token(db: Session):
    traveler = _create_user(db, "traveler.token@ceylongo.com", role="traveler")
    token = create_mvp_access_token(
        user_id=traveler.id,
        email=traveler.email,
        role=traveler.role,
    )
    res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
    assert "admin" in res.json()["detail"].lower()


def test_admin_auth_never_exposes_secrets(db: Session):
    user = _create_user(db, "secret.check@ceylongo.com", role="admin")

    res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "secret.check@ceylongo.com", "password": "AdminPassword123!"},
    )
    raw_text = res.text
    assert "password_hash" not in raw_text
    assert "hashed_password" not in raw_text
    assert user.hashed_password not in raw_text
