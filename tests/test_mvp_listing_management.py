from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import MVPBase, MVPProviderProfile, MVPUser
from app.services.mvp_homestay import SRI_LANKA

PREFIX = "/api/v1"


@pytest.fixture(params=["admin", "super_admin"])
def managed(tmp_path, monkeypatch, request):
    engine = create_engine(f"sqlite:///{tmp_path / 'management.sqlite'}")
    MVPBase.metadata.create_all(engine)
    monkeypatch.setenv("PROVIDER_UPLOAD_DIR", str(tmp_path))
    app = create_mvp_app()

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_mvp_db] = db
    with Session(engine) as session:
        accounts = [
            MVPUser(email=f"management-{i}@example.com", role=role, full_name=f"User {i}",
                    is_active=True, hashed_password="unused")
            for i, role in enumerate(("provider", "provider", request.param, "traveler"))
        ]
        session.add_all(accounts)
        for account in accounts[:2]:
            session.add(MVPProviderProfile(
                user=account, business_name=account.full_name, district="Badulla",
                phone="0771234567", verification_status="approved",
            ))
        session.commit()
        headers = [
            {"Authorization": "Bearer " + create_mvp_access_token(user.id, user.email, user.role)}
            for user in accounts
        ]
    image = BytesIO()
    Image.new("RGB", (8, 8)).save(image, "PNG")
    with TestClient(app) as client:
        yield client, headers, {"file": ("picture.png", image.getvalue(), "image/png")}
    engine.dispose()


def test_homestay_verification_and_existing_booking_paths(managed):
    client, (host, other, admin, traveler), image = managed
    created = client.post(f"{PREFIX}/provider/listings", headers=host, json={"category": "homestay"})
    assert created.status_code == 201, created.text
    listing = created.json()["listing"]["id"]
    path = f"{PREFIX}/provider/listings/{listing}"
    public = f"{PREFIX}/homestays/{listing}"
    review = f"{PREFIX}/admin/listings/{listing}"
    assert client.get(path, headers=other).status_code == 403
    assert client.get(public).status_code == 404
    assert client.post(path + "/submit", headers=host).status_code == 422
    assert client.post(review + "/decision", headers=admin,
                       json={"approval_status": "approved"}).status_code == 409
    assert client.post(review + "/decision", headers=host,
                       json={"approval_status": "approved"}).status_code == 403
    assert client.patch(path, headers=host, json={"latitude": 95}).status_code == 422
    edited = client.patch(path, headers=host, json={
        "title": "Ella family homestay", "description": "A quiet homestay near Ella.",
        "district": "Badulla", "locality": "Ella", "latitude": "6.8667", "longitude": "81.0466",
        "province": "Uva", "address": "Ella Road 12", "contact": "0771234567",
        "check_in_time": "14:00", "check_out_time": "10:00", "house_rules": "No smoking",
        "metadata": {"property_type": "cottage", "amenities": ["wifi"]},
    })
    assert edited.status_code == 200, edited.text
    assert client.post(path + "/photos", headers=host, files={
        "file": ("bad.png", b"invalid bytes", "image/png"),
    }).status_code == 400
    photo = client.post(path + "/photos", headers=host, files=image)
    assert photo.status_code == 201, photo.text
    photo_id = photo.json()["id"]
    assert client.put(f"{path}/photos/{photo_id}/cover", headers=host).status_code == 200
    assert client.get(f"{PREFIX}/listings/{listing}/photos/{photo_id}").status_code == 404
    assert client.get(f"{review}/photos/{photo_id}", headers=admin).status_code == 200
    assert client.put(path + "/evidence", headers=host, json={
        "links": [{"kind": "website", "url": "file:///etc/passwd"}],
    }).status_code == 422
    assert client.put(path + "/evidence", headers=host, json={
        "links": [{"kind": "website", "url": "https://example.com"}],
    }).status_code == 200
    assert client.post(path + "/evidence/files", headers=host, files=image,
                       data={"kind": "customer_feedback"}).status_code == 422
    evidence = client.post(path + "/evidence/files", headers=host, files=image,
                           data={"kind": "customer_feedback", "redacted": "true"})
    assert evidence.status_code == 201, evidence.text
    file_id = evidence.json()["id"]
    private = f"{path}/evidence/files/{file_id}"
    assert client.get(private, headers=other).status_code == 403
    assert client.get(private).status_code == 401
    assert client.get(private, headers=host).status_code == 200
    assert client.get(f"{review}/evidence/files/{file_id}", headers=admin).status_code == 200
    room = client.post(path + "/rooms", headers=host, json={
        "label": "Double", "capacity": 2, "nightly_price": "9000.00", "photo_ids": [photo_id],
    })
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]
    start = (datetime.now(SRI_LANKA) + timedelta(days=5)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=2)
    slot = {"unit_id": room_id, "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "capacity": 2, "is_open": True}
    assert client.post(f"{PREFIX}/listings/{listing}/slots", headers=other, json=slot).status_code == 403
    calendar = client.post(path + "/availability", headers=host, json=slot)
    assert calendar.status_code == 201, calendar.text
    slot_id = calendar.json()["id"]
    assert client.post(path + "/submit", headers=host).status_code == 200
    for state in ("more_evidence_required", "rejected"):
        response = client.post(review + "/decision", headers=admin,
                               json={"approval_status": state, "decision_reason": "Add clarification"})
        assert response.status_code == 200, response.text
        assert client.get(path, headers=host).json()["verification_status"] == state
        assert client.put(path + "/evidence", headers=host, json={"notes": "Clarification"}).status_code == 200
        assert client.post(path + "/submit", headers=host).status_code == 200
    approved = client.post(review + "/decision", headers=admin, json={"approval_status": "approved"})
    assert approved.status_code == 200, approved.text
    card = client.get(public).json()
    assert "details" not in card and "files" not in card and "links" not in card
    assert card["cover_photo_id"] == photo_id
    assert client.get(card["media"][0]["image_url"]).status_code == 200
    assert client.patch(f"{path}/rooms/{room_id}", headers=host,
                        json={"nightly_price": "9500.00"}).status_code == 200
    assert client.get(path, headers=host).json()["verification_status"] == "approved"
    dates = {"check_in": start.date().isoformat(), "check_out": end.date().isoformat(), "guests": 2}
    assert client.get(public + "/availability", params=dates).json()["total_amount"] == "19000.00"
    booked = client.post(public + "/bookings", headers=traveler, json={
        **dates, "room_id": room_id, "request_key": str(uuid4()),
    })
    assert booked.status_code == 201, booked.text
    booking_id = booked.json()["id"]
    assert client.get(f"{PREFIX}/provider/bookings/{booking_id}", headers=other).status_code == 404
    assert client.patch(f"{path}/rooms/{room_id}", headers=host,
                        json={"is_active": False}).status_code == 409
    assert client.put(f"{path}/availability/{slot_id}", headers=host,
                      json={**slot, "is_open": False}).status_code == 409
    assert client.post(f"{PREFIX}/payments/at-location", headers=traveler, json={
        "booking_id": booking_id, "request_key": str(uuid4()),
    }).status_code == 200
    decision = client.patch(f"{PREFIX}/bookings/{booking_id}/status", headers=host,
                            json={"to_status": "confirmed"})
    assert decision.status_code == 200, decision.text
    assert client.get(f"{PREFIX}/provider/bookings?status=confirmed", headers=host).json()[0]["id"] == booking_id
    assert client.post(review + "/decision", headers=admin,
                       json={"approval_status": "suspended", "decision_reason": "Further review"}).status_code == 200
    assert client.get(public).status_code == 404
    assert client.get(f"{PREFIX}/provider/bookings/{booking_id}", headers=host).status_code == 200
    assert client.get(f"{public}/bookings/{booking_id}", headers=traveler).status_code == 200
    assert [item["decision"] for item in client.get(review + "/history", headers=admin).json()] == [
        "suspended", "approved", "rejected", "more_evidence_required",
    ]


@pytest.mark.parametrize("category", ["adventure", "guide", "bike", "bicycle", "artisan"])
def test_shared_service_approval_rules(managed, category):
    client, (host, _, admin, _), image = managed
    response = client.post(f"{PREFIX}/listings", headers=host, json={
        "category": category, "title": "Approved service", "description": "A complete service listing",
        "district": "Badulla", "locality": "Ella", "latitude": "6.8667", "longitude": "81.0466",
        "price": "4500.00",
    })
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    path = f"{PREFIX}/provider/listings/{identifier}"
    public = f"{PREFIX}/listings/{identifier}"
    assert client.get(public).status_code == 404
    assert client.post(path + "/photos", headers=host, files=image).status_code == 201
    assert client.post(path + "/submit", headers=host).status_code == 422
    assert client.put(path + "/evidence", headers=host, json={
        "notes": "New academic service with alternative verification explanation",
    }).status_code == 200
    if category == "adventure":
        assert client.post(path + "/submit", headers=host).status_code == 422
        assert client.patch(path, headers=host, json={"activity": {
            "category": "hiking-trekking", "meeting_point": "Trail entrance",
            "duration_minutes": 120, "max_participants": 4,
            "safety_instructions": "Follow the marked trail",
        }}).status_code == 200
    if category == "guide":
        assert client.patch(path, headers=host, json={"guide": {}}).status_code == 200
        assert client.post(path + "/packages", headers=host, json={
            "title": "Ella Walk", "description": "Walk around Ella with a local guide.",
            "duration_hours": "2", "price": "4500", "meeting_point": "Ella station",
        }).status_code == 201
    if category in {"bike", "bicycle"}:
        assert client.patch(path, headers=host, json={"rental": {
            "subtype": "scooter" if category == "bike" else "city",
            "pickup_name": "Ella rental office",
        }}).status_code == 200
        assert client.post(path + "/units", headers=host, json={
            "label": "Vehicle 1", "capacity": 1,
        }).status_code == 201
    submitted = client.post(path + "/submit", headers=host)
    assert submitted.status_code == 200, submitted.text
    decision = client.post(f"{PREFIX}/admin/listings/{identifier}/decision", headers=admin,
                           json={"approval_status": "approved"})
    assert decision.status_code == 200, decision.text
    if category in {"adventure", "bike", "bicycle"}:
        assert client.get(public).status_code == 404
        assert client.post(path + "/publish", headers=host).status_code == 200
    assert client.get(public).status_code == 200
    assert client.patch(path, headers=host, json={"locality": "Bandarawela"}).json()["verification_status"] == "draft"
    assert client.get(public).status_code == 404


def test_mvp_guide_packages_booking_payment_and_wishlist(managed):
    client, (host, other, admin, traveler), image = managed
    listing = client.post(f"{PREFIX}/provider/listings", headers=host, json={
        "category": "guide", "title": "Ella Local Guide",
        "description": "Guided walks and heritage tours around Ella.",
        "district": "Badulla", "locality": "Ella", "latitude": "6.8667",
        "longitude": "81.0466", "price": "4500.00",
    }).json()["listing"]["id"]
    provider = f"{PREFIX}/provider/listings/{listing}"
    public = f"{PREFIX}/tour-guides/{listing}"
    package = {
        "title": "Ella Heritage Walk", "description": "Explore local history with a guide.",
        "duration_hours": "2.00", "max_people": 4, "pricing_type": "per_group",
        "price": "4500.00", "meeting_point": "Ella station",
    }
    assert client.get(public).status_code == 404
    assert client.patch(provider, headers=host, json={"guide": {
        "years_of_experience": 4, "categories": ["cultural_heritage"],
        "languages": [{"language_code": "en", "language_name": "English"}],
    }}).status_code == 200
    assert client.post(provider + "/packages", headers=other, json=package).status_code == 403
    one = client.post(provider + "/packages", headers=host, json=package)
    assert one.status_code == 201, one.text
    two = client.post(provider + "/packages", headers=host, json={
        **package, "title": "Ella Nature Walk", "pricing_type": "per_person",
    })
    assert two.status_code == 201, two.text
    first, second = one.json()["id"], two.json()["id"]
    assert client.post(provider + "/photos", headers=host, files=image).status_code == 201
    assert client.put(provider + "/evidence", headers=host, json={
        "notes": "Private guide credentials explanation",
    }).status_code == 200
    assert client.post(provider + "/submit", headers=host).status_code == 200
    review = client.get(f"{PREFIX}/admin/listings/{listing}", headers=admin).json()
    assert [item["id"] for item in review["details"]["packages"]] == [first, second]
    assert client.post(f"{PREFIX}/admin/listings/{listing}/decision", headers=admin,
                       json={"approval_status": "approved"}).status_code == 200
    found = client.get(f"{PREFIX}/tour-guides", params={"destination": "ella"}).json()["items"]
    assert [item["listing"]["id"] for item in found] == [listing]
    assert found[0]["profile"]["categories"] == ["cultural_heritage"]
    assert "notes" not in found[0]
    assert client.get(f"{PREFIX}/tour-guides/categories").json()[0]["slug"] == "cultural_heritage"
    assert client.get(public + f"/packages/{first}").json()["price"] == "4500.00"
    assert client.put(public + "/wishlist", headers=traveler).json()["saved"]
    assert client.get(f"{PREFIX}/tour-guides/wishlist", headers=traveler).json()["items"]
    assert client.delete(public + "/wishlist", headers=traveler).json()["saved"] is False
    assert client.get(f"{PREFIX}/tour-guides/wishlist", headers=traveler).json()["items"] == []
    start = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0)
    slot_payload = {"starts_at": start.isoformat(),
                    "ends_at": (start + timedelta(hours=2)).isoformat(), "capacity": 4}
    slots = [
        client.post(provider + "/availability", headers=host,
                    json={**slot_payload, "unit_id": identifier})
        for identifier in (first, second)
    ]
    assert [response.status_code for response in slots] == [201, 201]
    closed_path = f"{provider}/availability/{slots[0].json()['id']}"
    assert client.put(closed_path, headers=host, json={
        **slot_payload, "unit_id": first, "is_open": False,
    }).status_code == 200
    assert not client.get(public + "/availability", params={
        "package_id": first,
    }).json()[0]["available"]
    assert client.put(closed_path, headers=host, json={
        **slot_payload, "unit_id": first,
    }).status_code == 200
    group_price, person_price = [
        client.get(public + "/availability", params={
            "package_id": identifier, "participants": 2,
        }).json()[0]["total_amount"]
        for identifier in (first, second)
    ]
    assert (group_price, person_price) == ("4500.00", "9000.00")

    def request(index, amount):
        return {
            "listing_id": listing, "slot_id": slots[index].json()["id"],
            "starts_at": slot_payload["starts_at"], "ends_at": slot_payload["ends_at"],
            "expected_total": amount, "quantity": 2, "request_key": str(uuid4()),
        }

    booking_url = f"{PREFIX}/bookings"
    assert client.post(booking_url, headers=traveler, json=request(0, "1.00")).status_code == 409
    payloads = [request(0, group_price), request(1, person_price)]
    barrier = Barrier(2, timeout=5)

    def book(payload):
        barrier.wait()
        return client.post(booking_url, headers=traveler, json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, payloads))
    assert sorted(response.status_code for response in results) == [201, 409], [
        response.text for response in results
    ]
    winner = 0 if results[0].status_code == 201 else 1
    saved = results[winner].json()
    booking = saved["id"]
    status_url = f"{booking_url}/{booking}/status"
    assert saved["package_id"] == (first, second)[winner]
    assert saved["booking_reference"] == f"GUIDE-{booking}"
    assert client.post(booking_url, headers=traveler, json=payloads[winner]).json()["id"] == booking
    assert client.patch(status_url, headers=host,
                        json={"to_status": "confirmed"}).status_code == 409
    payment = client.post(f"{PREFIX}/payments/simulate", headers=traveler, json={
        "booking_id": booking, "request_key": str(uuid4()), "simulate_outcome": "success",
    })
    assert payment.json()["status"] == "demo_succeeded"
    assert client.get(f"{booking_url}/{booking}", headers=traveler).json()["status"] == "pending"
    assert client.patch(status_url, headers=other,
                        json={"to_status": "confirmed"}).status_code == 403
    assert client.patch(status_url, headers=host,
                        json={"to_status": "confirmed"}).json()["status"] == "confirmed"
    assert client.patch(status_url, headers=traveler,
                        json={"to_status": "cancelled"}).json()["status"] == "cancelled"
    other_booking = client.post(booking_url, headers=traveler, json=payloads[1 - winner]).json()["id"]
    later = client.post(f"{PREFIX}/payments/at-location", headers=traveler, json={
        "booking_id": other_booking, "request_key": str(uuid4()),
    })
    assert later.json()["status"] == "unpaid"
    assert client.get(f"{booking_url}/{other_booking}", headers=traveler).json()["status"] == "pending"
    assert client.patch(f"{booking_url}/{other_booking}/status", headers=host,
                        json={"to_status": "confirmed"}).json()["status"] == "confirmed"
    my_bookings = client.get(booking_url + "/my", headers=traveler, params={"category": "guide"})
    assert [item["id"] for item in my_bookings.json()] == [other_booking, booking]
