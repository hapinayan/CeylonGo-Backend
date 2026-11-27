import os
from datetime import UTC, datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.user import Base, User
from app.models.admin_invitation import AdminInvitation
from app.services.admin_invitation_service import _IN_MEMORY_INVITATIONS


@pytest.fixture
def test_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
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

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    _IN_MEMORY_INVITATIONS.clear()
    try:
        yield session
    finally:
        session.close()
        _IN_MEMORY_INVITATIONS.clear()


@pytest.fixture
def client(test_db_session):
    app = create_mvp_app()
    app.dependency_overrides[get_db] = lambda: test_db_session
    app.dependency_overrides[get_mvp_db] = lambda: test_db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def super_admin_token(test_db_session):
    user = User(
        email="superadmin@ceylongo.lk",
        full_name="Super Admin User",
        role="super_admin",
        password_hash=hash_password("SuperSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "super_admin"})


@pytest.fixture
def ordinary_admin_token(test_db_session):
    user = User(
        email="opsadmin@ceylongo.lk",
        full_name="Operations Admin User",
        role="operations_admin",
        password_hash=hash_password("OpsSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "operations_admin"})


@pytest.fixture
def traveler_token(test_db_session):
    user = User(
        email="traveler@ceylongo.lk",
        full_name="Traveler User",
        role="traveler",
        password_hash=hash_password("TravelerSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "traveler"})


def test_super_admin_can_issue_invitation(client, super_admin_token):
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "finance.lead@ceylongo.lk",
            "full_name": "Finance Lead",
            "role": "finance_admin",
            "department": "Finance & Compliance",
            "invitation_note": "Welcome to the finance admin team.",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["email"] == "finance.lead@ceylongo.lk"
    assert data["role"] == "finance_admin"
    assert data["status"] == "pending"
    assert "raw_token" in data
    assert data["raw_token"] is not None


def test_ordinary_admin_cannot_issue_invitations(client, ordinary_admin_token):
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {ordinary_admin_token}"},
        json={
            "email": "another@ceylongo.lk",
            "full_name": "Another Admin",
            "role": "support_admin",
        },
    )
    assert res.status_code == 403
    assert "Super Administrator authority required" in res.json()["detail"] or "Access forbidden" in res.json()["detail"]


def test_traveler_cannot_issue_invitations(client, traveler_token):
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {traveler_token}"},
        json={
            "email": "another@ceylongo.lk",
            "full_name": "Another Admin",
            "role": "support_admin",
        },
    )
    assert res.status_code in [401, 403]


def test_unauthenticated_cannot_access_invitations(client):
    res = client.post(
        "/api/v1/admin/management/invitations",
        json={
            "email": "another@ceylongo.lk",
            "full_name": "Another Admin",
            "role": "support_admin",
        },
    )
    assert res.status_code in [401, 403]


def test_duplicate_active_invitation_rejected(client, super_admin_token):
    payload = {
        "email": "duplicate.test@ceylongo.lk",
        "full_name": "Duplicate Candidate",
        "role": "support_admin",
    }
    first = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json=payload,
    )
    assert first.status_code == 201

    second = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json=payload,
    )
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


def test_invitation_to_existing_user_rejected(client, super_admin_token):
    payload = {
        "email": "superadmin@ceylongo.lk",
        "full_name": "Already Registered",
        "role": "support_admin",
    }
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json=payload,
    )
    assert res.status_code == 400
    assert "already exists" in res.json()["detail"]


def test_resend_and_revoke_invitation_workflow(client, super_admin_token):
    # 1. Create
    create_res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "workflow.test@ceylongo.lk",
            "full_name": "Workflow User",
            "role": "content_moderator",
        },
    )
    assert create_res.status_code == 201
    inv_id = create_res.json()["id"]
    token1 = create_res.json()["raw_token"]

    # 2. Resend
    resend_res = client.post(
        f"/api/v1/admin/management/invitations/{inv_id}/resend",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert resend_res.status_code == 200
    token2 = resend_res.json()["raw_token"]
    assert token1 != token2

    # Old token is now invalid/replaced
    verify_old = client.get(f"/api/v1/admin/auth/invitations/verify?token={token1}")
    assert verify_old.status_code == 404

    # New token is valid
    verify_new = client.get(f"/api/v1/admin/auth/invitations/verify?token={token2}")
    assert verify_new.status_code == 200
    assert verify_new.json()["email"] == "workflow.test@ceylongo.lk"

    # 3. Revoke
    revoke_res = client.post(
        f"/api/v1/admin/management/invitations/{inv_id}/revoke",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert revoke_res.status_code == 200

    # Revoked token rejected on verify
    verify_revoked = client.get(f"/api/v1/admin/auth/invitations/verify?token={token2}")
    assert verify_revoked.status_code == 400
    assert "revoked" in verify_revoked.json()["detail"]


def test_account_activation_and_subsequent_login(client, super_admin_token):
    # 1. Create invitation
    invite_res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "new.moderator@ceylongo.lk",
            "full_name": "New Moderator",
            "role": "content_moderator",
        },
    )
    assert invite_res.status_code == 201
    raw_token = invite_res.json()["raw_token"]

    # 2. Public verification of token
    verify_res = client.get(f"/api/v1/admin/auth/invitations/verify?token={raw_token}")
    assert verify_res.status_code == 200
    assert verify_res.json()["email"] == "new.moderator@ceylongo.lk"

    # 3. Password setup validation (short password rejected)
    short_res = client.post(
        "/api/v1/admin/auth/invitations/activate",
        json={"token": raw_token, "password": "short"},
    )
    assert short_res.status_code in [400, 422]

    # 4. Successful activation
    activate_res = client.post(
        "/api/v1/admin/auth/invitations/activate",
        json={"token": raw_token, "password": "SecurePassword123!"},
    )
    assert activate_res.status_code == 200
    assert activate_res.json()["success"] is True

    # 5. Token cannot be reused (single-use enforcement)
    reuse_res = client.post(
        "/api/v1/admin/auth/invitations/activate",
        json={"token": raw_token, "password": "SecurePassword123!"},
    )
    assert reuse_res.status_code == 400

    # 6. Immediate login with newly created credentials
    login_res = client.post(
        "/api/v1/admin/auth/login",
        json={
            "email": "new.moderator@ceylongo.lk",
            "password": "SecurePassword123!",
        },
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert "access_token" in login_data
    new_admin_jwt = login_data["access_token"]

    # 7. Access /me profile endpoint
    me_res = client.get(
        "/api/v1/admin/auth/me",
        headers={"Authorization": f"Bearer {new_admin_jwt}"},
    )
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "new.moderator@ceylongo.lk"
    assert me_res.json()["is_active"] is True


def test_missing_schema_in_live_mode_returns_503(monkeypatch, test_db_session, super_admin_token, client):
    # Simulate production/live mode where _is_isolated_test_environment returns False
    from app.services import admin_invitation_service
    from app.models.admin_invitation import AdminInvitation

    monkeypatch.setattr(admin_invitation_service, "_is_isolated_test_environment", lambda db: False)

    real_query = test_db_session.query

    def mock_query(model, *args, **kwargs):
        if model == AdminInvitation:
            raise Exception("sqlite3.OperationalError: no such table: admin_invitations")
        return real_query(model, *args, **kwargs)

    monkeypatch.setattr(test_db_session, "query", mock_query)

    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "prod.fail@ceylongo.lk",
            "full_name": "Prod Candidate",
            "role": "operations_admin",
        },
    )
    assert res.status_code == 503
    assert "schema version 1.0.8 migration" in res.json()["detail"]
