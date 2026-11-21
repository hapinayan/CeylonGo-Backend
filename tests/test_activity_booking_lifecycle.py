from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.activity import Activity
from app.models.activity_booking import ActivityBooking, ActivityBookingParticipant
from app.models.activity_slot import ActivitySlot
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, PaymentTransaction
from tests import test_interactive_map

catalog = test_interactive_map.catalog
API = "/api/v1"
PROVIDER_BOOKINGS = f"{API}/provider/bookings"
TRAVELER_BOOKINGS = f"{API}/activity-bookings"


def create_future_slot(db: Session, activity_id: int = 1, capacity: int = 10, hours_ahead: int = 48) -> ActivitySlot:
    now = datetime.now(UTC).replace(tzinfo=None)
    slot = ActivitySlot(
        activity_id=activity_id,
        starts_at=now + timedelta(hours=hours_ahead),
        ends_at=now + timedelta(hours=hours_ahead + 2),
        capacity=capacity,
        reserved_count=0,
        is_active=True,
    )
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def test_booking_creation_with_participants(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        slot_id = slot.id

    actor["id"] = 5  # traveler
    payload = {
        "activity_slot_id": slot_id,
        "participants": 2,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Alice Traveler",
                "age": 28,
                "emergency_contact_name": "Bob Emergency",
                "emergency_contact_phone": "+94771234567",
                "special_requirements": "Vegetarian",
                "waiver_signed": True,
            },
            {
                "full_name": "Charlie Traveler",
                "age": 25,
                "emergency_contact_name": "Bob Emergency",
                "emergency_contact_phone": "+94771234567",
                "special_requirements": None,
                "waiver_signed": True,
            },
        ],
    }

    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["booking_status"] == "pending"
    assert data["payment_status"] == "unpaid"
    assert data["participants"] == 2
    assert len(data["participants_list"]) == 2
    assert data["participants_list"][0]["full_name"] == "Alice Traveler"
    assert data["participants_list"][0]["age"] == 28
    assert data["participants_list"][0]["waiver_signed"] is True

    # Verify slot capacity reserved
    with Session(engine) as db:
        updated_slot = db.get(ActivitySlot, slot_id)
        assert updated_slot.reserved_count == 2

        # Verify DB participant records
        parts = db.query(ActivityBookingParticipant).filter_by(activity_booking_id=data["id"]).all()
        assert len(parts) == 2
        assert {p.full_name for p in parts} == {"Alice Traveler", "Charlie Traveler"}


def test_booking_creation_participant_count_mismatch(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        slot_id = slot.id

    actor["id"] = 5  # traveler
    payload = {
        "activity_slot_id": slot_id,
        "participants": 2,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Solo Traveler",
                "age": 30,
            }
        ],
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 422
    assert "Number of participants in participants_list must match participants count" in res.text


def test_booking_creation_empty_name_validation(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        slot_id = slot.id

    actor["id"] = 5
    payload = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "   ",
                "age": 20,
            }
        ],
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 422
    assert "full_name" in res.text


def test_booking_creation_minimum_age_enforcement(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        # Create an activity requiring minimum age 18
        activity = Activity(
            title="Adult Scuba Diving",
            description="Deep sea diving adventure",
            category="cultural",
            district="Colombo District",
            city="Colombo",
            meeting_point="Harbor Gate",
            duration_minutes=120,
            minimum_age=18,
            pricing_type="per_person",
            price=Decimal("500.00"),
            currency="LKR",
            provider_profile_id=1,
            approval_status="approved",
            is_active=True,
        )
        db.add(activity)
        db.commit()
        db.refresh(activity)

        slot = create_future_slot(db, activity_id=activity.id, capacity=4)
        slot_id = slot.id

    actor["id"] = 5  # traveler

    # Underage participant should fail
    payload_underage = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Young Teen",
                "age": 14,
            }
        ],
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload_underage)
    assert res.status_code == 422
    assert "below the minimum age of 18" in res.text

    # Adult participant succeeds
    payload_adult = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Adult Diver",
                "age": 22,
            }
        ],
    }
    res2 = client.post(TRAVELER_BOOKINGS, json=payload_adult)
    assert res2.status_code == 201
    assert res2.json()["booking_status"] == "pending"


def test_booking_creation_adventure_waiver_and_emergency_contact(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        adv_activity = Activity(
            title="White Water Rafting",
            description="Extreme adventure rafting",
            category="adventure",
            district="Colombo District",
            city="Kitulgala",
            meeting_point="Rafting Base",
            duration_minutes=90,
            minimum_age=12,
            pricing_type="per_person",
            price=Decimal("450.00"),
            currency="LKR",
            provider_profile_id=1,
            approval_status="approved",
            is_active=True,
        )
        db.add(adv_activity)
        db.commit()
        db.refresh(adv_activity)

        slot = create_future_slot(db, activity_id=adv_activity.id, capacity=6)
        slot_id = slot.id

    actor["id"] = 5

    # Missing waiver
    payload_no_waiver = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Risk Taker",
                "age": 25,
                "emergency_contact_name": "Mom",
                "emergency_contact_phone": "+94770000000",
                "waiver_signed": False,
            }
        ],
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload_no_waiver)
    assert res.status_code == 422
    assert "Waiver must be signed" in res.text

    # Missing emergency contact
    payload_no_contact = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Risk Taker",
                "age": 25,
                "waiver_signed": True,
            }
        ],
    }
    res2 = client.post(TRAVELER_BOOKINGS, json=payload_no_contact)
    assert res2.status_code == 422
    assert "Emergency contact name and phone are required" in res2.text

    # Valid adventure booking
    payload_valid = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Safe Adventurer",
                "age": 25,
                "emergency_contact_name": "Mom",
                "emergency_contact_phone": "+94770000000",
                "waiver_signed": True,
            }
        ],
    }
    res3 = client.post(TRAVELER_BOOKINGS, json=payload_valid)
    assert res3.status_code == 201


def test_booking_creation_legacy_compatibility_without_participants(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=10)
        slot_id = slot.id

    actor["id"] = 5
    payload = {
        "activity_slot_id": slot_id,
        "participants": 3,
        "payment_method": "pay_at_location",
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["booking_status"] == "pending"
    assert data["participants"] == 3
    assert data["participants_list"] == []


def test_provider_accept_booking(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("200.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        slot.reserved_count = 2
        db.commit()
        booking_id = booking.id
        slot_id = slot.id

    # Non-owner provider (actor 2) attempt
    actor["id"] = 2
    res_unauth = client.post(f"{PROVIDER_BOOKINGS}/{booking_id}/action", json={"action": "accept"})
    assert res_unauth.status_code == 404

    # Owning provider (actor 1) accepts
    actor["id"] = 1
    res = client.post(f"{PROVIDER_BOOKINGS}/{booking_id}/action", json={"action": "accept"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "confirmed"

    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.booking_status == "confirmed"
        s = db.get(ActivitySlot, slot_id)
        assert s.reserved_count == 2  # Preserved!

    # Idempotent re-acceptance
    res_repeat = client.post(f"{PROVIDER_BOOKINGS}/{booking_id}/action", json={"action": "accept"})
    assert res_repeat.status_code == 200
    assert res_repeat.json()["status"] == "confirmed"


def test_provider_reject_booking_and_releases_capacity(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("200.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        slot.reserved_count = 2
        db.commit()
        booking_id = booking.id
        slot_id = slot.id

    actor["id"] = 1
    reject_reason = "Weather warning in coastal zone"
    res = client.post(
        f"{PROVIDER_BOOKINGS}/{booking_id}/action",
        json={"action": "reject", "reason": reject_reason},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "rejected"

    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.booking_status == "rejected"
        assert b.cancellation_reason == reject_reason
        assert b.cancelled_at is not None

        # Capacity released exactly once
        s = db.get(ActivitySlot, slot_id)
        assert s.reserved_count == 0

    # Repeated reject is idempotent and does not release capacity below 0
    res_repeat = client.post(
        f"{PROVIDER_BOOKINGS}/{booking_id}/action",
        json={"action": "reject", "reason": reject_reason},
    )
    assert res_repeat.status_code == 200
    with Session(engine) as db:
        s = db.get(ActivitySlot, slot_id)
        assert s.reserved_count == 0


def test_provider_on_site_cash_payment(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("200.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
        )
        db.add(booking)
        slot.reserved_count = 2
        db.commit()
        booking_id = booking.id

    actor["id"] = 1

    # Record cash payment
    res = client.post(
        f"{PROVIDER_BOOKINGS}/{booking_id}/action",
        json={"action": "cash", "reason": "Paid in LKR bills"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["payment_status"] == "paid"

    with Session(engine) as db:
        b = db.get(ActivityBooking, booking_id)
        assert b.payment_status == "paid"

        # Check PaymentTransaction record
        ref = db.scalar(
            select(BookingReference).where(
                BookingReference.module_type == "activity",
                BookingReference.target_booking_id == booking_id,
            )
        )
        assert ref is not None
        assert ref.payment_status == "paid"

        txs = db.query(PaymentTransaction).filter_by(booking_reference_id=ref.id).all()
        assert len(txs) == 1
        assert txs[0].payment_method == "cash"
        assert txs[0].amount == Decimal("200.00")
        assert txs[0].payment_status in ("paid", "captured", "completed", "success")

    # Idempotent repeated cash recording does not create second transaction
    res_repeat = client.post(
        f"{PROVIDER_BOOKINGS}/{booking_id}/action",
        json={"action": "record_cash"},
    )
    assert res_repeat.status_code == 200
    with Session(engine) as db:
        txs = db.query(PaymentTransaction).filter_by(booking_reference_id=ref.id).all()
        assert len(txs) == 1


def test_provider_cancel_session(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=8)
        b1 = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("200.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        b2 = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=3,
            total_price=Decimal("300.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="confirmed",
        )
        db.add_all([b1, b2])
        slot.reserved_count = 5
        db.commit()
        slot_id = slot.id
        b1_id, b2_id = b1.id, b2.id

    # Non-owner provider (actor 2) attempt
    actor["id"] = 2
    res_unauth = client.post(
        f"{API}/provider/activities/1/slots/{slot_id}/cancel-session",
        json={"reason": "Adverse weather and heavy rain"},
    )
    assert res_unauth.status_code in (403, 404)

    # Owning provider cancels session
    actor["id"] = 1
    cancel_reason = "Severe flash flood warning in the area"
    res = client.post(
        f"{API}/provider/activities/1/slots/{slot_id}/cancel-session",
        json={"reason": cancel_reason},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["cancelled_bookings_count"] == 2
    assert data["released_capacity"] == 5
    assert data["is_active"] is False

    with Session(engine) as db:
        slot = db.get(ActivitySlot, slot_id)
        assert slot.is_active is False
        assert slot.reserved_count == 0

        for bid in (b1_id, b2_id):
            b = db.get(ActivityBooking, bid)
            assert b.booking_status == "cancelled"
            assert "Severe flash flood" in b.cancellation_reason
            assert b.cancelled_at is not None

    # Calling cancel-session on already cancelled slot is idempotent
    res_repeat = client.post(
        f"{API}/provider/activities/1/slots/{slot_id}/cancel-session",
        json={"reason": cancel_reason},
    )
    assert res_repeat.status_code == 200
    assert res_repeat.json()["cancelled_bookings_count"] == 0
    assert res_repeat.json()["released_capacity"] == 0


def test_traveler_cancel_pending_booking(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5, hours_ahead=72)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=2,
            total_price=Decimal("200.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        slot.reserved_count = 2
        db.commit()
        booking_id = booking.id
        slot_id = slot.id

    actor["id"] = 5  # traveler
    res = client.post(
        f"{TRAVELER_BOOKINGS}/{booking_id}/cancel",
        json={"reason": "Schedule conflict arose"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["booking_status"] == "cancelled"

    with Session(engine) as db:
        s = db.get(ActivitySlot, slot_id)
        assert s.reserved_count == 0


def test_traveler_get_my_bookings_includes_participants(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=1,
            total_price=Decimal("100.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.flush()
        part = ActivityBookingParticipant(
            activity_booking_id=booking.id,
            full_name="Detailed Traveler",
            age=32,
            emergency_contact_name="Family Member",
            emergency_contact_phone="+94770001122",
            special_requirements="None",
            waiver_signed=True,
        )
        db.add(part)
        db.commit()
        target_id = booking.id

    actor["id"] = 5
    res = client.get(f"{TRAVELER_BOOKINGS}/me")
    assert res.status_code == 200, res.text
    bookings = res.json()
    assert len(bookings) >= 1
    target = next((b for b in bookings if b["id"] == target_id), None)
    assert target is not None
    assert len(target["participants_list"]) == 1
    assert target["participants_list"][0]["full_name"] == "Detailed Traveler"


def test_cash_payment_rejected_on_pending_booking(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=1,
            total_price=Decimal("100.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        slot.reserved_count = 1
        db.commit()
        booking_id = booking.id

    actor["id"] = 1
    res = client.post(
        f"{PROVIDER_BOOKINGS}/{booking_id}/action",
        json={"action": "cash"},
    )
    assert res.status_code == 409
    assert "Only confirmed bookings can accept cash payments" in res.text


def test_adventure_booking_cannot_bypass_waiver_by_omitting_participants_list(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        adv_act = Activity(
            title="Kithulgala Canyon Hike",
            description="High risk canyon adventure",
            category="adventure",
            district="Colombo District",
            city="Avissawella",
            meeting_point="Bridge 1",
            duration_minutes=90,
            minimum_age=0,
            pricing_type="per_person",
            price=Decimal("300.00"),
            currency="LKR",
            provider_profile_id=1,
            approval_status="approved",
            is_active=True,
        )
        db.add(adv_act)
        db.commit()
        db.refresh(adv_act)
        slot = create_future_slot(db, activity_id=adv_act.id, capacity=6)
        slot_id = slot.id

    actor["id"] = 5
    # Omitting participants_list must be blocked on adventure activities
    payload = {
        "activity_slot_id": slot_id,
        "participants": 2,
        "payment_method": "pay_at_location",
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 422
    assert "Participant details, signed waivers, and emergency contacts are required for adventure activities" in res.text


def test_age_restricted_booking_cannot_bypass_by_omitting_participants_list(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        age_act = Activity(
            title="Night Brewery Walk",
            description="Adult cultural tour",
            category="cultural",
            district="Colombo District",
            city="Colombo",
            meeting_point="Fort Clock",
            duration_minutes=60,
            minimum_age=21,
            pricing_type="per_person",
            price=Decimal("150.00"),
            currency="LKR",
            provider_profile_id=1,
            approval_status="approved",
            is_active=True,
        )
        db.add(age_act)
        db.commit()
        db.refresh(age_act)
        slot = create_future_slot(db, activity_id=age_act.id, capacity=6)
        slot_id = slot.id

    actor["id"] = 5
    # Omitting participants_list must be blocked on age-restricted activities
    payload = {
        "activity_slot_id": slot_id,
        "participants": 2,
        "payment_method": "pay_at_location",
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 422
    assert "Participant details and age verification are required for activities with a minimum age restriction" in res.text


def test_age_restricted_booking_cannot_bypass_with_null_age(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        age_act = Activity(
            title="Scuba Club",
            description="Adult scuba",
            category="cultural",
            district="Colombo District",
            city="Colombo",
            meeting_point="Marina",
            duration_minutes=60,
            minimum_age=18,
            pricing_type="per_person",
            price=Decimal("250.00"),
            currency="LKR",
            provider_profile_id=1,
            approval_status="approved",
            is_active=True,
        )
        db.add(age_act)
        db.commit()
        db.refresh(age_act)
        slot = create_future_slot(db, activity_id=age_act.id, capacity=6)
        slot_id = slot.id

    actor["id"] = 5
    # Supplying participants with age=None must be rejected
    payload = {
        "activity_slot_id": slot_id,
        "participants": 1,
        "payment_method": "pay_at_location",
        "participants_list": [
            {
                "full_name": "Ageless Diver",
                "age": None,
            }
        ],
    }
    res = client.post(TRAVELER_BOOKINGS, json=payload)
    assert res.status_code == 422
    assert "must provide age for age-restricted activities" in res.text


def test_traveler_participant_privacy_isolation(catalog):
    client, actor, engine = catalog
    with Session(engine) as db:
        slot = create_future_slot(db, activity_id=1, capacity=5)
        # Booking by traveler user_id=5
        booking = ActivityBooking(
            activity_slot_id=slot.id,
            user_id=5,
            participants=1,
            total_price=Decimal("100.00"),
            currency="LKR",
            payment_method="pay_at_location",
            payment_status="unpaid",
            booking_status="pending",
        )
        db.add(booking)
        db.flush()
        part = ActivityBookingParticipant(
            activity_booking_id=booking.id,
            full_name="Secret VIP Traveler",
            age=40,
            emergency_contact_name="Confidential Contact",
            emergency_contact_phone="+94779998877",
            waiver_signed=True,
        )
        db.add(part)
        db.commit()
        booking_id = booking.id

    # Another traveler (create or switch to user 4 if traveler or simulate)
    with Session(engine) as db:
        from app.models.user import User
        other_user = User(
            id=99,
            email="other_traveler@example.test",
            full_name="Other Traveler",
            role="traveler",
            is_active=True,
        )
        db.add(other_user)
        db.commit()

    actor["id"] = 99
    res = client.get(f"{TRAVELER_BOOKINGS}/me")
    assert res.status_code == 200
    my_bookings = res.json()
    assert all(b["id"] != booking_id for b in my_bookings)
    assert not any("Secret VIP Traveler" in str(b) for b in my_bookings)


def test_public_activity_discovery_never_exposes_participants(catalog):
    client, actor, engine = catalog
    res = client.get(f"{API}/activities/1")
    assert res.status_code == 200
    data = res.json()
    assert "participants" not in data
    assert "participants_list" not in data
    assert "emergency_contact" not in str(data).lower()


