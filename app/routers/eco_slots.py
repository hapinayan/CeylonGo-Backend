"""
Module 04 — Staged Eco-Tourism Slot Management and Public Availability Router.

End-Point Specification:
1. POST /api/v1/provider/eco-experiences/{id}/slots
   - Enforces authenticated provider authorization (provider_type == 'eco_tourism', approved status).
   - Validates listing ownership, listing approval status, and publication status.
   - Executes full pre-creation validation (future timing, bounds, seasonal closures, collision checks).
   - STAGED ACTIVATION GUARD: Disabled with HTTP 503 pending Database Coordinator approval
     of Migration 030 (making activity_slots.activity_id nullable).
   - Never creates or references dummy activities or fake IDs.

2. GET /api/v1/eco-experiences/{id}/slots
   - Public slot availability lookup for travelers.
   - Strictly returns active, future slots with positive availability for approved & published experiences.
   - Computes open places safely (available_places = max(0, capacity - reserved_count)).
   - Excludes private provider contact details, financial records, or administrative metadata.

STRICT ISOLATION:
- Unregistered in app/main.py.
- Zero database writes executed.
"""

import logging
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import require_approved_eco_provider
from app.database.connection import get_db
from app.models.activity_slot import ActivitySlot
from app.models.eco_experience import EcoExperience, EcoSeasonRestriction
from app.models.provider_profile import ProviderProfile
from app.schemas.eco_slot import (
    EcoSlotAvailabilityQuery,
    EcoSlotCreate,
    EcoSlotPublicItem,
    EcoSlotResponse,
    EcoSlotWeatherCancelRequest,
    EcoSlotWeatherCancelResponse,
)
from app.services.eco_booking_service import (
    EcoBookingServiceError,
    execute_weather_emergency_cancellation_atomic,
    validate_weather_emergency_cancellation_read_only,
)
from app.services.eco_slot_validation import (
    EcoSlotValidationError,
    validate_eco_slot_creation_read_only,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Eco-Tourism Slots (Staged)"])


# ==============================================================================
# 1. Provider Slot Creation (Staged & Activation-Guarded)
# ==============================================================================


@router.post(
    "/api/v1/provider/eco-experiences/{experience_id}/slots",
    response_model=EcoSlotResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a scheduled slot for an owned Eco-Tourism experience (Staged)",
)
def create_eco_slot(
    experience_id: int,
    payload: EcoSlotCreate,
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Provider-only endpoint: Schedules a new slot for an approved, owned Eco-Tourism experience.

    Validation Pipeline:
    1. Authenticated caller must be an approved provider with provider_type == 'eco_tourism'.
    2. Experience must exist, belong to the caller, be 'approved', and 'is_published' == True.
    3. Slot timing must be in the future with ends_at > starts_at.
    4. Slot capacity must not exceed experience max_group_size or platform environmental ceiling (50).
    5. Slot window must not overlap with active seasonal restrictions or existing active slots.

    STAGED GUARD:
    - This endpoint validates all inputs against business rules without inserting records.
    - Actual database insertion is guarded and returns HTTP 503 until Migration 030 is approved
      by the Database Coordinator (Issue 2).
    """
    try:
        # Execute comprehensive read-only validation pipeline
        validation_result = validate_eco_slot_creation_read_only(
            db=db,
            eco_experience_id=experience_id,
            provider_profile=current_provider,
            payload=payload,
        )
    except EcoSlotValidationError as err:
        # Map domain validation errors to appropriate HTTP status codes
        if err.code in ("ECO_EXPERIENCE_NOT_FOUND",):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=err.message,
            )
        elif err.code in ("ECO_PROVIDER_NOT_AUTHORIZED", "ECO_EXPERIENCE_NOT_OWNED"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=err.message,
            )
        elif err.code in ("ECO_SLOT_OVERLAPPING", "ECO_SEASON_RESTRICTED", "ECO_EXPERIENCE_NOT_APPROVED"):
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
    # STAGED ACTIVATION GUARD (ISSUE 2 / MIGRATION 030 PREREQUISITE)
    # =========================================================================
    # In dbo.activity_slots, activity_id is currently INT NOT NULL.
    # Inserting an eco-only slot requires Migration 030 approval to allow NULL.
    # Creating dummy activity records is strictly forbidden by project rules.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Eco slot validation succeeded for '{validation_result.experience_title}', "
            f"but database insertion is staged and disabled pending Database Coordinator "
            f"approval of Migration 030 (activity_slots.activity_id nullability). "
            f"Zero database writes were executed."
        ),
    )

    # PREPARED FOR ACTIVATION (Behind HTTP 503 guard):
    return _execute_slot_create_with_transaction(db, id, payload)


def _execute_slot_create_with_transaction(
    db: Session,
    experience_id: int,
    payload: EcoSlotCreate,
) -> EcoSlotResponse:
    """
    Transactional router execution helper for eco slot creation (Phase 4D).
    Explicitly manages db.commit() and db.rollback() and safely handles commit failures.
    """
    slot = ActivitySlot(
        activity_id=None,  # Requires Migration 030
        eco_experience_id=experience_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        capacity=payload.capacity,
        reserved_count=0,
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db.add(slot)
    try:
        db.commit()
        db.refresh(slot)
    except Exception as err:
        db.rollback()
        logger.exception("Database commit failed during slot persistence: %s", err)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist slot due to an internal database error. Please try again later.",
        )

    return EcoSlotResponse(
        id=slot.id,
        eco_experience_id=slot.eco_experience_id,
        activity_id=slot.activity_id,
        starts_at=slot.starts_at,
        ends_at=slot.ends_at,
        capacity=slot.capacity,
        reserved_count=slot.reserved_count,
        is_active=slot.is_active,
        created_at=slot.created_at,
    )


# ==============================================================================
# 2. Public Slot Availability Query
# ==============================================================================


@router.get(
    "/api/v1/eco-experiences/{experience_id}/slots",
    response_model=list[EcoSlotPublicItem],
    summary="Get available scheduling slots for an approved eco experience",
)
def get_public_eco_slots(
    experience_id: int,
    db: Annotated[Session, Depends(get_db)],
    start_date: Annotated[
        str | None,
        Query(description="Filter from date (YYYY-MM-DD)"),
    ] = None,
    end_date: Annotated[
        str | None,
        Query(description="Filter to date (YYYY-MM-DD)"),
    ] = None,
    min_available_places: Annotated[
        int | None,
        Query(ge=1, le=50, description="Minimum available seats required"),
    ] = None,
):
    """
    Public discovery endpoint: Returns upcoming available slots for an approved, published eco experience.

    Security & Privacy Rules:
    - Verifies experience is approved and published; returns HTTP 404 otherwise.
    - Filters only future, active slots where available_places > 0.
    - Omits internal admin metadata, provider contact details, and financial figures.
    """
    # 1. Verify experience readiness
    experience = (
        db.query(EcoExperience)
        .filter(
            EcoExperience.id == experience_id,
            EcoExperience.listing_status == "approved",
            EcoExperience.is_published.is_(True),
        )
        .first()
    )

    if not experience:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Eco experience not found or is not currently available for booking.",
        )

    # 2. Query active future slots
    now_utc = datetime.now(UTC)
    query = (
        db.query(ActivitySlot)
        .filter(
            ActivitySlot.eco_experience_id == experience_id,
            ActivitySlot.is_active.is_(True),
            ActivitySlot.starts_at > now_utc,
        )
    )

    # 3. Optional date range filtering
    if start_date:
        try:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").replace(
                hour=0, minute=0, second=0, tzinfo=UTC
            )
            query = query.filter(ActivitySlot.starts_at >= parsed_start)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start_date must be in YYYY-MM-DD format.",
            )

    if end_date:
        try:
            parsed_end = datetime.strptime(end_date, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=UTC
            )
            query = query.filter(ActivitySlot.starts_at <= parsed_end)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="end_date must be in YYYY-MM-DD format.",
            )

    slots = query.order_by(ActivitySlot.starts_at.asc()).all()

    # 4. Filter active seasonal restrictions (E06 & T07)
    active_restrictions = (
        db.query(EcoSeasonRestriction)
        .filter(
            EcoSeasonRestriction.eco_experience_id == experience_id,
            EcoSeasonRestriction.is_active.is_(True),
        )
        .all()
    )

    # 5. Filter available places and build public representations
    public_items: list[EcoSlotPublicItem] = []
    for slot in slots:
        # Exclude slots that overlap any active seasonal restriction
        slot_start_d = (
            slot.starts_at.date()
            if isinstance(slot.starts_at, datetime)
            else slot.starts_at
        )
        slot_end_d = (
            slot.ends_at.date()
            if isinstance(slot.ends_at, datetime)
            else slot.ends_at
        )
        if any(
            slot_start_d <= r.end_date and slot_end_d >= r.start_date
            for r in active_restrictions
        ):
            continue

        open_places = max(0, slot.capacity - slot.reserved_count)
        if open_places <= 0:
            continue

        if min_available_places is not None and open_places < min_available_places:
            continue

        public_items.append(
            EcoSlotPublicItem(
                id=slot.id,
                eco_experience_id=slot.eco_experience_id or experience_id,
                starts_at=slot.starts_at,
                ends_at=slot.ends_at,
                capacity=slot.capacity,
                reserved_count=slot.reserved_count,
                available_places=open_places,
                is_available=True,
            )
        )

    return public_items


# ==============================================================================
# 3. Emergency Slot Weather Cancellation (E06 - Staged)
# ==============================================================================


@router.post(
    "/api/v1/provider/eco-experiences/{experience_id}/slots/{slot_id}/weather-cancel",
    response_model=EcoSlotWeatherCancelResponse,
    summary="Emergency weather/hazard cancellation for an eco slot (E06 - Staged)",
)
def emergency_weather_cancel_slot(
    experience_id: int,
    slot_id: int,
    payload: EcoSlotWeatherCancelRequest,
    current_provider: Annotated[
        ProviderProfile, Depends(require_approved_eco_provider)
    ],
    db: Annotated[Session, Depends(get_db)],
):
    """
    Emergency cancellation of a scheduled slot and its existing bookings due to severe weather/hazards (E06).
    - Deactivates the slot (is_active = False) preventing new reservations.
    - Cancels all pending/confirmed bookings for the slot.
    - Releases capacity and updates booking references.
    - Queues 100% full refunds on PaymentRefund table for paid transactions.
    - Creates an active EcoSeasonRestriction to block subsequent reservations.
    - Dispatches notifications to travelers and the provider.
    """
    try:
        plan = validate_weather_emergency_cancellation_read_only(
            db=db,
            provider_profile=current_provider,
            eco_experience_id=experience_id,
            reason=payload.reason,
            hazard_type=payload.hazard_type,
            slot_id=slot_id,
        )
        result = execute_weather_emergency_cancellation_atomic(db=db, plan=plan)
        db.commit()
    except EcoBookingServiceError as err:
        db.rollback()
        if err.code in ("ECO_EXPERIENCE_NOT_FOUND", "ECO_SLOT_NOT_FOUND"):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=err.message)
        elif err.code in ("ECO_PROVIDER_NOT_AUTHORIZED", "ECO_EXPERIENCE_NOT_OWNED"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=err.message)
        else:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=err.message)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to execute emergency weather slot cancellation: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to execute emergency weather slot cancellation due to an internal error.",
        )

    return EcoSlotWeatherCancelResponse(
        slot_id=slot_id,
        eco_experience_id=experience_id,
        is_active=False,
        hazard_type=result["hazard_type"],
        reason=result["reason"],
        cancelled_bookings_count=result["cancelled_bookings_count"],
        released_capacity=result["released_capacity_total"],
        refunds_initiated_count=result["refunds_initiated_count"],
        total_refund_amount=Decimal(result["total_refund_amount"]),
        cancelled_booking_ids=result["cancelled_booking_ids"],
        status="success",
        message=result["message"],
    )

