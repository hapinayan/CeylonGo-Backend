"""
Unit Tests for Module 04 Eco-Tourism Booking Validation and Pricing Logic.

Target:
    app.services.eco_booking_pricing

Features Covered:
1. Valid and invalid participant counts.
2. Group size bounds (min_participants, max_group_size, environmental ceiling 50).
3. Available slot capacity checks.
4. Participant age validation and min_age boundary enforcement.
5. Decimal precision and calculations for 'per_person' and 'per_group' pricing types.
6. Timezone-aware cancellation cutoff evaluation (before, at, and after deadline).
7. Error code stability and invalid/missing input handling.
8. Full end-to-end booking validation and pricing orchestrator.

ZERO DATABASE DEPENDENCY - RUNS ISOLATED IN MEMORY.
"""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import pytest

from app.services.eco_booking_pricing import (
    ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING,
    EcoBookingValidationError,
    calculate_eco_booking_price,
    evaluate_eco_cancellation_eligibility,
    normalize_datetime_utc,
    to_decimal_currency,
    validate_and_price_eco_booking,
    validate_group_size,
    validate_participant_ages,
)


# ==============================================================================
# 1. Group Size and Participant Count Tests
# ==============================================================================


class TestGroupSizeValidation:
    """Tests for validate_group_size function."""

    def test_valid_participant_counts(self):
        # Middle of range
        validate_group_size(participants=5, min_participants=2, max_group_size=10)
        # Exactly at min
        validate_group_size(participants=2, min_participants=2, max_group_size=10)
        # Exactly at max
        validate_group_size(participants=10, min_participants=2, max_group_size=10)
        # Single participant experience
        validate_group_size(participants=1, min_participants=1, max_group_size=1)
        # Maximum environmental ceiling (50)
        validate_group_size(participants=50, min_participants=1, max_group_size=50)

    @pytest.mark.parametrize("invalid_p", [0, -1, -100, None, "5", 4.5, True, False])
    def test_invalid_participant_types_and_values(self, invalid_p):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=invalid_p, min_participants=1, max_group_size=10)  # type: ignore
        assert exc.value.code == "ECO_INVALID_PARTICIPANTS"

    def test_participant_below_minimum(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=2, min_participants=4, max_group_size=12)
        assert exc.value.code == "ECO_BELOW_MIN_PARTICIPANTS"
        assert exc.value.details["requested_participants"] == 2
        assert exc.value.details["min_participants"] == 4

    def test_participant_exceeds_max_group_size(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=15, min_participants=2, max_group_size=10)
        assert exc.value.code == "ECO_EXCEEDS_MAX_GROUP_SIZE"
        assert exc.value.details["requested_participants"] == 15
        assert exc.value.details["max_group_size"] == 10

    def test_invalid_listing_bounds_min_greater_than_max(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=5, min_participants=10, max_group_size=5)
        assert exc.value.code == "ECO_INVALID_LISTING_BOUNDS"

    def test_invalid_listing_bounds_min_less_than_one(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=2, min_participants=0, max_group_size=10)
        assert exc.value.code == "ECO_INVALID_LISTING_BOUNDS"

    def test_exceeds_environmental_ceiling(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=55, min_participants=1, max_group_size=55)
        assert exc.value.code == "ECO_EXCEEDS_ENVIRONMENTAL_CEILING"
        assert exc.value.details["ceiling"] == ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING

    def test_slot_capacity_bound(self):
        # Within capacity
        validate_group_size(participants=4, min_participants=1, max_group_size=10, available_capacity=4)
        validate_group_size(participants=3, min_participants=1, max_group_size=10, available_capacity=5)

        # Exceeds slot capacity even if within max_group_size
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=5, min_participants=1, max_group_size=10, available_capacity=4)
        assert exc.value.code == "ECO_EXCEEDS_SLOT_CAPACITY"
        assert exc.value.details["requested_participants"] == 5
        assert exc.value.details["available_capacity"] == 4

    def test_invalid_slot_capacity_value(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_group_size(participants=2, min_participants=1, max_group_size=10, available_capacity=-1)
        assert exc.value.code == "ECO_INVALID_CAPACITY"


# ==============================================================================
# 2. Participant Age Validation Tests
# ==============================================================================


class TestParticipantAgeValidation:
    """Tests for validate_participant_ages function."""

    def test_valid_participant_ages(self):
        # No min_age restriction
        validate_participant_ages(participant_ages=[5, 25, 65], min_age=None)
        # All above min_age
        validate_participant_ages(participant_ages=[12, 15, 30], min_age=10)
        # Exactly at min_age
        validate_participant_ages(participant_ages=[10, 10, 10], min_age=10)
        # None ages provided passes (age collection is optional at request stage)
        validate_participant_ages(participant_ages=None, min_age=12)

    def test_age_below_minimum_boundary(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_participant_ages(participant_ages=[25, 9, 30], min_age=10)
        assert exc.value.code == "ECO_AGE_BELOW_MINIMUM"
        assert exc.value.details["age"] == 9
        assert exc.value.details["min_age"] == 10
        assert exc.value.details["index"] == 1

    def test_participant_age_count_mismatch(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_participant_ages(participant_ages=[20, 25], min_age=12, expected_count=3)
        assert exc.value.code == "ECO_PARTICIPANT_AGE_COUNT_MISMATCH"
        assert exc.value.details["ages_count"] == 2
        assert exc.value.details["expected_count"] == 3

    @pytest.mark.parametrize("bad_age", [-1, 125, "twenty", 18.5, None, True])
    def test_invalid_age_values(self, bad_age):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_participant_ages(participant_ages=[25, bad_age], min_age=10)  # type: ignore
        assert exc.value.code == "ECO_INVALID_AGE"

    @pytest.mark.parametrize("bad_min_age", [-1, 105, "ten", 12.5, True])
    def test_invalid_min_age_configuration(self, bad_min_age):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_participant_ages(participant_ages=[20], min_age=bad_min_age)  # type: ignore
        assert exc.value.code == "ECO_INVALID_AGE_CONFIG"


# ==============================================================================
# 3. Pricing Calculation Tests
# ==============================================================================


class TestPricingCalculations:
    """Tests for calculate_eco_booking_price and to_decimal_currency."""

    def test_per_person_pricing(self):
        res = calculate_eco_booking_price(
            pricing_type="per_person",
            base_price=Decimal("1500.00"),
            participants=4,
            currency="LKR",
        )
        assert res.pricing_type == "per_person"
        assert res.base_price == Decimal("1500.00")
        assert res.participants == 4
        assert res.total_price == Decimal("6000.00")
        assert res.currency == "LKR"

    def test_per_group_pricing(self):
        res = calculate_eco_booking_price(
            pricing_type="per_group",
            base_price=Decimal("12500.00"),
            participants=6,
            currency="LKR",
        )
        assert res.pricing_type == "per_group"
        assert res.base_price == Decimal("12500.00")
        assert res.participants == 6
        assert res.total_price == Decimal("12500.00")  # Flat rate regardless of group size

    def test_decimal_precision_no_float_drift(self):
        # 19.99 * 3 = 59.97 exactly
        res = calculate_eco_booking_price(
            pricing_type="per_person",
            base_price=Decimal("19.99"),
            participants=3,
        )
        assert res.total_price == Decimal("59.97")

        # Fractional string input
        res2 = calculate_eco_booking_price(
            pricing_type="per_person",
            base_price="33.335",
            participants=1,
        )
        # 33.335 rounds half up to 33.34
        assert res2.total_price == Decimal("33.34")

    def test_free_experience_zero_base_price(self):
        res = calculate_eco_booking_price(
            pricing_type="per_person",
            base_price=Decimal("0.00"),
            participants=10,
        )
        assert res.total_price == Decimal("0.00")

    def test_negative_base_price_raises_error(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            calculate_eco_booking_price(
                pricing_type="per_person",
                base_price=Decimal("-500.00"),
                participants=2,
            )
        assert exc.value.code == "ECO_INVALID_BASE_PRICE"

    def test_invalid_pricing_type_raises_error(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            calculate_eco_booking_price(
                pricing_type="per_hour",
                base_price=Decimal("1000.00"),
                participants=2,
            )
        assert exc.value.code == "ECO_INVALID_PRICING_TYPE"

    def test_invalid_participants_in_pricing(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            calculate_eco_booking_price(
                pricing_type="per_person",
                base_price=Decimal("1000.00"),
                participants=0,
            )
        assert exc.value.code == "ECO_INVALID_PARTICIPANTS"

    def test_unparseable_base_price(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            to_decimal_currency("not-a-number")
        assert exc.value.code == "ECO_INVALID_BASE_PRICE"


# ==============================================================================
# 4. Cancellation Cutoff Eligibility Tests
# ==============================================================================


class TestCancellationCutoffEligibility:
    """Tests for evaluate_eco_cancellation_eligibility."""

    def test_cancellation_well_before_cutoff(self):
        slot_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        requested_at = datetime(2026, 12, 7, 10, 0, tzinfo=UTC)  # 72 hours before
        hours_policy = 24

        res = evaluate_eco_cancellation_eligibility(
            slot_start=slot_start,
            cancellation_requested_at=requested_at,
            free_cancellation_hours=hours_policy,
        )
        assert res.is_free_cancellation is True
        assert res.penalty_applicable is False
        assert res.cancellation_deadline == datetime(2026, 12, 9, 10, 0, tzinfo=UTC)
        assert res.hours_until_slot == 72.0

    def test_cancellation_exactly_at_deadline(self):
        slot_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        requested_at = datetime(2026, 12, 9, 10, 0, tzinfo=UTC)  # Exactly 24h before
        hours_policy = 24

        res = evaluate_eco_cancellation_eligibility(
            slot_start=slot_start,
            cancellation_requested_at=requested_at,
            free_cancellation_hours=hours_policy,
        )
        assert res.is_free_cancellation is True
        assert res.penalty_applicable is False
        assert res.hours_until_slot == 24.0

    def test_cancellation_after_cutoff_penalty_applies(self):
        slot_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        requested_at = datetime(2026, 12, 9, 15, 0, tzinfo=UTC)  # 19 hours before (policy is 24h)
        hours_policy = 24

        res = evaluate_eco_cancellation_eligibility(
            slot_start=slot_start,
            cancellation_requested_at=requested_at,
            free_cancellation_hours=hours_policy,
        )
        assert res.is_free_cancellation is False
        assert res.penalty_applicable is True
        assert res.hours_until_slot == 19.0

    def test_cancellation_at_or_after_start_time_rejected(self):
        slot_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        requested_at = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)  # Exactly at start

        with pytest.raises(EcoBookingValidationError) as exc:
            evaluate_eco_cancellation_eligibility(
                slot_start=slot_start,
                cancellation_requested_at=requested_at,
                free_cancellation_hours=24,
            )
        assert exc.value.code == "ECO_SLOT_ALREADY_STARTED"

    def test_negative_cancellation_hours_rejected(self):
        slot_start = datetime(2026, 12, 10, 10, 0, tzinfo=UTC)
        with pytest.raises(EcoBookingValidationError) as exc:
            evaluate_eco_cancellation_eligibility(
                slot_start=slot_start,
                cancellation_requested_at=None,
                free_cancellation_hours=-5,
            )
        assert exc.value.code == "ECO_INVALID_CANCELLATION_HOURS"

    def test_timezone_conversion_awareness(self):
        # Local Colombo time (UTC+5:30)
        colombo_tz = timezone(timedelta(hours=5, minutes=30))
        slot_colombo = datetime(2026, 12, 10, 15, 30, tzinfo=colombo_tz)  # 10:00 UTC
        requested_utc = datetime(2026, 12, 9, 10, 0, tzinfo=UTC)         # 24h prior in UTC

        res = evaluate_eco_cancellation_eligibility(
            slot_start=slot_colombo,
            cancellation_requested_at=requested_utc,
            free_cancellation_hours=24,
        )
        assert res.is_free_cancellation is True
        assert res.hours_until_slot == 24.0

    def test_naive_datetime_normalization(self):
        naive_start = datetime(2026, 12, 10, 10, 0)
        norm = normalize_datetime_utc(naive_start)
        assert norm.tzinfo == UTC
        assert norm.year == 2026 and norm.hour == 10

    def test_non_datetime_normalization_error(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            normalize_datetime_utc("2026-12-10T10:00:00")  # type: ignore
        assert exc.value.code == "ECO_INVALID_DATETIME"


# ==============================================================================
# 5. Full Validation & Pricing Orchestrator Tests
# ==============================================================================


class TestBookingValidationOrchestrator:
    """Tests for validate_and_price_eco_booking."""

    def test_successful_per_person_booking(self):
        summary = validate_and_price_eco_booking(
            participants=3,
            min_participants=1,
            max_group_size=8,
            pricing_type="per_person",
            base_price=Decimal("2500.00"),
            min_age=8,
            participant_ages=[12, 15, 40],
            available_capacity=5,
            currency="LKR",
        )
        assert summary.valid is True
        assert summary.participants == 3
        assert summary.pricing.total_price == Decimal("7500.00")
        assert summary.min_age_enforced == 8
        assert summary.participant_ages == [12, 15, 40]

    def test_successful_per_group_booking(self):
        summary = validate_and_price_eco_booking(
            participants=5,
            min_participants=2,
            max_group_size=10,
            pricing_type="per_group",
            base_price="15000.00",
            min_age=None,
            participant_ages=None,
            available_capacity=10,
        )
        assert summary.valid is True
        assert summary.pricing.total_price == Decimal("15000.00")

    def test_orchestrator_stops_on_group_size_violation(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_and_price_eco_booking(
                participants=12,
                min_participants=2,
                max_group_size=10,
                pricing_type="per_person",
                base_price=Decimal("1000.00"),
            )
        assert exc.value.code == "ECO_EXCEEDS_MAX_GROUP_SIZE"

    def test_orchestrator_stops_on_age_violation(self):
        with pytest.raises(EcoBookingValidationError) as exc:
            validate_and_price_eco_booking(
                participants=2,
                min_participants=1,
                max_group_size=10,
                pricing_type="per_person",
                base_price=Decimal("1000.00"),
                min_age=16,
                participant_ages=[20, 14],  # 14 is below 16
            )
        assert exc.value.code == "ECO_AGE_BELOW_MINIMUM"
