"""
Reusable Shared Eco-Tourism Eligibility Service for CeylonGo Backend.

Import Path:
    `from app.services.eco_experience_eligibility import validate_eco_booking_eligibility, EcoEligibilityResult, EcoEligibilityException`

Confirmed Interface:
    validate_eco_booking_eligibility(
        db: Session,
        eco_experience_id: int,
        participants: int | None,
        slot_start: datetime,
        slot_end: datetime,
    ) -> EcoEligibilityResult

Confirmed Error Codes:
    - ECO_NOT_FOUND: Experience with the given ID does not exist.
    - ECO_NOT_APPROVED: Experience listing_status is not 'approved'.
    - ECO_NOT_PUBLISHED: Experience is_published is False.
    - ECO_GROUP_SIZE_EXCEEDED: Requested participants exceeds max_group_size.
    - ECO_SEASON_RESTRICTED: Slot window overlaps with an active EcoSeasonRestriction.
    - ECO_INVALID_SLOT_WINDOW: Slot start/end times are invalid (e.g. end <= start).
    - ECO_INVALID_PARTICIPANTS: Participants is not a positive integer (when provided).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session, selectinload

from app.models.eco_experience import EcoExperience, EcoSeasonRestriction


class EcoEligibilityException(Exception):
    """
    Exception raised when an eco experience fails scheduling, availability,
    or booking eligibility checks.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable explanation.
        details: Optional contextual dictionary with validation details.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass(frozen=True)
class EcoEligibilityResult:
    """
    Structured outcome returned when an eco experience is eligible for a slot or booking.

    Attributes:
        eco_experience_id: Unique identifier of the EcoExperience.
        eligible: Always True when returned.
        max_group_size: Maximum participants allowed by environmental rules (<= 50).
        min_participants: Minimum viable group size.
        pricing_type: 'per_person' or 'per_group'.
        base_price: Baseline price in currency.
        currency: Pricing currency (default 'LKR').
        free_cancellation_hours: Free cancellation cutoff threshold in hours.
        meeting_point: Meeting and rendezvous location.
        environmental_guidelines: Site-specific responsible travel instructions.
        wildlife_safety_summary: Safety precautions, no-feeding rules, and operator instructions.
        mandatory_safety_rules: List of mandatory safety guidelines.
        has_verified_eco_badge: True if the experience holds an unexpired admin eco badge.
    """

    eco_experience_id: int
    eligible: bool
    max_group_size: int
    min_participants: int
    pricing_type: str
    base_price: Decimal
    currency: str
    free_cancellation_hours: int
    meeting_point: str
    environmental_guidelines: str
    wildlife_safety_summary: str
    mandatory_safety_rules: list[str] = field(default_factory=list)
    has_verified_eco_badge: bool = False


def _normalize_to_utc(dt: datetime) -> datetime:
    """
    Normalize datetimes to timezone-aware UTC.
    If naive datetime is supplied, assume UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def validate_eco_booking_eligibility(
    db: Session,
    eco_experience_id: int,
    participants: int | None,
    slot_start: datetime,
    slot_end: datetime,
    *,
    calendar_timezone: tzinfo | None = None,
) -> EcoEligibilityResult:
    """
    Shared eligibility service invoked by Module 03 (Activity Scheduling & Booking)
    during slot creation, public slot availability search, and booking creation.

    Args:
        db: Active SQLAlchemy database session.
        eco_experience_id: Primary key of the EcoExperience.
        participants: Requested participant count, or None for slot creation/availability.
        slot_start: Slot start datetime (normalized to UTC).
        slot_end: Slot end datetime (normalized to UTC).

    Returns:
        EcoEligibilityResult with structured operational and pricing details.

    Raises:
        EcoEligibilityException with a stable `code` attribute.
    """
    # 1. Validate slot window timing
    if not isinstance(slot_start, datetime) or not isinstance(slot_end, datetime):
        raise EcoEligibilityException(
            code="ECO_INVALID_SLOT_WINDOW",
            message="slot_start and slot_end must be datetime objects.",
        )

    start_utc = _normalize_to_utc(slot_start)
    end_utc = _normalize_to_utc(slot_end)

    if end_utc <= start_utc:
        raise EcoEligibilityException(
            code="ECO_INVALID_SLOT_WINDOW",
            message=f"slot_end ({end_utc}) must be strictly after slot_start ({start_utc}).",
            details={
                "slot_start": start_utc.isoformat(),
                "slot_end": end_utc.isoformat(),
            },
        )

    # 2. Retrieve EcoExperience with relationships
    experience = (
        db.query(EcoExperience)
        .options(
            selectinload(EcoExperience.season_restrictions),
            selectinload(EcoExperience.safety_rules),
            selectinload(EcoExperience.verifications),
        )
        .filter(EcoExperience.id == eco_experience_id)
        .first()
    )

    if not experience:
        raise EcoEligibilityException(
            code="ECO_NOT_FOUND",
            message=f"EcoExperience with id {eco_experience_id} was not found.",
            details={"eco_experience_id": eco_experience_id},
        )

    # 3. Validate admin approval status
    if experience.listing_status != "approved":
        raise EcoEligibilityException(
            code="ECO_NOT_APPROVED",
            message=(
                f"EcoExperience {eco_experience_id} is not approved "
                f"(current status: '{experience.listing_status}')."
            ),
            details={
                "eco_experience_id": eco_experience_id,
                "listing_status": experience.listing_status,
            },
        )

    # 4. Validate publication status
    if not experience.is_published:
        raise EcoEligibilityException(
            code="ECO_NOT_PUBLISHED",
            message=f"EcoExperience {eco_experience_id} is not published.",
            details={
                "eco_experience_id": eco_experience_id,
                "is_published": experience.is_published,
            },
        )

    # 5. Validate participant count if provided
    if participants is not None:
        if (
            isinstance(participants, bool)
            or not isinstance(participants, int)
            or participants <= 0
        ):
            raise EcoEligibilityException(
                code="ECO_INVALID_PARTICIPANTS",
                message=f"Participants count must be a positive integer, got: {participants}.",
                details={"participants": participants},
            )

        if participants > experience.max_group_size:
            raise EcoEligibilityException(
                code="ECO_GROUP_SIZE_EXCEEDED",
                message=(
                    f"Requested participants ({participants}) exceeds maximum "
                    f"allowed group size of {experience.max_group_size}."
                ),
                details={
                    "eco_experience_id": eco_experience_id,
                    "requested_participants": participants,
                    "max_group_size": experience.max_group_size,
                },
            )

    # 6. Validate seasonal restrictions across slot date window
    slot_start_date, slot_end_date = start_utc.date(), end_utc.date()
    if calendar_timezone is not None:
        slot_start_date = start_utc.astimezone(calendar_timezone).date()
        slot_end_date = (end_utc - timedelta(microseconds=1)).astimezone(calendar_timezone).date()

    for restriction in experience.season_restrictions:
        if restriction.is_active:
            # Overlap check: [slot_start_date, slot_end_date] and [restriction.start_date, restriction.end_date]
            if (
                slot_start_date <= restriction.end_date
                and slot_end_date >= restriction.start_date
            ):
                raise EcoEligibilityException(
                    code="ECO_SEASON_RESTRICTED",
                    message=(
                        f"Slot window ({slot_start_date} to {slot_end_date}) overlaps with active "
                        f"seasonal restriction '{restriction.restriction_type}': {restriction.reason} "
                        f"({restriction.start_date} to {restriction.end_date})."
                    ),
                    details={
                        "eco_experience_id": eco_experience_id,
                        "restriction_id": restriction.id,
                        "restriction_type": restriction.restriction_type,
                        "reason": restriction.reason,
                        "start_date": str(restriction.start_date),
                        "end_date": str(restriction.end_date),
                    },
                )

    # 7. Collect mandatory safety rules
    mandatory_rules = [
        rule.title
        for rule in experience.safety_rules
        if rule.is_mandatory
    ]

    return EcoEligibilityResult(
        eco_experience_id=experience.id,
        eligible=True,
        max_group_size=experience.max_group_size,
        min_participants=experience.min_participants,
        pricing_type=experience.pricing_type,
        base_price=experience.base_price,
        currency=experience.currency,
        free_cancellation_hours=experience.free_cancellation_hours,
        meeting_point=experience.meeting_point,
        environmental_guidelines=experience.environmental_guidelines,
        wildlife_safety_summary=experience.wildlife_safety_summary,
        mandatory_safety_rules=mandatory_rules,
        has_verified_eco_badge=experience.has_verified_eco_badge,
    )
