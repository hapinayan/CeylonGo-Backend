"""
Module 04 — Eco-Tourism Booking Validation and Pricing Business Logic.

This service provides independent, pure business logic for Module 04:
1. Participant count and group-size limit validation (enforcing min_participants and environmental max <= 50).
2. Minimum age boundary and participant age roster validation.
3. Accurate monetary calculations using Python Decimal with 2-decimal-place precision (ROUND_HALF_UP).
4. Timezone-aware cancellation cutoff eligibility evaluation based on free_cancellation_hours.
5. Structured validation exceptions with stable machine-readable error codes.

ZERO EXTERNAL DATABASE WRITES OR PAYMENT GATEWAY DEPENDENCIES.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Sequence

# Canonical environmental ceiling mandated by Master Database & Module 04 Spec
ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING = 50


class EcoBookingValidationError(ValueError):
    """
    Raised when an eco-experience booking validation or pricing calculation fails.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable error description.
        details: Contextual details dictionary for API responses and logging.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class EcoPricingResult:
    """
    Structured outcome of an eco-experience booking price calculation.
    """

    pricing_type: str
    base_price: Decimal
    participants: int
    total_price: Decimal
    currency: str = "LKR"


@dataclass(frozen=True)
class EcoCancellationResult:
    """
    Structured outcome of an eco-experience cancellation cutoff evaluation.
    """

    is_free_cancellation: bool
    cancellation_deadline: datetime
    cancellation_requested_at: datetime
    slot_start: datetime
    free_cancellation_hours: int
    hours_until_slot: float
    penalty_applicable: bool


@dataclass(frozen=True)
class EcoBookingValidationSummary:
    """
    Aggregated validation and pricing summary for an eco booking request.
    """

    valid: bool
    participants: int
    pricing: EcoPricingResult
    min_age_enforced: int | None
    participant_ages: list[int] | None


def normalize_datetime_utc(dt: datetime) -> datetime:
    """
    Ensure a datetime is timezone-aware and normalized to UTC.
    If a naive datetime is supplied, assume it represents UTC.
    """
    if not isinstance(dt, datetime):
        raise EcoBookingValidationError(
            code="ECO_INVALID_DATETIME",
            message=f"Expected a datetime object, got {type(dt).__name__}.",
        )
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def validate_group_size(
    participants: int,
    min_participants: int,
    max_group_size: int,
    available_capacity: int | None = None,
) -> None:
    """
    Validate requested participant count against listing-level limits and slot availability.

    Rules:
    - participants must be a strictly positive integer.
    - min_participants must be >= 1.
    - max_group_size must be >= min_participants and <= 50 (environmental ceiling).
    - participants must satisfy: min_participants <= participants <= max_group_size.
    - If available_capacity is given, participants must be <= available_capacity.

    Raises:
        EcoBookingValidationError with specific error codes.
    """
    if isinstance(participants, bool) or not isinstance(participants, int) or participants <= 0:
        raise EcoBookingValidationError(
            code="ECO_INVALID_PARTICIPANTS",
            message=f"Participant count must be a positive integer, got: {participants}.",
            details={"participants": participants},
        )

    if min_participants < 1:
        raise EcoBookingValidationError(
            code="ECO_INVALID_LISTING_BOUNDS",
            message=f"Listing min_participants must be at least 1, got: {min_participants}.",
            details={"min_participants": min_participants},
        )

    if max_group_size < min_participants:
        raise EcoBookingValidationError(
            code="ECO_INVALID_LISTING_BOUNDS",
            message=(
                f"Listing max_group_size ({max_group_size}) cannot be less than "
                f"min_participants ({min_participants})."
            ),
            details={"min_participants": min_participants, "max_group_size": max_group_size},
        )

    if max_group_size > ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING:
        raise EcoBookingValidationError(
            code="ECO_EXCEEDS_ENVIRONMENTAL_CEILING",
            message=(
                f"Listing max_group_size ({max_group_size}) exceeds the platform environmental "
                f"ceiling of {ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING} participants per slot."
            ),
            details={
                "max_group_size": max_group_size,
                "ceiling": ENVIRONMENTAL_MAX_GROUP_SIZE_CEILING,
            },
        )

    if participants < min_participants:
        raise EcoBookingValidationError(
            code="ECO_BELOW_MIN_PARTICIPANTS",
            message=(
                f"Requested participant count ({participants}) is below the minimum required "
                f"group size of {min_participants}."
            ),
            details={
                "requested_participants": participants,
                "min_participants": min_participants,
            },
        )

    if participants > max_group_size:
        raise EcoBookingValidationError(
            code="ECO_EXCEEDS_MAX_GROUP_SIZE",
            message=(
                f"Requested participant count ({participants}) exceeds the maximum allowed "
                f"group size of {max_group_size}."
            ),
            details={
                "requested_participants": participants,
                "max_group_size": max_group_size,
            },
        )

    if available_capacity is not None:
        if isinstance(available_capacity, bool) or not isinstance(available_capacity, int) or available_capacity < 0:
            raise EcoBookingValidationError(
                code="ECO_INVALID_CAPACITY",
                message=f"Available slot capacity must be a non-negative integer, got: {available_capacity}.",
                details={"available_capacity": available_capacity},
            )
        if participants > available_capacity:
            raise EcoBookingValidationError(
                code="ECO_EXCEEDS_SLOT_CAPACITY",
                message=(
                    f"Requested participant count ({participants}) exceeds currently available "
                    f"slot capacity ({available_capacity})."
                ),
                details={
                    "requested_participants": participants,
                    "available_capacity": available_capacity,
                },
            )


def validate_participant_ages(
    participant_ages: Sequence[int] | None,
    min_age: int | None,
    expected_count: int | None = None,
) -> None:
    """
    Validate participant ages against minimum age requirements.

    Rules:
    - If min_age is None, no minimum age requirement is enforced (any valid age >= 0 is allowed).
    - If min_age is set, it must be between 0 and 100.
    - If participant_ages is provided:
        - If expected_count is provided, len(participant_ages) must match expected_count.
        - Every age must be an integer between 0 and 120.
        - If min_age is set, every age must be >= min_age.

    Raises:
        EcoBookingValidationError with specific error codes.
    """
    if min_age is not None:
        if isinstance(min_age, bool) or not isinstance(min_age, int) or min_age < 0 or min_age > 100:
            raise EcoBookingValidationError(
                code="ECO_INVALID_AGE_CONFIG",
                message=f"min_age must be an integer between 0 and 100, got: {min_age}.",
                details={"min_age": min_age},
            )

    if participant_ages is None:
        return

    if expected_count is not None and len(participant_ages) != expected_count:
        raise EcoBookingValidationError(
            code="ECO_PARTICIPANT_AGE_COUNT_MISMATCH",
            message=(
                f"Number of participant ages ({len(participant_ages)}) does not match "
                f"the requested participant count ({expected_count})."
            ),
            details={
                "ages_count": len(participant_ages),
                "expected_count": expected_count,
            },
        )

    for idx, age in enumerate(participant_ages):
        if isinstance(age, bool) or not isinstance(age, int) or age < 0 or age > 120:
            raise EcoBookingValidationError(
                code="ECO_INVALID_AGE",
                message=f"Participant {idx + 1} has an invalid age ({age}). Age must be between 0 and 120.",
                details={"index": idx, "age": age},
            )

        if min_age is not None and age < min_age:
            raise EcoBookingValidationError(
                code="ECO_AGE_BELOW_MINIMUM",
                message=(
                    f"Participant {idx + 1} (age {age}) is below the required minimum "
                    f"age of {min_age} years."
                ),
                details={
                    "index": idx,
                    "age": age,
                    "min_age": min_age,
                },
            )


def to_decimal_currency(val: Decimal | str | int | float) -> Decimal:
    """
    Convert a numeric input to a Decimal quantized to 2 decimal places with ROUND_HALF_UP.
    """
    try:
        dec = Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        raise EcoBookingValidationError(
            code="ECO_INVALID_BASE_PRICE",
            message=f"Unable to parse '{val}' into a valid monetary Decimal.",
            details={"value": str(val)},
        )
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_eco_booking_price(
    pricing_type: str,
    base_price: Decimal | str | int | float,
    participants: int,
    currency: str = "LKR",
) -> EcoPricingResult:
    """
    Calculate booking total price using Decimal precision.

    Rules:
    - pricing_type must be either 'per_person' or 'per_group'.
    - base_price must be non-negative.
    - participants must be a strictly positive integer.
    - per_person: total_price = base_price * participants
    - per_group: total_price = base_price (fixed for the entire reservation)

    Returns:
        EcoPricingResult with quantized Decimals.
    """
    if pricing_type not in ("per_person", "per_group"):
        raise EcoBookingValidationError(
            code="ECO_INVALID_PRICING_TYPE",
            message=(
                f"Unsupported pricing_type '{pricing_type}'. "
                f"Must be 'per_person' or 'per_group'."
            ),
            details={"pricing_type": pricing_type},
        )

    if isinstance(participants, bool) or not isinstance(participants, int) or participants <= 0:
        raise EcoBookingValidationError(
            code="ECO_INVALID_PARTICIPANTS",
            message=f"Participants must be a positive integer, got: {participants}.",
            details={"participants": participants},
        )

    dec_base_price = to_decimal_currency(base_price)

    if dec_base_price < Decimal("0.00"):
        raise EcoBookingValidationError(
            code="ECO_INVALID_BASE_PRICE",
            message=f"Base price cannot be negative, got: {dec_base_price}.",
            details={"base_price": str(dec_base_price)},
        )

    if pricing_type == "per_person":
        total = (dec_base_price * Decimal(participants)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:  # per_group
        total = dec_base_price

    return EcoPricingResult(
        pricing_type=pricing_type,
        base_price=dec_base_price,
        participants=participants,
        total_price=total,
        currency=currency,
    )


def evaluate_eco_cancellation_eligibility(
    slot_start: datetime,
    cancellation_requested_at: datetime | None,
    free_cancellation_hours: int,
) -> EcoCancellationResult:
    """
    Evaluate cancellation eligibility against the listing's free_cancellation_hours policy.

    Rules:
    - slot_start and cancellation_requested_at are normalized to UTC.
    - If cancellation_requested_at is None, current UTC time is used.
    - free_cancellation_hours must be an integer >= 0.
    - cancellation_deadline = slot_start - timedelta(hours=free_cancellation_hours).
    - If cancellation_requested_at >= slot_start:
        Raises error because the experience has already started or concluded.
    - If cancellation_requested_at <= cancellation_deadline:
        Eligible for free cancellation (no penalty).
    - If cancellation_requested_at > cancellation_deadline:
        Ineligible for free cancellation (late cancellation / penalty applicable).

    Returns:
        EcoCancellationResult with exact timing analysis.
    """
    if isinstance(free_cancellation_hours, bool) or not isinstance(free_cancellation_hours, int) or free_cancellation_hours < 0:
        raise EcoBookingValidationError(
            code="ECO_INVALID_CANCELLATION_HOURS",
            message=f"free_cancellation_hours must be an integer >= 0, got: {free_cancellation_hours}.",
            details={"free_cancellation_hours": free_cancellation_hours},
        )

    start_utc = normalize_datetime_utc(slot_start)
    req_utc = (
        normalize_datetime_utc(cancellation_requested_at)
        if cancellation_requested_at is not None
        else datetime.now(UTC)
    )

    if req_utc >= start_utc:
        raise EcoBookingValidationError(
            code="ECO_SLOT_ALREADY_STARTED",
            message=(
                f"Cancellation requested at ({req_utc.isoformat()}) after slot "
                f"start time ({start_utc.isoformat()}). Experiences cannot be cancelled after commencement."
            ),
            details={
                "slot_start": start_utc.isoformat(),
                "cancellation_requested_at": req_utc.isoformat(),
            },
        )

    deadline = start_utc - timedelta(hours=free_cancellation_hours)
    is_free = req_utc <= deadline
    seconds_remaining = (start_utc - req_utc).total_seconds()
    hours_remaining = round(seconds_remaining / 3600.0, 4)

    return EcoCancellationResult(
        is_free_cancellation=is_free,
        cancellation_deadline=deadline,
        cancellation_requested_at=req_utc,
        slot_start=start_utc,
        free_cancellation_hours=free_cancellation_hours,
        hours_until_slot=hours_remaining,
        penalty_applicable=not is_free,
    )


def validate_and_price_eco_booking(
    participants: int,
    min_participants: int,
    max_group_size: int,
    pricing_type: str,
    base_price: Decimal | str | int | float,
    min_age: int | None = None,
    participant_ages: Sequence[int] | None = None,
    available_capacity: int | None = None,
    currency: str = "LKR",
) -> EcoBookingValidationSummary:
    """
    Convenience orchestrator for complete Module 04 booking request validation and pricing.
    """
    # 1. Group size validation
    validate_group_size(
        participants=participants,
        min_participants=min_participants,
        max_group_size=max_group_size,
        available_capacity=available_capacity,
    )

    # 2. Participant ages validation
    validate_participant_ages(
        participant_ages=participant_ages,
        min_age=min_age,
        expected_count=participants if participant_ages is not None else None,
    )

    # 3. Pricing calculation
    pricing = calculate_eco_booking_price(
        pricing_type=pricing_type,
        base_price=base_price,
        participants=participants,
        currency=currency,
    )

    return EcoBookingValidationSummary(
        valid=True,
        participants=participants,
        pricing=pricing,
        min_age_enforced=min_age,
        participant_ages=list(participant_ages) if participant_ages is not None else None,
    )
