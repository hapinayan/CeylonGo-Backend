from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.booking import Booking
from app.models.shared import BookingStatusHistory, Notification, PaymentTransaction
from app.models.showcase_item import ShowcaseItem
from app.models.tour_guide import TourGuideAvailability, TourGuidePackage
from app.services import provider_reservations
from app.services.provider_reservations import LOCAL
from tests import test_interactive_map

catalog = test_interactive_map.catalog
API = "/api/v1"
PROVIDER = f"{API}/provider/bookings"


@pytest.fixture
def inventory(catalog):
    client, actor, engine = catalog
    day = datetime.now(LOCAL).date() + timedelta(days=3)
    with Session(engine) as db:
        item = ShowcaseItem(
            artisan_listing_id=1, title="Wooden bowl", price=100, stock_quantity=2
        )
        package = TourGuidePackage(
            tour_guide_id=1,
            title="City walk",
            description="A public walk",
            duration_hours=2,
            price=500,
            max_people=3,
            meeting_point="Town Hall",
        )
        db.add_all(
            [
                item,
                package,
                TourGuideAvailability(
                    tour_guide_id=1,
                    available_date=day,
                    start_time=time(9),
                    end_time=time(15),
                ),
            ]
        )
        db.flush()
        ids = item.id, package.id
        db.commit()
    return client, actor, engine, day, *ids


def test_artisan_cash_reservation_stock_history_and_isolation(inventory):
    client, actor, engine, day, item_id, _ = inventory
    actor["id"] = 5
    payload = {"item_id": item_id, "quantity": 2, "pickup_date": day.isoformat()}
    response = client.post(f"{API}/artisan-reservations", json=payload)
    assert response.status_code == 201, response.text
    booking_id = response.json()["id"]
    assert response.json()["total"] == "200.00"
    assert client.post(f"{API}/artisan-reservations", json=payload).status_code == 409
    assert (
        client.get(f"{API}/reservations/artisan_reservation/me").json()[0]["id"]
        == booking_id
    )
    actor["id"] = 3
    assert client.get(PROVIDER).status_code == 403
    actor["id"] = 2
    assert client.get(f"{PROVIDER}/{booking_id}").status_code == 404
    actor["id"] = 7
    assert (
        client.get(PROVIDER, params={"q": "bowl", "state": "pending"}).json()["items"][
            0
        ]["id"]
        == booking_id
    )
    action = f"{PROVIDER}/{booking_id}/action"
    assert client.post(action, json={"action": "cash"}).status_code == 409
    assert (
        client.post(action, json={"action": "accept"}).json()["status"] == "confirmed"
    )
    assert (
        client.post(action, json={"action": "cash"}).json()["payment_status"] == "paid"
    )
    assert client.post(action, json={"action": "cash"}).status_code == 409
    assert client.post(action, json={"action": "complete"}).status_code == 409
    assert (
        client.get(f"{PROVIDER}/{booking_id}").json()["history"][0]["status"]
        == "confirmed"
    )
    assert client.get(f"{API}/provider/dashboard").json()["bookings"]["upcoming"] == 1
    with Session(engine) as db:
        assert db.get(ShowcaseItem, item_id).stock_quantity == 0
        assert len(db.scalars(select(PaymentTransaction)).all()) == 1
        assert len(db.scalars(select(Notification)).all()) == 3


def test_guide_validates_availability_and_releases_rejected_booking(inventory):
    client, actor, _, day, _, package_id = inventory
    actor["id"] = 5
    payload = {
        "guide_id": 1,
        "package_id": package_id,
        "people": 2,
        "starts_at": datetime.combine(day, time(10), LOCAL).isoformat(),
    }
    path = f"{API}/tour-guide-reservations"
    assert client.post(path, json={**payload, "people": 4}).status_code == 409
    assert (
        client.post(
            path,
            json={
                **payload,
                "starts_at": datetime.combine(day, time(7), LOCAL).isoformat(),
            },
        ).status_code
        == 409
    )
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.text
    booking_id = response.json()["id"]
    assert response.json()["meeting_point"] == "Town Hall"
    assert client.post(path, json=payload).status_code == 409
    actor["id"] = 8
    action = f"{PROVIDER}/{booking_id}/action"
    assert client.post(action, json={"action": "reject"}).json()["status"] == "rejected"
    assert client.post(action, json={"action": "accept"}).status_code == 409
    actor["id"] = 5
    assert client.post(path, json=payload).status_code == 201


def test_guide_completion_requires_recorded_cash_and_elapsed_tour(
    inventory, monkeypatch
):
    client, actor, _, day, _, package_id = inventory
    actor["id"] = 5
    start = datetime.combine(day, time(10), LOCAL)
    response = client.post(
        f"{API}/tour-guide-reservations",
        json={
            "guide_id": 1,
            "package_id": package_id,
            "people": 1,
            "starts_at": start.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    actor["id"] = 8
    action = f"{PROVIDER}/{response.json()['id']}/action"
    assert client.post(action, json={"action": "accept"}).status_code == 200
    assert client.post(action, json={"action": "complete"}).status_code == 409
    assert (
        client.post(action, json={"action": "cash"}).json()["payment_status"] == "paid"
    )
    monkeypatch.setattr(
        provider_reservations,
        "utc_now",
        lambda: (start + timedelta(hours=3)).astimezone(UTC).replace(tzinfo=None),
    )
    assert (
        client.post(action, json={"action": "complete"}).json()["status"] == "completed"
    )


def test_provider_booking_action_rejects_artisan_reservation(inventory):
    client, actor, engine, day, item_id, _ = inventory
    actor["id"] = 5
    created = client.post(
        f"{API}/artisan-reservations",
        json={"item_id": item_id, "quantity": 1, "pickup_date": day.isoformat()},
    )
    assert created.status_code == 201
    actor["id"] = 7
    action = f"{PROVIDER}/{created.json()['id']}/action"
    assert client.post(action, json={"action": "reject"}).status_code == 422
    result = client.post(action, json={"action": "reject", "reason": "Closed today"})
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "rejected"
    with Session(engine) as db:
        assert db.get(ShowcaseItem, item_id).stock_quantity == 2


def test_artisan_cancellation_restores_stock_and_pickup_completes(inventory):
    client, actor, engine, _, item_id, _ = inventory
    actor["id"] = 5
    payload = {
        "item_id": item_id,
        "quantity": 1,
        "pickup_date": datetime.now(LOCAL).date().isoformat(),
    }
    path = f"{API}/artisan-reservations"
    first = client.post(path, json=payload).json()
    detailed_payload = {
        "showcase_item_id": item_id,
        "quantity": 1,
        "pickup_date": payload["pickup_date"],
    }
    assert client.post(path, json=detailed_payload).status_code == 409
    cancelled = client.post(
        f"{API}/reservations/artisan_reservation/{first['id']}/cancel",
        json={},
    )
    assert cancelled.json()["status"] == "cancelled"
    with Session(engine) as db:
        assert db.get(ShowcaseItem, item_id).stock_quantity == 2
    second = client.post(path, json=payload).json()
    assert second["id"] != first["id"]
    actor["id"] = 7
    action = f"{PROVIDER}/{second['id']}/action"
    for step, status in [
        ("accept", "confirmed"),
        ("ready", "ready_for_pickup"),
        ("cash", "ready_for_pickup"),
        ("complete", "completed"),
    ]:
        assert client.post(action, json={"action": step}).json()["status"] == status
        if step == "cash":
            blocked = client.post(f"{path}/{second['id']}/cancel", json={})
            assert blocked.status_code == 409
            with Session(engine) as db:
                assert db.get(ShowcaseItem, item_id).stock_quantity == 1


def test_artisan_payload_and_action_routes_share_pickup_rules(inventory):
    client, actor, engine, future_day, item_id, _ = inventory
    actor["id"] = 5
    pickup = datetime.now(LOCAL).date()
    path = f"{API}/artisan-reservations"
    for key in ("item_id", "showcase_item_id"):
        booked = client.post(
            path, json={key: item_id, "quantity": 1, "pickup_date": pickup.isoformat()}
        )
        assert booked.status_code == 201, booked.text
        booking_id = booked.json()["id"]
        detail = client.get(f"{path}/{booking_id}").json()
        assert detail["payment_status"] == "unpaid" and detail["expires_at"]
        actor["id"] = 7
        shared = f"{PROVIDER}/{booking_id}/action"
        assert client.patch(f"{path}/{booking_id}/ready").status_code == 400
        assert client.post(shared, json={"action": "ready"}).status_code == 409
        assert client.patch(f"{path}/{booking_id}/confirm").status_code == 200
        assert client.post(shared, json={"action": "ready"}).status_code == 200
        completed = client.post(f"{path}/{booking_id}/complete-pickup")
        assert completed.json()["payment_status"] == "paid"
        assert client.post(shared, json={"action": "complete"}).status_code == 409
        actor["id"] = 5
    with Session(engine) as db:
        assert db.get(ShowcaseItem, item_id).stock_quantity == 0
        assert len(db.scalars(select(PaymentTransaction)).all()) == 2
    assert client.post(
        path, json={"item_id": item_id, "quantity": 1, "pickup_date": future_day.isoformat()}
    ).status_code == 409


def test_homestay_cash_receipt_and_single_review(catalog):
    client, actor, engine = catalog
    day = datetime.now(UTC).date()
    with Session(engine) as db:
        row = Booking(
            homestay_id=1,
            user_id=5,
            check_in=day - timedelta(days=3),
            check_out=day - timedelta(days=1),
            guests=1,
            total_price=200,
            status="completed",
            payment_method="pay_at_location",
        )
        db.add(row)
        db.commit()
        booking_id = row.id
    actor["id"] = 11
    cash = f"{API}/bookings/{booking_id}/record-cash-payment"
    assert client.post(cash, json={"amount": "100"}).status_code == 400
    result = client.post(cash, json={})
    assert result.status_code == 200, result.text
    assert result.json()["payment_status"] == "paid"
    assert client.post(cash, json={}).status_code == 409
    actor["id"] = 5
    review = f"{API}/homestays/1/reviews"
    payload = {"booking_id": booking_id, "rating": 5, "title": "Good stay"}
    assert client.post(review, json=payload).status_code == 201
    assert client.post(review, json=payload).status_code == 409
    with Session(engine) as db:
        assert len(db.scalars(select(BookingStatusHistory)).all()) == 1


def test_activity_provider_completion_and_owned_history(catalog):
    client, actor, engine = catalog
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(engine) as db:
        slot = ActivitySlot(
            activity_id=1,
            starts_at=now - timedelta(hours=3),
            ends_at=now - timedelta(hours=1),
            capacity=4,
            reserved_count=1,
        )
        db.add(slot)
        db.flush()
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=1,
            total_price=200,
            payment_method="pay_at_location",
            booking_status="confirmed",
        )
        db.add(booking)
        db.commit()
        booking_id = booking.id
    actor["id"] = 2
    url = f"{PROVIDER}/{booking_id}/action"
    assert client.post(url, json={"action": "complete"}).status_code == 404
    actor["id"] = 1
    result = client.post(url, json={"action": "complete"})
    assert result.status_code == 200, result.text
    assert result.json()["history"][0]["status"] == "completed"
    assert client.post(url, json={"action": "complete"}).status_code == 409


@pytest.mark.parametrize(
    "actor_id, status", [(1, 200), (7, 200), (8, 200), (9, 200), (11, 200), (12, 200)]
)
def test_provider_booking_queue_category_coverage(catalog, actor_id, status):
    client, actor, _ = catalog
    actor["id"] = actor_id
    response = client.get(PROVIDER, params={"page_size": 1, "state": "upcoming"})
    assert response.status_code == status, response.text
