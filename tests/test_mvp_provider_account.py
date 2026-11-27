from datetime import datetime, timedelta
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select

from app.core.security import hash_password
from app.database.mvp_session import get_mvp_db
from app.models.mvp import MVPBooking, MVPListing, MVPProviderProfile, MVPUser
from app.routers.mvp.account import avatar_bytes
from app.services import mvp_account
from app.services.mvp_services import MVPAuthService
from app.schemas.mvp import PasswordResetRequest

pytest_plugins = ("tests.test_mvp_listing_management",)

API = "/api/v1"
ME = API + "/providers/me"
SETTINGS = API + "/provider/settings"
DASH = API + "/provider/dashboard"
CURRENT = "OriginalPass123!"
NEW = "ProviderAfter123!"


def test_profile_ownership_and_admin_review(managed):
    client, (owner, other, admin, traveler), _ = managed
    response = client.get(ME, headers=owner)
    assert response.status_code == 200, response.text
    original = response.json()
    assert client.get(ME, headers=traveler).status_code == 403
    assert client.get(ME).status_code == 401
    for protected in (
        "email",
        "role",
        "approval_status",
        "verification_status",
        "user_id",
    ):
        assert (
            client.patch(ME, headers=owner, json={protected: "fake"}).status_code == 422
        )
    assert (
        client.patch(ME, headers=owner, json={"business_name": None}).status_code == 422
    )
    assert (
        client.patch(
            ME, headers=owner, json={"website_url": "javascript:alert(1)"}
        ).status_code
        == 422
    )
    patch = {
        "full_name": "Updated Provider",
        "business_name": "Updated Tours",
        "business_category": "tour_guide",
        "business_description": "Guided walks",
        "business_phone": "+94 77 123 4570",
        "personal_address": "Local road",
        "business_district": "Kandy",
        "website_url": "https://example.org",
    }
    response = client.patch(ME, headers=owner, json=patch)
    assert response.status_code == 200, response.text
    result = response.json()
    change = result["profile_change"]
    assert result["full_name"] == original["full_name"]
    assert result["business_description"] == "Guided walks"
    assert result["business_phone"] == "0771234570"
    assert result["business_name"] == original["business_name"]
    assert result["approval_status"] == "approved"
    assert change["status"] == "pending"
    assert (
        client.patch(ME, headers=owner, json={"full_name": "Another name"}).status_code
        == 409
    )
    assert client.get(ME, headers=other).json()["profile_change"] is None
    queue = client.get(API + "/admin/providers", headers=admin).json()
    assert any(
        row["id"] == result["id"] and row["profile_change_pending"] for row in queue
    )
    admin_url = API + f"/admin/providers/{result['id']}"
    assert client.get(admin_url + "/application", headers=owner).status_code == 403
    assert (
        client.get(admin_url + "/application", headers=admin).json()["profile_change"]
        == change
    )
    decision = {
        "verification_status": "rejected",
        "profile_change_request_id": change["request_id"],
        "decision_reason": "Supply a matching business name",
    }
    assert (
        client.post(admin_url + "/decision", headers=admin, json=decision).status_code
        == 200
    )
    rejected = client.get(ME, headers=owner).json()
    assert rejected["verification_status"] == "approved"
    assert rejected["profile_change"]["decision_reason"] == decision["decision_reason"]
    response = client.patch(ME, headers=owner, json=patch)
    decision["verification_status"] = "approved"
    assert (
        client.post(admin_url + "/decision", headers=admin, json=decision).status_code
        == 409
    )
    decision["profile_change_request_id"] = response.json()["profile_change"][
        "request_id"
    ]
    assert (
        client.post(admin_url + "/decision", headers=admin, json=decision).status_code
        == 200
    )
    approved = client.get(ME, headers=owner).json()
    for field in (
        "full_name",
        "business_name",
        "business_category",
        "personal_address",
        "business_district",
    ):
        assert approved[field] == patch[field]
    assert approved["profile_change"]["status"] == "approved"


def test_provider_photo_validation_replacement_and_removal(
    managed, tmp_path, monkeypatch
):
    client, (owner, other, _, traveler), _ = managed
    avatar = ME + "/avatar"
    storage = tmp_path / "avatars"
    monkeypatch.setenv("TRAVELER_AVATAR_DIR", str(storage))
    assert client.get(avatar, headers=owner).status_code == 404
    assert (
        client.post(
            avatar, headers=owner, files={"file": ("file.jpg", b"bad", "image/jpeg")}
        ).status_code
        == 415
    )
    with pytest.raises(HTTPException) as oversized:
        avatar_bytes(UploadFile(BytesIO(b"x" * (5 * 1024 * 1024 + 1))))
    assert oversized.value.status_code == 413
    for format in ("PNG", "WEBP", "JPEG"):
        image = BytesIO()
        Image.new("RGB", (8, 8)).save(image, format=format)
        uploaded = client.post(
            avatar,
            headers=owner,
            files={
                "file": (
                    "../../ignored.txt",
                    image.getvalue(),
                    "application/octet-stream",
                ),
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        assert len(list(storage.iterdir())) == 1
        url = uploaded.json()["avatar_url"]
        assert client.get(url, headers=owner).headers["content-type"] == "image/jpeg"
        assert client.get(url, headers=other).status_code == 404
        assert client.get(url, headers=traveler).status_code == 403
    assert client.delete(avatar, headers=owner).json()["avatar_url"] is None
    assert not list(storage.iterdir())


def test_provider_settings_dashboard_and_suspension(managed):
    client, (owner, other, admin, _), _ = managed
    profile = client.get(ME, headers=owner).json()
    assert client.get(SETTINGS, headers=owner).json() == {
        "dashboard_booking_limit": 5,
        "provider_notifications_available": False,
    }
    assert (
        client.patch(
            SETTINGS, headers=owner, json={"booking_updates": False}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            SETTINGS, headers=owner, json={"dashboard_booking_limit": 1}
        ).status_code
        == 200
    )
    assert client.get(SETTINGS, headers=other).json()["dashboard_booking_limit"] == 5
    dependency = client.app.dependency_overrides[get_mvp_db]()
    db = next(dependency)
    try:
        traveler = db.scalar(select(MVPUser).where(MVPUser.role == "traveler"))
        host_ids = [profile["id"], client.get(ME, headers=other).json()["id"]]
        for index, (status, active, host) in enumerate(
            (
                ("approved", True, 0),
                ("pending", False, 0),
                ("rejected", False, 0),
                ("approved", True, 1),
            )
        ):
            listing = MVPListing(
                provider_id=host_ids[host],
                category="bike",
                title=f"Rental {index}",
                description="Rental",
                district="Badulla",
                locality="Ella",
                price=100,
                approval_status=status,
                is_active=active,
            )
            db.add(listing)
            db.flush()
            if status == "approved":
                for state in (
                    "pending",
                    "confirmed",
                    "completed",
                    "cancelled",
                    "rejected",
                ):
                    db.add(
                        MVPBooking(
                            listing_id=listing.id,
                            traveler_id=traveler.id,
                            quantity=1,
                            starts_at=datetime.now() + timedelta(days=2),
                            ends_at=datetime.now() + timedelta(days=3),
                            total_amount=100,
                            status=state,
                            request_key=str(uuid4()),
                            request_hash=b"x" * 32,
                        )
                    )
        db.commit()
    finally:
        dependency.close()
    result = client.get(DASH, headers=owner)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["total_services"] == 3 and data["published_services"] == 1
    assert data["pending_verification_services"] == data["rejected_services"] == 1
    assert data["total_bookings"] == 5
    assert all(
        data[f"{state}_bookings"] == 1
        for state in ("pending", "confirmed", "completed", "cancelled")
    )
    assert len(data["recent_bookings"]) == len(data["upcoming_bookings"]) == 1
    assert data["upcoming_bookings"][0]["status"] == "confirmed"
    assert data["free_bookings_remaining"] == 1
    assert (
        client.post(
            API + "/provider/wallet/top-up",
            headers=owner,
            json={
                "request_key": str(uuid4()),
                "simulate_outcome": "success",
                "amount": "100.00",
            },
        ).status_code
        == 200
    )
    assert client.get(DASH, headers=owner).json()["wallet_balance"] == "100.00"
    change = client.post(
        API + f"/admin/providers/{profile['id']}/decision",
        headers=admin,
        json={"verification_status": "suspended"},
    )
    assert change.status_code == 200
    assert client.get(ME, headers=owner).status_code == 403
    assert (
        client.patch(
            ME, headers=owner, json={"business_description": "Denied"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            SETTINGS, headers=owner, json={"dashboard_booking_limit": 2}
        ).status_code
        == 403
    )
    assert client.get(DASH, headers=owner).status_code == 403
    assert client.get(API + "/provider/wallet", headers=owner).status_code == 403

    dependency = client.app.dependency_overrides[get_mvp_db]()
    db = next(dependency)
    try:
        pending = db.get(MVPProviderProfile, host_ids[1])
        pending.verification_status = "pending"
        db.commit()
    finally:
        dependency.close()
    assert client.get(ME, headers=other).status_code == 403
    assert client.post(ME + "/avatar", headers=other).status_code == 403


def test_provider_password_change_reset_and_logout(managed):
    client, (owner, _, _, _), _ = managed
    identity = client.get(ME, headers=owner).json()
    dependency = client.app.dependency_overrides[get_mvp_db]()
    db = next(dependency)
    try:
        user = db.get(MVPUser, identity["user_id"])
        user.hashed_password = hash_password(CURRENT)
        user.updated_at = mvp_account.now()
        db.commit()
        reset = MVPAuthService.request_password_reset(
            db, PasswordResetRequest(email=identity["email"]), send_email=False
        )
    finally:
        dependency.close()
    endpoint = API + "/providers/change-password"
    payload = {
        "current_password": "wrong",
        "new_password": NEW,
        "confirm_password": NEW,
    }
    assert client.post(endpoint, headers=owner, json=payload).status_code == 400
    payload["current_password"] = CURRENT
    response = client.post(endpoint, headers=owner, json=payload)
    assert response.status_code == 200, response.text
    assert client.get(ME, headers=owner).status_code == 401
    assert (
        client.post(
            API + "/auth/password-reset/confirm",
            json={"token": reset, "new_password": NEW},
        ).status_code
        == 400
    )
    response = client.post(
        API + "/providers/login", json={"email": identity["email"], "password": NEW}
    )
    assert response.status_code == 200, response.text
    auth = {"Authorization": "Bearer " + response.json()["access_token"]}
    assert client.post(API + "/providers/logout", headers=auth).status_code == 200
    assert client.get(ME, headers=auth).status_code == 401

    dependency = client.app.dependency_overrides[get_mvp_db]()
    db = next(dependency)
    try:
        reset = MVPAuthService.request_password_reset(
            db,
            PasswordResetRequest(email=identity["email"]),
            send_email=False,
        )
    finally:
        dependency.close()
    recovery = API + "/auth/password-reset/confirm"
    assert (
        client.post(
            recovery,
            json={
                "token": reset,
                "new_password": "shortpass",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            recovery,
            json={
                "token": reset,
                "new_password": CURRENT,
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            API + "/providers/login",
            json={
                "email": identity["email"],
                "password": CURRENT,
            },
        ).status_code
        == 200
    )
