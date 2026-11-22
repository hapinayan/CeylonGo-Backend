"""
Focused offline tests for P1 Stage 1 — Admin Authentication.

Covers:
1. Admin login success for legacy `admin` and all five approved admin role identifiers
   (`super_admin`, `operations_admin`, `finance_admin`, `content_moderator`, `support_admin`).
2. Email normalization and JWT contract compatibility (`sub`, `exp`, `/api/v1/auth/me`, `require_admin`).
3. Invalid credentials (wrong password, unknown email, null password hash) -> 401 Unauthorized.
4. Inactive admin accounts rejected -> 403 Forbidden.
5. Non-admin users (`traveler`, `provider`, unknown roles) rejected from `/api/v1/admin/auth/login`
   and `/api/v1/admin/auth/me` -> 403 Forbidden (without leaking role existence on bad passwords).
6. Safe response fields only (no `password_hash`, `password`, `google_sub`, or secrets).
7. Reuse of existing secure password reset flow for admin accounts.
8. Rate limiting on `/api/v1/admin/auth/login`.
9. Regression check that traveler and provider login behavior remains unchanged.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import (
    ADMIN_ROLES,
    APPROVED_ADMIN_ROLES,
    is_admin_role,
    resolve_canonical_admin_role,
)
from app.core.rate_limiter import reset_all_limiters
from app.core.security import (
    create_access_token as sign_raw_access_token,
    decode_access_token,
    hash_password,
    hash_reset_token,
)
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.password_reset import PasswordReset
from app.models.provider_profile import ProviderProfile
from app.models.user import Base, User

# Isolated in-memory SQLite database for offline testing
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
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
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clear_rate_limiters():
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
def client(db_session: Session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _create_user(
    db: Session,
    *,
    email: str,
    role: str,
    password: str | None = "AdminStrongPass123!",
    full_name: str = "CeylonGo Admin",
    is_active: bool = True,
) -> User:
    user = User(
        full_name=full_name,
        email=email.strip().lower(),
        password_hash=hash_password(password) if password is not None else None,
        role=role,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ==============================================================================
# 1. Admin Role Identifier & Helper Unit Tests
# ==============================================================================


def test_approved_admin_role_identifiers_inventory():
    expected = {
        "super_admin",
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
    }
    assert APPROVED_ADMIN_ROLES == expected
    assert ADMIN_ROLES == expected | {"admin"}

    for role in ADMIN_ROLES:
        assert is_admin_role(role) is True
        assert is_admin_role(f"  {role.upper()}  ") is True

    for non_admin in ["traveler", "provider", "guest", "", "   ", None]:
        assert is_admin_role(non_admin) is False

    assert resolve_canonical_admin_role("admin") == "super_admin"
    for role in expected:
        assert resolve_canonical_admin_role(role) == role


# ==============================================================================
# 2. Admin Login Success Across All 5 Approved Roles + Legacy 'admin'
# ==============================================================================


@pytest.mark.parametrize(
    ("stored_role", "expected_canonical_role"),
    [
        ("admin", "super_admin"),
        ("super_admin", "super_admin"),
        ("operations_admin", "operations_admin"),
        ("finance_admin", "finance_admin"),
        ("content_moderator", "content_moderator"),
        ("support_admin", "support_admin"),
    ],
)
def test_admin_login_succeeds_for_all_approved_and_legacy_roles(
    client: TestClient,
    db_session: Session,
    stored_role: str,
    expected_canonical_role: str,
):
    email = f"{stored_role}@ceylongo.com"
    password = "AdminSecurePassword123!"
    user = _create_user(
        db_session,
        email=email,
        role=stored_role,
        password=password,
        full_name=f"Admin ({stored_role})",
    )

    response = client.post(
        "/api/v1/admin/auth/login",
        json={"email": f"  {email.upper()}  ", "password": password},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "bearer"
    assert "access_token" in data and len(data["access_token"]) > 20

    user_data = data["user"]
    assert user_data["id"] == user.id
    assert user_data["email"] == email
    assert user_data["full_name"] == f"Admin ({stored_role})"
    assert user_data["role"] == stored_role
    assert user_data["admin_role"] == expected_canonical_role
    assert user_data["is_active"] is True
    assert "created_at" in user_data

    # Verify JWT contract is identical to existing auth tokens
    decoded = decode_access_token(data["access_token"])
    assert decoded["sub"] == str(user.id)
    assert "exp" in decoded

    # Verify token works on /api/v1/admin/auth/me
    me_res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["id"] == user.id
    assert me_res.json()["role"] == stored_role
    assert me_res.json()["admin_role"] == expected_canonical_role

    # Verify token also works seamlessly on existing /api/v1/auth/me and role-permitted admin routes
    legacy_me_res = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert legacy_me_res.status_code == 200
    assert legacy_me_res.json()["id"] == user.id

    role_probe_endpoint = {
        "super_admin": "/api/v1/admin/providers",
        "operations_admin": "/api/v1/admin/providers",
        "finance_admin": "/api/v1/payments/admin/summary",
        "content_moderator": "/api/v1/community/admin/reports",
        "support_admin": "/api/v1/admin/rentals/bookings",
    }[expected_canonical_role]

    probe_res = client.get(
        role_probe_endpoint,
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert probe_res.status_code == 200


def test_shared_login_rejects_legacy_admin_users(
    client: TestClient,
    db_session: Session,
):
    """Legacy admin accounts use the dedicated admin sign-in path."""
    user = _create_user(
        db_session,
        email="legacy.portal.admin@ceylongo.com",
        role="admin",
        password="LegacyAdminPassword123!",
    )
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": "legacy.portal.admin@ceylongo.com",
            "password": "LegacyAdminPassword123!",
        },
    )
    assert res.status_code == 401
    assert res.json() == {"detail": "Invalid email or password"}


# ==============================================================================
# 3. Invalid Credentials & Inactive Accounts
# ==============================================================================


def test_admin_login_rejects_invalid_credentials(
    client: TestClient,
    db_session: Session,
):
    _create_user(
        db_session,
        email="real.admin@ceylongo.com",
        role="super_admin",
        password="CorrectAdminPassword123!",
    )
    _create_user(
        db_session,
        email="nopassword.admin@ceylongo.com",
        role="super_admin",
        password=None,
    )

    # 1. Wrong password
    res_wrong_pw = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "real.admin@ceylongo.com", "password": "WrongPassword123!"},
    )
    assert res_wrong_pw.status_code == 401
    assert res_wrong_pw.json() == {"detail": "Invalid email or password"}

    # 2. Non-existent email
    res_unknown = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "ghost.admin@ceylongo.com", "password": "AnyPassword123!"},
    )
    assert res_unknown.status_code == 401
    assert res_unknown.json() == {"detail": "Invalid email or password"}

    # 3. Admin record without password_hash
    res_null_pw = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "nopassword.admin@ceylongo.com", "password": "AnyPassword123!"},
    )
    assert res_null_pw.status_code == 401
    assert res_null_pw.json() == {"detail": "Invalid email or password"}


@pytest.mark.parametrize(
    "admin_role",
    [
        "admin",
        "super_admin",
        "operations_admin",
        "finance_admin",
        "content_moderator",
        "support_admin",
    ],
)
def test_admin_login_and_me_reject_inactive_admin_accounts(
    client: TestClient,
    db_session: Session,
    admin_role: str,
):
    email = f"inactive.{admin_role}@ceylongo.com"
    password = "InactiveAdminPassword123!"
    user = _create_user(
        db_session,
        email=email,
        role=admin_role,
        password=password,
        is_active=False,
    )

    # Login uses the same failure as bad credentials.
    login_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 401
    assert login_res.json() == {"detail": "Invalid email or password"}

    # Pre-existing token rejected on /api/v1/admin/auth/me with 403
    token = create_access_token(data={"sub": str(user.id)})
    me_res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_res.status_code == 403
    assert "inactive" in me_res.json()["detail"].lower()


# ==============================================================================
# 4. Non-Admin Rejection (Traveler, Provider, Unknown Role)
# ==============================================================================


@pytest.mark.parametrize("non_admin_role", ["traveler", "provider", "moderator_guest"])
def test_non_admin_cannot_authenticate_via_admin_auth_endpoints(
    client: TestClient,
    db_session: Session,
    non_admin_role: str,
):
    email = f"user.{non_admin_role}@ceylongo.com"
    password = "UserPassword123!"
    user = _create_user(
        db_session,
        email=email,
        role=non_admin_role,
        password=password,
    )

    # 1. Attempting admin login with wrong password returns 401 (no role oracle)
    bad_pw_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": email, "password": "WrongPassword999!"},
    )
    assert bad_pw_res.status_code == 401
    assert bad_pw_res.json() == {"detail": "Invalid email or password"}

    # 2. Valid credentials for a non-admin produce the same login failure.
    good_pw_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": email, "password": password},
    )
    assert good_pw_res.status_code == 401
    assert good_pw_res.json() == bad_pw_res.json()
    assert "access_token" not in good_pw_res.json()

    # 3. Attempting /api/v1/admin/auth/me with a valid traveler/provider JWT returns 403
    user_token = create_access_token(data={"sub": str(user.id)})
    me_res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert me_res.status_code == 403
    assert "admin role required" in me_res.json()["detail"].lower()


def test_admin_me_rejects_unauthenticated_invalid_and_expired_tokens(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        email="token.check.admin@ceylongo.com",
        role="operations_admin",
    )

    # Missing token
    res_missing = client.get("/api/v1/admin/auth/me")
    assert res_missing.status_code == 401

    # Malformed token
    res_invalid = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": "Bearer malformed-jwt-token"},
    )
    assert res_invalid.status_code == 401

    # Expired token
    expired_token = create_access_token(
        data={"sub": str(admin.id)},
        expires_delta=timedelta(minutes=-10),
    )
    res_expired = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert res_expired.status_code == 401
    assert "expired" in res_expired.json()["detail"].lower()


# ==============================================================================
# 5. Sensitive Data Protection & Rate Limiting
# ==============================================================================


def test_admin_auth_responses_never_expose_secrets_or_hashes(
    client: TestClient,
    db_session: Session,
):
    raw_password = "TopSecretAdminPassword123!"
    user = _create_user(
        db_session,
        email="safe.fields@ceylongo.com",
        role="finance_admin",
        password=raw_password,
    )
    user.google_sub = "secret_google_sub_identifier"
    db_session.commit()

    login_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "safe.fields@ceylongo.com", "password": raw_password},
    )
    assert login_res.status_code == 200
    body = login_res.json()

    expected_user_keys = {
        "id",
        "full_name",
        "email",
        "role",
        "admin_role",
        "is_active",
        "created_at",
    }
    assert set(body["user"].keys()) == expected_user_keys
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "google_sub" not in body["user"]
    assert raw_password not in login_res.text
    assert user.password_hash not in login_res.text
    assert "secret_google_sub_identifier" not in login_res.text


def test_admin_login_rate_limiting(client: TestClient):
    for i in range(5):
        res = client.post(
            "/api/v1/admin/auth/login",
            json={"email": f"attempt{i}@ceylongo.com", "password": "WrongPassword123!"},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
        )
        assert res.status_code == 401

    blocked_res = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "attempt6@ceylongo.com", "password": "WrongPassword123!"},
        headers={"X-Forwarded-For": "198.51.100.6"},
    )
    assert blocked_res.status_code == 429
    assert "Retry-After" in blocked_res.headers
    assert "Too many authentication attempts" in blocked_res.json()["detail"]


# ==============================================================================
# 6. Reuse of Existing Password Reset Flow & Traveler/Provider Regression
# ==============================================================================


def test_admin_password_reset_reuses_existing_secure_flow(
    client: TestClient,
    db_session: Session,
):
    admin = _create_user(
        db_session,
        email="reset.admin@ceylongo.com",
        role="support_admin",
        password="OldAdminPassword123!",
    )

    raw_token = "admin-password-reset-token-xyz987"
    reset_record = PasswordReset(
        user_id=admin.id,
        token_hash=hash_reset_token(raw_token),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=30),
        is_used=False,
    )
    db_session.add(reset_record)
    db_session.commit()

    # Verify reset token via existing endpoint
    verify_res = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert verify_res.status_code == 200
    assert verify_res.json()["valid"] is True

    # Complete password reset via existing endpoint
    reset_res = client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "NewAdminPassword456!"},
    )
    assert reset_res.status_code == 200

    # Old password rejected on admin login
    old_login = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "reset.admin@ceylongo.com", "password": "OldAdminPassword123!"},
    )
    assert old_login.status_code == 401

    # New password succeeds on admin login
    new_login = client.post(
        "/api/v1/admin/auth/login",
        json={"email": "reset.admin@ceylongo.com", "password": "NewAdminPassword456!"},
    )
    assert new_login.status_code == 200
    assert new_login.json()["user"]["admin_role"] == "support_admin"
    repeated = client.get(f"/api/v1/auth/verify-reset-token?token={raw_token}")
    assert repeated.status_code == 400
    assert repeated.json() == {"detail": "Invalid or expired password reset token."}


@pytest.mark.parametrize("linked", [False, True])
def test_google_admin_sign_in_and_linking_are_blocked(client, db_session, monkeypatch, linked):
    admin = _create_user(db_session, email="google-admin@example.com", role="operations_admin",
                         password="ValidPassword123!")
    if linked:
        admin.google_sub = "test-google-sub"
        db_session.commit()
    monkeypatch.setattr("app.routers.auth.verify_google_id_token", lambda _: {
        "google_sub": "test-google-sub", "email": admin.email, "full_name": admin.full_name,
    })
    response = client.post("/api/v1/auth/google", json={"id_token": "mock-google-token"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    db_session.refresh(admin)
    assert admin.google_sub == ("test-google-sub" if linked else None)
    old_token = sign_raw_access_token({"sub": str(admin.id)})
    assert client.get("/api/v1/admin/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    assert client.post("/api/v1/auth/check-email", json={"email": admin.email}).json()["is_registered"] is False


@pytest.mark.parametrize("verify", [True, False])
def test_recovery_rate_limit_ignores_forwarded_header(client, verify):
    for index in range(6):
        headers = {"X-Forwarded-For": f"198.51.100.{index + 1}"}
        if verify:
            response = client.get("/api/v1/auth/verify-reset-token", params={"token": "invalid"}, headers=headers)
        else:
            response = client.post("/api/v1/auth/reset-password", headers=headers,
                                   json={"token": "invalid", "new_password": "SecurePassword123!"})
        assert response.status_code == (400 if index < 5 else 429)


def test_traveler_and_provider_login_flows_remain_unchanged(
    client: TestClient,
    db_session: Session,
):
    # 1. Traveler registration & login
    reg_res = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Regression Traveler",
            "email": "reg.traveler@ceylongo.com",
            "password": "TravelerPassword123!",
        },
    )
    assert reg_res.status_code == 201

    trav_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "reg.traveler@ceylongo.com",
            "password": "TravelerPassword123!",
        },
    )
    assert trav_login.status_code == 200
    assert trav_login.json()["user"]["role"] == "traveler"

    # 2. Approved provider login
    provider_user = _create_user(
        db_session,
        email="reg.provider@ceylongo.com",
        role="provider",
        password="ProviderStrongPassword123!",
        full_name="Regression Provider",
    )
    profile = ProviderProfile(
        user_id=provider_user.id,
        business_name="Ceylon Heritage Tours",
        provider_type="tour_guide",
        phone="0771234567",
        district="Kandy",
        approval_status="approved",
    )
    db_session.add(profile)
    db_session.commit()

    prov_login = client.post(
        "/api/v1/providers/login",
        json={
            "email": "reg.provider@ceylongo.com",
            "password": "ProviderStrongPassword123!",
        },
    )
    assert prov_login.status_code == 200
    assert prov_login.json()["user"]["role"] == "provider"
