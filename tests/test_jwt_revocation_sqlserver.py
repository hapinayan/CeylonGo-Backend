"""Opt-in JWT integration tests. The dedicated runner owns database setup."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.security import (
    decode_access_token,
    get_jwt_secret_key,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.database.connection import get_db
from app.models.password_reset import PasswordReset
from app.models.provider_profile import ProviderProfile
from app.models.user import User
from app.routers import artisan_images, auth, provider_auth, tour_guide_public
from scripts.run_jwt_sqlserver_tests import verified_connection

RUN_MARKER = None


@pytest.fixture(scope="module")
def sessions():
    if RUN_MARKER is None:
        pytest.skip("Use the dedicated JWT SQL Server runner.")
    engine = create_engine(
        "mssql+pyodbc://", creator=lambda: verified_connection(RUN_MARKER),
        poolclass=NullPool, hide_parameters=True,
    )
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture
def client(sessions, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", secrets.token_urlsafe(48))
    monkeypatch.setattr(auth, "send_password_reset_email", lambda **_: True)
    from app.core.rate_limiter import reset_all_limiters
    reset_all_limiters()
    app = FastAPI()
    for router in (auth, provider_auth, artisan_images, tour_guide_public):
        app.include_router(router.router)

    def db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = db
    with TestClient(app) as test_client:
        yield test_client


def make_user(sessions, role="traveler"):
    suffix = uuid4().hex
    email = f"jwt-{suffix}@example.com"
    password = secrets.token_urlsafe(20)
    token = secrets.token_urlsafe(32)
    with sessions.begin() as db:
        user = User(
            email=email, full_name="Synthetic JWT user", role=role,
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
        if role == "provider":
            db.add(ProviderProfile(
                user_id=user.id, business_name="JWT test", provider_type="homestay",
                district="Kandy", phone="0770000000", approval_status="approved",
                nic_number=suffix[:12], selfie_image_path="test",
                nic_front_image_path="test", nic_back_image_path="test",
            ))
        db.add(PasswordReset(
            user_id=user.id, token_hash=hash_reset_token(token),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
        ))
        user_id = user.id
    return user_id, email, password, token


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def login(client, email, password, role="traveler"):
    prefix = "providers" if role == "provider" else "auth"
    return client.post(f"/api/v1/{prefix}/login", json={"email": email, "password": password})


def redeem(client, token, password):
    return client.post("/api/v1/auth/reset-password", json={
        "token": token, "new_password": password,
    })


def state(sessions, user_id, token):
    with sessions() as db:
        user = db.get(User, user_id)
        record = db.scalar(select(PasswordReset).where(
            PasswordReset.token_hash == hash_reset_token(token)
        ))
        return user.credential_version, user.password_hash, record.is_used, record.used_at


def test_migration_schema_and_defaults(sessions):
    with sessions() as db:
        row = db.execute(text("""
            SELECT TYPE_NAME(c.user_type_id), c.is_nullable, d.name, d.definition,
                   k.name, k.is_disabled, k.is_not_trusted
            FROM sys.columns c
            JOIN sys.default_constraints d ON d.object_id = c.default_object_id
            JOIN sys.check_constraints k ON k.parent_object_id = c.object_id
            WHERE c.object_id = OBJECT_ID('dbo.users') AND c.name = 'credential_version'
              AND k.name = 'CK_users_credential_version_nonnegative'
        """)).one()
        assert tuple(row[:3]) == ("bigint", False, "DF_users_credential_version")
        assert row[3].replace("(", "").replace(")", "").strip() == "0"
        assert not row[5] and not row[6]
        assert db.execute(text(
            "SELECT credential_version FROM dbo.users WHERE email = 'jwt-pre040@example.invalid'"
        )).scalar_one() == 0
        db.execute(text(
            "INSERT INTO dbo.users (full_name, email) VALUES ('JWT default probe', :email)"
        ), {"email": "jwt-default-probe@example.invalid"})
        assert db.execute(text(
            "SELECT credential_version FROM dbo.users WHERE email = 'jwt-default-probe@example.invalid'"
        )).scalar_one() == 0
    for invalid in (-1, None):
        with sessions() as db, pytest.raises(IntegrityError):
            db.execute(text(
                "INSERT INTO dbo.users (full_name, email, credential_version) "
                "VALUES ('JWT constraint probe', :email, :value)"
            ), {"email": "jwt-constraint-probe@example.invalid", "value": invalid})


@pytest.mark.parametrize("role", ["traveler", "provider", "admin"])
def test_reset_revokes_old_session(sessions, client, role):
    user_id, email, password, token = make_user(sessions, role)
    path = "/api/v1/providers/me" if role == "provider" else "/api/v1/auth/me"
    original = login(client, email, password, role)
    assert original.status_code == 200
    old_jwt = original.json()["access_token"]
    assert decode_access_token(old_jwt)["credential_version"] == 0
    assert client.get(path, headers=headers(old_jwt)).status_code == 200
    replacement = secrets.token_urlsafe(20)
    assert redeem(client, token, replacement).status_code == 200
    assert state(sessions, user_id, token)[0] == 1
    assert client.get(path, headers=headers(old_jwt)).status_code == 401
    assert login(client, email, password, role).status_code == 401
    fresh = login(client, email, replacement, role)
    assert fresh.status_code == 200
    fresh_jwt = fresh.json()["access_token"]
    assert decode_access_token(fresh_jwt)["credential_version"] == 1
    assert client.get(path, headers=headers(fresh_jwt)).status_code == 200


def test_google_legacy_and_optional_auth(sessions, client, monkeypatch):
    user_id, email, _, token = make_user(sessions)
    expiry = datetime.now(UTC) + timedelta(minutes=5)
    payload = {"sub": str(user_id), "exp": expiry}

    def encode(claims):
        return jwt.encode(claims, get_jwt_secret_key(), algorithm="HS256")

    path = "/api/v1/auth/me"
    legacy = encode(payload)
    assert client.get(path, headers=headers(legacy)).status_code == 401
    monkeypatch.setenv("JWT_LEGACY_CREDENTIAL_VERSION_UNTIL", str(int(expiry.timestamp())))
    assert client.get(path, headers=headers(legacy)).status_code == 200
    for bad in (None, True, "0", -1, 1):
        malformed = encode(dict(payload, credential_version=bad))
        assert client.get(path, headers=headers(malformed)).status_code == 401

    assert redeem(client, token, secrets.token_urlsafe(20)).status_code == 200
    assert client.get(path, headers=headers(legacy)).status_code == 401
    monkeypatch.setattr(auth, "verify_google_id_token", lambda _: {
        "google_sub": uuid4().hex, "email": email, "full_name": "Synthetic JWT user",
    })
    response = client.post("/api/v1/auth/google", json={"id_token": "mock-google-assertion"})
    assert response.status_code == 200
    fresh = response.json()["access_token"]
    assert decode_access_token(fresh)["credential_version"] == 1
    assert client.get(path, headers=headers(fresh)).status_code == 200
    stale = encode(dict(payload, credential_version=0))
    guide = "/api/v1/tour-guides/1/contact"
    assert client.get(guide).status_code == 404
    assert client.get(guide, headers=headers(stale)).status_code == 401
    assert client.get(guide, headers=headers(fresh)).status_code == 404
    gallery = "/api/v1/artisan-listings/1/images"
    assert client.get(gallery).status_code == 404
    assert client.get(gallery, headers=headers(stale)).status_code == 404


def test_two_connections_claim_once(sessions, client, monkeypatch):
    user_id, _, _, token = make_user(sessions)
    barrier = Barrier(2, timeout=10)

    def synchronize(password):
        result = hash_password(password)
        barrier.wait()
        return result

    monkeypatch.setattr(auth, "hash_password", synchronize)
    passwords = [secrets.token_urlsafe(20), secrets.token_urlsafe(20)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        calls = [workers.submit(redeem, client, token, password) for password in passwords]
        statuses = [call.result(timeout=35).status_code for call in calls]
    assert sorted(statuses) == [200, 400]
    version, password_hash, used, used_at = state(sessions, user_id, token)
    assert version == 1 and used and used_at is not None
    assert verify_password(passwords[statuses.index(200)], password_hash)


def test_login_reset_race(sessions, client, monkeypatch):
    _, email, password, token = make_user(sessions)
    read, release = Event(), Event()

    def pause(old_password, password_hash):
        read.set()
        if not release.wait(timeout=15):
            raise RuntimeError("Login/reset synchronization timed out.")
        return verify_password(old_password, password_hash)

    monkeypatch.setattr(auth, "verify_password", pause)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(login, client, email, password)
        try:
            assert read.wait(timeout=10)
            assert redeem(client, token, secrets.token_urlsafe(20)).status_code == 200
        finally:
            release.set()
        response = future.result(timeout=20)
    assert response.status_code == 200
    stale = response.json()["access_token"]
    assert decode_access_token(stale)["credential_version"] == 0
    assert client.get("/api/v1/auth/me", headers=headers(stale)).status_code == 401


def test_reset_application_rollback(sessions, client, monkeypatch):
    user_id, _, _, token = make_user(sessions)
    sibling = secrets.token_urlsafe(32)
    with sessions.begin() as db:
        db.add(PasswordReset(
            user_id=user_id, token_hash=hash_reset_token(sibling),
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
        ))
    before = state(sessions, user_id, token)
    sibling_before = state(sessions, user_id, sibling)

    def fail_commit(_session):
        raise SQLAlchemyError("Injected JWT rehearsal failure.")

    with monkeypatch.context() as injected:
        injected.setattr(sessions.class_, "commit", fail_commit)
        assert redeem(client, token, secrets.token_urlsafe(20)).status_code == 500
    assert state(sessions, user_id, token) == before
    assert state(sessions, user_id, sibling) == sibling_before
