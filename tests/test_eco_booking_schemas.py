"""
Unit Tests for Module 04 Eco-Tourism Slot and Booking Pydantic Schemas.

Targets:
    app.schemas.eco_slot
    app.schemas.eco_booking

Tests:
1. EcoSlotCreate timing, timezone, environmental capacity bounds, and extra field prohibition.
2. EcoSlotResponse and EcoSlotPublicItem serialization and available_places calculation.
3. EcoSlotAvailabilityQuery date range validation and capacity bounds.
4. EcoParticipantCreate field validations, age limits, and string trimming.
5. EcoBookingCreate participant limits, payment methods, roster count consistency, and extra field rejection.
6. EcoBookingResponse data mapping and Decimal price preservation.
7. EcoBookingCancellation reason validation.
8. EcoBookingProviderDecision action validation and rejection note enforcement.

ZERO DATABASE DEPENDENCY - 100% IN-MEMORY PYDANTIC VALIDATION.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.schemas.eco_slot import (
    ENVIRONMENTAL_MAX_SLOT_CAPACITY,
    EcoSlotAvailabilityQuery,
    EcoSlotCreate,
    EcoSlotPublicItem,
    EcoSlotResponse,
)
from app.schemas.eco_booking import (
    ENVIRONMENTAL_MAX_BOOKING_PARTICIPANTS,
    EcoBookingCancellation,
    EcoBookingCreate,
    EcoBookingProviderDecision,
    EcoBookingResponse,
    EcoParticipantCreate,
    EcoParticipantResponse,
)


# ==============================================================================
# 1. EcoSlotCreate Tests
# ==============================================================================


class TestEcoSlotCreateSchema:
    def test_valid_slot_create(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=2)
        end = start + timedelta(hours=3)

        payload = {
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "capacity": 15,
        }
        slot = EcoSlotCreate.model_validate(payload)
        assert slot.capacity == 15
        assert slot.starts_at.astimezone(timezone.utc) == start

    def test_slot_rejects_naive_datetime(self):
        naive_start = datetime(2027, 5, 10, 10, 0)
        naive_end = datetime(2027, 5, 10, 13, 0)

        with pytest.raises(ValidationError) as exc:
            EcoSlotCreate(starts_at=naive_start, ends_at=naive_end, capacity=10)
        assert "timezone offset" in str(exc.value)

    def test_slot_rejects_past_start_time(self):
        now = datetime.now(timezone.utc)
        past_start = now - timedelta(hours=1)
        future_end = now + timedelta(hours=2)

        with pytest.raises(ValidationError) as exc:
            EcoSlotCreate(starts_at=past_start, ends_at=future_end, capacity=10)
        assert "start time must be strictly in the future" in str(exc.value)

    def test_slot_rejects_end_before_start(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1)
        end = start - timedelta(hours=1)

        with pytest.raises(ValidationError) as exc:
            EcoSlotCreate(starts_at=start, ends_at=end, capacity=10)
        assert "end time must be strictly after the start time" in str(exc.value)

    def test_slot_rejects_capacity_below_one(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1)
        end = start + timedelta(hours=2)

        with pytest.raises(ValidationError):
            EcoSlotCreate(starts_at=start, ends_at=end, capacity=0)

    def test_slot_rejects_capacity_exceeding_environmental_ceiling(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1)
        end = start + timedelta(hours=2)

        with pytest.raises(ValidationError):
            EcoSlotCreate(
                starts_at=start,
                ends_at=end,
                capacity=ENVIRONMENTAL_MAX_SLOT_CAPACITY + 1,  # 51
            )

    def test_slot_rejects_extra_client_fields(self):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1)
        end = start + timedelta(hours=2)

        payload = {
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "capacity": 10,
            "reserved_count": 5,  # Forbidden client override
            "id": 999,
        }
        with pytest.raises(ValidationError) as exc:
            EcoSlotCreate.model_validate(payload)
        assert "extra_forbidden" in str(exc.value)


# ==============================================================================
# 2. EcoSlotResponse & Availability Tests
# ==============================================================================


class TestEcoSlotResponseSchemas:
    def test_slot_response_attributes_and_available_places(self):
        data = {
            "id": 101,
            "eco_experience_id": 20,
            "activity_id": None,
            "starts_at": datetime(2026, 12, 1, 9, 0, tzinfo=UTC),
            "ends_at": datetime(2026, 12, 1, 12, 0, tzinfo=UTC),
            "capacity": 20,
            "reserved_count": 6,
            "is_active": True,
            "created_at": datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
        }
        resp = EcoSlotResponse.model_validate(data)
        assert resp.id == 101
        assert resp.eco_experience_id == 20
        assert resp.available_places == 14

    def test_slot_availability_query_validation(self):
        # Valid query with matching bounds
        q = EcoSlotAvailabilityQuery(
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 15),
            min_available_places=4,
        )
        assert q.start_date == date(2026, 12, 1)
        assert q.min_available_places == 4

        # Invalid: end_date < start_date
        with pytest.raises(ValidationError) as exc:
            EcoSlotAvailabilityQuery(
                start_date=date(2026, 12, 15),
                end_date=date(2026, 12, 1),
            )
        assert "end_date cannot be earlier than start_date" in str(exc.value)

        # Extra forbidden
        with pytest.raises(ValidationError):
            EcoSlotAvailabilityQuery.model_validate({"extra_param": "test"})


# ==============================================================================
# 3. EcoParticipantCreate Tests
# ==============================================================================


class TestEcoParticipantSchema:
    def test_valid_participant_create(self):
        p = EcoParticipantCreate(
            full_name="Nimal Senanayake",
            age=32,
            emergency_contact_name="Sunil Senanayake",
            emergency_contact_phone="+94771234567",
            special_requirements="Vegetarian meal",
            waiver_signed=True,
        )
        assert p.full_name == "Nimal Senanayake"
        assert p.age == 32
        assert p.waiver_signed is True

    def test_empty_or_whitespace_name_rejected(self):
        with pytest.raises(ValidationError):
            EcoParticipantCreate(full_name="   ")

    def test_invalid_age_bounds(self):
        with pytest.raises(ValidationError):
            EcoParticipantCreate(full_name="Valid Name", age=-1)

        with pytest.raises(ValidationError):
            EcoParticipantCreate(full_name="Valid Name", age=121)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            EcoParticipantCreate.model_validate({
                "full_name": "Valid Name",
                "is_admin": True,
            })


# ==============================================================================
# 4. EcoBookingCreate Tests
# ==============================================================================


class TestEcoBookingCreateSchema:
    def test_valid_minimal_booking_create(self):
        booking = EcoBookingCreate(
            eco_slot_id=42,
            participants=4,
            payment_method="pay_at_location",
        )
        assert booking.eco_slot_id == 42
        assert booking.participants == 4
        assert booking.payment_method == "pay_at_location"
        assert booking.participants_roster is None

    def test_valid_booking_with_roster(self):
        roster = [
            EcoParticipantCreate(full_name="Participant One", age=25),
            EcoParticipantCreate(full_name="Participant Two", age=28),
        ]
        booking = EcoBookingCreate(
            eco_slot_id=42,
            participants=2,
            payment_method="online",
            participants_roster=roster,
        )
        assert len(booking.participants_roster) == 2

    def test_roster_count_mismatch_rejected(self):
        roster = [
            EcoParticipantCreate(full_name="Participant One", age=25),
        ]
        with pytest.raises(ValidationError) as exc:
            EcoBookingCreate(
                eco_slot_id=42,
                participants=3,  # Mismatch: 3 participants but only 1 in roster
                participants_roster=roster,
            )
        assert "does not match requested participant count" in str(exc.value)

    def test_rejects_client_controlled_price_and_status(self):
        # Attempt to inject client-controlled pricing or status
        payload = {
            "eco_slot_id": 42,
            "participants": 2,
            "payment_method": "pay_at_location",
            "total_price": 0.00,  # FORBIDDEN
            "payment_status": "paid",  # FORBIDDEN
            "booking_status": "confirmed",  # FORBIDDEN
            "user_id": 1,  # FORBIDDEN
        }
        with pytest.raises(ValidationError) as exc:
            EcoBookingCreate.model_validate(payload)
        assert "extra_forbidden" in str(exc.value)

    def test_rejects_invalid_payment_method(self):
        with pytest.raises(ValidationError):
            EcoBookingCreate(
                eco_slot_id=42,
                participants=2,
                payment_method="cryptocurrency",  # type: ignore
            )

    def test_rejects_participants_exceeding_ceiling(self):
        with pytest.raises(ValidationError):
            EcoBookingCreate(
                eco_slot_id=42,
                participants=ENVIRONMENTAL_MAX_BOOKING_PARTICIPANTS + 1,  # 51
            )


# ==============================================================================
# 5. EcoBookingResponse Tests
# ==============================================================================


class TestEcoBookingResponseSchema:
    def test_booking_response_serialization(self):
        now = datetime.now(UTC)
        data = {
            "id": 501,
            "booking_reference": "ECO-20261118-A78B",
            "eco_experience_id": 12,
            "activity_slot_id": 42,
            "user_id": 8,
            "participants": 3,
            "total_price": Decimal("7500.00"),
            "currency": "LKR",
            "payment_method": "pay_at_location",
            "payment_status": "unpaid",
            "booking_status": "confirmed",
            "cancellation_reason": None,
            "cancelled_at": None,
            "created_at": now,
            "updated_at": now,
            "participants_list": [
                {
                    "id": 1,
                    "full_name": "Traveler One",
                    "age": 30,
                    "waiver_signed": True,
                    "created_at": now,
                }
            ],
        }
        resp = EcoBookingResponse.model_validate(data)
        assert resp.id == 501
        assert resp.booking_reference == "ECO-20261118-A78B"
        assert resp.total_price == Decimal("7500.00")
        assert len(resp.participants_list) == 1
        assert resp.participants_list[0].full_name == "Traveler One"


# ==============================================================================
# 6. Cancellation & Provider Decision Schemas Tests
# ==============================================================================


class TestCancellationAndDecisionSchemas:
    def test_cancellation_valid_reason(self):
        c = EcoBookingCancellation(reason="Family medical emergency")
        assert c.reason == "Family medical emergency"

    def test_cancellation_rejects_too_short_reason(self):
        with pytest.raises(ValidationError):
            EcoBookingCancellation(reason="bad")

    def test_cancellation_rejects_whitespace_reason(self):
        with pytest.raises(ValidationError):
            EcoBookingCancellation(reason="      ")

    def test_provider_decision_accept_and_confirm_cash(self):
        d1 = EcoBookingProviderDecision(action="accept")
        assert d1.action == "accept"
        assert d1.note is None

        d2 = EcoBookingProviderDecision(action="confirm_cash", note="Received 7500 LKR in cash")
        assert d2.action == "confirm_cash"

    def test_provider_decision_reject_requires_explanatory_note(self):
        # Reject without note fails
        with pytest.raises(ValidationError) as exc1:
            EcoBookingProviderDecision(action="reject")
        assert "A clear note" in str(exc1.value)

        # Reject with too short note fails
        with pytest.raises(ValidationError) as exc2:
            EcoBookingProviderDecision(action="reject", note="no")
        assert "A clear note" in str(exc2.value)

        # Reject with valid note passes
        d = EcoBookingProviderDecision(action="reject", note="Heavy rain warning on the mountain trail")
        assert d.action == "reject"
