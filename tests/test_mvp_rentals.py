from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

from tests.test_mvp_listing_management import managed

API = "/api/v1"


def publish_rental(managed, category, unit_count=1):
    client, (provider, stranger, admin, _), image = managed
    made = client.post(f"{API}/provider/listings", headers=provider, json={
        "category": category, "title": "Ella rental fleet",
        "description": "Rent a well maintained vehicle in Ella.",
        "district": "Badulla", "locality": "Ella", "price": "1500.00",
        "latitude": "6.8667", "longitude": "81.0466",
    })
    assert made.status_code == 201, made.text
    listing_id = made.json()["listing"]["id"]
    listing = f"{API}/provider/listings/{listing_id}"
    public = f"{API}/rental-vehicles/{listing_id}"
    rental = {
        "subtype": "scooter" if category == "bike" else "mountain",
        "transmission": "automatic" if category == "bike" else "manual",
        "pickup_name": "Ella station", "rental_terms": "Return to the pickup office",
    }
    assert client.get(public).status_code == 404
    assert client.patch(listing, headers=stranger, json={"rental": rental}).status_code == 403
    changed = client.patch(listing, headers=provider, json={"rental": rental})
    assert changed.status_code == 200, changed.text
    photo = client.post(listing + "/photos", headers=provider, files=image)
    assert photo.status_code == 201, photo.text
    evidence = client.post(listing + "/evidence/files", headers=provider, files=image,
                           data={"kind": "vehicle_registration"})
    assert evidence.status_code == 201, evidence.text
    assert client.put(listing + "/evidence", headers=provider, json={
        "notes": "Vehicle ownership confirmed for the demo review.",
    }).status_code == 200
    assert client.post(listing + "/submit", headers=provider).status_code == 422
    start = (datetime.now(UTC) + timedelta(days=4)).replace(microsecond=0)
    pickup, returned = start, start + timedelta(days=1, minutes=1)
    slots = []
    for index in range(unit_count):
        unit = client.post(listing + "/units", headers=provider, json={
            "label": f"Unit {index + 1}", "capacity": 1,
        })
        assert unit.status_code == 201, unit.text
        slot_payload = {
            "unit_id": unit.json()["id"], "capacity": 1,
            "starts_at": (start - timedelta(days=1)).isoformat(),
            "ends_at": (start + timedelta(days=5)).isoformat(),
        }
        slot = client.post(listing + "/availability", headers=provider, json=slot_payload)
        assert slot.status_code == 201, slot.text
        slots.append(slot.json())
    assert client.post(listing + "/submit", headers=provider).status_code == 200
    review = f"{API}/admin/listings/{listing_id}"
    assert client.get(review + "/evidence/files/" + evidence.json()["id"],
                      headers=admin).status_code == 200
    assert client.post(review + "/decision", headers=admin,
                       json={"approval_status": "approved"}).status_code == 200
    assert client.get(public).status_code == 404
    assert client.post(listing + "/publish", headers=provider).status_code == 200
    return listing_id, listing, public, pickup, returned, slots


def test_rental_angular_endpoints_on_empty_and_populated_mvp(managed):
    client, (provider, _, _, traveler), _ = managed
    endpoint = f"{API}/rental-vehicles"
    empty = client.get(endpoint, params={"vehicle_type": "bicycle", "page": 1, "limit": 6})
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": [], "total": 0, "page": 1, "limit": 6, "pages": 0}
    assert {item["category"] for item in client.get(endpoint + "/categories").json()} == {
        "bike", "bicycle",
    }
    listing_id, listing, public, pickup, returned, slots = publish_rental(managed, "bicycle")
    date_body = {"pickup_at": pickup.isoformat(), "return_at": returned.isoformat()}
    params = {
        **date_body, "vehicle_type": "bicycle", "vehicle_subtype": "mountain",
        "city": "ell", "min_price": "1000", "max_price": "1500", "transmission": "manual",
        "sort_by": "price_asc",
    }
    found = client.get(endpoint, params=params)
    assert found.status_code == 200, found.text
    assert [item["id"] for item in found.json()["items"]] == [listing_id]
    assert not client.get(endpoint, params={**params, "transmission": "single_speed"}).json()["items"]
    assert not client.get(endpoint, params={**params, "page": 2}).json()["items"]
    detail = client.get(public).json()
    assert detail["location"]["location_name"] == "Ella station"
    assert detail["rental"]["rental_terms"] == "Return to the pickup office"
    assert "files" not in detail
    assert detail["primary_image_url"].startswith("http://testserver/")
    assert client.get(detail["primary_image_url"]).status_code == 200
    assert client.get(public + "/reviews").json()["items"] == []
    date_check = client.post(f"{API}/rental-bookings/validate-dates",
                             json={"vehicle_id": listing_id, **date_body})
    assert date_check.json()["is_valid"]
    availability = client.get(public + "/availability", params={
        "start_date": date_body["pickup_at"], "end_date": date_body["return_at"],
    })
    assert availability.json()["available_quantity"] == 1
    quote = client.post(public + "/calculate-quote", json=date_body).json()
    assert quote["duration_days"] == 2 and quote["total_price"] == "3000.00"
    assert quote["requires_driving_license"] is False
    wishlist = f"{API}/rental-wishlist"
    assert client.post(f"{wishlist}/{listing_id}").status_code == 401
    assert client.post(f"{wishlist}/{listing_id}", headers=traveler).json()["saved"]
    assert client.get(f"{wishlist}/{listing_id}/status", headers=traveler).json()["saved"]
    assert client.get(wishlist, headers=traveler).json()["total"] == 1
    assert client.delete(f"{wishlist}/{listing_id}", headers=traveler).json()["saved"] is False
    block = client.post(listing + "/availability", headers=provider, json={
        "unit_id": slots[0]["unit_id"], "starts_at": pickup.isoformat(),
        "ends_at": returned.isoformat(), "capacity": 1, "is_open": False,
    })
    assert block.status_code == 201, block.text
    assert not client.get(endpoint, params=params).json()["items"]
    restored = client.put(f"{listing}/availability/{block.json()['id']}", headers=provider, json={
        "unit_id": slots[0]["unit_id"], "starts_at": pickup.isoformat(),
        "ends_at": returned.isoformat(), "capacity": 1, "is_open": True,
    })
    assert restored.status_code == 200, restored.text
    assert client.get(endpoint, params=params).json()["total"] == 1
    bicycle_booking = client.post(f"{API}/bookings", headers=traveler, json={
        "listing_id": listing_id, "starts_at": date_body["pickup_at"],
        "ends_at": date_body["return_at"], "expected_total": quote["total_price"],
        "request_key": str(uuid4()),
    })
    assert bicycle_booking.status_code == 201, bicycle_booking.text


def test_rental_fleet_booking_payments_and_transactional_inventory(managed):
    client, (provider, other, _, traveler), _ = managed
    listing_id, listing, public, pickup, returned, slots = publish_rental(managed, "bike", 2)
    booking = {
        "listing_id": listing_id, "starts_at": pickup.isoformat(), "ends_at": returned.isoformat(),
        "quantity": 2, "expected_total": "6000.00", "request_key": str(uuid4()),
    }
    url = f"{API}/bookings"
    assert client.post(url, headers=traveler, json=booking).status_code == 422
    booking.update(motorcycle_license_confirmed=True, license_valid_until="2090-01-01")
    assert client.post(url, headers=traveler,
                       json={**booking, "license_valid_until": "2000-01-01"}).status_code == 422
    assert client.post(url, headers=traveler,
                       json={**booking, "expected_total": "1.00"}).status_code == 409
    barrier = Barrier(2, timeout=5)

    def book(key):
        barrier.wait()
        return client.post(url, headers=traveler, json={**booking, "request_key": key})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(book, (booking["request_key"], str(uuid4()))))
    assert sorted(response.status_code for response in results) == [201, 409], [
        response.text for response in results
    ]
    booked = next(response.json() for response in results if response.status_code == 201)
    booking_id = booked["id"]
    assert booked["booking_reference"] == f"RENT-{booking_id}"
    assert booked["quantity"] == 2
    assert len(booked["rental_unit_ids"]) == 2
    replayed = client.post(url, headers=traveler, json={
        **booking, "request_key": booked["request_key"],
    })
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["id"] == booking_id
    overlap = client.post(url, headers=traveler, json={
        **booking, "starts_at": (pickup + timedelta(hours=1)).isoformat(),
        "quantity": 1, "expected_total": "1500.00", "request_key": str(uuid4()),
    })
    assert overlap.status_code == 409, overlap.text
    adjacent = client.get(public + "/availability", params={
        "start_date": returned.isoformat(),
        "end_date": (returned + timedelta(hours=1)).isoformat(),
    })
    assert adjacent.json()["available_quantity"] == 2
    assert client.patch(f"{listing}/units/{slots[0]['unit_id']}", headers=provider,
                        json={"label": "Unit 1", "is_active": False}).status_code == 409
    status_path = f"{url}/{booking_id}/status"
    assert client.patch(status_path, headers=provider,
                        json={"to_status": "confirmed"}).status_code == 409
    assert client.patch(status_path, headers=other,
                        json={"to_status": "confirmed"}).status_code == 403
    payment = {"booking_id": booking_id, "request_key": str(uuid4()), "simulate_outcome": "failure"}
    assert client.post(f"{API}/payments/simulate", headers=traveler,
                       json=payment).json()["status"] == "demo_failed"
    payment.update(simulate_outcome="success", request_key=str(uuid4()), attempt_no=2)
    demo = client.post(f"{API}/payments/simulate", headers=traveler, json=payment)
    assert demo.json()["status"] == "demo_succeeded"
    assert client.post(f"{API}/payments/simulate", headers=traveler, json=payment).json() == demo.json()
    assert client.get(f"{url}/{booking_id}", headers=traveler).json()["status"] == "pending"
    assert client.patch(status_path, headers=provider,
                        json={"to_status": "confirmed"}).json()["status"] == "confirmed"
    assert client.patch(status_path, headers=traveler,
                        json={"to_status": "cancelled"}).json()["status"] == "cancelled"
    another = client.post(url, headers=traveler, json={**booking, "request_key": str(uuid4())})
    assert another.status_code == 201, another.text
    second_id = another.json()["id"]
    paid_later = client.post(f"{API}/payments/at-location", headers=traveler, json={
        "booking_id": second_id, "request_key": str(uuid4()),
    }).json()
    assert paid_later["status"] == "unpaid"
    assert client.get(f"{url}/{second_id}", headers=traveler).json()["status"] == "pending"
    assert client.patch(f"{url}/{second_id}/status", headers=provider,
                        json={"to_status": "confirmed"}).json()["status"] == "confirmed"
    assert client.get(f"{url}/my?category=bike", headers=traveler).json()[0]["id"] == second_id
    assert client.get(f"{API}/provider/bookings?category=bike",
                      headers=provider).json()[0]["id"] == second_id
    assert client.patch(f"{url}/{second_id}/status", headers=provider,
                        json={"to_status": "completed"}).json()["status"] == "completed"
