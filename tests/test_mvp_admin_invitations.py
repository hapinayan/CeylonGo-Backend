from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token, hash_password
from app.database.connection import get_db
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPUser
from app.models.admin_invitation import AdminInvitation
from app.core.rate_limiter import reset_all_limiters


@pytest.fixture
def test_db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    MVPBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    reset_all_limiters()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(test_db_session, monkeypatch):
    delivery = Mock(return_value=True)
    monkeypatch.setenv("ADMIN_INVITATION_ACTIVATION_URL", "https://admin.example.test/admin/activate")
    monkeypatch.setattr("app.services.admin_invitation_service.is_smtp_configured", lambda: True)
    monkeypatch.setattr("app.services.admin_invitation_service.send_admin_invitation_email", delivery)

    app = create_mvp_app()
    app.dependency_overrides[get_db] = lambda: test_db_session
    app.dependency_overrides[get_mvp_db] = lambda: test_db_session
    with TestClient(app) as test_client:
        test_client.delivery = delivery
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def super_admin_token(test_db_session):
    user = MVPUser(
        email="superadmin@ceylongo.lk",
        full_name="Super Admin User",
        role="super_admin",
        hashed_password=hash_password("SuperSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "super_admin"})


@pytest.fixture
def ordinary_admin_token(test_db_session):
    user = MVPUser(
        email="opsadmin@ceylongo.lk",
        full_name="Operations Admin User",
        role="admin",
        hashed_password=hash_password("OpsSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "admin"})


@pytest.fixture
def traveler_token(test_db_session):
    user = MVPUser(
        email="traveler@ceylongo.lk",
        full_name="Traveler User",
        role="traveler",
        hashed_password=hash_password("TravelerSecret123!"),
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(user)
    test_db_session.commit()
    test_db_session.refresh(user)
    return create_access_token(data={"sub": str(user.id), "role": "traveler"})


def _delivered_token(client):
    url = client.delivery.call_args.args[1]
    return parse_qs(urlsplit(url).query)["token"][0]


def test_super_admin_can_issue_invitation(client, super_admin_token):
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "finance.lead@ceylongo.lk",
            "full_name": "Finance Lead",
            "role": "admin",
            "department": "Finance & Compliance",
            "invitation_note": "Welcome to the finance admin team.",
        },
    )
    assert res.status_code == 201
    data = res.json()
    assert data["email"] == "finance.lead@ceylongo.lk"
    assert data["role"] == "admin"
    assert data["status"] == "pending"
    assert data["raw_token"] is None
    assert data["email_delivered"] is True


def test_ordinary_admin_cannot_issue_invitations(client, ordinary_admin_token):
    res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {ordinary_admin_token}"},
        json={
            "email": "another@ceylongo.lk",
            "full_name": "Another Admin",
            "role": "admin",
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
            "role": "admin",
        },
    )
    assert res.status_code in [401, 403]


def test_unauthenticated_cannot_access_invitations(client):
    res = client.post(
        "/api/v1/admin/management/invitations",
        json={
            "email": "another@ceylongo.lk",
            "full_name": "Another Admin",
            "role": "admin",
        },
    )
    assert res.status_code in [401, 403]


def test_duplicate_active_invitation_rejected(client, super_admin_token):
    payload = {
        "email": "duplicate.test@ceylongo.lk",
        "full_name": "Duplicate Candidate",
        "role": "admin",
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
        "role": "admin",
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
            "role": "admin",
        },
    )
    assert create_res.status_code == 201
    inv_id = create_res.json()["id"]
    token1 = _delivered_token(client)

    # 2. Resend
    resend_res = client.post(
        f"/api/v1/admin/management/invitations/{inv_id}/resend",
        headers={"Authorization": f"Bearer {super_admin_token}"},
    )
    assert resend_res.status_code == 200
    token2 = _delivered_token(client)
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


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_account_activation_and_subsequent_login(client, super_admin_token, role):
    # 1. Create invitation
    invite_res = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={
            "email": "new.moderator@ceylongo.lk",
            "full_name": "New Moderator",
            "role": role,
        },
    )
    assert invite_res.status_code == 201
    raw_token = _delivered_token(client)

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
    assert me_res.json()["admin_role"] == role


def test_missing_schema_in_live_mode_returns_503(test_db_session, super_admin_token, client):
    AdminInvitation.__table__.drop(test_db_session.get_bind())
    result = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"email": "new@ceylongo.lk", "full_name": "New Admin", "role": "admin"},
    )
    assert result.status_code == 503

@pytest.mark.parametrize("role", [
    "operations_admin", "finance_admin", "content_moderator", "support_admin",
    "traveler", "provider",
])
def test_invitation_rejects_unsupported_roles(client, super_admin_token, role):
    response = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"email": "new@ceylongo.lk", "full_name": "New Admin", "role": role},
    )
    assert response.status_code == 422
    client.delivery.assert_not_called()


def test_activation_rejects_role_override_and_existing_account(
    client, super_admin_token, test_db_session,
):
    email = "existing@ceylongo.lk"
    response = client.post(
        "/api/v1/admin/management/invitations",
        headers={"Authorization": f"Bearer {super_admin_token}"},
        json={"email": email, "full_name": "New Admin", "role": "admin"},
    )
    assert response.status_code == 201
    url = "/api/v1/admin/auth/invitations/activate"
    payload = {"token": _delivered_token(client), "password": "SecurePassword123!"}
    assert client.post(url, json={**payload, "role": "super_admin"}).status_code == 422
    user = MVPUser(email=email, full_name="Existing", hashed_password="original", role="traveler")
    test_db_session.add(user)
    test_db_session.commit()
    assert client.post(url, json=payload).status_code == 409
    test_db_session.refresh(user)
    assert (user.role, user.hashed_password) == ("traveler", "original")
    assert test_db_session.get(AdminInvitation, response.json()["id"]).status == "pending"


def test_deactivated_inviter_and_last_super_admin(
    client, super_admin_token, test_db_session, ordinary_admin_token, traveler_token,
):
    issuer = test_db_session.query(MVPUser).filter_by(role="super_admin").one()
    issuer_headers = {"Authorization": f"Bearer {super_admin_token}"}
    invite = client.post(
        "/api/v1/admin/management/invitations",
        headers=issuer_headers,
        json={"email": "new@ceylongo.lk", "full_name": "New Admin", "role": "super_admin"},
    )
    assert invite.status_code == 201
    raw_token = _delivered_token(client)
    users_url = "/api/v1/admin/management/admins"
    toggle = {"is_active": False}
    assert client.post(f"{users_url}/{issuer.id}/status", headers=issuer_headers, json=toggle).status_code == 409
    traveler = test_db_session.query(MVPUser).filter_by(role="traveler").one()
    assert client.post(f"{users_url}/{traveler.id}/status", headers=issuer_headers, json=toggle).status_code == 404

    second = MVPUser(
        email="second@ceylongo.lk", full_name="Other Super Admin",
        hashed_password="unused", role="super_admin",
    )
    test_db_session.add(second)
    test_db_session.commit()
    second_token = create_access_token(data={"sub": str(second.id), "role": second.role})
    second_headers = {"Authorization": f"Bearer {second_token}"}
    assert client.post(
        f"{users_url}/{issuer.id}/status",
        headers={"Authorization": f"Bearer {ordinary_admin_token}"},
        json=toggle,
    ).status_code == 403
    assert client.post(f"{users_url}/{issuer.id}/status", headers=second_headers, json=toggle).status_code == 200
    assert client.post(
        "/api/v1/admin/auth/invitations/activate",
        json={"token": raw_token, "password": "SecurePassword123!"},
    ).status_code == 403
    assert client.post(
        f"{users_url}/{issuer.id}/status", headers=second_headers, json={"is_active": True},
    ).status_code == 200
    assert client.get(users_url, headers=issuer_headers).status_code == 401

def test_expired_invitation_is_reported_and_cannot_activate(
    client, super_admin_token, test_db_session,
):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    url = "/api/v1/admin/management/invitations"
    created = client.post(
        url,
        headers=headers,
        json={"email": "past@ceylongo.lk", "full_name": "Past Admin", "role": "admin"},
    )
    invitation = test_db_session.get(AdminInvitation, created.json()["id"])
    invitation.expires_at = datetime(2020, 1, 1)
    test_db_session.commit()

    assert client.get(url, headers=headers, params={"status": "pending"}).json() == []
    expired = client.get(url, headers=headers, params={"status": "expired"})
    assert [item["status"] for item in expired.json()] == ["expired"]
    result = client.post(
        "/api/v1/admin/auth/invitations/activate",
        json={"token": _delivered_token(client), "password": "SecurePassword123!"},
    )
    assert result.status_code == 410
