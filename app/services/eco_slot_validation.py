"""
Module 04 — Eco-Tourism Slot Scheduling Validation Service.

Provides reusable, independent validation logic for Eco-Tourism scheduling slots:
1. Provider ownership and role prerequisites (provider_type == 'eco_tourism', approved status).
2. Listing approval and publication prerequisites (listing_status == 'approved', is_published == True).
3. Slot timing validity (timezone-aware UTC, future starts_at, ends_at > starts_at, max scheduling horizon).
4. Environmental capacity limits (1 <= capacity <= 50, capacity <= max_group_size, capacity >= min_participants).
5. Overlapping slot collision detection (starts_at < existing_ends_at and ends_at > existing_starts_at).
6. Seasonal restrictions and closure periods (active monsoon, breeding, park closure overlaps).

STRICT ISOLATION & READ-ONLY INTEGRITY:
- Executes read-only queries when a database session is provided.
- NEVER executes database writes, commits, or modifications.
- Functions are decomposed to support pure in-memory unit testing without database access.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.schemas.eco_slot import ENVIRONMENTAL_MAX_SLOT_CAPACITY, EcoSlotCreate
from app.services.eco_booking_pricing import normalize_datetime_utc


class EcoSlotValidationError(ValueError):
    """
    Exception raised when an eco slot scheduling validation fails.

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
class EcoSlotValidationResult:
    """
    Structured outcome of a successful eco slot validation.
    """

    eco_experience_id: int
    starts_at_utc: datetime
    ends_at_utc: datetime
    capacity: int
    duration_hours: float
    provider_profile_id: int
    experience_title: str
    max_group_size: int
    min_participants: int
    is_valid: bool = True


# ==============================================================================
# 1. Pure In-Memory Validation Helpers
# ==============================================================================


def validate_slot_timing_bounds(
    starts_at: datetime,
    ends_at: datetime,
    now_utc: datetime | None = None,
    min_duration_minutes: int | None = None,
    max_horizon_days: int | None = None,
) -> tuple[datetime, datetime]:
    """
    Pure validation of slot start and end timing based strictly on official specifications:
    - ends_at > starts_at (CK_activity_slots_time)
    - starts_at strictly in the future relative to now_utc.
    - All datetimes normalized to timezone-aware UTC.
    - Optional bounds (min_duration_minutes, max_horizon_days) applied only when specified.

    Returns:
        tuple[datetime, datetime]: (starts_at_utc, ends_at_utc) normalized.
    """
    start_utc = normalize_datetime_utc(starts_at)
    end_utc = normalize_datetime_utc(ends_at)
    current_utc = now_utc if now_utc is not None else datetime.now(UTC)

    if start_utc <= current_utc:
        raise EcoSlotValidationError(
            code="ECO_INVALID_SLOT_TIMING",
            message=(
                f"Slot start time ({start_utc.isoformat()}) must be in the future. "
                f"Current time: {current_utc.isoformat()}."
            ),
            details={
                "starts_at": start_utc.isoformat(),
                "current_time": current_utc.isoformat(),
            },
        )

    if end_utc <= start_utc:
        raise EcoSlotValidationError(
            code="ECO_INVALID_SLOT_TIMING",
            message=f"Slot end time ({end_utc.isoformat()}) must be strictly after start time ({start_utc.isoformat()}).",
            details={
                "starts_at": start_utc.isoformat(),
                "ends_at": end_utc.isoformat(),
            },
        )

    duration_seconds = (end_utc - start_utc).total_seconds()
    if min_duration_minutes is not None and duration_seconds < (min_duration_minutes * 60):
        raise EcoSlotValidationError(
            code="ECO_SLOT_DURATION_TOO_SHORT",
            message=(
                f"Slot duration ({round(duration_seconds / 60, 1)} mins) is shorter than the "
                f"required {min_duration_minutes} minutes."
            ),
            details={
                "duration_minutes": duration_seconds / 60,
                "min_duration_minutes": min_duration_minutes,
            },
        )

    if max_horizon_days is not None:
        horizon_cutoff = current_utc + timedelta(days=max_horizon_days)
        if start_utc > horizon_cutoff:
            raise EcoSlotValidationError(
                code="ECO_SLOT_EXCEEDS_SCHEDULING_HORIZON",
                message=(
                    f"Slot start time ({start_utc.isoformat()}) exceeds the maximum scheduling "
                    f"horizon of {max_horizon_days} days."
                ),
                details={
                    "starts_at": start_utc.isoformat(),
                    "max_horizon_days": max_horizon_days,
                },
            )

    return start_utc, end_utc


def validate_slot_capacity_against_listing(
    capacity: int,
    listing_max_group_size: int,
    listing_min_participants: int = 1,
) -> None:
    """
    Validate slot capacity against environmental ceiling and listing-level parameters.

    Rules:
    - 1 <= capacity <= ENVIRONMENTAL_MAX_SLOT_CAPACITY (50).
    - capacity cannot exceed listing_max_group_size.
    - capacity cannot be less than listing_min_participants (minimum viable group).
    """
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise EcoSlotValidationError(
            code="ECO_INVALID_CAPACITY",
            message=f"Slot capacity must be a positive integer, got: {capacity}.",
            details={"capacity": capacity},
        )

    if capacity > ENVIRONMENTAL_MAX_SLOT_CAPACITY:
        raise EcoSlotValidationError(
            code="ECO_EXCEEDS_ENVIRONMENTAL_CEILING",
            message=(
                f"Slot capacity ({capacity}) exceeds the environmental maximum ceiling of "
                f"{ENVIRONMENTAL_MAX_SLOT_CAPACITY} participants."
            ),
            details={
                "capacity": capacity,
                "environmental_ceiling": ENVIRONMENTAL_MAX_SLOT_CAPACITY,
            },
        )

    if capacity > listing_max_group_size:
        raise EcoSlotValidationError(
            code="ECO_SLOT_CAPACITY_EXCEEDS_LISTING",
            message=(
                f"Slot capacity ({capacity}) exceeds listing maximum group size "
                f"({listing_max_group_size})."
            ),
            details={
                "capacity": capacity,
                "listing_max_group_size": listing_max_group_size,
            },
        )

    if capacity < listing_min_participants:
        raise EcoSlotValidationError(
            code="ECO_SLOT_CAPACITY_BELOW_MINIMUM",
            message=(
                f"Slot capacity ({capacity}) is below listing minimum viable participants "
                f"({listing_min_participants}). A group cannot be formed."
            ),
            details={
                "capacity": capacity,
                "listing_min_participants": listing_min_participants,
            },
        )


def check_slot_collision_in_memory(
    new_start: datetime,
    new_end: datetime,
    existing_slots: Sequence[Any],
) -> None:
    """
    Check if a new slot window overlaps any existing active slots.

    Overlapping Condition:
        new_start < existing_end AND new_end > existing_start
    Note: Adjacent slots (new_start == existing_end or new_end == existing_start) are ALLOWED.
    """
    start_utc = normalize_datetime_utc(new_start)
    end_utc = normalize_datetime_utc(new_end)

    for slot in existing_slots:
        # Check if slot is active
        is_active = getattr(slot, "is_active", True)
        if not is_active:
            continue

        slot_start = normalize_datetime_utc(getattr(slot, "starts_at"))
        slot_end = normalize_datetime_utc(getattr(slot, "ends_at"))

        # Overlap check
        if start_utc < slot_end and end_utc > slot_start:
            slot_id = getattr(slot, "id", None)
            raise EcoSlotValidationError(
                code="ECO_SLOT_OVERLAPPING",
                message=(
                    f"Slot window ({start_utc.isoformat()} to {end_utc.isoformat()}) overlaps "
                    f"an existing active slot ({slot_start.isoformat()} to {slot_end.isoformat()})."
                ),
                details={
                    "overlapping_slot_id": slot_id,
                    "new_slot": {"start": start_utc.isoformat(), "end": end_utc.isoformat()},
                    "existing_slot": {"start": slot_start.isoformat(), "end": slot_end.isoformat()},
                },
            )


def check_seasonal_restrictions_in_memory(
    slot_start: datetime,
    slot_end: datetime,
    restrictions: Sequence[Any],
) -> None:
    """
    Check if a slot window overlaps any active seasonal restrictions or closures.
    """
    start_date = normalize_datetime_utc(slot_start).date()
    end_date = normalize_datetime_utc(slot_end).date()

    for restriction in restrictions:
        is_active = getattr(restriction, "is_active", True)
        if not is_active:
            continue

        r_start = getattr(restriction, "start_date")
        r_end = getattr(restriction, "end_date")
        r_type = getattr(restriction, "restriction_type", "seasonal_closure")
        r_reason = getattr(restriction, "reason", "Seasonal restriction active")
        r_id = getattr(restriction, "id", None)

        if start_date <= r_end and end_date >= r_start:
            raise EcoSlotValidationError(
                code="ECO_SEASON_RESTRICTED",
                message=(
                    f"Slot date range ({start_date} to {end_date}) overlaps with active "
                    f"seasonal restriction '{r_type}': {r_reason} ({r_start} to {r_end})."
                ),
                details={
                    "restriction_id": r_id,
                    "restriction_type": r_type,
                    "reason": r_reason,
                    "start_date": str(r_start),
                    "end_date": str(r_end),
                },
            )


# ==============================================================================
# 2. Provider Ownership & Experience Readiness Validation
# ==============================================================================


def validate_provider_scheduling_prerequisites(
    provider_profile: Any,
) -> None:
    """
    Ensure the authenticated caller is an approved service provider of type 'eco_tourism'.
    """
    p_type = getattr(provider_profile, "provider_type", None)
    if p_type != "eco_tourism":
        raise EcoSlotValidationError(
            code="ECO_PROVIDER_NOT_AUTHORIZED",
            message=f"Only 'eco_tourism' providers can manage eco slots (current type: '{p_type}').",
            details={"provider_type": p_type},
        )

    approval = getattr(provider_profile, "approval_status", None)
    if approval != "approved":
        raise EcoSlotValidationError(
            code="ECO_PROVIDER_NOT_APPROVED",
            message=f"Provider profile must be 'approved' to manage slots (current status: '{approval}').",
            details={"approval_status": approval},
        )


def validate_eco_experience_readiness_and_ownership(
    experience: Any,
    provider_profile_id: int,
) -> None:
    """
    Verify ownership, approval, and publication status of the target EcoExperience.
    """
    if experience is None:
        raise EcoSlotValidationError(
            code="ECO_EXPERIENCE_NOT_FOUND",
            message="Target EcoExperience was not found.",
        )

    exp_provider_id = getattr(experience, "provider_profile_id", None)
    if exp_provider_id != provider_profile_id:
        raise EcoSlotValidationError(
            code="ECO_EXPERIENCE_NOT_OWNED",
            message="Access denied: You do not own this eco-experience listing.",
            details={
                "experience_provider_id": exp_provider_id,
                "caller_provider_id": provider_profile_id,
            },
        )

    status = getattr(experience, "listing_status", None)
    if status != "approved":
        raise EcoSlotValidationError(
            code="ECO_EXPERIENCE_NOT_APPROVED",
            message=(
                f"Slots can only be scheduled on 'approved' eco-experiences "
                f"(current status: '{status}')."
            ),
            details={"listing_status": status},
        )

    is_published = getattr(experience, "is_published", False)
    if not is_published:
        raise EcoSlotValidationError(
            code="ECO_EXPERIENCE_NOT_PUBLISHED",
            message="Slots can only be scheduled on published eco-experiences.",
            details={"is_published": is_published},
        )


# ==============================================================================
# 3. Read-Only Database-Backed Orchestrator
# ==============================================================================


def validate_eco_slot_creation_read_only(
    db: Session,
    eco_experience_id: int,
    provider_profile: ProviderProfile,
    payload: EcoSlotCreate,
    now_utc: datetime | None = None,
) -> EcoSlotValidationResult:
    """
    Comprehensive read-only validation orchestrator for eco slot creation.

    Queries Executed (READ-ONLY):
    1. SELECT EcoExperience with season_restrictions by primary key.
    2. SELECT active ActivitySlots where eco_experience_id = id and time overlaps.

    ZERO WRITES, MODIFICATIONS, OR COMMITS EXECUTED.

    Raises:
        EcoSlotValidationError upon any prerequisite or integrity failure.
    """
    # 1. Validate provider role and approval
    validate_provider_scheduling_prerequisites(provider_profile)

    # 2. Read-only retrieval of EcoExperience
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == eco_experience_id)
        .first()
    )
    validate_eco_experience_readiness_and_ownership(experience, provider_profile.id)

    # 3. Validate slot start and end timing
    start_utc, end_utc = validate_slot_timing_bounds(
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        now_utc=now_utc,
    )

    # 4. Validate slot capacity against listing parameters
    validate_slot_capacity_against_listing(
        capacity=payload.capacity,
        listing_max_group_size=experience.max_group_size,
        listing_min_participants=experience.min_participants,
    )

    # 5. Check active seasonal restrictions
    active_restrictions = (
        db.query(EcoSeasonRestriction)
        .filter(
            EcoSeasonRestriction.eco_experience_id == eco_experience_id,
            EcoSeasonRestriction.is_active.is_(True),
        )
        .all()
    )
    check_seasonal_restrictions_in_memory(
        slot_start=start_utc,
        slot_end=end_utc,
        restrictions=active_restrictions,
    )

    # 6. Read-only query for overlapping active slots on the same experience
    overlapping_slot = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.eco_experience_id == eco_experience_id,
            ActivitySlot.is_active.is_(True),
            ActivitySlot.starts_at < end_utc,
            ActivitySlot.ends_at > start_utc,
        )
        .first()
    )

    if overlapping_slot is not None:
        raise EcoSlotValidationError(
            code="ECO_SLOT_OVERLAPPING",
            message=(
                f"Slot window ({start_utc.isoformat()} to {end_utc.isoformat()}) overlaps "
                f"an existing active slot (ID: {overlapping_slot.id})."
            ),
            details={
                "overlapping_slot_id": overlapping_slot.id,
                "starts_at": overlapping_slot.starts_at.isoformat(),
                "ends_at": overlapping_slot.ends_at.isoformat(),
            },
        )

    duration_hours = round((end_utc - start_utc).total_seconds() / 3600.0, 2)

    return EcoSlotValidationResult(
        eco_experience_id=experience.id,
        starts_at_utc=start_utc,
        ends_at_utc=end_utc,
        capacity=payload.capacity,
        duration_hours=duration_hours,
        provider_profile_id=provider_profile.id,
        experience_title=experience.title,
        max_group_size=experience.max_group_size,
        min_participants=experience.min_participants,
        is_valid=True,
    )
