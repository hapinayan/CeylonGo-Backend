from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import create_sponsored_tracking_token
from app.models.eco_promotion import (
    EcoPromotionAttribution, EcoPromotionPackage, EcoSponsoredPromotion,
    EcoSponsorshipCapture,
)
from app.models.shared import Notification, PaymentRefund
from app.routers.eco_promotions import _reset_tracking_caches
from tests.test_eco_native_payments import callback, checkout, confirm, enabled
from tests.test_eco_reservations import (
    API, RESERVATIONS, SLOTS, catalog, eco_inventory, reservation, slot,
)


def test_verified_sponsorship_replay_expiry_and_durable_attribution(eco_inventory, enabled, monkeypatch):
    client, actor, engine, day = eco_inventory
    monkeypatch.setenv("ECO_SPONSORSHIP_LEDGER_ENABLED", "1")
    monkeypatch.setenv("ECO_SPONSORSHIP_CHECKOUT_ENABLED", "1")
    with Session(engine) as db:
        package = EcoPromotionPackage(
            name="Synthetic sponsorship", duration_days=3, price=Decimal("500.00"),
            currency="LKR", placement_tier="standard", is_active=True,
        )
        db.add(package)
        db.commit()
        package_id = package.id
    sid = slot(client, actor, day)["id"]
    root = f"{API}/eco-promotions"
    admin = f"{API}/admin/eco-promotions"
    payload = {"eco_experience_id": 1, "package_id": package_id}
    response = client.post(f"{root}/campaigns", json=payload)
    assert response.status_code == 201, response.text
    cid = response.json()["id"]
    purchase = f"{root}/me/campaigns/{cid}/payments/initiate"
    assert client.post(purchase).status_code == 409
    assert client.post(f"{admin}/campaigns/{cid}/approve").status_code == 403
    actor["id"] = 4
    assert client.post(f"{admin}/campaigns/{cid}/approve").status_code == 200
    actor["id"] = 2
    assert client.post(purchase).status_code in (403, 404)
    actor["id"] = 12
    order = client.post(purchase)
    assert order.status_code == 200, order.text
    order_id = order.json()["order_id"]
    assert client.post(purchase).json()["order_id"] == order_id
    assert not client.get(f"{API}/eco-experiences/1").json()["is_sponsored"]
    payment = {"reference": order_id, "total": "500.00", "currency": "LKR"}
    assert callback(client, payment, "sponsor-1", corrupt_signature=True).status_code == 400
    assert callback(client, payment, "sponsor-1", amount="1.00").status_code == 400
    assert callback(client, payment, "sponsor-1").json()["status"] == "ok"
    assert callback(client, payment, "SPONSOR-1").json()["status"] == "already_processed"
    assert callback(client, payment, "sponsor-2").json()["status"] == "requires_reconciliation"
    assert callback(client, payment, "sponsor-2").json()["status"] == "already_processed"
    assert client.get(f"{API}/eco-experiences/1").json()["is_sponsored"]
    matches = client.get(
        f"{API}/eco-experiences", params={"search": "Jaffna"}
    ).json()["items"]
    assert any(item["id"] == 1 and item["is_sponsored"] for item in matches)
    token = create_sponsored_tracking_token(cid, 1)
    wrong_listing = create_sponsored_tracking_token(cid, 2)
    assert client.post(f"{root}/track/click", json={"token": wrong_listing}).status_code == 400
    for kind in ("impression", "click"):
        endpoint = f"{root}/track/{kind}"
        assert client.post(endpoint, json={"token": token}).status_code == 200
        _reset_tracking_caches()
        assert client.post(endpoint, json={"token": token}).status_code == 409
    assert reservation(client, actor, sid, 1, sponsored_tracking_token=token).status_code == 201
    assert reservation(client, actor, sid, 1, traveler=13, sponsored_tracking_token=token).status_code == 201
    analytics = f"{root}/me/analytics"
    assert client.get(analytics).status_code == 403
    actor["id"] = 2
    assert client.get(analytics).json()["attributed_bookings"] == 0
    actor["id"] = 12
    aggregate = client.get(analytics).json()
    assert aggregate["attributed_bookings"] == 1
    assert aggregate["campaigns"][0]["attributed_bookings"] == 1
    assert client.get(f"{root}/me/campaigns/{cid}/analytics").json()["attributed_bookings"] == 1
    assert client.get(analytics, params={"end_date": "2000-01-01"}).json()[
        "attributed_bookings"
    ] == 0
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(EcoPromotionAttribution)) == 1
        assert db.scalar(select(func.count()).select_from(EcoSponsorshipCapture)) == 2
        campaign = db.get(EcoSponsoredPromotion, cid)
        campaign.ends_at = campaign.starts_at
        db.commit()
    assert not client.get(f"{API}/eco-experiences/1").json()["is_sponsored"]
    assert client.get(f"{admin}/analytics").status_code == 403
    actor["id"] = 4
    analytics = client.get(f"{admin}/analytics", params={"page_size": 1}).json()
    assert analytics["attributed_bookings"] == 1
    assert analytics["items"][0]["attributed_bookings"] == 1
    assert client.get(f"{admin}/analytics", params={
        "end_date": "2000-01-01",
    }).json()["attributed_bookings"] == 0
    audit = client.get(f"{admin}/campaigns/{cid}/payments").json()
    assert {row["status"] for row in audit} == {"applied", "reconciliation_required"}
    for _ in range(2):
        assert client.post(f"{admin}/campaigns/{cid}/expire").json()["campaign_status"] == "expired"
    actor["id"] = 12
    assert client.post(f"{root}/campaigns", json=payload).status_code == 201


def test_native_reservation_and_slot_closure_notifications_are_idempotent(eco_inventory):
    client, actor, engine, day = eco_inventory
    sid = slot(client, actor, day)["id"]
    booking = reservation(client, actor, sid, 1).json()
    actor["id"] = 12
    for _ in range(2):
        assert client.patch(f"{SLOTS}/{sid}/close").status_code == 200
    with Session(engine) as db:
        entries = db.scalars(select(Notification).where(
            Notification.message.contains(booking["reference"])
            | (Notification.title == f"Reservation {booking['reference']}"),
        )).all()
    requested = [item for item in entries if item.notification_type == "reservation_requested"]
    closed = [item for item in entries if item.notification_type == "reservation_slot_closed"]
    assert {item.user_id for item in requested} == {5, 12}
    assert len(closed) == 1 and closed[0].user_id == 5


def test_native_payment_and_refund_notifications_are_replay_safe(eco_inventory, enabled):
    client, actor, engine, _ = eco_inventory
    booking = confirm(eco_inventory)
    assert checkout(client, booking).status_code == 200
    for _ in range(2):
        assert callback(client, booking, "native-notification").status_code == 200
        assert client.post(f"{RESERVATIONS}/{booking['id']}/payments/cancel").status_code == 200
    with Session(engine) as db:
        refund_id = db.scalars(select(PaymentRefund.id)).one()
    actor["id"] = 4
    for _ in range(2):
        assert client.post(
            f"{API}/admin/eco-reservations/refunds/{refund_id}/decision",
            json={"decision": "approved"},
        ).status_code == 200
    with Session(engine) as db:
        rows = db.scalars(select(Notification).where(
            Notification.message.contains(booking["reference"])
        )).all()
    for event in ("online_payment_received", "cancelled_refund_requested", "refund_approved"):
        assert sorted(
            row.user_id for row in rows if row.notification_type == f"reservation_{event}"
        ) == [5, 12]


def test_provider_receives_payment_notice_for_each_booking(eco_inventory, enabled):
    client, actor, engine, day = eco_inventory
    inventory = slot(client, actor, day)
    bookings = []
    for traveler in (5, 13):
        booking = reservation(client, actor, inventory["id"], 1, traveler=traveler).json()
        actor["id"] = 12
        assert client.post(
            f"{API}/provider/bookings/{booking['id']}/action", json={"action": "accept"}
        ).status_code == 200
        actor["id"] = traveler
        assert checkout(client, booking).status_code == 200
        assert callback(client, booking, f"booked-{traveler}").status_code == 200
        bookings.append(booking)
    with Session(engine) as db:
        titles = set(db.scalars(select(Notification.title).where(
            Notification.user_id == 12,
            Notification.notification_type == "reservation_online_payment_received",
        )))
    assert titles == {f"Reservation {booking['reference']}" for booking in bookings}


def test_schedule_moves_notify_only_affected_travelers_once_per_change(eco_inventory):
    client, actor, engine, day = eco_inventory
    original = slot(client, actor, day)
    occupied = slot(client, actor, day + timedelta(days=1))
    booking = reservation(client, actor, original["id"], 1).json()
    endpoint = f"{SLOTS}/{original['id']}/schedule"
    initial = datetime.fromisoformat(original["starts_at"])
    first_move = (initial + timedelta(days=2)).isoformat()
    second_move = (initial + timedelta(days=3)).isoformat()
    first_request = {
        "expected_starts_at": original["starts_at"], "starts_at": first_move,
    }
    second_request = {"expected_starts_at": first_move, "starts_at": second_move}
    actor["id"] = 5
    assert client.patch(endpoint, json=first_request).status_code == 403
    actor["id"] = 12
    assert client.patch(
        endpoint, json={**first_request, "starts_at": occupied["starts_at"]}
    ).status_code == 409
    for request in (first_request, first_request, second_request, second_request):
        response = client.patch(endpoint, json=request)
        assert response.status_code == 200, response.text
        assert (
            datetime.fromisoformat(response.json()["starts_at"]).isoformat()
            == request["starts_at"]
        )
        assert response.json()["available_places"] == original["available_places"] - 1
    assert client.patch(endpoint, json=first_request).status_code == 409
    actor["id"] = 5
    detail = client.get(f"{RESERVATIONS}/{booking['id']}").json()
    assert datetime.fromisoformat(detail["starts_at"]).isoformat() == second_move
    with Session(engine) as db:
        rows = db.scalars(select(Notification).where(
            Notification.notification_type == "reservation_slot_rescheduled"
        )).all()
    assert [row.user_id for row in rows] == [5, 5]
    assert all(booking["reference"] in row.message for row in rows)
    assert len({row.message for row in rows}) == 2
    inbox = f"{API}/notifications"
    response = client.get(inbox, params={"unread_only": True})
    assert response.status_code == 200, response.text
    notices = response.json()["items"]
    assert {notice["message"] for notice in notices if (
        notice["notification_type"] == "reservation_slot_rescheduled"
    )} == {row.message for row in rows}
    assert any(notice["notification_type"] == "reservation_requested" for notice in notices)
    actor["id"] = 13
    assert all(booking["reference"] not in notice["message"] for notice in (
        client.get(inbox).json()["items"]
    ))
