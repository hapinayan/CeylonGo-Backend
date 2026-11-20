"""
Module 04 — Staged Traveler Eco-Tourism Booking Router.

Endpoints Specification:
1. POST /api/v1/eco-bookings
   - Traveler booking creation endpoint.
   - Enforces authenticated traveler authorization (role == 'traveler').
   - Validates slot existence, future start time, active status, and eco-experience match.
   - Validates participant bounds and minimum age requirements.
   - Computes server-side pricing with Decimal precision.
   - Restricts payment method strictly to 'pay_at_location' in this phase.
   - STAGED ACTIVATION GUARD: Disabled with HTTP 503 pending resolution of:
     * Issue 1: ActivityBookingParticipant ORM mismatch (id_or_passport alignment).
     * Issue 2: Migration 030 approval (activity_slots.activity_id nullability).
   - Zero database writes executed.

2. GET /api/v1/eco-bookings/my-bookings
   - Traveler booking history endpoint.
   - Safely queries traveler's eco bookings (eco_experience_id IS NOT NULL).
   - Formats canonical booking references (ECO-YYYYMMDD-XXXX).
   - CRITICAL SAFETY: Populates participants_list as empty list to prevent triggering
     the Issue 1 ORM column mismatch crash on SQL Server.

3. POST /api/v1/eco-bookings/{booking_id}/cancel
   - Traveler booking cancellation endpoint.
   - Validates traveler ownership, booking status, and cancellation cutoff.
   - STAGED ACTIVATION GUARD: Disabled with HTTP 503 pending booking activation.
   - Zero database writes executed.

STRICT ISOLATION:
- Unregistered in app/main.py.
- Zero database writes executed.
- Zero raw SQL workarounds.
"""

import logging
from datetime import UTC, date, datetime, time
from typing import Annotated, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Boolean, DateTime, Integer, Unicode, case, column, inspect, literal, select, table
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_eco_provider
from app.database.connection import get_db
from app.models.activity_booking import ActivityBooking
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience
from app.models.provider_profile import ProviderProfile
from app.models.shared import BookingReference
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.eco_booking import (
    EcoBookingCancellation,
    EcoBookingCreate,
    EcoBookingProviderDecision,
    EcoBookingProviderListItem,
    EcoBookingProviderListResponse,
    EcoBookingResponse,
    EcoParticipantResponse,
)
from app.services.eco_booking_service import (
    EcoBookingServiceError,
    EcoBookingValidationPlan,
    EcoCancellationValidationPlan,
    EcoProviderDecisionValidationPlan,
    execute_provider_decision_atomic,
    execute_traveler_booking_cancel_atomic,
    execute_traveler_booking_create_atomic,
    format_eco_booking_reference,
    validate_booking_cancellation_read_only,
    validate_booking_request_read_only,
    validate_provider_booking_ownership,
    validate_provider_decision_read_only,
    validate_traveler_authorization,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Eco-Tourism Bookings (Staged)"])

# Physical table mapping for dbo.activity_booking_participants
# Explicit column selection avoids querying the nonexistent id_or_passport column (Issue 1)
# and retrieves actual values for emergency_contact_name, emergency_contact_phone, and waiver_signed.
_participants_table = table(
    "activity_booking_participants",
    column("id", Integer),
    column("activity_booking_id", Integer),
    column("full_name", Unicode),
    column("age", Integer),
    column("emergency_contact_name", Unicode),
    column("emergency_contact_phone", Unicode),
    column("special_requirements", Unicode),
    column("waiver_signed", Boolean),
    column("created_at", DateTime),
)


# ==============================================================================
# 1. Traveler Booking Creation (Staged & Activation-Guarded)
# ==============================================================================


@router.post(
    "/api/v1/eco-bookings",
    response_model=EcoBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an Eco-Tourism reservation (Staged)",
)
def create_eco_booking(
    payload: EcoBookingCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Traveler-only endpoint: Books an upcoming slot for an approved Eco-Tourism experience.

    Validation Pipeline:
    1. Authenticated caller must have role 'traveler' and be active.
    2. Payment method must be 'pay_at_location' (online payments rejected in Phase 4).
    3. Target slot must exist, be active, start in the future, and belong to an approved EcoExperience.
    4. Participants count must satisfy min_participants <= N <= max_group_size and available capacity.
    5. Participant ages must satisfy listing's min_age if roster provided.
    6. Pricing calculated server-side with Decimal precision.
    7. Initial status lifecycle: booking_status='pending', payment_status='unpaid'.

    STAGED ACTIVATION GUARD:
    - Performs full read-only validation.
    - Actual database insertion is guarded and returns HTTP 503 until:
      * Issue 1: ActivityBookingParticipant ORM column alignment is resolved.
      * Issue 2: Migration 030 is approved by Database Coordinator.
    - Zero database writes are executed.
    """
    try:
        validation_plan = validate_booking_request_read_only(
            db=db,
            traveler=current_user,
            payload=payload,
        )
    except EcoBookingServiceError as err:
        if err.code in ("ECO_USER_NOT_TRAVELER", "ECO_USER_INACTIVE"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err.message,
            )
        elif err.code in ("ECO_SLOT_NOT_FOUND", "ECO_EXPERIENCE_NOT_FOUND"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err.message,
            )
        elif err.code in ("ECO_ONLINE_PAYMENT_UNSUPPORTED",):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err.message,
            )
        elif err.code in (
            "ECO_SLOT_NOT_ACTIVE",
            "ECO_SLOT_IN_PAST",
            "ECO_EXPERIENCE_NOT_ACTIVE",
            "ECO_SEASON_RESTRICTED",
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err.message,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err.message,
            )

    # =========================================================================
    # STAGED ACTIVATION GUARD (ISSUES 1 & 2 PREREQUISITES)
    # =========================================================================
    # Booking persistence is staged and disabled pending:
    # 1. Issue 1: ActivityBookingParticipant ORM alignment (id_or_passport mismatch).
    # 2. Issue 2: Migration 030 approval (activity_slots.activity_id NULL for eco).
    # Zero database writes executed.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Eco booking validation succeeded for experience '{validation_plan.experience_title}' "
            f"(Slot: {validation_plan.slot_id}, Participants: {validation_plan.participants}, "
            f"Total: {validation_plan.pricing.total_price} {validation_plan.pricing.currency}), "
            f"but booking creation is staged and disabled pending resolution of: "
            f"[Issue 1: ActivityBookingParticipant ORM alignment; Issue 2: Migration 030 approval]. "
            f"Zero database writes were executed."
        ),
    )

    # PREPARED FOR ACTIVATION (Behind HTTP 503 guard):
    return _execute_booking_create_with_transaction(db, validation_plan)


# ==============================================================================
# Shared Sourcing Helpers (Persisted Booking References & Physical Participants)
# ==============================================================================


CANONICAL_ECO_MODULE_TYPES = ("eco_tourism", "activity")


def _fetch_persisted_booking_references(
    db: Session,
    booking_ids: Sequence[int],
    user_id: int | None = None,
) -> dict[int, str]:
    """
    Deterministic persisted booking reference lookup from dbo.booking_references.
    Matches canonical module_type in ('eco_tourism', 'activity') and target_booking_id:
      - 'activity' aligns with SQL Server CHECK constraint CK_booking_references_type
        ('homestay', 'tour_guide', 'activity', 'rental_vehicle', 'artisan_reservation').
      - 'eco_tourism' supports Module 04-specific tagged reference records.
    Strictly isolates against other module types ('homestay', 'tour_guide', 'rental_vehicle', 'artisan_reservation').
    Optionally matches user_id for traveler queries.
    Orders by id DESC so the latest canonical record is selected deterministically if duplicates exist,
    prioritizing 'eco_tourism' if both 'eco_tourism' and 'activity' exist for the same target_booking_id.
    """
    if not booking_ids:
        return {}

    query = db.query(BookingReference).filter(
        BookingReference.module_type.in_(CANONICAL_ECO_MODULE_TYPES),
        BookingReference.target_booking_id.in_(booking_ids),
    )
    if user_id is not None:
        query = query.filter(BookingReference.user_id == user_id)

    # Order deterministically:
    # 1. Prefer specific 'eco_tourism' references over generic 'activity' references
    # 2. Prefer newest reference by id DESC
    priority_order = case(
        (BookingReference.module_type == "eco_tourism", 1),
        (BookingReference.module_type == "activity", 2),
        else_=3,
    )
    ref_records = query.order_by(priority_order, BookingReference.id.desc()).all()
    persisted_refs_by_booking_id: dict[int, str] = {}
    for ref in ref_records:
        if ref.target_booking_id not in persisted_refs_by_booking_id:
            persisted_refs_by_booking_id[ref.target_booking_id] = ref.booking_reference

    return persisted_refs_by_booking_id


def _fetch_participants_by_booking_ids(
    db: Session,
    booking_ids: Sequence[int],
) -> dict[int, list[EcoParticipantResponse]]:
    """
    Restore participant history using explicit SQL column selection.
    Omits the nonexistent id_or_passport column to avoid triggering Issue 1 ORM crash,
    while returning actual physical column values for emergency contacts and waiver_signed.
    """
    participants_by_booking_id: dict[int, list[EcoParticipantResponse]] = {
        bid: [] for bid in booking_ids
    }
    if not booking_ids:
        return participants_by_booking_id

    insp = inspect(db.bind)
    table_cols = {c["name"] for c in insp.get_columns("activity_booking_participants")}
    has_emergency_name = "emergency_contact_name" in table_cols
    has_emergency_phone = "emergency_contact_phone" in table_cols
    has_waiver = "waiver_signed" in table_cols

    cols_to_select = [
        _participants_table.c.id,
        _participants_table.c.activity_booking_id,
        _participants_table.c.full_name,
        _participants_table.c.age,
        (
            _participants_table.c.emergency_contact_name
            if has_emergency_name
            else literal(None).label("emergency_contact_name")
        ),
        (
            _participants_table.c.emergency_contact_phone
            if has_emergency_phone
            else literal(None).label("emergency_contact_phone")
        ),
        _participants_table.c.special_requirements,
        (
            _participants_table.c.waiver_signed
            if has_waiver
            else literal(False).label("waiver_signed")
        ),
        _participants_table.c.created_at,
    ]

    stmt = (
        select(*cols_to_select)
        .where(_participants_table.c.activity_booking_id.in_(booking_ids))
        .order_by(_participants_table.c.id.asc())
    )
    participant_rows = db.execute(stmt).fetchall()
    for row in participant_rows:
        if row.activity_booking_id in participants_by_booking_id:
            participants_by_booking_id[row.activity_booking_id].append(
                EcoParticipantResponse(
                    id=row.id,
                    full_name=row.full_name,
                    age=row.age,
                    emergency_contact_name=row.emergency_contact_name,
                    emergency_contact_phone=row.emergency_contact_phone,
                    special_requirements=row.special_requirements,
                    waiver_signed=bool(row.waiver_signed),
                    created_at=row.created_at,
                )
            )

    return participants_by_booking_id


# ==============================================================================
# Transactional Router Execution Helpers (Phase 4D Activation Wiring)
# ==============================================================================


def _build_traveler_booking_response(
    db: Session,
    booking_id: int,
    user_id: int,
) -> EcoBookingResponse:
    booking = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.id == booking_id,
            ActivityBooking.user_id == user_id,
            ActivityBooking.eco_experience_id.isnot(None),
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Booking {booking_id} was not found.",
        )

    refs = _fetch_persisted_booking_references(db, [booking.id], user_id=user_id)
    ref = refs.get(booking.id) or format_eco_booking_reference(booking.id, booking.created_at)
    participants_map = _fetch_participants_by_booking_ids(db, [booking.id])

    return EcoBookingResponse(
        id=booking.id,
        booking_reference=ref,
        eco_experience_id=booking.eco_experience_id,
        activity_slot_id=booking.activity_slot_id,
        user_id=booking.user_id,
        participants=booking.participants,
        total_price=booking.total_price,
        currency=booking.currency,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        booking_status=booking.booking_status,
        cancellation_reason=booking.cancellation_reason,
        cancelled_at=booking.cancelled_at,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        participants_list=participants_map.get(booking.id, []),
    )


def _build_provider_booking_detail(
    db: Session,
    booking_id: int,
    provider_profile_id: int,
) -> EcoBookingProviderListItem:
    booking = (
        db.query(ActivityBooking)
        .join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        .join(EcoExperience, ActivityBooking.eco_experience_id == EcoExperience.id)
        .filter(
            ActivityBooking.id == booking_id,
            EcoExperience.provider_profile_id == provider_profile_id,
            ActivityBooking.eco_experience_id.isnot(None),
        )
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Booking {booking_id} was not found for this provider.",
        )

    experience = db.query(EcoExperience).filter_by(id=booking.eco_experience_id).first()
    slot = db.query(ActivitySlot).filter_by(id=booking.activity_slot_id).first()
    traveler = db.query(User).filter_by(id=booking.user_id).first()
    refs = _fetch_persisted_booking_references(db, [booking.id])
    ref = refs.get(booking.id) or format_eco_booking_reference(booking.id, booking.created_at)
    participants_map = _fetch_participants_by_booking_ids(db, [booking.id])

    return EcoBookingProviderListItem(
        id=booking.id,
        booking_reference=ref,
        eco_experience_id=experience.id if experience else None,
        eco_experience_title=experience.title if experience else None,
        activity_slot_id=booking.activity_slot_id,
        slot_starts_at=slot.starts_at if slot else None,
        slot_ends_at=slot.ends_at if slot else None,
        traveler_id=booking.user_id,
        traveler_name=traveler.full_name if traveler else None,
        traveler_email=traveler.email if traveler else None,
        participants=booking.participants,
        total_price=booking.total_price,
        currency=booking.currency,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        booking_status=booking.booking_status,
        cancellation_reason=booking.cancellation_reason,
        cancelled_at=booking.cancelled_at,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        participants_list=participants_map.get(booking.id, []),
    )


def _execute_booking_create_with_transaction(
    db: Session,
    plan: EcoBookingValidationPlan,
) -> EcoBookingResponse:
    """
    Transactional router execution helper for traveler eco booking creation (Phase 4D).
    Explicitly manages db.commit() and db.rollback() and safely handles commit failures.
    """
    try:
        res = execute_traveler_booking_create_atomic(db=db, plan=plan)
        db.commit()
        db.expire_all()
    except EcoBookingServiceError as err:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
    except Exception as err:
        db.rollback()
        logger.exception("Database commit failed during booking creation: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete booking creation due to an internal database error. Please try again later.",
        )

    return _build_traveler_booking_response(db, res["booking_id"], plan.traveler_id)


def _execute_booking_cancel_with_transaction(
    db: Session,
    plan: EcoCancellationValidationPlan,
) -> EcoBookingResponse:
    """
    Transactional router execution helper for traveler eco booking cancellation (Phase 4D).
    Explicitly manages db.commit() and db.rollback() and safely handles commit failures.
    """
    try:
        res = execute_traveler_booking_cancel_atomic(db=db, plan=plan)
        db.commit()
        db.expire_all()
    except EcoBookingServiceError as err:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
    except Exception as err:
        db.rollback()
        logger.exception("Database commit failed during booking cancellation: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to complete booking cancellation due to an internal database error. Please try again later.",
        )

    return _build_traveler_booking_response(db, plan.booking_id, plan.traveler_id)


def _execute_provider_decision_with_transaction(
    db: Session,
    plan: EcoProviderDecisionValidationPlan,
    provider_profile_id: int,
) -> EcoBookingProviderListItem:
    """
    Transactional router execution helper for provider decision (Phase 4D).
    Explicitly manages db.commit() and db.rollback() and safely handles commit failures.
    """
    try:
        res = execute_provider_decision_atomic(db=db, plan=plan)
        db.commit()
        db.expire_all()
    except EcoBookingServiceError as err:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=err.message)
    except Exception as err:
        db.rollback()
        logger.exception("Database commit failed during provider decision: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to process provider booking decision due to an internal database error. Please try again later.",
        )

    return _build_provider_booking_detail(db, plan.booking_id, provider_profile_id)


# ==============================================================================
# 2. Traveler Booking History (Read-Only & Issue-1 Safe)
# ==============================================================================


@router.get(
    "/api/v1/eco-bookings/my-bookings",
    response_model=list[EcoBookingResponse],
    summary="List traveler's eco-tourism bookings (Read-Only)",
)
def get_my_eco_bookings(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Traveler-only endpoint: Returns the booking history for the authenticated traveler.

    Security & Stability Rules:
    - Verifies current user has role 'traveler' and is active.
    - Queries only bookings where eco_experience_id IS NOT NULL.
    - Sourcing persisted booking reference: batch lookups in dbo.booking_references (module_type in ('eco_tourism', 'activity'),
      target_booking_id == booking.id, user_id == current_user.id) with deterministic ID ordering (newest first).
      Falls back to standard canonical formatter format_eco_booking_reference(id, created_at) when no persisted record exists.
    - Participant History: uses explicit SQL column selection on dbo.activity_booking_participants to omit the nonexistent
      id_or_passport column (preventing Issue 1 ORM crashes) while returning actual physical values for emergency_contact_name,
      emergency_contact_phone, special_requirements, and waiver_signed.
    - Zero database writes executed.
    """
    try:
        validate_traveler_authorization(current_user)
    except EcoBookingServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=err.message,
        )

    bookings = (
        db.query(ActivityBooking)
        .filter(
            ActivityBooking.user_id == current_user.id,
            ActivityBooking.eco_experience_id.isnot(None),
        )
        .order_by(ActivityBooking.created_at.desc())
        .all()
    )

    if not bookings:
        return []

    booking_ids = [b.id for b in bookings]
    persisted_refs_by_booking_id = _fetch_persisted_booking_references(
        db=db,
        booking_ids=booking_ids,
        user_id=current_user.id,
    )
    participants_by_booking_id = _fetch_participants_by_booking_ids(
        db=db,
        booking_ids=booking_ids,
    )

    results: list[EcoBookingResponse] = []
    for b in bookings:
        reference = (
            persisted_refs_by_booking_id.get(b.id)
            or format_eco_booking_reference(b.id, b.created_at)
        )
        results.append(
            EcoBookingResponse(
                id=b.id,
                booking_reference=reference,
                eco_experience_id=b.eco_experience_id,
                activity_slot_id=b.activity_slot_id,
                user_id=b.user_id,
                participants=b.participants,
                total_price=b.total_price,
                currency=b.currency,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                booking_status=b.booking_status,
                cancellation_reason=b.cancellation_reason,
                cancelled_at=b.cancelled_at,
                created_at=b.created_at,
                updated_at=b.updated_at,
                participants_list=participants_by_booking_id.get(b.id, []),
            )
        )

    return results


# ==============================================================================
# 3. Traveler Booking Cancellation (Staged & Activation-Guarded)
# ==============================================================================


@router.post(
    "/api/v1/eco-bookings/{booking_id}/cancel",
    response_model=EcoBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Request cancellation of an Eco-Tourism reservation (Staged)",
)
def cancel_eco_booking(
    booking_id: int,
    payload: EcoBookingCancellation,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Traveler-only endpoint: Requests cancellation of an existing Eco-Tourism booking.

    Validation Pipeline:
    1. Authenticated caller must have role 'traveler' and own the booking.
    2. Booking must belong to an EcoExperience.
    3. Booking status cannot be 'cancelled' (cannot cancel twice).
    4. Booking status must be 'pending' or 'confirmed'.
    5. Cancellation cutoff is evaluated against EcoExperience.free_cancellation_hours.

    STAGED ACTIVATION GUARD:
    - Performs full read-only cancellation eligibility evaluation.
    - Status mutation and capacity release are guarded and return HTTP 503 until
      booking write operations are activated.
    - Zero database writes are executed.
    """
    try:
        cancellation_plan = validate_booking_cancellation_read_only(
            db=db,
            traveler=current_user,
            booking_id=booking_id,
            reason=payload.reason,
        )
    except EcoBookingServiceError as err:
        if err.code in ("ECO_USER_NOT_TRAVELER", "ECO_USER_INACTIVE"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err.message,
            )
        elif err.code in ("ECO_BOOKING_NOT_FOUND", "ECO_SLOT_NOT_FOUND"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err.message,
            )
        elif err.code in (
            "ECO_BOOKING_ALREADY_CANCELLED",
            "ECO_BOOKING_CANNOT_BE_CANCELLED",
            "ECO_CANCELLATION_PAST_START",
            "ECO_SLOT_ALREADY_STARTED",
            "ECO_CANCELLATION_CUTOFF_EXCEEDED",
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=err.message,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err.message,
            )

    # =========================================================================
    # STAGED ACTIVATION GUARD
    # =========================================================================
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Eco booking cancellation validation succeeded for booking {cancellation_plan.booking_id} "
            f"(Free cancellation eligible: {cancellation_plan.cancellation.is_free_cancellation}, "
            f"Capacity to release: {cancellation_plan.participants_to_release} seats), "
            f"but cancellation execution is staged and disabled pending booking activation. "
            f"Zero database writes were executed."
        ),
    )

    # PREPARED FOR ACTIVATION (Behind HTTP 503 guard):
    return _execute_booking_cancel_with_transaction(db, cancellation_plan)


# ==============================================================================
# 4. Provider Booking Management & Decision Endpoints (Phase 4B)
# ==============================================================================


@router.get(
    "/api/v1/provider/eco-bookings",
    response_model=EcoBookingProviderListResponse,
    summary="List bookings for authenticated provider's owned eco experiences (Staged)",
)
def list_provider_eco_bookings(
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, description="Items per page")] = 10,
    booking_status: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by booking status (pending, confirmed, rejected, cancelled, completed)",
        ),
    ] = None,
    eco_experience_id: Annotated[
        int | None,
        Query(description="Filter by specific owned eco-experience id"),
    ] = None,
    start_date: Annotated[
        date | None,
        Query(description="Filter bookings on or after this slot start date"),
    ] = None,
    end_date: Annotated[
        date | None,
        Query(description="Filter bookings on or before this slot start date"),
    ] = None,
):
    """
    Provider-only endpoint: Retrieves paginated bookings across all owned eco-experiences.

    Security & Business Rules:
    1. Authenticated caller must be an approved provider with provider_type == 'eco_tourism'.
    2. Scope is strictly restricted to listings owned by current_provider.id.
    3. If eco_experience_id filter is provided, verifies ownership; returns 403 if unowned.
    4. Canonical references retrieved from dbo.booking_references (with deterministic id.desc() ordering).
    5. Participant history retrieved via explicit column selection, omitting id_or_passport.
    6. Zero database writes executed.
    """
    valid_statuses = {"pending", "confirmed", "rejected", "cancelled", "completed"}
    if booking_status and booking_status.strip().lower() not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid booking status '{booking_status}'. Allowed values: {', '.join(sorted(valid_statuses))}",
        )

    owned_exp_query = db.query(EcoExperience).filter(
        EcoExperience.provider_profile_id == current_provider.id
    )

    if eco_experience_id is not None:
        target_exp = owned_exp_query.filter(EcoExperience.id == eco_experience_id).first()
        if target_exp is None:
            exp_exists = (
                db.query(EcoExperience.id)
                .filter(EcoExperience.id == eco_experience_id)
                .first()
            )
            if exp_exists is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"EcoExperience with id {eco_experience_id} was not found.",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to view bookings for this eco-experience.",
            )
        owned_experiences = [target_exp]
    else:
        owned_experiences = owned_exp_query.all()

    if not owned_experiences:
        return EcoBookingProviderListResponse(
            items=[],
            total=0,
            page=page,
            page_size=page_size,
        )

    exp_map = {e.id: e for e in owned_experiences}
    owned_exp_ids = list(exp_map.keys())

    query = db.query(ActivityBooking).filter(
        ActivityBooking.eco_experience_id.in_(owned_exp_ids)
    )

    if booking_status:
        query = query.filter(
            ActivityBooking.booking_status == booking_status.strip().lower()
        )

    if start_date or end_date:
        query = query.join(ActivitySlot, ActivityBooking.activity_slot_id == ActivitySlot.id)
        if start_date:
            dt_start = datetime.combine(start_date, time.min).replace(tzinfo=UTC)
            query = query.filter(ActivitySlot.starts_at >= dt_start)
        if end_date:
            dt_end = datetime.combine(end_date, time.max).replace(tzinfo=UTC)
            query = query.filter(ActivitySlot.starts_at <= dt_end)

    total = query.count()
    bookings = (
        query.order_by(ActivityBooking.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    if not bookings:
        return EcoBookingProviderListResponse(
            items=[],
            total=total,
            page=page,
            page_size=page_size,
        )

    booking_ids = [b.id for b in bookings]
    refs_map = _fetch_persisted_booking_references(db, booking_ids)
    participants_map = _fetch_participants_by_booking_ids(db, booking_ids)

    slot_ids = {b.activity_slot_id for b in bookings}
    slots = (
        db.query(ActivitySlot).filter(ActivitySlot.id.in_(slot_ids)).all()
        if slot_ids
        else []
    )
    slots_map = {s.id: s for s in slots}

    user_ids = {b.user_id for b in bookings}
    users = (
        db.query(User).filter(User.id.in_(user_ids)).all()
        if user_ids
        else []
    )
    users_map = {u.id: u for u in users}

    items: list[EcoBookingProviderListItem] = []
    for b in bookings:
        ref = refs_map.get(b.id) or format_eco_booking_reference(b.id, b.created_at)
        exp = exp_map.get(b.eco_experience_id)
        exp_title = exp.title if exp else "Eco-Experience"
        slot = slots_map.get(b.activity_slot_id)
        traveler = users_map.get(b.user_id)
        traveler_name = traveler.full_name if traveler else None
        traveler_email = traveler.email if traveler else None

        items.append(
            EcoBookingProviderListItem(
                id=b.id,
                booking_reference=ref,
                eco_experience_id=b.eco_experience_id,
                eco_experience_title=exp_title,
                activity_slot_id=b.activity_slot_id,
                slot_starts_at=slot.starts_at if slot else None,
                slot_ends_at=slot.ends_at if slot else None,
                traveler_id=b.user_id,
                traveler_name=traveler_name,
                traveler_email=traveler_email,
                participants=b.participants,
                total_price=b.total_price,
                currency=b.currency,
                payment_method=b.payment_method,
                payment_status=b.payment_status,
                booking_status=b.booking_status,
                cancellation_reason=b.cancellation_reason,
                cancelled_at=b.cancelled_at,
                created_at=b.created_at,
                updated_at=b.updated_at,
                participants_list=participants_map.get(b.id, []),
            )
        )

    return EcoBookingProviderListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/api/v1/provider/eco-experiences/{experience_id}/bookings",
    response_model=EcoBookingProviderListResponse,
    summary="List bookings for a specific owned eco experience (Staged)",
)
def list_experience_eco_bookings(
    experience_id: int,
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=50, description="Items per page")] = 10,
    booking_status: Annotated[
        str | None,
        Query(
            alias="status",
            description="Filter by booking status (pending, confirmed, rejected, cancelled, completed)",
        ),
    ] = None,
    start_date: Annotated[
        date | None,
        Query(description="Filter bookings on or after this slot start date"),
    ] = None,
    end_date: Annotated[
        date | None,
        Query(description="Filter bookings on or before this slot start date"),
    ] = None,
):
    """
    Provider-only endpoint: Delegates to list_provider_eco_bookings filtered by experience_id.
    Validates ownership of experience_id (403 if unowned, 404 if missing).
    """
    return list_provider_eco_bookings(
        current_provider=current_provider,
        db=db,
        page=page,
        page_size=page_size,
        booking_status=booking_status,
        eco_experience_id=experience_id,
        start_date=start_date,
        end_date=end_date,
    )


@router.get(
    "/api/v1/provider/eco-bookings/{booking_id}",
    response_model=EcoBookingProviderListItem,
    summary="Get single eco-booking detail for provider (Staged)",
)
def get_provider_eco_booking(
    booking_id: int,
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider-only endpoint: Retrieves detailed booking record for an owned eco-experience.

    Validation & Ownership:
    1. Authenticated caller must be an approved provider with provider_type == 'eco_tourism'.
    2. Booking must exist and belong to an experience owned by caller (403 if unowned, 404 if missing).
    3. Retrieves persisted booking reference and full participant roster with physical column safety.
    """
    booking = (
        db.query(ActivityBooking)
        .filter(ActivityBooking.id == booking_id)
        .first()
    )
    if not booking:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Booking with id {booking_id} was not found.",
        )

    try:
        experience = validate_provider_booking_ownership(
            db=db,
            booking=booking,
            provider_profile=current_provider,
        )
    except EcoBookingServiceError as err:
        if err.code == "ECO_EXPERIENCE_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err.message,
            )
        elif err.code == "ECO_PROVIDER_NOT_AUTHORIZED":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err.message,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err.message,
            )

    refs_map = _fetch_persisted_booking_references(db, [booking.id])
    ref = refs_map.get(booking.id) or format_eco_booking_reference(
        booking.id, booking.created_at
    )
    participants_map = _fetch_participants_by_booking_ids(db, [booking.id])

    slot = (
        db.query(ActivitySlot)
        .filter(ActivitySlot.id == booking.activity_slot_id)
        .first()
    )
    traveler = (
        db.query(User)
        .filter(User.id == booking.user_id)
        .first()
    )

    return EcoBookingProviderListItem(
        id=booking.id,
        booking_reference=ref,
        eco_experience_id=experience.id,
        eco_experience_title=experience.title,
        activity_slot_id=booking.activity_slot_id,
        slot_starts_at=slot.starts_at if slot else None,
        slot_ends_at=slot.ends_at if slot else None,
        traveler_id=booking.user_id,
        traveler_name=traveler.full_name if traveler else None,
        traveler_email=traveler.email if traveler else None,
        participants=booking.participants,
        total_price=booking.total_price,
        currency=booking.currency,
        payment_method=booking.payment_method,
        payment_status=booking.payment_status,
        booking_status=booking.booking_status,
        cancellation_reason=booking.cancellation_reason,
        cancelled_at=booking.cancelled_at,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        participants_list=participants_map.get(booking.id, []),
    )


@router.post(
    "/api/v1/provider/eco-bookings/{booking_id}/decision",
    response_model=EcoBookingProviderListItem,
    summary="Submit provider decision for an eco booking (Staged)",
)
def decide_provider_eco_booking(
    booking_id: int,
    payload: EcoBookingProviderDecision,
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider-only endpoint: Makes a lifecycle decision (accept, reject, confirm_cash).

    Validation Pipeline:
    1. Authenticated caller must be an approved provider with provider_type == 'eco_tourism'.
    2. Booking must exist and belong to an EcoExperience owned by caller.
    3. Action-specific state transitions:
       - 'accept': booking_status must be 'pending', slot must not be expired.
       - 'reject': booking_status must be 'pending', note must be >= 5 characters.
       - 'confirm_cash': payment_method must be 'pay_at_location', booking must be 'confirmed', payment not already 'paid'.
    4. Calculates capacity release requirements without mutating database.

    STAGED ACTIVATION GUARD:
    - Performs full domain validation.
    - Status mutation, capacity release, and cash confirmations are guarded and return HTTP 503 until
      Issues 1 and 2 are formally resolved.
    - Zero database writes are executed.
    """
    try:
        decision_plan = validate_provider_decision_read_only(
            db=db,
            provider_profile=current_provider,
            booking_id=booking_id,
            payload=payload,
        )
    except EcoBookingServiceError as err:
        if err.code in ("ECO_PROVIDER_NOT_AUTHORIZED",):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err.message,
            )
        elif err.code in (
            "ECO_BOOKING_NOT_FOUND",
            "ECO_EXPERIENCE_NOT_FOUND",
            "ECO_SLOT_NOT_FOUND",
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err.message,
            )
        elif err.code in (
            "ECO_INVALID_STATUS_TRANSITION",
            "ECO_INVALID_PAYMENT_METHOD",
            "ECO_ALREADY_PAID",
            "ECO_SLOT_EXPIRED",
            "ECO_REJECTION_NOTE_REQUIRED",
            "ECO_UNSUPPORTED_ACTION",
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=err.message,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=err.message,
            )

    # =========================================================================
    # STAGED ACTIVATION GUARD (ISSUES 1 & 2 PREREQUISITES)
    # =========================================================================
    # Decision state mutation and capacity release are staged and disabled pending:
    # 1. Issue 1: ActivityBookingParticipant ORM alignment.
    # 2. Issue 2: Migration 030 approval for activity_slots.
    # Zero database writes executed.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Provider booking decisions are currently staged and pending activation approvals "
            "for Issues 1 and 2. Decision validated successfully in read-only mode "
            f"(action='{decision_plan.action}', target_booking_status='{decision_plan.to_booking_status}', "
            f"target_payment_status='{decision_plan.to_payment_status}', release_capacity={decision_plan.participants_to_release}). "
            "Zero database writes were executed."
        ),
    )

    # PREPARED FOR ACTIVATION (Behind HTTP 503 guard):
    return _execute_provider_decision_with_transaction(db, decision_plan, current_provider.id)

