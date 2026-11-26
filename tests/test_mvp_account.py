from datetime import timedelta
from io import BytesIO

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects import mssql

from app.core.mvp_security import create_mvp_access_token, hash_password
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPUser
from app.routers.mvp.account import password_limit
from app.services.mvp_account import feed
from scripts.bootstrap_mvp_schema import UPGRADES
from tests.test_mvp_community import community_env
from tests.test_mvp_listing_management import managed
from tests.test_mvp_planner import catalog_data, interval

API = "/api/v1"


def test_profile_preferences_and_avatar(managed, tmp_path, monkeypatch):
    client, (provider, _, admin, traveler), image = managed
    monkeypatch.setenv("TRAVELER_AVATAR_DIR", str(tmp_path / "avatars"))
    path = API + "/auth/me"
    user = client.get(path, headers=traveler).json()
    assert user["email_verified"] is False and user["avatar_url"] is None
    assert "hashed_password" not in user
    for forbidden in (provider, admin):
        assert client.get(path, headers=forbidden).status_code == 200
        assert client.patch(path, headers=forbidden, json={"phone": None}).status_code == 403
    assert client.patch(path, json={"phone": None}).status_code == 401
    for payload in ({"role": "admin"}, {"email": "someone@example.com"},
                    {"phone": "bad"}, {"country": "123"}, {"full_name": "X"}):
        assert client.patch(path, headers=traveler, json=payload).status_code == 422
    edited = client.patch(path, headers=traveler, json={
        "full_name": "New Name", "phone": "+94 (77) 123-4567", "country": "Sri Lanka",
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["phone"] == "+94771234567"
    assert edited.json()["country"] == "Sri Lanka"
    assert client.patch(path, headers=traveler, json={"phone": None}).json()["phone"] is None

    prefs = API + "/traveler/preferences"
    assert client.get(prefs, headers=traveler).json()["interests"] == []
    response = client.put(prefs, headers=traveler, json={
        "preferred_destinations": ["Jaffna", " jaffna "], "interests": ["culture", "culture"],
        "budget_min": 2000, "budget_max": 10000,
    })
    assert response.status_code == 200, response.text
    assert response.json()["preferred_destinations"] == ["Jaffna"]
    assert client.get(prefs, headers=traveler).json() == response.json()
    assert client.put(prefs, headers=traveler, json={"budget_min": 2, "budget_max": 1}).status_code == 422
    assert client.put(prefs, headers=traveler, json={"interests": ["unknown"]}).status_code == 422

    avatar = path + "/avatar"
    assert client.post(avatar, files=image).status_code == 401
    upload = client.post(avatar, headers=traveler, files={
        "file": ("../../unsafe.exe", image["file"][1], "image/png"),
    })
    assert upload.status_code == 200, upload.text
    preview = upload.json()["avatar_url"]
    assert client.get(preview).status_code == 404
    result = client.get(preview, headers=traveler)
    assert result.status_code == 200 and result.headers["X-Content-Type-Options"] == "nosniff"
    assert Image.open(BytesIO(result.content)).format == "JPEG"
    for file, code in ((("x.svg", b"<svg/>", "image/svg+xml"), 415),
                       (("x.png", b"x" * (5 * 1024 * 1024 + 1), "image/png"), 413)):
        assert client.post(avatar, headers=traveler, files={"file": file}).status_code == code
        assert client.get(path, headers=traveler).json()["avatar_url"] == preview
    replacement = client.post(avatar, headers=traveler, files=image)
    assert replacement.json()["avatar_url"] != preview
    assert len(list((tmp_path / "avatars").iterdir())) == 1
    assert client.delete(avatar, headers=traveler).json()["avatar_url"] is None
    assert not list((tmp_path / "avatars").iterdir())


def test_password_change_security_and_reset_compatibility(managed):
    client, (_, _, _, traveler), _ = managed
    password_limit.reset()
    endpoint = API + "/auth/change-password"
    generator = client.app.dependency_overrides[get_mvp_db]()
    db = next(generator)
    user = db.scalar(select(MVPUser).where(MVPUser.role == "traveler"))
    email = user.email
    from app.services import mvp_account
    settings = mvp_account.settings(db, user.id, write=True)
    settings.google_only = True
    user.hashed_password = hash_password("Current123!")
    db.commit()
    good = {"current_password": "Current123!", "new_password": "New123456!",
            "confirm_password": "New123456!"}
    assert client.post(endpoint, headers=traveler, json=good).status_code == 400
    db.refresh(settings)
    settings.google_only = False
    db.commit()
    generator.close()
    password_limit.reset()
    assert client.post(endpoint, headers=traveler, json={**good, "current_password": "bad"}).status_code == 400
    assert client.post(endpoint, headers=traveler, json={**good, "confirm_password": "badtest"}).status_code == 422
    invalid = client.post(endpoint, headers=traveler, json={**good, "new_password": "x"})
    assert invalid.status_code == 422 and "Current123!" not in invalid.text
    unchanged = {key: good["current_password"] for key in good}
    assert client.post(endpoint, headers=traveler, json=unchanged).status_code == 400
    changed = client.post(endpoint, headers=traveler, json=good)
    assert changed.status_code == 200, changed.text
    assert changed.json()["login_required"] is True
    assert client.get(API + "/auth/me", headers=traveler).status_code == 401
    login = client.post(API + "/auth/login", json={"email": email, "password": good["new_password"]})
    assert login.status_code == 200, login.text
    new_headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.post(endpoint, headers=new_headers, json=good).status_code == 400
    assert client.post(endpoint, headers=new_headers, json=good).status_code == 429
    assert client.get(API + "/notifications", headers=new_headers).json()["items"][0][
        "notification_type"
    ] == "password_changed"
    password_limit.reset()


def test_ai_booking_payment_and_notification_state(catalog_data):
    client, traveler, other, ids, start = catalog_data
    confirmed = "/ai/planner/bookings/confirm"
    quote = client.post(API + "/ai/planner/booking-quotes", headers=traveler,
                        json=interval(ids["homestay"], start)).json()
    assert client.post(API + confirmed, headers=other, json={
        "confirmation_token": quote["confirmation_token"], "confirm": True,
    }).status_code == 403
    assert client.get(API + "/notifications", headers=traveler).json()["total"] == 0
    for _ in range(2):
        booked = client.post(API + confirmed, headers=traveler, json={
            "confirmation_token": quote["confirmation_token"], "confirm": True,
        })
        assert booked.status_code == 200, booked.text
    assert client.get(API + "/notifications", headers=traveler).json()["total"] == 1
    booking = booked.json()["id"]
    pay = {"booking_id": booking, "request_key": "demo-failure", "attempt_no": 1,
           "simulate_outcome": "failure"}
    for _ in range(2):
        assert client.post(API + "/payments/simulate", headers=traveler, json=pay).status_code == 200
    assert client.post(API + "/payments/at-location", headers=traveler, json={
        "booking_id": booking, "request_key": "location-payment",
    }).json()["status"] == "unpaid"
    page = client.get(API + "/notifications?page_size=1&category=payment&unread_only=true",
                      headers=traveler).json()
    assert page["total"] == 2 and page["unread_count"] == 3
    notification = API + f"/notifications/{page['items'][0]['id']}/read"
    assert client.patch(notification, headers=other).status_code == 404
    first = client.patch(notification, headers=traveler).json()
    assert first["is_read"] and client.patch(notification, headers=traveler).json() == first
    assert client.post(API + "/notifications/read-all", headers=traveler).json() == {
        "updated_count": 2, "unread_count": 0,
    }
    assert client.get(API + "/notifications/unread-count", headers=traveler).json()["unread_count"] == 0
    assert client.get(API + "/bookings/my", headers=traveler).json()[0]["status"] == "pending"
    generator = client.app.dependency_overrides[get_mvp_db]()
    db = next(generator)
    provider = db.scalar(select(MVPUser).where(MVPUser.role == "provider").order_by(MVPUser.id))
    host = {"Authorization": "Bearer " + create_mvp_access_token(
        provider.id, provider.email, provider.role,
    )}
    generator.close()
    for status in ("confirmed", "completed"):
        result = client.patch(API + f"/bookings/{booking}/status", headers=host,
                              json={"to_status": status})
        assert result.status_code == 200, result.text

    second_quote = client.post(API + "/ai/planner/booking-quotes", headers=traveler,
                               json=interval(ids["homestay"], start + timedelta(days=2))).json()
    next_booking = client.post(API + confirmed, headers=traveler, json={
        "confirmation_token": second_quote["confirmation_token"], "confirm": True,
    }).json()["id"]
    pay = {"booking_id": next_booking, "request_key": "demo-success", "attempt_no": 1,
           "simulate_outcome": "success"}
    assert client.post(API + "/payments/simulate", headers=traveler, json=pay).status_code == 200
    assert client.post(API + "/payments/simulate", headers=traveler, json=pay).status_code == 200
    result = client.patch(API + f"/bookings/{next_booking}/status", headers=traveler,
                          json={"to_status": "cancelled"})
    assert result.status_code == 200, result.text
    events = [r["notification_type"] for r in
              client.get(API + "/notifications", headers=traveler).json()["items"]]
    assert events.count("demo_succeeded") == 1
    assert {"booking_confirmed", "booking_completed", "booking_cancelled_by_traveler"} <= set(events)


def test_account_routes_openapi_migration_and_mssql_queries():
    app = create_mvp_app()
    expected = {
        "/auth/me": {"get", "patch"}, "/auth/me/avatar": {"post", "delete"},
        "/users/{user_id}/avatar": {"get"}, "/auth/change-password": {"post"},
        "/traveler/preferences": {"get", "put"}, "/notifications": {"get"},
        "/notifications/unread-count": {"get"}, "/notifications/{notification_id}/read": {"patch"},
        "/notifications/read-all": {"post"}, "/traveler/settings/notifications": {"get", "patch"},
    }
    for prefix in ("/api", "/api/v1"):
        for path, methods in expected.items():
            assert methods <= app.openapi()["paths"][prefix + path].keys()
    assert UPGRADES[-1].version == "1.0.7"
    assert UPGRADES[-1].batches()
    compiled = str(feed(1).compile(dialect=mssql.dialect()))
    assert "IS 1" not in compiled and "EXISTS" in compiled


def test_unapplied_schema_keeps_existing_profile_available(managed, monkeypatch):
    from app.services import mvp_account

    client, (_, _, _, traveler), _ = managed
    monkeypatch.setattr(mvp_account, "available", lambda db: False)
    assert client.get(API + "/auth/me", headers=traveler).status_code == 200
    assert client.get(API + "/traveler/preferences", headers=traveler).status_code == 503
    assert client.get(API + "/notifications", headers=traveler).status_code == 503
    assert client.patch(API + "/auth/me", headers=traveler, json={
        "country": "Sri Lanka",
    }).status_code == 503


def test_community_notification_privacy_and_preferences(community_env):
    client, tokens, _, _ = community_env
    owner = tokens["traveler1"]
    other = tokens["traveler2"]
    post = client.post(API + "/community/posts", headers=owner, json={
        "body": "A new trail near Jaffna",
    })
    assert post.status_code == 201, post.text
    path = API + f"/community/posts/{post.json()['id']}"
    notes = API + "/notifications"
    prefs = API + "/traveler/settings/notifications"
    assert client.get(prefs, headers=owner).json() == {
        "booking_updates": True, "payment_updates": True,
        "community_activity": True, "promotional_updates": False,
    }
    assert client.patch(prefs, headers=owner, json={"community_activity": "false"}).status_code == 422
    for headers in (owner, other):
        assert client.put(path + "/like", headers=headers).status_code == 200
    assert client.delete(path + "/like", headers=other).status_code == 200
    assert client.put(path + "/like", headers=other).status_code == 200
    assert client.get(notes, headers=owner).json()["total"] == 1
    assert client.get(notes, headers=other).json()["total"] == 0
    comment = client.post(path + "/comments", headers=other, json={"body": "What a great trail."})
    assert comment.status_code == 201, comment.text
    assert client.get(notes, headers=owner).json()["total"] == 2
    assert client.delete(API + f"/community/comments/{comment.json()['id']}",
                         headers=other).status_code == 200
    assert client.get(notes, headers=owner).json()["total"] == 1
    assert client.patch(prefs, headers=owner, json={"community_activity": False}).status_code == 200
    assert client.get(prefs, headers=other).json()["community_activity"] is True
    assert client.post(path + "/comments", headers=other, json={"body": "Another message."}).status_code == 201
    assert client.get(notes, headers=owner).json()["total"] == 1
    assert client.delete(path, headers=owner).status_code == 200
    assert client.get(notes, headers=owner).json()["total"] == 0

