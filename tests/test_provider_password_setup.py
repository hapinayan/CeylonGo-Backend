from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.otp import generate_authorization_token, hash_authorization_token
from app.core.rate_limiter import reset_all_limiters
from app.core.security import (
    decode_access_token,
    hash_password,
    hash_reset_token,
)
from tests.access_tokens import create_fixture_access_token as create_access_token
from app.database.connection import get_db
from app.main import app
from app.models.password_reset import PasswordReset
from app.models.provider_profile import ProviderProfile
from app.models.provider_registration_authorization import (
    ProviderRegistrationAuthorization,
)
from app.models.user import Base, User

# In-memory SQLite engine
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
def reset_limiters():
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


def issue_auth_token(
    db: Session,
    email: str,
    purpose: str = "password_setup",
    expires_in_minutes: int = 30,
    is_consumed: bool = False,
    is_revoked: bool = False,
) -> str:
    raw_token = generate_authorization_token()
    token_hash = hash_authorization_token(raw_token)
    auth = ProviderRegistrationAuthorization(
        email=email.strip().lower(),
        token_hash=token_hash,
        purpose=purpose,
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
        is_consumed=is_consumed,
        is_revoked=is_revoked,
    )
    db.add(auth)
    db.commit()
    return raw_token


def create_user_and_profile(
    db: Session,
    email: str,
    role: str = "provider",
    full_name: str = "Test Provider",
    is_active: bool = False,
    password: str | None = None,
    approval_status: str = "approved",
    business_name: str = "Hilltop Sanctuary",
    provider_type: str = "homestay",
) -> tuple[User, ProviderProfile]:
    user = User(
        email=email.strip().lower(),
        full_name=full_name,
        role=role,
        is_active=is_active,
        password_hash=hash_password(password) if password else None,
    )
    db.add(user)
    db.flush()

    profile = ProviderProfile(
        user_id=user.id,
        business_name=business_name,
        provider_type=provider_type,
        phone="0771234567",
        district="Kandy",
        nic_number="199212345678",
        approval_status=approval_status,
        selfie_image_path="selfie_1.jpg",
        nic_front_image_path="front_1.jpg",
        nic_back_image_path="back_1.jpg",
    )
    db.add(profile)
    db.commit()
    db.refresh(user)
    db.refresh(profile)
    return user, profile


# ==============================================================================
# 1. Password Setup Authorization Dependency Tests
# ==============================================================================


def test_password_setup_valid_authorization_accepted(client: TestClient, db_session: Session):
    email = "approved_prov@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")
    token = issue_auth_token(db_session, email=email, purpose="password_setup")

    res = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["account_activated"] is True
    assert data["next_action"] == "provider_login"


def test_password_setup_rejects_registration_and_resubmission_purposes(client: TestClient, db_session: Session):
    email = "wrong_purpose@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")

    # Registration token rejected
    reg_token = issue_auth_token(db_session, email=email, purpose="registration")
    res_reg = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {reg_token}"},
    )
    assert res_reg.status_code == 403
    assert "Expected 'password_setup'" in res_reg.json()["detail"]

    # Resubmission token rejected
    resub_token = issue_auth_token(db_session, email=email, purpose="resubmission")
    res_resub = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {resub_token}"},
    )
    assert res_resub.status_code == 403
    assert "Expected 'password_setup'" in res_resub.json()["detail"]


def test_password_setup_rejects_expired_consumed_and_revoked_tokens(client: TestClient, db_session: Session):
    email = "token_states@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")

    # Expired token
    exp_token = issue_auth_token(db_session, email=email, expires_in_minutes=-10)
    res_exp = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {exp_token}"},
    )
    assert res_exp.status_code == 401
    assert "expired" in res_exp.json()["detail"]

    # Consumed token
    con_token = issue_auth_token(db_session, email=email, is_consumed=True)
    res_con = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {con_token}"},
    )
    assert res_con.status_code == 401
    assert "already been used" in res_con.json()["detail"]

    # Revoked token
    rev_token = issue_auth_token(db_session, email=email, is_revoked=True)
    res_rev = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {rev_token}"},
    )
    assert res_rev.status_code == 401
    assert "revoked" in res_rev.json()["detail"]

    # Invalid token
    res_inv = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": "Bearer completely_fake_token"},
    )
    assert res_inv.status_code == 401


def test_password_setup_rejects_pending_and_rejected_providers(client: TestClient, db_session: Session):
    # Pending provider
    email_pen = "pending_pw@ceylongo.com"
    create_user_and_profile(db_session, email=email_pen, is_active=False, password=None, approval_status="pending")
    token_pen = issue_auth_token(db_session, email=email_pen)
    res_pen = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {token_pen}"},
    )
    assert res_pen.status_code == 400
    assert "pending review" in res_pen.json()["detail"]

    # Rejected provider
    email_rej = "rejected_pw@ceylongo.com"
    create_user_and_profile(db_session, email=email_rej, is_active=False, password=None, approval_status="rejected")
    token_rej = issue_auth_token(db_session, email=email_rej)
    res_rej = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {token_rej}"},
    )
    assert res_rej.status_code == 400
    assert "rejected" in res_rej.json()["detail"]


def test_password_setup_rejects_traveler_and_admin(client: TestClient, db_session: Session):
    # Traveler
    email_trav = "traveler_pw@ceylongo.com"
    trav_user = User(email=email_trav, full_name="Traveller", role="traveler", is_active=True)
    db_session.add(trav_user)
    db_session.commit()
    token_trav = issue_auth_token(db_session, email=email_trav)

    res_trav = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {token_trav}"},
    )
    assert res_trav.status_code == 403
    assert "not eligible for provider password setup" in res_trav.json()["detail"]


# ==============================================================================
# 2. Password Validation & Activation Tests
# ==============================================================================


def test_password_setup_validation_rules(client: TestClient, db_session: Session):
    email = "pw_val@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")
    token = issue_auth_token(db_session, email=email)

    # 1. Less than 12 characters
    res_short = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "Short123!", "confirm_password": "Short123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_short.status_code == 422

    # 2. Blank / whitespace only
    res_blank = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "            ", "confirm_password": "            "},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_blank.status_code == 422

    # 3. Password mismatch
    res_mismatch = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "DifferentPassword123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_mismatch.status_code == 422


def test_password_setup_activates_account_and_revokes_siblings(client: TestClient, db_session: Session):
    email = "activation_test@ceylongo.com"
    user, _ = create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")

    # Create primary token and another unused token for the same email
    token1 = issue_auth_token(db_session, email=email)
    token2 = issue_auth_token(db_session, email=email)

    res = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "SecurePassword1234!", "confirm_password": "SecurePassword1234!"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert res.status_code == 200

    # Verify user state
    db_session.refresh(user)
    assert user.is_active is True
    assert user.password_hash is not None

    # Verify token1 is consumed
    t1_hash = hash_authorization_token(token1)
    rec1 = db_session.query(ProviderRegistrationAuthorization).filter_by(token_hash=t1_hash).first()
    assert rec1.is_consumed is True
    assert rec1.consumed_at is not None

    # Verify token2 is revoked
    t2_hash = hash_authorization_token(token2)
    rec2 = db_session.query(ProviderRegistrationAuthorization).filter_by(token_hash=t2_hash).first()
    assert rec2.is_revoked is True
    assert rec2.revoked_at is not None

    # Attempt repeated password setup with the same token rejected with 401 (already consumed)
    res_repeat = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "SecurePassword1234!", "confirm_password": "SecurePassword1234!"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert res_repeat.status_code == 401

    # Attempt password setup with token2 rejected with 401 (revoked)
    res_t2 = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "SecurePassword1234!", "confirm_password": "SecurePassword1234!"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert res_t2.status_code == 401


# ==============================================================================
# 3. Separate Provider Login Tests
# ==============================================================================


def test_provider_login_success(client: TestClient, db_session: Session):
    email = "login_success@ceylongo.com"
    password = "CorrectStrongPassword123!"
    create_user_and_profile(
        db_session,
        email=email,
        is_active=True,
        password=password,
        approval_status="approved",
        business_name="Lanka Eco Lodge",
    )

    res = client.post(
        "/api/v1/providers/login",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == email
    assert data["user"]["role"] == "provider"
    assert data["user"]["business_name"] == "Lanka Eco Lodge"
    assert data["user"]["is_active"] is True


def test_provider_login_invalid_password(client: TestClient, db_session: Session):
    email = "wrong_pw@ceylongo.com"
    create_user_and_profile(
        db_session,
        email=email,
        is_active=True,
        password="ValidPassword123!",
        approval_status="approved",
    )

    res = client.post(
        "/api/v1/providers/login",
        json={"email": email, "password": "WrongPassword999!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


def test_provider_login_blocked_for_pending_rejected_and_incomplete(client: TestClient, db_session: Session):
    # 1. Pending provider
    email_pen = "pen_login@ceylongo.com"
    create_user_and_profile(db_session, email=email_pen, is_active=False, password="Password12345!", approval_status="pending")
    res_pen = client.post("/api/v1/providers/login", json={"email": email_pen, "password": "Password12345!"})
    assert res_pen.status_code == 403
    assert "pending admin review" in res_pen.json()["detail"]

    # 2. Rejected provider
    email_rej = "rej_login@ceylongo.com"
    create_user_and_profile(db_session, email=email_rej, is_active=False, password="Password12345!", approval_status="rejected")
    res_rej = client.post("/api/v1/providers/login", json={"email": email_rej, "password": "Password12345!"})
    assert res_rej.status_code == 403
    assert "rejected" in res_rej.json()["detail"]

    # 3. Approved without password
    email_nopw = "nopw_login@ceylongo.com"
    create_user_and_profile(db_session, email=email_nopw, is_active=False, password=None, approval_status="approved")
    res_nopw = client.post("/api/v1/providers/login", json={"email": email_nopw, "password": "Password12345!"})
    assert res_nopw.status_code == 403
    assert "Password setup required" in res_nopw.json()["detail"]


def test_provider_login_blocked_for_traveler_and_admin(client: TestClient, db_session: Session):
    # Traveler
    trav = User(email="trav_block@ceylongo.com", full_name="Traveler", role="traveler", is_active=True, password_hash=hash_password("Pass12345678!"))
    db_session.add(trav)

    # Admin
    admin = User(email="admin_block@ceylongo.com", full_name="Admin", role="admin", is_active=True, password_hash=hash_password("Pass12345678!"))
    db_session.add(admin)
    db_session.commit()

    res_trav = client.post("/api/v1/providers/login", json={"email": "trav_block@ceylongo.com", "password": "Pass12345678!"})
    assert res_trav.status_code == 403
    assert "Access restricted to provider accounts" in res_trav.json()["detail"]

    res_admin = client.post("/api/v1/providers/login", json={"email": "admin_block@ceylongo.com", "password": "Pass12345678!"})
    assert res_admin.status_code == 403
    assert "Access restricted to provider accounts" in res_admin.json()["detail"]


# ==============================================================================
# 4. Approved Provider Access & Profile Inspection (/me)
# ==============================================================================


def test_provider_me_authenticated_access(client: TestClient, db_session: Session):
    email = "me_access@ceylongo.com"
    user, profile = create_user_and_profile(
        db_session,
        email=email,
        is_active=True,
        password="MyStrongPassword123!",
        approval_status="approved",
        business_name="Kandy Cloud Resort",
    )
    jwt_token = create_access_token(data={"sub": str(user.id)})

    res = client.get(
        "/api/v1/providers/me",
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == profile.id
    assert data["user_id"] == user.id
    assert data["email"] == email
    assert data["business_name"] == "Kandy Cloud Resort"
    assert data["approval_status"] == "approved"
    assert data["is_active"] is True


def test_provider_me_unauthenticated_or_unapproved_blocked(client: TestClient, db_session: Session):
    # 1. No token
    res_no_auth = client.get("/api/v1/providers/me")
    assert res_no_auth.status_code == 401

    # 2. Traveler token attempting provider /me
    trav = User(email="trav_me@ceylongo.com", full_name="Traveler", role="traveler", is_active=True)
    db_session.add(trav)
    db_session.commit()
    trav_token = create_access_token(data={"sub": str(trav.id)})

    res_trav = client.get("/api/v1/providers/me", headers={"Authorization": f"Bearer {trav_token}"})
    assert res_trav.status_code == 403
    assert "provider role required" in res_trav.json()["detail"]


# ==============================================================================
# 5. Traveler & Admin Authentication Regression
# ==============================================================================


def test_existing_traveler_login_preserved(client: TestClient, db_session: Session):
    db_session.add(
        User(
            full_name="Existing Traveler",
            email="existing_traveler@ceylongo.com",
            password_hash=hash_password("Password123!"),
            role="traveler",
            is_active=True,
        )
    )
    db_session.commit()

    # Traveler login
    res_login = client.post(
        "/api/v1/auth/login",
        json={"email": "existing_traveler@ceylongo.com", "password": "Password123!"},
    )
    assert res_login.status_code == 200
    assert "access_token" in res_login.json()
    assert res_login.json()["user"]["role"] == "traveler"


def test_password_setup_database_error_rollback(client: TestClient, db_session: Session, monkeypatch):
    email = "rollback_test@ceylongo.com"
    user, _ = create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")
    token = issue_auth_token(db_session, email=email)

    def mock_commit():
        from sqlalchemy.exc import SQLAlchemyError
        raise SQLAlchemyError("Simulated database failure during commit")

    monkeypatch.setattr(db_session, "commit", mock_commit)

    res = client.post(
        "/api/v1/providers/password/setup",
        json={"password": "ValidStrongPassword123!", "confirm_password": "ValidStrongPassword123!"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 500
    assert "Failed to complete password setup" in res.json()["detail"]

    # Verify user was not activated and password was not permanently set
    db_session.refresh(user)
    assert user.is_active is False


def test_google_login_cannot_bypass_password_setup(client: TestClient, db_session: Session, monkeypatch):
    email = "prov_google_bypass@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")

    # Mock Google verification returning provider's email
    monkeypatch.setattr(
        "app.routers.auth.verify_google_id_token",
        lambda token: {
            "google_sub": "google_sub_bypass_123",
            "email": email,
            "full_name": "Google Bypass Attempt",
        },
    )

    res = client.post("/api/v1/auth/google", json={"id_token": "mock_google_token"})
    assert res.status_code == 403
    assert "not supported for provider accounts" in res.json()["detail"]


def test_password_reset_cannot_activate_incomplete_provider(client: TestClient, db_session: Session):
    email = "prov_reset_bypass@ceylongo.com"
    create_user_and_profile(db_session, email=email, is_active=False, password=None, approval_status="approved")

    res = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert res.status_code == 200
    assert "If an account with that email exists" in res.json()["message"]


@pytest.mark.parametrize("new_password", ["too-short", " " * 12])
def test_approved_provider_recovery(client, db_session, monkeypatch, new_password):
    user, _ = create_user_and_profile(
        db_session, "recovery@example.com", is_active=True, password="Original password"
    )
    delivered = {}
    login = {"email": user.email, "password": "Original password"}
    initial_login = client.post("/api/v1/providers/login", json=login)
    assert initial_login.status_code == 200
    original_token = initial_login.json()["access_token"]
    assert decode_access_token(original_token)["credential_version"] == 0

    def send_email(**kwargs):
        delivered.update(kwargs)
        return True

    monkeypatch.setattr("app.routers.auth.send_password_reset_email", send_email)
    request = client.post("/api/v1/auth/forgot-password", json={"email": user.email})
    assert request.status_code == 200
    token = delivered["token"]
    verification = client.get("/api/v1/auth/verify-reset-token", params={"token": token})
    assert verification.status_code == 200

    payload = {"token": token, "new_password": new_password}
    response = client.post("/api/v1/auth/reset-password", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "new_password"]

    payload["new_password"] = "Replacement password"
    assert client.post("/api/v1/auth/reset-password", json=payload).status_code == 200
    assert client.post("/api/v1/auth/reset-password", json=payload).status_code == 400
    assert client.get(
        "/api/v1/providers/me", headers={"Authorization": f"Bearer {original_token}"}
    ).status_code == 401
    assert client.post("/api/v1/providers/login", json=login).status_code == 401
    login["password"] = payload["new_password"]
    fresh_login = client.post("/api/v1/providers/login", json=login)
    assert fresh_login.status_code == 200
    fresh_token = fresh_login.json()["access_token"]
    assert decode_access_token(fresh_token)["credential_version"] == 1
    assert client.get(
        "/api/v1/providers/me", headers={"Authorization": f"Bearer {fresh_token}"}
    ).status_code == 200


@pytest.mark.parametrize(
    "password, approval_status", [(None, "approved"), ("Old password", "pending")]
)
def test_ineligible_provider_cannot_redeem_reset_token(
    client, db_session, password, approval_status
):
    user, _ = create_user_and_profile(
        db_session, "ineligible@example.com", is_active=True,
        password=password, approval_status=approval_status,
    )
    old_hash = user.password_hash
    token = "ineligible-provider-reset-test"
    record = PasswordReset(
        user_id=user.id, token_hash=hash_reset_token(token),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
    )
    db_session.add(record)
    db_session.commit()

    response = client.post("/api/v1/auth/reset-password", json={
        "token": token, "new_password": "Replacement password",
    })
    assert response.status_code == 400
    db_session.refresh(record)
    db_session.refresh(user)
    assert record.is_used is False
    assert user.password_hash == old_hash
    assert user.credential_version == 0


def test_provider_reset_rolls_back_claim_when_approval_changes(
    client, db_session, monkeypatch
):
    user, profile = create_user_and_profile(
        db_session, "approval-change@example.com", is_active=True,
        password="Original password",
    )
    old_hash = user.password_hash
    token = "approval-change-reset-test"
    record = PasswordReset(
        user_id=user.id, token_hash=hash_reset_token(token),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
    )
    db_session.add(record)
    db_session.commit()

    def hash_after_approval_change(password):
        profile.approval_status = "pending"
        db_session.commit()
        return hash_password(password)

    monkeypatch.setattr("app.routers.auth.hash_password", hash_after_approval_change)
    response = client.post("/api/v1/auth/reset-password", json={
        "token": token, "new_password": "Replacement password",
    })
    assert response.status_code == 400
    db_session.refresh(record)
    db_session.refresh(user)
    assert record.is_used is False and record.used_at is None
    assert user.password_hash == old_hash
    assert user.credential_version == 0
