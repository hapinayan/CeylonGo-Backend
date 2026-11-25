import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core import email as email_service
from app.database.connection import get_db
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPUser
from app.models.traveler_email_challenge import TravelerEmailChallenge
from app.models.user import User
from app.routers.auth import router as legacy_router
from app.routers.mvp.auth import router as mvp_router
from app.services import traveler_email_verification as service

PREFIX = "/api/v1/auth"
EMAIL = "signup@example.com"
DETAILS = {"email": EMAIL, "full_name": "New Traveler", "password": "TestPassword123!"}


@pytest.fixture(params=["mvp", "legacy"])
def auth_client(request, tmp_path, traveler_otp_mailbox):
    engine = create_engine(f"sqlite:///{tmp_path / 'signup.sqlite'}")
    if request.param == "legacy":

        @event.listens_for(engine, "connect")
        def register_sqlserver_clock(connection, _record):
            connection.create_function("sysutcdatetime", 0, lambda: "2026-01-01 00:00:00")

        model, router, dependency, prefix = User, legacy_router, get_db, ""
    else:
        model, router, dependency, prefix = MVPUser, mvp_router, get_mvp_db, "/api/v1"
    model.__table__.create(engine)
    TravelerEmailChallenge.__table__.create(engine)
    sessions = sessionmaker(engine)

    def isolated_db():
        with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[dependency] = isolated_db
    with TestClient(app) as client:
        yield client, sessions, model
    engine.dispose()


def post(client, endpoint, data):
    return client.post(f"{PREFIX}/{endpoint}", json=data)


def register(client):
    response = post(client, "register", DETAILS)
    assert response.status_code == 202, response.text
    assert set(response.json()) == {
        "email", "message", "registration_id", "expires_in", "resend_after"
    }
    assert response.json()["expires_in"] == 300
    assert response.json()["resend_after"] == 60
    return {"email": EMAIL, "registration_id": response.json()["registration_id"]}


def test_registration_requires_email_ownership(auth_client, traveler_otp_mailbox):
    client, sessions, model = auth_client
    challenge = register(client)
    otp = traveler_otp_mailbox[EMAIL]
    with sessions() as db:
        assert db.query(model).count() == 0
        row = db.get(TravelerEmailChallenge, EMAIL)
        assert row.otp_hash == service._otp_digest(EMAIL, row.registration_hash, otp)
        assert row.registration_hash != challenge["registration_id"]
    assert post(client, "login", DETAILS).status_code == 401
    invalid = post(client, "verify-email", {**challenge, "otp": f"{otp}invalid"})
    assert invalid.status_code == 422
    assert otp not in invalid.text
    verified = post(client, "verify-email", {**challenge, "otp": otp})
    assert verified.status_code == 200
    assert set(verified.json()) == {"message"}
    assert post(client, "verify-email", {**challenge, "otp": otp}).status_code == 400
    assert post(client, "login", DETAILS).status_code == 200
    assert post(client, "register", DETAILS).status_code == 409
    with sessions() as db:
        row = db.get(TravelerEmailChallenge, EMAIL)
        assert row.otp_hash is row.password_hash is row.registration_hash is None


def test_cooldown_attempts_and_expiry(auth_client, traveler_otp_mailbox, monkeypatch):
    client, sessions, _ = auth_client
    now = datetime(2026, 1, 1)
    monkeypatch.setattr(service, "_utc_now", lambda: now)
    challenge = register(client)
    first_otp = traveler_otp_mailbox[EMAIL]
    early = post(client, "resend-email-otp", challenge)
    assert early.status_code == 429
    assert early.headers["Retry-After"] == "60"
    assert post(client, "register", DETAILS).status_code == 429

    wrong = "000000" if first_otp != "000000" else "000001"
    for _ in range(5):
        assert post(client, "verify-email", {**challenge, "otp": wrong}).status_code == 400
    assert post(client, "verify-email", {**challenge, "otp": first_otp}).status_code == 400
    with sessions() as db:
        assert db.get(TravelerEmailChallenge, EMAIL).attempts == 5

    now += timedelta(minutes=1)
    assert post(client, "resend-email-otp", challenge).status_code == 202
    next_otp = traveler_otp_mailbox[EMAIL]
    assert next_otp != first_otp
    assert post(client, "verify-email", {**challenge, "otp": first_otp}).status_code == 400
    now += timedelta(minutes=5)
    assert post(client, "verify-email", {**challenge, "otp": next_otp}).status_code == 400


def test_concurrent_registration_and_verification(auth_client, traveler_otp_mailbox):
    client, sessions, model = auth_client

    def simultaneous(endpoint, data):
        barrier = Barrier(2, timeout=5)

        def request():
            barrier.wait()
            return post(client, endpoint, data)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(request) for _ in range(2)]
            return [future.result(timeout=10) for future in futures]

    starts = simultaneous("register", DETAILS)
    assert sorted(response.status_code for response in starts) == [202, 429]
    pending = next(response.json() for response in starts if response.status_code == 202)
    verifies = simultaneous(
        "verify-email",
        {
            "email": EMAIL,
            "registration_id": pending["registration_id"],
            "otp": traveler_otp_mailbox[EMAIL],
        },
    )
    assert sorted(response.status_code for response in verifies) == [200, 400]
    with sessions() as db:
        assert db.query(model).count() == 1


def test_delivery_failure_creates_no_account(auth_client, monkeypatch):
    client, sessions, model = auth_client
    monkeypatch.setattr(service, "send_traveler_otp_email", lambda email, otp: False)
    response = post(client, "register", DETAILS)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert set(response.json()) == {"detail"}
    assert post(client, "login", DETAILS).status_code == 401
    with sessions() as db:
        assert db.query(model).count() == 0


def test_peer_verification_rate_limit(auth_client):
    client, _, _ = auth_client
    data = {"email": EMAIL, "registration_id": "a" * 43, "otp": "123456"}
    for _ in range(10):
        assert post(client, "verify-email", data).status_code == 400
    assert post(client, "verify-email", data).status_code == 429


def test_google_login_with_pending_signup(auth_client, traveler_otp_mailbox, monkeypatch):
    client, sessions, model = auth_client
    challenge = register(client)

    def verified_google_claims(_token):
        return {"email": EMAIL, "full_name": "Google Traveler", "google_sub": "google-123"}

    google_verifier = (
        "app.core.google_auth.verify_google_id_token" if model is MVPUser
        else "app.routers.auth.verify_google_id_token"
    )
    monkeypatch.setattr(google_verifier, verified_google_claims)
    google = post(client, "google", {"id_token": "mock-verified-token"})
    assert google.status_code == 200, google.text
    assert "access_token" in google.json()
    verify = post(
        client, "verify-email", {**challenge, "otp": traveler_otp_mailbox[EMAIL]}
    )
    assert verify.status_code == 409
    assert post(client, "login", DETAILS).status_code == 401
    with sessions() as db:
        assert db.query(model).count() == 1


def test_traveler_otp_smtp_message_and_safe_failure(monkeypatch, caplog):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    smtp = MagicMock()
    server = smtp.return_value.__enter__.return_value
    server.send_message.return_value = {}
    monkeypatch.setattr(smtplib, "SMTP", smtp)
    code = "123456"
    assert email_service.send_traveler_otp_email(EMAIL, code)
    message = server.send_message.call_args.args[0]
    assert message["To"] == EMAIL
    assert code in message.get_content()
    assert "5 minutes" in message.get_content()

    server.send_message.side_effect = smtplib.SMTPException(code)
    assert not email_service.send_traveler_otp_email(EMAIL, code)
    assert code not in caplog.text
