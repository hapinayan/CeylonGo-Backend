from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.payhere import payhere_service
from app.models.provider_profile import ProviderProfile
from app.models.shared import (
    BookingReference,
    BookingStatusHistory,
    PaymentRefund,
    PaymentTransaction,
)
from app.models.tour_guide import (
    TourGuideAvailability,
    TourGuideBooking,
    TourGuidePackage,
)
from app.models.user import User
from app.services import guide_payments, provider_reservations
from app.services.guide_booking import LOCAL
from tests import test_interactive_map

catalog = test_interactive_map.catalog
GUIDE = "/api/v1/tour-guide-reservations"
PROVIDER = "/api/v1/provider/bookings"
CALLBACK = "/api/v1/payments/payhere/notify"


@pytest.fixture
def guide_inventory(catalog, monkeypatch):
    client, actor, engine = catalog
    day = datetime.now(LOCAL).date() + timedelta(days=3)
    with Session(engine) as db:
        package = TourGuidePackage(
            tour_guide_id=1,
            title="Two-day tour",
            description="Local tour",
            duration_hours=30,
            price=Decimal("1250.50"),
            max_people=4,
            meeting_point="Visitor centre",
        )
        db.add_all(
            [
                package,
                TourGuideAvailability(tour_guide_id=1, available_date=day),
                TourGuideAvailability(
                    tour_guide_id=1,
                    available_date=day + timedelta(days=1),
                    start_time=time(0),
                    end_time=time(12),
                ),
                User(
                    id=13,
                    full_name="Another traveler",
                    email="guide-traveler@example.test",
                    role="traveler",
                ),
            ]
        )
        db.get(ProviderProfile, 2).provider_type = "tour_guide"
        db.commit()
        package_id = package.id
    monkeypatch.setattr(
        payhere_service,
        "verify_ipn_signature",
        lambda **kwargs: kwargs["received_signature"] == "fixture",
    )
    start = datetime.combine(day, time(6), LOCAL)
    return (
        client,
        actor,
        engine,
        {
            "guide_id": 1,
            "package_id": package_id,
            "people": 2,
            "starts_at": start.isoformat(),
            "payment_method": "online",
        },
    )


def book(client, actor, payload, traveler=5):
    actor["id"] = traveler
    return client.post(GUIDE, json=payload)


def act(client, actor, booking_id, action, owner=8):
    actor["id"] = owner
    return client.post(f"{PROVIDER}/{booking_id}/action", json={"action": action})


def checkout(client, actor, booking_id, traveler=5):
    actor["id"] = traveler
    return client.post(f"{GUIDE}/{booking_id}/payments/initiate")


def callback(client, data, **overrides):
    return client.post(
        CALLBACK,
        data={
            "merchant_id": payhere_service.merchant_id,
            "order_id": data["reference"],
            "payment_id": "fixture-capture",
            "payhere_amount": data["total"],
            "payhere_currency": data["currency"],
            "status_code": "2",
            "md5sig": "fixture",
            **overrides,
        },
    )


def test_multiday_online_checkout_replay_and_paid_cancellation(guide_inventory):
    client, actor, engine, payload = guide_inventory
    response = book(client, actor, payload)
    assert response.status_code == 201, response.text
    booking = response.json()
    booking_id = booking["id"]
    assert booking["total"] == "2501.00"
    assert datetime.fromisoformat(booking["ends_at"]) - datetime.fromisoformat(
        booking["starts_at"]
    ) == timedelta(hours=30)
    assert book(client, actor, payload, traveler=13).status_code == 409
    assert checkout(client, actor, booking_id, traveler=13).status_code == 404
    assert checkout(client, actor, booking_id).status_code == 409
    assert callback(client, booking).status_code == 409
    assert act(client, actor, booking_id, "accept", owner=2).status_code == 404
    accepted = act(client, actor, booking_id, "accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "awaiting_payment"
    deadline = accepted.json()["payment_expires_at"]
    assert act(client, actor, booking_id, "accept").status_code == 409
    queued = client.get(PROVIDER, params={"state": "awaiting_payment"}).json()["items"]
    assert queued[0]["payment_expires_at"] == deadline
    assert queued[0]["payment_method"] == "online"
    assert "cash" not in queued[0]["actions"]
    assert (
        checkout(client, actor, booking_id).json()["order_id"] == booking["reference"]
    )
    assert checkout(client, actor, booking_id).json()["amount"] == booking["total"]
    status_url = f"{GUIDE}/{booking_id}/payments/status"
    assert client.get(status_url).json()["payment_expires_at"] == deadline
    for invalid in (
        {"md5sig": "wrong"},
        {"merchant_id": "wrong"},
        {"payhere_amount": "NaN"},
        {"payhere_amount": "1.00"},
        {"payhere_currency": "USD"},
        {"status_code": "unsupported"},
        {"payment_id": ""},
    ):
        assert callback(client, booking, **invalid).status_code == 400
    assert (
        callback(client, booking, status_code="-2").json()["payment_status"] == "failed"
    )
    assert client.get(status_url).json()["booking_status"] == "awaiting_payment"
    assert client.get(status_url).json()["payment_status"] == "failed"
    assert checkout(client, actor, booking_id).status_code == 200
    retried = client.get(status_url).json()
    assert retried["payment_status"] == "pending"
    assert retried["payment_expires_at"] == deadline
    assert callback(client, booking).json() == {
        "status": "ok",
        "payment_status": "paid",
    }
    assert callback(client, booking).json()["status"] == "already_processed"
    assert (
        callback(client, booking, status_code="-2").json()["status"]
        == "already_processed"
    )
    assert client.get(status_url).json()["booking_status"] == "confirmed"
    original_receipt = client.get(status_url).json()["transaction_id"]
    assert (
        client.post(
            f"/api/v1/reservations/tour_guide/{booking_id}/cancel", json={}
        ).status_code
        == 409
    )
    assert act(client, actor, booking_id, "cancel").status_code == 409
    with Session(engine) as db:
        row = db.get(TourGuideBooking, booking_id)
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.target_booking_id == booking_id
            )
        )
        assert row.payment_status == ref.payment_status == "paid"
        assert row.booking_status == ref.booking_status == "confirmed"
        assert len(db.scalars(select(PaymentTransaction)).all()) == 1
        assert db.scalars(
            select(BookingStatusHistory.to_status).order_by(BookingStatusHistory.id)
        ).all() == [
            "pending",
            "awaiting_payment",
            "awaiting_payment",
            "confirmed",
        ]
    assert (
        callback(client, booking, payment_id="distinct-real-capture").json()["status"]
        == "requires_reconciliation"
    )
    assert (
        callback(client, booking, payment_id="distinct-real-capture").json()["status"]
        == "already_processed"
    )
    actor["id"] = 5
    assert client.get(status_url).json()["transaction_id"] == original_receipt
    with Session(engine) as db:
        assert len(db.scalars(select(PaymentRefund)).all()) == 1
        assert db.get(TourGuideBooking, booking_id).booking_status == "confirmed"


def test_expired_hold_releases_on_new_booking_and_late_capture_cannot_reopen(
    guide_inventory,
    monkeypatch,
):
    client, actor, engine, payload = guide_inventory
    first = book(client, actor, payload).json()
    booking_id = first["id"]
    accepted = act(client, actor, booking_id, "accept").json()
    checkout(client, actor, booking_id).raise_for_status()
    deadline = datetime.fromisoformat(accepted["payment_expires_at"]).replace(
        tzinfo=None
    )
    monkeypatch.setattr(provider_reservations, "utc_now", lambda: deadline)
    monkeypatch.setattr(guide_payments, "utc_now", lambda: deadline)
    second = book(client, actor, payload, traveler=13)
    assert second.status_code == 201, second.text
    assert checkout(client, actor, booking_id).status_code == 409
    assert callback(client, first).json()["status"] == "requires_reconciliation"
    assert callback(client, first).json()["status"] == "already_processed"
    status = client.get(f"{GUIDE}/{booking_id}/payments/status").json()
    assert (status["booking_status"], status["payment_status"]) == (
        "expired",
        "refund_pending",
    )
    assert status["payment_expires_at"] == accepted["payment_expires_at"]
    with Session(engine) as db:
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.target_booking_id == booking_id
            )
        )
        assert (ref.booking_status, ref.payment_status) == ("expired", "refund_pending")
        assert len(db.scalars(select(PaymentRefund)).all()) == 1
        assert db.get(TourGuideBooking, second.json()["id"]).booking_status == "pending"


def test_checkout_expiry_is_persisted_at_exact_deadline(guide_inventory, monkeypatch):
    client, actor, _, payload = guide_inventory
    booking = book(client, actor, payload).json()
    booking_id = booking["id"]
    start = (
        datetime.fromisoformat(payload["starts_at"])
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    monkeypatch.setattr(
        provider_reservations, "utc_now", lambda: start - timedelta(minutes=5)
    )
    accepted = act(client, actor, booking_id, "accept").json()
    assert (
        datetime.fromisoformat(accepted["payment_expires_at"]).replace(tzinfo=None)
        == start
    )
    monkeypatch.setattr(guide_payments, "utc_now", lambda: start)
    assert checkout(client, actor, booking_id).status_code == 409
    assert (
        client.get(f"{GUIDE}/{booking_id}/payments/status").json()["booking_status"]
        == "expired"
    )


@pytest.mark.parametrize("change", ["gap", "blackout"])
def test_multiday_rejects_missing_overnight_coverage_or_blackout(
    guide_inventory, change
):
    client, actor, engine, payload = guide_inventory
    local_day = datetime.fromisoformat(payload["starts_at"]).date() + timedelta(days=1)
    with Session(engine) as db:
        if change == "gap":
            window = db.scalar(
                select(TourGuideAvailability).where(
                    TourGuideAvailability.available_date == local_day
                )
            )
            window.start_time = time(1)
        else:
            db.add(
                TourGuideAvailability(
                    tour_guide_id=1,
                    available_date=local_day,
                    start_time=time(8),
                    end_time=time(9),
                    is_available=False,
                )
            )
        db.commit()
    assert book(client, actor, payload).status_code == 409


def test_per_group_price_is_once_for_whole_package(guide_inventory):
    client, actor, engine, payload = guide_inventory
    with Session(engine) as db:
        db.get(TourGuidePackage, payload["package_id"]).pricing_type = "per_group"
        db.commit()
    response = book(client, actor, {**payload, "payment_method": "pay_at_location"})
    assert response.status_code == 201, response.text
    assert response.json()["total"] == "1250.50"
    assert response.json()["payment_method"] == "pay_at_location"
    assert (
        act(client, actor, response.json()["id"], "accept").json()["status"]
        == "confirmed"
    )


def test_midnight_endpoint_does_not_require_availability_on_next_day(guide_inventory):
    client, actor, engine, payload = guide_inventory
    next_day = datetime.fromisoformat(payload["starts_at"]).date() + timedelta(days=1)
    with Session(engine) as db:
        db.get(TourGuidePackage, payload["package_id"]).duration_hours = 18
        db.add(
            TourGuideAvailability(
                tour_guide_id=1, available_date=next_day, is_available=False
            )
        )
        db.commit()
    result = book(client, actor, payload)
    assert result.status_code == 201, result.text
    assert datetime.fromisoformat(result.json()["ends_at"]).astimezone(
        LOCAL
    ).time() == time(0)
