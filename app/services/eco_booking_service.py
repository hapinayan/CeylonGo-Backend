"""
Module 04 — Traveler Eco-Tourism Booking Service.

Provides independent business logic for Eco-Tourism bookings:
1. Traveler identity and authorization validation (must have role 'traveler').
2. Slot and experience consistency checks (active slot, future start, approved/published listing).
3. Participant count and group bounds validation (reusing eco_booking_pricing).
4. Minimum-age verification for participants (reusing eco_booking_pricing).
5. Server-side price calculation using Python Decimal with 2-decimal precision.
6. Payment method restrictions (strictly 'pay_at_location' in Phase 4; 'online' rejected).
7. Initial booking status lifecycle enforcement matching database CHECK constraints:
   - booking_status = 'pending'
   - payment_status = 'unpaid'
   - Provider acceptance is required before confirmation.
8. Cancellation policy and cutoff validation against free_cancellation_hours.
9. Canonical booking reference formatting (ECO-YYYYMMDD-XXXX).
10. Capacity reservation and release design (wired by contract to activity_capacity).

STRICT READ-ONLY INTEGRITY:
- Executes read-only queries during validation.
- ZERO database writes, insertions, or commits executed.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
import logging
from typing import Any, Sequence

from sqlalchemy import Boolean, Integer, Numeric, Unicode, column, func, inspect, table, text
from sqlalchemy.orm import Session

from app.core.notification_service import create_notification
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference, Notification, PaymentRefund, PaymentTransaction
from app.models.user import User

logger = logging.getLogger(__name__)
from app.schemas.eco_booking import (
    EcoBookingCreate,
    EcoBookingProviderDecision,
    EcoParticipantCreate,
)
from app.services.eco_booking_pricing import (
    EcoCancellationResult,
    EcoPricingResult,
    calculate_eco_booking_price,
    evaluate_eco_cancellation_eligibility,
    normalize_datetime_utc,
    validate_group_size,
    validate_participant_ages,
)


class EcoBookingServiceError(ValueError):
    """
    Raised when an eco-tourism booking or cancellation validation fails.

    Attributes:
        code: Stable machine-readable error code.
        message: Human-readable error description.
        details: Contextual dictionary for API error responses.
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
class EcoBookingValidationPlan:
    """
    Structured outcome of an eco-experience booking validation.
    Contains verified operational parameters ready for atomic reservation.
    """

    eco_experience_id: int
    slot_id: int
    traveler_id: int
    participants: int
    pricing: EcoPricingResult
    payment_method: str
    booking_status: str
    payment_status: str
    special_requests: str | None
    participant_roster: list[dict[str, Any]] | None
    experience_title: str
    slot_starts_at: datetime
    provider_profile_id: int = 1
    is_valid: bool = True


@dataclass(frozen=True)
class EcoCancellationValidationPlan:
    """
    Structured outcome of an eco-experience cancellation validation.
    Contains verified operational parameters ready for atomic capacity release.
    """

    booking_id: int
    traveler_id: int
    slot_id: int
    eco_experience_id: int
    participants_to_release: int
    cancellation: EcoCancellationResult
    reason: str
    is_valid: bool = True


def format_eco_booking_reference(booking_id: int, created_at: datetime) -> str:
    """
    Format standard canonical reference code: ECO-YYYYMMDD-XXXX.
    """
    date_str = created_at.strftime("%Y%m%d")
    return f"ECO-{date_str}-{booking_id:04d}"


def validate_traveler_authorization(user: Any) -> None:
    """
    Verify that caller has the authenticated traveler role.
    """
    if getattr(user, "role", None) != "traveler":
        raise EcoBookingServiceError(
            code="ECO_USER_NOT_TRAVELER",
            message="Only authenticated users with the 'traveler' role can create or cancel eco bookings.",
            details={"current_role": getattr(user, "role", None)},
        )

    if not getattr(user, "is_active", True):
        raise EcoBookingServiceError(
            code="ECO_USER_INACTIVE",
            message="User account is inactive.",
        )


def validate_booking_request_read_only(
    db: Session,
    traveler: User,
    payload: EcoBookingCreate,
    now_utc: datetime | None = None,
) -> EcoBookingValidationPlan:
    """
    Comprehensive read-only validation of an eco booking submission.

    Rules Enforced:
    1. Caller must have role 'traveler' and be active.
    2. Payment method must be 'pay_at_location' (online payments are disabled in Phase 4).
    3. Slot must exist, be active (is_active == True), and start in the future.
    4. EcoExperience must exist, be 'approved', and 'is_published' == True.
    5. Slot must be linked to the target EcoExperience.
    6. Participant count must satisfy min_participants <= N <= max_group_size and <= available_capacity.
    7. Participant ages must satisfy listing's min_age (if roster provided).
    8. Total price is calculated server-side with Decimal precision.
    9. Initial status lifecycle:
       - booking_status = 'pending'
       - payment_status = 'unpaid'
       (Provider acceptance is required before confirmation).

    ZERO DATABASE WRITES EXECUTED.

    Raises:
        EcoBookingServiceError on validation failure.
    """
    # 1. Authorize traveler
    validate_traveler_authorization(traveler)

    # 2. Payment method restriction (Pay-at-location only for this phase)
    if payload.payment_method != "pay_at_location":
        raise EcoBookingServiceError(
            code="ECO_ONLINE_PAYMENT_UNSUPPORTED",
            message=(
                f"Payment method '{payload.payment_method}' is not supported in this phase. "
                "Only 'pay_at_location' is currently enabled."
            ),
            details={"payment_method": payload.payment_method},
        )

    # 3. Read-only slot lookup
    slot = (
        db.query(ActivitySlot)
        .filter(ActivitySlot.id == payload.eco_slot_id)
        .first()
    )

    if slot is None:
        raise EcoBookingServiceError(
            code="ECO_SLOT_NOT_FOUND",
            message=f"Activity slot with id {payload.eco_slot_id} was not found.",
            details={"eco_slot_id": payload.eco_slot_id},
        )

    if not slot.is_active:
        raise EcoBookingServiceError(
            code="ECO_SLOT_NOT_ACTIVE",
            message="This scheduling slot is no longer active or has been cancelled.",
            details={"eco_slot_id": slot.id},
        )

    current_time = now_utc if now_utc is not None else datetime.now(UTC)
    slot_start = normalize_datetime_utc(slot.starts_at)

    if slot_start <= current_time:
        raise EcoBookingServiceError(
            code="ECO_SLOT_IN_PAST",
            message=f"Slot start time ({slot_start.isoformat()}) has already passed.",
            details={"slot_starts_at": slot_start.isoformat()},
        )

    # 4. Read-only EcoExperience lookup
    experience_id = slot.eco_experience_id
    if not experience_id:
        raise EcoBookingServiceError(
            code="ECO_SLOT_EXPERIENCE_MISMATCH",
            message="Target slot is not associated with an Eco-Tourism experience.",
            details={"eco_slot_id": slot.id},
        )

    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == experience_id)
        .first()
    )

    if not experience:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_FOUND",
            message=f"EcoExperience {experience_id} was not found.",
            details={"eco_experience_id": experience_id},
        )

    if experience.listing_status != "approved" or not experience.is_published:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_ACTIVE",
            message="This eco-experience is not approved or is currently unpublished.",
            details={
                "listing_status": experience.listing_status,
                "is_published": experience.is_published,
            },
        )

    # 4b. Check active seasonal restrictions on target slot (E06)
    slot_start_date = slot_start.date()
    slot_end_date = normalize_datetime_utc(slot.ends_at).date()
    active_restriction = (
        db.query(EcoSeasonRestriction)
        .filter(
            EcoSeasonRestriction.eco_experience_id == experience_id,
            EcoSeasonRestriction.is_active.is_(True),
            EcoSeasonRestriction.start_date <= slot_end_date,
            EcoSeasonRestriction.end_date >= slot_start_date,
        )
        .first()
    )
    if active_restriction is not None:
        raise EcoBookingServiceError(
            code="ECO_SEASON_RESTRICTED",
            message=(
                f"Cannot book slot: experience has an active seasonal or environmental restriction "
                f"'{active_restriction.restriction_type}': {active_restriction.reason} "
                f"({active_restriction.start_date} to {active_restriction.end_date})."
            ),
            details={
                "restriction_id": active_restriction.id,
                "restriction_type": active_restriction.restriction_type,
                "reason": active_restriction.reason,
                "start_date": str(active_restriction.start_date),
                "end_date": str(active_restriction.end_date),
            },
        )

    # 5. Capacity and group bounds validation
    available_capacity = max(0, slot.capacity - slot.reserved_count)
    try:
        validate_group_size(
            participants=payload.participants,
            min_participants=experience.min_participants,
            max_group_size=experience.max_group_size,
            available_capacity=available_capacity,
        )
    except Exception as err:
        code = getattr(err, "code", "ECO_INVALID_GROUP_SIZE")
        details = getattr(err, "details", {})
        raise EcoBookingServiceError(code=code, message=str(err), details=details) from err

    # 6. Minimum-age and roster validation
    ages_list = None
    roster_dicts = None
    if payload.participants_roster:
        ages_list = [p.age for p in payload.participants_roster if p.age is not None]
        roster_dicts = [p.model_dump() for p in payload.participants_roster]

        try:
            validate_participant_ages(
                participant_ages=ages_list,
                min_age=experience.min_age,
                expected_count=payload.participants if len(ages_list) == payload.participants else None,
            )
        except Exception as err:
            code = getattr(err, "code", "ECO_INVALID_AGE")
            details = getattr(err, "details", {})
            raise EcoBookingServiceError(code=code, message=str(err), details=details) from err

    # 7. Pricing calculation
    try:
        pricing_result = calculate_eco_booking_price(
            pricing_type=experience.pricing_type,
            base_price=experience.base_price,
            participants=payload.participants,
            currency=experience.currency,
        )
    except Exception as err:
        code = getattr(err, "code", "ECO_INVALID_PRICING")
        details = getattr(err, "details", {})
        raise EcoBookingServiceError(code=code, message=str(err), details=details) from err

    # 8. Initial status lifecycle matching database CHECK constraints
    # - CK_activity_bookings_status: 'pending'
    # - CK_activity_bookings_payment: 'unpaid'
    return EcoBookingValidationPlan(
        eco_experience_id=experience.id,
        slot_id=slot.id,
        traveler_id=traveler.id,
        participants=payload.participants,
        pricing=pricing_result,
        payment_method="pay_at_location",
        booking_status="pending",
        payment_status="unpaid",
        special_requests=payload.special_requests,
        participant_roster=roster_dicts,
        experience_title=experience.title,
        slot_starts_at=slot_start,
        provider_profile_id=getattr(experience, "provider_profile_id", 1) or 1,
        is_valid=True,
    )


def validate_booking_cancellation_read_only(
    db: Session,
    traveler: User,
    booking_id: int,
    reason: str,
    now_utc: datetime | None = None,
) -> EcoCancellationValidationPlan:
    """
    Comprehensive read-only validation of an eco booking cancellation request.

    Rules Enforced:
    1. Caller must have role 'traveler' and own the booking.
    2. Booking must belong to an EcoExperience (eco_experience_id is not None).
    3. Booking status cannot be 'cancelled' (cannot cancel twice).
    4. Booking status must be 'pending' or 'confirmed'.
    5. Cancellation cutoff is evaluated against EcoExperience.free_cancellation_hours.
    6. Identifies exact participant capacity to be released.

    ZERO DATABASE WRITES EXECUTED.

    Raises:
        EcoBookingServiceError on cancellation ineligibility.
    """
    # 1. Authorize traveler
    validate_traveler_authorization(traveler)

    if not reason or len(reason.strip()) < 5:
        raise EcoBookingServiceError(
            code="ECO_INVALID_CANCELLATION_REASON",
            message="Cancellation reason must be at least 5 non-whitespace characters.",
        )

    # 2. Read-only booking lookup
    booking = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.id == booking_id,
            ActivityBooking.user_id == traveler.id,
        )
        .first()
    )

    if not booking:
        raise EcoBookingServiceError(
            code="ECO_BOOKING_NOT_FOUND",
            message=f"Eco booking with id {booking_id} was not found for this traveler.",
            details={"booking_id": booking_id},
        )

    if not booking.eco_experience_id:
        raise EcoBookingServiceError(
            code="ECO_BOOKING_NOT_ECO",
            message="Target booking is not an Eco-Tourism reservation.",
            details={"booking_id": booking_id},
        )

    if booking.booking_status == "cancelled":
        raise EcoBookingServiceError(
            code="ECO_BOOKING_ALREADY_CANCELLED",
            message="This booking is already cancelled.",
            details={"booking_id": booking_id},
        )

    if booking.booking_status not in ("pending", "confirmed"):
        raise EcoBookingServiceError(
            code="ECO_BOOKING_CANNOT_BE_CANCELLED",
            message=f"Bookings with status '{booking.booking_status}' cannot be cancelled.",
            details={"booking_id": booking_id, "booking_status": booking.booking_status},
        )

    # 3. Read-only slot & experience lookup
    slot = (
        db.query(ActivitySlot)
        .filter(ActivitySlot.id == booking.activity_slot_id)
        .first()
    )
    if not slot:
        raise EcoBookingServiceError(
            code="ECO_SLOT_NOT_FOUND",
            message="Slot associated with this booking was not found.",
        )

    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == booking.eco_experience_id)
        .first()
    )
    free_hours = experience.free_cancellation_hours if experience else 24

    # 4. Cancellation cutoff evaluation
    current_time = now_utc if now_utc is not None else datetime.now(UTC)
    try:
        cancellation_result = evaluate_eco_cancellation_eligibility(
            slot_start=slot.starts_at,
            cancellation_requested_at=current_time,
            free_cancellation_hours=free_hours,
        )
    except Exception as err:
        code = getattr(err, "code", "ECO_INVALID_CANCELLATION")
        details = getattr(err, "details", {})
        raise EcoBookingServiceError(code=code, message=str(err), details=details) from err

    return EcoCancellationValidationPlan(
        booking_id=booking.id,
        traveler_id=traveler.id,
        slot_id=slot.id,
        eco_experience_id=booking.eco_experience_id,
        participants_to_release=booking.participants,
        cancellation=cancellation_result,
        reason=reason.strip(),
        is_valid=True,
    )


# ==============================================================================
# 4. Provider Booking Management & Decision Validation Services (Phase 4B)
# ==============================================================================


@dataclass(frozen=True)
class EcoProviderDecisionValidationPlan:
    """
    Structured outcome of a provider booking decision validation.
    Calculates capacity release requirements and status transitions in read-only mode.
    """

    booking_id: int
    provider_profile_id: int
    eco_experience_id: int
    action: str  # 'accept', 'reject', 'confirm_cash'
    from_booking_status: str
    to_booking_status: str
    from_payment_status: str
    to_payment_status: str
    slot_id: int
    participants_to_release: int
    note: str | None
    is_valid: bool = True


def validate_provider_booking_ownership(
    db: Session,
    booking: ActivityBooking,
    provider_profile: ProviderProfile,
) -> EcoExperience:
    """
    Verify that the authenticated provider owns the EcoExperience associated with this booking.

    Raises:
        EcoBookingServiceError:
            - ECO_EXPERIENCE_NOT_FOUND if the experience is missing or unlinked.
            - ECO_PROVIDER_NOT_AUTHORIZED if the experience is owned by another provider.
    """
    if booking.eco_experience_id is None:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_FOUND",
            message="Booking is not linked to any eco-experience.",
            details={"booking_id": booking.id},
        )

    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == booking.eco_experience_id)
        .first()
    )
    if experience is None:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_FOUND",
            message=f"EcoExperience with id {booking.eco_experience_id} was not found.",
            details={"eco_experience_id": booking.eco_experience_id},
        )

    if experience.provider_profile_id != provider_profile.id:
        raise EcoBookingServiceError(
            code="ECO_PROVIDER_NOT_AUTHORIZED",
            message="You do not have permission to manage bookings for this eco-experience.",
            details={
                "booking_id": booking.id,
                "experience_id": experience.id,
                "owner_provider_id": experience.provider_profile_id,
                "caller_provider_id": provider_profile.id,
            },
        )

    return experience


def validate_provider_decision_read_only(
    db: Session,
    provider_profile: ProviderProfile,
    booking_id: int,
    payload: EcoBookingProviderDecision,
    now_utc: datetime | None = None,
) -> EcoProviderDecisionValidationPlan:
    """
    Comprehensive read-only validation of a provider booking decision.

    Safety & Business Rules:
    1. 'accept':
       - Current booking_status must be 'pending'.
       - Associated slot must not have expired in the past (starts_at > now_utc).
       - Target booking_status = 'confirmed'.
       - Target payment_status remains unchanged ('unpaid').
       - Capacity to release = 0.
    2. 'reject':
       - Current booking_status must be 'pending'.
       - Mandatory rejection note of >= 5 non-whitespace characters.
       - Target booking_status = 'rejected' (valid under CK_activity_bookings_status).
       - Target payment_status must adhere strictly to CK_activity_bookings_payment:
         ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed').
         * For standard pending/unpaid bookings, payment_status remains 'unpaid'.
         * If payment was already collected ('paid'), payment_status transitions to 'refund_pending'.
         * Note: 'cancelled' is NEVER a valid payment_status in SQL Server (only a booking_status).
       - Capacity to release = booking.participants (calculated without performing release).
       - Future implementation safeguards:
         * Uses conditional update (WHERE booking_status = 'pending') to prevent double-release.
         * Atomic decrement with non-negative check (reserved_count >= :p) prevents negative capacity.
    3. 'confirm_cash':
       - Payment method must be 'pay_at_location'.
       - Booking must already be 'confirmed' (or 'completed').
       - Payment status must NOT be 'paid' already.
       - Target payment_status = 'paid'.
       - Target booking_status remains 'confirmed' (activity completion is NOT conflated with cash payment).
       - Capacity to release = 0.

    ZERO DATABASE WRITES EXECUTED.

    Raises:
        EcoBookingServiceError on validation failure.
    """
    booking = (
        db.query(ActivityBooking)
        .filter(ActivityBooking.id == booking_id)
        .first()
    )
    if booking is None:
        raise EcoBookingServiceError(
            code="ECO_BOOKING_NOT_FOUND",
            message=f"Booking with id {booking_id} was not found.",
            details={"booking_id": booking_id},
        )

    experience = validate_provider_booking_ownership(
        db=db,
        booking=booking,
        provider_profile=provider_profile,
    )

    slot = (
        db.query(ActivitySlot)
        .filter(ActivitySlot.id == booking.activity_slot_id)
        .first()
    )
    if slot is None:
        raise EcoBookingServiceError(
            code="ECO_SLOT_NOT_FOUND",
            message="Slot associated with this booking was not found.",
            details={"slot_id": booking.activity_slot_id},
        )

    current_time = now_utc if now_utc is not None else datetime.now(UTC)

    action = payload.action
    from_booking_status = booking.booking_status
    from_payment_status = booking.payment_status

    if action == "accept":
        if from_booking_status != "pending":
            raise EcoBookingServiceError(
                code="ECO_INVALID_STATUS_TRANSITION",
                message=f"Cannot accept booking with current status '{from_booking_status}'. Only 'pending' bookings can be accepted.",
                details={"current_status": from_booking_status, "action": action},
            )
        slot_starts_at = normalize_datetime_utc(slot.starts_at)
        if slot_starts_at <= current_time:
            raise EcoBookingServiceError(
                code="ECO_SLOT_EXPIRED",
                message="Cannot accept a booking for a slot that has already started or passed.",
                details={"slot_id": slot.id, "starts_at": slot_starts_at.isoformat()},
            )
        to_booking_status = "confirmed"
        to_payment_status = from_payment_status
        participants_to_release = 0

    elif action == "reject":
        if from_booking_status != "pending":
            raise EcoBookingServiceError(
                code="ECO_INVALID_STATUS_TRANSITION",
                message=f"Cannot reject booking with current status '{from_booking_status}'. Only 'pending' bookings can be rejected.",
                details={"current_status": from_booking_status, "action": action},
            )
        if not payload.note or len(payload.note.strip()) < 5:
            raise EcoBookingServiceError(
                code="ECO_REJECTION_NOTE_REQUIRED",
                message="A clear rejection note of at least 5 characters is mandatory when rejecting a booking.",
            )
        to_booking_status = "rejected"
        # Validate against SQL Server CK_activity_bookings_payment constraint:
        # Allowed values: ('unpaid', 'pending', 'paid', 'refund_pending', 'refunded', 'failed')
        # 'cancelled' is NOT a valid payment_status in SQL Server (it is only a valid booking_status).
        if from_payment_status == "paid":
            to_payment_status = "refund_pending"
        elif from_payment_status in ("unpaid", "failed"):
            to_payment_status = "unpaid"
        else:
            to_payment_status = from_payment_status

        assert to_payment_status != "cancelled", "payment_status cannot be 'cancelled' under CK_activity_bookings_payment"
        participants_to_release = booking.participants

    elif action == "confirm_cash":
        if booking.payment_method != "pay_at_location":
            raise EcoBookingServiceError(
                code="ECO_INVALID_PAYMENT_METHOD",
                message=f"Cannot confirm cash payment for booking with payment method '{booking.payment_method}'. Only 'pay_at_location' bookings accept cash confirmation.",
                details={"payment_method": booking.payment_method},
            )
        if from_booking_status not in ("confirmed", "completed"):
            raise EcoBookingServiceError(
                code="ECO_INVALID_STATUS_TRANSITION",
                message=f"Cannot confirm cash payment for booking with status '{from_booking_status}'. Booking must be confirmed first.",
                details={"booking_status": from_booking_status, "action": action},
            )
        if from_payment_status == "paid":
            raise EcoBookingServiceError(
                code="ECO_ALREADY_PAID",
                message="Cash payment has already been recorded as paid for this booking.",
                details={"payment_status": from_payment_status},
            )
        to_payment_status = "paid"
        to_booking_status = from_booking_status  # preserves 'confirmed', avoiding conflation
        participants_to_release = 0

    else:
        raise EcoBookingServiceError(
            code="ECO_UNSUPPORTED_ACTION",
            message=f"Unsupported provider action '{action}'.",
            details={"action": action},
        )

    return EcoProviderDecisionValidationPlan(
        booking_id=booking.id,
        provider_profile_id=provider_profile.id,
        eco_experience_id=experience.id,
        action=action,
        from_booking_status=from_booking_status,
        to_booking_status=to_booking_status,
        from_payment_status=from_payment_status,
        to_payment_status=to_payment_status,
        slot_id=slot.id,
        participants_to_release=participants_to_release,
        note=payload.note.strip() if payload.note else None,
        is_valid=True,
    )


# ==============================================================================
# 5. Atomic Transaction Execution & Lock Management (Phase 4C)
# ==============================================================================


def _handle_applock_result(res: int | None, slot_id: int) -> int:
    """
    Evaluate return code from SQL Server sp_getapplock:
      >= 0: Lock acquired successfully (0 = synchronous, 1 = after wait).
      -1: Lock request timed out.
      -2: Lock request canceled.
      -3: Deadlock victim.
      -999: Parameter validation or other failure.
    """
    if res is None:
        raise EcoBookingServiceError(
            code="ECO_LOCK_FAILED",
            message="Null response received from sp_getapplock.",
            details={"slot_id": slot_id},
        )
    if res in (0, 1):
        return res
    elif res == -1:
        raise EcoBookingServiceError(
            code="ECO_LOCK_TIMEOUT",
            message=f"Lock acquisition timed out on slot {slot_id} due to concurrent activity.",
            details={"slot_id": slot_id, "lock_code": res},
        )
    elif res == -2:
        raise EcoBookingServiceError(
            code="ECO_LOCK_CANCELED",
            message=f"Lock request on slot {slot_id} was canceled.",
            details={"slot_id": slot_id, "lock_code": res},
        )
    elif res == -3:
        raise EcoBookingServiceError(
            code="ECO_LOCK_DEADLOCK",
            message=f"Lock request on slot {slot_id} was chosen as a deadlock victim.",
            details={"slot_id": slot_id, "lock_code": res},
        )
    else:
        raise EcoBookingServiceError(
            code="ECO_LOCK_FAILED",
            message=f"Failed to acquire application lock on slot {slot_id} (error code {res}).",
            details={"slot_id": slot_id, "lock_code": res},
        )


def acquire_slot_lock(db: Session, slot_id: int, timeout_ms: int = 5000) -> int:
    """
    Acquire a transaction-level exclusive lock on an eco slot using SQL Server's sp_getapplock.
    In SQLite test environments, acts as an in-memory lock or simulated successful return code (0).
    """
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind else "sqlite"
    if dialect_name == "mssql":
        resource = f"eco_slot_{slot_id}"
        query = text(
            "DECLARE @res INT; "
            "EXEC @res = sp_getapplock "
            "  @Resource = :resource, "
            "  @LockMode = 'Exclusive', "
            "  @LockOwner = 'Transaction', "
            "  @LockTimeout = :timeout; "
            "SELECT @res AS result;"
        )
        res = db.execute(query, {"resource": resource, "timeout": timeout_ms}).scalar()
        return _handle_applock_result(res, slot_id)
    return 0


def release_slot_lock(db: Session, slot_id: int) -> int:
    """
    Release a transaction-level application lock on SQL Server using sp_releaseapplock.
    """
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind else "sqlite"
    if dialect_name == "mssql":
        resource = f"eco_slot_{slot_id}"
        query = text(
            "DECLARE @res INT; "
            "EXEC @res = sp_releaseapplock "
            "  @Resource = :resource, "
            "  @LockOwner = 'Transaction'; "
            "SELECT @res AS result;"
        )
        res = db.execute(query, {"resource": resource}).scalar()
        return res if res is not None else 0
    return 0


# ==============================================================================
# T16 Booking Lifecycle Notification Dispatchers
# ==============================================================================


def _get_provider_user_id(db: Session, provider_profile_id: int) -> int | None:
    """Helper to locate the provider User.id given a provider_profile_id."""
    prof = (
        db.query(ProviderProfile.user_id)
        .filter(ProviderProfile.id == provider_profile_id)
        .first()
    )
    return prof[0] if prof else None


def dispatch_booking_created_notifications(
    db: Session,
    booking_id: int,
    booking_ref_code: str,
    plan: EcoBookingValidationPlan,
) -> None:
    """
    T16: Safe notification dispatch for eco booking creation.
    - Notifies traveler of request receipt.
    - Notifies provider of incoming request.
    """
    # 1. Traveler confirmation notification
    create_notification(
        db=db,
        user_id=plan.traveler_id,
        title=f"Booking Request Submitted: {plan.experience_title}",
        message=(
            f"Your booking request for {plan.experience_title} ({booking_ref_code}) "
            f"for {plan.participants} participant(s) has been submitted and is awaiting provider confirmation."
        ),
        notification_type="eco_booking_created",
        link_url="/my-bookings",
    )

    # 2. Provider notification
    provider_user_id = _get_provider_user_id(db, plan.provider_profile_id)
    if provider_user_id:
        create_notification(
            db=db,
            user_id=provider_user_id,
            title=f"New Booking Request: {plan.experience_title}",
            message=(
                f"You have received a new booking request for {plan.experience_title} ({booking_ref_code}) "
                f"for {plan.participants} participant(s) on {plan.slot_starts_at.strftime('%Y-%m-%d %H:%M')}."
            ),
            notification_type="eco_booking_created",
            link_url="/provider/eco-bookings",
        )


def dispatch_booking_cancelled_notifications(
    db: Session,
    plan: EcoCancellationValidationPlan,
) -> None:
    """
    T16: Safe notification dispatch for traveler booking cancellation.
    - Notifies traveler of successful cancellation.
    - Notifies provider of cancelled booking and reason.
    """
    exp = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == plan.eco_experience_id)
        .first()
    )
    exp_title = exp.title if exp else "Eco Experience"

    # 1. Traveler notification
    create_notification(
        db=db,
        user_id=plan.traveler_id,
        title=f"Booking Cancelled: {exp_title}",
        message=f"Your booking for {exp_title} has been successfully cancelled.",
        notification_type="eco_booking_cancelled",
        link_url="/my-bookings",
    )

    # 2. Provider notification
    if exp and exp.provider_profile_id:
        provider_user_id = _get_provider_user_id(db, exp.provider_profile_id)
        if provider_user_id:
            reason_suffix = f" Reason: {plan.reason}." if plan.reason else ""
            create_notification(
                db=db,
                user_id=provider_user_id,
                title=f"Booking Cancelled: {exp_title}",
                message=f"A booking for {exp_title} was cancelled by the traveler.{reason_suffix}",
                notification_type="eco_booking_cancelled",
                link_url="/provider/eco-bookings",
            )


def dispatch_provider_decision_notifications(
    db: Session,
    plan: EcoProviderDecisionValidationPlan,
) -> None:
    """
    T16: Safe notification dispatch for provider decisions (accept, reject, confirm_cash).
    - accept: Notifies traveler that booking is confirmed.
    - reject: Notifies traveler that booking was declined with note.
    - confirm_cash: Notifies traveler that payment at location was confirmed.
    """
    exp = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == plan.eco_experience_id)
        .first()
    )
    exp_title = exp.title if exp else "Eco Experience"

    booking = (
        db.query(
            ActivityBooking.user_id,
            ActivityBooking.total_price,
            ActivityBooking.currency,
        )
        .filter(ActivityBooking.id == plan.booking_id)
        .first()
    )
    if not booking:
        return
    traveler_id, total_price, currency = booking[0], booking[1], booking[2] or "LKR"

    if plan.action == "accept":
        create_notification(
            db=db,
            user_id=traveler_id,
            title=f"Booking Confirmed: {exp_title}",
            message=f"Great news! Your booking for {exp_title} has been confirmed by the provider.",
            notification_type="eco_booking_accepted",
            link_url="/my-bookings",
        )
    elif plan.action == "reject":
        note_suffix = f" Reason: {plan.note}." if plan.note else ""
        create_notification(
            db=db,
            user_id=traveler_id,
            title=f"Booking Declined: {exp_title}",
            message=f"Your booking request for {exp_title} could not be confirmed by the provider.{note_suffix}",
            notification_type="eco_booking_rejected",
            link_url="/my-bookings",
        )
    elif plan.action == "confirm_cash":
        create_notification(
            db=db,
            user_id=traveler_id,
            title=f"Cash Payment Recorded: {exp_title}",
            message=f"Your cash payment of {currency} {total_price} for {exp_title} has been confirmed by the provider.",
            notification_type="eco_payment_confirmed",
            link_url="/my-bookings",
        )


def execute_traveler_booking_create_atomic(
    db: Session,
    plan: EcoBookingValidationPlan,
) -> dict[str, Any]:
    """
    Transaction-safe execution of an Eco-Tourism booking creation.
    Executes within an isolated transaction block:
    1. Acquires transaction-level lock on the scheduling slot.
    2. Atomically increments reserved_count with non-overselling check:
       UPDATE activity_slots SET reserved_count = reserved_count + :p
       WHERE id = :slot_id AND is_active = 1 AND (capacity - reserved_count) >= :p
       (Raises ECO_CAPACITY_EXCEEDED if rowcount == 0).
    3. Inserts ActivityBooking record.
    4. Inserts ActivityBookingParticipant records using safe explicit column mapping
       (avoiding the missing id_or_passport ORM column).
    5. Inserts BookingReference with module_type='activity' per SQL Server CK_booking_references_type.
    6. Automatically rolls back on any exception.
    """
    if not plan.is_valid:
        raise EcoBookingServiceError(
            code="ECO_PLAN_INVALID",
            message="Cannot execute an invalid booking validation plan.",
        )

    savepoint = db.begin_nested()
    try:
        # 1. Acquire transaction-level lock
        acquire_slot_lock(db, plan.slot_id)

        # 2. Atomic conditional capacity reservation
        slot_update = db.execute(
            text(
                "UPDATE activity_slots "
                "SET reserved_count = reserved_count + :participants "
                "WHERE id = :slot_id AND is_active = 1 AND (capacity - reserved_count) >= :participants"
            ),
            {"participants": plan.participants, "slot_id": plan.slot_id},
        )
        if slot_update.rowcount != 1:
            raise EcoBookingServiceError(
                code="ECO_CAPACITY_EXCEEDED",
                message="Slot capacity is insufficient or slot is no longer active.",
                details={"slot_id": plan.slot_id, "requested_participants": plan.participants},
            )

        # 3. Insert ActivityBooking
        booking = ActivityBooking(
            activity_slot_id=plan.slot_id,
            eco_experience_id=plan.eco_experience_id,
            user_id=plan.traveler_id,
            participants=plan.participants,
            total_price=plan.pricing.total_price,
            currency="LKR",
            payment_method=plan.payment_method,
            payment_status=plan.payment_status,
            booking_status=plan.booking_status,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(booking)
        db.flush()

        # 4. Insert Participants using safe explicit Core column mapping (Issue 1 safe)
        if plan.participant_roster:
            insp = inspect(db.connection())
            table_cols = {c["name"] for c in insp.get_columns("activity_booking_participants")}

            cols = [
                column("activity_booking_id", Integer),
                column("full_name", Unicode),
            ]
            if "age" in table_cols:
                cols.append(column("age", Integer))
            if "emergency_contact_name" in table_cols:
                cols.append(column("emergency_contact_name", Unicode))
            if "emergency_contact_phone" in table_cols:
                cols.append(column("emergency_contact_phone", Unicode))
            if "special_requirements" in table_cols:
                cols.append(column("special_requirements", Unicode))
            if "waiver_signed" in table_cols:
                cols.append(column("waiver_signed", Boolean))

            part_table = table("activity_booking_participants", *cols)
            part_rows = []
            for p in plan.participant_roster:
                row = {
                    "activity_booking_id": booking.id,
                    "full_name": p["full_name"],
                }
                if "age" in table_cols:
                    row["age"] = p.get("age")
                if "emergency_contact_name" in table_cols:
                    row["emergency_contact_name"] = p.get("emergency_contact_name")
                if "emergency_contact_phone" in table_cols:
                    row["emergency_contact_phone"] = p.get("emergency_contact_phone")
                if "special_requirements" in table_cols:
                    row["special_requirements"] = p.get("special_requirements")
                if "waiver_signed" in table_cols:
                    row["waiver_signed"] = bool(p.get("waiver_signed", False))
                part_rows.append(row)

            db.execute(part_table.insert(), part_rows)

        # 5. Insert BookingReference with canonical module_type='activity'
        ref_code = format_eco_booking_reference(booking.id, booking.created_at)
        booking_ref = BookingReference(
            booking_reference=ref_code,
            module_type="activity",  # SQL Server constraint CK_booking_references_type
            target_booking_id=booking.id,
            user_id=plan.traveler_id,
            provider_profile_id=plan.provider_profile_id,
            total_price=plan.pricing.total_price,
            currency="LKR",
            booking_status=plan.booking_status,
            payment_status=plan.payment_status,
            created_at=datetime.now(UTC),
        )
        db.add(booking_ref)
        db.flush()

        # 6. Dispatch T16 booking creation notifications safely
        try:
            dispatch_booking_created_notifications(
                db=db,
                booking_id=booking.id,
                booking_ref_code=ref_code,
                plan=plan,
            )
        except Exception as notif_err:
            logger.warning("Failed to dispatch booking created notification: %s", notif_err)

        savepoint.commit()
        return {
            "booking_id": booking.id,
            "booking_reference": ref_code,
            "status": booking.booking_status,
            "payment_status": booking.payment_status,
            "participants": booking.participants,
            "total_price": str(booking.total_price),
        }
    except Exception:
        savepoint.rollback()
        raise


def execute_traveler_booking_cancel_atomic(
    db: Session,
    plan: EcoCancellationValidationPlan,
) -> dict[str, Any]:
    """
    Transaction-safe execution of an Eco-Tourism booking cancellation.
    1. Acquires transaction-level lock on the slot.
    2. Atomically transitions booking_status to 'cancelled':
       UPDATE activity_bookings
       SET booking_status = 'cancelled', cancellation_reason = :reason, cancelled_at = :now
       WHERE id = :booking_id AND booking_status IN ('pending', 'confirmed')
       (Raises ECO_BOOKING_ALREADY_CANCELLED if rowcount == 0 to prevent double cancellation).
    3. Atomically decrements slot reserved_count:
       UPDATE activity_slots
       SET reserved_count = reserved_count - :p
       WHERE id = :slot_id AND reserved_count >= :p
       (Raises ECO_CAPACITY_UNDERFLOW if rowcount == 0 to prevent negative capacity).
    4. Updates BookingReference booking_status = 'cancelled'.
    5. Automatically rolls back on any exception.
    """
    if not plan.is_valid:
        raise EcoBookingServiceError(
            code="ECO_PLAN_INVALID",
            message="Cannot execute an invalid cancellation validation plan.",
        )

    savepoint = db.begin_nested()
    try:
        acquire_slot_lock(db, plan.slot_id)
        now_dt = datetime.now(UTC)

        # 1. Atomic booking update preventing double-cancellation
        booking_update = db.execute(
            text(
                "UPDATE activity_bookings "
                "SET booking_status = 'cancelled', cancellation_reason = :reason, cancelled_at = :now "
                "WHERE id = :booking_id AND booking_status IN ('pending', 'confirmed')"
            ),
            {"booking_id": plan.booking_id, "reason": plan.reason, "now": now_dt},
        )
        if booking_update.rowcount != 1:
            raise EcoBookingServiceError(
                code="ECO_BOOKING_ALREADY_CANCELLED",
                message=f"Booking {plan.booking_id} is already cancelled or in an un-cancellable state.",
                details={"booking_id": plan.booking_id},
            )

        # 2. Atomic capacity release preventing negative capacity
        slot_update = db.execute(
            text(
                "UPDATE activity_slots "
                "SET reserved_count = reserved_count - :participants "
                "WHERE id = :slot_id AND reserved_count >= :participants"
            ),
            {"slot_id": plan.slot_id, "participants": plan.participants_to_release},
        )
        if slot_update.rowcount != 1:
            raise EcoBookingServiceError(
                code="ECO_CAPACITY_UNDERFLOW",
                message=f"Cannot release {plan.participants_to_release} places: slot reserved count would become negative.",
                details={"slot_id": plan.slot_id, "participants": plan.participants_to_release},
            )

        # 3. Sync BookingReference status
        db.execute(
            text(
                "UPDATE booking_references "
                "SET booking_status = 'cancelled' "
                "WHERE target_booking_id = :booking_id AND module_type IN ('activity', 'eco_tourism')"
            ),
            {"booking_id": plan.booking_id},
        )

        # 4. Dispatch T16 booking cancellation notifications safely
        try:
            dispatch_booking_cancelled_notifications(
                db=db,
                plan=plan,
            )
        except Exception as notif_err:
            logger.warning("Failed to dispatch booking cancelled notification: %s", notif_err)

        savepoint.commit()
        return {
            "booking_id": plan.booking_id,
            "status": "cancelled",
            "released_capacity": plan.participants_to_release,
            "cancelled_at": now_dt.isoformat(),
        }
    except Exception:
        savepoint.rollback()
        raise


def execute_provider_decision_atomic(
    db: Session,
    plan: EcoProviderDecisionValidationPlan,
) -> dict[str, Any]:
    """
    Transaction-safe execution of an Eco-Tourism provider decision (accept, reject, confirm_cash).
    - accept: Atomic update pending -> confirmed.
    - reject: Atomic update pending -> rejected with note; atomic capacity release; payment_status -> unpaid (or refund_pending).
    - confirm_cash: Atomic update payment_status -> paid where method=pay_at_location and status=confirmed.
    - Syncs booking_references table.
    - Automatically rolls back on any exception.
    """
    if not plan.is_valid:
        raise EcoBookingServiceError(
            code="ECO_PLAN_INVALID",
            message="Cannot execute an invalid provider decision validation plan.",
        )

    savepoint = db.begin_nested()
    try:
        acquire_slot_lock(db, plan.slot_id)
        now_dt = datetime.now(UTC)

        if plan.action == "accept":
            # Atomic update pending -> confirmed
            res = db.execute(
                text(
                    "UPDATE activity_bookings "
                    "SET booking_status = 'confirmed', updated_at = :now "
                    "WHERE id = :booking_id AND booking_status = 'pending'"
                ),
                {"booking_id": plan.booking_id, "now": now_dt},
            )
            if res.rowcount != 1:
                raise EcoBookingServiceError(
                    code="ECO_DECISION_CONFLICT",
                    message="Booking is no longer in 'pending' status; cannot accept.",
                    details={"booking_id": plan.booking_id},
                )
            # Sync BookingReference
            db.execute(
                text(
                    "UPDATE booking_references "
                    "SET booking_status = 'confirmed' "
                    "WHERE target_booking_id = :booking_id AND module_type IN ('activity', 'eco_tourism')"
                ),
                {"booking_id": plan.booking_id},
            )

        elif plan.action == "reject":
            # Atomic update pending -> rejected (prevents double rejection)
            res = db.execute(
                text(
                    "UPDATE activity_bookings "
                    "SET booking_status = 'rejected', cancellation_reason = :note, cancelled_at = :now, "
                    "    payment_status = :to_payment_status, updated_at = :now "
                    "WHERE id = :booking_id AND booking_status = 'pending'"
                ),
                {
                    "booking_id": plan.booking_id,
                    "note": plan.note,
                    "now": now_dt,
                    "to_payment_status": plan.to_payment_status,
                },
            )
            if res.rowcount != 1:
                raise EcoBookingServiceError(
                    code="ECO_DECISION_CONFLICT",
                    message="Booking is no longer in 'pending' status; cannot reject.",
                    details={"booking_id": plan.booking_id},
                )

            # Atomic capacity release preventing negative capacity
            if plan.participants_to_release > 0:
                slot_res = db.execute(
                    text(
                        "UPDATE activity_slots "
                        "SET reserved_count = reserved_count - :participants "
                        "WHERE id = :slot_id AND reserved_count >= :participants"
                    ),
                    {"slot_id": plan.slot_id, "participants": plan.participants_to_release},
                )
                if slot_res.rowcount != 1:
                    raise EcoBookingServiceError(
                        code="ECO_CAPACITY_UNDERFLOW",
                        message=f"Cannot release {plan.participants_to_release} places: slot reserved count would become negative.",
                        details={"slot_id": plan.slot_id, "participants": plan.participants_to_release},
                    )

            # Sync BookingReference
            db.execute(
                text(
                    "UPDATE booking_references "
                    "SET booking_status = 'rejected', payment_status = :to_payment_status "
                    "WHERE target_booking_id = :booking_id AND module_type IN ('activity', 'eco_tourism')"
                ),
                {"booking_id": plan.booking_id, "to_payment_status": plan.to_payment_status},
            )

        elif plan.action == "confirm_cash":
            # Atomic payment_status unpaid -> paid (does NOT change booking_status)
            res = db.execute(
                text(
                    "UPDATE activity_bookings "
                    "SET payment_status = 'paid', updated_at = :now "
                    "WHERE id = :booking_id AND payment_status = 'unpaid' AND booking_status IN ('confirmed', 'completed')"
                ),
                {"booking_id": plan.booking_id, "now": now_dt},
            )
            if res.rowcount != 1:
                raise EcoBookingServiceError(
                    code="ECO_PAYMENT_CONFLICT",
                    message="Cash payment could not be confirmed: booking is not unpaid or not confirmed.",
                    details={"booking_id": plan.booking_id},
                )
            # Sync BookingReference
            db.execute(
                text(
                    "UPDATE booking_references "
                    "SET payment_status = 'paid' "
                    "WHERE target_booking_id = :booking_id AND module_type IN ('activity', 'eco_tourism')"
                ),
                {"booking_id": plan.booking_id},
            )

        # Dispatch T16 provider decision notifications safely
        try:
            dispatch_provider_decision_notifications(
                db=db,
                plan=plan,
            )
        except Exception as notif_err:
            logger.warning("Failed to dispatch provider decision notification: %s", notif_err)

        savepoint.commit()
        return {
            "booking_id": plan.booking_id,
            "action": plan.action,
            "booking_status": plan.to_booking_status,
            "payment_status": plan.to_payment_status,
            "released_capacity": plan.participants_to_release,
        }
    except Exception:
        savepoint.rollback()
        raise


# ==============================================================================
# 5. Weather Emergency & Hazard Cancellation Service (E06)
# ==============================================================================


@dataclass(frozen=True)
class EcoWeatherEmergencyPlan:
    """
    Structured execution plan for declaring weather emergencies and cancelling affected slots/bookings (E06).
    """

    eco_experience_id: int
    provider_profile_id: int
    provider_user_id: int | None
    experience_title: str
    hazard_type: str
    reason: str
    affected_slots: list[tuple[int, int, datetime, datetime]]  # (slot_id, reserved_count, starts_at, ends_at)
    start_date: date
    end_date: date
    is_valid: bool = True


def validate_weather_emergency_cancellation_read_only(
    db: Session,
    provider_profile: Any,
    eco_experience_id: int,
    reason: str,
    hazard_type: str = "weather_alert",
    slot_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> EcoWeatherEmergencyPlan:
    """
    Validates provider authorization, experience ownership, and target slot/date eligibility
    for emergency weather cancellation (E06).
    """
    # 1. Authorization checks
    p_type = getattr(provider_profile, "provider_type", None)
    if p_type != "eco_tourism":
        raise EcoBookingServiceError(
            code="ECO_PROVIDER_NOT_AUTHORIZED",
            message=f"Only 'eco_tourism' providers can manage emergency cancellations (got: '{p_type}').",
            details={"provider_type": p_type},
        )
    p_approval = getattr(provider_profile, "approval_status", None)
    if p_approval != "approved":
        raise EcoBookingServiceError(
            code="ECO_PROVIDER_NOT_APPROVED",
            message=f"Provider profile must be 'approved' to manage emergency cancellations (got: '{p_approval}').",
            details={"approval_status": p_approval},
        )

    # 2. Experience ownership
    experience = (
        db.query(EcoExperience)
        .filter(EcoExperience.id == eco_experience_id)
        .first()
    )
    if not experience:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_FOUND",
            message=f"EcoExperience with id {eco_experience_id} not found.",
            details={"eco_experience_id": eco_experience_id},
        )
    if experience.provider_profile_id != provider_profile.id:
        raise EcoBookingServiceError(
            code="ECO_EXPERIENCE_NOT_OWNED",
            message="Authenticated provider does not own this eco experience.",
            details={
                "eco_experience_id": eco_experience_id,
                "provider_profile_id": provider_profile.id,
            },
        )

    # 3. Reason & Hazard validation
    hazard_cleaned = hazard_type.strip().lower()
    if hazard_cleaned not in ("weather_alert", "monsoon_season", "wildlife_breeding", "park_closure"):
        raise EcoBookingServiceError(
            code="ECO_INVALID_HAZARD_TYPE",
            message=f"Invalid hazard_type '{hazard_type}'. Must be weather_alert, monsoon_season, wildlife_breeding, or park_closure.",
            details={"hazard_type": hazard_type},
        )
    if not reason or len(reason.strip()) < 5:
        raise EcoBookingServiceError(
            code="ECO_INVALID_REASON",
            message="Emergency cancellation reason must be at least 5 characters.",
            details={"reason": reason},
        )

    # 4. Target Slot(s) and Dates resolution
    affected_slots: list[tuple[int, int, datetime, datetime]] = []
    eff_start_date: date
    eff_end_date: date

    if slot_id is not None:
        slot = (
            db.query(ActivitySlot)
            .filter(
                ActivitySlot.id == slot_id,
                ActivitySlot.eco_experience_id == eco_experience_id,
            )
            .first()
        )
        if not slot:
            raise EcoBookingServiceError(
                code="ECO_SLOT_NOT_FOUND",
                message=f"Slot with id {slot_id} not found for eco experience {eco_experience_id}.",
                details={"slot_id": slot_id, "eco_experience_id": eco_experience_id},
            )
        start_utc = normalize_datetime_utc(slot.starts_at)
        end_utc = normalize_datetime_utc(slot.ends_at)
        eff_start_date = start_utc.date()
        eff_end_date = end_utc.date()
        affected_slots.append((slot.id, slot.reserved_count, slot.starts_at, slot.ends_at))
    else:
        if not start_date or not end_date:
            raise EcoBookingServiceError(
                code="ECO_MISSING_TARGET_WINDOW",
                message="Either slot_id or both start_date and end_date must be provided.",
            )
        if end_date < start_date:
            raise EcoBookingServiceError(
                code="ECO_INVALID_DATE_RANGE",
                message="end_date must be greater than or equal to start_date.",
                details={"start_date": str(start_date), "end_date": str(end_date)},
            )
        eff_start_date = start_date
        eff_end_date = end_date

        start_dt = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=UTC)
        end_dt = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, tzinfo=UTC)
        slots = (
            db.query(ActivitySlot)
            .filter(
                ActivitySlot.eco_experience_id == eco_experience_id,
                ActivitySlot.is_active.is_(True),
                ActivitySlot.starts_at <= end_dt,
                ActivitySlot.ends_at >= start_dt,
            )
            .order_by(ActivitySlot.starts_at.asc())
            .all()
        )
        for s in slots:
            affected_slots.append((s.id, s.reserved_count, s.starts_at, s.ends_at))

    provider_user_id = getattr(provider_profile, "user_id", None)
    if provider_user_id is None:
        provider_user_id = _get_provider_user_id(db, provider_profile.id)

    return EcoWeatherEmergencyPlan(
        eco_experience_id=eco_experience_id,
        provider_profile_id=provider_profile.id,
        provider_user_id=provider_user_id,
        experience_title=experience.title,
        hazard_type=hazard_cleaned,
        reason=reason.strip(),
        affected_slots=affected_slots,
        start_date=eff_start_date,
        end_date=eff_end_date,
        is_valid=True,
    )


def execute_weather_emergency_cancellation_atomic(
    db: Session,
    plan: EcoWeatherEmergencyPlan,
) -> dict[str, Any]:
    """
    Transaction-safe execution of weather emergency cancellation (E06).
    1. Acquires locks on all affected slots.
    2. Deactivates affected slots (is_active = False).
    3. Identifies and cancels all pending and confirmed bookings for these slots.
    4. Releases capacity safely for each booking.
    5. Syncs booking_references.
    6. Identifies paid transactions and initiates 100% full refund records in payment_refunds (idempotent).
    7. Creates active EcoSeasonRestriction to block future reservations for this hazard window.
    8. Dispatches in-app notifications to all affected travelers and the provider.
    9. Fully rolls back on any error.
    """
    if not plan.is_valid:
        raise EcoBookingServiceError(
            code="ECO_PLAN_INVALID",
            message="Cannot execute an invalid weather emergency cancellation plan.",
        )

    savepoint = db.begin_nested()
    try:
        now_utc = datetime.now(UTC)
        cancelled_booking_ids: list[int] = []
        released_capacity_total = 0
        refunds_initiated_count = 0
        total_refund_amount = Decimal("0.00")
        processed_tx_ids: set[int] = set()

        for slot_id, reserved_count, starts_at, ends_at in plan.affected_slots:
            acquire_slot_lock(db, slot_id)

            # Deactivate slot to prevent new reservations
            db.execute(
                text("UPDATE activity_slots SET is_active = 0 WHERE id = :slot_id"),
                {"slot_id": slot_id},
            )

            # Query all active bookings for this slot
            bookings = (
                db.query(ActivityBooking)
                .filter(
                    ActivityBooking.activity_slot_id == slot_id,
                    ActivityBooking.booking_status.in_(["pending", "confirmed"]),
                )
                .all()
            )

            for b in bookings:
                p_count = b.participants
                new_payment_status = (
                    "refund_pending" if b.payment_status == "paid" else b.payment_status
                )

                # Atomically update booking status
                b_update = db.execute(
                    text(
                        "UPDATE activity_bookings "
                        "SET booking_status = 'cancelled', "
                        "    cancellation_reason = :reason, "
                        "    cancelled_at = :now, "
                        "    payment_status = :payment_status "
                        "WHERE id = :booking_id AND booking_status IN ('pending', 'confirmed')"
                    ),
                    {
                        "booking_id": b.id,
                        "reason": f"Weather Emergency ({plan.hazard_type}): {plan.reason}",
                        "now": now_utc,
                        "payment_status": new_payment_status,
                    },
                )
                if b_update.rowcount != 1:
                    continue  # Already cancelled concurrently

                cancelled_booking_ids.append(b.id)

                # Release capacity safely
                slot_upd = db.execute(
                    text(
                        "UPDATE activity_slots "
                        "SET reserved_count = reserved_count - :p "
                        "WHERE id = :slot_id AND reserved_count >= :p"
                    ),
                    {"slot_id": slot_id, "p": p_count},
                )
                if slot_upd.rowcount == 1:
                    released_capacity_total += p_count

                # Sync booking_references
                db.execute(
                    text(
                        "UPDATE booking_references "
                        "SET booking_status = 'cancelled', "
                        "    payment_status = :payment_status "
                        "WHERE target_booking_id = :booking_id AND module_type IN ('activity', 'eco_tourism')"
                    ),
                    {"booking_id": b.id, "payment_status": new_payment_status},
                )

                # Refund tracking using approved payment infrastructure
                ref = (
                    db.query(BookingReference)
                    .filter(
                        BookingReference.target_booking_id == b.id,
                        BookingReference.module_type.in_(["activity", "eco_tourism"]),
                    )
                    .first()
                )
                booking_ref_code = ref.booking_reference if ref else f"ECO-BK-{b.id}"

                if ref and (b.payment_status == "paid" or b.payment_method == "online"):
                    tx = (
                        db.query(PaymentTransaction)
                        .filter(
                            PaymentTransaction.booking_reference_id == ref.id,
                            PaymentTransaction.payment_status.in_(["paid", "success", "completed"]),
                        )
                        .order_by(PaymentTransaction.id.desc())
                        .first()
                    )
                    if tx and tx.id not in processed_tx_ids and tx.amount > Decimal("0.00"):
                        # Calculate existing refunds to prevent duplicate refund records
                        existing_sum = (
                            db.query(func.coalesce(func.sum(PaymentRefund.amount), Decimal("0.00")))
                            .filter(
                                PaymentRefund.payment_transaction_id == tx.id,
                                PaymentRefund.refund_status.in_(["pending", "approved", "completed", "processed"]),
                            )
                            .scalar()
                        ) or Decimal("0.00")

                        refundable = tx.amount - existing_sum
                        if refundable > Decimal("0.00"):
                            refund_record = PaymentRefund(
                                payment_transaction_id=tx.id,
                                amount=refundable,
                                currency=tx.currency,
                                refund_reason=f"Weather Emergency ({plan.hazard_type}): {plan.reason}",
                                refund_status="pending",
                            )
                            db.add(refund_record)
                            processed_tx_ids.add(tx.id)
                            refunds_initiated_count += 1
                            total_refund_amount += refundable

                # In-app notification to traveler
                try:
                    create_notification(
                        db=db,
                        user_id=b.user_id,
                        title="Weather Emergency: Booking Cancelled",
                        message=(
                            f"Your booking ({booking_ref_code}) for '{plan.experience_title}' "
                            f"has been cancelled due to weather/environmental hazard: {plan.reason}. "
                            f"Eligible payments are queued for 100% full refund."
                        ),
                        notification_type="eco_weather_cancellation",
                        link_url="/my-bookings",
                    )
                except Exception as notif_err:
                    logger.warning("Failed to dispatch weather cancellation traveler notification: %s", notif_err)

        # Record active EcoSeasonRestriction to block future reservations
        existing_restriction = (
            db.query(EcoSeasonRestriction)
            .filter(
                EcoSeasonRestriction.eco_experience_id == plan.eco_experience_id,
                EcoSeasonRestriction.start_date == plan.start_date,
                EcoSeasonRestriction.end_date == plan.end_date,
                EcoSeasonRestriction.restriction_type == plan.hazard_type,
                EcoSeasonRestriction.is_active.is_(True),
            )
            .first()
        )
        if not existing_restriction:
            new_restriction = EcoSeasonRestriction(
                eco_experience_id=plan.eco_experience_id,
                start_date=plan.start_date,
                end_date=plan.end_date,
                restriction_type=plan.hazard_type,
                reason=plan.reason,
                is_active=True,
                created_by=plan.provider_user_id,
            )
            db.add(new_restriction)
            db.flush()
            restriction_id = new_restriction.id
        else:
            restriction_id = existing_restriction.id

        # In-app notification to provider
        if plan.provider_user_id:
            try:
                create_notification(
                    db=db,
                    user_id=plan.provider_user_id,
                    title="Weather Emergency Restrictions Applied",
                    message=(
                        f"Weather emergency restrictions applied to '{plan.experience_title}' "
                        f"({plan.start_date} to {plan.end_date}). "
                        f"{len(plan.affected_slots)} slots deactivated, {len(cancelled_booking_ids)} bookings cancelled."
                    ),
                    notification_type="eco_weather_cancellation",
                    link_url="/provider/eco-bookings",
                )
            except Exception as notif_err:
                logger.warning("Failed to dispatch weather cancellation provider notification: %s", notif_err)

        savepoint.commit()

        return {
            "eco_experience_id": plan.eco_experience_id,
            "hazard_type": plan.hazard_type,
            "reason": plan.reason,
            "restriction_id": restriction_id,
            "affected_slots_count": len(plan.affected_slots),
            "cancelled_bookings_count": len(cancelled_booking_ids),
            "released_capacity_total": released_capacity_total,
            "refunds_initiated_count": refunds_initiated_count,
            "total_refund_amount": str(total_refund_amount),
            "cancelled_booking_ids": cancelled_booking_ids,
            "status": "success",
            "message": (
                f"Weather emergency declared successfully. {len(plan.affected_slots)} slot(s) deactivated, "
                f"{len(cancelled_booking_ids)} booking(s) cancelled, {refunds_initiated_count} refund(s) initiated."
            ),
        }
    except Exception:
        savepoint.rollback()
        raise


