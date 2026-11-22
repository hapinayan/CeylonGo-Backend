from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.core.audit import record_admin_audit_log
from app.core.security import ADMIN_AUTH_METHOD, create_access_token, decode_access_token
from app.database.connection import get_db
from app.main import app
from app.models.shared import AdminAuditLog
from app.models.user import Base, User
from app.routers import admin_travelers, artisan_images, auth, tour_guide_public
from tests.test_admin_rbac_matrix import _create_user_and_headers, client, db_session, role_headers


PATH = "/api/v1/admin/travelers"


def test_traveler_directory_and_history(client, db_session, role_headers):
    traveler = db_session.query(User).filter_by(role="traveler").one()
    admin = db_session.query(User).filter_by(role="super_admin").one()
    traveler.full_name = "Traveler _%["
    traveler.google_sub = "private-google-subject"
    record_admin_audit_log(
        db_session, admin_user_id=admin.id, action="traveler_reactivated",
        entity_name="traveler_account", entity_id=traveler.id,
        details={"previous_is_active": False, "is_active": True,
                 "reason_code": "issue_resolved", "note": "Resolved", "other_private_field": "hidden"},
    )
    record_admin_audit_log(db_session, admin_user_id=admin.id, action="unrelated",
                           entity_name="traveler_account", entity_id=traveler.id)
    db_session.commit()
    for role in ("admin", "super_admin", "operations_admin", "support_admin"):
        headers = role_headers[role]
        directory = client.get(PATH, headers=headers, params={"search": "_%[", "is_active": True, "page_size": 1})
        assert directory.status_code == 200
        assert directory.json()["total"] == 1
        assert client.get(PATH, headers=headers, params={"search": "%missing_"}).json()["total"] == 0
        assert client.get(PATH, headers=headers, params={"page_size": 101}).status_code == 422
        assert client.get(PATH, headers=headers, params={"page": 2, "page_size": 1}).json()["items"] == []
        assert client.get(PATH, headers=headers, params={"is_active": False}).json()["items"] == []
        detail = client.get(f"{PATH}/{traveler.id}", headers=headers).json()
        assert detail == directory.json()["items"][0]
        assert set(detail) == {"id", "full_name", "email", "is_active", "created_at"}
        assert detail["email"] == traveler.email
        history = client.get(f"{PATH}/{traveler.id}/history", headers=headers).json()
        assert len(history) == 1
        assert set(history[0]) == {"admin_user_id", "created_at", "previous_is_active", "is_active", "reason_code", "note"}
        for suffix in ("", "/history"):
            assert client.get(f"{PATH}/{admin.id}{suffix}", headers=headers).status_code == 404


@pytest.mark.parametrize("suffix", ["", "/1", "/1/history"])
def test_traveler_reads_reject_unauthorized_callers(client, db_session, role_headers, suffix):
    for role in ("finance_admin", "content_moderator", "traveler", "provider"):
        assert client.get(PATH + suffix, headers=role_headers[role]).status_code == 403
    _, inactive = _create_user_and_headers(db_session, email="inactive-reader@example.com",
                                           role="super_admin", is_active=False)
    assert client.get(PATH + suffix, headers=inactive).status_code == 403
    assert client.get(PATH + suffix).status_code == 401


def test_status_transitions_and_retries(client, db_session, role_headers):
    traveler = db_session.query(User).filter_by(role="traveler").one()
    traveler_id = traveler.id
    path = f"{PATH}/{traveler_id}/status"
    disable = {"is_active": False, "reason_code": "security_risk", "note": "  Reviewed  "}
    enable = {"is_active": True, "reason_code": "security_cleared", "note": "[Review] Resolved"}
    for role in ("operations_admin", "support_admin", "finance_admin", "content_moderator", "provider", "traveler"):
        assert client.patch(path, headers=role_headers[role], json=disable).status_code == 403
    assert client.patch(path, json=disable).status_code == 401
    for invalid in ({"reason_code": "security_cleared"}, {"note": "  "}, {"note": "x" * 501}, {"is_active": 0}):
        assert client.patch(path, headers=role_headers["admin"], json=disable | invalid).status_code == 422
    provider_id = db_session.query(User.id).filter_by(role="provider").scalar()
    assert client.patch(f"{PATH}/{provider_id}/status", headers=role_headers["admin"], json=disable).status_code == 404
    for role, payload, version in (("admin", disable, 1), ("super_admin", disable, 1), ("super_admin", enable, 2)):
        result = client.patch(path, headers=role_headers[role], json=payload)
        assert result.status_code == 200
        assert result.json()["is_active"] is payload["is_active"]
        db_session.expire_all()
        assert db_session.get(User, traveler_id).auth_version == version
    history = client.get(f"{PATH}/{traveler_id}/history", headers=role_headers["support_admin"]).json()
    assert [event["reason_code"] for event in history] == ["security_cleared", "security_risk"]
    assert history[0]["note"] == "[Review] Resolved"
    assert history[1]["note"] == "Reviewed"
    assert history[1]["previous_is_active"] is True
    assert history[1]["admin_user_id"] == db_session.query(User.id).filter_by(role="admin").scalar()


@pytest.mark.parametrize("note", ["password: do-not-leak-123", "Contact sensitive-person@example.com", ["hidden-secret"]])
def test_unsafe_status_notes_are_rejected_without_echo(client, db_session, role_headers, caplog, note):
    traveler = db_session.query(User).filter_by(role="traveler").one()
    response = client.patch(f"{PATH}/{traveler.id}/status", headers=role_headers["super_admin"],
                            json={"is_active": False, "reason_code": "security_risk", "note": note})
    assert response.status_code == 422
    for secret in ("do-not-leak-123", "sensitive-person@example.com", "hidden-secret"):
        assert secret not in response.text + caplog.text
    assert traveler.is_active and traveler.auth_version == 0
    assert db_session.query(AdminAuditLog).count() == 0


@pytest.mark.parametrize("details", [
    '{"note":"private-history-secret"',
    '{"previous_is_active":"true","is_active":false,"reason_code":"security_risk","note":"private-history-secret"}',
    '{"previous_is_active":false,"is_active":false,"reason_code":"security_risk","note":"private-history-secret"}',
    '{"previous_is_active":false,"is_active":true,"reason_code":"issue_resolved","note":"private-history-secret"}',
])
def test_history_fails_closed_for_invalid_events(client, db_session, role_headers, caplog, details):
    traveler_id = db_session.query(User.id).filter_by(role="traveler").scalar()
    admin_id = db_session.query(User.id).filter_by(role="super_admin").scalar()
    db_session.add(AdminAuditLog(admin_user_id=admin_id, action="traveler_deactivated",
                                 entity_name="traveler_account", entity_id=traveler_id, details=details))
    db_session.commit()
    response = client.get(f"{PATH}/{traveler_id}/history", headers=role_headers["support_admin"])
    assert response.status_code == 500
    assert response.json() == {"detail": "Traveler status history is unavailable"}
    assert "Invalid traveler status history event id=" in caplog.text
    assert "private-history-secret" not in response.text + caplog.text


@pytest.mark.parametrize("conflict", [False, True])
def test_status_failure_keeps_account_and_history_consistent(client, db_session, role_headers, monkeypatch, conflict):
    traveler_id = db_session.query(User.id).filter_by(role="traveler").scalar()
    if conflict:
        execute = db_session.execute

        def miss_update(statement, *args, **kwargs):
            if statement.is_update:
                # Force the conditional write to miss as it would after a competing transition.
                statement = statement.where(User.auth_version == -1)
            return execute(statement, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", miss_update)
    else:
        def fail_audit(*args, **kwargs):
            raise RuntimeError("audit write failed")

        monkeypatch.setattr(admin_travelers, "record_admin_audit_log", fail_audit)

    def transition():
        return client.patch(f"{PATH}/{traveler_id}/status", headers=role_headers["super_admin"],
                            json={"is_active": False, "reason_code": "security_risk", "note": "Reviewed"})

    if conflict:
        assert transition().status_code == 409
    else:
        with pytest.raises(RuntimeError, match="audit write failed"):
            transition()
    traveler = db_session.get(User, traveler_id)
    assert traveler.is_active and traveler.auth_version == 0
    assert db_session.query(AdminAuditLog).count() == 0


def test_traveler_jwt_revocation_and_new_logins(client, db_session, role_headers, monkeypatch):
    traveler = db_session.query(User).filter_by(role="traveler").one()
    traveler_id, email = traveler.id, traveler.email
    password_payload = {"email": email, "password": "ValidPass123!"}
    login_url = "/api/v1/auth/login"
    me = "/api/v1/auth/me"

    def header(token):
        return {"Authorization": f"Bearer {token}"}

    login = client.post(login_url, json=password_payload)
    assert login.status_code == 200
    old_token = login.json()["access_token"]
    assert decode_access_token(old_token)["auth_version"] == 0
    assert client.get(me, headers=header(old_token)).status_code == 200
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=old_token)
    assert artisan_images.get_optional_user(credentials, db_session).id == traveler_id
    assert tour_guide_public.get_optional_current_user(credentials, db_session).id == traveler_id
    assert artisan_images.get_optional_user(None, db_session) is None
    assert tour_guide_public.get_optional_current_user(None, db_session) is None

    path = f"{PATH}/{traveler_id}/status"
    admin = role_headers["super_admin"]
    for active, reason, expected in ((False, "security_risk", 403), (True, "security_cleared", 401)):
        assert client.patch(path, headers=admin, json={"is_active": active, "reason_code": reason,
                                                      "note": "Reviewed"}).status_code == 200
        db_session.expire_all()
        assert client.get(me, headers=header(old_token)).status_code == expected
        assert artisan_images.get_optional_user(credentials, db_session) is None
        with pytest.raises(HTTPException) as rejected:
            tour_guide_public.get_optional_current_user(credentials, db_session)
        assert rejected.value.status_code == expected
        if not active:
            assert client.post(login_url, json=password_payload).status_code == 403

    for claim in ({}, {"auth_version": 1}, {"auth_version": "2"}, {"auth_version": True}):
        token = create_access_token({"sub": str(traveler_id), **claim})
        assert client.get(me, headers=header(token)).status_code == 401

    monkeypatch.setattr(auth, "verify_google_id_token", lambda _: {
        "google_sub": "stage-5-google-subject", "email": email, "full_name": "Traveler",
    })
    for url, payload in ((login_url, password_payload), ("/api/v1/auth/google", {"id_token": "mocked"})):
        fresh_login = client.post(url, json=payload)
        assert fresh_login.status_code == 200, fresh_login.text
        token = fresh_login.json()["access_token"]
        assert decode_access_token(token)["auth_version"] == 2
        assert client.get(me, headers=header(token)).status_code == 200
        assert "auth_version" not in fresh_login.json()["user"]

    for role, expected in (("super_admin", 401), ("provider", 200)):
        user_id = db_session.query(User.id).filter_by(role=role).scalar()
        legacy_token = create_access_token({"sub": str(user_id)})
        assert client.get(me, headers=header(legacy_token)).status_code == expected


def test_concurrent_status_updates_write_one_transition(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'travelers.sqlite').as_posix()}")

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        connection.create_function("sysutcdatetime", 0, lambda: "2026-01-01 00:00:00")

    barrier = Barrier(2, timeout=10)

    @event.listens_for(engine, "before_cursor_execute")
    def align_updates(_connection, _cursor, statement, _params, _context, _many):
        if statement.startswith("UPDATE users SET"):
            barrier.wait()

    sessions = sessionmaker(bind=engine)

    def get_test_db():
        with sessions() as session:
            yield session

    try:
        Base.metadata.create_all(engine)
        with sessions() as session:
            admin = User(full_name="Admin", email="concurrent-admin@example.com", role="super_admin")
            traveler = User(full_name="Traveler", email="concurrent-traveler@example.com", role="traveler")
            session.add_all([admin, traveler])
            session.commit()
            traveler_id = traveler.id
            token = create_access_token({"sub": str(admin.id), "auth_method": ADMIN_AUTH_METHOD})

        monkeypatch.setitem(app.dependency_overrides, get_db, get_test_db)

        def deactivate():
            with TestClient(app) as test_client:
                return test_client.patch(
                    f"{PATH}/{traveler_id}/status", headers={"Authorization": f"Bearer {token}"},
                    json={"is_active": False, "reason_code": "security_risk", "note": "Reviewed"},
                ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(lambda _: deactivate(), range(2))) == [200, 409]
        with sessions() as session:
            traveler = session.get(User, traveler_id)
            assert not traveler.is_active and traveler.auth_version == 1
            assert session.query(AdminAuditLog).count() == 1
    finally:
        engine.dispose()
