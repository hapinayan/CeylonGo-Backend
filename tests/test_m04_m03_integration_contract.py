"""
Contract & Integration Tests for Module 04 (Eco-Tourism) and Module 03 (Activity Scheduling & Booking).

Tests:
1. Slot scheduling contract validation (timing, capacity <= 50, guide assignment).
2. Slot scheduling block on unapproved or unpublished eco experiences.
3. Seasonal restriction enforcement on slot creation (monsoon, breeding, park closure).
4. Pay-at-location pricing calculation contracts (per_person vs per_group).
5. Traveler booking capacity validation (overbooking prevention, group bounds).
6. Cancellation policy and deadline cutoff evaluation.
7. Simulated concurrent booking capacity reservation (preventing overselling).

NO LIVE DATABASE TABLES FOR MODULE 03 ARE REQUIRED OR USED.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import threading

import pytest
from pydantic import ValidationError

from app.schemas.m03_integration_contract import (
    M03BookingCancelRequestContract,
    M03BookingRequestContract,
    M03BookingResponseContract,
    M03SlotCreateContract,
    M03SlotResponseContract,
    M04toM03ContractValidator,
)


# ==============================================================================
# 1. Slot Scheduling Contract Tests
# ==============================================================================


def test_slot_creation_contract_validation():
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    end = start + timedelta(hours=4)

    # Valid slot payload
    slot = M03SlotCreateContract(
        eco_experience_id=101,
        start_datetime=start,
        end_datetime=end,
        max_capacity=12,
        guide_name="Kavinda Perera",
    )
    assert slot.eco_experience_id == 101
    assert slot.max_capacity == 12
    assert slot.guide_name == "Kavinda Perera"

    # Invalid: end_datetime <= start_datetime
    with pytest.raises(ValidationError):
        M03SlotCreateContract(
            eco_experience_id=101,
            start_datetime=start,
            end_datetime=start,
            max_capacity=12,
        )

    # Invalid: capacity > 50 (environmental ceiling)
    with pytest.raises(ValidationError):
        M03SlotCreateContract(
            eco_experience_id=101,
            start_datetime=start,
            end_datetime=end,
            max_capacity=55,
        )


def test_slot_blocked_if_eco_experience_unapproved_or_unpublished():
    now = datetime.now(UTC)
    start = now + timedelta(days=3)
    end = start + timedelta(hours=3)

    # Experience is draft / unpublished
    check_draft = M04toM03ContractValidator.validate_slot_creation(
        experience_is_approved=False,
        experience_is_published=False,
        experience_max_group_size=15,
        requested_capacity=10,
        slot_start=start,
        slot_end=end,
    )
    assert check_draft["allowed"] is False
    assert "unapproved or unpublished" in check_draft["reason"].lower()

    # Experience is approved but not published
    check_unpublished = M04toM03ContractValidator.validate_slot_creation(
        experience_is_approved=True,
        experience_is_published=False,
        experience_max_group_size=15,
        requested_capacity=10,
        slot_start=start,
        slot_end=end,
    )
    assert check_unpublished["allowed"] is False

    # Experience is approved and published -> Allowed
    check_valid = M04toM03ContractValidator.validate_slot_creation(
        experience_is_approved=True,
        experience_is_published=True,
        experience_max_group_size=15,
        requested_capacity=10,
        slot_start=start,
        slot_end=end,
    )
    assert check_valid["allowed"] is True
    assert check_valid["reason"] is None


def test_slot_blocked_during_active_seasonal_restrictions():
    now = datetime.now(UTC)
    target_date = (now + timedelta(days=15)).date()
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=UTC) + timedelta(hours=8)
    end = start + timedelta(hours=4)

    season_restrictions = [
        {
            "start_date": target_date - timedelta(days=2),
            "end_date": target_date + timedelta(days=5),
            "restriction_type": "monsoon_season",
            "reason": "Southwest Monsoon Park Closure",
            "is_active": True,
        }
    ]

    # Slot falls inside active monsoon closure
    result = M04toM03ContractValidator.validate_slot_creation(
        experience_is_approved=True,
        experience_is_published=True,
        experience_max_group_size=20,
        requested_capacity=10,
        slot_start=start,
        slot_end=end,
        active_season_restrictions=season_restrictions,
    )
    assert result["allowed"] is False
    assert "active seasonal restriction" in result["reason"].lower()
    assert "monsoon" in result["reason"].lower()

    # When restriction is inactive, slot is permitted
    season_restrictions[0]["is_active"] = False
    inactive_res = M04toM03ContractValidator.validate_slot_creation(
        experience_is_approved=True,
        experience_is_published=True,
        experience_max_group_size=20,
        requested_capacity=10,
        slot_start=start,
        slot_end=end,
        active_season_restrictions=season_restrictions,
    )
    assert inactive_res["allowed"] is True


# ==============================================================================
# 2. Traveler Booking & Pricing Contract Tests
# ==============================================================================


def test_booking_request_pricing_calculation():
    # 1. per_person pricing
    total_per_person = M04toM03ContractValidator.calculate_total_price(
        pricing_type="per_person",
        base_price=Decimal("4500.00"),
        participants=3,
    )
    assert total_per_person == Decimal("13500.00")

    # 2. per_group pricing
    total_per_group = M04toM03ContractValidator.calculate_total_price(
        pricing_type="per_group",
        base_price=Decimal("18000.00"),
        participants=5,
    )
    assert total_per_group == Decimal("18000.00")

    # 3. Booking schema enforcement of Pay-at-Location
    req = M03BookingRequestContract(
        eco_experience_id=1,
        slot_id=5,
        participants=2,
        special_requests="Eco binoculars needed",
    )
    assert req.payment_method == "pay_at_location"


def test_booking_eligibility_capacity_and_group_bounds():
    # 1. Booking exceeds available capacity
    check_over_cap = M04toM03ContractValidator.validate_booking_eligibility(
        experience_is_publicly_visible=True,
        slot_status="available",
        available_capacity=4,
        requested_participants=5,
        experience_min_participants=1,
        experience_max_group_size=10,
    )
    assert check_over_cap["allowed"] is False
    assert "exceeds available capacity" in check_over_cap["reason"].lower()

    # 2. Booking exceeds eco experience max_group_size
    check_over_max = M04toM03ContractValidator.validate_booking_eligibility(
        experience_is_publicly_visible=True,
        slot_status="available",
        available_capacity=20,
        requested_participants=15,
        experience_min_participants=1,
        experience_max_group_size=10,
    )
    assert check_over_max["allowed"] is False
    assert "maximum group size" in check_over_max["reason"].lower()

    # 3. Slot is sold out
    check_sold_out = M04toM03ContractValidator.validate_booking_eligibility(
        experience_is_publicly_visible=True,
        slot_status="sold_out",
        available_capacity=0,
        requested_participants=2,
        experience_min_participants=1,
        experience_max_group_size=10,
    )
    assert check_sold_out["allowed"] is False
    assert "sold_out" in check_sold_out["reason"].lower()

    # 4. Experience is not publicly visible
    check_hidden = M04toM03ContractValidator.validate_booking_eligibility(
        experience_is_publicly_visible=False,
        slot_status="available",
        available_capacity=10,
        requested_participants=2,
        experience_min_participants=1,
        experience_max_group_size=10,
    )
    assert check_hidden["allowed"] is False
    assert "not currently publicly available" in check_hidden["reason"].lower()


# ==============================================================================
# 3. Cancellation Policy & Concurrency Contract Tests
# ==============================================================================


def test_cancellation_policy_and_deadline_evaluation():
    now = datetime.now(UTC)
    slot_time = now + timedelta(hours=48)
    free_cancellation_hours = 24

    # Cancellation requested 40 hours before slot (> 24 hr cutoff) -> Eligible
    early_req = now + timedelta(hours=8)
    res_early = M04toM03ContractValidator.evaluate_cancellation_policy(
        slot_start_datetime=slot_time,
        cancellation_requested_at=early_req,
        free_cancellation_hours=free_cancellation_hours,
    )
    assert res_early["eligible_for_free_cancellation"] is True

    # Cancellation requested 10 hours before slot (< 24 hr cutoff) -> Ineligible for automatic free cancellation
    late_req = now + timedelta(hours=38)
    res_late = M04toM03ContractValidator.evaluate_cancellation_policy(
        slot_start_datetime=slot_time,
        cancellation_requested_at=late_req,
        free_cancellation_hours=free_cancellation_hours,
    )
    assert res_late["eligible_for_free_cancellation"] is False


def test_simulated_concurrent_booking_capacity_reservation():
    """
    Simulate thread-safe atomic capacity reservation on a shared eco slot
    to verify concurrency behavior and overbooking prevention contract.
    """
    class MockEcoSlot:
        def __init__(self, slot_id: int, max_capacity: int):
            self.slot_id = slot_id
            self.max_capacity = max_capacity
            self.booked_participants = 0
            self.lock = threading.Lock()
            self.successful_bookings = 0
            self.rejected_bookings = 0

        def reserve(self, participants: int) -> bool:
            with self.lock:
                if self.booked_participants + participants <= self.max_capacity:
                    self.booked_participants += participants
                    self.successful_bookings += 1
                    return True
                else:
                    self.rejected_bookings += 1
                    return False

    # Slot with 10 max capacity
    slot = MockEcoSlot(slot_id=1, max_capacity=10)

    # 6 concurrent threads each attempting to reserve 2 seats (total 12 seats requested)
    threads = []
    for _ in range(6):
        t = threading.Thread(target=slot.reserve, args=(2,))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 5 bookings must succeed (10 seats reserved), and exactly 1 must be rejected
    assert slot.booked_participants == 10
    assert slot.successful_bookings == 5
    assert slot.rejected_bookings == 1
