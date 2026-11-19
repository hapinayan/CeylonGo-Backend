from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.eco_experience import EcoExperience, EcoSafetyRule, EcoSeasonRestriction
from app.models.eco_reservation import EcoAvailabilitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import PaymentTransaction
from app.models.user import User
from app.services import eco_reservations
from app.services.provider_reservations import LOCAL
from tests import test_interactive_map

catalog = test_interactive_map.catalog
API = "/api/v1"
SLOTS = f"{API}/provider/eco-experiences/1/slots"
PUBLIC = f"{API}/eco-experiences/1/availability"
RESERVATIONS = f"{API}/eco-reservations"
BOOKINGS = f"{API}/provider/bookings"


@pytest.fixture
def eco_inventory(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        db.add(
            User(
                id=13,
                email="other-traveler@example.test",
                full_name="Other",
                role="traveler",
            )
        )
        db.get(ProviderProfile, 2).provider_type = "eco_tourism"
        db.get(ProviderProfile, 3).provider_type = "eco_tourism"
        db.commit()
    day = datetime.now(LOCAL).date() + timedelta(days=3)
    return client, actor, engine, day


def slot(client, actor, day, capacity=4, at=time(9)):
    actor["id"] = 12
    start = datetime.combine(day, at, LOCAL)
    result = client.post(
        SLOTS, json={"starts_at": start.isoformat(), "capacity": capacity}
    )
    assert result.status_code == 201, result.text
    return result.json()


def reservation(client, actor, slot_id, participants, traveler=5, **kwargs):
    actor["id"] = traveler
    return client.post(
        RESERVATIONS, json={"slot_id": slot_id, "participants": participants, **kwargs}
    )


def action(client, actor, booking_id, value, provider=12):
    actor["id"] = provider
    return client.post(f"{BOOKINGS}/{booking_id}/action", json={"action": value})


def test_per_person_lifecycle_cash_history_and_access(eco_inventory, monkeypatch):
    client, actor, engine, day = eco_inventory
    inventory = slot(client, actor, day)
    sid = inventory["id"]
    assert inventory["starts_at"] == f"{day}T03:30:00Z"
    assert (
        client.get(PUBLIC, params={"day": day.isoformat()}).json()[0][
            "available_places"
        ]
        == 4
    )
    first = reservation(client, actor, sid, 2)
    assert first.status_code == 201, first.text
    data = first.json()
    booking_id = data["id"]
    assert data["payment_method"] == "pay_at_location"
    assert data["total"] == "3600.00"
    assert reservation(client, actor, sid, 1).status_code == 409
    assert reservation(client, actor, sid, 3, traveler=13).status_code == 409
    assert client.get(f"{RESERVATIONS}/{booking_id}").status_code == 404
    assert action(client, actor, booking_id, "accept", provider=2).status_code == 404
    assert action(client, actor, booking_id, "accept", provider=3).status_code == 403
    assert action(client, actor, booking_id, "cash").status_code == 409
    assert action(client, actor, booking_id, "accept").json()["status"] == "confirmed"
    assert action(client, actor, booking_id, "cash").json()["payment_status"] == "paid"
    assert action(client, actor, booking_id, "cash").status_code == 409
    assert action(client, actor, booking_id, "cancel").status_code == 409
    assert action(client, actor, booking_id, "complete").status_code == 409
    dashboard = client.get(f"{API}/provider/dashboard").json()
    assert dashboard["bookings"]["upcoming"] == 1
    assert dashboard["cash_receipts"] == [{"currency": "LKR", "amount": "3600.00"}]
    assert dashboard["finance"] is None
    monkeypatch.setattr(
        eco_reservations,
        "utc_now",
        lambda: (
            datetime.fromisoformat(inventory["ends_at"])
            .astimezone(UTC)
            .replace(tzinfo=None)
        ),
    )
    assert action(client, actor, booking_id, "complete").json()["status"] == "completed"
    history = client.get(f"{BOOKINGS}/{booking_id}").json()["history"]
    assert {event["status"] for event in history} == {
        "pending",
        "confirmed",
        "completed",
    }
    with Session(engine) as db:
        assert len(db.scalars(select(PaymentTransaction)).all()) == 1


def test_private_groups_rejection_cancellation_and_close_release_once(eco_inventory):
    client, actor, engine, day = eco_inventory
    with Session(engine) as db:
        db.get(EcoExperience, 1).pricing_type = "per_group"
        db.commit()
    sid = slot(client, actor, day)["id"]
    first = reservation(client, actor, sid, 2).json()
    assert first["total"] == "1800.00"
    assert reservation(client, actor, sid, 1, traveler=13).status_code == 409
    assert action(client, actor, first["id"], "reject").json()["status"] == "rejected"
    assert action(client, actor, first["id"], "reject").status_code == 409
    second = reservation(client, actor, sid, 1, traveler=13).json()
    cancel = f"{RESERVATIONS}/{second['id']}/cancel"
    assert client.post(cancel, json={}).json()["status"] == "cancelled"
    assert client.post(cancel, json={}).status_code == 409
    actor["id"] = 12
    assert client.patch(f"{SLOTS}/{sid}/close").json()["is_open"] is False
    assert client.get(PUBLIC).json() == []
    assert reservation(client, actor, sid, 1).status_code == 409
    with Session(engine) as db:
        assert db.get(EcoAvailabilitySlot, sid).reserved_count == 0


def test_local_date_restriction_and_booking_safety_rules(eco_inventory):
    client, actor, engine, day = eco_inventory
    sid = slot(client, actor, day, at=time(1))["id"]
    with Session(engine) as db:
        db.add(
            EcoSafetyRule(
                eco_experience_id=1,
                title="Stay on trail",
                description="Follow the marked trail",
                rule_type="environmental_conduct",
                is_mandatory=True,
            )
        )
        db.commit()
    assert reservation(client, actor, sid, 1).status_code == 422
    with Session(engine) as db:
        db.add(
            EcoSeasonRestriction(
                eco_experience_id=1,
                start_date=day,
                end_date=day,
                restriction_type="park_closure",
                reason="Park closed",
            )
        )
        db.commit()
    assert client.get(PUBLIC).json() == []
    blocked = reservation(client, actor, sid, 1, accepts_safety_rules=True)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "ECO_SEASON_RESTRICTED"
