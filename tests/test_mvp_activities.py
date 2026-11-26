from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPProviderProfile, MVPUser
from app.services.mvp_homestay import local_date

@pytest.fixture
def activity(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'activity.sqlite'}")
    MVPBase.metadata.create_all(engine)
    monkeypatch.setenv("PROVIDER_UPLOAD_DIR", str(tmp_path))
    app = create_mvp_app()

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_mvp_db] = database
    with Session(engine) as session:
        roles = ("provider", "provider", "admin", "traveler", "traveler")
        users = [
            MVPUser(email=f"activity{i}@example.com", full_name=f"Account {i}",
                    hashed_password="unused", role=role)
            for i, role in enumerate(roles)
        ]
        session.add_all(users)
        session.add_all([
            MVPProviderProfile(user=user, business_name="Ella Trails", phone="0771234567",
                               district="Badulla", verification_status="approved")
            for user in users[:2]
        ])
        session.commit()
        headers = [
            {"Authorization": "Bearer " + create_mvp_access_token(user.id, user.email, user.role)}
            for user in users
        ]
    image = BytesIO()
    Image.new("RGB", (8, 8)).save(image, "PNG")
    files = {"file": ("trail.png", image.getvalue(), "image/png")}
    with TestClient(app, base_url="http://testserver/api/v1/") as client:
        yield client, headers, files
    engine.dispose()


def setup_listing(activity):
    client, (provider, other, admin, traveler, stranger), photo = activity
    result = client.post("provider/listings", headers=provider, json={"category": "adventure"})
    assert result.status_code == 201, result.text
    listing_id = result.json()["listing"]["id"]
    manage = f"provider/listings/{listing_id}"
    review = f"admin/listings/{listing_id}"
    public = f"activities/{listing_id}"
    config = {
        "category": "hiking-trekking", "meeting_point": "Ella main entrance",
        "duration_minutes": 120, "min_participants": 1, "max_participants": 2,
        "safety_instructions": "Wear trail shoes", "minimum_age": 12,
    }
    result = client.patch(manage, headers=provider, json={
        "title": "Ella Ridge Hike", "description": "A guided hike along the ridge.",
        "district": "Badulla", "locality": "Ella", "latitude": "6.8667",
        "longitude": "81.0466", "price": "2500.00", "activity": config,
    })
    assert result.status_code == 200, result.text
    unit = result.json()["listing"]["units"][0]["id"]
    assert client.patch(manage, headers=other, json={"price": 10}).status_code == 403
    assert client.get(public).status_code == 404
    result = client.post(manage + "/photos", headers=provider, files=photo)
    assert result.status_code == 201, result.text
    evidence_path = manage + "/evidence/files"
    assert client.post(evidence_path, headers=provider, files=photo,
                       data={"kind": "customer_feedback"}).status_code == 422
    evidence = client.post(evidence_path, headers=provider, files=photo,
                           data={"kind": "customer_feedback", "redacted": "true"})
    assert evidence.status_code == 201, evidence.text
    evidence_id = evidence.json()["id"]
    assert client.get(f"{evidence_path}/{evidence_id}", headers=stranger).status_code == 403
    assert client.get(f"{review}/evidence/files/{evidence_id}", headers=admin).status_code == 200
    assert client.put(manage + "/evidence", headers=provider,
                      json={"notes": "Safety evidence explanation"}).status_code == 200
    assert client.post(manage + "/submit", headers=provider).status_code == 200
    queue = client.get("admin/listings", headers=admin, params={"category": "adventure"})
    assert [row["listing"]["id"] for row in queue.json()] == [listing_id]
    assert client.post(review + "/decision", headers=provider,
                       json={"approval_status": "approved"}).status_code == 403
    assert client.post(review + "/decision", headers=admin, json={
        "approval_status": "more_evidence_required", "decision_reason": "Add trail photos",
    }).status_code == 200
    assert client.post(manage + "/photos", headers=provider, files=photo).status_code == 201
    assert client.post(manage + "/submit", headers=provider).status_code == 200
    assert client.post(review + "/decision", headers=admin,
                       json={"approval_status": "approved"}).status_code == 200
    assert client.get(public).status_code == 404
    assert client.post(manage + "/publish", headers=provider).status_code == 200
    assert len(client.get(review + "/history", headers=admin).json()) == 2
    start = (datetime.now(UTC) + timedelta(days=5)).replace(
        hour=4, minute=0, second=0, microsecond=0,
    )
    slot_data = {
        "unit_id": unit, "capacity": 2, "starts_at": start.isoformat(),
        "ends_at": (start + timedelta(hours=2)).isoformat(),
    }
    slot = client.post(manage + "/availability", headers=provider, json=slot_data)
    assert slot.status_code == 201, slot.text
    payload = {
        "listing_id": listing_id, "slot_id": slot.json()["id"],
        "starts_at": slot_data["starts_at"], "ends_at": slot_data["ends_at"],
        "quantity": 2, "expected_total": "5000.00", "request_key": str(uuid4()),
    }
    return manage, public, config, slot_data, payload, local_date(start)


def test_activity_discovery_wishlist_and_provider_lifecycle(activity):
    client, (provider, _, admin, traveler, stranger), _ = activity
    manage, public, config, slot_data, booking, day = setup_listing(activity)
    listing_id = booking["listing_id"]
    detail = client.get(public).json()
    assert detail["activity"]["minimum_age"] == 12
    assert detail["rating"] is None and detail["reviews"] == []
    assert "files" not in detail and "notes" not in detail
    assert client.get("http://testserver" + detail["photos"][0]["image_url"]).status_code == 200
    parameters = {"destination": "ell", "category": "hiking-trekking", "date": day,
                  "participants": 2, "min_price": 2000, "max_price": 2500,
                  "sort": "price_asc", "limit": 1}
    found = client.get("activities", params=parameters)
    assert found.status_code == 200, found.text
    assert [row["id"] for row in found.json()["items"]] == [listing_id]
    assert client.get("activities", params={"date": day}).json()["items"]
    assert not client.get("activities", params={**parameters, "participants": 3}).json()["items"]
    assert not client.get("activities", params={**parameters, "offset": 1}).json()["items"]
    slot = client.get(public + "/availability", params={"date": day, "participants": 2}).json()[0]
    assert slot["remaining_capacity"] == 2 and slot["total_amount"] == "5000.00"
    assert client.get(public + "/availability", params={"participants": 3}).status_code == 422
    slot_path = f"{manage}/availability/{slot['id']}"
    assert client.put(slot_path, headers=provider, json={**slot_data, "is_open": False}).status_code == 200
    assert not client.get(public + "/availability").json()[0]["available"]
    assert not client.get("activities", params=parameters).json()["items"]
    assert client.post("bookings", headers=traveler, json=booking).status_code == 409
    assert client.put(slot_path, headers=provider, json=slot_data).status_code == 200
    assert client.post(manage + "/availability", headers=provider, json=slot_data).status_code == 409
    assert client.get("activities/wishlist").status_code == 401
    assert client.put(public + "/wishlist", headers=traveler).json()["saved"]
    assert client.get("activities/wishlist", headers=stranger).json()["items"] == []
    assert client.delete(public + "/wishlist", headers=stranger).status_code == 200
    assert client.delete(f"homestays/{listing_id}/wishlist", headers=traveler).status_code == 204
    assert client.get("activities/wishlist", headers=traveler).json()["items"][0]["id"] == listing_id
    assert client.delete(public + "/wishlist", headers=traveler).json()["saved"] is False
    result = client.patch(manage, headers=provider,
                          json={"price": 2700, "activity": {**config, "max_participants": 3}})
    assert result.json()["verification_status"] == "approved"
    assert client.get(public).status_code == 200
    assert client.post(manage + "/unpublish", headers=provider).status_code == 200
    assert client.get(public).status_code == 404
    assert client.post(manage + "/publish", headers=provider).status_code == 200
    assert client.post(f"admin/listings/{listing_id}/decision", headers=admin, json={
        "approval_status": "suspended", "decision_reason": "Safety reassessment",
    }).status_code == 200
    assert client.get(public).status_code == 404
    assert client.post(manage + "/publish", headers=provider).status_code == 409


def test_activity_booking_payment_and_cancellation(activity):
    client, (provider, other, _, traveler, stranger), _ = activity
    manage, public, _, slot_data, payload, _ = setup_listing(activity)
    assert client.post("bookings", json=payload).status_code == 401
    assert client.post("bookings", headers=traveler,
                       json={**payload, "expected_total": "1.00"}).status_code == 409
    assert client.post("bookings", headers=traveler,
                       json={**payload, "slot_id": None}).status_code == 422
    result = client.post("bookings", headers=traveler, json=payload)
    assert result.status_code == 201, result.text
    booking = result.json()
    booking_id = booking["id"]
    path = f"bookings/{booking_id}"
    assert booking["status"] == "pending" and booking["booking_reference"] == f"ACT-{booking_id}"
    assert client.post("bookings", headers=traveler, json=payload).json()["id"] == booking_id
    assert client.post("bookings", headers=traveler,
                       json={**payload, "request_key": str(uuid4())}).status_code == 409
    assert client.get(path, headers=stranger).status_code == 403
    assert client.get("bookings/my?category=adventure", headers=traveler).json()[0]["id"] == booking_id
    assert client.get("provider/bookings?category=adventure",
                      headers=provider).json()[0]["slot_id"] == payload["slot_id"]
    assert client.get(f"provider/bookings/{booking_id}", headers=other).status_code == 404
    provider_detail = client.get(f"provider/bookings/{booking_id}", headers=provider).json()
    assert provider_detail["traveler_contact"]["email"] == "activity3@example.com"
    assert "traveler_contact" not in client.get(path, headers=traveler).json()
    assert client.put(f"{manage}/availability/{payload['slot_id']}", headers=provider,
                      json={**slot_data, "is_open": False}).status_code == 409
    confirm = {"to_status": "confirmed"}
    assert client.patch(path + "/status", headers=provider, json=confirm).status_code == 409
    payment = {"booking_id": booking_id, "request_key": str(uuid4()), "simulate_outcome": "failure"}
    assert client.post("payments/simulate", headers=stranger, json=payment).status_code == 403
    assert client.post("payments/simulate", headers=traveler,
                       json={**payment, "card_number": "placeholder"}).status_code == 422
    assert client.post("payments/simulate", headers=traveler, json=payment).json()["status"] == "demo_failed"
    assert client.patch(path + "/status", headers=provider, json=confirm).status_code == 409
    payment.update(request_key=str(uuid4()), simulate_outcome="success", attempt_no=2)
    paid = client.post("payments/simulate", headers=traveler, json=payment)
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "demo_succeeded"
    assert client.post("payments/simulate", headers=traveler, json=payment).json() == paid.json()
    details = client.get(path, headers=traveler).json()
    assert details["status"] == "pending"
    assert {item["status"] for item in details["payments"]} == {"demo_succeeded", "demo_failed"}
    assert client.patch(path + "/status", headers=other, json=confirm).status_code == 403
    assert client.patch(path + "/status", headers=provider, json=confirm).json()["status"] == "confirmed"
    cancelled = client.patch(path + "/status", headers=traveler, json={"to_status": "cancelled"})
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(public + "/availability").json()[0]["remaining_capacity"] == 2
    other_booking = client.post("bookings", headers=stranger,
                                json={**payload, "request_key": str(uuid4())}).json()["id"]
    pay_later = {"booking_id": other_booking, "request_key": str(uuid4())}
    assert client.post("payments/at-location", headers=stranger, json=pay_later).json()["status"] == "unpaid"
    assert client.get(f"bookings/{other_booking}", headers=stranger).json()["status"] == "pending"
    accepted = client.patch(f"bookings/{other_booking}/status", headers=provider, json=confirm)
    assert accepted.json()["status"] == "confirmed"
    assert client.post("payments/at-location", headers=stranger, json=pay_later).json()["status"] == "unpaid"
    assert client.patch(f"bookings/{other_booking}/status", headers=stranger,
                        json={"to_status": "cancelled"}).status_code == 200
    pending = client.post("bookings", headers=traveler,
                          json={**payload, "request_key": str(uuid4())}).json()
    rejected = client.patch(f"bookings/{pending['id']}/status", headers=provider,
                            json={"to_status": "rejected", "reason": "Trail unavailable"})
    assert rejected.json()["status"] == "rejected"
    assert client.get(public + "/availability").json()[0]["remaining_capacity"] == 2


def test_concurrent_requests_for_the_last_activity_places(activity):
    client, (_, _, _, traveler, stranger), _ = activity
    _, public, _, _, payload, _ = setup_listing(activity)
    barrier = Barrier(2, timeout=5)

    def book(headers):
        barrier.wait()
        return client.post("bookings", headers=headers, json={**payload, "request_key": str(uuid4())})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, (traveler, stranger)))
    assert sorted(item.status_code for item in results) == [201, 409], [
        item.text for item in results
    ]
    assert client.get(public + "/availability").json()[0]["remaining_capacity"] == 0
