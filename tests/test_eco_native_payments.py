from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import md5
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.core.payhere import payhere_service
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.eco_reservation import (
    EcoAgeLimit, EcoAvailabilitySlot, EcoCancellationPolicy, EcoSlotClearance,
)
from app.models.shared import BookingReference, PaymentRefund, PaymentTransaction
from app.schemas.eco_reservation import EcoRefundReconcile
from app.services import eco_native_payments, eco_reservations
from tests.test_eco_reservations import (
    API, RESERVATIONS, action, catalog, eco_inventory, reservation, slot,
)


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ECO_NATIVE_PAYMENTS_ENABLED", "1")
    monkeypatch.setenv("ECO_NATIVE_PAID_CANCELLATION_ENABLED", "1")
    monkeypatch.setattr(payhere_service, "merchant_id", "offline-eco-test")
    monkeypatch.setattr(payhere_service, "merchant_secret", "offline-synthetic-secret")


def confirm(inventory):
    client, actor, _, day = inventory
    sid = slot(client, actor, day)["id"]
    booking = reservation(client, actor, sid, 2).json()
    result = action(client, actor, booking["id"], "accept")
    assert result.status_code == 200
    actor["id"] = 5
    return booking


def checkout(client, booking):
    return client.post(f"{RESERVATIONS}/{booking['id']}/payments/initiate")


@pytest.mark.parametrize("percent", [0, 35])
def test_late_refund_uses_immutable_snapshot(eco_inventory, enabled, monkeypatch, percent):
    client, actor, engine, _ = eco_inventory
    monkeypatch.setenv("ECO_NATIVE_POLICY_SNAPSHOTS_ENABLED", "1")
    monkeypatch.setenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED", "1")
    with Session(engine) as db:
        db.add(EcoCancellationPolicy(eco_experience_id=1, late_refund_percent=percent))
        db.commit()
    booking = confirm(eco_inventory)
    with Session(engine) as db:
        db.get(EcoCancellationPolicy, 1).late_refund_percent = 100
        db.commit()
    assert checkout(client, booking).status_code == 200
    assert callback(client, booking, "snapshot-capture").json()["status"] == "ok"
    start = datetime.fromisoformat(booking["starts_at"]).replace(tzinfo=None)
    monkeypatch.setattr(eco_reservations, "utc_now", lambda: start - timedelta(minutes=1))
    url = f"{RESERVATIONS}/{booking['id']}/payments/cancel"
    for _ in range(2):
        result = client.post(url)
        assert result.status_code == 200, result.text
        assert result.json()["payment_status"] == ("refund_pending" if percent else "no_refund_due")
    with Session(engine) as db:
        assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == 0
        refunds = db.scalars(select(PaymentRefund)).all()
        if not percent:
            assert refunds == []
            return
        assert len(refunds) == 1
        assert refunds[0].amount == Decimal("1260.00")
        refund_id = refunds[0].id
    base = f"{API}/admin/eco-reservations/refunds/{refund_id}"
    actor["id"] = 4
    assert client.post(f"{base}/decision", json={"decision": "approved"}).status_code == 200
    evidence = {"state": "processing", "capture_reference": "snapshot-capture",
                "amount": "1260.00", "currency": "LKR"}
    assert client.post(f"{base}/reconcile", json=evidence).status_code == 200
    evidence.update(state="succeeded", refund_reference="900123", amount="1.00")
    assert client.post(f"{base}/reconcile", json=evidence).status_code == 409
    evidence["amount"] = "1260.00"
    for _ in range(2):
        assert client.post(f"{base}/reconcile", json=evidence).status_code == 200
    actor["id"] = 5
    assert client.get(f"{RESERVATIONS}/{booking['id']}").json()["payment_status"] == "partially_refunded"


def test_weather_cascade_replays_without_releasing_twice(eco_inventory):
    client, actor, engine, day = eco_inventory
    booking = confirm(eco_inventory)
    with Session(engine) as db:
        rule = EcoSeasonRestriction(eco_experience_id=1, start_date=day, end_date=day,
                                    restriction_type="weather_alert", reason="Offline exercise")
        db.add(rule)
        db.commit()
        rule_id = rule.id
    url = f"{API}/admin/eco-experiences/1/restrictions/{rule_id}/cancel-slots"
    assert client.post(url).status_code == 403
    actor["id"] = 4
    assert client.post(url).json() == {"slots_closed": 1, "reservations_cancelled": 1}
    assert client.post(url).json() == {"slots_closed": 0, "reservations_cancelled": 0}
    with Session(engine) as db:
        assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == 0


def test_booking_needs_current_verified_slot_clearance_and_maximum_age(eco_inventory, monkeypatch):
    client, actor, engine, day = eco_inventory
    sid = slot(client, actor, day)["id"]
    monkeypatch.setenv("ECO_NATIVE_SAFETY_RECORDS_ENABLED", "1")
    with Session(engine) as db:
        listing = db.get(EcoExperience, 1)
        listing.protected_area_permit_required = listing.certified_guide_required = True
        db.add(EcoAgeLimit(eco_experience_id=1, maximum_age=65))
        db.commit()
    assert reservation(client, actor, sid, 1, participant_ages=[32]).status_code == 409
    with Session(engine) as db:
        end = db.get(EcoAvailabilitySlot, sid).ends_at
        for kind in ("permit", "guide"):
            db.add(EcoSlotClearance(slot_id=sid, kind=kind, verified_by=4,
                                    evidence_reference="synthetic", valid_until=end))
        db.commit()
    assert reservation(client, actor, sid, 1, participant_ages=[66]).status_code == 422
    assert reservation(client, actor, sid, 1, participant_ages=[65]).status_code == 201


def test_parallel_capture_cancel_and_refund_replays(eco_inventory, enabled, monkeypatch, tmp_path):
    client, _, source, _ = eco_inventory
    booking = confirm(eco_inventory)
    assert checkout(client, booking).status_code == 200
    monkeypatch.setenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED", "1")
    engine = create_engine(f"sqlite:///{tmp_path / 'eco-race.sqlite'}")

    @event.listens_for(engine, "connect")
    def install_fixture_clock(connection, _):
        connection.create_function(
            "sysutcdatetime", 0, lambda: datetime.now(UTC).replace(tzinfo=None).isoformat(" ")
        )

    # File-backed sessions permit independent connections. Emulate the SQL Server
    # transaction-owned schedule lock, which SQLite does not implement.
    with source.connect() as original, engine.connect() as target:
        original.connection.driver_connection.backup(target.connection.driver_connection)
    schedule_lock = Lock()

    def lock_schedule(db, _experience_id):
        schedule_lock.acquire()
        db.info["eco_test_lock"] = True

    def release_lock(db, transaction):
        if transaction.parent is None and db.info.pop("eco_test_lock", False):
            schedule_lock.release()

    monkeypatch.setattr(eco_reservations, "lock_schedule", lock_schedule)
    event.listen(Session, "after_transaction_end", release_lock)
    traveler = SimpleNamespace(id=5)
    admin = SimpleNamespace(id=4)

    def race(call):
        ready = Barrier(2, timeout=5)

        def run():
            with Session(engine) as db:
                ready.wait()
                return call(db)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(run), pool.submit(run)
            return first.result(timeout=15), second.result(timeout=15)

    try:
        with Session(engine) as db:
            reference = db.scalar(select(BookingReference.id).where(
                BookingReference.booking_reference == booking["reference"]
            ))
        outcomes = race(lambda db: eco_native_payments.capture(
            db, reference, "parallel-capture", booking["total"], "LKR", "2"
        ))
        assert {result["status"] for result in outcomes} == {"ok", "already_processed"}
        race(lambda db: eco_native_payments.cancel_paid(db, traveler, booking["id"]))
        with Session(engine) as db:
            refund = db.scalars(select(PaymentRefund)).one()
            refund_id = refund.id
            assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == 0
            eco_native_payments.decide_refund(db, admin, refund_id, "approved")
        evidence = {
            "capture_reference": "parallel-capture", "amount": booking["total"],
            "currency": "LKR",
        }
        with Session(engine) as db:
            eco_native_payments.reconcile_refund(
                db, admin, refund_id, EcoRefundReconcile(state="processing", **evidence)
            )
        successes = race(lambda db: eco_native_payments.reconcile_refund(
            db, admin, refund_id, EcoRefundReconcile(
                state="succeeded", refund_reference="900124", **evidence
            )
        ))
        assert all(outcome["state"] == "succeeded" for outcome in successes)
    finally:
        event.remove(Session, "after_transaction_end", release_lock)
        engine.dispose()


def callback(client, booking, payment_id, *, code="2", amount=None, currency=None,
             corrupt_signature=False):
    merchant = payhere_service.merchant_id
    order = booking["reference"]
    amount = amount or booking["total"]
    currency = currency or booking["currency"]
    secret_hash = md5(payhere_service.merchant_secret.encode()).hexdigest().upper()
    signature = md5(
        f"{merchant}{order}{amount}{currency}{code}{secret_hash}".encode()
    ).hexdigest().upper()
    return client.post(f"{API}/payments/payhere/notify", data={
        "merchant_id": merchant, "order_id": order, "payment_id": payment_id,
        "payhere_amount": amount, "payhere_currency": currency, "status_code": code,
        "md5sig": "invalid" if corrupt_signature else signature,
    })


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_online_capture_paid_cancellation_and_refund_decision(
    eco_inventory, enabled, decision
):
    client, actor, engine, _ = eco_inventory
    booking = confirm(eco_inventory)
    actor["id"] = 13
    assert checkout(client, booking).status_code == 404
    actor["id"] = 5
    for _ in range(2):
        response = checkout(client, booking)
        assert response.status_code == 200, response.text
        assert response.json()["order_id"] == booking["reference"]
        assert response.json()["amount"] == booking["total"]
    assert action(client, actor, booking["id"], "cash").status_code == 409
    actor["id"] = 5
    assert callback(client, booking, "capture1", corrupt_signature=True).status_code == 400
    assert callback(client, booking, "capture1", amount="0.01").status_code == 400
    assert callback(client, booking, "capture1", currency="USD").status_code == 400
    assert callback(client, booking, "capture1").json()["status"] == "ok"
    assert callback(client, booking, "CAPTURE1").json()["status"] == "already_processed"
    assert callback(client, booking, "capture2").json()["status"] == "requires_reconciliation"
    assert callback(client, booking, "capture2").json()["status"] == "already_processed"
    status = client.get(f"{RESERVATIONS}/{booking['id']}/payments/status").json()
    assert status["payment_status"] == "paid"
    assert status["payment_method"] == "online"

    for _ in range(2):
        result = client.post(f"{RESERVATIONS}/{booking['id']}/payments/cancel")
        assert result.status_code == 200, result.text
        assert result.json()["payment_status"] == "refund_pending"
    with Session(engine) as db:
        refunds = db.scalars(select(PaymentRefund).order_by(PaymentRefund.id)).all()
        assert len(refunds) == 2
        assert all(x.refund_status == "pending" for x in refunds)
        assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == 0
        refund_id = refunds[-1].id
    endpoint = f"{API}/admin/eco-reservations/refunds/{refund_id}/decision"
    assert client.post(endpoint, json={"decision": decision}).status_code == 403
    actor["id"] = 4
    for _ in range(2):
        result = client.post(endpoint, json={"decision": decision})
        assert result.status_code == 200, result.text
        assert result.json()["refund_status"] == decision
        assert result.json()["processed_at"] is None
    assert client.post(endpoint, json={"decision": "completed"}).status_code == 422
    actor["id"] = 5
    refunds = client.get(f"{RESERVATIONS}/{booking['id']}/payments/refunds").json()
    assert refunds[-1]["refund_status"] == decision
    assert refunds[-1]["amount"] == booking["total"]
    status = client.get(f"{RESERVATIONS}/{booking['id']}/payments/status").json()
    expected = "refund_rejected" if decision == "rejected" else "refund_pending"
    assert status["payment_status"] == expected


def test_paid_cancellation_after_free_cutoff_leaves_hold_and_receipt(
    eco_inventory, enabled, monkeypatch
):
    from datetime import datetime

    client, _, engine, _ = eco_inventory
    booking = confirm(eco_inventory)
    assert checkout(client, booking).status_code == 200
    assert callback(client, booking, "cutoff-capture").json()["status"] == "ok"
    start = datetime.fromisoformat(booking["starts_at"]).replace(tzinfo=None)
    monkeypatch.setattr(eco_reservations, "utc_now", lambda: start - timedelta(minutes=1))
    assert client.post(f"{RESERVATIONS}/{booking['id']}/payments/cancel").status_code == 409
    with Session(engine) as db:
        assert db.scalar(select(PaymentRefund)) is None
        assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == 2


@pytest.mark.parametrize("scenario", ["cancelled", "failed", "expired"])
def test_unapplied_successful_capture_is_queued(
    eco_inventory, enabled, monkeypatch, scenario
):
    client, actor, engine, _ = eco_inventory
    booking = confirm(eco_inventory)
    assert checkout(client, booking).status_code == 200
    if scenario == "cancelled":
        assert client.post(f"{RESERVATIONS}/{booking['id']}/cancel", json={}).status_code == 200
    elif scenario == "failed":
        assert callback(client, booking, "late", code="-2").json()["status"] == "ignored"
    else:
        later = eco_reservations.utc_now() + timedelta(minutes=31)
        monkeypatch.setattr(eco_reservations, "utc_now", lambda: later)
        status = client.get(f"{RESERVATIONS}/{booking['id']}/payments/status").json()
        assert status["payment_status"] == "expired"
    assert callback(client, booking, "late").json()["status"] == "requires_reconciliation"
    with Session(engine) as db:
        assert db.scalar(select(PaymentRefund)).refund_status == "pending"
        ref = db.scalar(select(BookingReference).where(
            BookingReference.booking_reference == booking["reference"]
        ))
        assert ref.payment_status == "refund_pending"
        expected_holds = 0 if scenario == "cancelled" else 2
        assert db.get(EcoAvailabilitySlot, booking["slot_id"]).reserved_count == expected_holds
        assert len(db.scalars(select(PaymentTransaction)).all()) == 1


def test_checkout_default_closed_and_native_age_and_permit_checks(eco_inventory, monkeypatch):
    client, actor, engine, day = eco_inventory
    monkeypatch.delenv("ECO_NATIVE_PAYMENTS_ENABLED", raising=False)
    sid = slot(client, actor, day)["id"]
    with Session(engine) as db:
        listing = db.get(EcoExperience, 1)
        listing.min_age = 18
        db.commit()
    assert reservation(client, actor, sid, 2, participant_ages=[18, 17]).status_code == 422
    assert reservation(client, actor, sid, 2, participant_ages=[18]).status_code == 422
    with Session(engine) as db:
        listing = db.get(EcoExperience, 1)
        listing.protected_area_permit_required = True
        db.commit()
    assert reservation(client, actor, sid, 2, participant_ages=[18, 19]).status_code == 409
    with Session(engine) as db:
        db.get(EcoExperience, 1).protected_area_permit_required = False
        db.commit()
    booking = reservation(client, actor, sid, 2, participant_ages=[18, 19]).json()
    assert action(client, actor, booking["id"], "accept").status_code == 200
    actor["id"] = 5
    assert checkout(client, booking).status_code == 503
