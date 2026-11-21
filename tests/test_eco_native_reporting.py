from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_reservation import EcoAvailabilitySlot, EcoRefundReconciliation
from app.models.shared import BookingReference, PaymentRefund, PaymentTransaction, ProviderSettlement
from app.services import eco_reservations
from tests.test_eco_native_payments import callback, checkout, confirm, enabled
from tests.test_eco_reservations import API, RESERVATIONS, action, catalog, eco_inventory


def seed_legacy(engine, booking, *, status="completed"):
    with Session(engine) as db:
        native = db.get(EcoAvailabilitySlot, booking["slot_id"])
        slot = ActivitySlot(
            activity_id=1, eco_experience_id=1, starts_at=native.starts_at,
            ends_at=native.ends_at, capacity=4,
        )
        db.add(slot)
        db.flush()
        db.add(ActivityBooking(
            id=booking["id"], activity_slot_id=slot.id, eco_experience_id=1,
            user_id=5, participants=1, total_price=Decimal("100.00"),
            payment_method="pay_at_location", payment_status="paid", booking_status=status,
        ))
        db.commit()


def test_review_uses_explicit_native_source_and_canonical_reference(eco_inventory, monkeypatch):
    client, actor, engine, _ = eco_inventory
    booking = confirm(eco_inventory)
    seed_legacy(engine, booking)
    path = f"{API}/eco-experiences/1/reviews"
    payload = {"booking_id": booking["id"], "booking_source": "eco_experience", "rating": "4.75"}
    assert client.post(path, json=payload).status_code == 400
    actor["id"] = 13
    assert client.post(path, json=payload).status_code == 404
    assert action(client, actor, booking["id"], "cash").status_code == 200
    with Session(engine) as db:
        end = db.get(EcoAvailabilitySlot, booking["slot_id"]).ends_at
    monkeypatch.setattr(eco_reservations, "utc_now", lambda: end + timedelta(minutes=1))
    assert action(client, actor, booking["id"], "complete").status_code == 200
    actor["id"] = 5
    native = client.post(path, json=payload)
    assert native.status_code == 201, native.text
    assert native.json()["booking_source"] == "eco_experience"
    assert client.post(path, json=payload).status_code == 409
    payload.pop("booking_source")
    legacy = client.post(path, json=payload)
    assert legacy.status_code == 201, legacy.text
    assert client.post(path, json=payload).status_code == 409
    assert {item["booking_source"] for item in client.get(path).json()["items"]} == {
        "activity", "eco_experience",
    }


def test_native_ledger_separates_captures_cash_duplicates_and_refunds(
    eco_inventory, enabled, monkeypatch
):
    client, actor, engine, day = eco_inventory
    monkeypatch.setenv("ECO_NATIVE_REFUND_RECONCILIATION_ENABLED", "1")
    booking = confirm(eco_inventory)
    seed_legacy(engine, booking)
    assert checkout(client, booking).status_code == 200
    assert callback(client, booking, "report-capture").json()["status"] == "ok"
    assert callback(client, booking, "report-late").json()["status"] == "requires_reconciliation"
    assert client.post(f"{RESERVATIONS}/{booking['id']}/payments/cancel").status_code == 200
    # Reuse the released slot for a separate cash booking.
    actor["id"] = 13
    cash = client.post(RESERVATIONS, json={"slot_id": booking["slot_id"], "participants": 1})
    assert cash.status_code == 201, cash.text
    cash_id = cash.json()["id"]
    assert action(client, actor, cash_id, "accept").status_code == 200
    assert action(client, actor, cash_id, "cash").status_code == 200
    with Session(engine) as db:
        capture = db.scalar(select(PaymentTransaction).where(
            PaymentTransaction.gateway_transaction_id == "report-capture"
        ))
        capture.commission_amount = Decimal("100.00")
        refund = db.scalar(select(PaymentRefund).where(
            PaymentRefund.payment_transaction_id == capture.id
        ))
        refund.amount = Decimal("250.00")
        refund.refund_status = "completed"
        db.add(EcoRefundReconciliation(
            refund_id=refund.id, state="succeeded", gateway_refund_reference="765432",
        ))
        # A duplicate ledger import with the same gateway ID must not multiply revenue.
        db.add(PaymentTransaction(
            booking_reference_id=capture.booking_reference_id, user_id=capture.user_id,
            gateway_name=capture.gateway_name, gateway_transaction_id="REPORT-CAPTURE",
            amount=capture.amount, currency=capture.currency, payment_method="online",
            payment_status="completed", paid_at=capture.paid_at,
        ))
        provider_id = db.get(BookingReference, capture.booking_reference_id).provider_profile_id
        for state, evidence in (("paid", None), ("paid", "synthetic-payout"), ("approved", None)):
            db.add(ProviderSettlement(
                provider_profile_id=provider_id, period_start=day, period_end=day,
                gross_amount=Decimal("300.00"), platform_fee=Decimal("20.00"),
                net_payable=Decimal("280.00"), currency="USD",
                settlement_status=state, payout_reference=evidence,
            ))
        db.commit()
    earnings = f"{API}/eco-experiences/me/earnings/summary"
    report = f"{API}/admin/eco-reservations/report"
    assert client.get(report).status_code == 403
    assert client.get(earnings).status_code == 200
    provider = client.get(earnings).json()
    assert provider["gross_earnings"] == "100.00"
    native, = provider["native_financials"]
    assert native["bookings"] == 2
    assert native["captured_online"] == "3600.00"
    assert native["unapplied_captures"] == "3600.00"
    assert native["cash_receipts"] == "1800.00"
    assert native["pending_refunds"] == "3600.00"
    assert native["verified_refunds"] == "250.00"
    assert native["recorded_online_commission"] == "100.00"
    settlements, = provider["provider_settlements_by_currency"]
    assert settlements["currency"] == "USD"
    assert settlements["disbursed"] == "280.00"
    assert settlements["pending"] == "280.00"
    actor["id"] = 2
    assert client.get(earnings).json()["native_financials"] == []
    actor["id"] = 4
    first = client.get(report, params={"page_size": 1, "provider_id": provider_id})
    assert first.status_code == 200, first.text
    second = client.get(report, params={"page_size": 1, "page": 2, "provider_id": provider_id})
    assert first.json()["total"] == second.json()["total"] == 2
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
    cancelled = client.get(report, params={"booking_status": "cancelled"}).json()
    assert cancelled["total"] == 1
    assert {item["state"] for item in cancelled["items"][0]["refunds"]} == {
        "succeeded", "reconciliation_required",
    }
    assert client.get(report, params={"start_date": day.isoformat()}).json()["total"] == 0
    assert client.get(f"{API}/payments/admin/summary").json()["native_eco_financials"] == [native]
    audit = client.get(f"{API}/admin/eco-experiences/payments/audit")
    assert audit.status_code == 200, audit.text
    assert audit.json()["total"] == 1
    assert audit.json()["native_financials"] == [native]
