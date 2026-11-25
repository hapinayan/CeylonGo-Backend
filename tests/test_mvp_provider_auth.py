import io
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.mvp_security import create_mvp_access_token
from app.core.rate_limiter import reset_all_limiters
from app.core.security import hash_password
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPProviderEmailChallenge, MVPProviderProfile, MVPUser
from app.services import mvp_provider_otp

API = "/api/v1"
EMAIL = "provider@example.com"
PASSWORD = "ProviderPass123!"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    MVPBase.metadata.create_all(engine)
    app = create_mvp_app()

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_mvp_db] = db
    sent = []
    monkeypatch.setenv("PROVIDER_UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(mvp_provider_otp, "send_provider_otp_email",
                        lambda email, code, **_: not sent.append((email, code)))
    monkeypatch.setattr("app.routers.mvp.admin.send_provider_approval_email", lambda *_: True)
    reset_all_limiters()
    with Session(engine) as db:
        admin = MVPUser(email="admin@example.com", full_name="Admin",
                        hashed_password=hash_password(PASSWORD), role="admin")
        db.add(admin)
        db.commit()
        admin_token = create_mvp_access_token(admin.id, admin.email, admin.role)
    with TestClient(app) as client:
        yield client, engine, sent, admin_token
    engine.dispose()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def send(client):
    return client.post(API + "/providers/otp/send", json={"email": EMAIL})


def verify(client, code):
    return client.post(API + "/providers/otp/verify", json={"email": EMAIL, "otp": code})


def test_provider_workflow(setup, monkeypatch):
    client, _, sent, admin_token = setup
    now = mvp_provider_otp.utc_now()
    monkeypatch.setattr(mvp_provider_otp, "utc_now", lambda: now)
    response = send(client)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"message", "email"}
    assert send(client).status_code == 429
    response = verify(client, sent[-1][1])
    assert response.json()["next_action"] == "continue_registration"
    headers = bearer(response.json()["authorization_token"])
    assert verify(client, sent[-1][1]).status_code == 400
    assert client.get(API + "/providers/me", headers=headers).status_code == 401
    assert client.get(API + "/providers/registration/draft", headers=headers).status_code == 200
    result = client.put(API + "/providers/registration/personal", headers=headers, json={
        "full_name": "Provider Example", "phone": "0771234567", "nic_number": "901234567V",
        "date_of_birth": "1990-01-01", "gender": "Other",
        "permanent_province": "Western", "permanent_district": "Colombo",
        "permanent_city": "Colombo", "permanent_landmark": "Example road 15",
        "permanent_postal_code": "00100",
    })
    assert result.status_code == 200, result.text
    result = client.put(API + "/providers/registration/business", headers=headers, json={
        "business_name": "MVP Homestay", "provider_type": "homestay", "same_as_permanent": True,
    })
    assert result.status_code == 200, result.text
    assert client.post(API + "/providers/registration/submit", headers=headers).status_code == 409
    photo = io.BytesIO()
    Image.new("RGB", (10, 10)).save(photo, format="PNG")
    result = client.post(API + "/providers/registration/identity", headers=headers,
                         files={kind: ("image.png", photo.getvalue(), "image/png")
                                for kind in ("selfie", "nic_front", "nic_back")})
    assert result.status_code == 200, result.text
    assert result.json()["identity_completed"]
    result = client.post(API + "/providers/registration/submit", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["approval_status"] == "pending"
    assert client.post(API + "/providers/registration/submit", headers=headers).status_code == 401
    credentials = {"email": EMAIL, "password": PASSWORD}
    assert client.post(API + "/providers/login", json=credentials).status_code in {401, 403}
    admin = bearer(admin_token)
    listing = client.get(API + "/admin/providers", headers=admin)
    provider_id = listing.json()[0]["id"]
    assert client.get(f"{API}/admin/providers/{provider_id}/identity/selfie", headers=admin).status_code == 200
    result = client.post(f"{API}/admin/providers/{provider_id}/decision", headers=admin,
                         json={"verification_status": "approved"})
    assert result.status_code == 200, result.text
    now += timedelta(minutes=2)
    assert send(client).status_code == 200
    response = verify(client, sent[-1][1])
    assert response.json()["next_action"] == "create_password"
    setup_headers = bearer(response.json()["authorization_token"])
    password = {"password": PASSWORD, "confirm_password": PASSWORD}
    result = client.post(API + "/providers/password/setup", headers=setup_headers, json=password)
    assert result.status_code == 200, result.text
    assert client.post(API + "/providers/password/setup", headers=setup_headers,
                       json=password).status_code == 401
    result = client.post(API + "/providers/login", json=credentials)
    assert result.status_code == 200, result.text
    session = bearer(result.json()["access_token"])
    assert client.get(API + "/providers/me", headers=session).status_code == 200
    assert client.post(API + "/providers/logout", headers=session).status_code == 200
    assert client.get(API + "/providers/me", headers=session).status_code == 401


def test_provider_otp_resend_lockout_and_delivery_failure(setup, monkeypatch):
    client, engine, sent, _ = setup
    now = mvp_provider_otp.utc_now()
    monkeypatch.setattr(mvp_provider_otp, "utc_now", lambda: now)
    assert send(client).status_code == 200
    original = sent[-1][1]
    with Session(engine) as db:
        assert db.query(MVPProviderEmailChallenge).one().challenge_hash != original.encode()
    now += timedelta(seconds=61)
    assert send(client).status_code == 200
    assert sent[-1][1] != original
    for _ in range(5):
        assert verify(client, original).status_code == 400
    assert verify(client, sent[-1][1]).status_code == 400
    now += timedelta(seconds=61)
    monkeypatch.setattr(mvp_provider_otp, "send_provider_otp_email", lambda *_, **__: False)
    assert send(client).status_code == 503


def test_pending_provider_cannot_use_password_or_existing_session(setup):
    client, engine, _, _ = setup
    with Session(engine) as db:
        account = MVPUser(email=EMAIL, full_name="Pending Provider", role="provider",
                          hashed_password=hash_password(PASSWORD), is_active=True)
        db.add(MVPProviderProfile(
            user=account, business_name="Pending Homestay", phone="0771234567",
            district="Colombo", verification_status="pending",
        ))
        db.commit()
        session = create_mvp_access_token(account.id, account.email, account.role)
    credentials = {"email": EMAIL, "password": PASSWORD}
    assert client.post(API + "/providers/login", json=credentials).status_code == 403
    assert client.post(API + "/auth/login", json=credentials).status_code == 403
    assert client.get(API + "/providers/me", headers=bearer(session)).status_code == 403
    assert client.post(API + "/auth/register", json={
        **credentials, "role": "admin", "full_name": "Untrusted Admin",
    }).status_code == 403
