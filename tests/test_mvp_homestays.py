from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.mvp_security import create_mvp_access_token
from app.database.mvp_session import get_mvp_db
from app.main_mvp import create_mvp_app
from app.models.mvp import (
    MVPAvailabilitySlot, MVPBase, MVPInventoryUnit, MVPListing, MVPListingMedia,
    MVPProviderProfile, MVPUser,
)
from app.schemas.mvp_homestay import StayDates
from app.services.mvp_homestay import SRI_LANKA, stay_window

API = "/api/v1"


@pytest.fixture
def homestay():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    MVPBase.metadata.create_all(engine)
    app = create_mvp_app()

    def db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_mvp_db] = db
    first = datetime.now(SRI_LANKA).date() + timedelta(days=10)
    dates = StayDates(check_in=first, check_out=first + timedelta(days=2), guests=2)
    start, end = stay_window(dates)
    with Session(engine) as session:
        users = [
            MVPUser(email=f"{role}{index}@example.com", full_name=role.title(),
                    hashed_password="unused", role=role)
            for index, role in enumerate(["provider", "traveler", "traveler"])
        ]
        provider = MVPProviderProfile(user=users[0], business_name="Ella Hosts", phone="0771234567",
                                      district="Badulla", verification_status="approved")
        listing = MVPListing(provider=provider, category="homestay", title="Ella View",
                             description="An approved homestay", district="Badulla", locality="Ella",
                             latitude=Decimal("6.8667"), longitude=Decimal("81.0466"),
                             price=Decimal("8500.00"), approval_status="approved", is_active=True,
                             homestay_metadata='{"property_type":"cottage","amenities":["wifi","parking"]}')
        hidden = MVPListing(provider=provider, category="homestay", title="Hidden homestay",
                            description="A draft homestay", district="Badulla", locality="Ella",
                            price=Decimal("7500"), approval_status="draft", is_active=False)
        session.add_all([*users, listing, hidden])
        session.flush()
        rooms = [
            MVPInventoryUnit(listing_id=listing.id, provider_id=provider.id,
                             label=label, capacity=2)
            for label in ("Double", "Gap", "Closed")
        ]
        session.add_all(rooms)
        session.flush()
        for unit, left, right, opened in (
            (rooms[0], start, start + timedelta(days=1), True),
            (rooms[0], start + timedelta(days=1), end + timedelta(days=2), True),
            (rooms[1], start, start + timedelta(days=1), True),
            (rooms[2], start - timedelta(days=1), end, True),
            (rooms[2], start + timedelta(hours=1), end, False),
        ):
            session.add(MVPAvailabilitySlot(
                listing_id=listing.id, unit_id=unit.id, starts_at=left,
                ends_at=right, capacity=2, is_open=opened,
            ))
        session.add(MVPListingMedia(listing_id=listing.id, storage_key="https://example.com/ella.jpg",
                                    sort_order=0, alt_text="Ella homestay"))
        session.commit()
        headers = [
            {"Authorization": f"Bearer {create_mvp_access_token(user.id, user.email, user.role)}"}
            for user in users
        ]
        identifiers = (listing.id, hidden.id, rooms[0].id)

    with TestClient(app) as client:
        yield client, *identifiers, dates.model_dump(mode="json"), *headers
    engine.dispose()


def test_public_homestay_filters_and_wishlist(homestay):
    client, listing, hidden, room, dates, host, traveler, other = homestay
    url = f"{API}/homestays/{listing}"
    assert client.get(f"{API}/homestays/{hidden}").status_code == 404
    assert client.get(f"{API}/listings/{hidden}").status_code == 404
    assert client.put(url + "/metadata", headers=host, json={
        "property_type": "cottage", "amenities": ["WiFi", "parking"],
    }).status_code == 200
    response = client.get(f"{API}/homestays", params={
        **dates, "destination": "ella", "property_type": "cottage",
        "amenities": ["wifi", "parking"], "min_price": 8000, "max_price": 9000,
    })
    assert response.status_code == 200, response.text
    details = response.json()["items"][0]
    assert details["id"] == listing
    assert details["host"]["business_name"] == "Ella Hosts"
    assert details["media"][0]["image_url"] == "https://example.com/ella.jpg"
    assert details["latitude"] == "6.866700"
    assert client.get(f"{API}/homestays", params={"amenities": ["pool"]}).json()["items"] == []
    quote = client.get(url + "/availability", params=dates).json()
    assert [item["id"] for item in quote["available_rooms"]] == [room]
    assert quote["total_amount"] == "17000.00"
    assert client.get(url + "/availability", params={**dates, "guests": 3}).json()["available_rooms"] == []
    assert client.get(url + "/availability", params={**dates, "check_out": dates["check_in"]}).status_code == 422
    assert client.put(url + "/wishlist").status_code == 401
    for _ in range(2):
        assert client.put(url + "/wishlist", headers=traveler).json() == {"listing_id": listing, "saved": True}
    assert len(client.get(f"{API}/homestays/wishlist", headers=traveler).json()["items"]) == 1
    assert client.get(f"{API}/homestays/wishlist", headers=other).json()["items"] == []
    assert client.delete(url + "/wishlist", headers=traveler).status_code == 204
    assert client.get(f"{API}/homestays/wishlist", headers=traveler).json()["items"] == []


@pytest.mark.parametrize("payment", ["online", "at_location"])
def test_book_payment_and_host_confirmation(homestay, payment):
    client, listing, _, room, dates, host, traveler, other = homestay
    root = f"{API}/homestays/{listing}"
    request = {**dates, "room_id": room, "request_key": str(uuid4()), "expected_total": "17000.00"}
    assert client.post(root + "/bookings", headers=host, json=request).status_code == 403
    assert client.post(root + "/bookings", headers=traveler,
                       json={**request, "expected_total": "1.00"}).status_code == 409
    response = client.post(root + "/bookings", headers=traveler, json=request)
    assert response.status_code == 201, response.text
    booking = response.json()
    assert booking["room_ids"] == [room]
    assert booking["total_amount"] == "17000.00"
    booking_id = booking["id"]
    assert client.post(root + "/bookings", headers=traveler, json=request).json()["id"] == booking_id
    assert client.post(root + "/bookings", headers=other,
                       json={**request, "request_key": str(uuid4())}).status_code == 409
    next_stay = datetime.fromisoformat(dates["check_out"]).date()
    adjoining = client.post(root + "/bookings", headers=other, json={
        **request, "request_key": str(uuid4()), "check_in": next_stay.isoformat(),
        "check_out": (next_stay + timedelta(days=2)).isoformat(),
    })
    assert adjoining.status_code == 201, adjoining.text
    assert client.get(root + "/availability", params=dates).json()["available_rooms"] == []
    status_url = f"{API}/bookings/{booking_id}/status"
    assert client.patch(status_url, headers=other, json={"to_status": "confirmed"}).status_code == 403
    assert client.patch(status_url, headers=host, json={"to_status": "confirmed"}).status_code == 409
    pay = {"booking_id": booking_id, "request_key": str(uuid4())}
    if payment == "online":
        assert client.post(f"{API}/payments/simulate", headers=traveler,
                           json=pay).status_code == 422
        failed = client.post(f"{API}/payments/simulate", headers=traveler,
                             json={**pay, "simulate_outcome": "failure"})
        assert failed.json()["status"] == "demo_failed"
        assert client.patch(status_url, headers=host, json={"to_status": "confirmed"}).status_code == 409
        pay = {**pay, "request_key": str(uuid4()), "simulate_outcome": "success", "attempt_no": 2}
        endpoint, expected = "simulate", "demo_succeeded"
    else:
        endpoint, expected = "at-location", "unpaid"
    assert client.post(f"{API}/payments/{endpoint}", headers=other, json=pay).status_code == 403
    paid = client.post(f"{API}/payments/{endpoint}", headers=traveler, json=pay)
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == expected
    assert client.post(f"{API}/payments/{endpoint}", headers=traveler, json=pay).json()["id"] == paid.json()["id"]
    detail = f"{root}/bookings/{booking_id}"
    assert client.get(detail, headers=traveler).json()["status"] == "pending"
    assert client.get(detail, headers=other).status_code == 404
    confirmed = client.patch(status_url, headers=host, json={"to_status": "confirmed"})
    assert confirmed.status_code == 200, confirmed.text
    assert client.get(detail, headers=traveler).json()["status"] == "confirmed"
    assert client.patch(status_url, headers=traveler, json={"to_status": "cancelled"}).status_code == 200
    assert [item["id"] for item in client.get(root + "/availability", params=dates).json()["available_rooms"]] == [room]
